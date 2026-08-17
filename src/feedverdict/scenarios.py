# Repeatable failure scenarios for demos and evaluation.

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from feedverdict.models import (
    Confidence,
    Market,
    PriceObservation,
    ReconciliationStatus,
)
from feedverdict.sources import SourceError


DEMO_MARKET = Market("BTC", "USD")


class ScenarioSource:
    def __init__(
        self,
        name: str,
        outcome: PriceObservation | SourceError,
    ) -> None:
        self.name = name
        self.outcome = outcome

    def get_markets(self) -> dict[Market, str]:
        return {DEMO_MARKET: f"{self.name}-BTCUSD"}

    def fetch(self, market: Market) -> PriceObservation:
        if market != DEMO_MARKET:
            raise SourceError("UNSUPPORTED_MARKET", f"Scenario does not include {market.symbol}")
        if isinstance(self.outcome, SourceError):
            raise self.outcome
        return self.outcome


@dataclass(frozen=True, slots=True)
class ScenarioExpectation:
    status: ReconciliationStatus
    confidence: Confidence
    canonical_updated: bool
    queried_sources: int
    required_code: str | None = None


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    description: str
    sources: tuple[ScenarioSource, ...]
    expectation: ScenarioExpectation
    now: datetime


def _observation(
    source: str,
    price: str,
    now: datetime,
    *,
    age_seconds: float = 1,
) -> PriceObservation:
    return PriceObservation(
        source=source,
        market=DEMO_MARKET,
        provider_market=f"{source}-BTCUSD",
        price=Decimal(price),
        provider_timestamp=now - timedelta(seconds=age_seconds),
        received_at=now,
        event_id=f"{source}-{int(now.timestamp())}",
        latency_ms=12,
    )


def scenario_names() -> tuple[str, ...]:
    return (
        "healthy",
        "stale",
        "timeout",
        "outlier",
        "single-source",
        "all-failed",
    )


def build_scenario(name: str, *, now: datetime | None = None) -> Scenario:
    now = now or datetime.now(timezone.utc)
    if name == "healthy":
        return Scenario(
            name,
            "The first two fresh independent sources agree; the third is not queried.",
            (
                ScenarioSource("Coinbase", _observation("Coinbase", "118000", now)),
                ScenarioSource("Kraken", _observation("Kraken", "118020", now)),
                ScenarioSource("Bitstamp", _observation("Bitstamp", "150000", now)),
            ),
            ScenarioExpectation(
                ReconciliationStatus.VERIFIED,
                Confidence.HIGH,
                True,
                2,
                "STOP_VERIFIED",
            ),
            now,
        )
    if name == "stale":
        return Scenario(
            name,
            "The primary returns HTTP-success data with an old provider timestamp.",
            (
                ScenarioSource(
                    "Coinbase",
                    _observation("Coinbase", "110000", now, age_seconds=600),
                ),
                ScenarioSource("Kraken", _observation("Kraken", "118000", now)),
                ScenarioSource("Bitstamp", _observation("Bitstamp", "118020", now)),
            ),
            ScenarioExpectation(
                ReconciliationStatus.VERIFIED,
                Confidence.MEDIUM,
                True,
                3,
                "STALE_PROVIDER_TIMESTAMP",
            ),
            now,
        )
    if name == "timeout":
        return Scenario(
            name,
            "The primary never yields data before its deadline; alternatives form quorum.",
            (
                ScenarioSource(
                    "Coinbase",
                    SourceError("SOURCE_TIMEOUT", "Injected watchdog timeout"),
                ),
                ScenarioSource("Kraken", _observation("Kraken", "118000", now)),
                ScenarioSource("Bitstamp", _observation("Bitstamp", "118020", now)),
            ),
            ScenarioExpectation(
                ReconciliationStatus.VERIFIED,
                Confidence.MEDIUM,
                True,
                3,
                "SOURCE_TIMEOUT",
            ),
            now,
        )
    if name == "outlier":
        return Scenario(
            name,
            "Two fresh sources disagree; a third source identifies the agreeing cluster.",
            (
                ScenarioSource("Coinbase", _observation("Coinbase", "118000", now)),
                ScenarioSource("Kraken", _observation("Kraken", "150000", now)),
                ScenarioSource("Bitstamp", _observation("Bitstamp", "118020", now)),
            ),
            ScenarioExpectation(
                ReconciliationStatus.VERIFIED,
                Confidence.MEDIUM,
                True,
                3,
                "PRICE_OUTLIER",
            ),
            now,
        )
    if name == "single-source":
        return Scenario(
            name,
            "Only one fresh observation survives, so it remains an unverified candidate.",
            (
                ScenarioSource("Coinbase", _observation("Coinbase", "118000", now)),
                ScenarioSource(
                    "Kraken", SourceError("SOURCE_TIMEOUT", "Injected watchdog timeout")
                ),
                ScenarioSource(
                    "Bitstamp", SourceError("NETWORK_ERROR", "Injected network outage")
                ),
            ),
            ScenarioExpectation(
                ReconciliationStatus.UNVERIFIED_SINGLE_SOURCE,
                Confidence.LOW,
                False,
                3,
                "STOP_SOURCES_EXHAUSTED",
            ),
            now,
        )
    if name == "all-failed":
        return Scenario(
            name,
            "No source returns usable evidence, so the agent refuses to guess.",
            (
                ScenarioSource(
                    "Coinbase", SourceError("SOURCE_TIMEOUT", "Injected watchdog timeout")
                ),
                ScenarioSource(
                    "Kraken", SourceError("NETWORK_ERROR", "Injected network outage")
                ),
                ScenarioSource(
                    "Bitstamp", SourceError("SCHEMA_INVALID", "Injected malformed payload")
                ),
            ),
            ScenarioExpectation(
                ReconciliationStatus.NO_QUORUM,
                Confidence.NONE,
                False,
                3,
                "STOP_SOURCES_EXHAUSTED",
            ),
            now,
        )
    raise ValueError(f"Unknown scenario {name!r}; choose from {', '.join(scenario_names())}")
