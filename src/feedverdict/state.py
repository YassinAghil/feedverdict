# Stores canonical prices, decision traces, and source health in SQLite.

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable, TypeVar

from feedverdict.agent import AgentRun
from feedverdict.models import Confidence, Market, ReconciliationStatus
from feedverdict.paths import app_home
from feedverdict.sources import PriceSource


@dataclass(frozen=True, slots=True)
class SourceHealth:
    source: str
    successes: int
    failures: int
    stale_observations: int
    price_outliers: int
    consecutive_failures: int
    ewma_latency_ms: float | None
    reliability_score: float


@dataclass(frozen=True, slots=True)
class CanonicalRecord:
    market: Market
    price: Decimal
    confidence: Confidence
    sources: tuple[str, ...]
    provider_timestamp: datetime
    updated_at: datetime
    run_id: str


SourceT = TypeVar("SourceT", bound=PriceSource)


class StateStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (app_home() / "feedverdict.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._initialise()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialise(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_health (
                    source TEXT PRIMARY KEY COLLATE NOCASE,
                    successes INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0,
                    stale_observations INTEGER NOT NULL DEFAULT 0,
                    price_outliers INTEGER NOT NULL DEFAULT 0,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    ewma_latency_ms REAL,
                    last_event_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS canonical_prices (
                    base TEXT NOT NULL,
                    quote TEXT NOT NULL,
                    price TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    provider_timestamp TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    PRIMARY KEY (base, quote)
                );

                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    base TEXT NOT NULL,
                    quote TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    canonical_price TEXT,
                    candidate_price TEXT,
                    reason TEXT NOT NULL,
                    trace_json TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );
                """
            )
        os.chmod(self.path, 0o600)

    @staticmethod
    def _score(
        successes: int,
        failures: int,
        stale_observations: int,
        price_outliers: int,
        consecutive_failures: int,
    ) -> float:
        # New sources start at 0.8. Bad or stale responses lower the score,
        # and consecutive failures make it fall faster.
        weighted_bad = failures + stale_observations + (price_outliers * 0.5)
        evidence_score = (successes + 4.0) / (successes + weighted_bad + 5.0)
        recency_penalty = 0.85**consecutive_failures
        return max(0.0, min(1.0, evidence_score * recency_penalty))

    def health(self, source: str) -> SourceHealth:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_health WHERE source = ? COLLATE NOCASE",
                (source,),
            ).fetchone()
        if row is None:
            return SourceHealth(source, 0, 0, 0, 0, 0, None, 0.8)
        score = self._score(
            row["successes"],
            row["failures"],
            row["stale_observations"],
            row["price_outliers"],
            row["consecutive_failures"],
        )
        return SourceHealth(
            source=row["source"],
            successes=row["successes"],
            failures=row["failures"],
            stale_observations=row["stale_observations"],
            price_outliers=row["price_outliers"],
            consecutive_failures=row["consecutive_failures"],
            ewma_latency_ms=row["ewma_latency_ms"],
            reliability_score=score,
        )

    def all_health(self) -> tuple[SourceHealth, ...]:
        with self._connect() as connection:
            names = [
                row["source"]
                for row in connection.execute(
                    "SELECT source FROM source_health ORDER BY source COLLATE NOCASE"
                )
            ]
        return tuple(self.health(name) for name in names)

    def rank_sources(self, sources: Iterable[SourceT]) -> list[SourceT]:
        indexed = list(enumerate(sources))
        return [
            source
            for _index, source in sorted(
                indexed,
                key=lambda item: (
                    -self.health(item[1].name).reliability_score,
                    item[0],
                ),
            )
        ]

    def record_run(self, run: AgentRun) -> str:
        run_id = str(uuid.uuid4())
        result = run.result
        accepted = {item.source.casefold(): item for item in result.accepted}
        rejected: dict[str, list] = {}
        for item in result.rejected:
            rejected.setdefault(item.observation.source.casefold(), []).append(item)
        failures = {item.source.casefold(): item for item in result.failures}

        trace = [
            {
                "sequence": event.sequence,
                "kind": event.kind.value,
                "code": event.code,
                "source": event.source,
                "message": event.message,
            }
            for event in run.events
        ]

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_runs (
                    run_id, base, quote, status, confidence, canonical_price,
                    candidate_price, reason, trace_json, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    result.market.base,
                    result.market.quote,
                    result.status.value,
                    result.confidence.value,
                    str(result.canonical_price) if result.canonical_price is not None else None,
                    str(result.candidate_price) if result.candidate_price is not None else None,
                    result.reason,
                    json.dumps(trace, separators=(",", ":")),
                    run.completed_at.isoformat(),
                ),
            )

            for source in run.queried_sources:
                key = source.casefold()
                observation = accepted.get(key)
                source_rejections = rejected.get(key, [])
                failure = failures.get(key)
                success_delta = int(observation is not None)
                stale_delta = int(
                    any(item.code == "STALE_PROVIDER_TIMESTAMP" for item in source_rejections)
                )
                outlier_delta = int(any(item.code == "PRICE_OUTLIER" for item in source_rejections))
                invalid_delta = int(
                    any(
                        item.code not in {"STALE_PROVIDER_TIMESTAMP", "PRICE_OUTLIER"}
                        for item in source_rejections
                    )
                )
                # Not supporting a market does not make a source unreliable.
                failure_delta = int(
                    failure is not None and failure.code != "UNSUPPORTED_MARKET"
                ) + invalid_delta
                latency = observation.latency_ms if observation is not None else None
                if latency is None and source_rejections:
                    latency = source_rejections[0].observation.latency_ms
                failed_now = bool(failure_delta or stale_delta)

                connection.execute(
                    """
                    INSERT INTO source_health (
                        source, successes, failures, stale_observations,
                        price_outliers, consecutive_failures, ewma_latency_ms,
                        last_event_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source) DO UPDATE SET
                        successes = successes + excluded.successes,
                        failures = failures + excluded.failures,
                        stale_observations = stale_observations + excluded.stale_observations,
                        price_outliers = price_outliers + excluded.price_outliers,
                        consecutive_failures = CASE
                            WHEN excluded.successes > 0 THEN 0
                            WHEN excluded.consecutive_failures > 0
                                THEN consecutive_failures + 1
                            ELSE consecutive_failures
                        END,
                        ewma_latency_ms = CASE
                            WHEN excluded.ewma_latency_ms IS NULL THEN ewma_latency_ms
                            WHEN ewma_latency_ms IS NULL THEN excluded.ewma_latency_ms
                            ELSE (ewma_latency_ms * 0.8) + (excluded.ewma_latency_ms * 0.2)
                        END,
                        last_event_at = excluded.last_event_at
                    """,
                    (
                        source,
                        success_delta,
                        failure_delta,
                        stale_delta,
                        outlier_delta,
                        int(failed_now),
                        latency,
                        run.completed_at.isoformat(),
                    ),
                )

            if (
                result.status == ReconciliationStatus.VERIFIED
                and result.canonical_price is not None
                and result.accepted
            ):
                provider_timestamp = max(
                    observation.provider_timestamp for observation in result.accepted
                )
                connection.execute(
                    """
                    INSERT INTO canonical_prices (
                        base, quote, price, confidence, sources_json,
                        provider_timestamp, updated_at, run_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(base, quote) DO UPDATE SET
                        price = excluded.price,
                        confidence = excluded.confidence,
                        sources_json = excluded.sources_json,
                        provider_timestamp = excluded.provider_timestamp,
                        updated_at = excluded.updated_at,
                        run_id = excluded.run_id
                    """,
                    (
                        result.market.base,
                        result.market.quote,
                        str(result.canonical_price),
                        result.confidence.value,
                        json.dumps([item.source for item in result.accepted]),
                        provider_timestamp.isoformat(),
                        run.completed_at.isoformat(),
                        run_id,
                    ),
                )
        return run_id

    def canonical(self, market: Market) -> CanonicalRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM canonical_prices WHERE base = ? AND quote = ?",
                (market.base, market.quote),
            ).fetchone()
        if row is None:
            return None
        return CanonicalRecord(
            market=market,
            price=Decimal(row["price"]),
            confidence=Confidence(row["confidence"]),
            sources=tuple(json.loads(row["sources_json"])),
            provider_timestamp=datetime.fromisoformat(row["provider_timestamp"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            run_id=row["run_id"],
        )
