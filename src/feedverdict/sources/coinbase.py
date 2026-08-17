# Coinbase Exchange market-data adapter.

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from feedverdict.http import HttpClientError, JsonHttpClient
from feedverdict.models import Market, PriceObservation

from .base import SourceError, UnsupportedMarketError


class CoinbaseSource:
    name = "Coinbase"
    _BASE_URL = "https://api.exchange.coinbase.com"

    def __init__(self, client: JsonHttpClient) -> None:
        self.client = client
        self._markets: dict[Market, str] | None = None

    def get_markets(self) -> dict[Market, str]:
        if self._markets is not None:
            return self._markets

        try:
            response = self.client.get_json(f"{self._BASE_URL}/products")
        except HttpClientError as exc:
            raise SourceError(exc.code, f"Coinbase market discovery failed: {exc}") from exc

        if not isinstance(response.payload, list):
            raise SourceError("SCHEMA_INVALID", "Coinbase products response was not a list")

        markets: dict[Market, str] = {}
        try:
            for product in response.payload:
                if product.get("status") != "online":
                    continue
                market = Market(
                    base=str(product["base_currency"]),
                    quote=str(product["quote_currency"]),
                )
                markets[market] = str(product["id"])
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise SourceError("SCHEMA_INVALID", "Coinbase product schema was invalid") from exc

        self._markets = markets
        return markets

    def fetch(self, market: Market) -> PriceObservation:
        provider_market = self.get_markets().get(market)
        if provider_market is None:
            raise UnsupportedMarketError(self.name, market)

        try:
            response = self.client.get_json(
                f"{self._BASE_URL}/products/{provider_market}/ticker"
            )
        except HttpClientError as exc:
            raise SourceError(exc.code, f"Coinbase ticker request failed: {exc}") from exc

        payload = response.payload
        if not isinstance(payload, dict):
            raise SourceError("SCHEMA_INVALID", "Coinbase ticker response was not an object")

        try:
            price = Decimal(str(payload["price"]))
            provider_timestamp = datetime.fromisoformat(
                str(payload["time"]).replace("Z", "+00:00")
            )
            event_id = str(payload["trade_id"])
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise SourceError("SCHEMA_INVALID", "Coinbase ticker fields were invalid") from exc

        return PriceObservation(
            source=self.name,
            market=market,
            provider_market=provider_market,
            price=price,
            provider_timestamp=provider_timestamp,
            received_at=response.received_at,
            event_id=event_id,
            latency_ms=response.latency_ms,
        )
