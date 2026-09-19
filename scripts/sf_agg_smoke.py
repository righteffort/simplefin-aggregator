# SPDX-License-Identifier: GPL-3.0-only

"""End-to-end check of a real sf-agg against two fake providers.

It sets up providers:
- it starts two `sf_server_fake` instances serving the same accounts, and its
  throwaway config lists one provider for each
- for each provider, it exchanges a setup token for an access URL.
And then sets up and uses the aggregator:
- starts the server
- retrieves a setup token and exchanges it for an access URL
- uses that access URL to retrieve data from the two providers via the aggregator.

And tests other aspects of correct aggregator functioning.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import unquote, urlencode, urlsplit

import httpx2
from sf_server_fake import FakeUser, RunningFake, sf_server_fake


if TYPE_CHECKING:
    from collections.abc import Generator


# Run sf-agg with this interpeter
SF_AGG = [sys.executable, "-m", "sf_agg.cli"]


def _free_port() -> int:
    """Find a loopback port nothing is listening on."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return cast(tuple[str, int], probe.getsockname())[1]


PORT = _free_port()
BASE_URL = f"http://127.0.0.1:{PORT}"
PREFIXED_KEY = "fake-one"
PREFIX = f"{PREFIXED_KEY}:"
BLANK_KEY = "fake-two"

CLIENT_KEY = "sf-agg-smoke"

# Fake credentials, one per sf_server_fake, valid only against it.
USERS = (
    FakeUser(claim_secret="claim-one", username="user-one", password="password-one"),  # noqa: S106
    FakeUser(claim_secret="claim-two", username="user-two", password="password-two"),  # noqa: S106
)


def _account(account_id: str, name: str, balance: str) -> dict[str, object]:
    return {
        "org": {"domain": "bank.example.com", "sfin-url": "https://bank.example.com/simplefin"},
        "id": account_id,
        "name": name,
        "currency": "USD",
        "balance": balance,
        "balance-date": 1767225600,
        "transactions": [
            {
                "id": f"{account_id}-txn-1",
                "posted": 1767225600,
                "amount": "-12.34",
                "description": "Coffee",
            }
        ],
    }


ACCOUNTS = (_account("acct-1", "Checking", "100.00"), _account("acct-2", "Savings", "200.00"))


def config(origins: tuple[str, str]) -> str:
    return f"""
bind_host = "127.0.0.1"
bind_port = {PORT}
base_url = "{BASE_URL}"

[[custom_providers]]
key = "{PREFIXED_KEY}"
origin = "{origins[0]}"

[[custom_providers]]
key = "{BLANK_KEY}"
origin = "{origins[1]}"

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
    completed = subprocess.run(  # noqa: S603  # fixed program and args
        [*SF_AGG, *arguments],
        input=stdin,
        env={**os.environ, "SF_AGG_DIR": str(config_dir)},
        check=False,
        capture_output=True,
        text=True,
    )
    print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        message = f"`{arguments[0]}` exited {completed.returncode}"
        raise RuntimeError(message)
    return completed.stdout.strip()


def sfa_request(
    url: str, *, method: str = "GET", auth: tuple[str, str] | None = None
) -> tuple[int, str]:
    """One HTTP request to our server, returning the status and body rather than raising."""
    if not url.startswith(f"{BASE_URL}/simplefin/"):
        message = "sfa_request called with unexpected URL"
        raise RuntimeError(message)
    response = httpx2.request(method, url, auth=auth, follow_redirects=False, timeout=5.0)
    return response.status_code, response.text


def expect(result: tuple[int, str], want: HTTPStatus, what: str) -> str:
    """Expect an status code and print it, returning the body."""
    status, body = result
    if status != want:
        message = f"{what}: expected HTTP {want}, got HTTP {status}"
        raise RuntimeError(message)
    print(f"    HTTP {status}")
    return body


@contextmanager
def serving(config_dir: Path) -> Generator[None]:
    """Run the real server for the duration of the block."""
    server = subprocess.Popen(  # noqa: S603  # fixed program and args
        [*SF_AGG, "serve"], env={**os.environ, "SF_AGG_DIR": str(config_dir)}
    )
    try:
        # Poll until the server is up and healthy
        status: int | None = None
        for _ in range(50):
            if server.poll() is not None:
                # Including when something else took PORT after `_free_port` chose it.
                exited = "the server exited before it started listening"
                raise RuntimeError(exited)
            try:
                status, _body = sfa_request(f"{BASE_URL}/simplefin/info")
            except httpx2.TransportError:
                status = None
            if status == HTTPStatus.OK:
                break
            time.sleep(0.2)
        else:
            never_listened = f"the server never answered /simplefin/info with 200 (last: {status})"
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


def run(fakes: tuple[RunningFake, RunningFake], config_dir: Path) -> None:
    config_file = config_dir / "config.toml"
    _ = config_file.write_text(config((fakes[0].origin, fakes[1].origin)))
    config_file.chmod(0o600)

    for key, fake, user in zip((PREFIXED_KEY, BLANK_KEY), fakes, USERS, strict=True):
        print(f"==> Exchanging a provider setup token as {key}")
        # On stdin, which `claim` prompts for, rather than as an argument.
        _ = aggregator("claim", key, config_dir=config_dir, stdin=f"{fake.setup_token(user)}\n")

    print("==> Adding a client for this run")
    setup_token = aggregator("client", "add", CLIENT_KEY, config_dir=config_dir)
    claim_url = base64.b64decode(setup_token).decode("ascii")

    with serving(config_dir):
        print("==> Exchanging its setup token, as a client app would")
        access_url = expect(sfa_request(claim_url, method="POST"), HTTPStatus.OK, "the exchange")
        credentials = urlsplit(access_url)
        auth = (unquote(credentials.username or ""), unquote(credentials.password or ""))
        print("    access URL received")

        print("==> Exchanging it a second time (expect 403)")
        _ = expect(
            sfa_request(claim_url, method="POST"), HTTPStatus.FORBIDDEN, "the repeated exchange"
        )

        print("==> GET /simplefin/accounts")
        accounts = expect(
            sfa_request(f"{BASE_URL}/simplefin/accounts", auth=auth),
            HTTPStatus.OK,
            "/simplefin/accounts",
        )
        print(f"    {accounts}")
        # sf-agg responds 200 for an unreachable provider, so check that each
        # provider contributed accounts.
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
            sfa_request(f"{BASE_URL}/simplefin/accounts?{query}", auth=auth),
            HTTPStatus.OK,
            "/simplefin/accounts naming accounts",
        )
        print(f"    {filtered}")
        if account_ids(filtered) != [prefixed[0], blank[0]]:
            message = "/simplefin/accounts did not answer with exactly the accounts named"
            raise RuntimeError(message)

        print("==> GET /simplefin/accounts with no credentials (expect 403)")
        _ = expect(
            sfa_request(f"{BASE_URL}/simplefin/accounts"),
            HTTPStatus.FORBIDDEN,
            "/simplefin/accounts with no credentials",
        )

        print("==> Revoking the client, then asking again (expect 403, with no restart)")
        _ = aggregator("client", "revoke", CLIENT_KEY, config_dir=config_dir)
        _ = expect(
            sfa_request(f"{BASE_URL}/simplefin/accounts", auth=auth),
            HTTPStatus.FORBIDDEN,
            "/simplefin/accounts after revoking",
        )

    print("==> Done.")


def main() -> None:
    with (
        sf_server_fake([USERS[0]], ACCOUNTS) as one,
        sf_server_fake([USERS[1]], ACCOUNTS) as two,
        tempfile.TemporaryDirectory() as name,
    ):
        run((one, two), Path(name))


if __name__ == "__main__":
    main()
