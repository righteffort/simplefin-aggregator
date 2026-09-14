"""Command-line entry points: `claim`, `app`, and `serve`."""

from __future__ import annotations

import base64
import sys
from http import HTTPStatus
from typing import TYPE_CHECKING, Annotated, NoReturn, cast

import httpx2
import typer
import uvicorn
from pydantic import SecretStr

from .access_log import install_access_log_redaction
from .app import CLAIM_PATH_PREFIX, ProviderAccessUrlError, create_app
from .app_tokens import (
    ClaimedAppToken,
    app_tokens_path,
    load_app_tokens,
    new_app_token,
    update_app_tokens,
)
from .config import (
    DIR_ENV_VAR,
    Config,
    ConfigError,
    config_dir,
    config_path,
    default_config_dir,
    load_config,
)
from .provider_access_urls import load_access_urls, provider_creds_path, save_access_url
from .provider_registry import KEY_PATTERN, ProviderRegistryError, find_provider
from .setup_token import build_setup_token
from .state_file import StateFileError, check_can_save
from .url_validation import UrlValidationError, validate_access_url, validate_claim_url


if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from pathlib import Path

    from fastapi import FastAPI

    from .provider_registry import ProviderEntry
    from .url_validation import NormalizedUrl

# Help text here is plain text: in Typer's "rich" mode a bracketed pattern such
# as the key rule is read as a markup tag and silently dropped.
app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode=None,
    help=(
        "simplefin-aggregator: a SimpleFIN Bridge server backed by other SimpleFIN "
        f"providers. Reads and writes its state in the directory named by {DIR_ENV_VAR}, "
        f"or {default_config_dir()} if that is unset."
    ),
)


# Longer than the probe's: a read timeout comes after the POST has gone out,
# when the provider may have spent the token on a reply that never arrives.
_CLAIM_TIMEOUT = httpx2.Timeout(30.0)


def _build_claim_client() -> httpx2.Client:
    """Overridden in tests to inject an httpx2.MockTransport."""
    return httpx2.Client(timeout=_CLAIM_TIMEOUT, follow_redirects=False)


def _stdin_is_a_terminal() -> bool:
    """Overridden in tests: CliRunner's stdin is never a real terminal."""
    return sys.stdin.isatty()


def _fail(*lines: str) -> NoReturn:
    """Report an error on stderr and exit non-zero."""
    for line in lines:
        typer.echo(line, err=True)
    raise typer.Exit(code=1)


def _load_config_or_exit() -> Config:
    try:
        return load_config(config_path())
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


def _resolve_provider(entries: Sequence[ProviderEntry], key: str | None) -> ProviderEntry:
    """Which provider the setup token came from: named on the command line, or asked for."""
    if key is None:
        return _select_provider(entries)

    _check_key(key, "--provider")
    try:
        return find_provider(entries, key)
    except ProviderRegistryError as exc:
        _fail(f"error: {exc}")


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
        _fail(f"error: pasted setup token is not valid base64: {exc}")

    try:
        return claim_url_bytes.decode("ascii")
    except UnicodeDecodeError:
        # Not the exception text: it quotes the offending bytes, which are
        # whatever the token decoded to.
        _fail("error: decoded setup token is not ASCII, so it is not a URL")


def _claim_access_url(claim_url: NormalizedUrl, entry: ProviderEntry) -> str:
    """POST the claim URL and return the access URL the provider replies with."""
    # Whether this reached the provider is unknown, so retrying with the same
    # token is safe advice here; the 403 case below is definite and gets none.
    retry_advice = (
        "This request is not retried automatically. Run `claim` again with the "
        "same setup token: it is still valid unless the provider already spent it."
    )
    with _build_claim_client() as claim_client:
        try:
            # origin_and_path, not the string the token decoded to: it is the
            # rendering that was matched against the root, and a claim URL
            # carries no credentials for it to have dropped.
            response = claim_client.post(claim_url.origin_and_path)
        except httpx2.HTTPError as exc:
            # Not the message: httpx2's text can quote provider-chosen bytes,
            # and the request path here is the live setup token.
            _fail(
                f"error: could not reach provider {entry.key!r} ({type(exc).__name__})",
                retry_advice,
            )

    if response.status_code == HTTPStatus.FORBIDDEN:
        already_claimed = (
            "A setup token is claimable once. If you did not just claim this one "
            "yourself, someone else has, and it should be revoked at the provider."
        )
        _fail(f"error: provider {entry.key!r} rejected the setup token (403).", already_claimed)
    if response.status_code != HTTPStatus.OK:
        # The status alone. A response body is attacker-influenced, and this
        # path also catches the 3xx that redirects-disabled turns into a
        # failure rather than a hop.
        _fail(
            f"error: claim failed: provider {entry.key!r} answered {response.status_code}",
            retry_advice,
        )

    # Stripped, since a provider that ends the body with a newline means the
    # URL and not a URL with a control character in it, which is what
    # validation would otherwise see.
    return response.text.strip()


_PROBE_TIMEOUT = httpx2.Timeout(10.0)


def _build_probe_client(access_url: NormalizedUrl) -> httpx2.Client:
    """Overridden in tests to inject an httpx2.MockTransport."""
    return httpx2.Client(
        base_url=access_url.origin_and_path,
        auth=(access_url.username, access_url.password),
        timeout=_PROBE_TIMEOUT,
        follow_redirects=False,
    )


def _probe_access_url(access_url: NormalizedUrl) -> str | None:
    """GET /accounts?balances-only=1 once, to check the credentials work. None on success.

    One attempt, bounded by _PROBE_TIMEOUT so a dead provider does not turn
    `claim` into a hang -- like the claim POST, and unlike it not retried
    because there is nothing irreversible to protect here: the access URL is
    already stored either way, so a failed check costs the user nothing they
    cannot repeat by hand.
    """
    typer.echo("Checking that the credentials work...", err=True)
    try:
        with _build_probe_client(access_url) as probe_client:
            response = probe_client.get("/accounts", params={"balances-only": "1"})
    except httpx2.HTTPError as exc:
        # Not str(exc): see _claim_access_url -- the same provider-chosen
        # bytes, including a possibly-echoed Authorization header, can end up
        # in httpx2's exception text.
        return f"could not reach {access_url.origin} ({type(exc).__name__})"
    if response.status_code == HTTPStatus.OK:
        typer.echo("Credentials work.", err=True)
        return None
    return f"{access_url.origin} answered HTTP {response.status_code}"


@app.command()
def claim(
    provider: Annotated[
        str | None,
        typer.Option(
            "--provider",
            help=f"Provider key, matching {KEY_PATTERN.pattern}. Asked for if omitted.",
        ),
    ] = None,
) -> None:
    """Claim a one-time SimpleFIN setup token and store the access URL it returns."""
    loaded_config = _load_config_or_exit()
    store_path = provider_creds_path()

    # Before the token is spent, since a file problem found after the POST is a
    # lost credential: the token cannot be claimed a second time.
    try:
        _ = load_access_urls(store_path)
        check_can_save(store_path)
    except StateFileError as exc:
        _fail(f"error: {exc}")

    entry = _resolve_provider(loaded_config.provider_entries(), provider)
    # hide_input only when stdin is a real terminal: hiding unconditionally
    # would route through getpass, which opens /dev/tty directly and ignores
    # a redirected file even though stdin is not a tty in that case.
    token = cast(str, typer.prompt("Setup token", hide_input=_stdin_is_a_terminal()))

    try:
        # Before the POST, not after: a host you contact has already learned
        # your egress IP and that the token is live, however you treat its
        # reply.
        claim_url = validate_claim_url(entry.root, _decode_setup_token(token), provider=entry.key)
    except UrlValidationError as exc:
        # Phrased as a checklist rather than a diagnosis, because every reason
        # validate_claim_url rejects a URL arrives as the same exception, and
        # there is deliberately no code on it to tell them apart. The token is
        # unspent whichever it was: validation runs before the POST.
        what_to_check = (
            f"Nothing was claimed and the setup token is still unspent. Check that you "
            f"pasted the whole token and that it came from {entry.label}; a provider "
            f"the menu does not list needs a [[custom_providers]] entry in "
            f"{config_path()} before its tokens can be claimed."
        )
        _fail(f"error: {exc}", what_to_check)

    access_url = SecretStr(_claim_access_url(claim_url, entry))

    try:
        validated_access_url = validate_access_url(
            entry.root, access_url.get_secret_value(), provider=entry.key
        )
    except UrlValidationError as exc:
        _fail(f"error: {exc}")

    try:
        save_access_url(store_path, entry.key, access_url)
    except StateFileError as exc:
        _fail(f"error: {exc}")

    typer.echo(f"Claimed {entry.label} as key {entry.key!r}.")
    typer.echo(f"note: its access URL is now in {store_path}.", err=True)

    # A warning, not a failure: the access URL is stored and valid, and the
    # setup token is spent and cannot be re-claimed if this exits non-zero.
    probe_failure = _probe_access_url(validated_access_url)
    if probe_failure is not None:
        could_not_confirm = f"warning: could not confirm the access URL works ({probe_failure})"
        typer.echo(f"{could_not_confirm}; it is stored anyway.", err=True)

    if entry.key not in {provider.key for provider in loaded_config.providers}:
        # serve reads the store by the key its config names, so a
        # claim the config does not reference would otherwise look like a
        # success and then fail at startup with "claim one first".
        unreferenced = (
            f"warning: no [[providers]] entry in {config_path()} names "
            f"key {entry.key!r}, so serve will not use this access URL."
        )
        typer.echo(unreferenced, err=True)


app_commands = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode=None,
    help="Manage the client apps this aggregator issues credentials to.",
)
app.add_typer(app_commands, name="app")

_KeyOption = Annotated[
    str, typer.Option("--key", help=f"The app's key, matching {KEY_PATTERN.pattern}.")
]


def _writable_store_or_exit() -> Path:
    """Where the store lives, having reported anything about the directory first."""
    store_path = app_tokens_path()
    try:
        check_can_save(store_path)
    except StateFileError as exc:
        _fail(f"error: {exc}")
    return store_path


def _check_key(key: str, option: str = "--key") -> None:
    """Reject a key the stores cannot be keyed by, without repeating it back.

    A mistyped key is most often a pasted setup token, which is a secret and
    which this pattern rejects because base64 is not in it. Naming the value
    would put that token on stderr to tell the user something they just typed.
    Every message past this check may name a key, since one that got here
    matched.
    """
    if not KEY_PATTERN.fullmatch(key):
        _fail(f"error: {option} must match {KEY_PATTERN.pattern}")


def _print_setup_token(base_url: str, claim_secret: str, key: str, store_path: Path) -> None:
    """Put the setup token on stdout alone, so `$(...)` captures it and nothing else."""
    typer.echo(f"note: {key!r} is now in {store_path}.", err=True)
    typer.echo(build_setup_token(base_url, claim_secret))
    shown_once = (
        f"note: this setup token is shown once and is not stored. If it is lost before "
        f"{key!r} claims it, run `app regen --key {key}` for a new one."
    )
    typer.echo(shown_once, err=True)


@app_commands.command("new")
def app_new(key: _KeyOption) -> None:
    """Issue a setup token for a client app that does not have one yet."""
    loaded_config = _load_config_or_exit()
    _check_key(key)
    store_path = _writable_store_or_exit()

    try:
        # The check and the add are one locked update: read the store first and
        # the duplicate check is answered from a version another writer is
        # already replacing.
        with update_app_tokens(store_path) as apps:
            if key in apps:
                _fail(
                    f"error: an app with key {key!r} already exists.",
                    f"To replace its credentials with a fresh setup token: app regen --key {key}",
                )
            claim_secret, apps[key] = new_app_token()
    except StateFileError as exc:
        _fail(f"error: {exc}")

    # After the store is written, never before: a token this aggregator has no
    # record of looks to the user like a working setup that never syncs.
    _print_setup_token(loaded_config.base_url, claim_secret, key, store_path)


def _format_time(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


@app_commands.command("list")
def app_list() -> None:
    """Show the client apps."""
    try:
        apps = load_app_tokens(app_tokens_path())
    except StateFileError as exc:
        _fail(f"error: {exc}")

    if not apps:
        typer.echo("No apps yet. Create one with `app new --key <key>`.", err=True)
        return

    header = ("KEY", "STATUS", "CREATED", "CLAIMED")
    rows = [
        (
            key,
            record.status,
            _format_time(record.created_at),
            _format_time(record.claimed_at) if isinstance(record, ClaimedAppToken) else "",
        )
        for key, record in sorted(apps.items())
    ]
    widths = [max(len(row[column]) for row in (header, *rows)) for column in range(len(header))]
    for row in (header, *rows):
        cells = (cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        typer.echo("  ".join(cells).rstrip())


@app_commands.command("revoke")
def app_revoke(key: _KeyOption) -> None:
    """Forget a client app, so its credentials stop working and its token cannot be claimed."""
    _check_key(key)
    store_path = _writable_store_or_exit()

    try:
        with update_app_tokens(store_path) as apps:
            if key not in apps:
                _fail(f"error: no app has key {key!r}. `app list` shows the ones that do.")
            del apps[key]
    except StateFileError as exc:
        _fail(f"error: {exc}")

    typer.echo(f"Revoked {key!r}.")


@app_commands.command("regen")
def app_regen(key: _KeyOption) -> None:
    """Issue a client app a fresh setup token, revoking whatever it holds now."""
    loaded_config = _load_config_or_exit()
    _check_key(key)
    store_path = _writable_store_or_exit()

    try:
        with update_app_tokens(store_path) as apps:
            if key not in apps:
                _fail(f"error: no app has key {key!r}. `app list` shows the ones that do.")
            claim_secret, apps[key] = new_app_token()
    except StateFileError as exc:
        _fail(f"error: {exc}")

    _print_setup_token(loaded_config.base_url, claim_secret, key, store_path)


def _build_app_or_exit(loaded_config: Config) -> FastAPI:
    try:
        access_urls = load_access_urls(provider_creds_path())
        return create_app(loaded_config, access_urls, app_tokens_path())
    except (StateFileError, UrlValidationError) as exc:
        _fail(f"error: {exc}")
    except ProviderAccessUrlError as exc:
        _fail(*(f"error: {message}" for message in exc.messages))


def _check_app_store_or_exit() -> None:
    """Fail before uvicorn starts if the store cannot be read or written.

    The server reads this file on every authenticated request and writes it on
    every claim, so a file it cannot parse or a directory it cannot write to
    is a server that answers 403 to everything and loses every claim -- found
    here rather than by the first client app that tries.
    """
    store_path = app_tokens_path()
    try:
        apps = load_app_tokens(store_path)
        check_can_save(store_path)
    except StateFileError as exc:
        _fail(f"error: {exc}")

    if not apps:
        # A warning and not a failure: this is the legitimate state between
        # installing the server and issuing the first app its token.
        no_apps = (
            "warning: no client apps yet, so every request will be refused. "
            "Issue one with `app new --key <key>`."
        )
        typer.echo(no_apps, err=True)


@app.command()
def serve() -> None:
    """Read the config and run the server. Never claims anything."""
    # Named because the environment chooses it, invisibly.
    typer.echo(f"note: using config directory {config_dir()}.", err=True)
    loaded_config = _load_config_or_exit()
    _check_app_store_or_exit()
    fastapi_app = _build_app_or_exit(loaded_config)

    install_access_log_redaction(
        logger_name="uvicorn.access",
        path_prefix=CLAIM_PATH_PREFIX,
        replacement=f"{CLAIM_PATH_PREFIX}[REDACTED]",
    )
    uvicorn.run(fastapi_app, host=loaded_config.bind_host, port=loaded_config.bind_port)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
