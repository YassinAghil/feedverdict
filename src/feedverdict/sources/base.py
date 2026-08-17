# Shared source interface and errors.

from __future__ import annotations

from typing import Protocol

from feedverdict.models import Market, PriceObservation


class SourceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class UnsupportedMarketError(SourceError):
    def __init__(self, source: str, market: Market) -> None:
        super().__init__(
            "UNSUPPORTED_MARKET",
            f"{source} does not offer {market.symbol}",
        )


class PriceSource(Protocol):
    name: str

    def get_markets(self) -> dict[Market, str]: ...

    def fetch(self, market: Market) -> PriceObservation: ...
