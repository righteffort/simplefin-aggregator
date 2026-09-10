#!/usr/bin/env python3
"""Manual, human-run end-to-end check against the real SimpleFIN demo bridge.

Not part of the automated test suite, which never makes a real network call.
This one does: it writes a throwaway config, claims a demo setup token, issues
itself a setup token, starts the real server, claims that token the way a
client app would, and uses the credentials it gets back.

Get a fresh demo setup token from
https://beta-bridge.simplefin.org/info/developers -- that page mints a new one
on every load -- then run:

    uv run scripts/manual_verify.py <demo-setup-token>
"""

from __future__ import annotations

import base64
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
from urllib.parse import unquote, urlsplit


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
# The demo bridge is the built-in `simplefin-bridge` provider, so its root
# already covers the demo claim URL and no [[custom_providers]] entry is needed.
PROVIDER_KEY = "simplefin-bridge"
APP_KEY = "manual-verify"

CONFIG = f"""
bind_host = "127.0.0.1"
bind_port = {PORT}
base_url = "{BASE_URL}"

[[providers]]
key = "{PROVIDER_KEY}"
"""


def aggregator(*arguments: str, config_dir: Path, stdin: str = "") -> str:
    """Run one aggregator command, returning its stdout.

    A secret goes in `stdin`, never in `arguments`: an argument is visible in
    `ps` to every user on the machine, and `CalledProcessError` renders the
    whole command line into its message.
    """
    completed = subprocess.run(  # noqa: S603  # no shell, and the program is fixed
        [UV, "run", "simplefin-aggregator", *arguments, "--config-dir", str(config_dir)],
        input=stdin,
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
        [UV, "run", "simplefin-aggregator", "serve", "--config-dir", str(config_dir)]
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


def main(demo_setup_token: str) -> None:
    # Removed on every path: it ends up holding a live provider access URL,
    # with the credentials for the user's bank data embedded in it.
    with tempfile.TemporaryDirectory() as name:
        run(demo_setup_token, Path(name))


def run(demo_setup_token: str, config_dir: Path) -> None:
    config_file = config_dir / "config.toml"
    _ = config_file.write_text(CONFIG)
    config_file.chmod(0o600)

    print("==> Claiming the SimpleFIN demo setup token")
    # On stdin, which `claim` prompts for, rather than as an argument.
    _ = aggregator(
        "claim", "--provider", PROVIDER_KEY, config_dir=config_dir, stdin=f"{demo_setup_token}\n"
    )

    print("==> Issuing a setup token for this run")
    setup_token = aggregator(
        "app", "new", "--key", APP_KEY, "--label", "Manual verify", config_dir=config_dir
    )
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
    if len(sys.argv) != 2:  # noqa: PLR2004
        print(f"usage: {sys.argv[0]} <demo-setup-token>", file=sys.stderr)
        print("  get one from https://beta-bridge.simplefin.org/info/developers", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
