"""A single provider's response to one request."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderSuccess:
    """The provider answered, with whatever status code it chose."""

    provider_name: str
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def ok(self) -> bool:
        return True

    @property
    def json(self) -> Any:  # noqa: ANN401 # pyright: ignore[reportAny, reportExplicitAny]
        """Parse the body as JSON. Not called on the pass-through path, by design."""
        return json.loads(self.body)  # pyright: ignore[reportAny]


@dataclass(frozen=True)
class ProviderFailure:
    """The provider was unreachable: connection refused, DNS failure, or timeout."""

    provider_name: str
    error: str

    @property
    def ok(self) -> bool:
        return False


ProviderResponse = ProviderSuccess | ProviderFailure
