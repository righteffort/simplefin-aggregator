"""A single provider's response to one request."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSuccess:
    """The provider answered, with whatever status code it chose."""

    provider_name: str
    status: int
    body: bytes

    @property
    def ok(self) -> bool:
        return True


@dataclass(frozen=True)
class ProviderFailure:
    """No usable response: the provider was unreachable, or answered with a redirect."""

    provider_name: str
    error: str

    @property
    def ok(self) -> bool:
        return False


ProviderResponse = ProviderSuccess | ProviderFailure
