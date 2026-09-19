# SPDX-License-Identifier: GPL-3.0-only

"""Build a provider's long-lived httpx2.AsyncClient."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx2


if TYPE_CHECKING:
    from .url_validation import NormalizedUrl

_TIMEOUT = httpx2.Timeout(30.0)


def build_provider_client(access_url: NormalizedUrl) -> httpx2.AsyncClient:
    """One AsyncClient per provider. Credentials go via `auth=`, not the URL."""
    return httpx2.AsyncClient(
        base_url=access_url.origin_and_path,
        auth=(access_url.username, access_url.password),
        timeout=_TIMEOUT,
        follow_redirects=False,
    )
