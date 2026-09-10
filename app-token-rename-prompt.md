# Task: rename the app record types from `app` to `app token`

Problem: `app_tokens.py` names its module, its file, and four of its functions after
"app tokens", but names its types and constructors after "apps". The mismatch
is the smaller problem. The larger one is that `app` is the most overloaded
word in this codebase — `app.py`, `create_app`, the FastAPI `app`, the
lifespan's `app` parameter, `app.state`, `_AppState`, the Typer `app`,
`app_commands`, the CLI's `app` group — so `ClaimedApp` makes a reader
disambiguate on every encounter. And `claim_app` has the verb wrong: you claim
a token, not an application.

`AGENTS.md` governs how to work. Read it first.

## The renames

| now | after |
|---|---|
| `UnclaimedApp` | `UnclaimedAppToken` |
| `ClaimedApp` | `ClaimedAppToken` |
| `AppRecord` | `AppTokenRecord` |
| `new_app` | `new_app_token` |
| `claim_app` | `claim_app_token` |

`AppTokenRecord` is what makes the variant names honest: a claimed record
holds no token, so "app token" has to denote the record — as `app_tokens.json`
already does, since that file holds both states — and the two variants are its
states. Keep the variant names aligned with the `status` values `"unclaimed"`
and `"claimed"`; that alignment is worth more than literal accuracy about what
each one contains.

Not renamed:

- **The CLI.** `app new`, `app list`, `app revoke`, `app regen` are
  operator-facing, and there "app" is the right word: the operator is
  registering an app, not thinking about records.
- **`with update_app_tokens(path) as apps:`.** Leave the local `apps` alone.
- `ClientCredentials`, `matches`, `_digest`, `_AppKey`, `_AppTokenFile`,
  `app_tokens_path`, `load_app_tokens`, `update_app_tokens`.

Nothing has shipped, so the on-disk format is free to change.
- `app_tokens.json` should be renamed `aggregator_creds.json`
- `_AppTokenFile.apps` and the `"apps"` key in that file should change
  from `apps` to `tokens`.  This means every fixture that writes the
  file by hand change with them; as should the record shape in
  `.notes/app-tokens-prompt.md`.

## The documentation is not mechanical

The code is a find-and-replace that the four gates will verify. The prose is
not, and it is the point of the task — the rename was prompted by
`docs/ARCHITECTURE.md`'s section on these types being hard to read, not by correctness.

Two passes, and neither is a search-and-replace:

**Read every comment and docstring on code that defines or uses a renamed
signature.** Sentences written around `UnclaimedApp` do not stay coherent when
the subject becomes an app token: `app_tokens.py`'s module docstring,
`_Sha256Hex`'s note about "an app's age", `ClientCredentials`, both variant
docstrings, `new_app`/`claim_app`, and the call sites in `app.py`, `auth.py`
and `cli.py`.

**Then `docs/ARCHITECTURE.md`, definitions and usages alike.** The
section headed `UnclaimedApp` / `ClaimedApp` is the one to rewrite
rather than retitle; the module map, the on-disk-state table, the `app
new` and claim flows, and the secrets section all refer to these types
in prose. Pay special attention to follow the rules in `AGENTS.md` to
say what each thing is, not how it differs from something else, and
write for a reader who has the file and not its history. Finally, remove the
corresponding bullet from `TODO.md`.

## Steps

One step, one commit. It is a rename with no behaviour change, so the review
is confirming that the diff is only names and that the prose reads better than
it did.
