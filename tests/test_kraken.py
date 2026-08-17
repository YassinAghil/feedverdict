import unittest
from datetime import datetime, timezone
from decimal import Decimal

from feedverdict.http import JsonResponse
from feedverdict.models import Market
from feedverdict.sources.kraken import KrakenSource
from tests.fakes import QueueJsonClient


class KrakenSourceTests(unittest.TestCase):
    def test_discovers_and_normalises_latest_trade(self) -> None:
        received_at = datetime(2026, 8, 15, 16, 20, 5, tzinfo=timezone.utc)
        trade_time = datetime(2026, 8, 15, 16, 20, 4, tzinfo=timezone.utc).timestamp()
        client = QueueJsonClient(
            [
                JsonResponse(
                    payload={
                        "error": [],
                        "result": {
                            "BTC/USD": {
                                "base": "BTC",
                                "quote": "USD",
                                "altname": "XBTUSD",
                            }
                        },
                    },
                    received_at=received_at,
                    latency_ms=14.0,
                ),
                JsonResponse(
                    payload={
                        "error": [],
                        "result": {
                            "BTC/USD": [
                                ["118020.10", "0.01", trade_time, "b", "m", "", 98765]
                            ],
                            "last": "123456789",
                        },
                    },
                    received_at=received_at,
                    latency_ms=20.0,
                ),
            ]
        )

        observation = KrakenSource(client).fetch(Market("BTC", "USD"))

        self.assertEqual(observation.provider_market, "XBTUSD")
        self.assertEqual(observation.price, Decimal("118020.10"))
        self.assertEqual(observation.event_id, "98765")
        self.assertEqual(observation.provider_timestamp.second, 4)
        self.assertEqual(client.calls[1][1]["pair"], "XBTUSD")


if __name__ == "__main__":
    unittest.main()

