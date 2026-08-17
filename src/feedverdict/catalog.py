# Finds markets supported by the configured price sources.

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Iterable

from feedverdict.models import Market, SourceFailure
from feedverdict.sources import PriceSource, SourceError


@dataclass(frozen=True, slots=True)
class MarketAvailability:
    market: Market
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MarketCatalog:
    markets: tuple[MarketAvailability, ...]
    failures: tuple[SourceFailure, ...] = field(default_factory=tuple)

    def availability_for(self, market: Market) -> MarketAvailability | None:
        return next((item for item in self.markets if item.market == market), None)


def discover_markets(
    sources: Iterable[PriceSource],
    *,
    min_sources: int = 2,
) -> MarketCatalog:
    # Only show markets covered by the required number of independent sources.
    source_tuple = tuple(sources)
    if not source_tuple:
        raise ValueError("At least one source is required for market discovery")
    if min_sources < 1:
        raise ValueError("min_sources must be at least one")

    def discover_one(
        source: PriceSource,
    ) -> tuple[PriceSource, dict[Market, str], SourceFailure | None]:
        try:
            return source, source.get_markets(), None
        except SourceError as exc:
            return source, {}, SourceFailure(source.name, exc.code, str(exc))
        except Exception as exc:  # One custom adapter should not stop discovery.
            return (
                source,
                {},
                SourceFailure(
                    source.name,
                    "UNEXPECTED_SOURCE_ERROR",
                    f"{type(exc).__name__}: {exc}",
                ),
            )

    with ThreadPoolExecutor(max_workers=min(len(source_tuple), 8)) as executor:
        discoveries = tuple(executor.map(discover_one, source_tuple))

    markets_by_source = {
        source.name: set(markets)
        for source, markets, failure in discoveries
        if failure is None
    }
    failures = tuple(
        failure for _source, _markets, failure in discoveries if failure is not None
    )
    all_markets = set().union(*markets_by_source.values()) if markets_by_source else set()

    availability: list[MarketAvailability] = []
    for market in all_markets:
        supporting_sources = tuple(
            source.name
            for source in source_tuple
            if market in markets_by_source.get(source.name, set())
        )
        if len(supporting_sources) >= min_sources:
            availability.append(MarketAvailability(market, supporting_sources))

    availability.sort(
        key=lambda item: (
            item.market.quote != "USD",
            item.market.quote,
            item.market.base,
        )
    )
    return MarketCatalog(tuple(availability), failures)
