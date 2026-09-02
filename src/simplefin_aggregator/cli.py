"""Command-line entry points: `claim` and `serve`."""

import base64
import binascii
from http import HTTPStatus
from pathlib import Path  # noqa: TC003 (Typer needs this at runtime for CLI type resolution)
from typing import Annotated

import httpx
import typer

from .config import ConfigError, default_config_path, load_config


app = typer.Typer(add_completion=False, no_args_is_help=True)


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

    with httpx.Client(follow_redirects=True) as client:
        try:
            response = client.post(claim_url)
        except httpx.HTTPError as exc:
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


@app.command()
def serve(
    config: Annotated[
        Path | None,
        typer.Option("--config", help=f"Path to config.toml (default: {default_config_path()})."),
    ] = None,
) -> None:
    """Read the config and run the server. Never claims anything."""
    config_path = config if config is not None else default_config_path()

    try:
        _ = load_config(config_path)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo("serve: not yet implemented", err=True)
    raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
