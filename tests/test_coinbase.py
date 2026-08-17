import unittest
from datetime import datetime, timezone
from decimal import Decimal

from feedverdict.http import JsonResponse
from feedverdict.models import Market
from feedverdict.sources.coinbase import CoinbaseSource
from tests.fakes import QueueJsonClient


class CoinbaseSourceTests(unittest.TestCase):
    def test_discovers_and_normalises_ticker(self) -> None:
        received_at = datetime(2026, 8, 15, 16, 20, 5, tzinfo=timezone.utc)
        client = QueueJsonClient(
            [
                JsonResponse(
                    payload=[
                        {
                            "id": "BTC-USD",
                            "base_currency": "BTC",
                            "quote_currency": "USD",
                            "status": "online",
                        },
                        {
                            "id": "OLD-USD",
                            "base_currency": "OLD",
                            "quote_currency": "USD",
                            "status": "offline",
                        },
                    ],
                    received_at=received_at,
                    latency_ms=12.5,
                ),
                JsonResponse(
                    payload={
                        "trade_id": 12345,
                        "price": "118000.25",
                        "time": "2026-08-15T16:20:04.000Z",
                    },
                    received_at=received_at,
                    latency_ms=18.0,
                ),
            ]
        )

        observation = CoinbaseSource(client).fetch(Market("btc", "usd"))

        self.assertEqual(observation.market, Market("BTC", "USD"))
        self.assertEqual(observation.provider_market, "BTC-USD")
        self.assertEqual(observation.price, Decimal("118000.25"))
        self.assertEqual(observation.event_id, "12345")
        self.assertEqual(observation.provider_timestamp.second, 4)
        self.assertEqual(len(client.calls), 2)


if __name__ == "__main__":
    unittest.main()

