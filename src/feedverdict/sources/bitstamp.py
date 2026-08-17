# Bitstamp public market-data adapter.

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from feedverdict.http import HttpClientError, JsonHttpClient
from feedverdict.models import Market, PriceObservation

from .base import SourceError, UnsupportedMarketError


class BitstampSource:
    name = "Bitstamp"
    _BASE_URL = "https://www.bitstamp.net/api/v2"

    def __init__(self, client: JsonHttpClient) -> None:
        self.client = client
        self._markets: dict[Market, str] | None = None

    def get_markets(self) -> dict[Market, str]:
        if self._markets is not None:
            return self._markets

        try:
            response = self.client.get_json(f"{self._BASE_URL}/markets/")
        except HttpClientError as exc:
            raise SourceError(exc.code, f"Bitstamp market discovery failed: {exc}") from exc

        if not isinstance(response.payload, list):
            raise SourceError("SCHEMA_INVALID", "Bitstamp markets response was not a list")

        markets: dict[Market, str] = {}
        try:
            for details in response.payload:
                if details.get("trading") != "Enabled":
                    continue
                if details.get("market_type", "SPOT") != "SPOT":
                    continue
                market = Market(
                    base=str(details["base_currency"]),
                    quote=str(details["counter_currency"]),
                )
                markets[market] = str(details["market_symbol"])
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise SourceError("SCHEMA_INVALID", "Bitstamp market schema was invalid") from exc

        self._markets = markets
        return markets

    def fetch(self, market: Market) -> PriceObservation:
        provider_market = self.get_markets().get(market)
        if provider_market is None:
            raise UnsupportedMarketError(self.name, market)

        try:
            response = self.client.get_json(
                f"{self._BASE_URL}/ticker/{provider_market}/"
            )
        except HttpClientError as exc:
            raise SourceError(exc.code, f"Bitstamp ticker request failed: {exc}") from exc

        payload = response.payload
        if not isinstance(payload, dict):
            raise SourceError("SCHEMA_INVALID", "Bitstamp ticker response was not an object")

        try:
            price = Decimal(str(payload["last"]))
            raw_timestamp = str(payload["timestamp"])
            provider_timestamp = datetime.fromtimestamp(
                float(raw_timestamp),
                tz=timezone.utc,
            )
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise SourceError("SCHEMA_INVALID", "Bitstamp ticker fields were invalid") from exc

        return PriceObservation(
            source=self.name,
            market=market,
            provider_market=provider_market,
            price=price,
            provider_timestamp=provider_timestamp,
            received_at=response.received_at,
            event_id=raw_timestamp,
            latency_ms=response.latency_ms,
        )
