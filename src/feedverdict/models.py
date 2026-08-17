# Data structures shared by the sources and reconciliation logic.

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class ReconciliationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED_SINGLE_SOURCE = "UNVERIFIED_SINGLE_SOURCE"
    NO_QUORUM = "NO_QUORUM"


# A provider-independent market such as BTC/USD.
@dataclass(frozen=True, slots=True)
class Market:
    base: str
    quote: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "base", self.base.strip().upper())
        object.__setattr__(self, "quote", self.quote.strip().upper())
        if not self.base or not self.quote:
            raise ValueError("Market base and quote symbols cannot be empty")

    @property
    def symbol(self) -> str:
        return f"{self.base}/{self.quote}"


# One price reading converted into the same format for every provider.
@dataclass(frozen=True, slots=True)
class PriceObservation:
    source: str
    market: Market
    provider_market: str
    price: Decimal
    provider_timestamp: datetime
    received_at: datetime
    event_id: str | None = None
    latency_ms: float | None = None

    def age_seconds(self, reference_time: datetime) -> float:
        return (reference_time - self.provider_timestamp).total_seconds()


@dataclass(frozen=True, slots=True)
class SourceFailure:
    source: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class RejectedObservation:
    observation: PriceObservation
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    market: Market
    status: ReconciliationStatus
    confidence: Confidence
    canonical_price: Decimal | None
    candidate_price: Decimal | None
    reason: str
    accepted: tuple[PriceObservation, ...] = field(default_factory=tuple)
    rejected: tuple[RejectedObservation, ...] = field(default_factory=tuple)
    failures: tuple[SourceFailure, ...] = field(default_factory=tuple)
    spread_bps: Decimal | None = None

    @property
    def canonical_updated(self) -> bool:
        return self.canonical_price is not None
