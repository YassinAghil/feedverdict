# Config-driven price sources

FeedVerdict has built-in adapters for Coinbase, Kraken, and Bitstamp. Additional
sources plug into the same `PriceSource` interface through versioned JSON config.

The command is intentionally `feedverdict source add CONFIG`, rather than
`feedverdict add URL`. A URL does not say which response field is the price,
whether a timestamp is the provider's event time or merely the HTTP receipt
time, how market symbols map, or where authentication belongs. Guessing any of
those would undermine the assessment's central requirement: detect stale data
instead of silently trusting it.

## Lifecycle

```bash
feedverdict source validate my-source.json
feedverdict source add my-source.json
feedverdict source list
```

Use `--replace` on `source add` to update an existing source with the same name.
Validation never makes a live price request and never reads credential values.

## HTTPS JSON schema

```json
{
  "schema_version": 1,
  "name": "Example HTTP Price API",
  "kind": "http_json",
  "url_template": "https://prices.example/v1/{provider_market}",
  "markets": {
    "BTC/USD": "btc-usd",
    "ETH/USD": "eth-usd"
  },
  "price_path": "data.price",
  "timestamp_path": "data.updated_at",
  "timestamp_format": "iso8601",
  "event_id_path": "data.id",
  "static_query": {
    "convert": "USD"
  },
  "headers_from_env": {
    "X-API-Key": "EXAMPLE_PRICE_API_KEY"
  }
}
```

Supported URL placeholders are `{base}`, `{quote}`, and `{provider_market}`.
Nested JSON paths use dots; numeric components index arrays, such as
`data.0.price`. Timestamp formats are `iso8601`, `unix_seconds`, or
`unix_milliseconds`. Provider timestamps are mandatory—HTTP receipt time is not
silently substituted because that would make stale payloads look fresh.

`headers_from_env` and `query_from_env` map an HTTP field to an environment
variable name. FeedVerdict rejects embedded URL credentials and credential-like
static query fields.

## CSV schema

```json
{
  "schema_version": 1,
  "name": "Warehouse Export",
  "kind": "csv",
  "path": "/feeds/prices.csv",
  "columns": {
    "market": "market",
    "price": "price",
    "timestamp": "provider_timestamp",
    "event_id": "event_id"
  },
  "timestamp_format": "iso8601"
}
```

The feed can contain multiple rows per market; the adapter selects the row with
the newest valid provider timestamp. Relative CSV paths are resolved when the
config is registered, so the managed config retains a stable absolute feed path.

## Safety boundaries

- HTTP sources must use absolute HTTPS URLs.
- Provider timestamps must be explicit and timezone-aware.
- Config files are capped at 1 MB; CSV inputs at 5 MB; HTTP responses at 2 MB.
- HTTP calls have bounded timeouts and managed `Host`/`Content-Length` headers.
- API secrets stay in environment variables and are never written to SQLite or
  the decision trace.
- Complex signing, pagination, or discovery logic belongs in a small Python
  adapter implementing `PriceSource`, not in an increasingly magical config.
