# simplefin-aggregator

A server that implements the [SimpleFIN Bridge
protocol (version 1)](https://www.simplefin.org/protocol-v1.html) by aggregating the
data from one or more SimpleFIN providers. It is intended for use by a
personal finance app -- Actual Budget is the motivating example, but
any personal finance app that supports SimpleFIN will work.

It fans out to the providers you configure and merges their responses,
passing each provider's data through unchanged.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync
```

### 1. Write a config file

Copy `config.toml` to the `~/.config/simplefin-aggregator/` directory
(default) or a location of your choice and edit it. **This comes
first:** `claim`, `app new` and `serve` all access the config, so it
has to exist and be valid before you can do anything else.

```sh
mkdir -p ~/.config/simplefin-aggregator
chmod 700 ~/.config/simplefin-aggregator
cp -i config.toml ~/.config/simplefin-aggregator/
chmod 600 ~/.config/simplefin-aggregator/config.toml
$EDITOR ~/.config/simplefin-aggregator/config.toml
```

Pass `--config-dir /path/to/dir` to invocations of
simplefin-aggregator if you did not use the default directory. The
`config.toml` in this repository is a template to copy: edit the copy in your
config directory, since the one here is a tracked file that `git` will happily
revert or commit.

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

`base_url` is the URL your client app will reach this aggregator at. Every
setup token you issue embeds it, so it has to be ASCII: give an
internationalized host in its encoded form.

**`config.toml` holds no credentials.** Keep it readable only by its owner
anyway — anyone who can write it can move `bind_host` off the loopback
interface. The same goes double for the directory it sits in: a directory
another local user can write lets them replace the files this application
depends on, and every command that writes there will warn you about it.

### 2. Claim a SimpleFIN setup token

Get a one-time-use setup token from your provider (for example,
`https://beta-bridge.simplefin.org/simplefin/create`, or your own SimpleFIN
server).

```sh
uv run simplefin-aggregator claim --provider <key>
```

`claim` prompts for the setup token, uses it to obtain an access URL, and
stores that in `provider_creds.json` in the config directory. It asks which
provider the token came from unless `--provider <key>` tells it. Naming the
wrong provider is an error rather than a guess: the access URL a provider
returns has to sit under the root that key names.

The prompt hides what you type. To run `claim` without a terminal, redirect a
file instead:

```sh
uv run simplefin-aggregator claim --provider <key> < token-file
```

**`provider_creds.json` cannot be regenerated.** A setup token is
one-time-use, so replacing a lost access URL means getting a fresh token from
the provider. Back it up, and keep it readable only by you.

Note: if you change the key of a custom provider, its stored access URL is
orphaned until you edit the matching entry in `provider_creds.json` to use the
new key.

### 3. Issue your client app a setup token

```sh
uv run simplefin-aggregator app new --key actual-budget --label "Actual Budget"
```

`--key` names the app in `app list` and `app revoke`, and must match
`[a-z0-9-]+`. The command prints a base64 setup token on stdout and nothing
else — the same shape a real SimpleFIN provider hands out — so `$(...)`
captures it cleanly.

**It is shown once.** The aggregator stores a SHA-256 digest of it and nothing
else, so it cannot be printed again; if you lose it before the app claims it,
run `app regen --key actual-budget` for a fresh one.

### 4. Run the server

```sh
uv run simplefin-aggregator serve [--config-dir <dir>]
```

This starts serving on `bind_host:bind_port` (default `127.0.0.1:8080`). It
refuses to start if `aggregator_creds.json` cannot be read or its directory
cannot be written, and warns if no app has been issued a token yet.

### 5. Point your app at it

In Actual Budget (or any other SimpleFIN client), when it asks for a SimpleFIN
setup token, give it the string from step 3. The app decodes it, POSTs to this
aggregator's claim URL, and receives back an access URL with a freshly
generated username and password embedded in it, which it uses for all
subsequent `/accounts` requests.

That claim spends the token. A second POST to the same URL gets a 403, exactly
as a token that was never issued does — the aggregator keeps no record that
could tell the two apart.

## Managing client apps

```sh
uv run simplefin-aggregator app list
uv run simplefin-aggregator app revoke --key <key>
uv run simplefin-aggregator app regen  --key <key>
```

`app list` shows each app's key, label, status and timestamps. It cannot show
you a credential, because the store holds none: everything in it is a digest,
verified against what an app presents and never reproduced.

`app revoke` removes an app. Its credentials stop working on the next request
— the server reads the store every time, so there is nothing to restart.

`app regen` issues an app a new setup token, keeping its key and label. Whatever
it held before — live credentials or an unclaimed token — stops working
immediately. This is the only way to reuse a key.

**`aggregator_creds.json` is disposable.** Losing it costs one fresh setup
token per client app, which is a different situation from
`provider_creds.json`. What matters for this file is that nobody else can
*write* it: reading it gains an attacker nothing, while writing it lets them
install a digest of a credential they chose.

## Endpoints

  - `POST /simplefin/claim/{token}` — spends a setup token once, returning an
    access URL. `403` if the token was never issued or has already been
    claimed; the two are deliberately indistinguishable.
  - `GET /simplefin/accounts` — requires HTTP Basic Auth with the credentials
    one claim issued; proxies to the configured provider, forwarding
    `start-date`, `end-date`, `pending`, `account` (repeatable),
    `balances-only`, and `version` verbatim.
  - `GET /simplefin/info` — answers `{"versions": ["1.0"]}`, the protocol
    version supported by this aggregator for its clients; no auth required.

A provider error (any non-2xx) is passed through with the same status and
body. A provider that's unreachable (DNS failure, connection refused, timeout)
produces a `502` with a JSON body shaped like a SimpleFIN error response, and
so does a provider that answers with a redirect: redirects are never followed,
on the claim POST or on `/accounts`.

## Docker

The image contains only the application. Mount your config directory at
`/config`, the path the image's default command reads and writes from.

**Set `bind_host = "0.0.0.0"` in your config first.** Left at `127.0.0.1` the
server binds the container's own loopback and the published port reaches
nothing. Publishing to `127.0.0.1`, as below, keeps it off the network.

```sh
docker build -t simplefin-aggregator .
docker run --rm \
  -p 127.0.0.1:8080:8080 \
  -v "$HOME/.config/simplefin-aggregator:/config" \
  --user "$(id -u):$(id -g)" \
  simplefin-aggregator
```

`--user` keeps the container reading and writing those files as you, so their
owner-only permissions work the same as outside Docker.

To claim a provider's token from inside the container instead of on the host,
override the command:

```sh
docker run --rm -it \
  -v "$HOME/.config/simplefin-aggregator:/config" \
  --user "$(id -u):$(id -g)" \
  simplefin-aggregator claim --config-dir /config
```

## Limitations

**POSIX only.** State files are locked with `fcntl.flock` and permissions are
checked as Unix mode bits. There is no Windows fallback.

**`base_url` is a literal loopback address, or HTTPS.** `http://127.0.0.1:8080`
and `http://[::1]:8080` are accepted, because that traffic cannot leave the
machine. Everything else must be `https://` — `localhost` included, which is a
name rather than an address and is rejected over http for that reason.

**This server does not terminate TLS.** An `https://` base URL means a reverse
proxy in front of it, holding the certificate the client app has to accept.
Setting one up is yours to do.

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

`scripts/manual_verify.py` is a separate, human-run smoke test against the
**real** SimpleFIN demo bridge (not part of `pytest`, and not run in CI). It
claims a demo setup token, issues itself a setup token, starts the real
server, claims that token the way a client app would, and uses the credentials
it gets back to query `/simplefin/info` and `/simplefin/accounts` end to end.

Get a fresh demo setup token from
[the SimpleFIN developer guide](https://beta-bridge.simplefin.org/info/developers)
— that page mints a new one on every load, so don't reuse an old one from
memory or from these docs — then run:

```sh
uv run scripts/manual_verify.py <demo-setup-token>
```

It is safe to put the demo token on the command line, since it only has the
potential to leak demo data.
