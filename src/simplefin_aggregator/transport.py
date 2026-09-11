"""Concurrent, non-raising provider requests."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx2

from .provider_response import ProviderFailure, ProviderResponse, ProviderSuccess


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .config import Provider
    from .request_counter import RequestCounter

logger = logging.getLogger(__name__)


async def fetch(
    client: httpx2.AsyncClient,
    provider_name: str,
    path: str,
    params: Sequence[tuple[str, str]],
    counter: RequestCounter,
) -> ProviderResponse:
    """Issue one request to one provider. Never raises; failure becomes ProviderFailure."""
    count_today = counter.record(provider_name)
    logger.info("provider request: %s (request #%d today)", provider_name, count_today)
    try:
        response = await client.get(path, params=httpx2.QueryParams(tuple(params)))
    except httpx2.HTTPError as exc:
        # Not str(exc): httpx2's text can quote provider-chosen bytes, and this is logged.
        return ProviderFailure(provider_name=provider_name, error=type(exc).__name__)
    if 300 <= response.status_code < 400:  # noqa: PLR2004
        return ProviderFailure(
            provider_name=provider_name,
            error=f"provider returned an unexpected redirect (HTTP {response.status_code})",
        )
    return ProviderSuccess(
        provider_name=provider_name, status=response.status_code, body=response.content
    )


async def fetch_all(
    clients: Mapping[str, httpx2.AsyncClient],
    requests: Sequence[tuple[Provider, Sequence[tuple[str, str]]]],
    path: str,
    counter: RequestCounter,
) -> list[ProviderResponse]:
    """Issue every given request concurrently. Output order matches `requests`.

    Each provider is paired with its own parameters, because an `account`
    filter is a list of that provider's own account ids: handing one provider
    another's would ask it about accounts it has never heard of.
    """
    return list(
        await asyncio.gather(
            *(
                fetch(clients[provider.key], provider.key, path, params, counter)
                for provider, params in requests
            )
        )
    )
