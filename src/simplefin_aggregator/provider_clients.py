"""Build a provider's long-lived httpx2.AsyncClient."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlunsplit

import httpx2


if TYPE_CHECKING:
    from .config import Provider

DEFAULT_TIMEOUT = httpx2.Timeout(30.0)


def build_provider_client(
    provider: Provider, *, timeout: httpx2.Timeout = DEFAULT_TIMEOUT
) -> httpx2.AsyncClient:
    """One AsyncClient per provider. Credentials go via `auth=`, not the URL."""
    parsed = provider.parsed_access_url()
    netloc = parsed.hostname or ""
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    base_url = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    return httpx2.AsyncClient(
        base_url=base_url,
        auth=(parsed.username or "", parsed.password or ""),
        timeout=timeout,
        follow_redirects=True,
    )
