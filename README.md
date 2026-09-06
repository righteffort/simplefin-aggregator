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

### 1. Write a config file

Copy `config.example.toml` and fill in your own choice of client app
credentials and claim token. **This comes first: `claim` reads the config, so
it has to exist and be valid before you can claim anything.**

```sh
cp config.example.toml config.toml
chmod 600 config.toml
$EDITOR config.toml
```

`[[providers]]` names the provider this aggregator proxies, by its
`provider_key` — the slug of one of the built-in providers or a custom
provider from `[[allowlist]]` (below):

| Provider | `provider_key` |
|---|---|
| SimpleFIN Bridge | `simplefin-bridge` |
| Lunch Flow | `lunchflow` |
| Redbark | `redbark` |

For example,
```toml
[[providers]]
provider_key = simplefin-bridge

[[providers]]
provider_key = lunchflow
```

`[[allowlist]]` lists additional available providers, such as
a third party that isn't built in, or a bridge you run yourself. Add an 

entry for it
and point the `[[providers]]` entry at its slug instead of a built-in one:

```toml
[[allowlist]]
label = "My Own Bridge"
slug = "my-bridge"
root = "https://simplefin.example.com/simplefin"

[[providers]]
provider_key = "my-bridge"
```

`slug` is the name you refer to it by; it must match `[a-z0-9-]+` (so
no underscores, capitals or spaces) and must not be one a built-in
provider already uses. `root` (with `/` appended if it is not
specified) is the prefix of the provider's claim and access URLs. It
must be `https`, unless the host is a literal loopback IP address such
as `127.0.0.1` or `[::1]`.

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

By default, `simplefin-aggregator` looks for the config file in the 
platform config directory (typically `~/.config/simplefin-aggregator/config.toml`
on Linux). Pass `--config /path/to/config.toml` to any subcommand to use a
different location.

### 2. Claim a SimpleFIN setup token

Get a one-time-use setup token from your provider (for example,
`https://beta-bridge.simplefin.org/simplefin/create`, or your own SimpleFIN
server).

```sh
uv run simplefin-aggregator claim [token]
```

`claim` asks which provider supplied the token, prompts for the token
if you did not specify it on the command line, and uses it to obtain
an access URL, and stores it in either `access_urls.json` in the
platform config directory or in the location specified by the the
`--access_urls_file` command line argument.

Note: if you ever change the key for a custom provider, the access URL
for the provider will be orphaned unless you manually edit the entry
in `access_urls.json` to use the new key.

### 3. Run the server

```sh
uv run simplefin-aggregator serve [--config <config_toml>] [--access_urls_file <acccess_urls_json>]
```

This starts serving on `bind_host:bind_port` (default `127.0.0.1:8080`).

### 4. Point your app at it

Generate a setup token that your app will use:

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

The image contains only the application — no `config.toml`, and none of your
claimed access URLs. Build it once, then supply both at container-run time.

**Set `bind_host = "0.0.0.0"` in your config first.** Left at the default
`127.0.0.1`, the server binds the container's own loopback interface, and the
published port reaches nothing. This is safe as long as the port is published
to `127.0.0.1` as below — the container's network is the boundary then, not
the bind address.

**`serve` also needs the access URLs `claim` stored, so the cache directory
has to be mounted in as well as the config file.** Claim on the host first
(step 2 above), then:

```sh
docker build -t simplefin-aggregator .
docker run --rm \
  -p 127.0.0.1:8080:8080 \
  -v "$(pwd)/config.toml:/config/config.toml:ro" \
  -v "$HOME/.cache/simplefin-aggregator:/cache:ro" \
  --user "$(id -u):$(id -g)" \
  simplefin-aggregator
```

`/config/config.toml` and `/cache` are the paths the image's default command
reads from; change the left-hand side of either `-v` to wherever yours live.

Without that second mount the container starts with an empty store and `serve`
exits with `no access URL stored for provider ...` — the fix is the mount, not
another claim, since re-claiming needs a fresh setup token. Both mounts are
`:ro`: `serve` only reads them, and `claim` is the only thing that writes.

`--user "$(id -u):$(id -g)"` keeps the container reading these as your own
user, so the owner-only permission checks (see [step
1](#1-write-a-config-file) above) behave the same way they do outside
Docker — files mounted in from the host keep the host's permissions and
ownership. Publish the port to `127.0.0.1` only, per this project's
loopback-only design — never to `0.0.0.0`.

The container-side paths must stay as-is unless you also override the
container's command to pass a different `--config` or `--cachedir`.

You can also run `claim` in the container rather than on the host, as long as
the cache mount is writable (drop its `:ro`), so that what it claims survives
the container exiting:

```sh
mkdir -p "$HOME/.cache/simplefin-aggregator"
docker run --rm -it \
  -v "$(pwd)/config.toml:/config/config.toml:ro" \
  -v "$HOME/.cache/simplefin-aggregator:/cache" \
  --user "$(id -u):$(id -g)" \
  simplefin-aggregator claim --config /config/config.toml --cachedir /cache
```

The `mkdir` matters: Docker creates a missing bind-mount source as root, and
`claim` running as you would then fail with `/cache is not writable`.

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
