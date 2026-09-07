# simplefin-aggregator

A server that implements the [SimpleFIN Bridge
protocol (version 1)](https://www.simplefin.org/protocol-v1.html) by aggregating the
data from one or more SimpleFIN providers. It is intended for use by a
personal finance app -- Actual Budget is the motivating example, but
any personal finance app that supports SimpleFIN will work.

It fans out to the providers you configure and merges their responses,
passing each provider's data through unchanged.

It is designed to run on `127.0.0.1` only, over plain HTTP, with no TLS and no
exposure to any network beyond the loopback interface.

## Setup

Requires Python 3.12.4+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
```

### 1. Write a config file

Copy `config.example.toml` and fill in your own choice of client app
credentials and claim token. **This comes first: `claim` reads the config, so
it has to exist and be valid before you can claim anything.**

```sh
mkdir -p -m 700 ~/.config/simplefin-aggregator
cp -i config.example.toml ~/.config/simplefin-aggregator/config.toml
chmod 600 ~/.config/simplefin-aggregator/config.toml
$EDITOR ~/.config/simplefin-aggregator/config.toml
```

That path is where every subcommand looks by default on Linux; pass
`--config-dir /path/to/dir` to put it somewhere else. `provider_creds.json`
(below) lives in the same directory -- the two always travel together.

`[[providers]]` names the providers to aggregate, by key:

| Provider | `key` |
|---|---|
| SimpleFIN Bridge | `simplefin-bridge` |
| Lunch Flow | `lunchflow` |
| Redbark | `redbark` |

For example,
```toml
[[providers]]
key = "simplefin-bridge"

[[providers]]
key = "lunchflow"
```

`[[custom_providers]]` adds a provider the built-in list does not name —
a third party that isn't built in, or a bridge you run yourself:

```toml
[[custom_providers]]
key = "my-bridge"
label = "My Own Bridge"
root = "https://simplefin.example.com/simplefin"

[[providers]]
key = "my-bridge"
```

`key` is the name you refer to it by; it must match `[a-z0-9-]+` and
must be unique.  `root` (with `/` appended if it is not specified) is
the prefix of the provider's claim and access URLs. It must be
`https`, unless the host is a literal loopback IP address such as
`127.0.0.1` or `[::1]`.

Editing this file is deliberately the only way to add a provider, to
discourage phishing modes such as trusting a malicious provider with a
domain that is a homograph of a legitimate domain, or reflexively
saying "yes" to "trust this site?"

`claim_token` is a shared secret, not a public constant — generate a random
one rather than picking something memorable. It also appears literally in a
URL path segment (see below), so it must be non-empty and need no
URL-encoding there; `secrets.token_urlsafe` already produces exactly that
kind of string:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**`config.toml` contains credentials in plain text — the client app password
and the claim token — and should be readable only by its owner.**

### 2. Claim a SimpleFIN setup token

Get a one-time-use setup token from your provider (for example,
`https://beta-bridge.simplefin.org/simplefin/create`, or your own SimpleFIN
server).

```sh
uv run simplefin-aggregator claim [token]
```

`claim` asks which provider supplied the token, prompts for the token if you
did not give it on the command line, uses it to obtain an access URL, and
stores that in `provider_creds.json` in the config directory.

**`provider_creds.json` cannot be regenerated.** A setup token is
one-time-use, so replacing a lost access URL means getting a fresh token from
the provider. Back it up, and keep it readable only by you.

Note: if you change the key of a custom provider, its stored access URL is
orphaned until you edit the matching entry in `provider_creds.json` to use the
new key.

### 3. Run the server

```sh
uv run simplefin-aggregator serve [--config-dir <dir>]
```

This starts serving on `bind_host:bind_port` (default `127.0.0.1:8080`).

### 4. Point your app at it

Generate a setup token that your app will use:

```sh
uv run simplefin-aggregator gen-token
```

This reads the config and prints a base64-encoded setup token — the same
shape a real SimpleFIN provider hands out — that decodes to this aggregator's
own claim URL, `<base_url>/simplefin/claim/<claim_token>`.

In Actual Budget (or any other SimpleFIN client), when it asks for a SimpleFIN
setup token, give it that string. The app will decode it, POST to the claim
URL, and receive back this aggregator's access URL — built from your
configured app `username`/`password` and `base_url` — which it then uses for
all subsequent `/accounts` requests.

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
  - `GET /simplefin/info` — proxies the provider's response; no auth required.

A provider error (any non-2xx) is passed through with the same status and
body. A provider that's unreachable (DNS failure, connection refused, timeout)
produces a `502` with a JSON body shaped like a SimpleFIN error response, and
so does a provider that answers with a redirect: redirects are never followed,
on the claim POST or on `/accounts`.

## Docker

The image contains only the application. Mount your config directory at
`/config`, the path the image's default command reads from.

**Set `bind_host = "0.0.0.0"` in your config first.** Left at `127.0.0.1` the
server binds the container's own loopback and the published port reaches
nothing. Publishing to `127.0.0.1`, as below, keeps it off the network.

```sh
docker build -t simplefin-aggregator .
docker run --rm \
  -p 127.0.0.1:8080:8080 \
  -v "$HOME/.config/simplefin-aggregator:/config:ro" \
  --user "$(id -u):$(id -g)" \
  simplefin-aggregator
```

`--user` keeps the container reading those files as you, so their owner-only
permissions work the same as outside Docker.

To claim from inside the container instead of on the host, drop the `:ro` so
the credentials it writes survive, and override the command:

```sh
docker run --rm -it \
  -v "$HOME/.config/simplefin-aggregator:/config" \
  --user "$(id -u):$(id -g)" \
  simplefin-aggregator claim --config-dir /config
```

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
