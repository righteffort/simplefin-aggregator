"""No-op id-rewriting hooks.

Once multiple providers exist, account/transaction ids will need to be namespaced
so ids from different providers can't collide in the aggregated response. These
hooks mark exactly where that rewriting will happen; today, with one provider,
there is nothing to rewrite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence


def unrewrite_ids(account_ids: Sequence[str]) -> Sequence[str]:
    """Map aggregator-visible account ids back to provider-local ids, before forwarding upstream."""
    return account_ids


def rewrite_ids(body: bytes) -> bytes:
    """Map provider-local ids in a response body to aggregator-visible ids, before returning it."""
    return body
