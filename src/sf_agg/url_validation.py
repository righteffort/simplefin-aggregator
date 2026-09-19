# SPDX-License-Identifier: GPL-3.0-only

"""Match provider URLs against a fixed set of known-good origins.

The threat is phishing and paste error. A SimpleFIN setup token is a
base64-encoded URL the user copies from a web page; if that page was a
lookalike -- a similar-looking domain, or a Unicode homograph of a real one --
the token points entirely at attacker infrastructure, and every request made
with the access URL goes to the attacker's host, which can craft an arbitrary
HTTP response and learns when and from where each request is made. Asking the
user to confirm the host does not help, because in the phishing case their
memory of where they just were *is* the attacker's domain. Exact matching against
known-good origins minimizes the risk. The origin -- scheme, host and port --
is all that is matched: what a known-good host serves at any path is its
operator's.

Two rules, each with a limit worth knowing:

1. A URL is accepted only when its origin equals a provider's, as normalized by
   the same `httpx2.URL` parse that the request is later built from: a
   redundant `:443` or an uppercase host matches, while a trailing dot on the
   host, which httpx2 leaves alone, does not.

2. Messages do not print secrets -- an access URL's credentials, a claim URL's
   setup token. Best effort, not a guarantee: see `UrlValidationError`.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx2


class UrlValidationError(Exception):
    """A URL was rejected, with a message complete on its own.

    A message names a URL only through `NormalizedUrl.origin`, never the raw
    string: an access URL's raw string holds the Basic Auth password, and a
    claim URL's path holds a secret that is still live when a mismatch is
    reported, with its setup token not yet exchanged.

    `origin` ends at the first "/", "?" or "#" after the "//", so a claim URL's
    secret, being in its path, cannot reach it. An access URL's password comes
    before that point, and any of those characters unencoded in it ends the
    origin early: with only digits before it, the username is read as the host
    and those digits as the port, and the message rejecting the URL names
    both. Only a broken provider issues one, and the credential is unusable
    anyway, since every client misparses it the same way.
    """


@dataclass(frozen=True)
class NormalizedUrl:
    """A URL parsed once, reduced to what matching and display need.

    Read every field an operation needs off one of these rather than parsing
    the source string again: two parses of a string that was normalized in
    between silently check different inputs.

    `origin` is what matching compares and what messages name;
    `origin_and_path` is what a request is sent to. Neither carries
    credentials.
    """

    scheme: str
    host: str
    """Lowercased, and punycode if the source was non-ASCII."""
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
    accepts, and a query string or fragment.

    Everything else is normalized rather than refused: scheme and host
    lowercased, a non-ASCII host punycoded, a default port dropped, a character
    not legal in a path percent-encoded.
    """
    try:
        url = httpx2.URL(raw)
        # urlsplit parses `raw` again for a port range check, since
        # SplitResult.port raises for one outside 0-65535 where httpx2 accepts
        # any integer. It also raises on a bracketed host that httpx2 accepts
        # ("https://h]/x"), which is why it sits inside this guard.
        _ = urlsplit(raw).port
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

    return NormalizedUrl(
        scheme=url.scheme,
        host=host,
        username=url.username,
        password=url.password,
        origin=origin,
        origin_and_path=origin_and_path,
    )


def parse_origin(raw: str) -> str:
    """Parse a provider origin from the built-in list or from config into its normalized form."""
    url = parse_url(raw)

    if url.has_creds:
        creds_msg = f"provider origin {url.origin} must not contain credentials"
        raise UrlValidationError(creds_msg)

    # https is always allowed. http is allowed only for a server reached over
    # the loopback interface.
    if not (url.scheme == "https" or (url.scheme == "http" and is_loopback_host(url.host))):
        scheme_msg = (
            f"provider origin {url.origin} must use https, "
            "or http with a literal loopback IP address as the host"
        )
        raise UrlValidationError(scheme_msg)

    if url.origin_and_path not in (url.origin, url.origin + "/"):
        path_msg = f"provider origin {url.origin} must not have a path"
        raise UrlValidationError(path_msg)

    return url.origin


def _check_matches_origin(origin: str, url: NormalizedUrl, kind: str, provider: str) -> None:
    if url.origin != origin:
        # `provider` names the entry in the message. Pass a key from
        # find_provider; ProviderEntry constrains those to [a-z0-9][a-z0-9-]*.
        msg = f"{kind} {url.origin} is not valid for provider {provider!r}: expected {origin}"
        raise UrlValidationError(msg)


def validate_claim_url(origin: str, raw: str, *, provider: str) -> NormalizedUrl:
    """Validate a base64-decoded claim URL against the provider origin the user selected."""
    url = parse_url(raw)
    if url.has_creds:
        msg = f"claim URL {url.origin} must not contain credentials"
        raise UrlValidationError(msg)
    _check_matches_origin(origin, url, "claim URL", provider)
    return url


def validate_access_url(origin: str, raw: str, *, provider: str) -> NormalizedUrl:
    """Validate a returned access URL against the same provider origin as the claim URL."""
    url = parse_url(raw)
    if not url.has_creds:
        msg = f"access URL {url.origin} must contain credentials"
        raise UrlValidationError(msg)
    _check_matches_origin(origin, url, "access URL", provider)
    return url
