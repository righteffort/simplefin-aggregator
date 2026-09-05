"""Configuration model and loading for simplefin-aggregator."""

from __future__ import annotations

import stat
import sys
import tomllib
from pathlib import Path
from urllib.parse import quote, urlsplit

from platformdirs import user_config_dir
from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator, model_validator

from .provider_allowlist import ProviderEntry, find_provider, merged_providers
from .url_validation import UrlValidationError, is_loopback_host, parse_root


APP_NAME = "simplefin-aggregator"


class ConfigError(Exception):
    """Raised when the config file is missing, malformed, or fails validation."""


class Provider(BaseModel):
    """A single SimpleFIN provider this aggregator proxies."""

    # An allowlist slug, built-in or from `allowlist` below. It identifies the
    # provider everywhere: as the store's key, as the provider client dict's
    # key, and in log lines.
    provider_key: str


class AllowlistEntry(BaseModel):
    """A self-hosted provider this config adds to the built-in allowlist.

    Editing this entry in the config file is deliberately the only way
    to add one -- see `provider_allowlist.py` for why there is no flag
    and no prompt.
    """

    slug: str
    label: str
    root: str

    def as_provider_entry(self) -> ProviderEntry:
        """Convert to an allowlist entry, validating the slug and the root."""
        try:
            root = parse_root(self.root)
        except UrlValidationError as exc:
            # UrlValidationError is not a ValueError, so pydantic would let it
            # escape as a traceback rather than reporting it as a config error.
            # Its message is built for display and carries no credentials.
            raise ValueError(str(exc)) from None
        return ProviderEntry(slug=self.slug, label=self.label, root=root)


class ClientAuth(BaseModel):
    """The basic-auth credentials this aggregator requires from a client app."""

    username: str
    password: SecretStr


class Config(BaseModel):
    """The parsed config file.

    Treated as read-only once `load_config` returns: nothing mutates a Config,
    and nothing rebinds app.state.config. Validators here may therefore
    establish invariants -- see _check_provider_keys -- that hold for the
    object's whole lifetime.
    """

    bind_host: str = "127.0.0.1"
    bind_port: int = 8080
    # Schema and internal types are already a list for the multi-provider version to come;
    # this version only supports exactly one.
    providers: list[Provider] = Field(min_length=1, max_length=1)
    allowlist: list[AllowlistEntry] = []
    client: ClientAuth
    claim_token: SecretStr
    base_url: str

    def provider_entries(self) -> tuple[ProviderEntry, ...]:
        """Every provider a token may be claimed from: the built-in ones plus this config's."""
        return merged_providers(entry.as_provider_entry() for entry in self.allowlist)

    @model_validator(mode="after")
    def _check_provider_keys(self) -> Config:
        """Fail at load time on a provider_key no allowlist entry defines.

        Checked here rather than at first use so that a dangling reference is
        reported by every command, not just the one that would dereference it.
        """
        entries = self.provider_entries()
        for provider in self.providers:
            _ = find_provider(entries, provider.provider_key)
        return self

    @field_validator("claim_token")
    @classmethod
    def _validate_claim_token(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if not token or quote(token, safe="") != token:
            msg = "claim_token must be non-empty and URL-safe"
            raise ValueError(msg)
        return value

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https"):
            msg = "base_url must be http or https"
            raise ValueError(msg)
        if parsed.hostname is None:
            msg = "base_url must include a host"
            raise ValueError(msg)
        if parsed.username is not None or parsed.password is not None:
            msg = "base_url must not include user-info"
            raise ValueError(msg)
        # Plaintext only where the traffic cannot leave the machine. See
        # is_loopback_host for why `localhost` does not qualify.
        if parsed.scheme == "http" and not is_loopback_host(parsed.hostname):
            msg = "base_url must use https unless the host is a literal loopback IP address"
            raise ValueError(msg)
        return value


def default_config_path() -> Path:
    return Path(user_config_dir(APP_NAME)) / "config.toml"


def warn_if_permissive(path: Path) -> None:
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(
            (
                f"warning: {path} is readable or writable by group/other; "
                "it contains credentials and should be chmod 600"
            ),
            file=sys.stderr,
        )


def load_config(path: Path) -> Config:
    try:
        raw = path.read_text()
    except OSError as exc:
        msg = f"cannot read config file {path}: {exc}"
        raise ConfigError(msg) from exc

    warn_if_permissive(path)

    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        msg = f"malformed TOML in {path}: {exc}"
        raise ConfigError(msg) from exc

    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        # Never str(exc) directly: pydantic's default rendering includes each
        # field's raw input value, which would print credentials (access_url,
        # claim_token, passwords) straight to stderr on a validation failure.
        details = "\n".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_url=False, include_input=False)
        )
        msg = f"invalid config in {path}:\n{details}"
        raise ConfigError(msg) from None
