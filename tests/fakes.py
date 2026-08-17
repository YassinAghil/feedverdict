from collections import deque

from feedverdict.http import JsonResponse
from feedverdict.models import Market, PriceObservation
from feedverdict.sources.base import SourceError


class QueueJsonClient:
    def __init__(self, responses: list[JsonResponse]) -> None:
        self.responses = deque(responses)
        self.calls: list[
            tuple[str, dict[str, str] | None, dict[str, str] | None]
        ] = []

    def get_json(self, url: str, *, query=None, headers=None) -> JsonResponse:
        self.calls.append((url, query, headers))
        if not self.responses:
            raise AssertionError(f"Unexpected HTTP call: {url}")
        return self.responses.popleft()


class FakeSource:
    def __init__(
        self,
        name: str,
        outcome: PriceObservation | SourceError | Exception,
    ) -> None:
        self.name = name
        self.outcome = outcome
        self.fetch_calls: list[Market] = []

    def get_markets(self) -> dict[Market, str]:
        if isinstance(self.outcome, PriceObservation):
            return {self.outcome.market: self.outcome.provider_market}
        return {}

    def fetch(self, market: Market) -> PriceObservation:
        self.fetch_calls.append(market)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome
