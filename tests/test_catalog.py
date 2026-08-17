import unittest

from feedverdict.catalog import MarketAvailability, discover_markets
from feedverdict.models import Market
from feedverdict.picker import market_label
from feedverdict.sources import SourceError


class DiscoverySource:
    def __init__(self, name: str, markets_or_error) -> None:
        self.name = name
        self.markets_or_error = markets_or_error

    def get_markets(self) -> dict[Market, str]:
        if isinstance(self.markets_or_error, Exception):
            raise self.markets_or_error
        return self.markets_or_error

    def fetch(self, market):
        raise AssertionError("fetch should not run during discovery")


class MarketCatalogTests(unittest.TestCase):
    def test_keeps_only_markets_with_two_source_coverage(self) -> None:
        btc = Market("BTC", "USD")
        eth = Market("ETH", "USD")
        sol = Market("SOL", "EUR")
        sources = [
            DiscoverySource("A", {btc: "a-btc", eth: "a-eth"}),
            DiscoverySource("B", {btc: "b-btc", sol: "b-sol"}),
            DiscoverySource("C", {sol: "c-sol"}),
        ]

        catalog = discover_markets(sources, min_sources=2)

        self.assertEqual([item.market for item in catalog.markets], [btc, sol])
        self.assertEqual(catalog.availability_for(btc).sources, ("A", "B"))
        self.assertIsNone(catalog.availability_for(eth))

    def test_discovery_failure_does_not_hide_other_sources(self) -> None:
        btc = Market("BTC", "USD")
        sources = [
            DiscoverySource("Failed", SourceError("SOURCE_TIMEOUT", "timed out")),
            DiscoverySource("A", {btc: "a-btc"}),
            DiscoverySource("B", {btc: "b-btc"}),
        ]

        catalog = discover_markets(sources)

        self.assertEqual(len(catalog.markets), 1)
        self.assertEqual(catalog.failures[0].code, "SOURCE_TIMEOUT")

    def test_picker_label_contains_common_name_symbol_and_coverage(self) -> None:
        item = MarketAvailability(Market("BTC", "USD"), ("A", "B", "C"))

        label = market_label(item)

        self.assertIn("Bitcoin", label)
        self.assertIn("BTC/USD", label)
        self.assertIn("3 sources", label)


if __name__ == "__main__":
    unittest.main()
