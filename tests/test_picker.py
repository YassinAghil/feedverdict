import unittest
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from feedverdict.catalog import MarketAvailability
from feedverdict.models import Market
from feedverdict.picker import (
    PickerUnavailableError,
    _resolve_market_selection,
    choose_market,
)


class ChoiceLike:
    def __init__(self, value):
        self.value = value


class FakeTTY:
    def isatty(self):
        return True


class PickerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.shib = Market("SHIB", "USD")
        self.markets = (
            MarketAvailability(
                self.shib,
                ("Coinbase", "Kraken", "Bitstamp"),
            ),
        )

    def test_resolves_selected_symbol(self) -> None:
        selected = _resolve_market_selection("SHIB/USD", self.markets)

        self.assertEqual(selected, self.shib)

    def test_accepts_choice_like_return_value(self) -> None:
        selected = _resolve_market_selection(
            ChoiceLike("SHIB/USD"),
            self.markets,
        )

        self.assertEqual(selected, self.shib)

    def test_rejects_unknown_selection(self) -> None:
        with self.assertRaises(PickerUnavailableError):
            _resolve_market_selection("UNKNOWN/USD", self.markets)

    def test_picker_uses_symbol_value_and_returns_market(self) -> None:
        class FakeChoice:
            def __init__(self, value, name):
                self.value = value
                self.name = name

        class FakePrompt:
            def __init__(self, result):
                self.result = result

            def execute(self):
                return self.result

        def fake_fuzzy(**options):
            self.assertEqual(options["choices"][0].value, "SHIB/USD")
            return FakePrompt(options["choices"][0].value)

        inquirerpy = ModuleType("InquirerPy")
        inquirerpy.inquirer = SimpleNamespace(fuzzy=fake_fuzzy)
        inquirerpy_base = ModuleType("InquirerPy.base")
        inquirerpy_control = ModuleType("InquirerPy.base.control")
        inquirerpy_control.Choice = FakeChoice

        fake_modules = {
            "InquirerPy": inquirerpy,
            "InquirerPy.base": inquirerpy_base,
            "InquirerPy.base.control": inquirerpy_control,
        }
        with (
            patch.dict(sys.modules, fake_modules),
            patch("feedverdict.picker.sys.stdin", FakeTTY()),
            patch("feedverdict.picker.sys.stdout", FakeTTY()),
        ):
            selected = choose_market(self.markets)

        self.assertEqual(selected, self.shib)


if __name__ == "__main__":
    unittest.main()
