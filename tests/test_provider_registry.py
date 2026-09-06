"""Tests for the provider registry.

`.invalid` (RFC 2606) is reserved and guaranteed never to resolve, so it is
safe as a stand-in provider host with no risk of a real lookup.
"""

from __future__ import annotations

import pytest

from simplefin_aggregator.provider_registry import (
    KNOWN_PROVIDERS,
    ProviderEntry,
    ProviderRegistryError,
    find_provider,
    merged_providers,
)
from simplefin_aggregator.url_validation import UrlValidationError, parse_root


# Spelled out rather than derived from KNOWN_PROVIDERS: keys are permanent
# identifiers (they key the access URL store and are referenced from config
# files), so a change to one should have to be made here too, deliberately.
EXPECTED_STATIC_PROVIDERS = [
    ("simplefin-bridge", "https://beta-bridge.simplefin.org/simplefin/"),
    ("lunchflow", "https://www.lunchflow.app/api/simplefin-bridge/"),
    ("redbark", "https://api.redbark.com/simplefin/"),
]


def _entry(key: str, root: str = "https://my-bridge.invalid/simplefin") -> ProviderEntry:
    return ProviderEntry(key=key, label=f"label for {key}", root=parse_root(root))


def test_static_provider_roots() -> None:
    assert [
        (provider.key, provider.root.origin_and_path) for provider in KNOWN_PROVIDERS
    ] == EXPECTED_STATIC_PROVIDERS


@pytest.mark.parametrize(
    "provider", KNOWN_PROVIDERS, ids=[provider.key for provider in KNOWN_PROVIDERS]
)
def test_static_provider_root_is_canonical(provider: ProviderEntry) -> None:
    """Each root survives a re-parse unchanged, so matching sees what is written here."""
    root = provider.root
    assert root.origin_and_path.endswith("/")
    assert not root.has_creds
    assert parse_root(root.origin_and_path).origin_and_path == root.origin_and_path
    assert provider.label


def test_static_provider_labels_are_present() -> None:
    assert all(provider.label for provider in KNOWN_PROVIDERS)


def test_merged_providers_keeps_static_entries_first() -> None:
    extra = _entry("my-bridge")

    merged = merged_providers([extra])

    assert merged == (*KNOWN_PROVIDERS, extra)


def test_merged_providers_rejects_a_key_colliding_with_static_entry() -> None:
    with pytest.raises(ProviderRegistryError, match="duplicate provider key 'lunchflow'"):
        _ = merged_providers([_entry("lunchflow")])


def test_merged_providers_rejects_a_key_duplicated_between_config_entries() -> None:
    with pytest.raises(ProviderRegistryError, match="duplicate provider key 'my-bridge'"):
        _ = merged_providers([_entry("my-bridge"), _entry("my-bridge")])


def test_find_provider_returns_the_matching_entry() -> None:
    extra = _entry("my-bridge")
    providers = merged_providers([extra])

    assert find_provider(providers, "my-bridge") is extra
    assert find_provider(providers, "redbark").label == "Redbark"


def test_find_provider_rejects_an_absent_key() -> None:
    providers = merged_providers([])

    with pytest.raises(ProviderRegistryError) as excinfo:
        _ = find_provider(providers, "redbarc")

    # No fuzzy matching: the near-miss key is reported as unknown, and the
    # message lists what is actually configured.
    message = str(excinfo.value)
    assert "unknown provider 'redbarc'" in message
    # And says how to reach a provider the built-in list does not name, since
    # an unknown key is as likely to be a missing entry as a typo.
    assert "[[custom_providers]]" in message
    assert "redbark" in message


BAD_ROOTS = [
    ("http://provider.invalid/simplefin", "must use https"),
    ("https://user:pass@provider.invalid/simplefin", "must not contain credentials"),
    ("https://provider.invalid/simplefin?x=1", "must not contain a query string or fragment"),
]


@pytest.mark.parametrize(("raw", "expected"), BAD_ROOTS)
def test_custom_provider_root_is_rejected_with_a_reason(raw: str, expected: str) -> None:
    """A config-supplied entry's root goes through parse_root, which names the problem."""
    with pytest.raises(UrlValidationError, match=expected):
        _ = parse_root(raw)


BAD_KEYS = ["", "Redbark", "my bank", "my_bank", "café", "my-bridge\n", "a/b", "x\x1b[31m"]


@pytest.mark.parametrize("key", BAD_KEYS)
def test_entry_rejects_a_malformed_key(key: str) -> None:
    with pytest.raises(ProviderRegistryError, match="must match"):
        _ = _entry(key)


@pytest.mark.parametrize(
    ("key", "forbidden"),
    [
        # An ANSI escape must not reach the terminal that prints the message,
        # and a homograph key must not be rendered in a form that looks like
        # the ASCII one it imitates.
        ("x\x1b[31m", "\x1b"),
        ("redb\u0430rk", "\u0430"),
    ],
)
def test_malformed_key_message_escapes_the_key(key: str, forbidden: str) -> None:
    with pytest.raises(ProviderRegistryError) as excinfo:
        _ = _entry(key)

    assert forbidden not in str(excinfo.value)


@pytest.mark.parametrize("key", ["redbark", "simplefin-bridge", "bank2", "0"])
def test_entry_accepts_a_well_formed_key(key: str) -> None:
    assert _entry(key).key == key
