"""Build the access URL this aggregator hands back to the consumer."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit, urlunsplit


if TYPE_CHECKING:
    from .config import Config


def build_access_url(config: Config) -> str:
    """Compose an access URL from base_url and the consumer's basic-auth credentials."""
    parsed = urlsplit(config.base_url)
    username = quote(config.consumer.username, safe="")
    password = quote(config.consumer.password.get_secret_value(), safe="")
    netloc = f"{username}:{password}@{parsed.netloc}"
    path = parsed.path.rstrip("/") + "/simplefin"
    return urlunsplit((parsed.scheme, netloc, path, "", ""))
