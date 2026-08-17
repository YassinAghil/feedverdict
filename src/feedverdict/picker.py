# Fuzzy market picker used by the interactive command.

from __future__ import annotations

import sys

from feedverdict.catalog import MarketAvailability
from feedverdict.models import Market


class PickerUnavailableError(RuntimeError):
    pass


# These names let users search for "bitcoin" as well as "BTC".
_COMMON_ASSET_NAMES = {
    "ADA": "Cardano",
    "AVAX": "Avalanche",
    "BCH": "Bitcoin Cash",
    "BTC": "Bitcoin",
    "DOGE": "Dogecoin",
    "DOT": "Polkadot",
    "ETH": "Ethereum",
    "LINK": "Chainlink",
    "LTC": "Litecoin",
    "MATIC": "Polygon",
    "PEPE": "Pepe",
    "SHIB": "Shiba Inu",
    "SOL": "Solana",
    "TRX": "Tron",
    "UNI": "Uniswap",
    "USDC": "USD Coin",
    "USDT": "Tether",
    "XLM": "Stellar",
    "XRP": "XRP",
}


def market_label(item: MarketAvailability) -> str:
    asset_name = _COMMON_ASSET_NAMES.get(item.market.base)
    name_prefix = f"{asset_name} · " if asset_name else ""
    source_word = "source" if len(item.sources) == 1 else "sources"
    return (
        f"{name_prefix}{item.market.symbol:<12} "
        f"{len(item.sources)} {source_word} · {', '.join(item.sources)}"
    )


# InquirerPy usually returns the value directly, but some versions return a
# Choice-like object instead. Accept both forms here.
def _resolve_market_selection(
    selected,
    markets: tuple[MarketAvailability, ...],
) -> Market:
    selected_symbol = getattr(selected, "value", selected)
    if not isinstance(selected_symbol, str):
        raise PickerUnavailableError("No market was selected.")

    markets_by_symbol = {item.market.symbol: item.market for item in markets}
    try:
        return markets_by_symbol[selected_symbol]
    except KeyError as exc:
        raise PickerUnavailableError("No market was selected.") from exc


def choose_market(markets: tuple[MarketAvailability, ...]) -> Market:
    if not markets:
        raise PickerUnavailableError(
            "No market is currently available from at least two independent sources."
        )
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise PickerUnavailableError(
            "The fuzzy picker needs an interactive terminal; pass a symbol directly, "
            "for example: feedverdict BTC USD"
        )

    try:
        from InquirerPy import inquirer
        from InquirerPy.base.control import Choice
    except ImportError as exc:
        raise PickerUnavailableError(
            "The interactive picker is not installed. Run `pip install -e .` first."
        ) from exc

    choices = [
        Choice(value=item.market.symbol, name=market_label(item)) for item in markets
    ]
    selected = inquirer.fuzzy(
        message="Choose a cryptocurrency market:",
        choices=choices,
        instruction="Type to filter · ↑/↓ move · Enter selects",
        border=True,
        max_height=15,
        mandatory=True,
    ).execute()
    return _resolve_market_selection(selected, markets)
