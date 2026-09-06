"""Command-line entry points: `claim`, `gen-token`, and `serve`."""

from __future__ import annotations

import base64
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, NoReturn, cast

import httpx2
import typer
import uvicorn

from .access_log import install_access_log_redaction
from .app import CLAIM_PATH_PREFIX, create_app
from .config import Config, ConfigError, default_config_path, load_config
from .provider_access_urls import (
    AccessUrlStoreError,
    access_urls_path,
    check_can_save,
    load_access_urls,
    save_access_url,
)
from .setup_token import build_setup_token
from .url_validation import UrlValidationError, validate_access_url, validate_claim_url


if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import FastAPI

    from .provider_allowlist import ProviderEntry
    from .url_validation import NormalizedUrl

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _build_claim_client() -> httpx2.Client:
    """Overridden in tests to inject an httpx2.MockTransport."""
    return httpx2.Client(follow_redirects=False)


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


def _fail(*lines: str) -> NoReturn:
    """Report an error on stderr and exit non-zero."""
    for line in lines:
        typer.echo(line, err=True)
    raise typer.Exit(code=1)


def _resolve_config_path(config: Path | None) -> Path:
    return config if config is not None else default_config_path()


def _load_config_or_exit(config: Path | None) -> Config:
    try:
        return load_config(_resolve_config_path(config))
    except ConfigError as exc:
        _fail(f"error: {exc}")


def _select_provider(entries: Sequence[ProviderEntry]) -> ProviderEntry:
    """Ask which provider the setup token came from.

    A menu rather than free text, and no default: which root the token is
    matched against is the whole of the phishing defense, so it is the user's
    deliberate answer or nothing.
    """
    typer.echo("Which provider did you get this setup token from?")
    for number, entry in enumerate(entries, start=1):
        typer.echo(f"  {number}. {entry.label} ({entry.root.origin_and_path})")
    choice = cast(str, typer.prompt("Provider"))

    # isdecimal, not isdigit: "²".isdigit() is true and int("²") then raises.
    if not choice.isdecimal() or not 1 <= int(choice) <= len(entries):
        _fail(f"error: enter a number between 1 and {len(entries)}")
    return entries[int(choice) - 1]


def _decode_setup_token(setup_token: str) -> str:
    """Decode a setup token to the claim URL it carries, unvalidated.

    Plain `b64decode`, as the SimpleFIN reference implementation does:
    `validate=True` would reject tokens a real provider issued, and buys
    nothing, since what actually protects the user is matching the decoded URL
    against the selected root.
    """
    try:
        claim_url_bytes = base64.b64decode(setup_token)
    except ValueError as exc:
        # binascii.Error (bad padding, and with it bad length) subclasses
        # ValueError, as does a token with non-ASCII characters in it.
        _fail(f"error: setup token is not valid base64: {exc}")

    try:
        return claim_url_bytes.decode("ascii")
    except UnicodeDecodeError:
        # Not the exception text: it quotes the offending bytes, which are
        # whatever the token decoded to.
        _fail("error: decoded setup token is not ASCII, so it is not a URL")


def _claim_access_url(claim_url: NormalizedUrl, entry: ProviderEntry) -> str:
    """POST the claim URL and return the access URL the provider replies with."""
    with _build_claim_client() as claim_client:
        try:
            # origin_and_path, not the string the token decoded to: it is the
            # rendering that was matched against the root, and a claim URL
            # carries no credentials for it to have dropped.
            response = claim_client.post(claim_url.origin_and_path)
        except httpx2.HTTPError as exc:
            # httpx2's message describes the failure without naming the URL,
            # whose path is the still-unclaimed setup token.
            _fail(f"error: could not reach provider {entry.slug!r}: {exc}")

    if response.status_code == HTTPStatus.FORBIDDEN:
        already_claimed = (
            "A setup token is claimable once. If you did not just claim this one "
            "yourself, someone else has, and it should be revoked at the provider."
        )
        _fail(f"error: provider {entry.slug!r} rejected the setup token (403).", already_claimed)
    if response.status_code != HTTPStatus.OK:
        # The status alone. A response body is attacker-influenced, and this
        # path now also catches the 3xx that redirects-disabled turns into a
        # failure rather than a hop.
        _fail(f"error: claim failed: provider {entry.slug!r} answered {response.status_code}")

    # Stripped, since a provider that ends the body with a newline means the
    # URL and not a URL with a control character in it, which is what
    # validation would otherwise see.
    return response.text.strip()


@app.command()
def claim(
    setup_token: Annotated[str | None, typer.Argument(help="Prompted for if omitted.")] = None,
    config: _ConfigOption = None,
    cachedir: _CacheDirOption = None,
) -> None:
    """Claim a one-time SimpleFIN setup token and store the access URL it returns."""
    loaded_config = _load_config_or_exit(config)
    store_path = access_urls_path(cachedir)

    # Before the token is spent, since a file problem found after the POST is a
    # lost credential: the token cannot be claimed a second time.
    try:
        _ = load_access_urls(store_path)
        check_can_save(store_path)
    except AccessUrlStoreError as exc:
        _fail(f"error: {exc}")

    entry = _select_provider(loaded_config.provider_entries())
    token = setup_token if setup_token is not None else typer.prompt("Setup token")

    try:
        # Before the POST, not after: a host you contact has already learned
        # your egress IP and that the token is live, however you treat its
        # reply.
        claim_url = validate_claim_url(entry.root, _decode_setup_token(token), provider=entry.slug)
    except UrlValidationError as exc:
        # Phrased as a checklist rather than a diagnosis, because every reason
        # validate_claim_url rejects a URL arrives as the same exception, and
        # there is deliberately no code on it to tell them apart. The token is
        # unspent whichever it was: validation runs before the POST.
        what_to_check = (
            f"Nothing was claimed and the setup token is still unspent. Check that you "
            f"pasted the whole token and that it came from {entry.label}; a provider "
            f"the menu does not list needs an [[allowlist]] entry in "
            f"{_resolve_config_path(config)} before its tokens can be claimed."
        )
        _fail(f"error: {exc}", what_to_check)

    access_url = _claim_access_url(claim_url, entry)

    try:
        _ = validate_access_url(entry.root, access_url, provider=entry.slug)
    except UrlValidationError as exc:
        _fail(f"error: {exc}")

    try:
        save_access_url(store_path, entry.slug, access_url)
    except AccessUrlStoreError as exc:
        _fail(f"error: {exc}")
    except OSError as exc:
        _fail(f"error: cannot write access URL file {store_path}: {exc}")

    typer.echo(f"Claimed {entry.label} as provider_key {entry.slug!r}.")
    not_disposable = (
        f"note: its access URL is now in {store_path}, which is not disposable — "
        "replacing it needs a fresh setup token from the provider."
    )
    typer.echo(not_disposable, err=True)

    if entry.slug not in {provider.provider_key for provider in loaded_config.providers}:
        # serve reads the store by the provider_key its config names, so a
        # claim the config does not reference would otherwise look like a
        # success and then fail at startup with "claim one first".
        unreferenced = (
            f"warning: no [[providers]] entry in {_resolve_config_path(config)} names "
            f"provider_key {entry.slug!r}, so serve will not use this access URL."
        )
        typer.echo(unreferenced, err=True)


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
        _fail(f"error: {exc}")


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
