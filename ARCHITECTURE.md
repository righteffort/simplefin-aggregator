# Architecture

This is a developer/agent-facing map of `simplefin-aggregator`.

- **What to build and why**: `PROMPT.md` (the original spec/brief; not
  committed to git, lives only in the working tree).
- **How to use it**: `README.md` (operator-facing setup instructions).
- **How it's built**: this file.

## Purpose

A server that speaks the SimpleFIN Bridge protocol to a client app
(Actual Budget is the motivating example, but it's generic) and
proxies one or more SimpleFIN providers behind it. The current version
is an identity function: exactly one provider: provider responses are
passed through unchanged, while transport failures are normalized to
generated 502 responses. Every module is already shaped for a future
multi-provider version that fans out and merges — see "Seams for the
multi-provider future" below.

## Module map

`src/simplefin_aggregator/`:

| File | Responsibility |
|---|---|
| `cli.py` | Typer entry points: `claim`, `gen-token`, `serve`. Wires everything else together. `main()` is the `console_scripts` target. |
| `config.py` | Pydantic models (`Config`, `Provider`, `ClientAuth`) and `load_config()`. All config validation lives here. |
| `app.py` | `create_app(config) -> FastAPI`: the ASGI app factory, lifespan, and all three HTTP routes. |
| `auth.py` | `require_client_auth`, the FastAPI dependency guarding `/simplefin/accounts`. |
| `access_url.py` | Builds the access URL this aggregator hands back from `POST /simplefin/claim/{token}`. |
| `setup_token.py` | Builds the base64 setup token `gen-token` prints (the inverse direction of `access_url.py`: this aggregator's *own* claim URL, encoded the way a real provider's would be). |
| `provider_clients.py` | `build_provider_client(provider) -> httpx2.AsyncClient`: one long-lived client per provider, built once at startup. |
| `transport.py` | `fetch`/`fetch_all`: the concurrent, non-raising provider-request layer. |
| `provider_response.py` | `ProviderSuccess` / `ProviderFailure` / `ProviderResponse` — the uniform result type `fetch` always returns. |
| `merge.py` | `merge(responses) -> MergedResponse`. Single-provider passthrough today; where multi-provider merging will live. |
| `provider_resolution.py` | `resolve_provider_for_account`: "which provider owns this account id" seam. Trivial today (always the sole provider). |
| `id_rewriting.py` | `rewrite_ids` / `unrewrite_ids`: no-op seams for future cross-provider id namespacing. |
| `request_counter.py` | `RequestCounter`: per-provider daily request counts, logged for observability only, never used as a control. |
| `access_log.py` | Generic uvicorn-access-log redaction utility. Knows nothing about SimpleFIN or claim tokens — `app.py`/`cli.py` supply what to redact. |

`tests/support.py` holds shared test helpers (`make_config`, `install_provider_transport`) used across most test files.

## Key data structures

### `Config` (`config.py`)

```text
Config
  bind_host: str = "127.0.0.1"
  bind_port: int = 8080
  providers: list[Provider]      # Field(min_length=1, max_length=1) -- exactly one, for now
  client: ClientAuth
  claim_token: SecretStr         # validated: non-empty, URL-path-safe (quote(x, safe="") == x)
  base_url: str                  # validated: scheme in (http, https), has a hostname

Provider
  name: str
  access_url: SecretStr          # validated: scheme == https, has hostname, embeds user:pass
  .parsed_access_url() -> SplitResult   # re-parses on demand, never cached in a field

ClientAuth
  username: str
  password: SecretStr
```

`providers` is deliberately still a `list` (not a single `Provider` field) even
though exactly one is enforced — that's the seam for the multi-provider
version. All secrets are `pydantic.SecretStr`, which redacts itself in
`repr()`/`str()` by construction, not by convention.

**`load_config(path) -> Config`** reads TOML, checks the file isn't
group/other-readable (warns on stderr, doesn't block), and validates via
`Config.model_validate(dict)`. On a `pydantic.ValidationError`, it does **not**
`str()` the exception directly — pydantic's default rendering embeds each
failed field's raw input value, which would print credentials to stderr. It
rebuilds a message from `exc.errors(include_url=False, include_input=False)`
instead. Any new field validator added to `Config`/`Provider`/`ClientAuth`
gets this redaction for free; don't bypass it by catching and re-stringifying
the raw `ValidationError` elsewhere.

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
discriminated union of two frozen dataclasses, narrowed via
`isinstance` (see `merge.py` for the `isinstance(response,
ProviderFailure)` pattern).

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

## Request/command flows

### `serve --config PATH`

```text
cli.serve
  -> _load_config_or_exit(path)          # load_config, or print+exit 1
  -> install_access_log_redaction(...)   # generic filter, told about CLAIM_PATH_PREFIX by app.py
  -> create_app(config)                  # builds FastAPI app + lifespan closure
  -> uvicorn.run(app, host, port)
       -> lifespan startup: build_provider_client() per provider -> _AppState on app.state
       -> ... serves requests ...
       -> lifespan shutdown: aclose() every provider client
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
     provider name -> providers_to_query (today: always the sole provider)
     else: providers_to_query = all configured providers
  -> fetch_all(clients, providers_to_query, "/accounts", params, counter)
       -> asyncio.gather over fetch() per provider, order preserved
       -> fetch() never raises: httpx2.HTTPError -> ProviderFailure
  -> merge(responses) -> MergedResponse
  -> rewrite_ids(merged.body)   # no-op today
  -> Response(body, status, content_type)
```

`/simplefin/info` is the same shape minus the client-auth dependency and the
account-filtering branch (it always queries every configured provider with no
params).

### CLI `claim <setup-token>` (claiming from a *real* provider)

```text
cli.claim(setup_token)
  -> base64.b64decode -> claim_url
  -> _build_claim_client() (httpx2.Client; swappable in tests via monkeypatch)
  -> POST claim_url
  -> non-200 -> print error, exit 1
  -> 200 -> print response.text (the access URL) to stdout, one-time-use
     reminder to stderr
```

Pure CLI utility, no FastAPI/app involvement. `_build_claim_client` is a
seam purely for test injection (see Testing below) — not a general
dependency-injection pattern used elsewhere in this codebase.

### CLI `gen-token` (the inverse: building *this aggregator's own* setup token)

```text
cli.gen_token(config_path)
  -> _load_config_or_exit(config_path)
  -> build_setup_token(config) -> base64(f"{base_url}/simplefin/claim/{claim_token}")
  -> print to stdout
```

## Concurrency model

- Everything provider-facing is `httpx2.AsyncClient` / `async def`. No sync
  client anywhere in the request path (the CLI's `claim` command is the one
  legitimate exception — it's a one-shot utility outside any request path).
- One `AsyncClient` per provider, built once in the lifespan, closed once at
  shutdown. Never built per-request.
- `fetch_all` always goes through `asyncio.gather`, even for today's single
  provider — "with one provider this is indistinguishable; with two it is the
  whole point" (direct from the spec). Don't collapse it into a plain loop as
  a "simplification" — that would silently break the concurrency guarantee
  the moment a second provider is configured.
- Output order from `fetch_all` matches the input `providers` order (gather
  preserves order; this is relied on, not incidental).
- `RequestCounter` is passed explicitly into `fetch`/`fetch_all` rather than
  reached for as global/module state — it's shared across concurrent
  `fetch()` calls, but simple dict increments with no `await` in between are
  safe under asyncio's single-threaded cooperative model.

## Cross-cutting: secrets and logging

Three distinct places have had to actively defend against leaking secrets;
know all three before touching anything credential-adjacent:

1. **`SecretStr`** on every credential-bearing config field — redacts in
   `repr()`/`str()` automatically.
2. **`load_config`'s error path** — never `str()` a `ValidationError` directly
   (see `config.py` above); pydantic's default rendering embeds the raw
   rejected input.
3. **uvicorn's own access logger** — bypasses application-level logging
   entirely. `access_log.py` + the `CLAIM_PATH_PREFIX`-based wiring in
   `cli.py`/`app.py` exists because uvicorn was printing the raw
   `claim_token` to stdout on every `POST /simplefin/claim/{token}`,
   independent of anything the app itself logs. If a future route ever
   embeds a credential in its path, it needs the same treatment; if it only
   sends credentials via headers (like Basic Auth today), it doesn't need
   any redaction since uvicorn's access log never includes headers.

## Testing conventions

- **Fakes over mocks.** `httpx2.MockTransport` fakes provider HTTP calls;
  `FastAPI.TestClient` drives real ASGI request/response cycles including the
  lifespan. No `unittest.mock`, no `respx` (dropped when the project migrated
  from `httpx` to `httpx2` — respx doesn't support `httpx2`).
- **No real network, ever, in the automated suite.** The one place that *does*
  hit a real network — `scripts/manual_verify.sh` against the live SimpleFIN
  demo bridge — is explicitly separate, human-run, and documented as such in
  the README. Never add a test that makes a real outbound call.
- **`tests/support.py`** has `make_config(...)` (builds a `Config` via
  `Config.model_validate(dict)`, the same path `load_config` uses — not via
  direct kwargs, which trips up basedpyright on `SecretStr` fields) and
  `install_provider_transport(app, provider_name, handler)` (swaps a
  provider's real `AsyncClient` for a `MockTransport`-backed one — must be
  called *after* `TestClient(app)`'s `with` block has started, since that's
  what runs the lifespan and creates `_AppState` in the first place).
- Reaching into `app.py`'s private `_AppState` from test code is accepted
  (`tests/support.py` imports it with a `# pyright: ignore[reportPrivateUsage]`)
  — it's the established pattern for tests that need to touch internal wiring.
- `asyncio_mode = "auto"` in `pyproject.toml` — async test functions don't
  need an explicit `@pytest.mark.asyncio`.
- `httpx2.QueryParams(tuple(params))` in `transport.py`'s `fetch()` isn't
  arbitrary — passing a bare `Sequence[tuple[str, str]]` fails basedpyright
  under httpx2's `QueryParamTypes` (invariance on `list[tuple[...]]`); `tuple`
  is covariant and satisfies the stub.
- Every basedpyright warning is treated as something to fix or explicitly
  `# pyright: ignore[rule]` with a one-line reason — not just errors. Run
  `ruff format --check .`, `ruff check .`, `basedpyright`, and `pytest` as one
  verification pass after every change; a subset is not sufficient.

## Deliberate deviations from the SimpleFIN spec

- **Repeatable claim.** `POST /simplefin/claim/{token}` is stateless and
  accepts the same constant `claim_token` indefinitely, rather than the
  spec's one-time-claim rule. Documented in the README with rationale
  (loopback-only deployment).

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
  Multi-provider: real id-namespacing-based ownership lookup. Per the spec,
  this must **never** fan out to ask each provider to find an id — namespacing
  in the id itself is the only allowed mechanism, since "no traffic beyond
  what the consumer generates" is a hard constraint.
- **`rewrite_ids(body)` / `unrewrite_ids(account_ids)`** (`id_rewriting.py`)
  — today: identity functions. Multi-provider: map between
  provider-local ids and aggregator-global (namespaced) ids, in the response
  body and in outbound `account` filter params respectively.
- **`Config.providers`** — already `list[Provider]` with `max_length=1`;
  multi-provider is raising that limit and building out the above, not a
  schema change.
- **`fetch_all`** — already fans out over an arbitrary-length provider list
  via `asyncio.gather`; no change needed here at all when a second provider
  is added. This is deliberately already-done, per the spec's concurrency
  section, and is covered by tests using two providers even though
  production config only ever has one today (`tests/test_transport.py`).

## Known planned future work

See `TODO.md` for a running list (currently: making claims one-time-use with
dynamically generated per-claim username/password, and access revocation).
That's a real change to the "repeatable-claim deviation" documented above —
check `TODO.md` before assuming the current repeatable-claim behavior is
permanent.

## Stack notes

Python 3.12.4+, FastAPI, uvicorn, httpx2. Pydantic v2 for config
models. Typer for the CLI. `uv` for packaging/dependency management.
`ruff` (full `ALL` ruleset with ignore list in `pyproject.toml`) and
`basedpyright` ("recommended" mode, zero-warning policy) for static
checks.
