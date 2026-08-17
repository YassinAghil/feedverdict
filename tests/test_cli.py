import unittest
from decimal import Decimal

from feedverdict.cli import _money


class CliFormattingTests(unittest.TestCase):
    def test_tiny_usd_price_does_not_round_to_zero(self) -> None:
        self.assertEqual(_money(Decimal("0.000004540"), "USD"), "$0.00000454")
        self.assertEqual(_money(Decimal("118000"), "USD"), "$118,000.00")


if __name__ == "__main__":
    unittest.main()
