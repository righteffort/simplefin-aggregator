from __future__ import annotations

from simplefin_aggregator.provider_resolution import resolve_provider_for_account

from .support import ProviderSpec, make_config


A = ProviderSpec(key="bank-a", root="https://bank-a.example.com/simplefin")
B = ProviderSpec(key="bank-b", root="https://bank-b.example.com/simplefin")


def test_an_id_resolves_to_the_provider_whose_prefix_it_carries() -> None:
    """Requirement: an exposed id says which provider issued it, without asking any provider."""
    config = make_config(A, B)

    resolved = resolve_provider_for_account("bank-b:acc-1", config.providers)

    assert resolved is not None
    provider, account_id = resolved
    assert provider.key == "bank-b"
    assert account_id == "acc-1", (
        "the provider is asked about the id it issued, not the exposed one"
    )


def test_an_id_no_prefix_claims_resolves_to_nothing() -> None:
    """Requirement: an unrecognized id is not guessed at, and reaches no provider."""
    config = make_config(A, B)

    assert resolve_provider_for_account("bank-c:acc-1", config.providers) is None


def test_the_blank_prefix_takes_only_the_ids_no_other_prefix_claims() -> None:
    """Requirement: the blank prefix is a catch-all, not a claim on every id.

    It matches every id, so a first-match implementation would hand it ids
    belonging to the provider named in them.
    """
    config = make_config(ProviderSpec(key="bank-a", root=A.root, prefix=""), B)

    claimed = resolve_provider_for_account("bank-b:acc-1", config.providers)
    unclaimed = resolve_provider_for_account("plain-id", config.providers)

    assert claimed is not None
    assert claimed[0].key == "bank-b"
    assert unclaimed is not None
    assert unclaimed == (config.providers[0], "plain-id")


def test_the_longest_matching_prefix_wins_over_a_shorter_one() -> None:
    """Behavior, not a requirement: config validation rejects prefixes that overlap like this.

    A Config cannot hold `b:` alongside `b:sub:`, so this pins what the lookup
    does with a provider list assembled some other way -- keeping the rule the
    blank prefix relies on true in general rather than by luck.
    """
    outer = ProviderSpec(key="bank-a", root=A.root, prefix="b:")
    inner = ProviderSpec(key="bank-b", root=B.root, prefix="b:sub:")
    providers = [make_config(outer).providers[0], make_config(inner).providers[0]]

    resolved = resolve_provider_for_account("b:sub:acc-1", providers)

    assert resolved is not None
    assert resolved[0].key == "bank-b"
    assert resolved[1] == "acc-1"


def test_an_id_that_is_exactly_a_prefix_resolves_to_an_empty_provider_id() -> None:
    """Behavior, not a requirement: nothing rejects an id with no provider-local part.

    It is forwarded as the empty account id it is, and the provider answers
    about no account. Rejecting it would be a rule with nothing behind it.
    """
    config = make_config(A, B)

    assert resolve_provider_for_account("bank-a:", config.providers) == (config.providers[0], "")
