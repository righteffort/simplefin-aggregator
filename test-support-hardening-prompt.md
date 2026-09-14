# Task: make the test support unable to lie

`AGENTS.md` governs how to work; nothing about that is repeated here. Small
task, one step, one commit.

## Background

The tests replace production pieces in two ways: a factory monkeypatched with
a mock-backed one (`_build_claim_client`, `_build_probe_client`), and
`tests/support.py`'s `install_provider_transport` swapping a provider's
`AsyncClient` inside a running app. Either way, the setting the production
factory applies is not exercised by the tests that use the replacement. Each
redirect setting already has a direct test that builds the real client
(`test_provider_clients.py`, and the claim and probe client tests in
`test_claim.py`). Two gaps remain.

## 1. `install_provider_transport` accepts a key the app does not configure

Verified: with the app configured for `my-bank`, installing under `my-bnak`
leaves

    {'my-bank': 'AsyncHTTPTransport', 'my-bnak': 'MockTransport'}

The mock goes nowhere and the real transport stays on the real provider, so a
typo in a test either fails confusingly or makes a real outbound request --
against "never make a real outbound network call", with nothing noticing.

Fix it by construction, not with a test of the helper: refuse a key not
already in `provider_clients`, with a message naming the configured keys.
Then run the suite; any test that was quietly installing under the wrong key
will surface, and each one is a finding to report, not just to fix.

## 2. No timeout is asserted anywhere

Each client factory's timeout is load-bearing and untested:

- `build_provider_client`'s `DEFAULT_TIMEOUT` (30 s) is what stops one dead
  provider holding the client app's whole sync open.
- `_PROBE_TIMEOUT` (10 s), passed on the probe's `.get()`, is what stops
  `claim` from hanging.
- `_build_claim_client` sets none and relies on the library's default.

Asserting `client.timeout == DEFAULT_TIMEOUT` mostly compares a constant with
itself. The property worth pinning is that the timeout is *finite*. For the
claim client, first check by running it what the default actually is (per
"check a claim about a dependency by running it"); if it is finite, pin that
it stays finite, and consider whether an explicit timeout reads better than
leaning on the default. The probe's timeout lives on the call, not the
client, so a factory test cannot see it -- decide whether moving it onto the
client is the smaller change than testing the call site.

## 3. Check, and fix only if it fails: the absence assertions on the claim fakes

`test_claim.py`'s `_install_provider` and `_install_probe` return the URLs
they were asked for, and several tests assert those lists are empty ("the
probe was not reached"). That is only meaningful if each helper has a test
showing it *does* record a call. Confirm each has one; add it for any that
does not.

## Not in scope, with reasons

- `_stdin_is_a_terminal`: a one-line wrapper over `sys.stdin.isatty()`; a
  one-keyword function does not earn a harness.
- `test_serve.py`'s fake `uvicorn.run`: the tests already assert the bind
  address and that the real access-log redaction is wired.
- The `os.fsync` / `Path.replace` / `Path.stat` patches inject faults; they do
  not stand in for production configuration.
- `echoing_provider`: already yields the record of what it did and tells
  callers to assert on it first. A test of the fixture would duplicate every
  caller.
- `make_config` versus `test_config.py`'s `_providers_toml`: two helpers
  building the same config is a smell, but merging them is a separate
  cleanup.

## Decisions made while working

- Item 1: the guard surfaced no test installing under a wrong key.
- Item 2: run against httpx2 2.12.0, a client with no `timeout=` gets 5 s on
  every phase -- finite. The claim client gets an explicit `_CLAIM_TIMEOUT`
  of 30 s anyway: a read timeout lands after the POST went out, when the token
  may be spent, so waiting longer than the library's 5 s is worth it. The
  probe's timeout moves onto its client, which is smaller than testing the call
  site. `build_provider_client`'s `timeout=` parameter had no caller passing
  it, so it is removed: the factory test then covers what `app.py` runs.
  The finiteness tests pass if an explicit timeout is deleted, since the
  library default is also finite; that is intended, as finiteness is the
  requirement.
- Item 3: both fakes already have a positive test (deleting the claim fake's
  recording fails 8 tests, the probe fake's fails 2). Nothing added.
