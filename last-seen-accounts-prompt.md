# Task: tell the client app which connections need attention

When a provider fails, this aggregator answers 200 with that provider's
accounts simply missing, and says nothing about why. From the client app's
side the user's accounts have gone quiet with no explanation, and the user is
not told which of their bank connections needs their attention. Close that
gap.

`AGENTS.md` governs how to work: the verification pass, non-vacuous tests, the
review cycle, comment and commit discipline, secrets discipline. Read it first
and follow it; nothing about how to work is repeated here.

Read `docs/ARCHITECTURE.md` for the map of the code. It is current as of the
start of this work; each step brings it back into line for what that step
lands.

## What is already built

This application implements **SimpleFIN protocol v1**
(<https://www.simplefin.org/protocol-v1.html>), in both directions: v1 to the
client app, v1 to the providers. Four facts about the code this task starts
from:

- **v1's only error channel is `errors: [string]`.** `errlist` and
  `connections` are v2.0.0 additions; this application neither reads nor
  writes them.
- **`GET /simplefin/accounts` answers 200, or 403 for a client authentication
  failure, and nothing else.** A provider's own HTTP status is never reflected
  in this server's: 403 here is a statement about the client app's
  credentials, and a provider's is about a different pair of principals.
- **`merge` decides, per provider, whether a response is usable**: HTTP 200
  whose body parses as a JSON object with an `accounts` array of objects each
  carrying a string `id`. Anything else — a transport failure, a 3xx, a 402, a
  403, a 500, a body that is not JSON — is that provider failing. A failure is
  logged and contributes nothing to the merged body: no accounts, and no
  `errors` entry either, because a bare string saying a provider could not be
  reached names nothing a client app can act on.
- **Order follows the configured provider order**, never the order the
  providers answered in: each provider's accounts in turn, and then, in the
  same order, each provider's own `errors` strings passed through verbatim.

The third of those is what this task changes. A failed provider gains a voice
in `errors`, and it takes its place in the order the fourth describes.

## The design

### What the entries say

A bare `"provider unreachable"` string is true and useless: the client app
cannot tell the user which of their bank connections stopped working. What
SimpleFIN Bridge emits, and what a client app recognises, names the
institution:

```text
Connection to {account name} may need attention. {this application's detail, naming the provider}
```

Producing that for a provider this aggregator cannot currently reach means
remembering what that provider last told us about — which is why this task
introduces a third state file alongside the access URLs and the app tokens.

### The last-seen accounts

**A new store, `provider_accounts.json`,** in the same configuration directory
as `config.toml` and `provider_creds.json`, holding, per provider key, the
accounts that provider most recently reported, in the order it reported them.

Store per account its `id`, its `name`, and its `org` **verbatim as an opaque
object**. Not the transactions, which are bulk this has no use for, and not
the balances, which would turn a list of "these accounts exist" into a copy of
the user's finances on disk for no gain. Do not model the `org`: this code
reads a display name out of it when it writes an error string and otherwise
carries it as JSON.

Storing accounts rather than a digest of them is deliberate: an error entry
names the account it is about, so the names have to survive in the store. A
digest would answer only whether the set had changed, which is the other thing
this store is for and not the harder one.

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
- **Reuse the shared JSON state-file helper** in `state_file.py`, and the
  locked read-modify-write with it: `claim`'s probe writes this file too. The
  write happens on the request path, so it goes through
  `starlette.concurrency.run_in_threadpool` like every other file operation
  there.
- Permissions: 0600, warn if group- or other-readable, like the other two. Its
  risk profile differs from both and is worth a line in `ARCHITECTURE.md`: it
  holds no credentials, and nothing an attacker gains by writing it, but it
  does disclose which institutions and accounts the user has. It is
  disposable — the next successful unfiltered sync rebuilds it.

### Synthesizing the entries

**One entry per remembered account**, whatever institution it belongs to: five
accounts at one bank produce five entries. That is a requirement, so pin it as
one — and say in a comment where the entries are built that per-account is the
settled rule, or a later reader will re-derive per-institution and get the
other answer.

**When a provider has nothing remembered** — never yet reached, or a fresh
install — do not produce any error entries for the provider, only issue a log
line naming the provider and saying no accounts have been retrieved from it yet.
That is acceptable, and is what the claim probe makes rare.

**Error text carries no URLs.** The detail clause is this application's own
vocabulary keyed by provider key — `"simplefin-aggregator could not reach
provider 'simplefin-bridge' (connection timed out)"` — not `str(exc)` from
httpx2. The merged `errors` array is content this application hands to another
program; keep the exception detail in the log line, where it is useful, and
keep the body's strings stable and free of anything a URL could ride in on.

**The entries take the failed provider's place in the configured order**, so
`errors` still reads provider by provider: a provider contributes either its
own strings or its synthesized ones, never both, because a provider whose
response was usable is not a provider that failed.

### The claim probe populates the store

`claim` already fetches `/accounts` once with `balances-only=1` after storing
the access URL, to tell the user, at the moment they are set up to act on it,
if the credentials they just claimed do not actually work. It gains a second
purpose: populating `provider_accounts.json` for that provider, so a provider
that is down the first time the client app syncs still produces a useful
error.

A failed probe stays a warning rather than a failure, and exits 0, for the
reason it already does: the access URL is stored and valid, and the setup
token is spent and cannot be re-claimed.

## Fallout

- `provider_accounts.json` wants a module of its own beside
  `provider_access_urls.py` — schema and semantics only, the file handling
  being `state_file.py`'s — and a path helper beside the other two stores'.
- `merge.py`: a failed provider is no longer a `continue`. `merge` needs the
  remembered accounts to build entries from, and `MergedResponse` carries the
  accounts it saw, per provider, back to the route.
- `app.py`: `_AppState` gains the in-memory last-seen accounts, `create_app`
  gains that store's path, and the `/accounts` route persists the store when
  an unfiltered response changed it.
- `cli.py`: the probe writes what it fetched.
- `tests/support.py`: the fixtures that build an app gain the new store.
- `docs/ARCHITECTURE.md`: the on-disk state table and the asymmetry paragraph
  under it, `_AppState`, `MergedResponse`, and the `/accounts` flow.
- `README.md`: a third file in the configuration directory, what it holds and
  what it discloses, and that deleting it costs nothing.

## Out of scope

Do not build these.

- **A command to refresh `provider_accounts.json`.** Every successful
  unfiltered sync refreshes it.
- **Reporting a provider failure by any channel other than `errors`.** Not the
  HTTP status, and not `errlist` or `connections`, which are v2 fields this
  application neither reads nor writes.
- **Remembering anything else a provider said.** Balances and transactions are
  fetched, merged, and forgotten.

### If you want to push back

- *"Store the accounts' balances too, or the whole payload."* Nothing needs
  them, and they would turn a list of which accounts exist into a copy of the
  user's finances on disk.
- *"One entry per institution, not one per account."* Per-account is the
  settled rule. A client app shows the user the accounts that are not syncing,
  and an entry that stands for five of them at once is an entry the user has to
  expand themselves.
- *"A provider with nothing remembered should get a generic entry."* No: a
  generic entry is the bare string this task exists to replace. Nothing
  remembered means nothing to say to the client app, and the log line is for
  the operator, who can act on it.
- *"A provider's failure should surface as a non-200."* No. The `errors` array
  is the channel v1 gives for this, a non-2xx status stops most client apps
  parsing the body at all, and an empty `accounts` list does not delete
  anything client-side.

## Steps

Each step is a review-cycle unit as `AGENTS.md` describes, and lands as one
commit.

**A. The store, and the entries built from it.** `provider_accounts.json` on
the shared state-file helper, loaded at startup into `_AppState`, updated from
unfiltered successful responses, persisted on change, and consumed by `merge`
to synthesize the entries for a failed provider. The store starts empty on
every existing installation, so a provider failing before it has ever been
reached contributes nothing to the body and one line to the log — that is
expected here, and B is what makes it rare.

**B. The probe populates the store.** `claim`'s existing `balances-only=1`
fetch writes what it got.

**C. Documentation.** `README.md` and `docs/ARCHITECTURE.md` for what the two
steps landed, and `docs/TODO.md`, which records this gap and should stop.

## Required tests

The store:

- An unfiltered successful response records each account's `id`, `name` and
  `org`, in the order the provider reported them, and records no transactions
  or balances; a filtered response does not touch the store.
- The file is written only when the recorded accounts change.
- `claim` populates the store from the probe.

The entries:

- A provider failing after a successful sync produces
  `Connection to … may need attention` entries derived from what was
  remembered, naming the provider in the detail clause: one per remembered
  account, so two accounts at one institution produce two entries.
- A provider failing with nothing remembered produces no entries at all, and a
  log line naming the provider.
- With one provider failing and another answering with its own `errors`, the
  merged `errors` reads in configured provider order, the synthesized entries
  in the failed provider's place.

Secrets, as ever:

- A provider access URL, its password, and a setup token reach neither
  `provider_accounts.json` nor any synthesized error string, across a full
  claim → failing-sync flow.
- No synthesized error string contains a URL or an exception's text, however
  the provider failed.
