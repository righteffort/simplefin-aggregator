# Task: harden SimpleFIN token claiming against phishing

You are modifying a single-user Python CLI that is a client of 
SimpleFIN protocol v1 (<https://www.simplefin.org/protocol-v1.html>).

Read @docs/ARCHITECTURE.md for an overview of the existing code before
starting. The code is short (755 lines in `src/*/*.py` so reading it
is not unreasonable). 

Important: read the threat model below before implementing
anything — several requirements will look wrong if you assume a
different threat model than the one stated.

Work in steps and pause after each step for review and
discussion. Start by asking any clarifying questions, then describe
your plan and pause.

---

## 1. Threat model

**The attack we are defending against is phishing and paste error.**

The SimpleFIN setup token is a base64-encoded URL that the user obtains from a web page
and pastes into this CLI. If the user is directed to a lookalike site
(a similar-looking domain, or a Unicode homograph of a real one), they will paste a token
that points entirely at attacker infrastructure. The CLI will then POST to the attacker,
receive an attacker-controlled Access URL, store it, and send Basic Auth credentials to
the attacker on every sync forever.

**Human confirmation does not fix this.** A prompt that displays the decoded host and
asks "is this where you just were?" fails precisely in the phishing case, because the
user's memory of where they just were *is* the attacker's domain. The only control that
works here is exact matching against a fixed set of known-good origins.

### Explicitly out of scope

Do not implement these. They address a different threat model and will be rejected in
review.

- **SSRF defenses.** No blocking of loopback / link-local / RFC1918 destination
  IPs (other than the specific loopback allowance in §2), no DNS pre-resolution
  and pinning, no `safeurl`-style wrapper. SSRF requires the URL chooser and the
  network-privilege holder to be different principals. This is a single-user
  CLI; the user already has shell on the host. Making it GET `10.0.0.1` is not
  an escalation. It's also not a redirect target worth special-casing, since TLS
  binds identity to the hostname, not the resolved address — a hijacked DNS
  answer that can't present a valid certificate for the pinned hostname yields
  denial of service, not redirection.
- **Custom currency URL fetching.** The spec allows an account's `currency`
  field to be a URL the client dereferences. The app does not do this. Do not
  add it.
- **Multi-tenant concerns**, rate limiting, or credential encryption at rest
  beyond correct file permissions.

### If you want to push back

Reasonable, and here are the answers to the objections most likely to come up.

- *"This is SSRF, so it should use IP-range blocking / DNS pinning."* No — see §1.
  Single principal, no confused deputy, and TLS makes the resolved IP irrelevant to the
  identity check anyway.
- *"An allowlist will break when a new provider appears."* Yes, by design. The config
  file is the escape hatch. Do not soften it into a prompt or a flag.
- *"Just show the user the host and ask them to confirm."* Fails in the phishing case —
  see §1. May be added as a display nicety on top of the allowlist, never as a
  replacement for it.
- *"Default-port normalization is standard practice."* It is, and it's deliberately not
  what's wanted here — see §3.2.
- *"We should validate the currency URL too."* Out of scope — see §1.
- *"Storing components would be more robust than storing a normalized string."* Possibly,
  but the two failure modes that motivated it (URL truncation, credential leakage) are
  fully addressed by the parse-before-use and strip-before-log rules in §5. A structured
  store isn't required to get those guarantees.

If a requirement seems wrong for a reason not covered above, say so before implementing
rather than silently substituting a different design.

---

## 2. The allowlist

Add a static allowlist of known SimpleFIN provider **root URLs** in code, plus an
optional list of additional entries read from the existing config file.

Static entries (labels are guesses from the hostnames — adjust the display strings if
better names are known, but the URLs are final):

```python
KNOWN_PROVIDERS = [
    ProviderEntry(
        label="SimpleFIN Bridge (beta)", root="https://beta-bridge.simplefin.org/simplefin"
    ),
    ProviderEntry(label="Lunch Flow", root="https://www.lunchflow.app/api/simplefin-bridge"),
    ProviderEntry(label="RedBark", root="https://api.redbark.com/simplefin"),
]
```

- Each entry is a root URL with scheme, host, optional port, and optional path prefix.
- Config-supplied entries exist for people self-hosting a SimpleFIN server. They must be
  addable **only** by editing the config file — no command-line flag, no interactive
  "add this origin?" prompt. A phishing page's natural next move is to tell the user to
  run a command it supplies; requiring a deliberate file edit raises that bar, and an
  interactive prompt reintroduces the human comparison established not to work.
- **Scheme rule, applies to every entry, static or config:** the scheme must be exactly
  `https` or exactly `http` — no other scheme is ever accepted, for any host. `http` is
  permitted **only** when the entry's host is a literal loopback IP address, checked with
  `ipaddress.ip_address(host).is_loopback`. The string `localhost` does not qualify — it
  requires a DNS or hosts-file lookup to resolve, which is exactly the kind of resolution
  step this rule exists to avoid; the host must be a literal IP address, not a name that
  happens to usually resolve to loopback. Any non-loopback host must use `https`; there is
  no case in which a non-loopback host is allowed `http`.

---

## 3. URL handling rules

### 3.1 Parse each URL exactly once per operation

When validating a URL, or when building a request from a stored one, parse it with a
single call into a structured object (e.g. `urllib.parse.urlsplit`) and read every field
you need — scheme, host, port, path segments, userinfo — off that one object for the
whole operation. Do not re-parse the same string later in the same operation, and do not
derive a field by a separate string operation (slicing, regex, `startswith`) alongside
the parsed object. The risk is checking one field against one parse and another field
against a second, later parse of a string that was normalized or mutated in between, so
the two checks silently evaluate different inputs.

This applies to request-building as much as to validation: see §5 for why.

### 3.2 Normalization

Apply to every URL before comparison:

- Lowercase the host.
- Strip a single trailing dot from the host (`bridge.simplefin.org.` resolves
  identically to `bridge.simplefin.org` but is a different string).
- Normalize the path by splitting into segments; drop empty trailing segments.

**Do not** normalize an absent port to the scheme default. `https://host` and
`https://host:443` are to be treated as different entries. This is deliberate — it is
strictly stricter than the alternative, so it can only cause false rejections, never
false acceptances of a wrong host.

### 3.3 Non-ASCII hostnames

Reject any hostname containing non-ASCII characters outright, and render any hostname
already in `xn--` (punycode) form as punycode — never decoded to Unicode — in all output,
including error messages.

Rationale: the allowlist comparison already rejects a homograph, since it's a different
string from the allowlist entry. Punycode-only rendering protects the error *message*.
If an error prints the rejected host as decoded Unicode, that host can be visually
indistinguishable from the correct one in some fonts, which leaves the user unable to
tell why validation failed.

### 3.4 Prefix matching on segment boundaries

Where one URL must be a prefix of another, compare **parsed path segment lists**, never
strings. String `startswith` accepts `/simplefin-evil` against a `/simplefin` prefix;
suffix tests on hostnames accept `bridge.simplefin.org.evil.example`; substring tests
accept anything containing the target as a substring.

Apply this to all of: the selected allowlist entry, the base64-decoded claim URL, and the
returned Access URL.

---

## 4. The `claim` subcommand

Current flow (roughly): user pastes token, CLI decodes, POSTs, edits
config.toml to add Access URL.

New flow (claim can still be provided on the command line, prompt if
it is omitted after they select the provider in step 1 below). We will
have to maintain state in the system default cache directory for the
app (or the directory indicated by the --cache command line argument)
and if the user is running `serve` in Docker they will need to mount
that directory.

1. **Menu.** Present the allowlist (static + config) and require the user to select the
   provider they obtained their token from. No free-text host entry. The user still
   navigates to their provider and obtains the token themselves, exactly as before — the
   menu exists solely to fix which allowlist entry the following steps validate against.

2. **Accept the pasted token.** Base64-decode strictly: reject on invalid padding,
   embedded whitespace beyond a trim, or decoded output that is not valid UTF-8/ASCII.
   Be deliberate about which base64 alphabet you accept and document the choice.

3. **Validate the decoded claim URL, before any network call**, against the selected
   menu entry:
   - scheme matches what §2 permits for that entry;
   - host and port match the entry exactly (§3.2 normalization rules);
   - the entry's path is a segment-boundary prefix of the claim URL's path (§3.4);
   - **no userinfo component.** A claim URL has no business carrying credentials, and a
     userinfo component preceding the real host is a classic way to smuggle a different
     host past a careless check;
   - no query and no fragment.

   Validating before the POST matters: a hostile host that you contact has already
   learned your egress IP and that the token is live, even if you reject the response.

4. **POST to the claim URL** with redirects disabled (§6) and TLS verification on (except
   the loopback-http case from §2). Handle `403` per spec checklist items 1–2: the token
   has already been claimed, which may mean it's compromised, and the user should be told
   to revoke it at the provider.

5. **Validate the returned Access URL** against the same selected menu entry, using the
   same rules as step 3, with one inversion: userinfo is **required** here, since the
   Access URL carries the Basic Auth credentials.

   Note the spec does not require the Access URL to share an origin with the claim URL —
   it merely happens to, in the providers we know of. We're enforcing an assumption the
   spec leaves open. If this check ever fires against a legitimate provider that has
   re-architected, the failure message from §8 should be diagnosable, not a dead end.

6. **Normalize and store the Access URL** — see §5.

---

## 5. Credential storage and the cache directory

The app currently has config but no cache. Add a cache, in the platform-specific cache
directory the rest of the app's conventions would use (check `ARCHITECTURE.md` and
existing config-path handling for the established pattern rather than introducing a new
one).

- Store a map of provider → normalized Access URL, as a single string (scheme, userinfo,
  host, port, path — normalized per §3.2). Do not store components separately; a single
  normalized string is sufficient, provided the two rules below are followed everywhere
  the stored value is used.
- **When building a request** (e.g. `/accounts`), parse the stored URL once (§3.1) and
  join the request path onto the parsed path's segments. Do not build the request URL by
  string-concatenating the stored value with a suffix like `"/accounts"` — a stored value
  ending in `#` or `?` would swallow such a suffix silently, and parsing first makes that
  impossible rather than merely unlikely.
- **When logging or displaying** the stored value for any reason (errors, debug output,
  `--verbose`), parse it first and reconstruct a credential-free version (scheme + host +
  port + path) to show. Never print, log, or include the raw stored string, since it
  contains the Basic Auth password. Audit existing log lines and exception messages for
  any place the raw value could currently leak.
- Create the cache file with mode `0600`.
- **Docker note:** `claim` may be run from the CLI on the host while `serve` runs inside
  a container. The cache directory must be a configurable path so it can be bind-mounted
  into the container, and this must be documented in the deployment instructions (README
  or wherever `serve`'s container setup is documented) — otherwise `serve` will find an
  empty cache and nothing will work, with no obvious cause.

---

## 6. Redirects

Disable redirects on **both** the claim POST and every `/accounts` GET. Treat any `3xx`
response as an error.

- `requests`: pass `allow_redirects=False` explicitly. Its default is `True`, and on a
  302 response to a POST it will silently convert the retry to a GET.
- `httpx2`: pass `follow_redirects=False` explicitly. This is already the client default,
  but set it anyway so a future refactor can't silently flip it.

The spec defines `/accounts` as returning only 200, 402, or 403 — there is no legitimate
redirect to accommodate.

---

## 7. Use-phase (re-)validation

Each cached Access URL is associated with a specific provider from the claim step, not
with "the allowlist" generically. On every `/accounts` request, re-validate that
specific cached entry's origin and path prefix against that same provider's *current*
allowlist entry (static or config) before making the request — this is a single-entry
comparison, not a scan of the whole list. It catches config drift and a provider that was
later removed from the allowlist.

---

## 8. Failure behaviour

Unknown or mismatched origin, at claim time or at use time, is a **hard failure**. No
prompt, no override flag, no `--force`.

Write clear error messages: state which URL failed, which specific check failed (scheme,
host, port, path prefix, userinfo), and — for the claim-time case — that a mismatch may
mean the token did not come from the provider the user thinks it did, with a pointer to
the config file for adding a genuinely self-hosted provider. Don't over-specify exact
wording; use judgment, the requirement is that a human reading the message can figure out
what went wrong without reading the source.

---

## 9. Tests

Use `https://simplefin.invalid/simplefin` as the allowlist entry in all test cases below
unless noted otherwise. `.invalid` is reserved by RFC 2606 and guaranteed not to resolve.

Claim URL validation:

| Input | Expected |
|---|---|
| `https://simplefin.invalid/simplefin/claim/tok` | accept |
| `https://SIMPLEFIN.INVALID/simplefin/claim/tok` | accept (host case-folded) |
| `https://simplefin.invalid./simplefin/claim/tok` | accept (trailing dot stripped) |
| `https://simplefin.invalid@evil.example/simplefin/claim/tok` | reject (userinfo) |
| `https://simplefin.invalid.evil.example/simplefin/claim/tok` | reject (suffix trick) |
| `https://evil.example/simplefin.invalid/claim/tok` | reject (substring trick) |
| `https://simplefin.invalid/simplefin-evil/claim/tok` | reject (segment boundary) |
| `https://simplefin.invalid:8443/simplefin/claim/tok` | reject (port) |
| `https://simplefin.invalid:443/simplefin/claim/tok` | reject (no default-port normalization) |
| `http://simplefin.invalid/simplefin/claim/tok` | reject (non-loopback host requires https) |
| `ftp://127.0.0.1/simplefin/claim/tok` | reject (scheme not http/https, even for loopback) |
| `file:///etc/passwd` | reject (scheme) |
| a Unicode homograph of `simplefin.invalid` | reject (non-ASCII host) |
| `https://simplefin.invalid/simplefin/claim/tok?x=1` | reject (query) |
| `https://simplefin.invalid/simplefin/claim/tok#frag` | reject (fragment) |

Loopback-http allowance, for a config entry with root `http://127.0.0.1/simplefin`:

| Input | Expected |
|---|---|
| `http://127.0.0.1/simplefin/claim/tok` | accept |
| `https://127.0.0.1/simplefin/claim/tok` | accept (https always allowed too) |
| `http://localhost/simplefin/claim/tok` | reject (literal IP required, not the name) |
| `http://127.0.0.2/simplefin/claim/tok` | reject (different host than the entry) |

Flow-level:

- token that is not valid base64 → clear error, no network call
- token decoding to non-ASCII bytes → clear error, no network call
- claim POST returns `302` → error, redirect not followed
- claim POST returns `403` → message telling the user the token may be compromised
- claim returns an Access URL on a different host than selected → reject
- claim returns an Access URL with no userinfo → reject
- claim returns an Access URL containing `#` in the path → reject
- `/accounts` returns `302` → error, redirect not followed
- a cached Access URL whose provider entry was removed from the allowlist → reject on
  next use
- a cached Access URL ending in `?` → request to `/accounts` targets the correct path,
  not the truncated one
- assert no test log output or exception message contains the Basic Auth password

---

