import unittest
from datetime import datetime, timezone
from decimal import Decimal

from feedverdict.http import JsonResponse
from feedverdict.models import Market
from feedverdict.sources.bitstamp import BitstampSource
from tests.fakes import QueueJsonClient


class BitstampSourceTests(unittest.TestCase):
    def test_discovers_and_normalises_ticker(self) -> None:
        received_at = datetime(2026, 8, 15, 16, 20, 5, tzinfo=timezone.utc)
        trade_time = datetime(2026, 8, 15, 16, 20, 4, tzinfo=timezone.utc)
        client = QueueJsonClient(
            [
                JsonResponse(
                    payload=[
                        {
                            "name": "BTC/USD",
                            "market_symbol": "btcusd",
                            "base_currency": "BTC",
                            "counter_currency": "USD",
                            "trading": "Enabled",
                            "market_type": "SPOT",
                        },
                        {
                            "name": "OLD/USD",
                            "market_symbol": "oldusd",
                            "base_currency": "OLD",
                            "counter_currency": "USD",
                            "trading": "Disabled",
                            "market_type": "SPOT",
                        },
                    ],
                    received_at=received_at,
                    latency_ms=16.0,
                ),
                JsonResponse(
                    payload={
                        "last": "118015.75",
                        "timestamp": str(int(trade_time.timestamp())),
                    },
                    received_at=received_at,
                    latency_ms=22.0,
                ),
            ]
        )

        observation = BitstampSource(client).fetch(Market("btc", "usd"))

        self.assertEqual(observation.provider_market, "btcusd")
        self.assertEqual(observation.price, Decimal("118015.75"))
        self.assertEqual(observation.event_id, str(int(trade_time.timestamp())))
        self.assertEqual(observation.provider_timestamp, trade_time)
        self.assertTrue(client.calls[1][0].endswith("/ticker/btcusd/"))


if __name__ == "__main__":
    unittest.main()
