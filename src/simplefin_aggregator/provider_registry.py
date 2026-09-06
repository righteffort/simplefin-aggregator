"""The set of providers that can supply a setup token.

A setup token is a base64-encoded URL pasted in from a web page, so the URL
inside it is attacker-influenceable input (see `url_validation.py` for the
threat model). This module holds the known-good roots it is matched against:
the built-in `KNOWN_PROVIDERS`, plus whatever entries the user has written into
their config file for a provider this list does not name.

Adding an entry is deliberately a config-file edit and nothing else. A
phishing page's natural next move is to tell the user to run a command it
supplies, so there is no flag and no interactive "trust this origin?" prompt
here for it to talk the user through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .url_validation import parse_root


if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from .url_validation import NormalizedUrl


class ProviderRegistryError(ValueError):
    """The provider registry has a malformed or duplicate key, or names a provider that is absent.

    Subclasses `ValueError` so that raising it inside a pydantic validator is
    reported as an ordinary config validation error rather than escaping as a
    traceback.
    """


_KEY_PATTERN = re.compile(r"[a-z0-9-]+")


@dataclass(frozen=True)
class ProviderEntry:
    """One provider a setup token may be claimed from."""

    # Stable machine identifier: the access-URL store's key and what config
    # files reference. Changing one invalidates stored access URLs and config
    # references, so treat published keys as permanent.
    key: str
    label: str
    root: NormalizedUrl

    def __post_init__(self) -> None:
        # Every entry is built through here, config-supplied ones included, so
        # this is the one place the store's keys and the claim menu's
        # selectors are constrained.
        if not _KEY_PATTERN.fullmatch(self.key):
            msg = f"provider key {self.key!a} must match {_KEY_PATTERN.pattern}"
            raise ProviderRegistryError(msg)


KNOWN_PROVIDERS: tuple[ProviderEntry, ...] = (
    ProviderEntry(
        key="simplefin-bridge",
        label="SimpleFIN Bridge (beta)",
        root=parse_root("https://beta-bridge.simplefin.org/simplefin"),
    ),
    ProviderEntry(
        key="lunchflow",
        label="Lunch Flow",
        root=parse_root("https://www.lunchflow.app/api/simplefin-bridge"),
    ),
    ProviderEntry(
        key="redbark", label="Redbark", root=parse_root("https://api.redbark.com/simplefin")
    ),
)


def merged_providers(extra: Iterable[ProviderEntry]) -> tuple[ProviderEntry, ...]:
    """`KNOWN_PROVIDERS` followed by the config-supplied entries.

    A key that collides -- with a built-in entry or with another config entry
    -- is an error rather than an override: silently shadowing a built-in root
    with a config one would turn a typo into a downgrade of exactly the check
    this module exists to perform.
    """
    providers = (*KNOWN_PROVIDERS, *extra)
    seen: set[str] = set()
    for provider in providers:
        if provider.key in seen:
            msg = f"duplicate provider key {provider.key!r}"
            raise ProviderRegistryError(msg)
        seen.add(provider.key)
    return providers


def find_provider(providers: Sequence[ProviderEntry], key: str) -> ProviderEntry:
    """Return the entry with this key. An unknown key is an error, never guessed at."""
    for provider in providers:
        if provider.key == key:
            return provider
    known = ", ".join(provider.key for provider in providers)
    msg = (
        f"unknown provider {key!r}; known providers are: {known}. "
        "A provider that is not one of those needs a [[custom_providers]] entry in the config file."
    )
    raise ProviderRegistryError(msg)
