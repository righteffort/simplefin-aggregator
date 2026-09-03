# simplefin-aggregator

A server that implements the [SimpleFIN Bridge
protocol](https://www.simplefin.org/protocol.html) by aggregating the
data from or more SimpleFIN providers. It is intended for use by a
personal finance app -- Actual Budget is the motivating example, but
any personal finance app that supports SimpleFIN will work.

This version is an identity function: exactly one
provider, everything passed through unchanged, byte for byte. A later
version will fan out to several providers and merge their responses.

It is designed to run on `127.0.0.1` only, over plain HTTP, with no TLS and no
exposure to any network beyond the loopback interface.

## Setup

Requires Python 3.12.4+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
```

### 1. Claim a SimpleFIN setup token

Get a setup token from your SimpleFIN provider (for example,
`https://bridge.simplefin.org/simplefin/create`, or your bank's own SimpleFIN
server). It's a base64-encoded, one-time-use string.

```sh
uv run simplefin-aggregator claim <setup-token>
```

This decodes the token, POSTs to the provider's claim URL, and prints the
resulting **access URL** to stdout — a URL of the form
`https://user:pass@host/path` that is this aggregator's actual credential for
talking to that provider. Nothing else about `claim` has side effects.

**The setup token is one-time-use.** Once claimed, it cannot be claimed again,
and the access URL `claim` prints cannot be regenerated — if you lose it,
you'll need a fresh setup token from the provider. `claim` reminds you of this
on stderr every time.

### 2. Write a config file

Copy `config.example.toml` and fill in the access URL from step 1, plus your
own choice of app credentials and claim token:

```sh
cp config.example.toml config.toml
chmod 600 config.toml
$EDITOR config.toml
```

`claim_token` is a shared secret, not a public constant — generate a random
one rather than picking something memorable. It also appears literally in a
URL path segment (see below), so it must be non-empty and need no
URL-encoding there; `secrets.token_urlsafe` already produces exactly that
kind of string:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**`config.toml` contains credentials in plain text — provider access
URLs, the app password, the claim token — and must be readable
only by its owner.**  Run `chmod 600 config.toml` and keep it that
way. `simplefin-aggregator` warns if it finds the file's permissions
are anything looser than that.

By default, `simplefin-aggregator` looks for the config file at your
platform's standard config directory (e.g. `~/.config/simplefin-aggregator/config.toml`
on Linux). Pass `--config /path/to/config.toml` to `serve` to use a different
location.

### 3. Run the server

```sh
uv run simplefin-aggregator serve --config config.toml
```

This reads the config and starts serving on `bind_host:bind_port`
(default `127.0.0.1:8080`). If your access URL or other configuration
is wrong, fix it and restart.

### 4. Point your app at it

Generate a setup token for the app to use:

```sh
uv run simplefin-aggregator gen-token --config config.toml
```

This reads the config and prints a base64-encoded setup token — the same
shape a real SimpleFIN provider hands out — that decodes to this aggregator's
own claim URL, `<base_url>/simplefin/claim/<claim_token>`.

In Actual Budget (or any other SimpleFIN client), when it asks for a SimpleFIN
setup token, give it that string. The app will decode it, POST to the claim
URL, and receive back this aggregator's access URL — built from your
configured app `username`/`password` and `base_url` — which it then uses for
all subsequent `/accounts` and `/info` requests.

## The repeatable-claim deviation

The SimpleFIN protocol specifies that a claim token is one-time-use: once
claimed, a second `POST` to the same claim URL should return 403, on the
theory that a second claim attempt indicates the token leaked to someone else.

This aggregator's `POST /simplefin/claim/{token}` **does not** enforce
that.  The token is a constant from config, and is accepted every time
it's presented, returning the same access URL. This is an expedient
deviation: On loopback, with no network exposure, the threat the
one-time-use rule defends against (an eavesdropper stealing the setup
token in transit) doesn't exist here — nothing outside this machine
ever sees the token.

To revoke access, rotate `claim_token` in the config and restart the
server.

## Endpoints

  - `POST /simplefin/claim/{token}` — see above.
  - `GET /simplefin/accounts` — requires HTTP Basic Auth (the configured
    app credentials); proxies to the configured provider, forwarding
    `start-date`, `end-date`, `pending`, `account` (repeatable),
    `balances-only`, and `version` verbatim.
  - `GET /simplefin/info` — proxies the provider's response; no auth required,
    matching upstream SimpleFIN behavior.

A provider error (any non-2xx) is passed through with the same status and
body. A provider that's unreachable (DNS failure, connection refused, timeout)
produces a `502` with a JSON body shaped like a SimpleFIN error response.

## Docker

The image contains only the application — it does not include `config.toml`.
Build it once, then supply your own config file at container-run time by
bind-mounting it to `/config/config.toml`, the path the image's default
command reads from:

```sh
docker build -t simplefin-aggregator .
docker run --rm \
  -p 127.0.0.1:8080:8080 \
  -v "$(pwd)/config.toml:/config/config.toml:ro" \
  --user "$(id -u):$(id -g)" \
  simplefin-aggregator
```

Mounting `:ro` keeps the container from ever writing to your config file.
`--user "$(id -u):$(id -g)"` keeps the container reading it as your own user,
so the owner-only permission check (see [step
2](#2-write-a-config-file) above) behaves the same way it does outside
Docker — a config file mounted in from the host keeps the host's file
permissions and ownership. Publish the port to `127.0.0.1` only, per this
project's loopback-only design — never to `0.0.0.0`.

If your config lives somewhere other than the current directory, change the
left-hand side of the `-v` flag to that path; the container-side path
(`/config/config.toml`) must stay as-is unless you also override the
container's command to pass a different `--config`.

## Development

```sh
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run basedpyright
```

The test suite fakes providers with `httpx2.MockTransport` — it never makes a
real network call.

### Manual verification against the real SimpleFIN demo bridge

`scripts/manual_verify.sh` is a separate, human-run smoke test against the
**real** SimpleFIN demo bridge (not part of `pytest`, and not run in CI). It
claims a demo setup token, starts the real server, and queries
`/simplefin/info` and `/simplefin/accounts` through it end to end.

Get a fresh demo setup token from
[the SimpleFIN developer guide](https://beta-bridge.simplefin.org/info/developers)
— that page mints a new one on every load, so don't reuse an old one from
memory or from these docs — then run:

```sh
./scripts/manual_verify.sh <demo-setup-token>
```
