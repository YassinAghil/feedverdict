import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from feedverdict.agent import ReconciliationAgent
from feedverdict.models import Market, PriceObservation, ReconciliationStatus
from feedverdict.sources import SourceError
from feedverdict.state import StateStore
from tests.fakes import FakeSource


NOW = datetime(2026, 8, 15, 16, 20, tzinfo=timezone.utc)
MARKET = Market("BTC", "USD")


def observation(source: str, price: str) -> PriceObservation:
    return PriceObservation(
        source=source,
        market=MARKET,
        provider_market=f"{source}-BTCUSD",
        price=Decimal(price),
        provider_timestamp=NOW - timedelta(seconds=1),
        received_at=NOW,
        latency_ms=10,
    )


class StateStoreTests(unittest.TestCase):
    def test_persists_verified_canonical_and_ranks_failed_source_lower(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.sqlite3")
            failed = FakeSource("Failed", SourceError("SOURCE_TIMEOUT", "timed out"))
            second = FakeSource("Second", observation("Second", "118000"))
            third = FakeSource("Third", observation("Third", "118020"))
            run = ReconciliationAgent(
                [failed, second, third], clock=lambda: NOW
            ).run(MARKET)

            run_id = store.record_run(run)
            canonical = store.canonical(MARKET)
            ranked = store.rank_sources([failed, second, third])

            self.assertEqual(run.result.status, ReconciliationStatus.VERIFIED)
            self.assertIsNotNone(canonical)
            self.assertEqual(canonical.price, Decimal("118010"))
            self.assertEqual(canonical.run_id, run_id)
            self.assertEqual(ranked[-1].name, "Failed")
            self.assertLess(
                store.health("Failed").reliability_score,
                store.health("Second").reliability_score,
            )

    def test_no_quorum_run_does_not_overwrite_last_verified_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.sqlite3")
            verified = ReconciliationAgent(
                [
                    FakeSource("A", observation("A", "118000")),
                    FakeSource("B", observation("B", "118020")),
                ],
                clock=lambda: NOW,
            ).run(MARKET)
            first_run_id = store.record_run(verified)

            failed = ReconciliationAgent(
                [
                    FakeSource("A", SourceError("SOURCE_TIMEOUT", "timed out")),
                    FakeSource("B", SourceError("NETWORK_ERROR", "offline")),
                ],
                clock=lambda: NOW,
            ).run(MARKET)
            store.record_run(failed)
            canonical = store.canonical(MARKET)

            self.assertEqual(failed.result.status, ReconciliationStatus.NO_QUORUM)
            self.assertEqual(canonical.run_id, first_run_id)
            self.assertEqual(canonical.price, Decimal("118010"))


if __name__ == "__main__":
    unittest.main()
