"""Configuration model and loading for simplefin-aggregator."""

import ipaddress
import stat
import sys
import tomllib
from pathlib import Path
from urllib.parse import SplitResult, quote, urlsplit

from platformdirs import user_config_dir
from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator


APP_NAME = "simplefin-aggregator"


class ConfigError(Exception):
    """Raised when the config file is missing, malformed, or fails validation."""


class Provider(BaseModel):
    """A single SimpleFIN provider this aggregator proxies."""

    name: str
    access_url: SecretStr

    @field_validator("access_url")
    @classmethod
    def _validate_access_url(cls, value: SecretStr) -> SecretStr:
        parsed = urlsplit(value.get_secret_value())
        if parsed.scheme != "https":
            msg = "access_url must use https"
            raise ValueError(msg)
        if parsed.hostname is None:
            msg = "access_url must include a host"
            raise ValueError(msg)
        if parsed.username is None or parsed.password is None:
            msg = "access_url must embed basic auth credentials"
            raise ValueError(msg)
        return value

    def parsed_access_url(self) -> SplitResult:
        """Parse the access URL on demand; never cached in a logged field."""
        return urlsplit(self.access_url.get_secret_value())


class ClientAuth(BaseModel):
    """The basic-auth credentials this aggregator requires from a client app."""

    username: str
    password: SecretStr


class Config(BaseModel):
    bind_host: str = "127.0.0.1"
    bind_port: int = 8080
    # Schema and internal types are already a list for the multi-provider version to come;
    # this version only supports exactly one.
    providers: list[Provider] = Field(min_length=1, max_length=1)
    client: ClientAuth
    claim_token: SecretStr
    base_url: str

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
        if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
            msg = "base_url must use https unless the host is a loopback address"
            raise ValueError(msg)
        return value


def _is_loopback(hostname: str) -> bool:
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def default_config_path() -> Path:
    return Path(user_config_dir(APP_NAME)) / "config.toml"


def _warn_if_permissive(path: Path) -> None:
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

    _warn_if_permissive(path)

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
