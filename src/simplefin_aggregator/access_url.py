"""Build the access URL this aggregator hands back to the client app."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit, urlunsplit


if TYPE_CHECKING:
    from .app_tokens import ClientCredentials


def build_access_url(base_url: str, credentials: ClientCredentials) -> str:
    """Compose an access URL from the base URL and the credentials one claim issued.

    This is the only time either credential is rendered anywhere. The store
    keeps digests, so nothing can rebuild this URL afterwards -- the client app
    keeps it, as the protocol requires of it.
    """
    parsed = urlsplit(base_url)
    # A generated credential needs no encoding here, but the quoting stays: it
    # is a no-op on what the generator emits and stays correct if that changes.
    username = quote(credentials.username.get_secret_value(), safe="")
    password = quote(credentials.password.get_secret_value(), safe="")
    netloc = f"{username}:{password}@{parsed.netloc}"
    path = parsed.path.rstrip("/") + "/simplefin"
    return urlunsplit((parsed.scheme, netloc, path, "", ""))
