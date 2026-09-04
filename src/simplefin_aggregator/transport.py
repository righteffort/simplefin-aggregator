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
        return ProviderFailure(provider_name=provider_name, error=str(exc))
    return ProviderSuccess(
        provider_name=provider_name,
        status=response.status_code,
        headers=dict(response.headers),
        body=response.content,
    )


async def fetch_all(
    clients: Mapping[str, httpx2.AsyncClient],
    providers: Sequence[Provider],
    path: str,
    params: Sequence[tuple[str, str]],
    counter: RequestCounter,
) -> list[ProviderResponse]:
    """Fan out to every given provider concurrently. Output order matches `providers`."""
    return list(
        await asyncio.gather(
            *(
                fetch(clients[provider.provider_key], provider.provider_key, path, params, counter)
                for provider in providers
            )
        )
    )
