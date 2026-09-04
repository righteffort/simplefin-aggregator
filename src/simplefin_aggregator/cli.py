"""Command-line entry points: `claim`, `gen-token`, and `serve`."""

from __future__ import annotations

import base64
import binascii
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import httpx2
import typer
import uvicorn

from .access_log import install_access_log_redaction
from .app import CLAIM_PATH_PREFIX, create_app
from .config import Config, ConfigError, default_config_path, load_config
from .provider_access_urls import AccessUrlStoreError, access_urls_path, load_access_urls
from .setup_token import build_setup_token
from .url_validation import UrlValidationError


if TYPE_CHECKING:
    from fastapi import FastAPI

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _build_claim_client() -> httpx2.Client:
    """Overridden in tests to inject an httpx2.MockTransport."""
    return httpx2.Client(follow_redirects=True)


@app.command()
def claim(setup_token: str) -> None:
    """Claim a one-time SimpleFIN setup token and print the access URL it returns."""
    try:
        claim_url_bytes = base64.b64decode(setup_token, validate=True)
    except binascii.Error as exc:
        typer.echo(f"error: setup token is not valid base64: {exc}", err=True)
        raise typer.Exit(code=1) from None

    try:
        claim_url = claim_url_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        typer.echo(f"error: decoded setup token is not a valid URL: {exc}", err=True)
        raise typer.Exit(code=1) from None

    with _build_claim_client() as claim_client:
        try:
            response = claim_client.post(claim_url)
        except httpx2.HTTPError as exc:
            typer.echo(f"error: could not reach claim URL: {exc}", err=True)
            raise typer.Exit(code=1) from None

    if response.status_code != HTTPStatus.OK:
        typer.echo(
            f"error: claim failed with status {response.status_code}: {response.text}", err=True
        )
        raise typer.Exit(code=1)

    typer.echo(response.text)
    typer.echo(
        (
            "reminder: setup tokens are one-time-use. This access URL cannot be "
            "regenerated from the setup token again — store it in your config now."
        ),
        err=True,
    )


_ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", help=f"Path to config.toml (default: {default_config_path()})."),
]

_CacheDirOption = Annotated[
    Path | None,
    typer.Option(
        "--cachedir",
        help=(
            "Directory holding the access URLs claimed from providers "
            f"(default: {access_urls_path().parent}). Not disposable: its contents "
            "cannot be regenerated without a fresh setup token from each provider."
        ),
    ),
]


def _load_config_or_exit(config: Path | None) -> Config:
    config_path = config if config is not None else default_config_path()
    try:
        return load_config(config_path)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from None


@app.command("gen-token")
def gen_token(config: _ConfigOption = None) -> None:
    """Print a setup token a client app can use to claim this aggregator."""
    loaded_config = _load_config_or_exit(config)
    typer.echo(build_setup_token(loaded_config))


def _build_app_or_exit(loaded_config: Config, cachedir: Path | None) -> FastAPI:
    try:
        access_urls = load_access_urls(access_urls_path(cachedir))
        return create_app(loaded_config, access_urls)
    except (AccessUrlStoreError, UrlValidationError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from None


@app.command()
def serve(config: _ConfigOption = None, cachedir: _CacheDirOption = None) -> None:
    """Read the config and run the server. Never claims anything."""
    loaded_config = _load_config_or_exit(config)
    fastapi_app = _build_app_or_exit(loaded_config, cachedir)

    install_access_log_redaction(
        logger_name="uvicorn.access",
        method="POST",
        path_prefix=CLAIM_PATH_PREFIX,
        replacement=f"{CLAIM_PATH_PREFIX}[REDACTED]",
    )
    uvicorn.run(fastapi_app, host=loaded_config.bind_host, port=loaded_config.bind_port)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
