# Multi-step planning loop for price reconciliation.

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Mapping

from feedverdict.models import (
    Market,
    PriceObservation,
    ReconciliationResult,
    ReconciliationStatus,
    SourceFailure,
)
from feedverdict.reconciliation import reconcile
from feedverdict.sources import PriceSource, SourceError


class AgentEventKind(StrEnum):
    PLAN = "PLAN"
    OBSERVE = "OBSERVE"
    ERROR = "ERROR"
    ASSESS = "ASSESS"
    DECIDE = "DECIDE"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    sequence: int
    kind: AgentEventKind
    code: str
    message: str
    source: str | None = None


@dataclass(frozen=True, slots=True)
class AgentPolicy:
    max_age_seconds: float = 120.0
    max_future_skew_seconds: float = 5.0
    max_spread_bps: Decimal = Decimal("100")

    def __post_init__(self) -> None:
        if self.max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        if self.max_future_skew_seconds < 0:
            raise ValueError("max_future_skew_seconds cannot be negative")
        if self.max_spread_bps <= 0:
            raise ValueError("max_spread_bps must be positive")


@dataclass(frozen=True, slots=True)
class AgentRun:
    result: ReconciliationResult
    events: tuple[AgentEvent, ...]
    completed_at: datetime

    @property
    def queried_sources(self) -> tuple[str, ...]:
        return tuple(
            event.source
            for event in self.events
            if event.kind == AgentEventKind.PLAN and event.source is not None
        )


# The agent checks the result after every source response. It stops once it has
# enough independent evidence, or fetches another source when it does not.
class ReconciliationAgent:

    def __init__(
        self,
        sources: Iterable[PriceSource],
        *,
        policy: AgentPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        source_scores: Mapping[str, float] | None = None,
    ) -> None:
        self.sources = tuple(sources)
        if len(self.sources) < 2:
            raise ValueError("At least two independent price sources are required")

        source_names = [source.name.casefold() for source in self.sources]
        if len(source_names) != len(set(source_names)):
            raise ValueError("Source names must be unique")

        self.policy = policy or AgentPolicy()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.source_scores = {
            name.casefold(): score for name, score in (source_scores or {}).items()
        }

    def run(self, market: Market) -> AgentRun:
        observations: list[PriceObservation] = []
        failures: list[SourceFailure] = []
        events: list[AgentEvent] = []

        def record(
            kind: AgentEventKind,
            code: str,
            message: str,
            source: str | None = None,
        ) -> None:
            events.append(AgentEvent(len(events) + 1, kind, code, message, source))

        now = self.clock()
        result = self._reconcile(market, observations, failures, now)

        for source_index, source in enumerate(self.sources):
            plan_code, plan_reason = self._next_plan(result, source_index)
            score = self.source_scores.get(source.name.casefold())
            score_message = (
                f" Historical reliability score: {score:.3f}." if score is not None else ""
            )
            record(
                AgentEventKind.PLAN,
                plan_code,
                f"Select {source.name}: {plan_reason}.{score_message}",
                source.name,
            )

            try:
                observation = source.fetch(market)
            except SourceError as exc:
                failure = SourceFailure(source.name, exc.code, str(exc))
                failures.append(failure)
                record(AgentEventKind.ERROR, exc.code, str(exc), source.name)
            except Exception as exc:  # Do not let one broken adapter stop the run.
                failure = SourceFailure(
                    source.name,
                    "UNEXPECTED_SOURCE_ERROR",
                    f"{type(exc).__name__}: {exc}",
                )
                failures.append(failure)
                record(
                    AgentEventKind.ERROR,
                    failure.code,
                    failure.message,
                    source.name,
                )
            else:
                observations.append(observation)
                record(
                    AgentEventKind.OBSERVE,
                    "SOURCE_OBSERVATION",
                    (
                        f"Received {observation.price} {market.quote} with provider "
                        "timestamp "
                        f"{observation.provider_timestamp.isoformat(timespec='seconds')}"
                    ),
                    source.name,
                )

            now = self.clock()
            result = self._reconcile(market, observations, failures, now)
            record(
                AgentEventKind.ASSESS,
                result.status.value,
                result.reason,
                source.name,
            )

            if result.status == ReconciliationStatus.VERIFIED:
                record(
                    AgentEventKind.DECIDE,
                    "STOP_VERIFIED",
                    "Stop fetching: an independent price quorum has been established.",
                )
                return AgentRun(result, tuple(events), now)

        record(
            AgentEventKind.DECIDE,
            "STOP_SOURCES_EXHAUSTED",
            "Stop fetching: every eligible source was tried without establishing "
            "stronger evidence.",
        )
        return AgentRun(result, tuple(events), now)

    def _reconcile(
        self,
        market: Market,
        observations: list[PriceObservation],
        failures: list[SourceFailure],
        now: datetime,
    ) -> ReconciliationResult:
        return reconcile(
            market,
            observations,
            failures=failures,
            now=now,
            max_age_seconds=self.policy.max_age_seconds,
            max_future_skew_seconds=self.policy.max_future_skew_seconds,
            max_spread_bps=self.policy.max_spread_bps,
        )

    @staticmethod
    def _next_plan(
        result: ReconciliationResult,
        source_index: int,
    ) -> tuple[str, str]:
        if source_index == 0:
            return "FETCH_PRIMARY", "begin with the highest-priority source"

        if result.status == ReconciliationStatus.UNVERIFIED_SINGLE_SOURCE:
            return (
                "VERIFY_SINGLE_SOURCE",
                "one fresh price exists, but it needs an independent vote",
            )

        if len(result.accepted) >= 2:
            return (
                "RESOLVE_DISAGREEMENT",
                "fresh sources disagree, so another source is needed as a tie-breaker",
            )

        if result.failures or result.rejected:
            return (
                "FALLBACK_NO_VALID_DATA",
                "earlier evidence failed availability or validation checks",
            )

        return "GATHER_EVIDENCE", "more independent evidence is required"
