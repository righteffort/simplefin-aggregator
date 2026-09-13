"""Which provider owns an account id the client app asked for.

Inverts the prefixing `merge.py` applies: an exposed account id is a
provider's own id behind that provider's prefix, so the owner is the provider
whose prefix the id carries. An id no named prefix claims belongs to all
providers with the blank prefix.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence

    from .config import Provider


def resolve_providers_for_account(
    account_id: str, providers: Sequence[Provider]
) -> list[tuple[Provider, str]]:
    """Return each provider this id may belong to, with the id as that provider knows it.

    Those are the providers with the longest prefix the id carries. Empty when
    no prefix matches.
    """
    matching = [provider for provider in providers if account_id.startswith(provider.prefix)]
    if not matching:
        return []
    longest = max(len(provider.prefix) for provider in matching)
    return [
        (provider, account_id[longest:]) for provider in matching if len(provider.prefix) == longest
    ]
