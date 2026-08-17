import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from feedverdict.models import (
    Confidence,
    Market,
    PriceObservation,
    ReconciliationStatus,
)
from feedverdict.reconciliation import reconcile


NOW = datetime(2026, 8, 15, 16, 20, tzinfo=timezone.utc)
MARKET = Market("BTC", "USD")


def observation(source: str, price: str, age_seconds: float) -> PriceObservation:
    return PriceObservation(
        source=source,
        market=MARKET,
        provider_market=f"{source}-BTCUSD",
        price=Decimal(price),
        provider_timestamp=NOW - timedelta(seconds=age_seconds),
        received_at=NOW,
    )


class ReconciliationTests(unittest.TestCase):
    def test_two_fresh_close_sources_produce_verified_median(self) -> None:
        result = reconcile(
            MARKET,
            [observation("A", "118000", 1), observation("B", "118020", 2)],
            now=NOW,
        )

        self.assertEqual(result.status, ReconciliationStatus.VERIFIED)
        self.assertEqual(result.confidence, Confidence.HIGH)
        self.assertEqual(result.canonical_price, Decimal("118010"))
        self.assertTrue(result.canonical_updated)

    def test_stale_source_is_rejected_and_single_fresh_price_is_unverified(self) -> None:
        result = reconcile(
            MARKET,
            [observation("stale", "110000", 600), observation("fresh", "118020", 2)],
            now=NOW,
            max_age_seconds=120,
        )

        self.assertEqual(result.status, ReconciliationStatus.UNVERIFIED_SINGLE_SOURCE)
        self.assertEqual(result.candidate_price, Decimal("118020"))
        self.assertIsNone(result.canonical_price)
        self.assertEqual(result.rejected[0].code, "STALE_PROVIDER_TIMESTAMP")

    def test_large_disagreement_refuses_to_create_canonical_price(self) -> None:
        result = reconcile(
            MARKET,
            [observation("A", "118000", 1), observation("B", "150000", 1)],
            now=NOW,
            max_spread_bps=Decimal("100"),
        )

        self.assertEqual(result.status, ReconciliationStatus.NO_QUORUM)
        self.assertEqual(result.confidence, Confidence.NONE)
        self.assertIsNone(result.canonical_price)
        self.assertGreater(result.spread_bps, Decimal("100"))

    def test_future_timestamp_is_rejected(self) -> None:
        result = reconcile(
            MARKET,
            [observation("future", "118000", -30)],
            now=NOW,
        )

        self.assertEqual(result.status, ReconciliationStatus.NO_QUORUM)
        self.assertEqual(result.rejected[0].code, "FUTURE_PROVIDER_TIMESTAMP")

    def test_two_readings_from_same_source_do_not_create_false_quorum(self) -> None:
        result = reconcile(
            MARKET,
            [observation("same", "118000", 2), observation("same", "118020", 1)],
            now=NOW,
        )

        self.assertEqual(result.status, ReconciliationStatus.UNVERIFIED_SINGLE_SOURCE)
        self.assertEqual(result.candidate_price, Decimal("118020"))
        self.assertEqual(result.rejected[0].code, "DUPLICATE_SOURCE")


if __name__ == "__main__":
    unittest.main()
