import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from feedverdict.custom_sources import (
    CsvPriceSource,
    HttpJsonSource,
    SourceConfigError,
    build_custom_source,
    load_source_definition,
)
from feedverdict.http import JsonResponse
from feedverdict.models import Market
from feedverdict.registry import SourceRegistry
from feedverdict.sources import SourceError
from tests.fakes import QueueJsonClient


RECEIVED_AT = datetime(2026, 8, 15, 16, 20, 5, tzinfo=timezone.utc)


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


class CustomSourceTests(unittest.TestCase):
    def test_http_json_adapter_extracts_nested_fields_and_env_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "source.json"
            write_json(
                config_path,
                {
                    "schema_version": 1,
                    "name": "Example API",
                    "kind": "http_json",
                    "url_template": "https://prices.example/v1/{provider_market}",
                    "markets": {"BTC/USD": "btc-usd"},
                    "price_path": "data.ticker.price",
                    "timestamp_path": "data.ticker.updated_at",
                    "timestamp_format": "iso8601",
                    "event_id_path": "data.id",
                    "headers_from_env": {"X-API-Key": "EXAMPLE_PRICE_KEY"},
                },
            )
            client = QueueJsonClient(
                [
                    JsonResponse(
                        payload={
                            "data": {
                                "id": "event-7",
                                "ticker": {
                                    "price": "118012.50",
                                    "updated_at": "2026-08-15T16:20:04Z",
                                },
                            }
                        },
                        received_at=RECEIVED_AT,
                        latency_ms=11.0,
                    )
                ]
            )

            with patch.dict(os.environ, {"EXAMPLE_PRICE_KEY": "not-persisted"}):
                source = build_custom_source(load_source_definition(config_path), client)
                observation = source.fetch(Market("BTC", "USD"))

            self.assertIsInstance(source, HttpJsonSource)
            self.assertEqual(observation.price, Decimal("118012.50"))
            self.assertEqual(observation.event_id, "event-7")
            self.assertEqual(client.calls[0][2], {"X-API-Key": "not-persisted"})

    def test_missing_env_credential_fails_before_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "source.json"
            write_json(
                config_path,
                {
                    "schema_version": 1,
                    "name": "Keyed API",
                    "kind": "http_json",
                    "url_template": "https://prices.example/{provider_market}",
                    "markets": {"BTC/USD": "btc-usd"},
                    "price_path": "price",
                    "timestamp_path": "timestamp",
                    "timestamp_format": "unix_seconds",
                    "query_from_env": {"api_key": "ABSENT_PRICE_KEY"},
                },
            )
            client = QueueJsonClient([])
            source = build_custom_source(load_source_definition(config_path), client)

            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(SourceError) as raised:
                    source.fetch(Market("BTC", "USD"))

            self.assertEqual(raised.exception.code, "MISSING_CREDENTIAL")
            self.assertEqual(client.calls, [])

    def test_csv_adapter_selects_newest_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feed = root / "prices.csv"
            feed.write_text(
                "market,price,observed_at,event_id\n"
                "BTC/USD,118000,2026-08-15T16:19:00Z,old\n"
                "BTC/USD,118020,2026-08-15T16:20:04Z,new\n",
                encoding="utf-8",
            )
            config_path = root / "source.json"
            write_json(
                config_path,
                {
                    "schema_version": 1,
                    "name": "Warehouse CSV",
                    "kind": "csv",
                    "path": "prices.csv",
                    "columns": {
                        "market": "market",
                        "price": "price",
                        "timestamp": "observed_at",
                        "event_id": "event_id",
                    },
                    "timestamp_format": "iso8601",
                },
            )

            source = build_custom_source(
                load_source_definition(config_path), QueueJsonClient([])
            )
            observation = source.fetch(Market("BTC", "USD"))

            self.assertIsInstance(source, CsvPriceSource)
            self.assertEqual(observation.price, Decimal("118020"))
            self.assertEqual(observation.event_id, "new")

    def test_credentials_embedded_in_url_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "unsafe.json"
            write_json(
                config_path,
                {
                    "schema_version": 1,
                    "name": "Unsafe",
                    "kind": "http_json",
                    "url_template": "https://prices.example/ticker?api_key=plaintext",
                    "markets": {"BTC/USD": "btc-usd"},
                    "price_path": "price",
                    "timestamp_path": "timestamp",
                    "timestamp_format": "unix_seconds",
                },
            )

            with self.assertRaises(SourceConfigError):
                load_source_definition(config_path)

    def test_registry_normalises_relative_csv_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feed = root / "feed.csv"
            feed.write_text(
                "market,price,timestamp\nBTC/USD,1,2026-08-15T16:20:04Z\n",
                encoding="utf-8",
            )
            config_path = root / "source.json"
            write_json(
                config_path,
                {
                    "schema_version": 1,
                    "name": "CSV Feed",
                    "kind": "csv",
                    "path": "feed.csv",
                    "columns": {
                        "market": "market",
                        "price": "price",
                        "timestamp": "timestamp",
                    },
                    "timestamp_format": "iso8601",
                },
            )
            registry = SourceRegistry(root / "registry")

            registered_path = registry.add(config_path)
            sources, failures = registry.load_sources(QueueJsonClient([]))

            self.assertTrue(registered_path.exists())
            self.assertEqual(failures, ())
            self.assertEqual(sources[0].definition.path, feed.resolve())


if __name__ == "__main__":
    unittest.main()
