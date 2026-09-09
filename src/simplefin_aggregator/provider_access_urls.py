"""The access URLs this aggregator holds for its providers.

This is the most sensitive file this application owns: an access URL embeds
the Basic Auth credentials for a provider, and anything holding one can read
the user's bank data. None of it can be regenerated: a setup token is
one-time-use, so a lost entry costs a fresh token from the provider. The file
handling that follows from that -- 0600, atomic replace, an error path that
never quotes the rejected input -- is in `state_file.py`.

Entries are keyed by provider key, so two accounts at the same provider would
collide on one key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, SecretStr, field_serializer

from .config import default_config_dir
from .state_file import load_state_file, update_state_file


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

PROVIDER_CREDS_FILENAME = "provider_creds.json"


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
        return {key: access_url.get_secret_value() for key, access_url in access_urls.items()}


def provider_creds_path(config_dir: Path | None = None) -> Path:
    """Where the store lives: in `config_dir`, or the platform default."""
    directory = config_dir if config_dir is not None else default_config_dir()
    return directory / PROVIDER_CREDS_FILENAME


def load_access_urls(path: Path) -> dict[str, SecretStr]:
    """Read the store. A file that is not there yet is empty, not an error."""
    return load_state_file(path, _AccessUrlFile).access_urls


def save_access_url(path: Path, key: str, access_url: SecretStr) -> None:
    """Record one provider's access URL, leaving the others in place.

    Takes the URL already wrapped, so that it is redacted from the moment the
    provider hands it over rather than for every part of its life but the
    argument to this call.
    """
    with update_state_file(path, _AccessUrlFile) as stored:
        stored.access_urls[key] = access_url
