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
more SimpleFIN providers behind it. The current version is an identity
function over exactly one provider: provider responses are passed through
unchanged, while transport failures — and redirects — are normalized to
generated 502 responses. Every module is already shaped for a future
multi-provider version that fans out and merges — see "Seams for the
multi-provider future" below.

The other half of the job is credential handling. A provider access URL embeds
Basic Auth credentials for the user's bank data, and it is obtained by pasting
in a base64 URL from a web page. Guarding that path — see "Provider URL
validation" and "Cross-cutting: secrets and logging" — shapes more of this
codebase than the proxying does.

## Module map

`src/simplefin_aggregator/`:

| File | Responsibility |
|---|---|
| `cli.py` | Typer entry points: `claim`, `gen-token`, `serve`, each taking `--config-dir`. Wires everything else together. `main()` is the `console_scripts` target. |
| `config.py` | Pydantic models (`Config`, `Provider`, `CustomProvider`, `ClientAuth`), `load_config()`, and where the config directory lives. All config validation lives here. |
| `provider_registry.py` | `ProviderEntry` and `KNOWN_PROVIDERS`: the fixed set of providers a setup token may be claimed from, plus `merged_providers`/`find_provider`. Must not import `config.py` — config imports it. |
| `url_validation.py` | `NormalizedUrl`, `parse_url`/`parse_root`, `validate_claim_url`/`validate_access_url`, `UrlValidationError`. The phishing defense; read its module docstring before touching anything here. |
| `provider_access_urls.py` | The `provider_creds.json` store: provider key → access URL, loaded/saved with 0600 and a permission warning. |
| `app.py` | `create_app(config, access_urls) -> FastAPI`: the ASGI app factory, lifespan, and all three HTTP routes. |
| `auth.py` | `require_client_auth`, the FastAPI dependency guarding `/simplefin/accounts`. |
| `access_url.py` | Builds the access URL this aggregator hands back from `POST /simplefin/claim/{token}` — the one it *issues*, not the ones it holds (that is `provider_access_urls.py`). |
| `setup_token.py` | Builds the base64 setup token `gen-token` prints (the inverse direction of `access_url.py`: this aggregator's *own* claim URL, encoded the way a real provider's would be). |
| `provider_clients.py` | `build_provider_client(access_url) -> httpx2.AsyncClient`: one long-lived client per provider, built once at startup. |
| `transport.py` | `fetch`/`fetch_all`: the concurrent, non-raising provider-request layer. |
| `provider_response.py` | `ProviderSuccess` / `ProviderFailure` / `ProviderResponse` — the uniform result type `fetch` always returns. |
| `merge.py` | `merge(responses) -> MergedResponse`. Single-provider passthrough today; where multi-provider merging will live. |
| `provider_resolution.py` | `resolve_provider_for_account`: "which provider owns this account id" seam. Trivial today (always the sole provider). |
| `id_rewriting.py` | `rewrite_ids` / `unrewrite_ids`: no-op seams for future cross-provider id namespacing. |
| `request_counter.py` | `RequestCounter`: per-provider daily request counts, logged for observability only, never used as a control. |
| `access_log.py` | Generic uvicorn-access-log redaction utility. Knows nothing about SimpleFIN or claim tokens — `app.py`/`cli.py` supply what to redact. |

`tests/support.py` holds shared test helpers (`make_config`,
`make_access_urls`, `make_app`, `install_provider_transport`) used across most
test files.

## On-disk state

Two files, both in the directory `--config-dir` selects for every subcommand,
defaulting to `platformdirs.user_config_dir("simplefin-aggregator")`:

| File | Written by | Holds |
|---|---|---|
| `config.toml` | the user, by hand | `claim_token`, `client.password`, provider keys, `custom_providers` |
| `provider_creds.json` | `claim` | provider key → provider access URL |

`provider_creds.json` is the most sensitive thing this app owns and **nothing
regenerates it**: a setup token is one-time-use, so a lost entry costs the user
a fresh token from the provider. That is the constraint behind the store's
0600-and-atomic-write handling; never write code that treats an entry as safe
to discard.

`config.toml` still holds credentials of its own — `claim_token` and
`client.password` together let any local user read the user's bank data through
the loopback endpoint — so `warn_if_permissive` (warn on stderr, don't block)
runs on both files.

The store is keyed by provider key because that is the one identifier
`config.toml` and the store share; neither file has to name the other.
Consequence: two accounts at the same provider would collide on one key.

## Key data structures

### `Config` (`config.py`)

```text
Config
  bind_host: str = "127.0.0.1"
  bind_port: int = 8080
  providers: list[Provider]           # Field(min_length=1, max_length=1) -- exactly one, for now
  custom_providers: list[CustomProvider] = []
  client: ClientAuth
  claim_token: SecretStr              # validated: non-empty, URL-path-safe (quote(x, safe="") == x)
  base_url: str                       # validated: http/https, has a host, no user-info,
                                      #   http only for a literal loopback IP
  .provider_entries() -> tuple[ProviderEntry, ...]   # KNOWN_PROVIDERS + custom_providers

Provider
  key: str                            # a provider key; resolves against provider_entries()

CustomProvider
  key: str
  label: str
  root: str                           # validated via parse_root()

ClientAuth
  username: str
  password: SecretStr
```

A `Provider` is now just a reference: no `name`, and no `access_url` (that
moved to the store). `key` is the identifier everywhere — the store's key, the
`provider_clients` dict's key, and what appears in log lines.

Every `provider.key` is resolved against `provider_entries()` by a model-level
validator, so a dangling reference fails at config-load time for *every*
command rather than at first dereference — and a `custom_providers` root is
parsed during validation, so a broken one fails at `claim` time rather than
surviving to `serve`.

**A `Config` is read-only once `load_config` returns**, and there is no config
hot-reload — nothing mutates one, nothing rebinds `app.state.config`, and
`serve` reads both `config.toml` and the store exactly once at startup. That is
what lets validators here establish invariants good for the object's whole
lifetime.

`providers` is deliberately still a `list` (not a single `Provider` field) even
though exactly one is enforced — that's the seam for the multi-provider
version. All secrets are `pydantic.SecretStr`, which redacts itself in
`repr()`/`str()` by construction, not by convention.

**`load_config(path) -> Config`** reads TOML, warns if the file is
group/other-accessible, and validates via `Config.model_validate(dict)`. Its
error path never `str()`s the `ValidationError` — see "Cross-cutting: secrets
and logging". Any new validator added to `Config`/`Provider`/`ClientAuth` gets
that redaction for free; don't bypass it by catching and re-stringifying the
raw `ValidationError` elsewhere.

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

ProviderSuccess(provider_name, status: int, headers: dict[str, str], body: bytes)
  .ok -> True
  .json -> Any            # lazy; json.loads(body) on access, never called on the passthrough path

ProviderFailure(provider_name, error: str)
  .ok -> False
```

This is the uniform type `fetch()` always returns — it never raises. A
discriminated union of two frozen dataclasses, narrowed via `isinstance` (see
`merge.py` for the `isinstance(response, ProviderFailure)` pattern).

### `MergedResponse` (`merge.py`)

```python
MergedResponse(status: int, content_type: str, body: bytes)
```

The output of `merge(responses: list[ProviderResponse])`. With one response:
success passes status/content-type/body through byte-for-byte; failure becomes
`status=502`, a SimpleFIN-shaped JSON body (`{"accounts": [], "errors": [...],
"errlist": [...]}`). `merge` unpacks `(response,) = responses` — it will raise
if ever called with a list of any other length, which is intentional today
(there's no multi-provider merging logic yet; see below).

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

`app.state.config` is a **separate**, second thing stored directly (not inside
`_AppState`) — because `auth.py`'s `require_client_auth` lives in a different
module and isn't nested inside `create_app`, so it has no closure over
`config` the way the route handlers do. It reads `request.app.state.config`
instead, with its own `cast(Config, ...)`.

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
  them say they pin behaviour rather than a requirement. Both are accepted in
  preference to the checks that would close them.

## Request/command flows

### `serve [--config-dir DIR]`

```text
cli.serve
  -> _load_config_or_exit(config_path(dir))   # load_config, or print+exit 1
  -> load_access_urls(provider_creds_path(dir))
  -> create_app(config, access_urls)          # validates every stored access URL, see below
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

### CLI `claim [TOKEN] [--config-dir DIR]` (claiming from a *real* provider)

```text
cli.claim
  -> _load_config_or_exit(...)                # claim needs a fully valid config, like serve
  -> load_access_urls(...) + check_can_save(...)   # BEFORE the token is spent
  -> _select_provider(config.provider_entries())   # numbered menu; no default, no free-text host
  -> token from argv, else prompt
  -> _decode_setup_token  -> validate_claim_url(entry.root, ...)   # BEFORE any network call
  -> POST claim_url.origin_and_path, redirects disabled
       - 403      -> "may be compromised, revoke it at the provider"
       - non-200  -> status only, never the body (3xx lands here too)
  -> validate_access_url(entry.root, response.text.strip(), ...)
  -> save_access_url(store, entry.key, access_url)
  -> warn if no [[providers]] entry names this key
```

Four orderings in there are load-bearing:

1. **The store is read and its directory checked before the POST.** The token
   is one-time-use, so a file problem discovered afterwards is a lost
   credential rather than a retry.
2. **The menu comes before the token.** Which root the token is matched
   against is the whole of the defense, so it is the user's deliberate answer,
   never a default and never inferred from the token.
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

### CLI `gen-token` (the inverse: building *this aggregator's own* setup token)

```text
cli.gen_token(config_dir)
  -> _load_config_or_exit(...)
  -> build_setup_token(config) -> base64(f"{base_url}/simplefin/claim/{claim_token}")
  -> print to stdout
```

### `POST /simplefin/claim/{token}`

```text
app.claim(token)
  -> secrets.compare_digest(token, config.claim_token)   # constant-time; config is closure, not app.state
  -> 403 if mismatch
  -> else: build_access_url(config) -> 200 text/plain, no trailing newline
```

Stateless and repeatable by design — see "Deliberate deviations" below.
`config` here is the `create_app` closure variable, not read from `app.state`.

### `GET /simplefin/accounts` (and `/simplefin/info`, minus the auth/filtering)

```text
app.accounts(request)
  -> require_client_auth (FastAPI dependency; 403 on bad/missing Basic Auth)
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
  -> merge(responses) -> MergedResponse
  -> rewrite_ids(merged.body)   # no-op today
  -> Response(body, status, content_type)
```

`/simplefin/info` is the same shape minus the client-auth dependency and the
account-filtering branch (it always queries every configured provider with no
params).

**Redirects are never followed** — not here, and not on the claim POST; the
`follow_redirects=False` is set explicitly in both clients even though it is
httpx2's default, so a refactor cannot silently flip it. The spec defines
`/accounts` as returning only 200, 402 or 403, so there is no legitimate
redirect, and a `requests`-style silent POST→GET conversion on a 302 is exactly
what this prevents. With redirects disabled httpx2 *returns* the 3xx rather
than raising, so `transport.fetch` has to reject it explicitly or it would pass
through as a `ProviderSuccess`. A 3xx is therefore the one status class not
covered by the byte-identity pass-through guarantee: the client app sees the
same `502` and SimpleFIN-shaped error body it gets for an unreachable provider.

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

## Cross-cutting: secrets and logging

Four distinct places have had to actively defend against leaking secrets; know
all four before touching anything credential-adjacent:

1. **`SecretStr`** on every credential-bearing config field and on every stored
   access URL — redacts in `repr()`/`str()` automatically. The store's
   `field_serializer` reveals the real values for the JSON file and nowhere
   else.
2. **The `ValidationError` paths** in `load_config` and `load_access_urls` —
   never `str()` a `ValidationError` directly (see `config.py` above);
   pydantic's default rendering embeds the raw rejected input. For the same
   reason, neither reports the offending byte from a `UnicodeDecodeError`.
3. **uvicorn's own access logger** — bypasses application-level logging
   entirely. `access_log.py` + the `CLAIM_PATH_PREFIX`-based wiring in
   `cli.py`/`app.py` exists because uvicorn was printing the raw
   `claim_token` to stdout on every `POST /simplefin/claim/{token}`,
   independent of anything the app itself logs. If a future route ever
   embeds a credential in its path, it needs the same treatment; if it only
   sends credentials via headers (like Basic Auth today), it doesn't need
   any redaction since uvicorn's access log never includes headers.
4. **The stored provider access URL**, the most sensitive value in the system.
   It reaches a message only as `NormalizedUrl.origin` or `origin_and_path`,
   never as the raw string — and the same rule covers the setup token, whose
   live, unclaimed value is the *path* of a claim URL. `cli.py` therefore
   prints `UrlValidationError`'s own message and never re-renders the URL
   itself; that message is deliberately built for display, and its docstring
   says what it guarantees and where the guarantee stops. Provider response
   bodies are attacker-influenced and are never echoed either.

**Credentials reach a provider only through `auth=`, never through a URL.**
That is what makes the two places that render a dependency's own error text
safe: `transport.fetch`'s `str(exc)`, which becomes the 502 body the client app
sees, and `claim`'s "could not reach provider". Both describe a request whose
URL is an `origin_and_path`, so there is no credential in it to leak. Keep it
that way — putting credentials back into a request URL would silently
compromise both messages.

## Testing conventions

- **Fakes over mocks.** `httpx2.MockTransport` fakes provider HTTP calls;
  `FastAPI.TestClient` drives real ASGI request/response cycles including the
  lifespan; `typer.testing.CliRunner` drives `claim`'s menu and token prompts
  via `input=`. No `unittest.mock` beyond `monkeypatch`, no `respx` (dropped
  when the project migrated from `httpx` to `httpx2` — respx doesn't support
  `httpx2`).
- **No real network in the automated suite.** The one place that *does* hit a
  real network — `scripts/manual_verify.sh` against the live SimpleFIN demo
  bridge — is separate, human-run, and documented as such in the README.
- **`tests/support.py`** holds the shared fixtures: `make_config` builds a
  `Config` through `model_validate(dict)`, the same path `load_config` uses
  (direct kwargs trip up basedpyright on `SecretStr` fields);
  `make_access_urls`/`make_app` supply the app's second constructor argument;
  `install_provider_transport` swaps in a `MockTransport`-backed client, with
  an ordering constraint its docstring explains. The fixture provider is a
  `custom_providers` entry, so most tests exercise the config-supplied path
  rather than a built-in root.
- Tests are labelled to say whether they pin a *requirement* or *current
  behaviour*; `AGENTS.md` has the rule.
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

- **Repeatable claim.** `POST /simplefin/claim/{token}` is stateless and
  accepts the same constant `claim_token` indefinitely, rather than the
  spec's one-time-claim rule. Documented in the README with rationale
  (loopback-only deployment).
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

- **`merge(responses: list[ProviderResponse])`** — today: `(response,) =
  responses`, pure passthrough or 502. Multi-provider: combine several
  `ProviderSuccess`/`ProviderFailure` into one `MergedResponse`, presumably
  concatenating `accounts` arrays and aggregating `errors`/`errlist`.
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

Note that `README.md` and `config.example.toml` already describe the
multi-provider behaviour, showing several `[[providers]]` entries, while the
code accepts exactly one. That gap is intentional; don't "fix" the docs to
match the code.

## Known planned future work

`TODO.md` is the running list. Check it before assuming any behaviour
documented above is permanent — some of what is on it would change the
repeatable-claim deviation and what `config.toml` has to hold.

## Stack notes

FastAPI, uvicorn, httpx2. Pydantic v2 for config models and the access URL
store. Typer for the CLI. `platformdirs` for the config directory. `uv` for
packaging/dependency management. `ruff` (full `ALL` ruleset with ignore list)
and `basedpyright` ("recommended" mode, zero-warning policy) for static checks.
`pyproject.toml` is the source of truth for versions; keep `ruff`'s
`target-version` and basedpyright's `pythonVersion` matched to
`requires-python`, which is the *oldest* supported Python and not the newest
that exists — mismatched, `ruff format` will rewrite code into syntax the
interpreter cannot parse while `ruff check` reports nothing wrong.
