# Validates observations and chooses a price when independent sources agree.

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from statistics import median
from typing import Iterable

from feedverdict.models import (
    Confidence,
    Market,
    PriceObservation,
    ReconciliationResult,
    ReconciliationStatus,
    RejectedObservation,
    SourceFailure,
)


def _decimal_median(values: list[Decimal]) -> Decimal:
    return Decimal(median(values))


def _spread_bps(observations: list[PriceObservation]) -> Decimal:
    prices = [observation.price for observation in observations]
    midpoint = _decimal_median(prices)
    return ((max(prices) - min(prices)) / midpoint) * Decimal("10000")


# Prices are sorted so each possible agreement group is a continuous slice.
# Prefer the largest group, then the tightest spread, then the freshest data.
def _largest_consistent_cluster(
    observations: list[PriceObservation],
    max_spread_bps: Decimal,
) -> tuple[list[PriceObservation], Decimal] | None:
    ordered = sorted(observations, key=lambda item: item.price)
    candidates: list[tuple[list[PriceObservation], Decimal]] = []

    for start in range(len(ordered)):
        for end in range(start + 2, len(ordered) + 1):
            window = ordered[start:end]
            spread = _spread_bps(window)
            if spread <= max_spread_bps:
                candidates.append((window, spread))

    if not candidates:
        return None

    return min(
        candidates,
        key=lambda candidate: (
            -len(candidate[0]),
            candidate[1],
            -max(item.provider_timestamp.timestamp() for item in candidate[0]),
        ),
    )


# A canonical price is returned only when at least two providers agree.
def reconcile(
    market: Market,
    observations: Iterable[PriceObservation],
    *,
    failures: Iterable[SourceFailure] = (),
    now: datetime,
    max_age_seconds: float = 120.0,
    max_future_skew_seconds: float = 5.0,
    max_spread_bps: Decimal = Decimal("100"),
) -> ReconciliationResult:
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")

    valid: list[PriceObservation] = []
    rejected: list[RejectedObservation] = []

    for observation in observations:
        if observation.market != market:
            rejected.append(
                RejectedObservation(
                    observation,
                    "MARKET_MISMATCH",
                    f"Expected {market.symbol}, received {observation.market.symbol}",
                )
            )
            continue
        if observation.price <= 0 or not observation.price.is_finite():
            rejected.append(
                RejectedObservation(
                    observation,
                    "PRICE_INVALID",
                    "Price must be positive and finite",
                )
            )
            continue

        if observation.provider_timestamp.tzinfo is None:
            rejected.append(
                RejectedObservation(
                    observation,
                    "TIMESTAMP_INVALID",
                    "Provider timestamp must include a timezone",
                )
            )
            continue

        age_seconds = observation.age_seconds(now)
        if age_seconds < -max_future_skew_seconds:
            rejected.append(
                RejectedObservation(
                    observation,
                    "FUTURE_PROVIDER_TIMESTAMP",
                    f"Provider timestamp is {-age_seconds:.1f}s in the future",
                )
            )
            continue
        if age_seconds > max_age_seconds:
            rejected.append(
                RejectedObservation(
                    observation,
                    "STALE_PROVIDER_TIMESTAMP",
                    f"Provider data is {age_seconds:.1f}s old; limit is {max_age_seconds:.1f}s",
                )
            )
            continue

        valid.append(observation)

    # One provider cannot count as two votes. Keep only its newest reading.
    newest_by_source: dict[str, PriceObservation] = {}
    for observation in valid:
        source_key = observation.source.casefold()
        previous = newest_by_source.get(source_key)
        if previous is None:
            newest_by_source[source_key] = observation
            continue

        keep, discard = (
            (observation, previous)
            if observation.provider_timestamp > previous.provider_timestamp
            else (previous, observation)
        )
        newest_by_source[source_key] = keep
        rejected.append(
            RejectedObservation(
                discard,
                "DUPLICATE_SOURCE",
                "A second reading from the same provider is not an independent quorum vote",
            )
        )
    valid = list(newest_by_source.values())

    failure_tuple = tuple(failures)

    if len(valid) == 0:
        return ReconciliationResult(
            market=market,
            status=ReconciliationStatus.NO_QUORUM,
            confidence=Confidence.NONE,
            canonical_price=None,
            candidate_price=None,
            reason="No fresh, valid source observations were available.",
            rejected=tuple(rejected),
            failures=failure_tuple,
        )

    if len(valid) == 1:
        return ReconciliationResult(
            market=market,
            status=ReconciliationStatus.UNVERIFIED_SINGLE_SOURCE,
            confidence=Confidence.LOW,
            canonical_price=None,
            candidate_price=valid[0].price,
            reason=(
                "Only one fresh source was available, so the price was not "
                "independently verified."
            ),
            accepted=tuple(valid),
            rejected=tuple(rejected),
            failures=failure_tuple,
        )

    cluster = _largest_consistent_cluster(valid, max_spread_bps)
    if cluster is None:
        spread_bps = _spread_bps(valid)
        return ReconciliationResult(
            market=market,
            status=ReconciliationStatus.NO_QUORUM,
            confidence=Confidence.NONE,
            canonical_price=None,
            candidate_price=None,
            reason=(
                f"Fresh sources disagreed by {spread_bps:.2f} bps, above the "
                f"{max_spread_bps:.2f} bps limit."
            ),
            accepted=tuple(valid),
            rejected=tuple(rejected),
            failures=failure_tuple,
            spread_bps=spread_bps,
        )

    agreeing, spread_bps = cluster
    agreeing_ids = {id(observation) for observation in agreeing}
    outliers = [observation for observation in valid if id(observation) not in agreeing_ids]
    for observation in outliers:
        rejected.append(
            RejectedObservation(
                observation,
                "PRICE_OUTLIER",
                "Price did not belong to the largest independently agreeing cluster",
            )
        )

    canonical_price = _decimal_median([observation.price for observation in agreeing])
    degraded = bool(outliers or rejected or failure_tuple)

    reason = (
        f"{len(agreeing)} fresh independent sources agreed within the configured "
        "spread limit"
    )
    degraded_reasons: list[str] = []
    if len(outliers) == 1:
        degraded_reasons.append("1 price outlier was excluded")
    elif len(outliers) > 1:
        degraded_reasons.append(f"{len(outliers)} price outliers were excluded")
    validation_rejections = len(rejected) - len(outliers)
    if validation_rejections == 1:
        degraded_reasons.append("1 observation failed validation")
    elif validation_rejections > 1:
        degraded_reasons.append(
            f"{validation_rejections} observations failed validation"
        )
    if len(failure_tuple) == 1:
        degraded_reasons.append("1 source was unavailable")
    elif len(failure_tuple) > 1:
        degraded_reasons.append(f"{len(failure_tuple)} sources were unavailable")
    if degraded_reasons:
        reason += "; " + "; ".join(degraded_reasons) + ", so confidence was reduced."
    else:
        reason += "."

    return ReconciliationResult(
        market=market,
        status=ReconciliationStatus.VERIFIED,
        confidence=Confidence.MEDIUM if degraded else Confidence.HIGH,
        canonical_price=canonical_price,
        candidate_price=None,
        reason=reason,
        accepted=tuple(agreeing),
        rejected=tuple(rejected),
        failures=failure_tuple,
        spread_bps=spread_bps,
    )
