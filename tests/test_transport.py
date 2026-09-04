from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx2

from simplefin_aggregator.config import Provider
from simplefin_aggregator.request_counter import RequestCounter
from simplefin_aggregator.transport import fetch_all


if TYPE_CHECKING:
    from .support import MockHandler


def _provider(provider_key: str) -> Provider:
    return Provider.model_validate({"provider_key": provider_key})


def _client_for(name: str, handler: MockHandler) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler), base_url=f"https://{name}.example.com/simplefin"
    )


class _ConcurrencyTracker:
    """Records how many handlers were simultaneously in flight."""

    def __init__(self) -> None:
        self.current: int = 0
        self.max_seen: int = 0

    def enter(self) -> None:
        self.current += 1
        self.max_seen = max(self.max_seen, self.current)

    def exit(self) -> None:
        self.current -= 1


def _slow_ok_handler(calls: list[str], tracker: _ConcurrencyTracker) -> MockHandler:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.host)
        tracker.enter()
        await asyncio.sleep(0.05)
        tracker.exit()
        return httpx2.Response(200, json={"accounts": []})

    return handler


async def test_fetch_all_calls_every_provider() -> None:
    provider_a, provider_b = _provider("bank-a"), _provider("bank-b")
    calls: list[str] = []
    tracker = _ConcurrencyTracker()
    clients = {
        provider_a.provider_key: _client_for("bank-a", _slow_ok_handler(calls, tracker)),
        provider_b.provider_key: _client_for("bank-b", _slow_ok_handler(calls, tracker)),
    }

    responses = await fetch_all(
        clients, [provider_a, provider_b], "/accounts", [], RequestCounter()
    )

    assert [r.provider_name for r in responses] == ["bank-a", "bank-b"]
    assert all(r.ok for r in responses)
    assert set(calls) == {"bank-a.example.com", "bank-b.example.com"}


async def test_fetch_all_requests_overlap_in_time() -> None:
    provider_a, provider_b = _provider("bank-a"), _provider("bank-b")
    calls: list[str] = []
    tracker = _ConcurrencyTracker()
    clients = {
        provider_a.provider_key: _client_for("bank-a", _slow_ok_handler(calls, tracker)),
        provider_b.provider_key: _client_for("bank-b", _slow_ok_handler(calls, tracker)),
    }

    _ = await fetch_all(clients, [provider_a, provider_b], "/accounts", [], RequestCounter())

    assert tracker.max_seen == 2, "both provider requests should have been in flight at once"  # noqa: PLR2004


async def test_fetch_all_one_provider_failing_still_yields_response_for_both() -> None:
    provider_a, provider_b = _provider("bank-a"), _provider("bank-b")

    async def ok_handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"accounts": []})

    async def failing_handler(request: httpx2.Request) -> httpx2.Response:
        msg = "connection refused"
        raise httpx2.ConnectError(msg, request=request)

    clients = {
        provider_a.provider_key: _client_for("bank-a", ok_handler),
        provider_b.provider_key: _client_for("bank-b", failing_handler),
    }

    responses = await fetch_all(
        clients, [provider_a, provider_b], "/accounts", [], RequestCounter()
    )

    assert len(responses) == 2  # noqa: PLR2004
    assert responses[0].provider_name == "bank-a"
    assert responses[0].ok is True
    assert responses[1].provider_name == "bank-b"
    assert responses[1].ok is False
