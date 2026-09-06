# Task: harden SimpleFIN token claiming against phishing (steps 4b-9)

This continues work that is partly done. **Steps 1 through 7a are complete
and committed**; read them below for context but do not redo them. **Steps 7b,
7c and 9 are not started**; step 8 is deliberately blank.

This app is a client of SimpleFIN protocol v1
(<https://www.simplefin.org/protocol-v1.html>).

Read `docs/ARCHITECTURE.md` first for a map of the existing code. It is short
(about 1340 lines across `src/*/*.py`), so reading the whole thing is
reasonable.

**`ARCHITECTURE.md` does not reflect the current code and must not be trusted
as a description of it.** It predates steps 1-4b: its module map lists none of
`url_validation.py`, `provider_registry.py` or `provider_access_urls.py`; its
`Config`/`Provider` block still shows `name` and `access_url`; and its `serve`
flow predates the access URL store. Trust it for what this task has not
touched — the concurrency model, the multi-provider seams, the testing
conventions — and read the code for the rest. Step 7c brings it up to date,
in one pass, at the end; do not patch it mid-task, or it gets written five
times from five partial states instead of once from the finished one.

**Read the threat model before implementing anything.** Several requirements
look wrong if you assume a different threat model than the one stated.

## How to work

- **Work in steps.** Steps are numbered below and are meant to be done in
  order. **Pause after each step** and run this review cycle before starting
  the next.

  **Two points in this cycle are hard stops: you present, you end your turn,
  and you do nothing further until the human replies.** They are marked STOP
  below. Nothing else in this document authorizes you to pass one, and no
  amount of confidence that the work is finished substitutes for the reply.
  A turn that presents work and then keeps acting has not stopped, whatever
  it said while presenting.

  1. **STOP — human review and discussion.** Present what changed and why,
     then **end your turn.** Do not run an AI reviewer. Do not squash. Do not
     start the next step. Do not "get the reviews started while the human
     reads" -- that is the exact failure this gate exists to prevent, and it
     has happened. The human's next message is the only thing that releases
     the gate.

     Two reasons, either sufficient. The human's review routinely changes what
     is worth reviewing -- scope, direction, whole approaches -- so reviews
     launched first are spent on a version that is about to change. And the
     reviewers are a rationed resource: the CodeRabbit CLI allows three
     reviews per window, and a step that spends them before the human has
     spoken has none left for the version that matters.
  2. **CodeRabbit, in both modes that proved fruitful.** Only once the gate
     above has been released. The repo's
     `.coderabbit.yaml` sets `profile: assertive` (the default `chill`
     suppresses lower-confidence findings) and carries `path_instructions`
     with the threat model and the out-of-scope list, which stops it
     proposing SSRF defenses. Run both:
     - `coderabbit review --agent -t committed --base <commit before the step>`
       -- note `-t all` does **not** pick up working-tree changes, so commit
       first or the review reads stale code.
     - `/code-review high <range>` -- state the range explicitly, e.g.
       "review the range A..B, not commit A itself". Given a bare commit-ish
       it will review that commit rather than the range ending at it.
     Pass this document as *instructions* rather than as reviewed content:
     `-c docs/claim-hardening-continuation.md`. Committing it into the diff
     instead just produces a finding telling you not to commit it.
  3. **Describe every point raised and its disposition.** One line each:
     what was claimed, whether it is true (verify it against the code --
     both reviewers produced confident findings that were wrong), and fixed
     / rejected-with-reason / deferred-to-step-N. Do not relay a finding
     without checking it, and do not quietly drop one.
  4. **Give each reviewer a chance to answer a rejection.** Where you
     disagree with a finding, put the reasoning back to *that* reviewer and
     let it respond before you call the matter closed -- a disagreement is
     settled between you and the reviewer, not decided unilaterally and
     reported to the human as a fait accompli. For the CLI, re-run its review
     with the rebuttal supplied as instructions; for an agent-shaped reviewer,
     reply to the same agent so it keeps the context of its own finding. A
     reviewer that concedes on the second pass was wrong; one that holds its
     position with a new argument may be right, so read it before deciding.
     Report the exchange, not just your original verdict.
  5. **STOP — re-review with the human**, covering the fixes and the
     rejections. Present, end your turn, wait. Squashing is step 6 and belongs
     to the human's reply, not to your judgment that the findings are all
     handled.
  6. **Rebase the step into one commit — but not before the human has seen
     what the review changed.** The one-commit-per-step rule describes a step
     **once complete**, not how you work inside it. A step in progress is like
     a PR: its branch accumulates as many commits as the work takes, and they
     are squashed when it lands. Commit freely while iterating — a review tool
     reads committed code, so fixes have to be committed to be re-reviewed at
     all.

     Squashing them destroys the diff between what you wrote and what the
     reviewers talked you into, which is exactly what the human wants to
     review at step 5 — the disposition list says what you *claim* changed,
     the diff says what did. So: **at step 5, hand over that delta as
     something reviewable**, not just prose. `git diff <pre-review commit>
     <current>` is the minimum; a commit whose parent is the pre-review state
     and whose tree is the current one is better, because it can be read,
     commented on and fed back to a reviewer like any other commit:
     `git branch <name> $(git commit-tree <current>^{tree} -p <pre-review> -m ...)`.
     Squash only once the human has signed off. Keep the pre-review commit
     reachable from a tag or branch until then, or `git reset --soft` will
     leave it findable only through the reflog.

     After sign-off, squash so the step is exactly one commit on top of the
     previous step's tip, with a message describing the step rather than the
     review iterations. History should read as one commit per step.
- **Follow step 9's rules now, don't wait for `AGENTS.md` to exist.** The bullets
  listed under step 9 are there because they are the durable ones -- the
  verification pass, non-vacuous tests, no real network, the review cycle,
  terminology, comment discipline, secrets discipline. They are the standard
  this work is held to from the first step; step 9 only moves them into the
  repository so they outlive this document.
- **Keep step 7's instructions current as you go.** Step 7a rewrites the
  README and step 7c rewrites `ARCHITECTURE.md`, each in one pass, which only
  works if they know what to say. Whenever a step changes something a reader of those documents
  would need — a new module, a changed data structure, a flow that now works
  differently, a limitation worth recording — add it to step 7 before closing
  the step. The list there is the accumulated record of what those documents
  are missing.
- **Write non-vacuous tests as you go.** Where feasible, demonstrate a test is
  non-vacuous by mutating the code under test to provoke a failure, confirming
  the test catches it, then restoring the code. Do not just check that a new
  test passes once.
- **Verification pass after every step**, all four, every time:
  `uv run ruff format --check .`, `uv run ruff check .`,
  `uv run basedpyright`, `uv run pytest`. A subset is not sufficient.
  basedpyright runs a zero-warning policy: fix each warning, or add an
  explicit `# pyright: ignore[rule]` with a one-line reason.
- **Treat this document as ephemeral.** It is not committed and code must not
  reference it. Any rationale here that a future reader of the code would need
  belongs in a comment or docstring *in situ*, next to the code it explains.
  `ARCHITECTURE.md` will be updated separately in step 7c based on this design;
  do not provide excessive rationale in the code when the explanation in the
  revised ARCHITECTURE.md will suffice.
- **More code means more bugs.** Prefer the smaller design. Do not build
  framework-shaped abstractions for a single caller. Where a cheap check
  answers the question -- can this directory be written to? -- take it, rather
  than a more faithful one that acts on the filesystem to find out. A check
  that is right in the ordinary case and wrong at the margins is fine when the
  margin is someone else's permissions.
- If a requirement seems wrong, say so before implementing rather than
  silently substituting a different design.

## 1. Threat model

**The attack being defended against is phishing and paste error.**

The SimpleFIN setup token is a base64-encoded URL that the user obtains from a web
page and pastes into this CLI. If the user is directed to a lookalike site (a
similar-looking domain, or a Unicode homograph of a real one), they paste a
token pointing entirely at attacker infrastructure. The CLI then POSTs to the
attacker, receives an attacker-controlled access URL, stores it, and sends
Basic Auth credentials to the attacker on every sync forever.

**Human confirmation does not fix this.** A prompt that displays the decoded host and
asks "is this where you just were?" fails precisely in the phishing case, because the
user's memory of where they just were *is* the attacker's domain. The only control that
works is exact matching against a fixed set of known-good origins.

### Explicitly out of scope

Do not implement these. They address a different threat model and will be
rejected in review.

- **SSRF defenses.** No blocking of loopback / link-local / RFC1918
  destination IPs (beyond the loopback *allowance* described below), no DNS
  pre-resolution and pinning, no `safeurl`-style wrapper. SSRF requires the
  URL chooser and the network-privilege holder to be different principals.
  This is a single-user CLI; the user already has shell on the host. Making it
  GET `10.0.0.1` is not an escalation. It is also not a redirect target worth
  special-casing, since TLS binds identity to the hostname, not the resolved
  address — a hijacked DNS answer that cannot present a valid certificate for
  the pinned hostname yields denial of service, not redirection.
- **Custom currency URL fetching.** The spec allows an account's `currency`
  field to be a URL the client dereferences. The app does not do this. Do not
  add it.
- **Multi-tenant concerns**, rate limiting, or credential encryption at rest
  beyond correct file permissions.

### If you want to push back

Reasonable, and here are the answers to the objections most likely to come up.

- *"This is SSRF, so it should use IP-range blocking / DNS pinning."* No —
  single principal, no confused deputy, and TLS makes the resolved IP
  irrelevant to the identity check.
- *"A fixed provider list will break when a new provider appears."* Yes, by
  design. The config file is the escape hatch. Do not soften it into a prompt
  or a flag.
- *"Just show the user the host and ask them to confirm."* Fails in the
  phishing case. May be added as a display nicety on top of the root matching,
  never as a replacement for it.
- *"We should validate the currency URL too."* Out of scope.
- *"Storing the access URL's components would be more robust than
  storing one string."* Possibly, but at the cost of complexity; and
  the two failure modes that would motivate a structured store -- a
  stored URL truncating a request path, and credentials leaking into
  output -- are closed by parsing before use and rendering only
  `origin_and_path`. A structured store is not needed to get either.

---

## Steps 1-4b (DONE — read this, do not redo them)

`url_validation.py`, `provider_registry.py`, `provider_access_urls.py` and
the reworked `config.py` are in place, verified and mutation-tested. The specs
that produced them follow, marked DONE, for context only. Read the modules
themselves: their docstrings carry the rationale.

The `url_validation.py` API the remaining steps build on:

```python
@dataclass(frozen=True)
class NormalizedUrl:
    scheme: str
    host: str  # lowercased; punycode if the source was non-ASCII
    port: int | None  # None when absent or the scheme's default
    username: str  # percent-decoded; empty when absent
    password: str  # percent-decoded; empty when absent
    origin: str  # scheme://host[:port] -- what a message names
    origin_and_path: str  # scheme://host[:port]path -- NO credentials

    @property
    def has_creds(self) -> bool: ...  # either credential non-empty


# message is complete on its own; no error codes to branch on
class UrlValidationError(Exception): ...


def parse_url(raw: str) -> NormalizedUrl: ...
def parse_root(raw: str) -> NormalizedUrl: ...  # for provider roots
def validate_claim_url(root, raw, *, provider: str) -> NormalizedUrl: ...
def validate_access_url(root, raw, *, provider: str) -> NormalizedUrl: ...
```

Facts about it that the rest of the work depends on:

- **Parsing is `httpx2.URL`**, the type that later issues the request, so the
  string matching compares is the string the client fetches. Provider input is
  normalized rather than repaired: a default port is dropped, a character not
  legal in a path is percent-encoded, a Unicode host is punycoded and then
  fails the prefix match. A trailing dot on the host is left alone, and so
  simply fails to match.
- **`origin_and_path` is the credential-free rendering of any URL**, and what
  matching compares. **`origin` is what goes in an error message**: a claim
  URL's path carries the one-time setup token, and a message naming it prints
  a live bearer credential into terminal scrollback. Do not log or embed a raw
  access URL string.
- **`origin_and_path` is not a URL to fetch.** It carries no credentials, and
  a root's has a trailing slash the configured string need not have had. The
  one exception is `build_provider_client`'s httpx2 `base_url`, which joins
  request paths onto it rather than fetching it, and supplies the credentials
  separately via `auth=`.
- **Matching is one prefix comparison**, `(candidate.origin_and_path + "/")`
  must start with `root.origin_and_path`. Because that string begins with the
  scheme and host, the single test covers scheme, host, port and path prefix
  together. Do not add separate scheme/host/port checks anywhere; they would
  be redundant and could drift.
- **`parse_root` appends a trailing `/`** if absent, and enforces
  https-or-loopback-http and no credentials. Roots are configuration and are
  never dereferenced, so appending a slash is safe and makes matching strictly
  stricter. Use `parse_root` for every provider root, built-in or
  config-supplied.
- `validate_claim_url` rejects credentials; `validate_access_url` requires
  them -- `has_creds`, not merely an `@` section, so a URL spelled `:@` has
  none. Both reject query strings and fragments, including a bare trailing `?`
  or `#`, and `.` or `..` path segments.
- **Parse once per operation.** Read every field an operation needs off one
  `NormalizedUrl` rather than parsing the string again, and never derive a
  field by a separate string operation alongside it. Two parses of a string
  that was normalized in between silently check different inputs. This applies
  to request-building as much as to validation.
- `provider=` is the key used in the error message. Pass the provider's key;
  `ProviderEntry` constrains those to `[a-z0-9-]+`, which is what makes it
  safe to render.

**Two gaps are left open deliberately**, both of which step 7c records in
`ARCHITECTURE.md`. A provider root's path is rendered in full in messages,
since a root is configuration rather than a secret. And a URL malformed enough
that the parse disagrees about where its credentials are -- an unencoded `/`
in a password ends the authority before the `@` -- can put a fragment of one
in `origin`. Both cost more to close than they are worth; `UrlValidationError`
says so, and the tests that pin them are marked as pinning behaviour rather
than a requirement.

---

## Step 2: the provider registry (DONE)

Create `src/simplefin_aggregator/provider_registry.py`. It must not import
`config.py` (config imports it, not the reverse).

```python
@dataclass(frozen=True)
class ProviderEntry:
    key: str  # stable machine identifier; the store key and config reference
    label: str  # human display string, shown in the claim menu
    root: NormalizedUrl  # via parse_root()
```

Static entries. The URLs are final; adjust only the display labels if better
names are known. Keys are stable identifiers — changing one later invalidates
users' stored access URLs and config references, so treat them as permanent:

| key | label | root |
|---|---|---|
| `simplefin-bridge` | SimpleFIN Bridge (beta) | `https://beta-bridge.simplefin.org/simplefin` |
| `lunchflow` | Lunch Flow | `https://www.lunchflow.app/api/simplefin-bridge` |
| `redbark` | Redbark | `https://api.redbark.com/simplefin` |

Also provide a function that merges the static list with config-supplied
entries and hard-fails on a duplicate key, and a lookup-by-key that
hard-fails when the key is absent. An unknown key is never tolerated,
guessed at, or fuzzy-matched.

Tests: every static root parses and is canonical; duplicate key between a
config entry and a static entry is rejected; lookup of an absent key raises;
a config entry with a bad root (http on a non-loopback host, credentials,
query) is rejected with a message naming the problem.

## Step 3: the access URL store (DONE)

Create `src/simplefin_aggregator/provider_access_urls.py` (naming it for what
it holds, and matching the existing `provider_*` module family; note
`access_url.py` already exists and is a different thing -- the access URL this
aggregator hands *out* to its client app, not the ones it holds *for*
providers).

Nothing regenerates this file. A setup token is one-time-use, so losing it
means going back to every provider for a fresh token; never write anything
that treats it as safe to discard.

- Stores a map of **provider key → access URL string**. Use pydantic v2 for
  the file shape, consistently with `config.py`. JSON format (no TOML writer
  dependency exists, and this file is machine-written and never hand-edited).
- Named `provider_creds.json`, and kept in the configuration directory that
  `--config-dir` selects for every subcommand, defaulting to `platformdirs`
  `user_config_dir(APP_NAME)`.
- Create the file mode `0600`. A missing file loads as empty, not an error.
- **Warn if the file is group- or world-readable**, the way
  `config.py`'s `_warn_if_permissive` already does for config.toml: warn on
  stderr, do not block. It holds provider Basic Auth credentials, so it is now
  the most sensitive file this app owns. Reuse the existing helper rather than
  writing a second one.

Note on the config.toml permission warning: leave it in place. Even after
`access_url` moves out of config.toml, that file still holds `claim_token` and
`client.password`, which together let any local user read the user's bank data
through the loopback endpoint. It is a smaller exposure than a provider access
URL, not a nonexistent one.

Why the provider key is the store's key: it is the one identifier config.toml
and the store share, so a stored credential can be found for a configured
provider without either file having to name the other. (Consequence to note in
a comment: two accounts at the same provider would collide on one key.)

Tests: round-trip; missing file yields empty; file is created `0600`;
malformed JSON produces a clear error, not a traceback.

## Step 4: config schema rework (DONE)

In `config.py`:

- `Provider` **drops** `access_url` (now in the store) and **drops** `name`,
  and gains `key: str` — a provider key. `key` replaces
  `name` as the identifier everywhere, since it is already a stable,
  human-readable key. Delete `parsed_access_url()`.
- `Config` gains `custom_providers: list[...] = []`, the user-supplied provider
  entries, each `key`/`label`/`root`, validated via `parse_root`. These are a
  normal validated field on `Config`, **not** parsed standalone — a broken
  config.toml must fail loudly at `claim` time rather than being silently
  tolerated only to fail later at `serve` time.
- `Config` gains a model-level check that every `provider.key`
  resolves against `KNOWN_PROVIDERS + self.custom_providers`, so a dangling reference
  fails at config-load time for every command.
- Keep the existing `load_config` error path that rebuilds messages from
  `exc.errors(include_url=False, include_input=False)` — never `str()` a
  pydantic `ValidationError`, since its default rendering embeds raw input
  values, i.e. credentials.

Then rename `.name` → `.key` at its call sites: `app.py` (the
`provider_clients` dict keys, `seen_provider_names`), `transport.py`
(`fetch_all`'s `clients[provider.name]`). `request_counter.py`,
`provider_response.py` and `transport.py`'s `provider_name` parameters take
plain strings and need no change beyond what callers pass.

`provider_clients.py`'s `build_provider_client` currently takes a `Provider`
and calls `provider.parsed_access_url()`. It must instead take the validated
`NormalizedUrl` of the access URL: `origin_and_path` as the httpx2 `base_url`,
and `username`/`password` for `auth=`.

This is the one place `origin_and_path` feeds a network request, and it is
sound because httpx2's `base_url` is a prefix that request paths are joined
onto rather than a URL fetched as-is; the value is always a *candidate* (never
a root, so it has no synthetic trailing slash); and the credentials it
deliberately lacks are supplied separately via `auth=`. Say so in a comment
there, since the module docstring otherwise tells readers never to fetch it.

`tests/support.py`'s `make_config` builds `providers: [{"name": ..., "access_url": ...}]`
and must be updated; `install_provider_transport` keys off the provider name.
Many test files construct configs through it. Update all of them.

## Step 4b: adopt `httpx2.URL` in `url_validation.py` (DONE)

Superseded by the API and facts above.

## Step 5: redirects

**Redirects.** Disable them on both the claim POST and every `/accounts` GET;
treat any `3xx` as an error. The SimpleFIN spec defines `/accounts` as
returning only 200, 402, or 403 — there is no legitimate redirect. Note that
`requests`-style silent POST→GET conversion on a 302 is exactly what this
prevents.

- `provider_clients.py` currently passes `follow_redirects=True` — change it.
- `cli.py`'s `_build_claim_client` currently passes `follow_redirects=True` —
  change it. Set it explicitly even though `False` is the httpx2 default, so a
  future refactor cannot silently flip it.
- `transport.py`'s `fetch()` must treat a `3xx` response as a
  `ProviderFailure`. With redirects disabled httpx2 *returns* the 3xx response
  rather than raising, so the current code would pass it through as a
  `ProviderSuccess`.

There is no config hot-reload in this app and you should not add one. `serve`
reads config.toml and the access URL store once, at startup, and a `Config` is
read-only thereafter; both are fixed for the life of the process. Each stored
access URL is validated against its own provider's root as the app
is built, before uvicorn starts.

## Step 6: the `claim` subcommand

Current flow: user pastes token, CLI decodes it, POSTs, prints the access URL
to stdout for the user to paste into config.toml by hand.

New flow (`claim` gains `--config-dir`; it now requires a fully valid config,
loaded exactly as `serve` and `gen-token` do via `_load_config_or_exit`).

**Load the access URL store at startup too**, before the menu and before any
network call, tolerating a missing file as empty. `save_access_url` reads the
store before merging into it, so a corrupt or unreadable store would otherwise
first surface at step 6 below -- after the one-time setup token has been spent
on the POST. The token cannot be reused, so that ordering turns a recoverable
file problem into a lost credential. `serve` already reads the store at
startup and so already fails early.

The steps:

1. **Menu.** Present every known provider (built-in + config) and require the user to
   select the provider they got their token from. No free-text host entry.
   The user still navigates to their provider and obtains the token exactly as
   before — the menu exists solely to fix which entry the following steps
   validate against.
2. **Accept the pasted token**, from the command line if given, otherwise
   prompted for after the provider selection. Decode it with plain
   `base64.b64decode(token)`, matching the SimpleFIN reference implementation.
   Note this means **removing** the `validate=True` the current `cli.py`
   passes: being stricter than the reference risks rejecting a token a real
   provider issued, and buys nothing, because the decoded URL still has to
   match the selected provider's root -- that check is the actual control, not
   the base64 alphabet. Keep a clear error for decoded bytes that are not
   ASCII. (`b64decode` still raises `binascii.Error` on bad padding, so the
   existing "not valid base64" test case keeps working.)
3. **Validate the decoded claim URL before any network call**, via
   `validate_claim_url` against the selected entry's root. Validating first
   matters: a hostile host you contact has already learned your egress IP and
   that the token is live, even if you reject its response.
4. **POST to the claim URL** with redirects disabled and TLS verification on.
   Handle `403` specifically: the token has already been claimed, which may
   mean it is compromised, and the user should be told to revoke it at the
   provider.
5. **Validate the returned access URL** via `validate_access_url` against the
   same entry.
6. **Store it** under the provider's key.

Config-supplied providers must be addable **only** by editing the
config file — no command-line flag, no interactive "add this origin?" prompt.
A phishing page's natural next move is to tell the user to run a command it
supplies; requiring a deliberate file edit raises that bar, and an interactive
prompt reintroduces the human comparison already established not to work.

**Failure behaviour.** An unknown or mismatched origin, at claim time or use
time, is a hard failure: no prompt, no override, no `--force`. A human reading
the message should be able to work out what went wrong without reading the
source, so for the claim-time case add that a mismatch may mean the token did
not come from the provider the user thinks it did, and point at the config
file as the way to add a provider the menu does not list.

**Say "a provider the menu/list does not name", not "a self-hosted
provider."** Self-hosting is only one reason an entry is missing: a real
third-party provider that is simply not in `KNOWN_PROVIDERS` is at least as
likely, and "self-hosted" tells that user the feature is not for them. The
same wording applies wherever an unknown key is reported, not only at claim
time -- `find_provider`'s message reaches the user through config validation
too, and an unknown key there is as often a missing entry as a typo.

**Print the `UrlValidationError` message; do not echo the URL
yourself.** The raw claim URL is attacker-influenced, and rendering it
straight to a terminal is its own vulnerability. The access URL
contains credentials. Where the exception message names a URL at all,
it is in a form built for display: credentials stripped, a non-ASCII
host rendered as punycode, and anything not legal in a URL
percent-encoded, so no ANSI escape can reach the terminal. A URL that
failed to parse is described rather than named, and that is deliberate
-- do not add the URL back. Re-rendering the raw string
in the CLI throws all of that away. The same rule applies to the token
and to any provider response body echoed on failure -- including a `3xx`,
which step 5 newly routes to that path and whose body is typically empty
and always attacker-influenced.

`tests/test_claim.py` will need a substantial rewrite; `CliRunner` supports
driving the menu and token prompts via `input=`.

## Step 7: documentation

Earlier steps add to this list as they go, so read it as the accumulated set
of things `ARCHITECTURE.md` and the README are missing — not as a list written
in advance.

### Step 7a: user-facing documentation (DONE)

`README.md`, `config.example.toml`, `scripts/manual_verify.sh` and the
`Dockerfile` describe the current flow: write a config, `claim` a token
against a provider chosen from the menu, `serve`. Read them for what the CLI
does today rather than reconstructing it from the steps above.

Both `README.md` and `config.example.toml` deliberately describe the
multi-provider behaviour v0.1.0 will ship with, while the code still accepts
exactly one `[[providers]]` entry. That gap is intentional; do not "fix" the
documentation to match the code.

### Step 7b: final verification

- Audit every log line and exception message that could touch an access URL.
  Only `origin` or `origin_and_path` may be rendered, never the raw string.
  If not already covered, add a test asserting no captured log output or exception message contains
  the Basic Auth password of a well-formed access URL -- well-formed because
  of the gap recorded above, which `url_validation.py`'s own tests already
  pin.
- Confirm the flow-level test list at the end of this document is covered.

### Step 7c: developer documentation

- `docs/ARCHITECTURE.md`: module-map rows for `url_validation.py`,
  `provider_registry.py` and `provider_access_urls.py`; the revised
  `Config`/`Provider` structures; the updated `claim` and `/accounts` flows;
  and the access URL store as a second piece of on-disk state alongside
  config. Two deliberate gaps from step 4b belong here as known behaviour: a
  provider root's path is rendered in full in error messages, so a
  config-supplied root carrying a capability token in its path would print it
  on every
  mismatch -- the "a root is configuration, not a secret" policy working as
  intended; and a URL malformed enough that the parse reads a password prefix
  as the port can have that fragment named in a message. Both are accepted in
  preference to the checks that would close them. State plainly that a `Config` is read-only after `load_config` and
  that there is no config hot-reload. Its "Cross-cutting: secrets
  and logging" section lists three places that defend against leaking secrets
  — the stored access URL is a fourth and belongs there.
- **Credentials reach a provider only through `auth=`, never through a URL.**
  What step 7b's audit turned on: the two failure paths that render a
  dependency's own error text -- `transport.fetch`'s `str(exc)`, which becomes
  the 502 body the client app sees, and `claim`'s "could not reach provider" --
  cannot carry credentials, because the URL those requests use is
  `origin_and_path`. Belongs with the secrets-and-logging section, as the
  reason those two `str(exc)` renderings are safe.
- **Redirects are not followed** on the claim POST or on `/accounts`, and a
  `3xx` from a provider is a failure rather than a response passed through: the
  client app sees the same `502` and SimpleFIN-shaped error body it gets for an
  unreachable provider. Belongs in `ARCHITECTURE.md`'s `/accounts` flow, next
  to the byte-identity pass-through guarantee, since it is the one status class
  that is deliberately not passed through.

## Step 8: intentionally left blank

## Step 9: synthesize `AGENTS.md` and `CLAUDE.md`

The working agreements this task has been run under live in two places that
will not survive it: this document, which is ephemeral, and the assistant's
local per-project memory directory, which is per-machine and invisible to
everyone else. Move the durable parts into the repository.

**Create `AGENTS.md`** at the repository root, holding only what stays true
after this task ships.

- **What the project is**, in two or three sentences, and a pointer to
  `docs/ARCHITECTURE.md` as the map of the code. Do not restate the
  architecture here — one pointer, so there is a single place to update.
- **The verification pass**: `uv run ruff format --check .`,
  `uv run ruff check .`, `uv run basedpyright`, `uv run pytest`. All four,
  after every change, a subset is not sufficient. basedpyright runs a
  zero-warning policy: fix each warning or add an explicit
  `# pyright: ignore[rule]` with a one-line reason.
- **Non-vacuous tests.** Demonstrate a new test fails against mutated code
  before trusting it. Note the trap: clear `__pycache__` between mutating and
  restoring, or CPython may reuse the mutant's bytecode (same size, same
  second) and report a false pass.
- **A leak corpus is indexed by misparse shape, not by syntactic position.**
  Three separate marked-secret corpora in this task missed a real leak because
  they placed the marker where the secret *belongs* rather than where a
  particular misparse *moves* it. A secret in password position does not
  exercise the case where a password prefix is read as a port; that needs
  `8443/secret`. Enumerate the ways the parse can go wrong, and place a marker
  in each resulting position.
- **Lint and type-check configuration names the oldest supported Python, not
  the newest that exists.** `ruff`'s `target-version` and basedpyright's
  `pythonVersion` said 3.14 while `requires-python` was `>=3.12.4`, so
  `ruff format` rewrote code into 3.14-only syntax that the interpreter could
  not parse -- while `ruff check` reported no problem.
- **Never make real outbound network calls** in tests or manual checks. Use
  loopback fakes or a mock transport, even where the sandbox would allow the
  real thing.
- **Check a claim about a dependency by running it, not by reading its
  source.** Reading is how you form the belief; a runnable check against a
  loopback fake is what settles it. Both directions of this came up in step 6:
  a correct read of httpx2's error paths ("they do not embed the URL") that
  had not been executed, and a reviewer's confident reproduction of a locale
  bug (`LC_ALL=C`) that did not reproduce, because PEP 538 coerces that locale
  to C.UTF-8 -- the underlying defect was real, the stated repro was not.
- **Size the test to the fix.** A one-keyword robustness fix does not earn a
  subprocess harness. Step 6 pinned three `encoding="utf-8"` arguments and
  grew ~90 lines of scaffolding -- a child interpreter, an environment
  override, a skip-if-the-locale-refuses guard -- to prove they were there.
  The scaffolding was correct and was still the wrong trade: it cost more to
  read and maintain than the failure it protected against. Where a check needs
  more machinery than the code it checks, prefer stating the intent in a
  comment and leaving it untested.
- **The review cycle** from "How to work" above: human review, then AI review,
  then a stated disposition for every finding raised, then a chance for each
  reviewer to answer the findings you rejected, then human re-review, then
  commit. **Hand the human the review delta as something reviewable** -- a
  diff or a commit from the pre-review state to the current one -- before
  squashing it away; the disposition list says what you claim changed, the
  diff says what did.

  Say plainly that the human review before the AI review is a **gate, not a
  courtesy**: the agent presents its work and stops, and only a human reply
  starts the AI reviewers. Presenting and then launching them in the same turn
  is not a pause, and it spends a rationed resource on a version the human's
  reply may be about to change. Carry over the two operational traps — a review tool asked for
  "all changes" may still read only committed code, and one asked to review
  "commit X" may review that commit rather than the range ending at it. Say
  explicitly that a finding is verified against the code before being acted on
  or relayed, because confident-but-wrong findings are common.
- **Terminology**: "client" or "client app", never "consumer". The client app
  is generic; Actual Budget is only the motivating example, not a dependency.
- **Comments** state why, not what, and only where the reason is non-obvious;
  a comment that restates the code is noise. They are sized to the code they
  explain and open by naming their subject. Long rationale is welcome where a
  reader would otherwise re-litigate a decision; a two-line helper does not
  need a five-paragraph justification. A comment is never a record of how the
  code got here, and never an argument with a code reviewer -- that belongs in
  the reply to the reviewer and, if it changed the design, in the commit
  message. Heavy comment density reads as intricacies standing in for
  principles; if a module needs that much narration, say what it is for in its
  docstring instead.
- **Commit messages** are written for someone reading `git log` to find out
  why the code looks like this. Lead with the point, organize by topic, and
  rewrite from the current state rather than appending each round's news.
- **Secrets discipline**, the project's defining constraint: an access URL, a
  setup token and a Basic Auth password do not reach a log line, an exception
  message, or stdout. Point at `url_validation.py`'s `UrlValidationError`
  docstring for the rules rather than repeating them, including its statement
  of where the rule deliberately stops.
- **Separate the tests that pin a requirement from those that pin behaviour.**
  A test asserting what the code happens to do today -- an input it declines
  to normalize, a gap it deliberately leaves open, a dependency's exact output
  -- should say so, so that a future implementation changing it knows the
  change is allowed and deliberate rather than a regression it must undo.
- **A rule with a stated limit beats a rule defended by machinery.** Where
  holding a security property absolutely would cost real complexity -- gates
  on exotic input, checks that reject legitimate URLs -- take the simpler code
  and document the gap where a reader will meet it. A module whose comments
  argue for every corner case reads as though it has no principles, only
  intricacies.
- Any entries in the per-project memory directory not covered by the above.

**Write it agent-agnostically.** `AGENTS.md` is a cross-tool convention, so it
addresses "the agent", never a particular product, and it must not depend on
one tool's features. Where a concrete command is genuinely useful, give it as
an example of the practice rather than as the practice itself — the rule is
"have the step's diff reviewed by two independent AI reviewers and account for
every finding", and the specific CLI invocations are illustrations of it.
Likewise, do not reference the local memory directory: its *content* belongs
in `AGENTS.md`, its location does not.

**Create `CLAUDE.md`** at the repository root containing exactly two lines:

```
@AGENTS.md
Do not make changes here; make them in AGENTS.md.
```

Nothing else. The first line is the import; the second stops this file
accreting guidance of its own and drifting away from the shared one. Both
lines are instructions to the agent that reads it, which is what makes the
second one work.

Once both files exist, the per-project memories that `AGENTS.md` now covers
are redundant. Flag them for the human to prune rather than deleting them —
they are the user's, not the task's, and some may cover ground beyond this
repository.

---

## Required tests (steps 4-6)

`url_validation`'s own matching tables are already covered by steps 1-4b;
these are the flow-level cases that remain.

- Token that is not valid base64 → clear error, **no network call**.
- Token decoding to non-ASCII bytes → clear error, no network call.
- Claim POST returns `302` → error, redirect not followed.
- Claim POST returns `403` → message telling the user the token may be
  compromised and to revoke it at the provider.
- Claim returns an access URL on a different host than the selected provider
  → rejected, nothing written to the store.
- Claim returns an access URL with no credentials → rejected.
- Claim returns an access URL containing `#` → rejected.
- `/accounts` returns `302` → error, redirect not followed.
- A stored access URL that would have ended in `?` → cannot be stored at all,
  since `validate_access_url` rejects it at claim time.
- No test log output or exception message contains the Basic Auth password.
