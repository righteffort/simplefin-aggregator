"""Build the setup token a client app uses to claim this aggregator."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit


if TYPE_CHECKING:
    from .config import Config


def build_setup_token(config: Config) -> str:
    """Base64-encode this aggregator's own claim URL, the way a real provider's setup token does."""
    parsed = urlsplit(config.base_url)
    path = parsed.path.rstrip("/") + f"/simplefin/claim/{config.claim_token.get_secret_value()}"
    claim_url = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    return base64.b64encode(claim_url.encode("ascii")).decode("ascii")
