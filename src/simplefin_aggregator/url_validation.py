"""URL parsing and provider-allowlist matching, for claim time and for every use afterwards.

The threat being defended against is phishing and paste error. A SimpleFIN
setup token is a base64-encoded URL the user copies from a web page. If the
user was directed to a lookalike site -- a similar-looking domain, or a
Unicode homograph of a real one -- the token points entirely at attacker
infrastructure, and claiming it hands the attacker credentials that get
replayed on every sync thereafter.

Asking the user to confirm the decoded host does not help: in the phishing
case their memory of where they just were *is* the attacker's domain. The
only control that works is exact matching against a fixed set of known-good
provider roots, which is what this module implements.

The matching is deliberately one string comparison. Every URL is
reduced to its `origin_and_path` -- scheme, host, port, path, and
nothing else -- and a candidate matches a provider root when the root
is a prefix of it on a path segment boundary. Because that synthesized
string starts with the scheme and host, the single prefix test covers
scheme, host, port and path prefix at once; there is no separate check
for any of them to drift out of agreement. The decision not to
normalize port or trailing period on hostname (e.g. we treat
https://example.com.:433/ and https://example.com./ as different is
for simplicity, not correctness.

Two properties of that comparison are load-bearing. Roots always end in `/`,
so the prefix test cannot straddle a segment boundary: without it a
`/simplefin` root would match `/simplefin-evil`, and since the host is
likewise followed by `/` (or `:port`), `simplefin.example` cannot prefix-match
`simplefin.example.evil.test`. And candidate paths carry no `.` or `..`
segment, because the prefix test reads a path literally while an HTTP client
resolves it -- `/simplefin/../../evil` starts with `/simplefin/` as a string
but requests `/evil`. Both are enforced in `parse_url`.

"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, replace
from urllib.parse import unquote, urlsplit


class UrlValidationError(Exception):
    """A URL was rejected, with a message naming the URL and the specific problem.

    Messages must never contain credentials, and must not render the *path* of
    a provider-supplied URL. Build them from `NormalizedUrl.origin`, never from
    the raw URL string, which for an access URL contains the Basic Auth
    password.

    The path is withheld because a SimpleFIN claim URL carries the one-time
    setup token in it. A user who pastes a genuine token but picks the wrong
    provider from the menu gets a mismatch error, and that token is still live
    and unclaimed -- printing it into terminal scrollback or a log hands anyone
    who reads it a bearer credential. The origin is what identifies phishing,
    so withholding the path costs nothing diagnostically.

    A provider root is configuration rather than a secret, so messages about
    one may name it in full via `origin_and_path`.
    """


@dataclass(frozen=True)
class NormalizedUrl:
    """A URL parsed exactly once, with everything callers need in attributes.

    Callers must not re-parse the source string later in the same operation,
    and must not derive a field other than via `parse_url`.

    `origin_and_path` is scheme, host, port and path only. It carries no
    credentials, which makes it the string that matching compares.

    `origin` drops the path as well, leaving scheme, host and port. It is the
    form to render in an error message, because a claim URL's path holds the
    one-time setup token -- see `UrlValidationError`.

    Never fetch either one. Neither has credentials, and for a provider root
    `origin_and_path` carries a trailing slash the configured string need not
    have had. They are comparison and display strings, not URLs to request.
    """

    scheme: str
    host: str
    port: int | None
    username: str | None
    """username returned from urlsplit, *not* percent-decoded."""
    password: str | None
    """password returned from urlsplit, *not* percent-decoded."""
    origin: str
    origin_and_path: str

    @property
    def has_userinfo(self) -> bool:
        return self.username is not None or self.password is not None


def _parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Return `host` as an IP address, or None if it is a name rather than an address."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def is_loopback_host(host: str) -> bool:
    """Whether `host` is a literal loopback IP address (127.0.0.0/8 or ::1).

    Never a name, `localhost` included: a name resolves through DNS or a hosts
    file and can be pointed elsewhere.
    """
    ip = _parse_ip(host)
    return ip is not None and ip.is_loopback


def parse_url(raw: str) -> NormalizedUrl:
    """Parse `raw` exactly once and reduce it to the fields matching depends on.

    Rejects a hostname containing non-ASCII characters. The allowlist
    comparison would reject a homograph host anyway, since it is a different
    string from the entry -- rejecting it here keeps it out of the error
    *message*, where a Unicode host can be visually indistinguishable from the
    real one in some fonts, leaving the user unable to tell why validation
    failed. A host already in `xn--` (punycode) form is ASCII, and passes
    through untouched; it is never decoded for display.

    `urlsplit` lowercases the scheme and host before this sees them, so a URL
    differing from its provider root only in case still matches. DNS and TLS
    hostname matching are both case-insensitive, so that is the same origin
    rather than a different one being let through.
    """
    parsed = urlsplit(raw)

    hostname = parsed.hostname
    if hostname is None:
        no_host_msg = f"URL with scheme {parsed.scheme!r} has no host"
        raise UrlValidationError(no_host_msg)
    try:
        _ = hostname.encode("ascii")
    except UnicodeEncodeError:
        # Never echo the raw Unicode host -- rendering it is the harm, since it
        # can be visually identical to the real one. Its punycode form is ASCII,
        # unambiguous, and is what would actually have been resolved, so it is
        # safe to show and tells the user which host was rejected. The IDNA
        # codec itself rejects empty and over-long labels, hence the fallback.
        try:
            punycode = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            punycode = "<not representable as punycode>"
        non_ascii_msg = f"URL for host {punycode} (punycode) contains non-ASCII characters"
        raise UrlValidationError(non_ascii_msg) from None

    try:
        port = parsed.port
    except ValueError:
        # SplitResult.port raises for a non-numeric port.
        bad_port_msg = f"URL for host {hostname!r} has a malformed port"
        raise UrlValidationError(bad_port_msg) from None

    # An IPv6 host loses its brackets in SplitResult.hostname and needs them
    # back to reassemble a valid URL. Roots and candidates go through this same
    # synthesis, so the two can never disagree about bracketing.
    netloc = f"[{hostname}]" if isinstance(_parse_ip(hostname), ipaddress.IPv6Address) else hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    origin = f"{parsed.scheme}://{netloc}"
    origin_and_path = f"{origin}{parsed.path}"

    # A URL is ASCII by definition (RFC 3986); anything else must be
    # percent-encoded, so nothing legitimate is turned away here. This matters
    # because a rejected URL goes into an error message the user sees: without
    # this, a claim URL carrying ANSI escapes in its path could rewrite the
    # terminal as it is reported. Checked against the raw string, since
    # urlsplit silently strips tab/CR/LF before the components are built.
    # A non-ASCII *host* is reported above with a more specific message, so by
    # here that case is already handled.
    if any(not ("\x21" <= character <= "\x7e") for character in raw):
        # The one message that renders a candidate's path, since the offending
        # characters are usually in it and naming only the origin would leave
        # the user nothing to act on. Safe here because a URL carrying control
        # characters is malformed rather than a live setup token: a real one is
        # percent-encoded, so it cannot reach this branch. ascii() escapes
        # anything unprintable, so the message cannot drive the terminal.
        unprintable_msg = (
            f"URL {origin_and_path!a} contains characters that are not printable ASCII"
        )
        raise UrlValidationError(unprintable_msg)

    # Tested against the raw string rather than SplitResult.query/.fragment,
    # which are both the empty string -- falsy -- for a URL ending in a bare
    # "?" or "#".
    if "?" in raw or "#" in raw:
        query_msg = f"{origin} must not contain a query string or fragment"
        raise UrlValidationError(query_msg)

    # Reject a "." or ".." path segment rather than resolving it. Matching
    # compares paths as written, but httpx2 resolves dot segments when it builds
    # a request from `base_url`, so the two disagree about what a path means:
    # "/simplefin/../../evil" prefix-matches a "/simplefin/" root as a string,
    # then requests "/evil". The percent-encoded spellings go too -- httpx2
    # leaves "%2e%2e" alone, but RFC 3986 makes "%2E" equivalent to "." once
    # normalized, so an origin server is entitled to resolve it.
    #
    # Normalizing instead is tempting, since `httpx2.URL` will resolve dot
    # segments for us, but it does not close this hole and opens another. For
    # the encoded spelling its own views disagree -- `str()` keeps "%2e%2e"
    # verbatim while `.path` decodes it to "/../.." without resolving -- so the
    # check below would still be needed. And it strips default ports, which
    # this module deliberately does not do. The standard library is no help
    # either: urljoin applies remove_dot_segments only to a relative reference,
    # and posixpath.normpath is filesystem semantics, ignoring "%2e",
    # collapsing "//", and stripping the trailing slash roots depend on.
    #
    # tests/test_url_validation.py asserts the agreement this relies on: for
    # every URL accepted here, the path httpx2 requests is the path we matched.
    if any(unquote(segment) in (".", "..") for segment in parsed.path.split("/")):
        dot_segment_msg = f"{origin} must not contain a '.' or '..' path segment"
        raise UrlValidationError(dot_segment_msg)

    return NormalizedUrl(
        scheme=parsed.scheme,
        host=hostname,
        port=port,
        username=parsed.username,
        password=parsed.password,
        origin=origin,
        origin_and_path=origin_and_path,
    )


def parse_root(raw: str) -> NormalizedUrl:
    """Parse a provider root URL from the static allowlist or from config."""
    root = parse_url(raw)

    if root.has_userinfo:
        userinfo_msg = f"provider root {root.origin_and_path} must not contain credentials"
        raise UrlValidationError(userinfo_msg)

    # https is always allowed. http is allowed only for a self-hosted server
    # reached over the loopback interface.
    if not (root.scheme == "https" or (root.scheme == "http" and is_loopback_host(root.host))):
        scheme_msg = (
            f"provider root {root.origin_and_path} must use https, "
            "or http with a literal loopback IP address as the host"
        )
        raise UrlValidationError(scheme_msg)

    # The root must have a trailing slash in order for prefix-matching
    # to work (e.g. otherwise `/simplefin` would match
    # `/simplefin-evil`). As a convenience, append one if it is not
    # present in the configuration -- the parsed root is only used for
    # comparison, not for fetching.
    if root.origin_and_path.endswith("/"):
        return root
    return replace(root, origin_and_path=root.origin_and_path + "/")


def _check_matches_root(root: NormalizedUrl, url: NormalizedUrl, kind: str, provider: str) -> None:
    # One comparison covers scheme, host, port and path prefix, because
    # origin_and_path begins with the scheme and host. The trailing slash
    # appended to the candidate lets an access URL equal to the root itself
    # match ("https://h/simplefin" against a "https://h/simplefin/" root),
    # while still failing on a segment boundary ("https://h/simplefin-evil").
    if not (url.origin_and_path + "/").startswith(root.origin_and_path):
        msg = (
            f"{kind} {url.origin} is not valid for provider {provider!r}: "
            f"expected it to start with {root.origin_and_path}"
        )
        raise UrlValidationError(msg)


def validate_claim_url(root: NormalizedUrl, raw: str, *, provider: str) -> NormalizedUrl:
    """Validate a base64-decoded claim URL against the provider root the user selected."""
    url = parse_url(raw)
    # A claim URL has no need for credentials.
    if url.has_userinfo:
        msg = f"claim URL {url.origin} must not contain credentials"
        raise UrlValidationError(msg)
    _check_matches_root(root, url, "claim URL", provider)
    return url


def validate_access_url(root: NormalizedUrl, raw: str, *, provider: str) -> NormalizedUrl:
    """Validate a returned access URL against the same provider root as the claim URL.

    The SimpleFIN spec does not actually require the access URL to share an
    origin with the claim URL -- it merely does, for every provider known when
    this was written. Enforcing it closes an assumption the spec leaves open,
    on the theory that an access URL arriving on a surprising origin is far
    likelier to be a compromised or misbehaving provider than a legitimate
    re-architecture. If this ever fires against a real provider, the message
    says which URL and which root disagreed, so it is diagnosable rather than
    a dead end.
    """
    url = parse_url(raw)
    if not url.has_userinfo:
        msg = f"access URL {url.origin} must contain credentials"
        raise UrlValidationError(msg)
    _check_matches_root(root, url, "access URL", provider)
    return url
