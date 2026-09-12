"""Which provider owns an account id the client app asked for.

Inverts the prefixing `merge.py` applies: an exposed account id is a
provider's own id behind that provider's prefix, so the owner is the provider
whose prefix the id carries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence

    from .config import Provider


def resolve_provider_for_account(
    account_id: str, providers: Sequence[Provider]
) -> tuple[Provider, str] | None:
    """Return the provider this id belongs to and the id as that provider knows it, or None."""
    owner: Provider | None = None
    for provider in providers:
        if account_id.startswith(provider.prefix) and (
            owner is None or len(provider.prefix) > len(owner.prefix)
        ):
            owner = provider
    if owner is None:
        return None
    return owner, account_id[len(owner.prefix) :]
