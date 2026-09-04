"""The access URLs this aggregator holds for its  providers.

This is the most sensitive file this application owns: an access URL embeds
the Basic Auth credentials for a provider, and anything holding one can read
the user's bank data. Hence mode 0600 on write, a warning on load if the mode
is looser, and `SecretStr` values so that a stray repr cannot print one.

Keys are provider slugs (see `provider_allowlist.py`) rather than anything from
config.toml, because `claim` runs before config.toml necessarily mentions the
provider at all: the slug the user picked from the menu is the only stable
identifier available at claim time. One consequence is that two accounts at the
same provider would collide on one key. That is out of scope while config
enforces exactly one provider.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

from platformdirs import user_cache_dir
from pydantic import BaseModel, Field, SecretStr, ValidationError, field_serializer

from .config import APP_NAME, warn_if_permissive


if TYPE_CHECKING:
    from collections.abc import Mapping

ACCESS_URLS_FILENAME = "access_urls.json"


class AccessUrlStoreError(Exception):
    """Raised when the access URL file exists but cannot be read or understood."""


class _AccessUrlFile(BaseModel):
    """The on-disk shape."""

    access_urls: dict[str, SecretStr] = Field(default_factory=dict)

    @field_serializer("access_urls", when_used="json")
    def _reveal_access_urls(self, access_urls: Mapping[str, SecretStr]) -> dict[str, str]:
        """Write the real values; `SecretStr` would otherwise serialize as asterisks.

        Only this file is written from the revealed values. Every other
        rendering of the model -- repr, str, a pydantic error -- still shows
        the redacted form.
        """
        return {slug: access_url.get_secret_value() for slug, access_url in access_urls.items()}


def access_urls_path(cache_dir: Path | None = None) -> Path:
    """Where the store lives: inside `cache_dir` if given, else the platform default."""
    directory = cache_dir if cache_dir is not None else Path(user_cache_dir(APP_NAME))
    return directory / ACCESS_URLS_FILENAME


def load_access_urls(path: Path) -> dict[str, SecretStr]:
    """Read the store. A file that is not there yet is empty, not an error."""
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        msg = f"cannot read access URL file {path}: {exc}"
        raise AccessUrlStoreError(msg) from exc

    warn_if_permissive(path)

    try:
        data = cast(object, json.loads(raw))
    except json.JSONDecodeError as exc:
        msg = f"malformed JSON in {path}: {exc}"
        raise AccessUrlStoreError(msg) from exc

    try:
        return _AccessUrlFile.model_validate(data).access_urls
    except ValidationError as exc:
        # Same rule as load_config: never str() a ValidationError, whose
        # default rendering embeds the raw input -- here, access URLs with
        # their credentials.
        details = "\n".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_url=False, include_input=False)
        )
        msg = f"invalid access URL file {path}:\n{details}"
        raise AccessUrlStoreError(msg) from None


def save_access_url(path: Path, slug: str, access_url: str) -> None:
    """Record one provider's access URL, leaving the others in place."""
    stored = load_access_urls(path)
    stored[slug] = SecretStr(access_url)
    contents = _AccessUrlFile(access_urls=stored).model_dump_json(indent=2)

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Write-then-rename: a crash mid-write must not leave the file truncated,
    # since what it holds cannot be regenerated. The temporary file is created
    # 0600 and keeps that mode across the rename, so the store ends up 0600
    # even if an earlier version of it did not.
    temporary = path.with_name(f"{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        _ = handle.write(contents + "\n")
    _ = temporary.replace(path)
