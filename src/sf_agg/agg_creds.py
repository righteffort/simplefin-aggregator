# SPDX-License-Identifier: GPL-3.0-only

"""Aggregator's representations of clients' credentials.

Each entry represents one client's credentials, in one of two states: a setup
token has been issued and not yet exchanged, or the setup token has been
exchanged for Basic Auth credentials. Exchanging replaces the first state with
the second, which keeps no claim secret digest, so a replayed setup token
matches nothing -- failing the lookup that a token which never existed fails, by
the same code path.

The records live in `aggregator_creds.json`, which holds SHA-256 digests and
no plaintext. Every secret here is verified, never reproduced: the claim secret
against what the client presents, the credentials against what the client
sends, and the access URL is the client's to keep. So there is nothing in the
file to display, log or steal. Plain SHA-256 and no KDF, because these are
256-bit random values rather than chosen passwords, and there is no dictionary
to search.

That makes integrity matter more than confidentiality here, the opposite of
`provider_creds.json`: reading this file gains an attacker nothing, while
writing it installs digests of secrets they know.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal, NamedTuple, cast

from pydantic import BaseModel, ConfigDict, Discriminator, Field, SecretStr, Tag

from .config import config_dir
from .provider_registry import KEY_PATTERN
from .state_file import load_state_file, update_state_file


if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

AGG_CREDS_FILENAME = "aggregator_creds.json"

# 256 bits, which is what makes guessing a claim secret or a password hopeless
# without a rate limit and what makes a KDF pointless.
_SECRET_BYTES = 32

_Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
"""A digest as this store writes it. Constrained so that a hand-edited file is
rejected on load rather than at the comparison, which takes ASCII only. The
models below are strict for the same reason."""

_EpochSeconds = Annotated[int, Field(ge=0, le=253_370_764_799)]
"""Whole epoch seconds, through 9998, so any time zone can render one."""


class AccessUrlAuth(NamedTuple):
    """The Basic Auth pair an access URL carries.

    `SecretStr`, as every other live credential this application holds in
    memory is. The store keeps only digests of these, but the pair itself
    exists in the exchange that issues it, long enough for a traceback
    rendering its locals to print it.
    """

    username: SecretStr
    password: SecretStr


class UnexchangedAggCreds(BaseModel):
    """Representation of a client's credentials prior to exchanging the setup token."""

    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, extra="forbid")

    exchanged: Literal[False] = False
    created_at: _EpochSeconds
    claim_secret_sha256: _Sha256Hex


class ExchangedAggCreds(BaseModel):
    """Representation of a client's credentials after exchanging the setup token."""

    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, extra="forbid")

    exchanged: Literal[True] = True
    created_at: _EpochSeconds
    exchanged_at: _EpochSeconds
    username_sha256: _Sha256Hex
    password_sha256: _Sha256Hex


def _exchanged_tag(value: object) -> str | None:
    """Discriminate on `exchanged` only when it is a real boolean.

    A `Literal[False]` field matches by equality, so on its own it would admit
    the `0` a hand-edited file might hold, strict mode or not. Called with a
    dict when validating and with a model when serializing.
    """
    if isinstance(value, Mapping):
        exchanged = cast("Mapping[str, object]", value).get("exchanged")
    else:
        exchanged = getattr(value, "exchanged", None)
    if exchanged is True:
        return "exchanged"
    if exchanged is False:
        return "unexchanged"
    return None


AggCreds = Annotated[
    Annotated[UnexchangedAggCreds, Tag("unexchanged")]
    | Annotated[ExchangedAggCreds, Tag("exchanged")],
    Discriminator(_exchanged_tag),
]


_ClientKey = Annotated[str, Field(pattern=rf"^{KEY_PATTERN.pattern}$")]
"""A key as the commands accept one, constrained here as well as there.

The registry constrains a provider key at construction so that every entry is
checked and not only the ones a command supplied. The same reasoning applies
to a store a person can edit: what `client list` prints should be what this
application could have written."""


class _AggCredsFile(BaseModel):
    """The on-disk shape."""

    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, extra="forbid")

    creds: dict[_ClientKey, AggCreds] = Field(default_factory=dict)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def matches(secret: str, stored_digest: str) -> bool:
    """Whether `secret` is the value `stored_digest` was derived from."""
    return secrets.compare_digest(_digest(secret), stored_digest)


def new_agg_creds(created_at: int | None = None) -> tuple[str, UnexchangedAggCreds]:
    """Mint a claim secret and the record that will recognize it.

    The secret is returned and never stored, so this is the only moment it
    exists anywhere but in the client: the caller shows it once. `created_at`
    carries a client's creation time over to its new record; omitted, the
    client is new.
    """
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    return secret, UnexchangedAggCreds(
        created_at=int(time.time()) if created_at is None else created_at,
        claim_secret_sha256=_digest(secret),
    )


def exchange_agg_creds(unexchanged: UnexchangedAggCreds) -> tuple[AccessUrlAuth, ExchangedAggCreds]:
    """Exchange an unexchanged record, returning the credentials it issues.

    The record that replaces it carries no claim secret digest, which is what
    makes a second exchange of the same setup token indistinguishable from an
    exchange of one that was never issued.
    """
    username = secrets.token_urlsafe(_SECRET_BYTES)
    password = secrets.token_urlsafe(_SECRET_BYTES)
    return AccessUrlAuth(SecretStr(username), SecretStr(password)), ExchangedAggCreds(
        created_at=unexchanged.created_at,
        exchanged_at=int(time.time()),
        username_sha256=_digest(username),
        password_sha256=_digest(password),
    )


def agg_creds_path(directory: Path | None = None) -> Path:
    """Where the store lives: in `directory`, or where `config_dir()` resolves."""
    return (directory if directory is not None else config_dir()) / AGG_CREDS_FILENAME


def load_agg_creds(path: Path, *, warn: bool = True) -> dict[str, AggCreds]:
    """Read the store. A file that is not there yet is empty, not an error."""
    return load_state_file(path, _AggCredsFile, warn=warn).creds


@contextmanager
def update_agg_creds(path: Path) -> Generator[dict[str, AggCreds]]:
    """Change the records under an exclusive lock, saving them on a clean exit."""
    with update_state_file(path, _AggCredsFile) as stored:
        yield stored.creds
