# Architecture

This is a developer/agent-facing map of `simplefin-aggregator`. `README.md`
covers how to use it; `AGENTS.md` covers how to work on it.

It is a map, not a second copy of the code: it says how the pieces fit, which
invariants hold across them, and which are load-bearing enough that changing
one breaks something elsewhere. Where the reason for a single module's design
is already in that module's docstring, this file points at it rather than
restating it.

## Purpose

A server that speaks the SimpleFIN Bridge protocol to a client app
(Actual Budget is the motivating example, but it's generic) and proxies one or
more SimpleFIN providers behind it. `GET /simplefin/accounts` answers 200, or
403 for a client authentication failure, and nothing else: a provider's own
failure is reported in v1's `errors` array rather than in this server's status,
because 403 here is a statement about the client app's credentials and a
provider's is about a different pair of principals. `Config` still accepts
exactly one provider — the rest of the multi-provider version is what "Seams
for the multi-provider future" below describes.

The other half of the job is credential handling. A provider access URL embeds
Basic Auth credentials for the user's bank data, and it is obtained by pasting
in a base64 URL from a web page. Guarding that path — see "Provider URL
validation" and "Cross-cutting: secrets and logging" — shapes more of this
codebase than the proxying does.

## Module map

`src/simplefin_aggregator/`:

| File | Responsibility |
|---|---|
| `cli.py` | Typer entry points: `claim`, `serve`, and the `app` group (`new`, `list`, `revoke`, `regen`), each taking `--config-dir`. Wires everything else together. `main()` is the `console_scripts` target. |
| `config.py` | Pydantic models (`Config`, `Provider`, `CustomProvider`), `load_config()`, and where the config directory lives. All config validation lives here. Holds no credentials. |
| `state_file.py` | Everything this application does with a file it owns: the permission warning, the redacted validation-error rendering, the atomic 0600 write, the sidecar `flock`, and the locked read-modify-write. Imports nothing else in the package — `config.py` takes its warning and its error rendering from here, not the other way around. |
| `app_tokens.py` | The app token store: the two-state record, the digest helpers, and the constructors that mint a setup token and spend one for credentials. |
| `provider_registry.py` | `ProviderEntry` and `KNOWN_PROVIDERS`: the fixed set of providers a setup token may be claimed from, plus `merged_providers`/`find_provider`. Must not import `config.py` — config imports it. |
| `url_validation.py` | `NormalizedUrl`, `parse_url`/`parse_root`, `validate_claim_url`/`validate_access_url`, `UrlValidationError`. The phishing defense; read its module docstring before touching anything here. |
| `provider_access_urls.py` | The `provider_creds.json` store: provider key → access URL. Schema and semantics only; the file handling is `state_file.py`'s. |
| `app.py` | `create_app(config, access_urls, app_tokens_path) -> FastAPI`: the ASGI app factory, lifespan, and all three HTTP routes. |
| `auth.py` | `build_client_auth_dependency(store_path)`, the factory for the FastAPI dependency guarding `/simplefin/accounts`. |
| `access_url.py` | Builds the access URL this aggregator hands back from `POST /simplefin/claim/{token}` — the one it *issues*, not the ones it holds (that is `provider_access_urls.py`). |
| `setup_token.py` | Builds the base64 setup token `app new` prints (the inverse direction of `access_url.py`: this aggregator's *own* claim URL, encoded the way a real provider's would be). |
| `provider_clients.py` | `build_provider_client(access_url) -> httpx2.AsyncClient`: one long-lived client per provider, built once at startup. |
| `transport.py` | `fetch`/`fetch_all`: the concurrent, non-raising provider-request layer. |
| `provider_response.py` | `ProviderSuccess` / `ProviderFailure` / `ProviderResponse` — the uniform result type `fetch` always returns. |
| `merge.py` | `merge(results) -> MergedResponse`: several providers' responses concatenated into one v1 body, each account id behind its provider's prefix. |
| `provider_resolution.py` | `resolve_provider_for_account`: "which provider owns this account id" seam. Trivial today (always the sole provider). |
| `id_rewriting.py` | `rewrite_ids` / `unrewrite_ids`: no-op seams for future cross-provider id namespacing. |
| `request_counter.py` | `RequestCounter`: per-provider daily request counts, logged for observability only, never used as a control. |
| `access_log.py` | Generic uvicorn-access-log redaction utility. Knows nothing about SimpleFIN or claim tokens — `app.py`/`cli.py` supply what to redact. |

`tests/support.py` holds shared test helpers (`make_config`,
`make_access_urls`, `make_app`, `install_provider_transport`) used across most
test files.

## On-disk state

Three files, plus a lock sidecar per store, all in the directory
`--config-dir` selects for every subcommand, defaulting to
`platformdirs.user_config_dir("simplefin-aggregator")`:

| File | Written by | Holds |
|---|---|---|
| `config.toml` | the user, by hand | bind address, `base_url`, provider keys, `custom_providers` |
| `provider_creds.json` | `claim` | provider key → provider access URL |
| `aggregator_creds.json` | `app new`/`revoke`/`regen`, and the claim route | app key → an unclaimed or claimed app token record, digests only |

**The three want different things, and the difference is load-bearing.**

`provider_creds.json` is the most sensitive thing this application owns.
An access URL embeds Basic Auth credentials for the user's bank data, so
anything that can read this file can read that data.

`aggregator_creds.json` has no confidentiality concern at all, by
construction. Every value in it is a SHA-256 digest of a 256-bit random
secret, kept to recognise that secret and never to reproduce it, so reading
the file yields nothing an attacker can present to anything. That is what
makes "never display or log a credential" a property of the design rather
than a rule to follow. The file is disposable too: losing it costs one fresh
setup token per client app.

Its integrity does matter — a digest of the attacker's choosing, written into
it, authenticates them to `/accounts` — but only to someone who can write the
config directory, and this application warns about that rather than defending
against it.

`config.toml` holds no credentials. `bind_host` decides whether the server is
reachable from off this machine, and nothing enforces a choice there; the
README says what each one means.

So: modification matters for all three, disclosure only for the first.
`warn_if_permissive` (warn on stderr, never block) runs on all three and does
not say which risk applies to the file in hand — a flag on the check, or a
warning hedging about which case it is in, would be more machinery than the
asymmetry is worth. It runs on the config *directory* too, from
`check_can_save`: a directory another local user can write is the premise of
every attack the write path defends against.

It does not run on the `.lock` sidecars, which hold nothing.

The provider store is keyed by provider key because that is the one identifier
`config.toml` and the store share; neither file has to name the other.
Consequence: two accounts at the same provider would collide on one key. The
app token store is keyed by the `--key` its command was given, constrained to
`[a-z0-9-]+` at the command and again in the model, so what `app list` prints
is what this application could have written.

## Key data structures

### `Config` (`config.py`)

```text
Config
  bind_host: str = "127.0.0.1"
  bind_port: int = 8080
  providers: list[Provider]           # Field(min_length=1, max_length=1) -- exactly one, for now
  custom_providers: list[CustomProvider] = []
  base_url: str                       # validated: ASCII, http/https, has a host, no user-info,
                                      #   http only for a literal loopback IP
  .provider_entries() -> tuple[ProviderEntry, ...]   # KNOWN_PROVIDERS + custom_providers

Provider
  key: str                            # a provider key; resolves against provider_entries()

CustomProvider
  key: str
  label: str
  root: str                           # validated via parse_root()
```

`base_url` must be ASCII because a URL is: an internationalized host appears in
one as punycode, not as the characters it is spelled with. The provider path
gets that conversion free — `parse_root` reads `raw_host` off httpx2's parser,
which has already encoded it — while `base_url` goes through `urlsplit`, which
normalizes nothing and hands back whatever it was given. So the requirement
falls on the input, and it is checked at load time rather than where
`build_setup_token` would hit it, because `app new` writes its record before it
prints and a failure there would leave an app that could never be handed a
token.

A `Provider` is now just a reference: no `name`, and no `access_url` (that
moved to the store). `key` is the identifier everywhere — the store's key, the
`provider_clients` dict's key, and what appears in log lines.

Every `provider.key` is resolved against `provider_entries()` by a model-level
validator, so a dangling reference fails at config-load time for *every*
command rather than at first dereference — and a `custom_providers` root is
parsed during validation, so a broken one fails at `claim` time rather than
surviving to `serve`.

**A `Config` is read-only once `load_config` returns**, and there is no config
hot-reload — nothing mutates one, nothing reloads one, and `serve` reads
`config.toml` and `provider_creds.json` exactly once at startup. That is what
lets validators here establish invariants good for the object's whole lifetime;
a reload would cost that.

**`aggregator_creds.json` is live state: read afresh on every request that
authenticates, and written on every claim.** That is the whole of what makes
`app revoke` take effect without a restart. Do not add a cache, an mtime check
or a reload signal to it.

`providers` is deliberately still a `list` (not a single `Provider` field) even
though exactly one is enforced — that's the seam for the multi-provider
version.

**`load_config(path) -> Config`** reads TOML, warns if the file is
group/other-accessible, and validates via `Config.model_validate(dict)`. Its
error path never `str()`s the `ValidationError`, and renders it through
`state_file.py`'s `describe_validation_failure` — the same rendering the state
files get, for the same reason: a `custom_providers` root can carry userinfo.
See "Cross-cutting: secrets and logging". Don't bypass it by catching and
re-stringifying the raw `ValidationError` elsewhere.

### `CustomProvider` (`config.py`)

The model a user hand-edits into `custom_providers` for a provider the
built-in list does not name. Its `@field_validator("root")` runs
`parse_root()` and reports a bad root against that one field, rather than
leaving it to the model-level check to reject the whole `Config` — so the
error names the offending entry instead of, via pydantic's default
`ValidationError` rendering, the entire file. `as_provider_entry()` converts
one into a `ProviderEntry`, validating both `key` and `root` again in the
process; `provider_entries()` merges the result into `KNOWN_PROVIDERS`
through `merged_providers`, which rejects a duplicate key rather than letting
a custom entry shadow or collide with a built-in one.

### `UnclaimedAppToken` / `ClaimedAppToken` (`app_tokens.py`)

```text
UnclaimedAppToken                     # a setup token issued and not yet spent
  status: Literal["unclaimed"]
  label: str
  created_at: AwareDatetime
  claim_token_sha256: _Sha256Hex

ClaimedAppToken                       # what a claim exchanged it for
  status: Literal["claimed"]
  label: str
  created_at: AwareDatetime
  claimed_at: AwareDatetime
  username_sha256: _Sha256Hex
  password_sha256: _Sha256Hex

AppTokenRecord = Annotated[UnclaimedAppToken | ClaimedAppToken, Field(discriminator="status")]
```

An app token record is one client app's row in `aggregator_creds.json`, in one
of two states: `UnclaimedAppToken`, holding the digest of a setup token that
has not been spent, or `ClaimedAppToken`, holding the digests of the Basic
Auth credentials a claim exchanged it for. Neither variant literally holds a
token — the claimed one holds no token at all — but "app token" names the
record, not its payload, so the variant names track the `status` values
they discriminate on rather than what each one contains.

Two states and no third, narrowed by `isinstance` the way `ProviderSuccess |
ProviderFailure` is. **A claim replaces the unclaimed record rather than
marking it spent**, which is what makes a replayed setup token fail the same
lookup an unissued one fails — the protocol's indistinguishability requirement,
obtained by construction instead of defended by a branch. It is also why
`app regen` cannot leave an app holding live credentials and an unspent token
at once.

Nothing in these models is a `SecretStr`, because nothing in them is a secret.
`_Sha256Hex` and `AwareDatetime` exist for the same reason: a value this
application could not have written is rejected when the file is read, rather
than at the comparison or the subtraction it would later break.

`new_app_token` and `claim_app_token` are the only ways to build a record.
They mint the secret and return it once alongside the record that will
recognise it, which keeps the timestamp and the digesting out of `cli.py` and
the claim route — the two places where assembling a record by hand would put
a plaintext credential on disk.

### `ProviderEntry` (`provider_registry.py`)

```text
ProviderEntry(key: str, label: str, root: NormalizedUrl)   # frozen
```

`key` is constrained to `[a-z0-9-]+` in `__post_init__`, through which every
entry passes, config-supplied ones included — that is what makes a key safe to
render in an error message and to use as a store key. **Published keys are
permanent**: changing one orphans users' stored access URLs and breaks their
config references.

`merged_providers` rejects a duplicate key rather than letting a config entry
override a built-in one, and `find_provider` raises on an absent key — never
guessed at, never fuzzy-matched. Adding an entry is a config-file edit and
nothing else: no flag, no interactive "trust this origin?" prompt. See
"Provider URL validation".

### `NormalizedUrl` (`url_validation.py`)

```text
NormalizedUrl                        # frozen; produced only by parse_url/parse_root
  scheme, host, port, username, password
  origin: str                        # scheme://host[:port]      -- what a message names
  origin_and_path: str               # scheme://host[:port]path  -- what matching compares
  .has_creds -> bool
```

Per-field normalization is documented on the dataclass. What matters outside
`url_validation.py` is that neither `origin` nor `origin_and_path` carries
credentials, and three rules that are easy to break by accident:

- **Parse once per operation.** Read every field an operation needs off one
  `NormalizedUrl`; never re-parse the source string alongside it, and never
  derive a field by a separate string operation. Two parses of a string that
  was normalized in between silently check different inputs. This applies to
  request-building as much as to validation.
- **`origin_and_path` is not a URL to fetch.** It has no credentials, and a
  root's has a trailing `/` the configured string need not have had. The single
  exception is `build_provider_client`'s `base_url`, which httpx2 joins request
  paths onto rather than fetching as given, with credentials supplied
  separately via `auth=`. There is a comment there saying so.
- **Messages name a URL only through `origin` (or a root's
  `origin_and_path`), never the raw string.** See "Cross-cutting: secrets and
  logging".

### `ProviderResponse` (`provider_response.py`)

```text
ProviderResponse = ProviderSuccess | ProviderFailure

ProviderSuccess(provider_name, status: int, body: bytes)
  .ok -> True

ProviderFailure(provider_name, error: str)   # a class name or this application's own words,
  .ok -> False                               #   never a provider's text
```

This is the uniform type `fetch()` always returns — it never raises. A
discriminated union of two frozen dataclasses, narrowed via `isinstance` (see
`merge.py` for the `isinstance(response, ProviderFailure)` pattern).

### `MergedResponse` (`merge.py`)

```python
MergedResponse(body: bytes)
```

The output of `merge(results: Sequence[tuple[str, ProviderResponse]])`, which
pairs each provider's account-id prefix with its response. One field, because
the route supplies the other two itself: the status is always 200 and the body
is always JSON this application built. `merge.py`'s docstrings hold the rest —
the ordering rule, what makes a response usable, and what a collision costs.

### `_AppState` (`app.py`, private)

```python
_AppState(provider_clients: dict[str, httpx2.AsyncClient], request_counter: RequestCounter)
```

The **one** thing hung off FastAPI/Starlette's `app.state` (which is an
untyped attribute bag — `Any` all the way down). Built once in the lifespan
context manager, read via `_get_app_state(request)` which does the one
`cast(_AppState, request.app.state.app_state)` for the whole app. Adding a
new piece of request-scoped shared state means adding a field here, not a new
`app.state.whatever`.

It is the only one, and the way to keep it that way is that a dependency
needing something from `create_app` takes it as a factory argument:
`build_client_auth_dependency(store_path)` closes over the path, so `auth.py`
reads no attribute and casts nothing, even though it lives in another module
and has no closure over `create_app`'s locals.

## Provider URL validation

`url_validation.py`'s module docstring holds the threat model and the rules;
read it before changing anything there. What it means for the rest of the
codebase:

- **The threat is phishing and paste error, not SSRF.** Exact matching against
  a fixed set of known-good roots is the only control that works, since a user
  asked to confirm a host in the phishing case confirms the attacker's. That is
  why `KNOWN_PROVIDERS` is fixed, why extending it is a config edit and nothing
  else, and why a mismatch is a hard failure with no prompt and no `--force`.
- **Deliberately not implemented**, and previously rejected in review: IP-range
  blocking, DNS pre-resolution/pinning or other SSRF defenses (single
  principal, no confused deputy — and TLS binds identity to the hostname, so
  the resolved address is irrelevant); dereferencing an account's `currency`
  URL; credential encryption at rest beyond file permissions.
- **Matching is one prefix comparison** of `origin_and_path`, which begins with
  the scheme and host and so covers scheme, host, port and path prefix at once.
  **Do not add separate scheme/host/port checks anywhere** — they would be
  redundant and could drift.
- **Two gaps are left open deliberately**: a provider root's path is rendered
  in full in messages, so a config-supplied root carrying a capability token in
  its path would print it on every mismatch — the "a root is configuration, not
  a secret" policy working as intended; and a URL malformed enough that the
  parse misreads where its credentials end can put a fragment of one in
  `origin`. `UrlValidationError`'s docstring states both, and the tests pinning
  them say they pin behavior rather than a requirement. Both are accepted in
  preference to the checks that would close them.

## Request/command flows

### `serve [--config-dir DIR]`

```text
cli.serve
  -> _load_config_or_exit(config_path(dir))   # load_config, or print+exit 1
  -> _check_app_store_or_exit(dir)            # store parses, directory writable
                                              #   empty store -> warn, not fail
  -> load_access_urls(provider_creds_path(dir))
  -> create_app(config, access_urls, app_tokens_path(dir))
                                              # validates every stored access URL, see below
  -> install_access_log_redaction(...)        # generic filter, told about CLAIM_PATH_PREFIX
  -> uvicorn.run(app, host, port)
       -> lifespan startup: build_provider_client() per provider -> _AppState on app.state
       -> ... serves requests ...
       -> lifespan shutdown: aclose() every provider client
```

`create_app` raises rather than starting a server that cannot work: for each
configured `provider.key` it resolves the entry, looks the key up in the store
(missing → "claim one first"), and re-runs `validate_access_url` against that
provider's *current* root. That is a single-entry comparison, not a scan — the
URL was claimed from one specific provider, so that is the root it must still
match. All of it happens before uvicorn starts, so a config change that
invalidates a stored URL fails at startup, not on the first request.

The app token store is checked at startup but **not read into the app**: the
server reads it on every authenticated request and writes it on every claim, so
what `serve` establishes is that the file parses and its directory can be
written — a store it cannot parse would answer 403 to everything and a
directory it cannot write would lose every claim. An empty store is a warning
rather than a failure: it is the legitimate state between installing the server
and issuing the first app its token.

### CLI `claim [--provider KEY] [--config-dir DIR]` (claiming from a *real* provider)

```text
cli.claim
  -> _load_config_or_exit(...)                # claim needs a fully valid config, like serve
  -> load_access_urls(...) + check_can_save(...)   # BEFORE the token is spent
  -> _resolve_provider(config.provider_entries(), provider)
       --provider given -> _check_key + find_provider    # named, so exact; never fuzzy
       otherwise        -> numbered menu; no default, no free-text host
  -> prompt for the token, hidden if stdin is a real terminal
  -> _decode_setup_token  -> validate_claim_url(entry.root, ...)   # BEFORE any network call
  -> POST claim_url.origin_and_path, redirects disabled
       - 403      -> "may be compromised, revoke it at the provider"
       - non-200  -> status only, never the body (3xx lands here too)
  -> validate_access_url(entry.root, response.text.strip(), ...)
  -> save_access_url(store, entry.key, access_url)
  -> warn if no [[providers]] entry names this key
```

One property and four orderings in there are load-bearing.

The property: **the provider is named or chosen, never defaulted.** Which root
the token is matched against is the whole of the phishing defense, so it is the
user's deliberate answer either way — `--provider` on the command line is as
deliberate as picking from the menu, and an unknown key is an error rather than
a guess. A `--provider` failing the key pattern is rejected without being
echoed, for the reason `--key` is: a mistyped one is most often a pasted setup
token.

The orderings:

1. **The provider is settled before the token is read.** Otherwise the root a
   token is matched against could be inferred from the token, which is the one
   thing that must not decide it.
2. **The store is read and its directory checked before the POST.** The token
   is one-time-use, so a file problem discovered afterwards is a lost
   credential rather than a retry.
3. **Validation comes before the POST.** A hostile host you contact has
   already learned your egress IP and that the token is live, however you treat
   its reply.
4. **The response is stripped, then validated, then stored** — never printed.

A claim for a provider the config's `[[providers]]` does not name still
succeeds and is stored — it just warns, since `serve` would otherwise report it
as unclaimed later.

`_build_claim_client` is a seam purely for test injection (see Testing below)
— not a general dependency-injection pattern used elsewhere in this codebase.
This sync `httpx2.Client` is the one legitimate non-async HTTP call in the
project.

### CLI `app new` / `regen` / `revoke` (obtaining/revoking *this aggregator's* tokens)

```text
cli.app_new(key, label, config_dir)
  -> _load_config_or_exit(...)                  # for base_url
  -> _check_key(key)                            # rejected keys are never named back
  -> _writable_store_or_exit(config_dir)        # directory writable, and warn if shared
  -> update_app_tokens(store):                  # one locked read-modify-write
       key already present -> exit 1, naming `app regen`, store untouched
       else -> new_app_token(label) -> (secret, UnclaimedAppToken)
  -> build_setup_token(base_url, secret) -> stdout, alone
  -> "shown once" note -> stderr
```

Three orderings here are load-bearing. The duplicate check is *inside* the
lock, or it answers from a version another writer is already replacing. The
token is printed *after* the store is written, because a token this aggregator
has no record of looks to the user like a working setup that never syncs. And
a rejected `--key` is not echoed, because a mistyped one is most often a
pasted setup token and base64 is exactly what `[a-z0-9-]+` rejects.

`app regen` is the same flow over an existing record, keeping only the label.
`app revoke` deletes the record outright. Neither leaves an app holding a live
credential and an unspent token at once, which is what would make "revoked"
mean two things.

### `POST /simplefin/claim/{token}`

```text
app.claim(token)
  -> run_in_threadpool(_spend_setup_token, store_path, token)   # no file I/O on the event loop
       update_app_tokens(store):                                 # one locked read-modify-write
         find the UnclaimedAppToken whose digest matches (compare_digest)
         none -> raise _UnknownSetupTokenError, out of the update, nothing written
         else -> claim_app_token(record) -> (credentials, ClaimedAppToken); replaces the record
  -> _UnknownSetupTokenError -> 403 "unknown claim token"
  -> StateFileError          -> 500, and no access URL
  -> else: build_access_url(base_url, credentials) -> 200 text/plain, no trailing newline
```

**Persist, then respond.** Crashing after the write costs a setup token the
operator replaces with `app regen`; crashing after the response leaves the
client app holding credentials this server does not recognise. The
write-then-rename and its `fsync` are what make "persisted" mean survived a
power cut, not merely reached the page cache — which is why the write stays in
the request path.

**A replayed token and one that was never issued are answered identically, by
construction rather than by a branch.** The claim replaces the record it spent,
so there is nothing left that answers to a spent token; both fail the same
lookup and there is no code that could tell them apart.

### `GET /simplefin/accounts`

```text
app.accounts(request)
  -> require_client_auth (dependency built over the store path by
     build_client_auth_dependency; reads aggregator_creds.json in a threadpool on
     every request, 403 on missing, unknown or unreadable; 403 also on a
     record that has not claimed, which holds a token and not credentials)
  -> _get_app_state(request) -> provider_clients, request_counter
  -> _forwarded_accounts_params(request)
       - keep only the six spec'd query keys (ACCOUNTS_FORWARDED_PARAMS)
       - extract "account" values, run through unrewrite_ids() (no-op today),
         rebuild the param list with them substituted back in
  -> if any "account" values: resolve_provider_for_account() per id, dedupe by
     provider key -> providers_to_query (today: always the sole provider)
     else: providers_to_query = all configured providers
  -> fetch_all(clients, providers_to_query, "/accounts", params, counter)
       -> asyncio.gather over fetch() per provider, order preserved
       -> fetch() never raises: httpx2.HTTPError -> ProviderFailure
       -> 3xx                                    -> ProviderFailure
  -> merge([("", response) for response in responses]) -> MergedResponse
  -> rewrite_ids(merged.body)   # no-op today
  -> Response(body, 200, "application/json")
```

The empty prefix is right while `Config` accepts one provider: a lone provider
needs no namespace, and giving it one would re-identify every account the
client app already holds.

`GET /simplefin/info` shares none of this. It answers `{"versions": ["1.0"]}`
locally, contacting no provider: the version is a fact about the protocol this
server speaks to its client app, and the route is unauthenticated, so proxying
it would turn one anonymous request into one request per provider against the
budgets `RequestCounter` exists to watch.

**Redirects are never followed** — not here, and not on the claim POST; the
`follow_redirects=False` is set explicitly in both clients even though it is
httpx2's default, so a refactor cannot silently flip it. The spec defines
`/accounts` as returning only 200, 402 or 403, so there is no legitimate
redirect, and a `requests`-style silent POST→GET conversion on a 302 is exactly
what this prevents. With redirects disabled httpx2 *returns* the 3xx rather
than raising, so `transport.fetch` has to reject it explicitly or it would pass
through as a `ProviderSuccess`. A 3xx reaches the client app as that provider
contributing nothing, the same as any other way of failing.

## Concurrency model

- Everything provider-facing is `httpx2.AsyncClient` / `async def`. No sync
  client anywhere in the request path (the CLI's `claim` command is the one
  legitimate exception — it's a one-shot utility outside any request path).
- One `AsyncClient` per provider, built once in the lifespan, closed once at
  shutdown. Never built per-request.
- `fetch_all` always goes through `asyncio.gather`, even for today's single
  provider: with one provider that is indistinguishable from a loop, with two
  it is the whole point. Don't collapse it into a plain loop as a
  "simplification" — that would silently break the concurrency guarantee the
  moment a second provider is configured.
- Output order from `fetch_all` matches the input `providers` order (gather
  preserves order; this is relied on, not incidental).
- `RequestCounter` is passed explicitly into `fetch`/`fetch_all` rather than
  reached for as global/module state — it's shared across concurrent
  `fetch()` calls, but simple dict increments with no `await` in between are
  safe under asyncio's single-threaded cooperative model.

### Writing a state file

A store is written whole, so changing one entry means reading the rest first,
and two writers doing that at once each save a version missing what the other
did. Four writers can: `app new`, `app revoke`, `app regen`, and the server's
claim handler — and `claim` for the other store, where a lost update means an
access URL whose one-time setup token has already been spent.

- **Writers take an exclusive `fcntl.flock`**, held across the whole
  read-modify-write. `update_state_file` is the only way to write either store,
  so there is no path that can forget it.
- **The lock is a sidecar** (`provider_creds.lock`, `aggregator_creds.lock`), never
  the store itself: the atomic write replaces the store, so a lock held on its
  inode stops guarding the file that ends up at that path.
- **Readers take no lock and need none.** The write-then-rename means a reader
  sees one whole version or another, never a torn one.
- **The lock is not reentrant and has no timeout.** A second update on the same
  path from inside the body blocks on the first forever, silently. Compose two
  changes by making them in one block, never by nesting.
- **`fcntl` is POSIX-only**, as this application already is throughout its mode
  bits and its Dockerfile. There is no Windows fallback and there must be no
  silent no-op one, which would drop the guarantee callers take from this.
- **No file I/O on the event loop.** The claim handler's locked update and the
  auth path's read both go through `starlette.concurrency.run_in_threadpool`,
  because both can block: the read on the disk, the update on the lock.

An attacker who can write the config directory defeats the lock — they can
replace the sidecar between two writers' opens, and `flock` is on an inode.
That is not a hole to plug: the same access lets them rewrite the store
outright, which is easier and worse. The lock serializes this application's own
writers; it is not a security boundary, and no `flock`-based one could be.

## Cross-cutting: secrets and logging

**Two postures, and the first is much the safer.** A defense that *removes* the
secret leaves nothing to leak and no rule for anyone to follow: the digest-only
app token store and `SecretStr` are of that kind. A defense that keeps the
secret and handles it carefully — the right flags on an open, an error message
built to exclude its input, a credential carried between two representations —
depends on every future caller getting it right. Reach for the first when the
choice is available; when it is not, the list below is what has to be
remembered.

Five distinct places actively defend against leaking secrets; know all five
before touching anything credential-adjacent:

1. **`SecretStr`** on every stored access URL and on the credentials one claim
   issues — redacts in `repr()`/`str()` automatically. The access URL store's
   `field_serializer` reveals the real values for the JSON file and nowhere
   else. Nothing in `config.toml` needs wrapping: it holds no credential.
2. **Digests, not plaintext, in `aggregator_creds.json`.** Every secret that
   file concerns is verified and never reproduced: the setup token against
   what was pasted, the credentials against what the client app sends, and the
   access URL is the client app's to keep. So `app list` cannot print a
   credential and a stray `repr` cannot either — not because they are careful
   but because there is nothing there. Plain SHA-256 and no KDF: these are
   256-bit random values, not chosen passwords, and there is no dictionary to
   search.
3. **The `ValidationError` path** in `state_file.py`, which `load_config` and
   both stores share — never `str()` a `ValidationError` directly; pydantic's
   default rendering embeds the raw rejected input. The rendering is a
   *safelist*: it reads pydantic's message only for `value_error` and
   `assertion_error`, whose text this project wrote, and otherwise reads the
   failure's type, a fixed slug with nothing of the file in it. That is
   deliberate rather than an enumeration of dangerous cases: subtracting the
   bad parts from a message built out of file contents makes every model shape
   added later another chance to subtract the wrong set. A location is rendered
   from field names and list indices only, never mapping keys, which come from
   the file. `tests/test_state_file.py` pins the property across a corpus
   indexed by misparse shape. For the same reason none of these paths reports
   the offending byte from a `UnicodeDecodeError`.
4. **uvicorn's own access logger** — bypasses application-level logging
   entirely. `access_log.py` + the `CLAIM_PATH_PREFIX`-based wiring in
   `cli.py`/`app.py` exists because uvicorn was printing the raw setup token to
   stdout on every `POST /simplefin/claim/{token}`, independent of anything the
   app itself logs. If a future route ever embeds a credential in its path, it
   needs the same treatment; if it only sends credentials via headers (like
   Basic Auth today), it doesn't need any redaction since uvicorn's access log
   never includes headers.
5. **The stored provider access URL**, the most sensitive value in the system.
   It reaches a message only as `NormalizedUrl.origin` or `origin_and_path`,
   never as the raw string — and the same rule covers the setup token, whose
   live, unclaimed value is the *path* of a claim URL. `cli.py` therefore
   prints `UrlValidationError`'s own message and never re-renders the URL
   itself; that message is deliberately built for display, and its docstring
   says what it guarantees and where the guarantee stops. Provider response
   bodies are attacker-influenced. None of that text reaches an error message
   this application writes, and the only provider-derived value that reaches a
   log line is an account id, in `merge`'s collision warning, rendered with
   `%r` so that a newline in one cannot forge a log line of its own. What
   reaches the client app is deliberately much larger: every key inside
   `accounts` survives the merge, and v1's `errors` strings are relayed
   verbatim.

**A dependency's own logging is outside all five.** `httpcore2` traces each
exchange at DEBUG, including the exception it is about to raise, and a protocol
error's text quotes the status line the provider sent — so a provider that
echoes back the `Authorization` header it was given puts this aggregator's
Basic Auth password in a debug log. Nothing here sets a log level, so those
records reach no one who did not turn DEBUG on themselves. Clamping `httpcore2`
in `serve` would close it and would also silence an operator who deliberately
asked for it, so the gap is left open.

A sixth rule has no single home because it applies at every command boundary:
**a value the user typed is not safe to echo just because they typed it.** A
mistyped `--key` is most often a pasted setup token, so `_check_key` rejects it
without naming it, and `claim` declines to quote a setup token it could not
decode. Repeating the value would put a secret on stderr in order to tell the
user something they already know.

**Credentials reach a provider only through `auth=`, never through a URL.**
Keep it that way: it is what leaves a request's own URL free of secrets, on
every path but the claim POST, whose path is the live setup token.

It is not enough by itself, because a provider chooses what a failed exchange
looks like and httpx2 quotes the wire in the exception it raises. A provider
that echoes back the `Authorization` header it was given, or the path it was
called on, puts that value inside a protocol error's text. So a failed request
is reported by the exception's class and never by its message: `transport.fetch`
and `cli.claim` both render `type(exc).__name__`, and each has a leak corpus
entry driving a real socket that hands back what it was sent.

## Testing conventions

- **Fakes over mocks.** `httpx2.MockTransport` fakes provider HTTP calls;
  `FastAPI.TestClient` drives real ASGI request/response cycles including the
  lifespan; `typer.testing.CliRunner` drives `claim`'s menu and token prompts
  via `input=`; `_loopback_provider` serves one over a real socket. No
  `unittest.mock` beyond `monkeypatch`, no `respx` (dropped when the project
  migrated from `httpx` to `httpx2` — respx doesn't support `httpx2`).
- **No traffic leaves the machine in the automated suite.** One test binds a
  socket: `_loopback_provider` in `tests/test_accounts_endpoint.py` answers
  from `127.0.0.1`. It asserts that credentials stay out of the outbound URL,
  and a `MockTransport` cannot carry that — installing one replaces the client
  `build_provider_client` returned, discarding the `base_url`/`auth=` split
  that is the thing under test. Loopback is still the network stack; what the
  suite never does is address a host off this machine. The one place that does
  — `scripts/manual_verify.py` against the live SimpleFIN demo bridge — is
  separate, human-run, and documented as such in the README.
- **`tests/support.py`** holds the shared fixtures: `make_config` builds a
  `Config` through `model_validate(dict)`, the same path `load_config` uses
  (direct kwargs trip up basedpyright on `SecretStr` fields);
  `make_access_urls`/`make_app` supply `create_app`'s other arguments, and
  `make_app` takes a config *directory* so that a test can revoke an app
  mid-run and have the next request see it; `make_claimed_app` and
  `make_unclaimed_app` put a record in the store and hand back the one thing
  the store does not keep — the credentials, or the setup token secret;
  `install_provider_transport` swaps in a `MockTransport`-backed client, with
  an ordering constraint its docstring explains. The fixture provider is a
  `custom_providers` entry, so most tests exercise the config-supplied path
  rather than a built-in root.
- Tests are labelled to say whether they pin a *requirement* or *current
  behavior*; `AGENTS.md` has the rule.
- Reaching into `app.py`'s private `_AppState` from test code is accepted
  (`tests/support.py` imports it with a `# pyright: ignore[reportPrivateUsage]`)
  — it's the established pattern for tests that need to touch internal wiring.
- `asyncio_mode = "auto"` in `pyproject.toml` — async test functions don't
  need an explicit `@pytest.mark.asyncio`.
- `httpx2.QueryParams(tuple(params))` in `transport.py`'s `fetch()` isn't
  arbitrary — passing a bare `Sequence[tuple[str, str]]` fails basedpyright
  under httpx2's `QueryParamTypes` (invariance on `list[tuple[...]]`); `tuple`
  is covariant and satisfies the stub.
- `AGENTS.md` has the verification pass every change is expected to survive.

## Deliberate deviations from the SimpleFIN spec

- **No `GET /create`.** The browser flow a real provider offers for minting a
  setup token is not implemented; `app new` is this application's equivalent.
  Precedent: https://beta-bridge.simplefin.org/simplefin/create is unimplemented.
- **The access URL must share the claim URL's provider root.** The spec leaves
  that open; see "Provider URL validation".

## Non-goals (for *this* version — don't build ahead of need)

Explicitly out of scope until a real multi-provider version is undertaken:
id namespacing, actual merge logic, partial-failure handling across
providers, multi-provider config validation beyond "exactly one for now."
The seams below exist so that version doesn't require an architectural
rewrite — but do not fill them in speculatively.

## Seams for the multi-provider future

These functions/types are intentionally more general than today's
single-provider behavior requires. When multi-provider work actually starts,
these are where it goes — nowhere else should need to change:

- **`resolve_provider_for_account(account_id, providers)`** — today: asserts
  exactly one provider and returns it, ignoring `account_id` entirely.
  Multi-provider: real id-namespacing-based ownership lookup. It must **never**
  fan out to ask each provider to find an id — namespacing in the id itself is
  the only allowed mechanism, because generating no provider traffic beyond
  what the client app asks for is a hard constraint on this project.
- **`rewrite_ids(body)` / `unrewrite_ids(account_ids)`** (`id_rewriting.py`)
  — today: identity functions. Multi-provider: map between
  provider-local ids and aggregator-global (namespaced) ids, in the response
  body and in outbound `account` filter params respectively.
- **`Config.providers`** — already `list[Provider]` with `max_length=1`;
  multi-provider is raising that limit and building out the above, not a
  schema change. The access URL store, the `provider_clients` dict and
  `fetch_all` are all keyed by provider key already.
- **`fetch_all`** — already fans out over an arbitrary-length provider list
  via `asyncio.gather`; no change needed here at all when a second provider
  is added. Deliberately already-done, and covered by tests using two providers
  even though production config only ever has one today
  (`tests/test_transport.py`).

Note that `README.md` already describes the multi-provider behavior, showing
two `[[providers]]` entries, while the code accepts exactly one. That gap is
intentional; don't "fix" the docs to match the code. `config.toml` ships with a
single entry, because it is a file the user copies and edits rather than an
illustration.

## Known planned future work

`TODO.md` is the running list. Check it before assuming any behavior
documented above is permanent.

## Stack notes

FastAPI, uvicorn, httpx2. Pydantic v2 for config models and both on-disk
stores. Typer for the CLI. `platformdirs` for the config directory. `uv` for
packaging/dependency management. `ruff` (full `ALL` ruleset with ignore list)
and `basedpyright` ("recommended" mode, zero-warning policy) for static checks.
`pyproject.toml` is the source of truth for versions; keep `ruff`'s
`target-version` and basedpyright's `pythonVersion` matched to
`requires-python`, which is the *oldest* supported Python and not the newest
that exists — mismatched, `ruff format` will rewrite code into syntax the
interpreter cannot parse while `ruff check` reports nothing wrong.
