<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Architecture

This is a developer/agent-facing map of `simplefin-aggregator`. `README.md`
covers how to use it; `AGENTS.md` covers how to work on it.

## Summary

SimpleFIN Aggregator (`sf-agg`) provides a [SimpleFIN protocol (version
1)](https://www.simplefin.org/protocol-v1.html) server that aggregates data from one
or more other SimpleFIN protocol v1 servers.

It combines the accounts of every configured provider, with a prefix for each
prepended to each account id — see [Account id
namespacing](#account-id-namespacing). If not all providers respond, the results
from the remaining providers are returned — see [`GET
/simplefin/accounts`](#get-simplefinaccounts).

It manages both its own credentials ('aggregator credentials'), so that clients
can securely connect to it; and provider credentials, so that it can securely
connect to providers. Provider credentials are especially sensitive, as they are
effectively long-lived bearer tokens that grant read-only access to the user's
financial data.

It takes care to validate that the URLs used to connect to providers are
legitimate — see [Provider URL validation](#provider-url-validation), and not to
reveal credentials — see [Cross-cutting: secrets and
logging](#cross-cutting-secrets-and-logging).

## Terminology

<!-- TODO: incomplete; see `docs/TODO.md`. -->

### Fundamentals
- **SimpleFIN protocol** — a client-server protocol for sharing finance data.

SimpleFIN Aggregator (`sf-agg`) is both a SimpleFIN protocol client and server.

### Parties

- **provider** — a SimpleFIN server aggregated by `sf-agg`.
- **client app** — a client of `sf-agg`. Sometimes abbreviated as `client`.
- **sf-agg server** — the `sf-agg serve` process. Can be abbreviated to `server`
  if the meaning is clear from context.
- **`app`, in code** — an application object: the FastAPI instance (`app.py`)
  or the Typer root (`cli.app`). Never a client app.
- **key** — what `config.toml` and `provider_creds.json` know a provider by
  (**provider key**), or what `aggregator_creds.json` and the `client`
  commands know a client app by (**client key**). Both match `KEY_PATTERN`.

### Credentials

- **setup token** — the base64 string a user copies from a SimpleFIN server
  and pastes into a client. Can be abbreviated as `token`. There is no other kind of token.
- **claim URL** — what a setup token decodes to.
- **claim secret**, in code — the random last path segment of a claim URL,
  which the issuing server recognizes it by. Not user-facing vocabulary.
- **access URL** — what a claim URL responds with: a URL embedding the Basic
  Auth credentials a client presents on every subsequent request to the server that issued it.

Each SimpleFIN server issues its own credentials. When a term needs to be
disambiguated, it is prefixed with the issuer: **provider** or **aggregator**
(**`agg`** in identifiers). For example, "provider setup token", "aggregator access URL".

### Verbs

- **issue** — verb. A SimpleFIN server **issues** a setup token, and **issues** an access URL in
  exchange for one.
- **exchange** — verb. A setup token is **exchanged** for an access URL.
- **claim** — verb. "A client **claims** an access URL" is an acceptable
  alternative to the above if it reads more fluently.
- **exchanged** — adjective. A setup token that has already been exchanged for an access URL.
- **redeemed** — adjective. Acceptable synonym for `exchanged` if it would read more fluently.
- **use** / **used** — verb, adjective. Synonym for `exchange` / `exchanged`, only in informal user-facing text, e.g. "a setup token can be used once."
- **unexchanged** — adjective, a setup token that has not yet been exchanged
- **unused** — less-formal synonym for `unexchanged`
- Never: a setup token is *claimed*, *spent*, or *unredeemed*.

## Map of key modules

`src/sf_agg/`:

| File | Responsibility |
|---|---|
| `cli.py` | The command-line entry points | 
| `config.py` | The config file's two shapes (private `_ConfigModel`/`_ProviderFileEntry`, public `Config`/`Provider`), `CustomProvider`, `load_config()`, and where the config directory lives. All config validation lives here, the prefix rules included. Holds no credentials. |
| `state_file.py` | Everything this application does with a file it owns: the permission warning, the redacted validation-error rendering, the atomic 0600 write, the sidecar `flock`, and the locked read-modify-write. Imports nothing else in the package — `config.py` takes its warning and its error rendering from here, not the other way around. |
| `agg_creds.py` | The aggregator creds store: the two-state record, the digest helpers, and the constructors that mint a claim secret and exchange a record for credentials. |
| `provider_registry.py` | `ProviderEntry` and `KNOWN_PROVIDERS`: the fixed set of providers a setup token may reference, plus `merged_providers`/`find_provider`. Must not import `config.py` — config imports it. |
| `url_validation.py` | `NormalizedUrl`, `parse_url`/`parse_origin`, `validate_claim_url`/`validate_access_url`, `UrlValidationError`. The phishing defense; read its module docstring before touching anything here. |
| `provider_access_urls.py` | The `provider_creds.json` store: provider key → access URL. Schema and semantics only; the file handling is `state_file.py`'s. |
| `app.py` | `create_app(config, access_urls, agg_creds_path) -> FastAPI`: the ASGI app factory, lifespan, and all three HTTP routes. |
| `access_url.py` | Builds the access URL this aggregator hands back from `POST /simplefin/claim/{claim_secret}` — the one it *issues*, not the ones it holds (that is `provider_access_urls.py`). |
| `setup_token.py` | Builds the base64 setup token `client add` prints (the inverse direction of `access_url.py`: this aggregator's *own* claim URL, encoded the way a real provider's would be). |
| `provider_clients.py` | `build_provider_client(access_url) -> httpx2.AsyncClient`: one long-lived client per provider, built once at startup. |
| `transport.py` | `fetch`/`fetch_all`: the concurrent, non-raising provider-request layer. |
| `provider_response.py` | `ProviderSuccess` / `ProviderFailure` / `ProviderResponse` — the uniform result type `fetch` always returns. |
| `merge.py` | `merge(results) -> MergedResponse`: several providers' responses concatenated into one v1 body, each account id behind its provider's prefix. |
| `provider_resolution.py` | `resolve_providers_for_account`: which providers an exposed account id may belong to, and what that id is to each of them. |
| `access_log.py` | Generic uvicorn-access-log redaction utility. Knows nothing about SimpleFIN or setup tokens — `app.py`/`cli.py` supply what to redact. |

## On-disk state

Three files, plus a lock sidecar per store, all in the directory
`config_dir()` (`config.py`) resolves.

| File | Written by | Holds |
|---|---|---|
| `config.toml` | the user, by hand | the settings `_ConfigModel` in `config.py` declares |
| `provider_creds.json` | `claim` | provider key → provider access URL |
| `aggregator_creds.json` | `client add`/`revoke`/`reset`, and the claim route | client key → an unexchanged or exchanged aggregator creds record, digests only |

**The three want different things, and the difference is load-bearing.**

`provider_creds.json` is the most sensitive thing this application owns.
An access URL embeds Basic Auth credentials for the user's bank data, so
anything that can read this file can read that data.

`aggregator_creds.json` has no confidentiality concern at all, by
construction. Every value in it is a SHA-256 digest of a 256-bit random
secret, kept to recognize that secret and never to reproduce it, so reading
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
`config.toml` and the store share; neither file has to name the other — which
is why two accounts at one provider is a non-goal. The aggregator creds store
is keyed by client key, constrained to `[a-z0-9][a-z0-9-]*` at the command and
again in the model, so what `client list` prints is what this application could
have written.

## Key data structures

### `Config` (`config.py`)

```text
_ConfigModel / _ProviderFileEntry   private -- the config file as written
      |   _resolve()                defaults what the file omitted, then checks
      v                             the providers as a set
Config / Provider                   public -- frozen, every field present
```

To see the precise fields, consult `config.py`.

`config.py`'s module docstring says why there are two.

`base_url` must be ASCII because a URL is: an internationalized host appears in
one as punycode, not as the characters it is spelled with. A provider origin
gets that conversion free — `parse_origin` reads `raw_host` off httpx2's parser,
which has already encoded it — while `base_url` goes through `urlsplit`, which
normalizes nothing and hands back whatever it was given. So the requirement
falls on the input, and it is checked at load time rather than where
`build_setup_token` would hit it, because `client add` writes its record before
it prints and a failure there would leave a client that could never be handed a
setup token.

A `Provider` is a reference plus a namespace: no `name`, and no `access_url`
(that lives in the store). `key` is the identifier everywhere — the store's
key, the `provider_clients` dict's key, and what appears in log lines — and
`prefix` is what the client app sees, per "Account id namespacing".

Three checks run at load time rather than at first use, so every command
reports them and not just the one that would trip over them:

- Every `provider.key` is resolved against `provider_entries()`, so a dangling
  reference fails for *every* command rather than at first dereference — and a
  `custom_providers` origin is parsed during validation, so a broken one fails
  whichever command loads the config rather than surviving to `serve`.
- No key appears twice in `providers`. Everything per-provider is keyed by it,
  so two entries sharing one would collapse into a single provider rather than
  being aggregated.
- The prefix set is unambiguous, per "Account id namespacing".

**A `Config` is read-only once `load_config` returns**, and there is no config
hot-reload — nothing mutates one, nothing reloads one, and `serve` reads
`config.toml` and `provider_creds.json` exactly once at startup. That is what
lets the checks in `Config.__post_init__` establish invariants good for the
object's whole lifetime; a reload would cost that. `CustomProvider` is frozen
for the same reason: `provider_entries()` reads those, so a mutable one would
let a checked invariant stop being true of the object it was checked on.

**`aggregator_creds.json` is live state: read afresh on every request that
authenticates, and written on every setup token exchange.** That is the whole of what makes
`client revoke` take effect without a restart. Do not add a cache, an mtime check
or a reload signal to it.

**`load_config(path) -> Config`** reads TOML, warns if the file is
group/other-accessible, and validates and resolves via `config_from_mapping`.
Its two error branches are item 3 of "Cross-cutting: secrets and logging".

### `CustomProvider` (`config.py`)

The model a user hand-edits into `custom_providers` for a provider the
built-in list does not name. Its `@field_validator("origin")` runs
`parse_origin()` and reports a bad origin against that one field, rather than
leaving it to the model-level check to reject the whole `Config` — so the
error names the offending entry instead of, via pydantic's default
`ValidationError` rendering, the entire file.
`as_provider_entry()` converts one into a `ProviderEntry`,
validating both `key` and `origin` again in the process; `provider_entries()`
merges the result into `KNOWN_PROVIDERS`
through `merged_providers`, which rejects a duplicate key rather than letting
a custom entry shadow or collide with a built-in one.

### `AggCreds` (`agg_creds.py`)

`AggCreds` is what `aggregator_creds.json` keeps for one client, as digests
only: `UnexchangedAggCreds` while the client's setup token awaits exchange,
`ExchangedAggCreds` once it has been exchanged for an access URL.

Two states, narrowed by `isinstance` the way `ProviderSuccess | ProviderFailure`
is. **Exchanging a setup token replaces the record rather than flagging it**:
the exchanged record keeps no claim secret digest, which is what makes a
replayed setup token fail the same lookup an unissued one fails — the
protocol's indistinguishability requirement, obtained by construction instead
of defended by a branch. It is also why `client reset` cannot leave a client
holding live credentials and an unexchanged setup token at once.

Nothing in these models is a `SecretStr`, because nothing in them is a secret.
They are strict, and `exchanged` has to be a JSON boolean, for the reason
`_Sha256Hex` is constrained: a value this application could not have written is
rejected when the file is read, rather than at the comparison or the arithmetic
it would later break.

`new_agg_creds` and `exchange_agg_creds` are the only ways to build a record.
They mint the secret and return it once alongside the record that will
recognize it, which keeps the timestamp and the digesting out of `cli.py` and
the claim route — the two places where assembling a record by hand would put
a plaintext credential on disk.

### `ProviderEntry` (`provider_registry.py`)

```text
ProviderEntry(key: str, origin: str)   # frozen
```

`key` is constrained to `[a-z0-9][a-z0-9-]*` in `__post_init__`, through which every
entry passes, config-supplied ones included — that is what makes a key safe to
render in an error message and to use as a store key. **Published keys are
permanent**: changing one orphans users' stored access URLs and breaks their
config references.

`merged_providers` rejects a duplicate key rather than letting a config entry
override a built-in one, and `find_provider` raises on an absent key — never
guessed at, never fuzzy-matched. Adding an entry is a config-file edit and
nothing else: no flag, no interactive "trust this origin?" prompt.
`provider_registry.py`'s docstring says why.

### `NormalizedUrl` (`url_validation.py`)

```text
NormalizedUrl                        # frozen; produced only by parse_url
  scheme, host, username, password
  origin: str                        # scheme://host[:port]      -- what matching compares and a message names
  origin_and_path: str               # scheme://host[:port]path  -- what a request is sent to
  .has_creds -> bool
```

Per-field normalization is documented on the dataclass. What matters outside
`url_validation.py` is that neither `origin` nor `origin_and_path` carries
credentials, and two rules that are easy to break by accident:

- **Parse once per operation.** Read every field an operation needs off one
  `NormalizedUrl`; never re-parse the source string alongside it, and never
  derive a field by a separate string operation. Two parses of a string that
  was normalized in between silently check different inputs. This applies to
  request-building as much as to validation.
- **Messages name a URL only through `origin`, never the raw string or
  `origin_and_path`.** See "Cross-cutting: secrets and logging".

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

The **one** thing hung off FastAPI/Starlette's `app.state` (which is an
untyped attribute bag — `Any` all the way down). Built once in the lifespan
context manager, read via `_get_app_state(request)` which does the one
`cast(_AppState, request.app.state.app_state)` for the whole app. Adding a
new piece of request-scoped shared state means adding a field here, not a new
`app.state.whatever`.

## Account id namespacing

The account ids this aggregator exposes are each provider's own ids behind that
provider's `prefix`. Nothing else in a body is rewritten: v1 scopes a
transaction id's uniqueness to its account, so unique account ids make
transaction ids unique transitively, and an `org` is identified by `domain` and
`sfin-url`, which are global already — two providers reporting the same
institution is correct rather than a collision.

**A prefix is part of an account's identity to the client app.** Changing one
is indistinguishable, from that side, from every account at that provider
vanishing and a set of new ones appearing. Prefixes are as permanent as
provider keys.

The default, `f"{key}:"`, is prefix-free by construction: keys are unique and
match `[a-z0-9][a-z0-9-]*`, so no key plus a colon can be a prefix of another. An
explicit prefix is constrained to `[A-Za-z0-9._:-]*`, because it travels in a
URL query parameter and lands in the client app's database and nothing is
gained by allowing whitespace or `%` in it.

**The blank prefix is legal, and is the point of the field being
overridable.** Someone whose client app is already connected directly to a
provider, and so holds that provider's raw account ids, puts that provider
behind this aggregator by setting `prefix = ""` for it. Any other value
re-identifies every one of their accounts.

Routing inverts the prefixing, so the set has to be unambiguous. `Config`
validation keeps non-blank prefixes prefix-free — stricter than distinctness,
since `bank` and `bank2` are distinct and still ambiguous — and allows at most
one blank prefix unless `allow_multiple_blank_prefixes` is set (see below).
`resolve_providers_for_account` is then a longest-prefix match, returning every
provider tied for the longest.

**The blank prefix is a knowingly leaky choice, and the leak is accepted
rather than defended against.** If the blank provider returns an id of its own
beginning with another provider's prefix, that id routes to the wrong provider
— which answers with nothing, having no such account — and it can collide
outright with a real id from that provider. A prefix carrying a delimiter the
providers' own ids do not contain avoids both, which is what the default has.
A collision is detected in `merge`, which keeps every colliding account, in
configured provider order, and logs one line per colliding id naming the
providers it came from: dropping one would lose the
user's data to hide a configuration the operator chose, and a client app can do
nothing with the news while the operator can change a prefix.

**`allow_multiple_blank_prefixes = true` lifts the one-blank-prefix limit and
nothing else.** It is for a client app that already holds raw account ids from several
providers it used to connect to directly, where any prefix on any of them would
re-identify those accounts. An id no named prefix claims then belongs to every
blank-prefix provider, and the longest-prefix match returns all of them, so each
is asked about it and the ones that did not issue it answer about no
account. The costs are:

- those providers hear one another's ids — the one exception to "No provider
  hears another's account ids" under "`GET /simplefin/accounts`".
- their ids can collide, which is handled identically to the collisions
  described above.
- the response to the client app may include spurious errors from providers for
  the ids they do not recognize.

## Provider URL validation

A setup token is pasted in from a web page, making the URLs it leads to only as
trustworthy as that page: one from a lookalike site points at the attacker's
host, which would then answer every request. So a claim URL and an access URL
must each be validated before use, by `validate_claim_url` and
`validate_access_url`. `url_validation.py`'s module docstring holds the threat
model and the rules in more detail.

## Request/command flows

### `serve`

```text
cli.serve
  -> note naming the config directory -> stderr
  -> _load_config_or_exit()                   # load_config, or print+exit 1
  -> _check_agg_creds_or_exit()               # store parses, directory writable
                                              #   empty store -> warn, not fail
  -> load_access_urls(provider_creds_path())
  -> create_app(config, access_urls, agg_creds_path())
                                              # validates every stored access URL, see below
  -> install_access_log_redaction(...)        # generic filter, told about CLAIM_PATH_PREFIX
  -> uvicorn.run(app, host, port)
       -> lifespan startup: build_provider_client() per provider -> _AppState on app.state
       -> ... serves requests ...
       -> lifespan shutdown: aclose() every provider client
```

`create_app` raises rather than starting a server that cannot work: for each
configured `provider.key` it resolves the entry, looks the key up in the store
(missing → "run claim first"), and re-runs `validate_access_url` against that
provider's *current* origin. That is a single-entry comparison, not a scan —
the URL was issued by a specific provider, so that is the origin it must still
match. Every provider's check runs regardless of the others' outcome, and
`create_app` raises every failure together as one `ProviderAccessUrlError`;
`cli.py` prints each on its own line, the same display-safe text
`validate_access_url`/`StateFileError` produce for a single provider, so
"Cross-cutting: secrets and logging" governs it unchanged. All of it happens
before uvicorn starts, so a config change that invalidates a stored URL fails
at startup, not on the first request.

### CLI `claim [provider]` (exchanging a provider setup token)

```text
cli.claim
  -> refuse extra arguments                  # never quoting them
  -> _load_config_or_exit()                  # claim needs a fully valid config, like serve
  -> load_access_urls(...) + check_can_save(...)   # BEFORE the token is exchanged
  -> the provider, from config.provider_entries()
       provider given   -> find_provider    # named, so exact; never fuzzy; never echoed
       otherwise        -> numbered menu; no default, no free-text host
  -> prompt for the token, hidden if stdin is a real terminal
  -> _decode_setup_token  -> validate_claim_url(entry.origin, ...)   # BEFORE any network call
  -> POST claim_url.origin_and_path, redirects disabled
       - 403      -> "may be compromised, revoke it at the provider"
       - non-200  -> status only, never the body (3xx lands here too)
  -> validate_access_url(entry.origin, response.text.strip(), ...)
  -> save_access_url(store, entry.key, access_url)
  -> note naming the store path -> stderr
  -> _probe_access_url(access_url): GET /accounts?balances-only=1, one attempt
       "Checking that the credentials work..." on stderr, then:
       success    -> "Credentials work."
       failure    -> "warning: could not confirm ..."; exit code stays 0
  -> warn if no [[providers]] entry names this key
```

One property and four orderings in there are load-bearing.

The property: **the provider is named or chosen, never defaulted.** Which origin
the token is matched against is the whole of the phishing defense, so it is the
user's deliberate answer either way — naming it on the command line is as
deliberate as picking from the menu, and an unknown key is an error rather than
a guess. An unknown provider or an extra argument is refused without being
echoed, because it could plausibly be a setup token pasted onto the command
line instead of at the prompt.

The orderings:

1. **The provider is settled before the token is read.** Otherwise the origin a
   token is matched against could be inferred from the token, which is the one
   thing that must not decide it.
2. **The store is read and its directory checked before the POST.** The token
   is one-time-use, so a file problem discovered afterwards is a lost
   credential rather than a retry.
3. **Validation comes before the POST**, which would spend a pasted token
   whatever the reply.
4. **The response is stripped, then validated, then stored** — never printed.

A `claim` invocation for a provider the config's `[[providers]]` does not name still
succeeds and is stored — it just warns, since `serve` would otherwise report it
as unclaimed later.  <!-- TODO: "unclaimed" is unlikely to be the actual message here. -->

`_build_claim_client` and `_build_probe_client` are seams purely for test
injection (see Testing below) — not a general dependency-injection pattern
used elsewhere in this codebase.

### CLI `client add` / `reset` / `revoke` (issuing/revoking aggregator setup tokens)

```text
cli.client_add(key)
  -> _load_config_or_exit()                     # for base_url
  -> _check_key(key)                            # [a-z0-9][a-z0-9-]*, naming a rejected key
  -> _writable_store_or_exit()                  # directory writable, and warn if shared
  -> update_agg_creds(store):                   # one locked read-modify-write
       key already present -> exit 1, naming `client reset`, store untouched
       else -> new_agg_creds() -> (claim_secret, UnexchangedAggCreds)
  -> note naming the store path -> stderr
  -> build_setup_token(base_url, claim_secret) -> stdout, alone
  -> "shown once" note -> stderr
```

The duplicate check is *inside* the lock, or it answers from a version another
writer is already replacing.

`client reset` is the same flow over an existing record, keeping its
`created_at`. `client revoke` deletes the record outright. Neither leaves a
client holding a live credential and an unexchanged setup token at once, which
is what would make "revoked" mean two things.

### `POST /simplefin/claim/{claim_secret}`

```text
app.claim(claim_secret)
  -> run_in_threadpool(_exchange_setup_token, store_path, claim_secret)   # no file I/O on the event loop
       update_agg_creds(store):                                  # one locked read-modify-write
         find the UnexchangedAggCreds whose digest matches (compare_digest)
         none -> raise _UnknownSetupTokenError, out of the update, nothing written
         else -> exchange_agg_creds(record) -> (credentials, ExchangedAggCreds); replaces the record
  -> _UnknownSetupTokenError -> 403 "unknown token"
  -> StateFileError          -> 500, and no access URL
  -> else: build_access_url(base_url, credentials) -> 200 text/plain, no trailing newline
```

**Persist, then respond.** The write-then-rename and its `fsync` are what make "persisted" mean survived a
power cut, not merely reached the page cache — which is why the write stays in
the request path.

**A replayed token and one that was never issued are answered identically, by
construction rather than by a branch.** Exchanging the setup token replaces the
record it matched, so there is nothing left that answers to a redeemed token;
both fail the same lookup and there is no code that could tell them apart.

### `GET /simplefin/accounts`

<!-- TODO: too much repetition of unessential info about code -->

```text
app.accounts(request)
  -> require_client_auth (reads aggregator_creds.json in a threadpool on every
     request, 403 on missing, unknown or unreadable; 403 also on an
     unexchanged record, which recognizes a claim secret and not credentials)
  -> _get_app_state(request) -> provider_clients
  -> _forwarded_accounts_params(request)
       - keep only v1's five query keys (ACCOUNTS_FORWARDED_PARAMS); a
         "version" a client app sends is ignored, as v1 is the only supported
         version
  -> _route_requests(config, params) -> [(provider, that provider's params)]
       - no "account" values: every provider, none given an "account" param
       - otherwise: resolve_providers_for_account() per id -> owning providers
         and provider-local id; one request per owning provider, in configured
         order, carrying its own ids and the shared params
       - an id no prefix claims: logged, routed nowhere, absent from the response
  -> fetch_all(clients, requests, "/accounts")
       -> asyncio.gather over fetch() per request, order preserved
       -> fetch() never raises: httpx2.HTTPError -> ProviderFailure
       -> 3xx                                    -> ProviderFailure
  -> merge(zip(each request's provider.prefix, responses)) -> MergedResponse
  -> Response(body, 200, "application/json")
```

**No provider receives another's account ids**, except among providers sharing
the blank prefix, per "Account id namespacing". An `account` filter names ids
in one provider's namespace, so each queried provider is given only the ids
that resolved to it, alongside the parameters the request shares
(`start-date`, `end-date`, `pending`, `balances-only`). Requests are built by
iterating the configured providers rather than the requested ids, so two client
apps asking for the same accounts in different orders are answered in the same
order.

**An id no prefix claims is reported to the operator, not the client app.** It is
logged, naming the id, and nothing about it reaches the response: repeating it
there would put a value from outside into a body another program displays, and
an entry that withholds it is a bare count naming nothing a client app could
act on. A request naming only unknown ids therefore queries no provider and
answers 200 with an empty account set.

`GET /simplefin/info` shares none of this. It answers `{"versions": ["1.0"]}`
locally, contacting no provider: the version is a fact about the protocol this
server speaks to its client app, and requires no authentication.

<!-- TODO: 'both clients' -->

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

- Everything provider-facing is `httpx2.AsyncClient` / `async def`. The only
  synchronous clients are `claim`'s two, for the POST and the probe — a one-shot
  command outside any request path.
- One `AsyncClient` per provider, built once in the lifespan, closed once at
  shutdown. Never built per-request.
- `fetch_all` always goes through `asyncio.gather`, including for a
  single-provider configuration: with one provider that is indistinguishable
  from a loop, with two it is the whole point — one dead provider must not hold
  the client app's request open for the length of the others' as well. Don't
  collapse it into a plain loop as a "simplification".
- Output order from `fetch_all` matches the input `requests` order (gather
  preserves order; this is relied on, not incidental — it is what pairs each
  response with the prefix `merge` puts on its accounts).

### Writing a state file

<!-- TODO: "the other store" ? -->

A store is written whole, so changing one entry means reading the rest first,
and two writers doing that at once each save a version missing what the other
did. Four writers can: `client add`, `client revoke`, `client reset`, and the
server's claim handler — and `claim` for the other store, where a lost update
means an access URL whose one-time setup token has already been exchanged.

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
aggregator creds store and `SecretStr` are of that kind. A defense that keeps the
secret and handles it carefully — the right flags on an open, an error message
built to exclude its input, a credential carried between two representations —
depends on every future caller getting it right. Reach for the first when the
choice is available; when it is not, the list below is what has to be
remembered.

Five distinct places actively defend against leaking secrets; know all five
before touching anything credential-adjacent:

1. **`SecretStr`** on every stored access URL and on the credentials an
   exchange issues (`AccessUrlAuth`) — redacts in `repr()`/`str()`
   automatically. The access URL store's `field_serializer` reveals the real
   values for the JSON file and nowhere else. Nothing in `config.toml` needs wrapping: it holds no credential.
2. **Digests, not plaintext, in `aggregator_creds.json`.** Every secret that
   file concerns is verified and never reproduced: the claim secret against
   what a client presents, the credentials against what the client app sends,
   and the access URL is the client app's to keep. So `client list` cannot print a
   credential and a stray `repr` cannot either — not because they are careful
   but because there is nothing there. Plain SHA-256 and no KDF: these are
   256-bit random values, not chosen passwords, and there is no dictionary to
   search.
3. **The `ValidationError` path** in `state_file.py`, which `load_config` and
   both stores share. Never `str()` a `ValidationError` directly; pydantic's
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
   the offending byte from a `UnicodeDecodeError`, and a rejected `prefix` is
   not quoted back. Beside it, in `load_config` only, is the branch for
   `ConfigCheckError` and `ProviderRegistryError` — the checks that span
   providers, which run after validation and so cannot use that rendering. It
   interpolates their message directly, which is safe because both build their
   text from provider keys alone, already constrained to `[a-z0-9][a-z0-9-]*`. The
   catch names those two types rather than `ValueError`, so an unexpected one
   stays a traceback instead of being reported as the operator's config.
4. **uvicorn's own access logger** — bypasses application-level logging
   entirely. `access_log.py` (see its module docstring for why the filter
   matches on path prefix alone, with no notion of HTTP method) + the
   `CLAIM_PATH_PREFIX`-based wiring in `cli.py`/`app.py` exists because without it uvicorn
   would print the raw claim secret to stdout on every request to
   `/simplefin/claim/{claim_secret}`, independent of anything the app itself logs. If
   a future route ever embeds a credential in its path, it needs the same
   treatment; if it only sends credentials via headers (like Basic Auth
   today), it doesn't need any redaction since uvicorn's access log never
   includes headers.
5. **The stored provider access URL**, the most sensitive value in the system.
   It reaches a message only as `NormalizedUrl.origin`, never as the raw
   string — and the same rule covers the setup token, which
   encodes the *path* of a claim URL. `cli.py` therefore prints
   `UrlValidationError`'s own message and never re-renders the URL itself; that
   message is deliberately built for display, and its docstring says what it
   guarantees and where the guarantee stops. A provider response body is
   whatever the provider chose to send. None of that text reaches an error message this
   application writes. Account ids are the exception that proves the rule: two
   log lines render one, `merge`'s collision warning and the routing warning for
   an id no prefix claims, the first provider-derived and the second from the
   client app's query string. Both use `%r`, so that a newline in an id cannot
   forge a log line of its own, and neither line's text is relayed to the client
   app — an id reaches the response only as an account's own `id`, which is what
   the client app sends back to ask about that account again. What reaches the
   client app is deliberately much larger: every key inside `accounts` survives
   the merge, and v1's `errors` strings are relayed verbatim. What this
   application does not do is write a string of its own into `errors`: that array
   holds only what the providers themselves reported.

`claim` also keeps what is typed on its command line out of its errors: an
extra argument is refused without being quoted, and an unknown provider is
reported without being named. The `claim` flow says why.

**A dependency's own logging is outside all five.** `httpcore2` traces each
exchange at DEBUG, including the exception it is about to raise, and a protocol
error's text quotes the status line the provider sent — so a provider that
echoes back the `Authorization` header it was given puts this aggregator's
Basic Auth password in a debug log. Nothing here sets a log level, so those
records reach no one who did not turn DEBUG on themselves. Clamping `httpcore2`
in `serve` would close it and would also silence an operator who deliberately
asked for it, so the gap is left open.

**Credentials reach a provider only through `auth=`, never through a URL.**
Keep it that way: it is what leaves a request's own URL free of secrets, on
every path but the claim POST, whose path holds a live claim secret.

It is not enough by itself, because a provider chooses what a failed exchange
looks like and httpx2 quotes the wire in the exception it raises. A provider
that echoes back the `Authorization` header it was given, or the path it was
called on, puts that value inside a protocol error's text. So a failed request
is reported by the exception's class and never by its message: `transport.fetch`,
`cli.claim`, and `cli`'s post-claim probe all render `type(exc).__name__`, and
each has a leak corpus entry driving a real socket that hands back what it was
sent.

## Testing conventions

- **Fakes over mocks.** `httpx2.MockTransport` fakes provider HTTP calls;
  `FastAPI.TestClient` drives real ASGI request/response cycles including the
  lifespan; `typer.testing.CliRunner` drives `claim`'s menu and token prompts
  via `input=`; `_loopback_provider` serves one over a real socket. No
  `unittest.mock` beyond `monkeypatch`, and no `respx`, which does not support
  `httpx2`.
- **No traffic leaves the machine.** Two fakes bind a socket on `127.0.0.1`,
  where a test is about what reaches the wire and a `MockTransport` cannot
  carry it — installing one replaces the client `build_provider_client`
  returned, discarding the `base_url`/`auth=` split.
  `_loopback_provider` in `tests/test_accounts_endpoint.py` checks that
  credentials stay out of the outbound URL; `echoing_provider` in
  `tests/support.py` hands a request back as a malformed status line, for the
  leak corpus in "Cross-cutting: secrets and logging". Loopback is still the
  network stack; what the suite never does is address a host off this machine.
  Neither does `scripts/sf_agg_smoke.py`, the end-to-end check CI runs, which
  drives a real `sf-agg` against `scripts/sf_server_fake.py`, a SimpleFIN
  provider on loopback. Neither script ships in the package.
- **`tests/support.py`** holds the shared fixtures: `make_config` builds a
  `Config` through `config_from_mapping`, the path `load_config` uses, from as
  many `ProviderSpec`s as a test names — each one a key, an origin, an optional
  explicit prefix and the access URL the store will hold for it, spelled out
  rather than derived so that a test meaning them to disagree can say so;
  `make_access_urls`/`make_app` supply `create_app`'s other arguments, and
  `make_app` takes a config *directory* so that a test can revoke a client
  mid-run and have the next request see it; `add_client_and_exchange` and
  `add_client` put a record in the store and hand back the one
  thing the store does not keep — the credentials, or the claim secret;
  `install_provider_transport` swaps in a `MockTransport`-backed client, with
  an ordering constraint its docstring explains, and refuses a key the app
  built no client for. The fixture provider is a
  `custom_providers` entry, so most tests exercise the config-supplied path
  rather than a built-in one.
- Tests are labeled to say whether they pin a *requirement* or *current
  behavior*; `AGENTS.md` has the rule.
- Reaching into `app.py`'s private names from test code is accepted
  (`tests/support.py` imports `_AppState` with a `# pyright: ignore[reportPrivateUsage]`)
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
  setup token is not implemented; `client add` is this application's equivalent.
  Precedent: https://beta-bridge.simplefin.org/simplefin/create is unimplemented.
- **The access URL must share the claim URL's provider origin.** The spec leaves
  that open; see "Provider URL validation".

## Non-goals (don't build ahead of need)

- **Two accounts at the same provider.** The access URL store keys on the
  provider key, so one configured provider is one account at that provider.
  Supporting more means an instance identifier distinct from the registry key,
  which changes the store's shape and the claim menu.
- **De-duplicating one real-world account reached through two providers.** It
  appears twice, with two ids. Nothing in the protocol identifies it as one
  account, and guessing is worse than not.
- **Caching, retrying or coalescing proxied `/accounts` requests.** One client
  app request produces at most one request per provider.
- **Per-provider `start-date`/`end-date` rewriting**, partial-response
  assembly, or any other cleverness about what to ask each provider for. The
  client app's parameters go to every queried provider unchanged.
- **`errlist`, `connections`, or any other v2 field**, inbound or outbound.
- **Dereferencing an account's `currency` URL.**
- **Encrypting credentials at rest** beyond file permissions.

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
