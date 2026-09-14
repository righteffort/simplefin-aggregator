#!/usr/bin/env python3
"""Manual, human-run end-to-end check against the real SimpleFIN demo bridge.

Not part of the automated test suite, which never makes a real network call.
This one does: it fetches two fresh demo setup tokens, writes a throwaway
config naming the demo bridge as two providers, claims a token for each, issues
itself a setup token, starts the real server, claims that token the way a client
app would, and uses the credentials it gets back.

Run it with no arguments to fetch the demo setup tokens from
https://beta-bridge.simplefin.org/info/developers -- that page mints a new one
on every load -- or pass two already in hand:

    uv run scripts/manual_verify.py [<demo-setup-token> <demo-setup-token>]
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from base64 import b64encode
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import unquote, urlencode, urlsplit


if TYPE_CHECKING:
    from collections.abc import Generator
    from http.client import HTTPResponse

# Resolved to an absolute path, since a bare name is looked up against PATH
# afresh by every subprocess.
_FOUND_UV = shutil.which("uv")
if _FOUND_UV is None:
    _NO_UV = "uv is not on PATH"
    raise RuntimeError(_NO_UV)
UV: str = _FOUND_UV

PORT = 8321
BASE_URL = f"http://127.0.0.1:{PORT}"
# The demo bridge is the built-in `simplefin-bridge` provider, and the only
# live one this script can claim from, so it is configured a second time under
# a custom key to give the server two providers. That one takes the blank
# prefix, so both a default prefix and the catch-all are routed through.
PREFIXED_KEY = "simplefin-bridge"
PREFIX = f"{PREFIXED_KEY}:"
BLANK_KEY = "demo-bridge-again"
APP_KEY = "manual-verify"

DEVELOPERS_PAGE = "https://beta-bridge.simplefin.org/info/developers"
# The page embeds the token in a snippet of markup around it; a bare run of
# alphanumeric characters this long is the token and nothing else on the page.
_TOKEN_PATTERN = re.compile(r"[0-9A-Za-z]{80,}")

CONFIG = f"""
bind_host = "127.0.0.1"
bind_port = {PORT}
base_url = "{BASE_URL}"

[[custom_providers]]
key = "{BLANK_KEY}"
label = "SimpleFIN demo bridge, again"
root = "https://beta-bridge.simplefin.org/simplefin"

[[providers]]
key = "{PREFIXED_KEY}"

[[providers]]
key = "{BLANK_KEY}"
prefix = ""
"""


def aggregator(*arguments: str, config_dir: Path, stdin: str = "") -> str:
    """Run one aggregator command, returning its stdout.

    A secret goes in `stdin`, never in `arguments`: an argument is visible in
    `ps` to every user on the machine, and `CalledProcessError` renders the
    whole command line into its message.
    """
    completed = subprocess.run(  # noqa: S603  # no shell, and the program is fixed
        [UV, "run", "simplefin-aggregator", *arguments],
        input=stdin,
        env={**os.environ, "SIMPLEFIN_AGGREGATOR_DIR": str(config_dir)},
        check=False,
        capture_output=True,
        text=True,
    )
    # Always, not only on success: the run that failed is the one whose
    # diagnosis is worth reading.
    print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        message = f"`{arguments[0]}` exited {completed.returncode}"
        raise RuntimeError(message)
    return completed.stdout.strip()


def request(
    url: str, *, method: str = "GET", auth: tuple[str, str] | None = None
) -> tuple[int, str]:
    """One HTTP request, returning the status and body rather than raising on 4xx."""
    request_headers: dict[str, str] = {}
    if auth is not None:
        request_headers["Authorization"] = "Basic " + b64encode(":".join(auth).encode()).decode()
    try:
        opened = cast(
            "HTTPResponse",
            urllib.request.urlopen(  # noqa: S310  # this script's own URLs, http on loopback
                urllib.request.Request(url, method=method, headers=request_headers)  # noqa: S310
            ),
        )
        with opened as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def fetch_demo_token() -> str:
    """Fetch a fresh demo setup token from the SimpleFIN developers page."""
    # The site 403s Python's default User-Agent string; any browser-shaped one works.
    fetch_request = urllib.request.Request(  # a fixed https URL
        DEVELOPERS_PAGE, headers={"User-Agent": "curl/8.0"}
    )
    with cast("HTTPResponse", urllib.request.urlopen(fetch_request)) as response:  # noqa: S310
        page = response.read().decode()
    found = _TOKEN_PATTERN.search(page)
    if found is None:
        message = f"Unable to find demo setup token on {DEVELOPERS_PAGE}"
        raise RuntimeError(message)
    return found.group()


def expect(result: tuple[int, str], want: HTTPStatus, what: str) -> str:
    """Check one status and report it, returning the body.

    Every status this script prints is part of what it verifies -- a claim that
    can be replayed, an unauthenticated request that succeeds -- so a mismatch
    has to end the run. Printed and not checked, it would scroll past a reader
    who is watching for the next prompt, and the script would exit 0.
    """
    status, body = result
    if status != want:
        message = f"{what}: expected HTTP {want}, got HTTP {status}"
        raise RuntimeError(message)
    print(f"    HTTP {status}")
    return body


@contextmanager
def serving(config_dir: Path) -> Generator[None]:
    """Run the real server for the duration of the block."""
    server = subprocess.Popen(  # noqa: S603  # no shell, and the program is fixed
        [UV, "run", "simplefin-aggregator", "serve"],
        env={**os.environ, "SIMPLEFIN_AGGREGATOR_DIR": str(config_dir)},
    )
    try:
        for _ in range(50):
            if server.poll() is not None:
                exited = "the server exited before it started listening"
                raise RuntimeError(exited)
            try:
                _status, _body = request(f"{BASE_URL}/simplefin/info")
            except OSError:
                time.sleep(0.2)
            else:
                break
        else:
            never_listened = "the server never started listening"
            raise RuntimeError(never_listened)
        yield
    finally:
        server.terminate()
        _ = server.wait()


def account_ids(body: str) -> list[str]:
    accounts = cast(
        "list[dict[str, object]]", cast("dict[str, object]", json.loads(body))["accounts"]
    )
    return [cast(str, account["id"]) for account in accounts]


def main(demo_setup_tokens: tuple[str, str] | None) -> None:
    if demo_setup_tokens is None:
        print(f"==> Fetching two fresh demo setup tokens from {DEVELOPERS_PAGE}")
        demo_setup_tokens = (fetch_demo_token(), fetch_demo_token())
    # Removed on every path: it ends up holding live provider access URLs,
    # with the credentials for the user's bank data embedded in them.
    with tempfile.TemporaryDirectory() as name:
        run(demo_setup_tokens, Path(name))


def run(demo_setup_tokens: tuple[str, str], config_dir: Path) -> None:
    config_file = config_dir / "config.toml"
    _ = config_file.write_text(CONFIG)
    config_file.chmod(0o600)

    for key, demo_setup_token in zip((PREFIXED_KEY, BLANK_KEY), demo_setup_tokens, strict=True):
        print(f"==> Claiming a SimpleFIN demo setup token as {key}")
        # On stdin, which `claim` prompts for, rather than as an argument.
        _ = aggregator(
            "claim", "--provider", key, config_dir=config_dir, stdin=f"{demo_setup_token}\n"
        )

    print("==> Issuing a setup token for this run")
    setup_token = aggregator("app", "new", "--key", APP_KEY, config_dir=config_dir)
    claim_url = base64.b64decode(setup_token).decode("ascii")

    with serving(config_dir):
        print("==> Claiming that setup token, as a client app would")
        access_url = expect(request(claim_url, method="POST"), HTTPStatus.OK, "the claim")
        credentials = urlsplit(access_url)
        auth = (unquote(credentials.username or ""), unquote(credentials.password or ""))
        print("    access URL received")

        print("==> Claiming it a second time (expect 403)")
        _ = expect(request(claim_url, method="POST"), HTTPStatus.FORBIDDEN, "the repeated claim")

        print("==> GET /simplefin/info")
        info = expect(request(f"{BASE_URL}/simplefin/info"), HTTPStatus.OK, "/simplefin/info")
        print(f"    {info}")

        print("==> GET /simplefin/accounts")
        accounts = expect(
            request(f"{BASE_URL}/simplefin/accounts", auth=auth),
            HTTPStatus.OK,
            "/simplefin/accounts",
        )
        print(f"    {accounts}")
        # simplefin-aggregator responds 200 for an unreachable provider, so
        # check that each provider contributed accounts.
        ids = account_ids(accounts)
        prefixed = [account_id for account_id in ids if account_id.startswith(PREFIX)]
        blank = [account_id for account_id in ids if not account_id.startswith(PREFIX)]
        if not prefixed or not blank:
            message = "/simplefin/accounts is missing a provider's accounts; it did not answer"
            raise RuntimeError(message)
        if ids != prefixed + blank:
            message = "/simplefin/accounts did not list accounts in configured provider order"
            raise RuntimeError(message)

        print("==> GET /simplefin/accounts naming one account from each provider")
        # Named in the reverse of configured order; the answer follows configured order.
        query = urlencode([("account", blank[0]), ("account", prefixed[0])])
        filtered = expect(
            request(f"{BASE_URL}/simplefin/accounts?{query}", auth=auth),
            HTTPStatus.OK,
            "/simplefin/accounts naming accounts",
        )
        print(f"    {filtered}")
        if account_ids(filtered) != [prefixed[0], blank[0]]:
            message = "/simplefin/accounts did not answer with exactly the accounts named"
            raise RuntimeError(message)

        print("==> GET /simplefin/accounts with no credentials (expect 403)")
        _ = expect(
            request(f"{BASE_URL}/simplefin/accounts"),
            HTTPStatus.FORBIDDEN,
            "/simplefin/accounts with no credentials",
        )

        print("==> Revoking the app, then asking again (expect 403, with no restart)")
        _ = aggregator("app", "revoke", "--key", APP_KEY, config_dir=config_dir)
        _ = expect(
            request(f"{BASE_URL}/simplefin/accounts", auth=auth),
            HTTPStatus.FORBIDDEN,
            "/simplefin/accounts after revoking",
        )

    print("==> Done.")


if __name__ == "__main__":
    # A setup token given on a command line is still visible in shell history
    # and `ps`, unlike `claim`'s prompt. But this script is only intended to
    # handle a SimpleFIN demo token, which grants access to demo data rather
    # than a real account, so the exposure is a paste of throwaway data, not
    # a credential.
    match sys.argv[1:]:
        case []:
            main(None)
        case [first, second]:
            main((first, second))
        case _:
            print(f"usage: {sys.argv[0]} [<demo-setup-token> <demo-setup-token>]", file=sys.stderr)
            print(
                f"  with no arguments, fetches demo setup tokens from {DEVELOPERS_PAGE}",
                file=sys.stderr,
            )
            sys.exit(1)
