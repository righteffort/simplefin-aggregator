"""Build a provider's long-lived httpx2.AsyncClient."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import unquote

import httpx2


if TYPE_CHECKING:
    from .url_validation import NormalizedUrl

DEFAULT_TIMEOUT = httpx2.Timeout(30.0)


def build_provider_client(
    access_url: NormalizedUrl, *, timeout: httpx2.Timeout = DEFAULT_TIMEOUT
) -> httpx2.AsyncClient:
    """One AsyncClient per provider. Credentials go via `auth=`, not the URL."""
    return httpx2.AsyncClient(
        # The one place `origin_and_path` feeds a network request, against
        # url_validation.py's "never fetch it" rule. It holds here because
        # httpx2 joins request paths onto `base_url` rather than fetching it as
        # given; because this value is always a validated access URL and never
        # a provider root, so it carries no synthesized trailing slash; and
        # because the credentials it deliberately omits are supplied below.
        base_url=access_url.origin_and_path,
        # urlsplit does not percent-decode userinfo, so a credential
        # containing a reserved character arrives here still encoded and has
        # to be decoded before it goes into an Authorization header.
        auth=(unquote(access_url.username or ""), unquote(access_url.password or "")),
        timeout=timeout,
        follow_redirects=True,
    )
