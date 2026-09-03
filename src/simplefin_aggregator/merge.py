"""Combine provider responses into one response to the consumer.

With exactly one provider, this returns that response's raw bytes untouched —
which is what keeps the byte-identity guarantee for this version. Merging
several providers' responses is a later version's problem.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .provider_response import ProviderFailure


if TYPE_CHECKING:
    from .provider_response import ProviderResponse


@dataclass(frozen=True)
class MergedResponse:
    status: int
    content_type: str
    body: bytes


def merge(responses: list[ProviderResponse]) -> MergedResponse:
    (response,) = responses

    if isinstance(response, ProviderFailure):
        body = json.dumps(
            {
                "accounts": [],
                "errors": [response.error],
                "errlist": [{"code": "gen.unreachable", "msg": response.error}],
            }
        ).encode()
        return MergedResponse(status=502, content_type="application/json", body=body)

    content_type = response.headers.get("content-type", "application/json")
    return MergedResponse(status=response.status, content_type=content_type, body=response.body)
