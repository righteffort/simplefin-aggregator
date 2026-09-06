Build `simplefin-aggregator, a server that implements the SimpleFIN
Bridge protocol for use by a personal finance app such as Actual
Budget, and that aggregates data from one or more SimpleFIN
providers. This version is an identity function: exactly one provider,
everything passed through unchanged. A later version will fan out to
several providers and merge their responses, so structure the code for
that from the start.

## Work incrementally

Do not build this in one pass. Propose a sequence of small steps, then stop and
wait for my approval before starting. After each step, stop and show me the diff,
what you verified, and what you propose next. I want to course-correct between
steps, not review a finished system.

A reasonable first step is config parsing plus the `claim` subcommand — nothing
that serves traffic.

## Protocol

Read https://www.simplefin.org/protocol-v1.html and
https://beta-bridge.simplefin.org/info/developers before writing code. Do not
work from memory. The endpoints a SimpleFIN server implements are GET /info,
GET /create, POST /claim/:token, and GET /accounts. Prefer the spec's own
vocabulary where it has a term; use "provider" for the SimpleFIN servers this
aggregator proxies.

## Environment and stack

Runs on loopback over plain HTTP. Not exposed to any network. No TLS.

Python 3.11+, FastAPI, uvicorn, httpx2. No database. Config is TOML; there is no
other persistent state. Standard library where reasonable.

The code must pass `ruff format --check` and `ruff check` clean, and
`basedpyright` in "recommended" mode. Targeted `# pyright: ignore[rule]` is acceptable
where a third-party stub is genuinely inadequate, with a one-line comment saying
why. Don't reach for `Any` to silence it.

## CLI

Three subcommands.

`claim <setup-token>`
  A pure utility with no side effects beyond the network call. Decodes the base64
  setup token, POSTs to the claim URL, prints the resulting access URL to stdout,
  and exits. The operator pastes that into the config file. Print a reminder to
  stderr that setup tokens are one-time-use and this output cannot be regenerated.

`gen-token`
  Reads the config and prints a base64-encoded setup token for a client app to
  use: the claim URL `<base_url>/simplefin/claim/<claim_token>`, encoded the
  same shape a real SimpleFIN provider's setup token would be. Replaces having
  the operator compose and base64-encode that URL by hand.

`serve`
  Reads the config and runs the server. Never claims anything.

## Configuration

  - bind address and port (default 127.0.0.1:8080)
  - a list of providers, each with a name and an access_url of the form
    https://user:pass@host/path. Exactly one for now; the schema and internal
    types must already be a list.
  - the basic-auth username and password this aggregator requires from a client app
  - the constant claim token: the secret path segment for POST /claim/:token
  - the base URL to advertise in the access URL this aggregator hands out

Parse into pydantic models. Fail with a clear message on a malformed access_url
rather than at first request.

Never log access URLs, setup tokens, credentials, or Authorization headers.
Redact them.

## Endpoints

POST /simplefin/claim/{token}
  - Stateless. If {token} matches the configured claim token, return 200,
    Content-Type text/plain, body = this aggregator's access URL built from the
    configured app credentials and base URL. No trailing newline.
  - Any other token: 403.
  - Accept a POST with no body and no Content-Type.
  - The token is a constant and is accepted repeatedly. This deliberately violates
    the spec's one-time-claim rule, because the app may reset its credentials
    and re-claim, and on loopback the threat the rule addresses does not exist.
    Document the deviation in the README.

GET /simplefin/accounts
  - Require HTTP Basic Auth matching the configured client credentials; 403 on
    mismatch.
  - Forward these query parameters to the provider verbatim: start-date, end-date,
    pending, account (repeatable — preserve every value, do not collapse),
    balances-only, version.
  - Return the provider's response body unchanged, byte for byte. Same account
    ids, transaction ids, org objects, errors/errlist contents the client would
    have seen talking to the provider directly. Do not parse, normalize, reorder,
    or re-serialize it in this version.
  - Follow redirects on provider requests.

GET /simplefin/info
  - Proxy the provider's response.

## Error handling

  - When a provider returns a non-2xx status, return that same status and body to
    the client. Do not catch it and substitute a 500.
  - Per-account problems that SimpleFIN reports inside a 200 response (the errors
    / errlist arrays) are just part of the body and pass through untouched.
  - When a provider is unreachable — connection refused, DNS failure, timeout —
    there is no provider status to forward. Return 502 with a JSON body shaped
    like a SimpleFIN error response, so the app displays something useful.

## No traffic beyond what the client generates

An upstream request happens only in direct response to a client request. No
retries, no background polling, no health checks, no warmup, no caching. Each
provider must see exactly the request sequence it would have seen had the app
talked to it directly.

A filtered request (?account=X) goes only to the provider owning that account id —
never fan out to find it — and is issued in the same shape it arrived. Do not
satisfy a filtered request with an unfiltered upstream fetch or vice versa.

For observability only, never as a control: log each provider request with the
provider name and a running count for the current UTC day.

## Structure for the future

Even with one provider, the request path must be: parse request -> for each
provider, build and issue a request -> collect into a list of ProviderResponse
(status, headers, raw body bytes, and lazily-parsed JSON) -> pass the list to a
single `merge(responses)` -> serialize. With one provider, `merge` returns that
response's raw bytes untouched, which is what keeps the byte-identity guarantee
above. `merge` must be independently unit-testable.

Likewise route the `account` parameter through an explicit "which provider owns
this id" resolver, and put no-op `rewrite_ids` / `unrewrite_ids` hooks in the
response path, even though today they do nothing.

## Concurrency

The transport layer must be N-capable from the start, even though only one
provider is configured. Only `merge` may assume a single provider.

  - Async throughout: httpx2.AsyncClient, async route handlers. No sync client.
  - Fan out with asyncio.gather over the provider list, not a sequential loop with
    an await inside. With one provider this is indistinguishable; with two it is
    the whole point.
  - `fetch(provider, request) -> ProviderResponse` must never raise. Connection
    refused, DNS failure, and timeout are represented as a failure variant of
    ProviderResponse. `merge` receives a uniform list and is where a failure
    becomes a 502 with a SimpleFIN-shaped body.
  - Construct one AsyncClient per provider in a lifespan context manager, with an
    explicit httpx2.Timeout, and close them on shutdown. Do not build clients
    per request.
  - gather preserves input order; depend on that rather than completion order, and
    keep output ordering fixed to config order so responses are deterministic.
  - No shared mutable state in the fetch path.

Test the transport layer with TWO mocked providers even though the config permits
one: assert both are called, that the two requests overlap in time rather than
running back to back, and that one provider failing still yields a
ProviderResponse for both. `merge` may assert len(responses) == 1 and is tested
separately.

## Non-goals

No id namespacing, no merging logic, no partial-failure handling, no multi-
provider validation. Leave the seams; do not write speculative code for them.

## Tests

pytest, with httpx2.MockTransport faking the provider. Cover:
  - claim returns a well-formed access URL and is repeatable
  - claim with a wrong token is rejected
  - accounts without basic auth is rejected
  - repeated `account` params all reach the provider
  - the response body is byte-identical to the mocked provider body
  - a provider 403 comes back as 403
  - an unreachable provider produces a 502 with a SimpleFIN-shaped error body
  - a malformed access_url in config fails at startup, not at first request

Also a manual verification script using the SimpleFIN demo token from the
developer guide, documented in the README.

## Deliverables

  - the server
  - pyproject.toml, with ruff and basedpyright configured
  - config.example.toml
  - a Dockerfile
  - README: setup, the claim/serve subcommands, the repeatable-claim deviation,
    and how to point a personal finance app at it
