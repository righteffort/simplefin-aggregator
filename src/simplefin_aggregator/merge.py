"""Build the one v1 response the client app sees out of several providers'.

The account ids this aggregator exposes are each provider's own behind a
per-provider prefix, so prefixing happens here, where the accounts are already
parsed. The body carries `accounts` and `errors`; within an account every key
the provider sent survives, and only `id` changes.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

from .provider_response import ProviderFailure


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .provider_response import ProviderResponse

logger = logging.getLogger(__name__)

# An account keyed as its provider sent it, paired with the id this aggregator
# will expose it under.
_PrefixedAccount = tuple[str, dict[str, object]]


@dataclass(frozen=True)
class MergedResponse:
    body: bytes


def _json_object(body: bytes) -> dict[str, object] | None:
    try:
        parsed = cast(object, json.loads(body))
    except (ValueError, RecursionError):
        return None
    if not isinstance(parsed, dict):
        return None
    return cast("dict[str, object]", parsed)


def _prefixed_accounts(parsed: dict[str, object], prefix: str) -> list[_PrefixedAccount] | None:
    """Process the `accounts` array into pairs of exposed id and account, or None for garbage.

    Garbage is anything but a list of objects, each carrying a string `id`.
    """
    raw_accounts = parsed.get("accounts")
    if not isinstance(raw_accounts, list):
        return None
    accounts: list[_PrefixedAccount] = []
    for raw_account in cast("list[object]", raw_accounts):
        if not isinstance(raw_account, dict):
            return None
        account = cast("dict[str, object]", raw_account)
        account_id = account.get("id")
        if not isinstance(account_id, str):
            return None
        accounts.append((f"{prefix}{account_id}", account))
    return accounts


def _error_strings(parsed: dict[str, object], key: str) -> list[str]:
    """Process the `errors` array into an array of strings.

    Anything other than missing or an array of strings is logged and treated as
    an empty list.
    """
    raw_errors = parsed.get("errors", [])
    if isinstance(raw_errors, list):
        errors = cast("list[object]", raw_errors)
        if all(isinstance(error, str) for error in errors):
            return cast("list[str]", errors)
    logger.warning("provider %s sent an errors field this application cannot read", key)
    return []


def _accounts_and_errors(
    response: ProviderResponse, prefix: str
) -> tuple[list[_PrefixedAccount], list[str]] | None:
    """Process one provider's response into its accounts and its own error strings.

    None for a response this aggregator cannot use, logged with what was wrong
    with it. The four ways of being unusable are one answer to the caller and
    four different log lines, because the operator's next move differs: a 403
    means re-running `claim`, and a timeout means waiting.
    """
    key = response.provider_name
    if isinstance(response, ProviderFailure):
        logger.warning("provider %s did not answer: %s", key, response.error)
        return None
    if response.status != HTTPStatus.OK:
        logger.warning("provider %s answered HTTP %d", key, response.status)
        return None
    parsed = _json_object(response.body)
    if parsed is None:
        logger.warning("provider %s sent a body that is not a JSON object", key)
        return None
    accounts = _prefixed_accounts(parsed, prefix)
    if accounts is None:
        logger.warning("provider %s sent an accounts array this application cannot read", key)
        return None
    return accounts, _error_strings(parsed, key)


def _log_id_collisions(owners: Mapping[str, Sequence[str]]) -> None:
    """Log one line for each exposed id that more than one account claims."""
    for account_id, keys in owners.items():
        if len(keys) > 1:
            providers = ", ".join(dict.fromkeys(keys))
            logger.warning(
                "account id %r appears %d times, from %s", account_id, len(keys), providers
            )


def merge(results: Sequence[tuple[str, ProviderResponse]]) -> MergedResponse:
    """Build one v1 response out of each provider's, pairing a prefix with a response.

    Nothing a provider sent is discarded. Accounts come out provider by
    provider in the order `results` gives them, each provider's own order kept
    within its run, and when two prefixes let an id collide both accounts are
    still returned; the collision is logged rather than reported, being the
    operator's to fix by changing a prefix.

    `errors` holds what the providers themselves reported, in the same order.
    A provider that answered unusably is logged and contributes nothing.
    """
    accounts: list[dict[str, object]] = []
    errors: list[str] = []
    owners: defaultdict[str, list[str]] = defaultdict(list)

    for prefix, response in results:
        key = response.provider_name
        contributed = _accounts_and_errors(response, prefix)
        if contributed is None:
            # TODO(claude): step D1 generates the errors for this provider here,
            # one per account remembered from its last successful sync, naming
            # the institution the user has to go and fix. That is the only error
            # worth synthesizing; until then a failure is logged and nothing is
            # put in the response.
            continue

        provider_accounts, provider_errors = contributed
        for account_id, account in provider_accounts:
            owners[account_id].append(key)
            accounts.append({**account, "id": account_id})
        errors.extend(provider_errors)

    _log_id_collisions(owners)
    payload = {"accounts": accounts, "errors": errors}
    # ensure_ascii=False keeps an account name as text rather than as escapes.
    return MergedResponse(body=json.dumps(payload, ensure_ascii=False).encode("utf-8"))
