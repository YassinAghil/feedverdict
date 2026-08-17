# Small JSON HTTP client used by the source adapters.

from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class HttpClientError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class JsonResponse:
    payload: Any
    received_at: datetime
    latency_ms: float


class JsonHttpClient(Protocol):
    def get_json(
        self,
        url: str,
        *,
        query: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JsonResponse: ...


# Uses the Python standard library and limits request time and response size.
class UrllibJsonHttpClient:

    _MAX_RESPONSE_BYTES = 2_000_000

    def __init__(self, timeout_seconds: float = 4.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds

    def get_json(
        self,
        url: str,
        *,
        query: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JsonResponse:
        if query:
            url = f"{url}?{urlencode(query)}"

        request_headers = {
            "Accept": "application/json",
            "User-Agent": "FeedVerdict/0.1 (+assessment project)",
        }
        for name, value in (headers or {}).items():
            if "\n" in name or "\r" in name or "\n" in value or "\r" in value:
                raise HttpClientError("HEADER_INVALID", "HTTP headers cannot contain newlines")
            if name.casefold() in {"host", "content-length"}:
                raise HttpClientError(
                    "HEADER_INVALID",
                    f"Header {name!r} is managed by the HTTP client",
                )
            request_headers[name] = value

        request = Request(
            url,
            headers=request_headers,
            method="GET",
        )
        started = time.perf_counter()

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read(self._MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise HttpClientError("HTTP_ERROR", f"HTTP {exc.code}") from exc
        except (URLError, TimeoutError, socket.timeout) as exc:
            reason = getattr(exc, "reason", exc)
            code = (
                "SOURCE_TIMEOUT"
                if isinstance(reason, (TimeoutError, socket.timeout))
                else "NETWORK_ERROR"
            )
            raise HttpClientError(code, str(reason)) from exc

        latency_ms = (time.perf_counter() - started) * 1000
        received_at = datetime.now(timezone.utc)

        if len(body) > self._MAX_RESPONSE_BYTES:
            raise HttpClientError(
                "RESPONSE_TOO_LARGE",
                f"Response exceeded {self._MAX_RESPONSE_BYTES} bytes",
            )

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HttpClientError("INVALID_JSON", "Response was not valid UTF-8 JSON") from exc

        return JsonResponse(
            payload=payload,
            received_at=received_at,
            latency_ms=latency_ms,
        )
