# Task: one-time-use claims and app token management

Make this aggregator's own claim endpoint conform to SimpleFIN protocol v1,
and give the operator three commands to manage the tokens it issues.

`AGENTS.md` governs how to work: the verification pass, non-vacuous tests, the
review cycle, comment and commit discipline, secrets discipline, etc.. Read it first
and follow it; nothing about how to work is repeated here.

Read `docs/ARCHITECTURE.md` for the map of the code. It is current as of the start of the work, and this task invalidates parts of it —
the final step brings it back into line.

## What is changing and why

Today `POST /simplefin/claim/{token}` compares the pasted token against a
constant `claim_token` in `config.toml` and returns an access URL built from a
single `[client]` username and password, also in `config.toml`. The same token
works forever, every client app shares one credential, and revoking means
editing the config and restarting the server. `README.md` documents this as
"the repeatable-claim deviation".

The spec is unambiguous about the two rules being broken. On
`POST /claim/:token`:

> `:token` — A one-time use code embedded within the SimpleFIN Token.

> `403` — The claim token either does not exist or has already been used
> claimed by someone else.

and on `GET /accounts`:

> `403` — Authentication failed. This could be because access has been revoked
> or if the credentials are incorrect.

So: a setup token is spent by the claim that succeeds; a second claim of it is
indistinguishable from a claim of a token that never existed; and revocation
is an out-of-band operator action whose only protocol-visible effect is that
`/accounts` starts answering 403.

That last point is worth stating plainly because it shapes the whole design:
**the protocol has no revocation endpoint**. Revocation is a CLI command, and
what makes it work is that the server consults live state rather than a
startup snapshot.

`GET /create` — the browser flow that mints a token — is still not
implemented, and is not part of this task. `app new` is this
application's equivalent, which is the right shape for a single-user
server on loopback. The README is a user guide to what *is*
implemented, not what isn't, so it should not discuss the absence of
`GET /create` at all.

**This software has not been released.** There are no backward-compatibility
or migration concerns anywhere in this task. `claim_token` and `[client]` are
deleted from the config schema outright; no legacy acceptance, no migration
error, no deprecation path.

## The design

### The store holds no credentials, only digests

Everything this feature persists is *verified*, never *reproduced*:

- the claim secret is printed once by `app new` and thereafter only checked;
- the username and password are emitted once, inside the access URL the claim
  returns, and thereafter only checked;
- the aggregator never rebuilds an access URL — the client app stores it, as
  the spec requires of it.

So store SHA-256 hex digests and nothing else. This is what makes "never
display or log a credential, not even the generated username" structural
rather than a matter of discipline: there is no plaintext in the file for
`app list` to leak, for a stray `repr` to print, or for a reader of the file
to use. It is the same move as `SecretStr` redacting by construction, taken
one step further.

Plain SHA-256, no salt, no KDF. These are 256-bit `secrets.token_urlsafe(32)`
values, not user-chosen passwords; a slow KDF defends against a dictionary
search that cannot happen here, and would add a dependency. Compare with
`secrets.compare_digest`.

One consequence to carry into the docs: for this file **integrity matters more
than confidentiality**. Reading it gains an attacker nothing; writing it lets
them insert their own digests and gain access. The permission warning stays,
and covers both directions, but its rationale is not the one that applies to
`provider_creds.json`.

A second consequence: unlike `provider_creds.json`, this file *is*
disposable.  Losing it costs a fresh token per client app, not a fresh
token per provider.  Do not carry the "cannot be regenerated" warning
over to it. Think twice before bothering to mention disposability in
docs or comments: in most contexts it is uninteresting, and if you do
mention it, keep it brief, in keeping with the instructions in
@AGENTS.md.

### Record shape

A new store, `app_tokens.json`, in the same configuration directory as
`config.toml` and `provider_creds.json`, keyed by the app's key:

```json
{
  "apps": {
    "actual-budget": {
      "status": "unclaimed",
      "label": "Actual Budget",
      "created_at": "…",
      "claim_token_sha256": "…"
    },
    "beancount": {
      "status": "claimed",
      "label": "Beancount importer",
      "created_at": "…",
      "claimed_at": "…",
      "username_sha256": "…",
      "password_sha256": "…"
    }
  }
}
```

Two states, discriminated on `status`, narrowed by `isinstance` the way
`ProviderSuccess | ProviderFailure` already is in `provider_response.py`. A
successful claim *replaces* the unclaimed variant with the claimed one, so the
claim digest is gone rather than marked spent. A replayed token then fails the
same lookup an unknown token fails, by the same code path — which is the
spec's indistinguishability requirement, obtained for free rather than
defended by a branch.

Nothing in this model is a `SecretStr`, because nothing in it is a secret.

Timestamps: `datetime`, UTC, serialized as ISO-8601 by pydantic. Do not build a
clock seam for tests; assert the fields round-trip and move on.

### Concurrency

Four writers read-modify-write this file: `app new`, `app revoke`,
`app regen`, and the server's claim handler. In practice this is one person
who does not run two commands at once, but the server is a genuine source of
parallelism today and more so if anyone ever gives it internal concurrency.

- **Writers take an exclusive `fcntl.flock`** on a sidecar lock file in the
  same directory, held across the whole read-modify-write. Lock a sidecar, not
  the store itself: the atomic write replaces the file, so a lock held on the
  store's inode does not guard the file that ends up at that path.
- **The lock belongs to the shared state-file helper, not to this store**, so
  that it covers `provider_creds.json` too, and each store gets its own
  sidecar beside it. `save_access_url` is itself an unlocked read-modify-write:
  two `claim` processes at once each save a different provider and the second
  replace drops the first, losing an access URL whose one-time setup token has
  already been spent -- the one loss this application cannot undo. Step A
  extracts the helper precisely because both stores turn out to have identical
  file-handling requirements; locking is one more of them, so it is written
  once rather than twice.
- **Readers take no lock.** The existing write-then-rename means a reader sees
  the old file or the new one, never a torn one.
- `fcntl` is POSIX-only. That is fine and already true of this application
  (mode bits, `os.access`, `chmod 600`, the Dockerfile). Import it
  unconditionally; do not write a Windows fallback, and do not write a silent
  no-op lock, which is worse than no lock at all. Note the POSIX requirement
  once, briefly, in the README.
- The claim handler's write blocks and calls `fsync`, and may block on the
  lock. Run the whole read-modify-write through
  `starlette.concurrency.run_in_threadpool`, and the auth path's read too, so
  that the rule is simply "no file I/O on the event loop".

### Durability

`save_access_urls` already flushes, `fsync`s and renames. That is not
over-caution, but it is half a guarantee: it `fsync`s the file and not the
directory, so it defends against a rename that lands on an empty file while
leaving a lost rename open. Do both in the extracted helper, or neither. The
current middle is the one position that cannot be argued for.

Do both. The rename is what buys *consistency*, and it is free — a reader sees
the old file or the new one, never a torn one, with no `fsync` involved.
`fsync` buys *durability*: surviving power loss or a kernel panic, not a
process crash, for which `close` already suffices. For this store that
durability is load-bearing in a way it is not for `provider_creds.json`.
"Persist, then respond" is what stops a client app walking away with
credentials the server does not recognise, and without the `fsync` that rule is
only page-cache deep — the machine can lose power after the 200 and come back
with no record of the app. It is also why the write stays in the request path.

The directory `fsync` is the one half that is best-effort. It happens after
the rename, by which point the new file is in place and every reader already
sees it, so a failure there is a weaker durability guarantee than was asked
for and not a failed save -- and raising would tell `claim` it had lost a
credential it did in fact store, which is the more expensive wrong answer. It
is reachable without an exotic filesystem: a config directory that is writable
but not readable takes every write the store makes and still refuses the
read-only open this `fsync` needs.

The cost is a syscall on writes that happen when a human runs a command or an
app is claimed, never per request.

Two things to know before reading the ext4 lore and concluding otherwise: the
delayed-allocation heuristic that forces blocks out on a rename over an
existing file does not apply when there is no existing file, which is exactly
the first `app new` on a new machine; and `fsync` is only as good as the
drive's write cache, so this goes as far as asking the kernel and no further.

### The commands

```
simplefin-aggregator app new    --key <key> --label <label>
simplefin-aggregator app list
simplefin-aggregator app revoke --key <key>
simplefin-aggregator app regen  --key <key>
```

All four take `--config-dir`, like every other subcommand. `gen-token` is
deleted; `app new` replaces it.

- **`app new`** requires both options. `--key` must match `[a-z0-9-]+` — reuse
  the constraint `ProviderEntry` already applies, rather than writing a second
  one. A key that already exists is an error naming `app regen` as the way to
  reissue; it is never silently suffixed or overwritten. Prints the base64
  setup token on **stdout and nothing else**, so `$(...)` capture works, with
  the "this is the only time it is shown" note on stderr.
- **`app list`** takes no `--key` and prints key, label, status and
  timestamps. It cannot print a credential, because the store holds none.
- **`app revoke`** deletes the record outright — an unclaimed token or a live
  credential alike. No tombstone: there is no audit requirement, and reusing
  the key later is harmless because nothing else references it. An unknown key
  is an error and exit 1, never a no-op that reports success.
- **`app regen`** is revoke-and-reissue in one locked update, preserving the
  key and the label: any existing credentials stop working immediately, any
  unspent token is discarded, the record returns to `unclaimed`, and a fresh
  setup token is printed exactly as `app new` prints one. It is the only way
  to reuse a key, which is why it exists beyond convenience. Do not build a
  variant that keeps the old credentials alive until the new token is claimed:
  that would let one app hold live credentials and an unspent token at once,
  which breaks the two-state record and makes "revoked" mean two things.

The key is an option rather than a positional on all of them because a
consistent surface is worth more here than brevity. See `TODO.md` for the
out-of-scope kindness of also accepting a label; do not build it now.

### Server-side

- **The claim route** hashes the token from the path, compares against each
  unclaimed record with `compare_digest`, and 403s on no match. On a match it
  generates a username and a password with `secrets.token_urlsafe(32)`,
  **persists the claimed record, and only then responds**. That order is the
  one that matters: crashing after the write costs a wasted token, which the
  operator fixes with `app regen`; crashing after the response leaves the
  client app holding credentials the server does not recognize, which looks
  like a working setup that silently never syncs.
  `token_urlsafe` emits `[A-Za-z0-9_-]`, which needs no percent-encoding in
  userinfo — but keep `build_access_url`'s existing `quote`, which is then a
  no-op that stays correct if the generator ever changes.
- **`require_client_auth` reads the store on every request.** A revocation
  that needed a server restart would not be a revocation. This is a few-KB
  JSON read on a loopback server that a client app polls a few times a day;
  caching it would trade the property that makes revoke work for a saving
  nobody can measure. Do not add a cache, an mtime check, or a reload signal.
- That introduces a distinction `ARCHITECTURE.md` currently states the
  opposite of, and the final step must record it: **`Config` remains read-only
  and snapshot-at-startup; the app token store is live state, read per
  request.** There is still no config hot-reload.
- `require_client_auth` needs the store's path. Build it as a dependency
  factory taking that path and call it from `create_app`, rather than adding a
  second untyped `app.state` attribute for it to `cast` — that removes a
  `cast`, it does not add one. `create_app` gains the path as a parameter.
- **`serve` checks at startup** that the store parses and that its directory is
  writable, failing before uvicorn starts rather than on the claim that
  discovers it. A store with no apps in it is a warning, not a failure: it is
  the legitimate state between `serve` and the first `app new`.

### Fallout

- `config.toml` is left with **no credentials at all**: `bind_host`,
  `bind_port`, `base_url`, `[[providers]]`, `[[custom_providers]]`. Delete
  `claim_token`, its validator, and the whole `ClientAuth` model.
- `warn_if_permissive` applies to all three files this application manages,
  `app_tokens.json` included, and still warns on any group or other bit. Only
  its message changes: drop the "it contains credentials" clause, which stops
  being true of `config.toml`, and keep the remedy — the path, what is set on
  it, and `chmod 600`. Do not parameterise the check by which risk applies to
  which file. The real rule is asymmetric — modification matters for all three,
  and a writable `config.toml` can move `bind_host` off loopback, while
  disclosure only really matters for `provider_creds.json` — and that
  asymmetry belongs in `ARCHITECTURE.md`, not in a flag on a helper or a
  warning string hedging about which case it is in. Do not warn about the lock
  file, which holds nothing.
- `build_setup_token` and `build_access_url` currently take a whole `Config`
  to read two fields out of it. They become functions of `base_url` and the
  secret(s) they embed.
- `tests/support.py`'s `make_config` grows a store fixture and loses the
  credential arguments; most test files construct configs through it.
- `scripts/manual_verify.sh` uses the static config credentials and must now
  `app new`, POST the claim itself, and use what comes back. That is a little
  more script for a test that exercises the real flow.
- The README's Docker section mounts the config directory `:ro` for `serve`.
  The server now writes on claim, so that must go, with a sentence saying why.
- `TODO.md`: drop the entry this task completes, and add to the surviving
  `claim --provider` entry that `app --key` is the other place where accepting
  a label would be kind.

## Out of scope

Do not build these.

- **Expiry.** An unclaimed setup token stays valid until claimed or revoked.
  `app list` shows its age; `app revoke` removes it.
- **`last_used_at`,** or any other per-request write. It would make `list`
  more informative at the cost of a write on every authenticated request.
- **Rate limiting or lockout** on the claim endpoint. The token is 256 bits
  and the endpoint is on loopback.
- **An audit log** of issue and revoke events.
- **Windows support** for the lock.
- **Changing `/simplefin/info`,** which stays unauthenticated, and the shape of
  the 403 body, which stays as it is.

### If you want to push back

- *"Hash the secrets with argon2/bcrypt."* No: these are 256-bit random
  values, not user-chosen passwords. A KDF defends against a search that
  cannot succeed here.
- *"Reading the store on every request is wasteful."* It is what makes
  `app revoke` take effect without a restart, on a server handling a handful
  of requests a day. Say so in a comment; do not cache.
- *"`app regen` should keep the old credentials working until the new token is
  claimed."* Answered above: it breaks the two-state record.
- *"The store should be encrypted at rest."* It holds only digests of
  256-bit secrets. There is nothing in it to protect.
- *"The `fsync` is over-cautious / belongs off the request path."* Answered
  under Durability. And do not grow a harness to prove the `fsync` happens:
  stating what it buys in a comment is the proportionate check for a syscall.
- *"403 should say whether the token existed."* The spec deliberately
  conflates the two, and the design conflates them by construction.

## Steps

Each step is a review-cycle unit as `AGENTS.md` describes, and lands as one
commit.

**A. Extract the shared JSON state-file helper.** `provider_access_urls.py`
holds ~70 lines of hard-won file handling — utf-8 read, missing-file-is-empty,
the never-`str()`-a-`ValidationError` error path, `warn_if_permissive`,
`mkstemp` at 0600, `fsync` before `replace`, unlink-on-any-failure — that the
new store needs identically. Add the directory `fsync` after the rename here,
where it is written once for both stores. Move it to a module of its own with a shared
error type replacing `AccessUrlStoreError`, and leave the two stores holding
only their own schema and semantics. Two callers with identical requirements
is when extraction is warranted; this is not a framework, it is two functions.
Mechanical, no behaviour change, tests unchanged except for the renamed error.

**B. The app token store.** The new module: the two record variants, the
digest helpers, the constructors that mint a setup token secret and that spend
one for credentials, load, and the flock'd read-modify-write (which goes in
the shared helper -- see Concurrency). The store owns record construction, so
that the timestamp and the digesting live in one place instead of being
assembled inline by both `cli.py` and the claim route, which are the two
places where getting it wrong writes a plaintext credential to disk. Nothing
uses it yet; it is tested on its own.

**C1. The `app` commands.** `cli.py` gains the four commands on top of step
B's store. Nothing else changes: `config.toml` keeps `claim_token` and
`[client]`, the claim route and `require_client_auth` keep using them, and
`gen-token` is still there. The application works exactly as it did and
additionally issues setup tokens that nothing yet honours. State that gap in
the commit message and when handing the step over — it is what the split is
for, not an oversight — and do not paper over it by half-wiring the server.

**C2. The server switchover.** The claim route consumes a one-time token and
issues credentials, `require_client_auth` reads the store live, `serve` gains
its startup checks, `config.py` drops `claim_token` and `ClientAuth`, and
`gen-token` goes. This one does not split further: the moment the config loses
its credentials the old claim path cannot work.

**D. Documentation.** 
- `README.md` as a user guide — the `app` commands, the
  config file with no credentials in it, the Docker mount, the POSIX note, and
  the deletion of the repeatable-claim section, which is no longer a deviation.
`config.example.toml`, `scripts/manual_verify.sh`
- developer-facing documentation: `TODO.md`, and
  `docs/ARCHITECTURE.md`: the new modules, the two-state record, the
  live-store versus snapshot-config distinction, the claim and
  `/accounts` flows, the locking, and the app token store as a third
  piece of on-disk state whose integrity matters more than its
  confidentiality.
- agent-facing: `AGENTS.md` update to reflect any learnings/memories from the
  session. If you make changes here, do not blindly append, synthesize
  an improved file that stands on its own.

## Required tests

- `app new` writes an unclaimed record; stdout holds the base64 token and
  nothing else; the token decodes to `<base_url>/simplefin/claim/<secret>`;
  the secret itself appears nowhere in the store file.
- `app new` with an existing key fails, names `app regen`, and leaves the
  store byte-identical.
- `app list` shows key, label and status, and contains none of: the claim
  secret, the username, the password, any digest.
- `app revoke` removes the record; an unknown key exits non-zero and leaves
  the store unchanged.
- `app regen` on a claimed app: the old credentials stop authenticating, a new
  setup token is printed, the label survives, the record is unclaimed again.
- Claiming a valid token returns 200 and an access URL whose credentials
  authenticate `/accounts`; the record is then claimed.
- Claiming the same token a second time returns 403, with the same status and
  body as claiming a token that never existed.
- A claim whose store write fails leaves the record unclaimed and does not
  return an access URL.
- Revoking while the server is running: the next `/accounts` with those
  credentials gets 403, with no restart. Two apps: revoking one leaves the
  other working.
- Wrong password, and a well-formed username that belongs to no app, both get
  403.
- `serve` starts with an empty store and answers 403; it refuses to start when
  the store is malformed or its directory is not writable.
- Across a full `app new` → claim → `/accounts` → `app list` flow, no captured
  log output, stdout, stderr or exception message contains the generated
  username, the generated password, or the claim secret. This is the test the
  whole digest-only design exists to make easy — it should read as a
  confirmation, not as a defence.
