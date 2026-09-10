# Task: aggregate several providers

Make this application do the thing it is named for: proxy more than one
SimpleFIN provider behind one endpoint, presenting the union of their accounts
to the client app as if it were a single bridge.

`AGENTS.md` governs how to work: the verification pass, non-vacuous tests, the
review cycle, comment and commit discipline, secrets discipline. Read it first
and follow it; nothing about how to work is repeated here.

Read `docs/ARCHITECTURE.md` for the map of the code. It is current as of the
start of this work, and this task invalidates parts of it — the final step
brings it back into line.

This application implements **SimpleFIN protocol v1**
(<https://www.simplefin.org/protocol-v1.html>), in both directions: v1 to the
client app, v1 to the providers. There is no server-implementation guide and
you do not need one; what matters is the data types, and those are documented.
Two facts from the spec shape the whole design and are worth confirming in it
before you start:

- **v1's only error channel is `errors: [string]`.** `errlist` and
  `connections` are v2.0.0 additions — v2 deprecates `errors` *in favour of*
  `errlist`. This code currently emits both, which is a bug this task fixes by
  deleting `errlist`.
- **A transaction id is unique within its account**, not globally. So
  namespacing account ids makes transaction ids unique transitively, and
  nothing in a response body needs rewriting except `accounts[].id`.

## What is changing and why

Today `Config.providers` is `Field(min_length=1, max_length=1)` and `merge`
unpacks `(response,) = responses`. Everything else — `fetch_all`'s
`asyncio.gather`, the per-provider client dict, the `RequestCounter`, the
`resolve_provider_for_account` and `rewrite_ids` seams — was built for this
task. Most of those seams are in the right place. Two are not, and finding
that out is part of the work:

- **`fetch_all` needs per-provider parameters.** `ARCHITECTURE.md` claims it
  needs no change at all. That is wrong: today every queried provider receives
  the whole `account` filter list, so with two providers each one receives the
  other's account ids.
- **`id_rewriting.py` is in the wrong place and goes away.** `merge` has to
  parse each body to concatenate the accounts; prefixing ids in a separate
  pass over the serialized body would parse everything twice. The outbound
  half folds into `merge`, the inbound half into `provider_resolution`.

## The design

### Account ids: a per-provider prefix

The account ids this aggregator exposes to the client app are the provider's
own account ids with a per-provider prefix prepended.

- `Provider` gains `prefix: str`, **defaulting to `f"{key}:"`**. Constrain an
  explicit value to `[A-Za-z0-9._:-]*` — a prefix ends up in a URL query
  parameter and in the client app's database, and nothing is gained by
  allowing whitespace or `%` in it.
- The default is prefix-free by construction: keys are unique and match
  `[a-z0-9-]+`, so no `key + ":"` can be a prefix of another.

**An empty prefix is legal and is the point of the field being overridable.**
Someone already syncing directly from a provider, whose client app holds that
provider's raw account ids, puts that provider behind the aggregator by
setting `prefix = ""` for it. Any other value re-identifies every one of their
accounts.

That generalizes to a rule worth stating loudly in the README and in
`ARCHITECTURE.md`: **a prefix is part of an account's identity to the client
app.** Changing one later is indistinguishable, from the client app's side,
from the old accounts vanishing and new ones appearing. Prefixes are as
permanent as provider keys.

#### Uniqueness and the blank catch-all

Inbound `?account=` routing inverts the prefixing, so the prefix set has to be
unambiguous. Validate at config load:

- **Non-blank prefixes are prefix-free**: no non-blank prefix is a prefix of
  another. (This subsumes "distinct". It is also why a set of prefixes cannot
  simply be checked for equality: `bank` and `bank2` are distinct and still
  ambiguous.)
- **At most one provider may have a blank prefix.** `""` is a prefix of every
  string, so strict prefix-freeness would forbid it entirely; instead it is
  the designated catch-all.
- **Also reject duplicate `key`s in `providers`.** `max_length=1` has been
  hiding that two entries with the same key silently collapse into one
  `provider_clients` dict slot.

Resolution is then **longest matching prefix, falling back to the blank
provider if there is one**.

The blank catch-all is a knowingly leaky choice, and the leak belongs in the
docs rather than in defensive code. If the blank provider returns an account
whose own id begins with another provider's prefix, that id routes to the
wrong provider (which answers with nothing, since it has no such account), and
it can collide outright with a real id from that provider. Both are avoided by
giving prefixes a delimiter the providers' own ids do not contain, which the
default does. Detect a collision — a duplicate id in the merged accounts list
— and report it as a trailing error entry plus a log line, but keep both
accounts: dropping one loses data to protect the client from a config the
operator chose.

### Routing a request

- **No `account` parameter: fan out to every configured provider**, with no
  `account` parameter. That is what the endpoint means — every account the
  server knows about.
- **With `account` parameters: query only the owning providers, each with only
  its own ids.** Never fan an account id out to a provider that does not own
  it. Two ids spanning two providers means two concurrent requests, each
  carrying its own subset, each also carrying the shared parameters
  (`start-date`, `end-date`, `pending`, `balances-only`).
- **An id matching no prefix** is dropped from routing, logged, and produces a
  trailing error entry. Do not echo the id into the response body; log it. If
  every requested id is unknown, no provider is queried at all and the
  response is a well-formed empty one.

`fetch_all` therefore takes per-provider parameters. Something like

```python
async def fetch_all(
    clients: Mapping[str, httpx2.AsyncClient],
    requests: Sequence[tuple[Provider, Sequence[tuple[str, str]]]],
    path: str,
    counter: RequestCounter,
) -> list[ProviderResponse]: ...
```

keeping the existing guarantees: one `asyncio.gather`, output order matching
input order.

`ACCOUNTS_FORWARDED_PARAMS` drops `version` and becomes exactly v1's five:
`start-date`, `end-date`, `pending`, `account`, `balances-only`.

`version` is not forwarded, and it is not ignored either: **a request carrying
a `version` value other than exactly `1` is rejected with a 400** and a v1-shaped
body (`{"accounts": [], "errors": [...]}`), after client authentication, before
any provider is contacted. Every value is checked if the parameter is repeated.
This aggregator speaks 1.0 upstream whatever the client asks for, so a client
asking for something else is asking for a protocol this server does not
implement, and answering it in v1 anyway would be a silent lie. Forwarding the
parameter instead would be worse: a provider that honoured it would put v2
shapes into bodies this code merges as v1.

**Settled: accept `1` and `1.0`, and advertise `1.0`.** Read out of the two
published specs. v1 defines no `version` parameter and its `/info` example is
`{"versions": ["1.0"]}`. v2 introduces the parameter as a *major-version
prefix* — "Must be `2` for this version of the protocol. Can be `1` for earlier
versions" — and describes `/info`'s array as "version string prefixes", its own
example being `["1","2"]`. So `1` and `1.0` are two spellings of the one
version this server speaks, and both are accepted; every other value is the
400. Nothing longer is: `1.0.7` names a fix release this application makes no
claim about.

### Merging responses

`merge` becomes a real function of several responses. Its contract:

**Status.** `GET /simplefin/accounts` answers **200, or 403 for a client
authentication failure, and nothing else.** A provider's HTTP status is not
reflected in the aggregator's: 403 means the *client app's* credentials are
bad, and a provider's 403 (access revoked upstream) is a different fact about
a different pair of principals. Provider-side failure is reported in the
`errors` array, which is exactly what v1 provides it for and what a bridge
does when one of its bank connections breaks. This deletes the current
`status=502` path and its tests.

**A provider contributes either a usable body or errors, never both.** A
response is usable when it is HTTP 200 whose body parses as a JSON object
with an `accounts` array of objects each carrying a string `id`, and an
`errors` array of strings if present at all. Anything else — a transport
failure, a 3xx, a 402, a 403, a 500, a body that is not JSON, an `accounts`
entry with no string `id` — is that provider failing, handled uniformly by
the error synthesis below. Being strict here is safe: an account whose id
could not be prefixed must not reach the client unprefixed, because an
unprefixed id misroutes on the way back.

**Order is deterministic and follows the configured provider order**, never
the order the providers happened to answer in.

- `accounts`: each provider's accounts in turn, in configured order, each
  provider's own order preserved within its run.
- `errors`: for each provider in configured order, that provider's own
  `errors` strings passed through verbatim, or — if it failed — the
  synthesized entries described below. Then, at the end, the errors that
  belong to no provider: unknown account ids, id collisions.

**What is preserved.** The byte-identity guarantee is gone, and there is no
single-provider fast path to preserve it: a fast path would leave the merge
code untested in the most common deployment. What replaces it is weaker and
honest, and belongs in `ARCHITECTURE.md`: **every key a provider sent inside
`accounts` is preserved; only `accounts[].id` is modified.** Top-level keys
this aggregator does not speak — `errlist`, `connections`, anything else — are
dropped, because the response is a v1 response built by this application.
Serialize with `ensure_ascii=False` and encode UTF-8, so account names survive
as text rather than escapes.

Note in passing that re-serialization is not byte-preserving for numbers: a
provider writing `1.10` in an `extra` blob gets `1.1` back. Amounts are
strings in v1, so nothing that matters is affected.

### Provider failures the client app can act on

A bare `"provider unreachable"` string is true and useless: the client app
cannot tell the user which of their bank connections stopped working. What
SimpleFIN Bridge emits, and what a client app recognises, names the
institution:

```text
Connection to {name} may need attention. {this application's detail, naming the provider}
```

Producing that for a provider this aggregator cannot currently reach means
remembering what that provider last told us about — which is why this task
introduces a second kind of on-disk state alongside the access URLs.

#### The last-seen accounts

**A new store, `provider_accounts.json`,** in the same configuration directory
as `config.toml` and `provider_creds.json`, holding, per provider key, the
accounts that provider most recently reported, in the order it reported them.

Store per account its `id`, its `name`, and its `org` **verbatim as an opaque
object**. Not the transactions, which are bulk this has no use for, and not
the balances, which would turn a list of "these accounts exist" into a copy of
the user's finances on disk for no gain. Do not model the `org`: this code
reads a display name out of it when it writes an error string and otherwise
carries it as JSON.

Storing accounts rather than a digest of them is deliberate, because the
question of what the error entries should look like is open — see below — and
a store shaped around one answer would have to be rewritten for the other.

- **Updated only from an unfiltered successful response.** A response to a
  request carrying `account` parameters is a subset by construction, and
  writing it back would shrink the remembered set to whatever the client last
  asked about.
- **Held in memory** — read at startup into `_AppState`, updated there — **and
  persisted only when it changes.** It changes when the user adds or removes
  an account at their provider, so in practice this is a write every few
  months, not a write per request. `merge` already parses each usable body, so
  it should hand the accounts it saw back to the route alongside the merged
  response rather than have the route parse anything a second time.
- **Reuse the shared JSON state-file helper** extracted by the app-token task,
  and the locked read-modify-write with it: the CLI probe below writes this
  file too. The write happens on the request path, so it goes through
  `starlette.concurrency.run_in_threadpool` like every other file operation
  there.
- Permissions: 0600, warn if group- or other-readable, like the other two. Its
  risk profile differs from both and is worth a line in `ARCHITECTURE.md`: it
  holds no credentials, and nothing an attacker gains by writing it, but it
  does disclose which institutions and accounts the user has. It is
  disposable — the next successful unfiltered sync rebuilds it.

#### Synthesizing the error entries

> **Open question. Do not settle it from first principles.** Whether a failed
> provider should contribute one entry per remembered account or one per
> distinct institution — and exactly what text a client app matches on —
> depends on what SimpleFIN Bridge actually emits and what Actual Budget
> actually looks for. That is being established empirically and will take
> calendar time. Build the smaller half first: the store above does not depend
> on the answer, and the synthesis is one function over it.
>
> The argument for per-institution is that five accounts at one bank
> are one broken connection, and five near-identical strings are
> noise. The argument for per-account is that it is what the observed
> behaviour may turn out to be. Whichever lands, say in a comment that
> it was determined by observation, not by reasoning, so a later
> reader does not re-derive it and get the other answer. If you need
> to proceed to the absence of the definitive answer, assume it is
> per-account, leave behind a TODO for yourself to revisit at the
> relevant point in the code.

**When a provider has nothing remembered** — never yet reached, or a fresh
install — synthesize one generic entry naming the provider and saying no
accounts have been retrieved from it yet. Most client apps will ignore it.
That is acceptable, and is what the claim-time probe below exists to make
rare.

**Error text carries no URLs.** The detail clause is this application's own
vocabulary keyed by provider key — `"simplefin-aggregator could not reach
provider 'simplefin-bridge' (connection timed out)"` — not `str(exc)` from
httpx2. The merged `errors` array is content this application hands to another
program; keep the exception detail in the log line, where it is useful, and
keep the body's strings stable and free of anything a URL could ride in on.

### The claim probe

`claim` gains a step after storing the access URL: fetch `/accounts` once with
`balances-only=1`, and

1. populate `provider_accounts.json` for that provider, so a provider that is
   down the first time the client app syncs still produces a useful error; and
2. tell the user, at the moment they are set up to act on it, that the
   credentials they just claimed do not actually work.

**A failed probe is a warning, not a failure: report it and exit 0.** The
access URL is stored and valid; the setup token is spent and cannot be
re-claimed. Exiting non-zero would invite the user to re-run a command that
can no longer succeed.

**The probe retries; the claim POST never does.** The probe is an idempotent
GET, so a transient failure there should not cost the user a useful setup
step: three attempts with exponential backoff, an explicit per-attempt
timeout, and the whole thing bounded to roughly thirty seconds so a dead
provider does not turn `claim` into a hang.

**The claim POST is not idempotent and gets exactly one attempt.** If the
request reached the provider, the token may already be spent, and an automatic
retry that comes back 403 has destroyed a credential the user cannot get
again. One attempt, and a failure tells the user to run `claim` again with the
same token: if the token was not consumed that works, and if it was, they get
the provider's "already claimed" answer, which is the truth and is information
they need. A retry loop cannot distinguish those two cases; the user, holding
the provider's web page, can.

Decided: do not pass `retries=0`, as it is the default and adds no value.

**Nothing on the proxied `/accounts` path retries, backs off, or caches.** One
client request produces at most one request per provider, as it does today.
Say so in a comment beside the probe's retry loop, since that is where a
reader will wonder why the two differ.

### `/info`

`GET /simplefin/info` stops proxying and answers `{"versions": ["1.0"]}`
locally.

Two reasons, either sufficient. It describes the protocol version *this*
server speaks to *its* client, which is a fact about this application, not
about the providers. And it is unauthenticated, so proxying it turns one
anonymous request into one request per provider against exactly the
per-provider request budgets that `RequestCounter` exists to watch.

This removes the last caller of `merge` that is not `/accounts`.

## Fallout

- `id_rewriting.py` is deleted; `tests/test_id_rewriting.py` with it.
- `provider_resolution.py` keeps the "which provider owns this id" question
  and gains the answer: `(account_id, providers) -> (Provider, str) | None`,
  returning the owning provider and the provider-local id, or `None` for an id
  no prefix claims.
- `merge.py` is rewritten. `MergedResponse` loses `status` (always 200) or
  keeps it as documentation of that fact — decide in the code, not here — and
  carries the accounts it saw, per provider, back to the route.
- `transport.py`: the `fetch_all` signature above. `fetch` is unchanged.
- `app.py`: `accounts` builds per-provider parameter lists, `info` no longer
  fans out, `_AppState` gains the in-memory last-seen accounts, `create_app`
  gains that store's path.
- `config.py`: `providers` loses `max_length=1`, `Provider` gains `prefix`,
  and the model-level validator gains the key-uniqueness and prefix rules.
- `provider_clients.py` is unchanged: its 30-second timeout is already
  explicit, and with several providers in parallel it is what stops one dead
  provider holding the client app's request open — which now degrades into the
  synthesized-error path rather than into a stall.
- `request_counter.py`, `provider_response.py`: unchanged.
- `tests/support.py`: `make_config` grows a way to build several providers
  with prefixes; most test files construct configs through it.
- `config.toml` and `README.md`: a worked two-provider example, the
  prefix field with its default, and the "set `prefix = \"\"` for the provider
  you already sync from" instruction, which is the one thing an existing user
  must know before upgrading.
- `TODO.md`: drop what this task completes; record two accounts at the same
  provider as still out of scope.
- `docs/ARCHITECTURE.md`: the "identity function" purpose paragraph, the
  non-goals section and the entire "seams for the multi-provider future"
  section describe a version that no longer exists.

## Out of scope

Do not build these.

- **Two accounts at the same provider.** The access URL store keys on the
  provider key, so one configured provider is one account at that provider.
  Supporting more means an instance identifier distinct from the registry key,
  which changes the store's shape and the claim menu. Note it in `TODO.md`;
  do not build it.
- **De-duplicating the same real-world account reached through two
  providers.** It appears twice, with two ids. Nothing in the protocol
  identifies it as one account, and guessing is worse than not.
- **Caching, retrying or coalescing proxied `/accounts` requests.**
- **Per-provider `start-date`/`end-date` rewriting**, partial-response
  assembly, or any other cleverness about what to ask each provider for. The
  client's parameters go to every queried provider unchanged.
- **`errlist`, `connections`, or any other v2 field**, inbound or outbound.
- **Custom currency URL fetching**, as before.
- **A command to refresh `provider_accounts.json`.** Every successful
  unfiltered sync refreshes it.

### If you want to push back

- *"A provider's 403 should surface as a 403."* No — see the status rule.
  403 on this endpoint is a statement about the client app's credentials.
  Reflecting a provider's would tell the client app to re-authenticate against
  the wrong party, and would be ambiguous the moment two providers disagree.
- *"Total provider failure should not be a 200."* It should. The `errors`
  array is the channel v1 gives for this, a non-2xx status stops most client
  apps parsing the body at all, and an empty `accounts` list does not delete
  anything client-side.
- *"Prefix the transaction ids too."* Unnecessary: v1 scopes transaction id
  uniqueness to the account, and account ids are now unique.
- *"Rewrite `org.id`."* No. An org is identified by `domain`/`sfin-url`, which
  are already global; two providers reporting the same institution reporting
  the same org is correct, not a collision.
- *"Make the prefix mandatory / forbid the blank one."* The blank prefix is
  how an existing single-provider user keeps their client app's account links.
  Its hazards are documented above and belong in the README, not in code.
- *"Store the accounts' balances too, or the whole payload."* Nothing needs
  them, and they would turn a list of which accounts exist into a copy of the
  user's finances on disk.
- *"Retry the claim POST."* Answered above: a spent token cannot be
  re-claimed, and no automatic retry can tell a request that failed before
  arriving from one that failed after being processed. The user can.

## Steps

Each step is a review-cycle unit as `AGENTS.md` describes, and lands as one
commit.

**C runs first.** `/info` is a caller of `merge`, and an `/info` body is not an
accounts body: the moment `merge` enforces the usable/unusable rule below,
`{"versions": ["1.0"]}` becomes "that provider failed" and `/info` answers with
an empty account set. Answering `/info` locally is what makes A's claim true
that only a provider's non-200 changes. So the order is **C, A, B, D1, D2, E**;
C depends on nothing and A depends on C.

**A. Merge several responses.** Rewrite `merge` and its tests: concatenation,
prefixing, the deterministic order, the usable/unusable rule, always-200,
`errlist` deleted. Call sites still pass one response, and `Config` still
accepts one provider, so the application's behaviour changes only in that a
provider's non-200 becomes a 200-with-errors. Tested directly against
synthetic response lists, which is where the interesting cases live.

**B. Configure and route several providers.** `Config` opens up, `Provider`
gains `prefix` and the validators, `provider_resolution` learns to split a
prefixed id, `fetch_all` takes per-provider parameters, `app.accounts` builds
them, `id_rewriting.py` is deleted. At the end of this step the application is
genuinely multi-provider.

**C. `/info` answers locally.** Small and separable; it is a behaviour change
worth its own review rather than a footnote to B.

**D1. The last-seen accounts.** `provider_accounts.json` on the shared
state-file helper, loaded at startup into `_AppState`, updated from unfiltered
successful responses, persisted on change, and consumed by `merge` to
synthesize the error entries for a failed provider. The store starts empty on
every existing installation, so the generic nothing-remembered-yet message is
what a first run produces — that is expected here, and D2 is what makes it
rare. The open question above governs only the synthesis; if it is still open
when this step is ready, build whichever shape the evidence so far favours,
mark the test that pins it as pinning behaviour rather than a requirement, and
say in the commit message that it is provisional.

**D2. The claim probe.** `claim` fetches `balances-only=1` after storing the
access URL, populates the store, and warns on failure without failing. It
brings the probe's retry loop with it, and the explicit `retries=0` on the
claim POST, which is a hardening of existing behaviour rather than part of the
probe.

**E. Documentation.** 
- user-facing documentation: `README.md` (the two-provider example,
  prefixes and their permanence, the blank-prefix instruction and its
  hazard), `config.toml`, `scripts/manual_verify.py`
- developer-facing: `TODO.md`, and `docs/ARCHITECTURE.md`: the deleted
  and rewritten modules, the id-namespacing scheme, the routing and
  merging rules, the always-200 contract, `/info` answering locally,
  the `version` rejection, the last-seen accounts as a further piece
  of on-disk state with its own risk profile, and the retry asymmetry
  between the probe and everything else.
- agent-facing: `AGENTS.md` update to reflect any learnings/memories from the
  session. If you make changes here, do not blindly append, synthesize
  an improved file that stands on its own.

## Required tests

Merging and ordering:

- Two providers each returning accounts: the merged `accounts` are in
  configured provider order, each provider's own order preserved, every id
  carrying its provider's prefix.
- Each provider's own `errors` strings appear in configured provider order,
  and aggregator-level errors come after all of them.
- A provider returning 200 with an unparseable body, an `accounts` entry with
  no string `id`, a 402, a 403, a 3xx, and a transport failure: each is
  treated identically as that provider failing, and the other provider's
  accounts still come through with status 200.
- Every provider failing: status 200, empty `accounts`, populated `errors`.
- Unknown keys inside an account survive the round trip; `errlist` and
  `connections` sent by a provider do not appear in the response.

Configuration:

- Default prefixes are `key + ":"`; an explicit prefix overrides.
- Rejected: duplicate keys; two blank prefixes; a non-blank prefix that is a
  prefix of another. Accepted: one blank prefix alongside distinct non-blank
  ones.

Routing:

- No `account` parameter: every provider is queried, none receives an
  `account` parameter.
- `account` ids spanning two providers: two concurrent requests, each carrying
  only its own ids and both carrying the shared parameters.
- An id belonging to one provider does not reach the other.
- An unknown id: not routed anywhere, produces a trailing error, and does not
  appear in the response body.
- All ids unknown: no provider is queried, response is 200 with empty
  accounts.
- A blank-prefix provider and another provider producing the same merged id:
  both accounts are returned and a collision error is reported.

The last-seen accounts and the probe:

- An unfiltered successful response records each account's `id`, `name` and
  `org`, in the order the provider reported them, and records no transactions
  or balances; a filtered response does not touch the store.
- The file is written only when the recorded accounts change.
- A provider failing after a successful sync produces
  `Connection to … may need attention` entries derived from what was
  remembered, naming the provider in the detail clause.
- A provider failing with nothing remembered produces the generic entry.
- `claim` populates the store from the probe; a failing probe warns, exits 0,
  and leaves the stored access URL in place.
- The claim POST is attempted exactly once, whatever the failure, and the
  message tells the user to re-run `claim` with the same token.
- The proxied `/accounts` path issues exactly one request per queried provider
  even when a provider fails.

Protocol version:

- `version=1` is accepted and not forwarded to any provider; any other
  `version` value, including one among several, is a 400 with a v1-shaped
  error body and no provider request.

`/info`:

- Answers `{"versions": ["1.0"]}` and issues no provider requests.

Secrets, as ever:

- Across a full two-provider claim → sync flow, no captured log output,
  stdout, stderr, response body or exception message contains a provider
  access URL, its password, or a setup token.
