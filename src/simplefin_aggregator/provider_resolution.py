"""Which provider owns a given account id.

With exactly one provider configured, the answer is always that provider. Once
multiple providers exist, this is where id-namespacing-based ownership lookup
will live.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence

    from .config import Provider


def resolve_provider_for_account(
    account_id: str,  # noqa: ARG001 # pyright: ignore[reportUnusedParameter]
    providers: Sequence[Provider],
) -> Provider:
    (provider,) = providers
    return provider
