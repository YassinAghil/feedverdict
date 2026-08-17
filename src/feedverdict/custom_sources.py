# Config-driven JSON API and CSV price sources.

from __future__ import annotations

import csv
import io
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from string import Formatter
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

from feedverdict.http import HttpClientError, JsonHttpClient
from feedverdict.models import Market, PriceObservation
from feedverdict.sources.base import SourceError, UnsupportedMarketError


class SourceConfigError(ValueError):
    pass


_ALLOWED_TEMPLATE_FIELDS = {"base", "quote", "provider_market"}
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_SENSITIVE_NAME = re.compile(r"(?:api[_-]?key|token|secret|password)", re.IGNORECASE)


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SourceConfigError(f"{label} must be a JSON object")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceConfigError(f"{label} must be a non-empty string")
    return value.strip()


def _string_map(value: Any, label: str) -> dict[str, str]:
    payload = _require_object(value, label)
    return {
        _require_string(key, f"{label} key"): _require_string(item, f"{label}.{key}")
        for key, item in payload.items()
    }


def _market_from_text(value: str) -> Market:
    parts = [part.strip() for part in value.split("/")]
    if len(parts) != 2 or not all(parts):
        raise SourceConfigError(f"Market {value!r} must use BASE/QUOTE format")
    return Market(parts[0], parts[1])


def _parse_markets(value: Any) -> dict[Market, str]:
    markets: dict[Market, str] = {}
    for symbol, provider_market in _string_map(value, "markets").items():
        market = _market_from_text(symbol)
        if market in markets:
            raise SourceConfigError(f"Duplicate market {market.symbol}")
        markets[market] = provider_market
    if not markets:
        raise SourceConfigError("markets must contain at least one BASE/QUOTE mapping")
    return markets


def _parse_timestamp(value: Any, timestamp_format: str) -> datetime:
    try:
        if timestamp_format == "iso8601":
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        elif timestamp_format == "unix_seconds":
            result = datetime.fromtimestamp(float(value), tz=timezone.utc)
        elif timestamp_format == "unix_milliseconds":
            result = datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
        else:
            raise SourceConfigError(
                "timestamp_format must be iso8601, unix_seconds, or unix_milliseconds"
            )
    except (TypeError, ValueError, OverflowError) as exc:
        raise SourceError("SCHEMA_INVALID", "Provider timestamp could not be parsed") from exc

    if result.tzinfo is None:
        raise SourceError(
            "SCHEMA_INVALID",
            "Provider ISO-8601 timestamp must include a timezone",
        )
    return result


def _extract_path(payload: Any, path: str) -> Any:
    current = payload
    for token in path.split("."):
        try:
            if isinstance(current, Mapping):
                current = current[token]
            elif isinstance(current, list):
                current = current[int(token)]
            else:
                raise KeyError(token)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise SourceError(
                "SCHEMA_INVALID",
                f"Response did not contain configured path {path!r}",
            ) from exc
    return current


@dataclass(frozen=True, slots=True)
class HttpJsonDefinition:
    name: str
    markets: dict[Market, str]
    url_template: str
    price_path: str
    timestamp_path: str
    timestamp_format: str
    event_id_path: str | None
    static_query: dict[str, str]
    query_from_env: dict[str, str]
    headers_from_env: dict[str, str]
    kind: str = "http_json"

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "name": self.name,
            "kind": self.kind,
            "url_template": self.url_template,
            "markets": {
                market.symbol: provider_market
                for market, provider_market in self.markets.items()
            },
            "price_path": self.price_path,
            "timestamp_path": self.timestamp_path,
            "timestamp_format": self.timestamp_format,
        }
        if self.event_id_path:
            payload["event_id_path"] = self.event_id_path
        if self.static_query:
            payload["static_query"] = self.static_query
        if self.query_from_env:
            payload["query_from_env"] = self.query_from_env
        if self.headers_from_env:
            payload["headers_from_env"] = self.headers_from_env
        return payload


@dataclass(frozen=True, slots=True)
class CsvDefinition:
    name: str
    path: Path
    market_column: str
    price_column: str
    timestamp_column: str
    timestamp_format: str
    event_id_column: str | None
    delimiter: str = ","
    kind: str = "csv"

    def to_payload(self) -> dict[str, Any]:
        columns = {
            "market": self.market_column,
            "price": self.price_column,
            "timestamp": self.timestamp_column,
        }
        if self.event_id_column:
            columns["event_id"] = self.event_id_column
        return {
            "schema_version": 1,
            "name": self.name,
            "kind": self.kind,
            "path": str(self.path),
            "delimiter": self.delimiter,
            "columns": columns,
            "timestamp_format": self.timestamp_format,
        }


SourceDefinition = HttpJsonDefinition | CsvDefinition


def _validate_template(url_template: str) -> None:
    try:
        fields = {
            field_name
            for _literal, field_name, _format_spec, _conversion in Formatter().parse(
                url_template
            )
            if field_name is not None
        }
    except ValueError as exc:
        raise SourceConfigError("url_template contains invalid braces") from exc
    unsupported = fields - _ALLOWED_TEMPLATE_FIELDS
    if unsupported:
        raise SourceConfigError(
            f"Unsupported URL placeholders: {', '.join(sorted(unsupported))}"
        )
    rendered = url_template.format(
        base="BTC",
        quote="USD",
        provider_market="btc-usd",
    )
    parsed = urlsplit(rendered)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SourceConfigError("url_template must be an absolute HTTPS URL")
    if parsed.username or parsed.password:
        raise SourceConfigError("Credentials must not be embedded in url_template")
    for query_part in parsed.query.split("&"):
        key = query_part.partition("=")[0]
        if _SENSITIVE_NAME.search(key):
            raise SourceConfigError(
                "Credentials must come from query_from_env, not url_template"
            )


def _validate_env_map(values: dict[str, str], label: str, *, headers: bool = False) -> None:
    for request_name, env_name in values.items():
        if headers and not _HEADER_NAME.fullmatch(request_name):
            raise SourceConfigError(f"Invalid HTTP header name {request_name!r}")
        if not _ENV_NAME.fullmatch(env_name):
            raise SourceConfigError(f"{label}.{request_name} has an invalid environment name")


def load_source_definition(path: Path) -> SourceDefinition:
    try:
        if path.stat().st_size > 1_000_000:
            raise SourceConfigError("Source config exceeds the 1 MB limit")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SourceConfigError(f"Could not read source config: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SourceConfigError(f"Source config is not valid JSON: {exc}") from exc

    root = _require_object(payload, "source config")
    if root.get("schema_version") != 1:
        raise SourceConfigError("schema_version must be 1")
    name = _require_string(root.get("name"), "name")
    kind = _require_string(root.get("kind"), "kind")
    timestamp_format = _require_string(root.get("timestamp_format"), "timestamp_format")
    if timestamp_format not in {"iso8601", "unix_seconds", "unix_milliseconds"}:
        raise SourceConfigError(
            "timestamp_format must be iso8601, unix_seconds, or unix_milliseconds"
        )

    if kind == "http_json":
        url_template = _require_string(root.get("url_template"), "url_template")
        _validate_template(url_template)
        static_query = _string_map(root.get("static_query", {}), "static_query")
        if any(_SENSITIVE_NAME.search(name) for name in static_query):
            raise SourceConfigError(
                "Credential-like static query fields must use query_from_env"
            )
        query_from_env = _string_map(
            root.get("query_from_env", {}), "query_from_env"
        )
        headers_from_env = _string_map(
            root.get("headers_from_env", {}), "headers_from_env"
        )
        _validate_env_map(query_from_env, "query_from_env")
        _validate_env_map(headers_from_env, "headers_from_env", headers=True)
        event_id_path = root.get("event_id_path")
        if event_id_path is not None:
            event_id_path = _require_string(event_id_path, "event_id_path")
        return HttpJsonDefinition(
            name=name,
            markets=_parse_markets(root.get("markets")),
            url_template=url_template,
            price_path=_require_string(root.get("price_path"), "price_path"),
            timestamp_path=_require_string(
                root.get("timestamp_path"), "timestamp_path"
            ),
            timestamp_format=timestamp_format,
            event_id_path=event_id_path,
            static_query=static_query,
            query_from_env=query_from_env,
            headers_from_env=headers_from_env,
        )

    if kind == "csv":
        raw_path = Path(_require_string(root.get("path"), "path")).expanduser()
        if not raw_path.is_absolute():
            raw_path = (path.parent / raw_path).resolve()
        columns = _string_map(root.get("columns"), "columns")
        missing_columns = {"market", "price", "timestamp"} - columns.keys()
        if missing_columns:
            raise SourceConfigError(
                f"columns is missing: {', '.join(sorted(missing_columns))}"
            )
        delimiter = root.get("delimiter", ",")
        delimiter = _require_string(delimiter, "delimiter")
        if len(delimiter) != 1:
            raise SourceConfigError("delimiter must be exactly one character")
        return CsvDefinition(
            name=name,
            path=raw_path,
            market_column=columns["market"],
            price_column=columns["price"],
            timestamp_column=columns["timestamp"],
            timestamp_format=timestamp_format,
            event_id_column=columns.get("event_id"),
            delimiter=delimiter,
        )

    raise SourceConfigError("kind must be http_json or csv")


class HttpJsonSource:
    def __init__(self, definition: HttpJsonDefinition, client: JsonHttpClient) -> None:
        self.definition = definition
        self.client = client
        self.name = definition.name

    def get_markets(self) -> dict[Market, str]:
        return dict(self.definition.markets)

    def fetch(self, market: Market) -> PriceObservation:
        provider_market = self.definition.markets.get(market)
        if provider_market is None:
            raise UnsupportedMarketError(self.name, market)

        values = {
            "base": quote(market.base, safe=""),
            "quote": quote(market.quote, safe=""),
            "provider_market": quote(provider_market, safe=""),
        }
        url = self.definition.url_template.format(**values)
        query = dict(self.definition.static_query)
        headers: dict[str, str] = {}
        for request_name, env_name in self.definition.query_from_env.items():
            value = os.environ.get(env_name)
            if value is None:
                raise SourceError(
                    "MISSING_CREDENTIAL",
                    f"Environment variable {env_name} is required by {self.name}",
                )
            query[request_name] = value
        for request_name, env_name in self.definition.headers_from_env.items():
            value = os.environ.get(env_name)
            if value is None:
                raise SourceError(
                    "MISSING_CREDENTIAL",
                    f"Environment variable {env_name} is required by {self.name}",
                )
            headers[request_name] = value

        try:
            response = self.client.get_json(
                url,
                query=query or None,
                headers=headers or None,
            )
        except HttpClientError as exc:
            raise SourceError(exc.code, f"{self.name} request failed: {exc}") from exc

        try:
            price = Decimal(str(_extract_path(response.payload, self.definition.price_path)))
            provider_timestamp = _parse_timestamp(
                _extract_path(response.payload, self.definition.timestamp_path),
                self.definition.timestamp_format,
            )
            event_id = (
                str(_extract_path(response.payload, self.definition.event_id_path))
                if self.definition.event_id_path
                else None
            )
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise SourceError("SCHEMA_INVALID", "Configured price fields were invalid") from exc

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


class CsvPriceSource:
    _MAX_FILE_BYTES = 5_000_000

    def __init__(self, definition: CsvDefinition) -> None:
        self.definition = definition
        self.name = definition.name

    def _rows(self) -> list[dict[str, str]]:
        try:
            if self.definition.path.stat().st_size > self._MAX_FILE_BYTES:
                raise SourceError(
                    "RESPONSE_TOO_LARGE",
                    f"CSV feed exceeds {self._MAX_FILE_BYTES} bytes",
                )
            content = self.definition.path.read_text(encoding="utf-8")
        except SourceError:
            raise
        except OSError as exc:
            raise SourceError(
                "SOURCE_UNAVAILABLE",
                f"Could not read CSV feed {self.definition.path}: {exc}",
            ) from exc

        reader = csv.DictReader(io.StringIO(content), delimiter=self.definition.delimiter)
        if reader.fieldnames is None:
            raise SourceError("SCHEMA_INVALID", "CSV feed has no header row")
        required = {
            self.definition.market_column,
            self.definition.price_column,
            self.definition.timestamp_column,
        }
        if not required.issubset(reader.fieldnames):
            raise SourceError(
                "SCHEMA_INVALID",
                "CSV feed is missing columns: "
                + ", ".join(sorted(required - set(reader.fieldnames))),
            )
        return list(reader)

    def get_markets(self) -> dict[Market, str]:
        markets: dict[Market, str] = {}
        try:
            for row in self._rows():
                symbol = row[self.definition.market_column]
                market = _market_from_text(symbol)
                markets[market] = symbol
        except (KeyError, SourceConfigError) as exc:
            raise SourceError("SCHEMA_INVALID", "CSV market field was invalid") from exc
        return markets

    def fetch(self, market: Market) -> PriceObservation:
        matches: list[tuple[datetime, Decimal, str | None, str]] = []
        for row in self._rows():
            try:
                provider_market = row[self.definition.market_column]
                if _market_from_text(provider_market) != market:
                    continue
                timestamp = _parse_timestamp(
                    row[self.definition.timestamp_column],
                    self.definition.timestamp_format,
                )
                price = Decimal(row[self.definition.price_column])
                event_id = (
                    row.get(self.definition.event_id_column)
                    if self.definition.event_id_column
                    else None
                )
            except (KeyError, SourceConfigError, InvalidOperation) as exc:
                raise SourceError("SCHEMA_INVALID", "CSV price row was invalid") from exc
            matches.append((timestamp, price, event_id, provider_market))

        if not matches:
            raise UnsupportedMarketError(self.name, market)
        timestamp, price, event_id, provider_market = max(matches, key=lambda item: item[0])
        return PriceObservation(
            source=self.name,
            market=market,
            provider_market=provider_market,
            price=price,
            provider_timestamp=timestamp,
            received_at=datetime.now(timezone.utc),
            event_id=event_id,
        )


def build_custom_source(
    definition: SourceDefinition,
    client: JsonHttpClient,
) -> HttpJsonSource | CsvPriceSource:
    if isinstance(definition, HttpJsonDefinition):
        return HttpJsonSource(definition, client)
    return CsvPriceSource(definition)
