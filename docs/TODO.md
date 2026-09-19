<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Terminology

References:
- https://www.simplefin.org/protocol.html
- https://beta-bridge.simplefin.org/info/developers

## Terminology/naming mess: in progress ##

### Next up: ###

Names and terms only, plus the few logic changes listed here. No backward
compatibility: an existing `aggregator_creds.json` fails to load afterwards;
recreate its clients.

- Terms follow `docs/ARCHITECTURE.md`'s "Terminology". Flag violations found in
  passing; fix them in files this step touches.
- The `app` subcommand becomes `client`, help "Manage clients.":
  - `add CLIENT` — "Add a new client and print its one-time-use setup token."
  - `revoke CLIENT` — "Remove a client and revoke its credentials."
  - `reset CLIENT` — "Revoke a client's existing credentials and print a new
    one-time-use setup token." Keeps the client's `created_at` (logic change).
  - `list` — help "List clients."; shows each client's key and creation time.
    No status column, no exchange time.
- `app_tokens.py` becomes `agg_creds.py`; `UnclaimedAppToken`,
  `ClaimedAppToken`, `AppTokenRecord` become `UnexchangedAggCreds`,
  `ExchangedAggCreds`, `AggCreds` -- not `...Token`, since a record is not a
  setup token. Likewise `new_agg_creds`, `exchange_agg_creds`, and the JSON
  key `creds`. Tests follow.
- The `status` discriminator becomes `exchanged: Literal[False]` /
  `Literal[True]`, behind a callable discriminator that accepts only a JSON
  boolean: strict mode alone still accepts `0`/`1`.
- Both JSON stores are validated in strict mode; `config.toml` is not.
- `created_at` / `exchanged_at` become epoch seconds (`int`): strict mode rejects
  the ISO strings the loader would hand it, and lax mode accepts numbers where a
  timestamp belongs. `exchanged_at` stays in the store, undisplayed.
- `aggregator_creds.json` keeps its name: `<issuer>_creds.json`, like
  `provider_creds.json`.
- `ClientCredentials` becomes `AccessUrlAuth`.
- The random secret ending an aggregator claim URL is `claim_secret` in code:
  the route template `{claim_secret}`, the store field `claim_secret_sha256`.
  Not user-facing; the 403 stays "unknown token".

Deferred to its own step: last access time in `client list`.

### Flagged during the `app` -> `client` rename, not fixed ###

Line numbers drift; the quoted text is what to search for.

"claimed"/"unclaimed"/"claimable" used of a provider, to mean "has a stored
access URL". The glossary lets a client claim an access URL, not a provider:
- `tests/support.py:135` — "its one claimed provider" means the provider whose access URL the fixture stores.
- `tests/test_serve.py:187` — test name `test_serve_fails_when_the_provider_has_not_been_claimed` means no access URL is stored for it.
- `tests/test_serve.py:200` — test name `test_serve_fails_when_one_of_several_providers_has_not_been_claimed`, same.
- `tests/test_serve.py:203` — "Any unclaimed provider stops the server from starting.", same.
- `tests/test_serve.py:211` — "the claimed provider gets no server of its own", same.
- `tests/test_serve.py:218` — "Neither an unclaimed provider nor a root mismatch hides the other's report.", same.
- `tests/test_serve.py:224` — "claimed for 'my-bank', whose root just moved": an access URL claimed from `my-bank`.
- `tests/test_claim.py:322` — "not by what was claimed" means the provider key the access URL was claimed from.
- `tests/test_claim.py:324` — "provider stays claimable" means an access URL can still be claimed from it.
- `tests/test_provider_access_urls.py:472` — "Two providers claimed at once" means two access URLs saved concurrently.
- `docs/ARCHITECTURE.md:524` — "as unclaimed later", with its existing TODO: `serve` actually says "no access URL stored for provider ...".

"claim" of an account id, by a prefix or an account: a third sense the glossary
does not cover. Either define it or use another verb ("owns", "matches"):
- `src/sf_agg/merge.py:114` — "each exposed id that more than one account claims".
- `src/sf_agg/provider_resolution.py:7` — "An id no named prefix claims belongs to all".
- `src/sf_agg/config.py:219` — "named prefix claims then belongs to all of them".
- `tests/test_provider_resolution.py:43` — "not a claim on every id".
- `tests/test_provider_resolution.py:50` — local variable `claimed` (an id a named prefix matches).
- `tests/test_provider_resolution.py:51` — local variable `unclaimed` (an id no named prefix matches).
- `tests/test_provider_resolution.py:107` — "does not make it a claim on another provider's ids".
- `tests/test_accounts_endpoint.py:375` — "an id no named prefix claims is asked of every blank-prefix provider".
- `docs/ARCHITECTURE.md:400` — "An id no named prefix claims then belongs to every".
- `docs/ARCHITECTURE.md:599` — "an id no prefix claims: logged, routed nowhere".
- `docs/ARCHITECTURE.md:617` — "An id no prefix claims is reported to the operator".
- `docs/ARCHITECTURE.md:764` — "an id no prefix claims, the first provider-derived".

### Not yet ###

- conceptually the `claim` command is really a shorthand for a more technically correct non-existent command such as `provider exchange` (or maybe `provider new` , `provider update` ... )
- we need a terminology section in ARCHITECTURE
- terminology: currently/recently mis-used: 'protocol', 'demo', 'claim', 'bridge'
  - there is no 'SimpleFIN Bridge protocol'. it is the 'SimpleFIN protocol'
  - demo bridge is flat out wrong. demo token is ok in some contexts, demo user is more precise.

### More

- review the 1-line module docstrings, e.g. `provider_registry.py` unhelpfully
  says "The set of providers that can supply a setup token."
- get rid of useless test docstrings when test name is self-documenting; reconcile docstring/test name/test body as you go
- get rid of useless & verbose docstrings
- get rid of noise in ARCHITECTURE.md

# Short-term fit and finish [human]
- emphasize that `providers_creds.json` holds bearer tokens and what that means!
- README: "`config.toml` holds no credentials ..." is unhelpful: threats are unclear.
- a pointer in README to https://www.simplefin.org/protocol.html#app-quickstart
  for devs who want to smoketest `sf-agg` from the command line
  would be nice
- add a quickstart section to the top of the README
- maybe add an actualbudget+lunchmoney quickstart section to the top of the
  README
- ARCHITECTURE.md is chock-full of reciting what the code does rather than
  explaining principles, structure, why is the way it is, etc.
- In general the prose is terrible.

# Medium-term fit and finish
- ship a docker image righteffort/simplefin-aggregator
- ship a python package similarly
- maybe: provide an example docker_compose.yml .. currently in .notes/docker-compose-example.yml

# Questions
- Do we check permissions on the chain of directories from a symlinked config file to the real file?
- Why is
  `test_url_validation.py:test_mismatch_message_withholds_the_setup_token`
  docstring "The paste-error case..." ?

# Other future work

- review language for 'attacker', 'hostile', etc. and make sure they are not
  being used when 'misbehaving' would be more accurate.
- maybe in `cli.py:claim` show asterisks instead of nothing (`hide_input=True`
  behavior). IIUC once python3.13 is deprecated we can use a python3.14 to help
  with this, if true leave a TODO behind to simplify
