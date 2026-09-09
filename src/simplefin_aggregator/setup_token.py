"""Build the setup token a client app uses to claim this aggregator."""

from __future__ import annotations

import base64
from urllib.parse import urlsplit, urlunsplit


def build_setup_token(base_url: str, claim_secret: str) -> str:
    """Base64-encode this aggregator's own claim URL, the way a real provider's setup token does.

    `claim_secret` becomes a path segment as it stands, which is safe because
    the only thing that mints one emits the URL-safe base64 alphabet.
    """
    parsed = urlsplit(base_url)
    path = parsed.path.rstrip("/") + f"/simplefin/claim/{claim_secret}"
    claim_url = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    return base64.b64encode(claim_url.encode("ascii")).decode("ascii")
