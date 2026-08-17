# Kraken public market-data adapter.

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from feedverdict.http import HttpClientError, JsonHttpClient
from feedverdict.models import Market, PriceObservation

from .base import SourceError, UnsupportedMarketError


class KrakenSource:
    name = "Kraken"
    _BASE_URL = "https://api.kraken.com/0"

    def __init__(self, client: JsonHttpClient) -> None:
        self.client = client
        self._markets: dict[Market, str] | None = None

    def get_markets(self) -> dict[Market, str]:
        if self._markets is not None:
            return self._markets

        try:
            response = self.client.get_json(
                f"{self._BASE_URL}/public/AssetPairs",
                query={"assetVersion": "1"},
            )
        except HttpClientError as exc:
            raise SourceError(exc.code, f"Kraken market discovery failed: {exc}") from exc

        payload = response.payload
        if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
            raise SourceError("SCHEMA_INVALID", "Kraken asset-pairs response was invalid")
        if payload.get("error"):
            raise SourceError("PROVIDER_ERROR", f"Kraken returned: {payload['error']}")

        markets: dict[Market, str] = {}
        try:
            for details in payload["result"].values():
                market = Market(
                    base=str(details["base"]),
                    quote=str(details["quote"]),
                )
                markets[market] = str(details["altname"])
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise SourceError("SCHEMA_INVALID", "Kraken asset-pair schema was invalid") from exc

        self._markets = markets
        return markets

    def fetch(self, market: Market) -> PriceObservation:
        provider_market = self.get_markets().get(market)
        if provider_market is None:
            raise UnsupportedMarketError(self.name, market)

        try:
            response = self.client.get_json(
                f"{self._BASE_URL}/public/Trades",
                query={
                    "pair": provider_market,
                    "count": "1",
                    "assetVersion": "1",
                },
            )
        except HttpClientError as exc:
            raise SourceError(exc.code, f"Kraken trades request failed: {exc}") from exc

        payload = response.payload
        if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
            raise SourceError("SCHEMA_INVALID", "Kraken trades response was invalid")
        if payload.get("error"):
            raise SourceError("PROVIDER_ERROR", f"Kraken returned: {payload['error']}")

        result = payload["result"]
        trade_groups = [value for key, value in result.items() if key != "last"]

        try:
            trades = trade_groups[0]
            latest_trade = trades[-1]
            price = Decimal(str(latest_trade[0]))
            provider_timestamp = datetime.fromtimestamp(
                float(latest_trade[2]),
                tz=timezone.utc,
            )
            event_id = str(latest_trade[6]) if len(latest_trade) > 6 else str(result.get("last"))
        except (IndexError, TypeError, ValueError, InvalidOperation) as exc:
            raise SourceError("SCHEMA_INVALID", "Kraken returned no valid recent trade") from exc

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
