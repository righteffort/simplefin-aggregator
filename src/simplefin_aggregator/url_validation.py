"""Match provider URLs against a fixed set of known-good roots.

The threat is phishing and paste error. A SimpleFIN setup token is a
base64-encoded URL the user copies from a web page; if that page was a
lookalike -- a similar-looking domain, or a Unicode homograph of a real one --
the token points entirely at attacker infrastructure, and claiming it hands
over credentials that are replayed on every sync thereafter. Asking the user to
confirm the host does not help, because in the phishing case their memory of
where they just were *is* the attacker's domain. Exact matching against
known-good roots is the only control that works.

Three rules, each with a limit worth knowing:

1. A URL is accepted only when a provider root is a prefix of it, compared as
   one string. `origin_and_path` is that string -- scheme, host, port, path,
   nothing else -- and because it starts with the scheme and host, the single
   test covers all four at once. Roots end in "/", so it cannot straddle a
   segment boundary. The comparison is on `httpx2.URL`'s normalized form, so
   "the same URL" means "the same once normalized": a redundant `:443` or an
   unencoded space matches, while a trailing dot on the host, which httpx2
   leaves alone, does not.

2. The string compared is the string fetched, since both are httpx2's own
   rendering and cannot drift apart. The exception is a "." or ".." path
   segment, where httpx2's own views disagree; those are rejected rather than
   resolved.

3. Messages do not print secrets -- an access URL's credentials, a claim URL's
   setup token. Best effort rather than a guarantee: see `UrlValidationError`.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, replace
from urllib.parse import unquote, urlsplit

import httpx2


class UrlValidationError(Exception):
    """A URL was rejected, with a message complete on its own.

    A message names a URL only through `NormalizedUrl.origin` or
    `origin_and_path`, never the raw string: an access URL's raw string holds
    the Basic Auth password, and a claim URL's path holds the one-time setup
    token, which is still live and unclaimed when a mismatch is reported. A
    provider root is configuration rather than a secret, so messages about one
    name it in full.

    This holds for input well-formed enough to parse as intended. A URL
    malformed past that point can put a fragment of a credential in `origin` --
    an unencoded "/" in a password ends the authority early, and the password
    prefix is then read as the port. That gap is left open deliberately: the
    checks needed to close it cost more in complexity than the corner case is
    worth.
    """


@dataclass(frozen=True)
class NormalizedUrl:
    """A URL parsed once, reduced to what matching and display need.

    Read every field an operation needs off one of these rather than parsing
    the source string again: two parses of a string that was normalized in
    between silently check different inputs.

    `origin_and_path` is what matching compares; `origin` drops the path and is
    what messages name. Neither carries credentials, and neither is a URL to
    fetch -- a root's `origin_and_path` has a trailing slash the configured
    string need not have had.
    """

    scheme: str
    host: str
    """Lowercased, and punycode if the source was non-ASCII."""
    port: int | None
    """None when absent, and when it is the scheme's default."""
    username: str
    """Percent-decoded; empty when absent."""
    password: str
    """Percent-decoded; empty when absent."""
    origin: str
    origin_and_path: str

    @property
    def has_creds(self) -> bool:
        """Whether either credential is non-empty. A bare "@" or ":@" carries neither."""
        return bool(self.username or self.password)


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
    """Parse `raw` once, into the fields matching and display need.

    Rejects what `httpx2.URL` rejects -- a control character anywhere, which is
    also what stops CRLF splicing; a non-numeric port; a hostname that is not
    valid IDNA -- plus a missing host, a port outside the range `urlsplit`
    accepts, a query string or fragment, and a "." or ".." path segment.

    Everything else is normalized rather than refused: scheme and host
    lowercased, a non-ASCII host punycoded, a default port dropped, a character
    not legal in a path percent-encoded.
    """
    try:
        url = httpx2.URL(raw)
        # urlsplit parses `raw` again for two things httpx2 does not give:
        # the path as written (see _has_dot_segment), and a port range check,
        # since SplitResult.port raises for one outside 0-65535 where httpx2
        # accepts any integer. It also raises on a bracketed host that httpx2
        # accepts ("https://h]/x"), which is why it sits inside this guard.
        split = urlsplit(raw)
        path_as_written = split.path
        _ = split.port
    except (httpx2.InvalidURL, ValueError):
        # The parser's own text quotes a slice of what it choked on, which for
        # a failed parse can be any part of the string, credentials included.
        invalid_msg = "not a valid URL"
        raise UrlValidationError(invalid_msg) from None

    # .raw_host, never .host: .host decodes punycode back to the Unicode that a
    # homograph attack wants rendered.
    host = url.raw_host.decode("ascii")
    if not host:
        # httpx2 does not require a host: "file:///etc/passwd" parses fine.
        no_host_msg = f"URL with scheme {url.scheme!a} has no host"
        raise UrlValidationError(no_host_msg)

    stripped = url.copy_with(userinfo=b"", query=None, fragment=None)
    origin = str(stripped.copy_with(raw_path=b""))
    origin_and_path = str(stripped)

    # Against the raw string, since url.query and url.fragment are both empty
    # for a URL ending in a bare "?" or "#".
    if "?" in raw or "#" in raw:
        query_msg = f"{origin} must not contain a query string or fragment"
        raise UrlValidationError(query_msg)

    if _has_dot_segment(path_as_written) or _has_dot_segment(url.path):
        dot_segment_msg = f"{origin} must not contain a '.' or '..' path segment"
        raise UrlValidationError(dot_segment_msg)

    return NormalizedUrl(
        scheme=url.scheme,
        host=host,
        port=url.port,
        username=url.username,
        password=url.password,
        origin=origin,
        origin_and_path=origin_and_path,
    )


def _has_dot_segment(path: str) -> bool:
    """Whether `path` has a "." or ".." segment, in either view of it.

    Matching reads a path literally while a server resolves it, so
    "/simplefin/../../evil" prefix-matches a "/simplefin/" root while naming
    "/evil". The path as written catches the plain spelling, which httpx2
    resolves before this sees it; httpx2's percent-decoded `.path` catches
    "%2e%2e" and "%2f..", which it passes through for a server to resolve.
    """
    return any(unquote(segment) in (".", "..") for segment in path.split("/"))


def parse_root(raw: str) -> NormalizedUrl:
    """Parse a provider root URL from the static allowlist or from config."""
    root = parse_url(raw)

    if root.has_creds:
        creds_msg = f"provider root {root.origin_and_path} must not contain credentials"
        raise UrlValidationError(creds_msg)

    # https is always allowed. http is allowed only for a self-hosted server
    # reached over the loopback interface.
    if not (root.scheme == "https" or (root.scheme == "http" and is_loopback_host(root.host))):
        scheme_msg = (
            f"provider root {root.origin_and_path} must use https, "
            "or http with a literal loopback IP address as the host"
        )
        raise UrlValidationError(scheme_msg)

    # Prefix-matching needs the trailing slash, or a "/simplefin" root would
    # match "/simplefin-evil". A root is only ever compared, never fetched, so
    # appending one is safe.
    if root.origin_and_path.endswith("/"):
        return root
    return replace(root, origin_and_path=root.origin_and_path + "/")


def _check_matches_root(root: NormalizedUrl, url: NormalizedUrl, kind: str, provider: str) -> None:
    # One comparison covers scheme, host, port and path prefix, because
    # origin_and_path begins with the scheme and host. The trailing slash on
    # the candidate lets an access URL equal to the root itself match
    # ("https://h/simplefin" against a "https://h/simplefin/" root), while
    # still failing on a segment boundary ("https://h/simplefin-evil").
    if not (url.origin_and_path + "/").startswith(root.origin_and_path):
        # `provider` names the entry in the message. Pass a slug from
        # find_provider; ProviderEntry constrains those to [a-z0-9-]+.
        msg = (
            f"{kind} {url.origin} is not valid for provider {provider!r}: "
            f"expected it to start with {root.origin_and_path}"
        )
        raise UrlValidationError(msg)


def validate_claim_url(root: NormalizedUrl, raw: str, *, provider: str) -> NormalizedUrl:
    """Validate a base64-decoded claim URL against the provider root the user selected."""
    url = parse_url(raw)
    if url.has_creds:
        msg = f"claim URL {url.origin} must not contain credentials"
        raise UrlValidationError(msg)
    _check_matches_root(root, url, "claim URL", provider)
    return url


def validate_access_url(root: NormalizedUrl, raw: str, *, provider: str) -> NormalizedUrl:
    """Validate a returned access URL against the same provider root as the claim URL.

    The SimpleFIN spec does not require the access URL to share an origin with
    the claim URL -- it merely does, for every provider known when this was
    written. Enforcing it closes an assumption the spec leaves open, on the
    theory that an access URL arriving on a surprising origin is likelier to be
    a compromised or misbehaving provider than a legitimate re-architecture.
    """
    url = parse_url(raw)
    if not url.has_creds:
        msg = f"access URL {url.origin} must contain credentials"
        raise UrlValidationError(msg)
    _check_matches_root(root, url, "access URL", provider)
    return url
