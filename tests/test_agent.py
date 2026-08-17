import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from feedverdict.agent import AgentEventKind, ReconciliationAgent
from feedverdict.models import Confidence, Market, PriceObservation, ReconciliationStatus
from feedverdict.sources import SourceError
from tests.fakes import FakeSource


NOW = datetime(2026, 8, 15, 16, 20, tzinfo=timezone.utc)
MARKET = Market("BTC", "USD")


def observation(source: str, price: str, age_seconds: float = 1) -> PriceObservation:
    return PriceObservation(
        source=source,
        market=MARKET,
        provider_market=f"{source}-BTCUSD",
        price=Decimal(price),
        provider_timestamp=NOW - timedelta(seconds=age_seconds),
        received_at=NOW,
    )


class AgentTests(unittest.TestCase):
    def make_agent(self, *sources: FakeSource) -> ReconciliationAgent:
        return ReconciliationAgent(sources, clock=lambda: NOW)

    def test_stops_after_two_agreeing_sources(self) -> None:
        primary = FakeSource("Primary", observation("Primary", "118000"))
        secondary = FakeSource("Secondary", observation("Secondary", "118020"))
        unused = FakeSource("Unused", observation("Unused", "150000"))

        run = self.make_agent(primary, secondary, unused).run(MARKET)

        self.assertEqual(run.result.status, ReconciliationStatus.VERIFIED)
        self.assertEqual(run.result.confidence, Confidence.HIGH)
        self.assertEqual(run.queried_sources, ("Primary", "Secondary"))
        self.assertEqual(unused.fetch_calls, [])
        self.assertEqual(run.events[-1].code, "STOP_VERIFIED")

    def test_stale_primary_causes_two_alternatives_to_be_consulted(self) -> None:
        stale = FakeSource("Stale", observation("Stale", "100000", age_seconds=600))
        second = FakeSource("Second", observation("Second", "118000"))
        third = FakeSource("Third", observation("Third", "118020"))

        run = self.make_agent(stale, second, third).run(MARKET)

        self.assertEqual(run.result.status, ReconciliationStatus.VERIFIED)
        self.assertEqual(run.result.confidence, Confidence.MEDIUM)
        self.assertEqual(run.queried_sources, ("Stale", "Second", "Third"))
        self.assertIn("FALLBACK_NO_VALID_DATA", [event.code for event in run.events])
        self.assertEqual(run.result.rejected[0].code, "STALE_PROVIDER_TIMESTAMP")

    def test_unavailable_primary_degrades_to_two_alternatives(self) -> None:
        failed = FakeSource("Failed", SourceError("SOURCE_TIMEOUT", "timed out"))
        second = FakeSource("Second", observation("Second", "118000"))
        third = FakeSource("Third", observation("Third", "118020"))

        run = self.make_agent(failed, second, third).run(MARKET)

        self.assertEqual(run.result.status, ReconciliationStatus.VERIFIED)
        self.assertEqual(run.result.confidence, Confidence.MEDIUM)
        self.assertEqual(run.result.failures[0].code, "SOURCE_TIMEOUT")
        self.assertTrue(
            any(
                event.kind == AgentEventKind.ERROR and event.code == "SOURCE_TIMEOUT"
                for event in run.events
            )
        )

    def test_disagreement_triggers_tie_breaker_and_excludes_outlier(self) -> None:
        first = FakeSource("First", observation("First", "118000"))
        outlier = FakeSource("Outlier", observation("Outlier", "150000"))
        tie_breaker = FakeSource("TieBreaker", observation("TieBreaker", "118020"))

        run = self.make_agent(first, outlier, tie_breaker).run(MARKET)

        self.assertEqual(run.result.status, ReconciliationStatus.VERIFIED)
        self.assertEqual(run.result.confidence, Confidence.MEDIUM)
        self.assertEqual(run.result.canonical_price, Decimal("118010"))
        self.assertIn("RESOLVE_DISAGREEMENT", [event.code for event in run.events])
        self.assertEqual(run.result.rejected[0].code, "PRICE_OUTLIER")

    def test_exhaustion_returns_no_quorum_instead_of_guessing(self) -> None:
        first = FakeSource("First", SourceError("SOURCE_TIMEOUT", "timed out"))
        second = FakeSource("Second", SourceError("NETWORK_ERROR", "offline"))

        run = self.make_agent(first, second).run(MARKET)

        self.assertEqual(run.result.status, ReconciliationStatus.NO_QUORUM)
        self.assertIsNone(run.result.canonical_price)
        self.assertEqual(run.events[-1].code, "STOP_SOURCES_EXHAUSTED")


if __name__ == "__main__":
    unittest.main()
