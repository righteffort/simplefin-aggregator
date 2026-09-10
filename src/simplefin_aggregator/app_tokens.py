"""App token records: what this aggregator's client apps authenticate with.

Each entry is one app token record, in one of two states: a setup token
issued and not yet spent, or the Basic Auth credentials a claim issued in
exchange for it. A claim replaces the first state with the second rather
than marking it spent, so a replayed setup token matches nothing -- failing
the lookup that a token which never existed fails, by the same code path.

The records live in `aggregator_creds.json`, which holds SHA-256 digests and
no plaintext. Every secret here is verified, never reproduced: the setup
token against what was pasted, the credentials against what the client app
sends, and the access URL is the client app's to keep. So there is nothing in
the file to display, log or steal. Plain SHA-256 and no KDF, because these
are 256-bit random values rather than chosen passwords, and there is no
dictionary to search.

That makes integrity matter more than confidentiality here, the opposite of
`provider_creds.json`: reading this file gains an attacker nothing, while
writing it installs digests of secrets they know.
"""

from __future__ import annotations

import hashlib
import secrets
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Literal, NamedTuple

from pydantic import AwareDatetime, BaseModel, Field, SecretStr

from .config import default_config_dir
from .provider_registry import KEY_PATTERN
from .state_file import load_state_file, update_state_file


if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

APP_TOKENS_FILENAME = "aggregator_creds.json"

# 256 bits, which is what makes guessing a setup token or a password
# hopeless without a rate limit and what makes a KDF pointless.
_SECRET_BYTES = 32

_Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
"""A digest as this store writes it. Constrained so that a hand-edited file is
rejected on load rather than at the comparison, which takes ASCII only.

The timestamps below are `AwareDatetime` for the same reason: a naive one
loads without complaint and then raises at the first subtraction, which is
whatever first shows an app token's age."""


class ClientCredentials(NamedTuple):
    """What one claim issues: the Basic Auth pair the client app will send back.

    `SecretStr`, as every other live credential this application holds in
    memory is. The store keeps only digests of these, but the pair itself
    exists in the claim that issues it, long enough for a traceback rendering
    its locals to print it.
    """

    username: SecretStr
    password: SecretStr


class UnclaimedAppToken(BaseModel):
    """A setup token issued to a client app and not yet spent."""

    status: Literal["unclaimed"] = "unclaimed"
    label: str
    created_at: AwareDatetime
    claim_token_sha256: _Sha256Hex


class ClaimedAppToken(BaseModel):
    """What a spent setup token became: the client app's Basic Auth credentials."""

    status: Literal["claimed"] = "claimed"
    label: str
    created_at: AwareDatetime
    claimed_at: AwareDatetime
    username_sha256: _Sha256Hex
    password_sha256: _Sha256Hex


AppTokenRecord = Annotated[UnclaimedAppToken | ClaimedAppToken, Field(discriminator="status")]


_AppKey = Annotated[str, Field(pattern=rf"^{KEY_PATTERN.pattern}$")]
"""A key as the commands accept one, constrained here as well as there.

The registry constrains a provider key at construction so that every entry is
checked and not only the ones a command supplied. The same reasoning applies
to a store a person can edit: what `app list` prints should be what this
application could have written."""


class _AppTokenFile(BaseModel):
    """The on-disk shape."""

    tokens: dict[_AppKey, AppTokenRecord] = Field(default_factory=dict)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def matches(secret: str, stored_digest: str) -> bool:
    """Whether `secret` is the value `stored_digest` was derived from."""
    return secrets.compare_digest(_digest(secret), stored_digest)


def new_app_token(label: str) -> tuple[str, UnclaimedAppToken]:
    """Mint a setup token secret and the record that will recognise it.

    The secret is returned and never stored, so this is the only moment it
    exists anywhere but in the client app: the caller shows it once.
    """
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    return secret, UnclaimedAppToken(
        label=label, created_at=datetime.now(UTC), claim_token_sha256=_digest(secret)
    )


def claim_app_token(token: UnclaimedAppToken) -> tuple[ClientCredentials, ClaimedAppToken]:
    """Spend an unclaimed record, returning the credentials it issues.

    The record that replaces it carries no claim digest, which is what makes a
    second claim of the same setup token indistinguishable from a claim of one
    that was never issued.
    """
    username = secrets.token_urlsafe(_SECRET_BYTES)
    password = secrets.token_urlsafe(_SECRET_BYTES)
    return ClientCredentials(SecretStr(username), SecretStr(password)), ClaimedAppToken(
        label=token.label,
        created_at=token.created_at,
        claimed_at=datetime.now(UTC),
        username_sha256=_digest(username),
        password_sha256=_digest(password),
    )


def app_tokens_path(config_dir: Path | None = None) -> Path:
    """Where the store lives: in `config_dir`, or the platform default."""
    directory = config_dir if config_dir is not None else default_config_dir()
    return directory / APP_TOKENS_FILENAME


def load_app_tokens(path: Path, *, warn: bool = True) -> dict[str, AppTokenRecord]:
    """Read the store. A file that is not there yet is empty, not an error."""
    return load_state_file(path, _AppTokenFile, warn=warn).tokens


@contextmanager
def update_app_tokens(path: Path) -> Generator[dict[str, AppTokenRecord]]:
    """Change the records under an exclusive lock, saving them on a clean exit."""
    with update_state_file(path, _AppTokenFile) as stored:
        yield stored.tokens
