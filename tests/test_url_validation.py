"""Tests for url_validation.py's provider-root matching.

`.invalid` (RFC 2606) is reserved and guaranteed never to resolve, so it is
safe as a stand-in provider host with no risk of a real lookup.

Cases marked "behaviour, not a requirement" pin what the module does today in
a situation where doing something else would be at least as correct -- an input it
declines to normalize, or a gap it deliberately leaves open. A future
implementation may change them; they are here so that such a change shows up
in a diff rather than passing unnoticed.
"""

from __future__ import annotations

import pytest

from simplefin_aggregator.url_validation import (
    UrlValidationError,
    is_loopback_host,
    parse_root,
    parse_url,
    validate_access_url,
    validate_claim_url,
)


ROOT = parse_root("https://simplefin.invalid/simplefin")
LOOPBACK_ROOT = parse_root("http://127.0.0.1/simplefin")
# A second provider root, for the case where the pasted token is genuine but
# the user picked the wrong entry from the claim menu.
OTHER_ROOT = parse_root("https://other-provider.invalid/simplefin")

# Stands in for the one-time setup token a real claim URL carries in its path.
SETUP_TOKEN = "s3cret-setup-token"  # noqa: S105

PROVIDER = "test-provider"


CLAIM_URL_CASES: list[tuple[str, bool]] = [
    ("https://simplefin.invalid/simplefin/claim/tok", True),
    # Scheme and host are case-insensitive to DNS and to TLS alike, so a URL
    # differing from the root only in their case names the same origin. The
    # path is not case-folded.
    ("https://SIMPLEFIN.INVALID/simplefin/claim/tok", True),
    ("https://simplefin.invalid/SIMPLEFIN/claim/tok", False),
    # A trailing dot names the same host to DNS, but is left as a distinct
    # string. Behaviour, not a requirement: normalizing it would be as correct.
    ("https://simplefin.invalid./simplefin/claim/tok", False),
    ("https://simplefin.invalid@evil.example/simplefin/claim/tok", False),
    ("https://simplefin.invalid.evil.example/simplefin/claim/tok", False),
    ("https://evil.example/simplefin.invalid/claim/tok", False),
    ("https://simplefin.invalid/simplefin-evil/claim/tok", False),
    ("https://simplefin.invalid:8443/simplefin/claim/tok", False),
    # The scheme's default port names the same origin as no port at all.
    ("https://simplefin.invalid:443/simplefin/claim/tok", True),
    ("https://simplefin.invalid:notaport/simplefin/claim/tok", False),
    ("http://simplefin.invalid/simplefin/claim/tok", False),
    ("ftp://127.0.0.1/simplefin/claim/tok", False),
    ("file:///etc/passwd", False),
    ("https://simplefin.invalid/simplefin/claim/tok?x=1", False),
    ("https://simplefin.invalid/simplefin/claim/tok#frag", False),
    # A bare delimiter with nothing after it is still a query or a fragment.
    ("https://simplefin.invalid/simplefin/claim/tok?", False),
    ("https://simplefin.invalid/simplefin/claim/tok#", False),
    # An empty path segment, left as written. Behaviour, not a requirement.
    ("https://simplefin.invalid//simplefin/claim/tok", False),
]


@pytest.mark.parametrize(("raw", "accepted"), CLAIM_URL_CASES)
def test_validate_claim_url_table(raw: str, *, accepted: bool) -> None:
    if accepted:
        _ = validate_claim_url(ROOT, raw, provider=PROVIDER)
    else:
        with pytest.raises(UrlValidationError):
            _ = validate_claim_url(ROOT, raw, provider=PROVIDER)


HOMOGRAPH_HOST = "simplefin.invalid".replace("p", "\u0440")  # Cyrillic er, drawn like p


def test_validate_claim_url_rejects_unicode_homograph() -> None:
    """A host that merely draws like the root's host is a different host, and must not match."""
    with pytest.raises(UrlValidationError) as exc_info:
        _ = validate_claim_url(
            ROOT, f"https://{HOMOGRAPH_HOST}/simplefin/claim/tok", provider=PROVIDER
        )
    assert "is not valid for provider" in str(exc_info.value)


def test_validate_claim_url_error_never_renders_a_unicode_host() -> None:
    """The message names the punycode form: legible, and not mistakable for the real host."""
    with pytest.raises(UrlValidationError) as exc_info:
        _ = validate_claim_url(
            ROOT, f"https://{HOMOGRAPH_HOST}/simplefin/claim/tok", provider=PROVIDER
        )
    message = str(exc_info.value)
    assert HOMOGRAPH_HOST not in message
    assert HOMOGRAPH_HOST.encode("idna").decode("ascii") in message


CLAIM_URLS_WITH_CREDENTIALS = [
    "https://user:pass@simplefin.invalid/simplefin/claim/tok",
    # Either half on its own is still a credential.
    "https://user@simplefin.invalid/simplefin/claim/tok",
    "https://:pass@simplefin.invalid/simplefin/claim/tok",
]


@pytest.mark.parametrize("raw", CLAIM_URLS_WITH_CREDENTIALS)
def test_validate_claim_url_rejects_credentials(raw: str) -> None:
    with pytest.raises(UrlValidationError, match="must not contain credentials"):
        _ = validate_claim_url(ROOT, raw, provider=PROVIDER)


EMPTY_USERINFO_URLS = [
    "https://@simplefin.invalid/simplefin/claim/tok",
    "https://:@simplefin.invalid/simplefin/claim/tok",
]


@pytest.mark.parametrize("raw", EMPTY_USERINFO_URLS)
def test_validate_claim_url_accepts_an_empty_userinfo_section(raw: str) -> None:
    """An empty section carries no credential, and names the same URL as one without it.

    Behaviour, not a requirement: rejecting the section outright would be
    equally correct, and no provider emits one.
    """
    url = validate_claim_url(ROOT, raw, provider=PROVIDER)

    assert url.has_creds is False
    assert url.origin_and_path == "https://simplefin.invalid/simplefin/claim/tok"


def test_validate_claim_url_message_names_origin_provider_and_expected_root() -> None:
    with pytest.raises(UrlValidationError) as exc_info:
        _ = validate_claim_url(ROOT, "https://evil.example/simplefin/claim/tok", provider=PROVIDER)
    message = str(exc_info.value)
    assert "https://evil.example" in message
    assert PROVIDER in message
    assert ROOT.origin_and_path in message


LOOPBACK_HOST_CASES: list[tuple[str, bool]] = [
    ("127.0.0.1", True),
    ("127.1.2.3", True),  # the whole of 127.0.0.0/8, not just 127.0.0.1
    ("::1", True),
    # A name is never loopback here, however it happens to resolve today.
    ("localhost", False),
    ("localhost.localdomain", False),
    ("simplefin.invalid", False),
    ("10.0.0.1", False),
]


@pytest.mark.parametrize(("host", "expected"), LOOPBACK_HOST_CASES)
def test_is_loopback_host_accepts_only_literal_addresses(host: str, *, expected: bool) -> None:
    assert is_loopback_host(host) is expected


DOT_SEGMENT_URLS = [
    "https://simplefin.invalid/simplefin/../../evil",
    "https://simplefin.invalid/simplefin/./claim/tok",
    "https://simplefin.invalid/simplefin/..",
    # RFC 3986 makes %2E equivalent to "." once normalized, so the encoded
    # spellings are rejected as well.
    "https://simplefin.invalid/simplefin/%2e%2e/evil",
    "https://simplefin.invalid/simplefin/%2E%2E/evil",
    # A percent-encoded slash: an origin server that decodes it sees a ".."
    # segment where the path as written shows none.
    "https://simplefin.invalid/simplefin/%2f../evil",
]


@pytest.mark.parametrize("raw", DOT_SEGMENT_URLS)
def test_parse_url_rejects_dot_segments(raw: str) -> None:
    with pytest.raises(UrlValidationError, match="path segment"):
        _ = parse_url(raw)


DOTTED_BUT_LEGITIMATE_URLS = [
    # Dots inside a segment are ordinary characters, not dot segments.
    "https://simplefin.invalid/simplefin/a.b/claim/tok",
    "https://simplefin.invalid/simplefin/..c/claim/tok",
    "https://simplefin.invalid/simplefin/tok...",
]


@pytest.mark.parametrize("raw", DOTTED_BUT_LEGITIMATE_URLS)
def test_parse_url_allows_dots_inside_a_segment(raw: str) -> None:
    assert parse_url(raw).origin_and_path == raw


def test_dot_segments_cannot_escape_the_provider_root() -> None:
    """The prefix test reads a path literally; whoever resolves it does not.

    Without this rejection "/simplefin/../../evil" prefix-matches a
    "/simplefin/" root as a string, and the provider's credentials then go to
    "/evil".
    """
    escaping = "https://user:pass@simplefin.invalid/simplefin/../../evil"

    with pytest.raises(UrlValidationError, match="path segment"):
        _ = validate_access_url(ROOT, escaping, provider=PROVIDER)


def test_parse_root_rejects_dot_segments() -> None:
    with pytest.raises(UrlValidationError, match="path segment"):
        _ = parse_root("https://simplefin.invalid/simplefin/../other")


def test_origin_excludes_the_path() -> None:
    url = parse_url(f"https://simplefin.invalid:8443/simplefin/claim/{SETUP_TOKEN}")

    assert url.origin == "https://simplefin.invalid:8443"
    assert SETUP_TOKEN in url.origin_and_path


def test_mismatch_message_withholds_the_setup_token() -> None:
    """The paste-error case: a genuine token, but the wrong menu entry selected.

    That token is still live and unclaimed, so printing it into terminal
    scrollback would hand a bearer credential to anyone who reads it.
    """
    with pytest.raises(UrlValidationError) as exc_info:
        _ = validate_claim_url(
            OTHER_ROOT,
            f"https://simplefin.invalid/simplefin/claim/{SETUP_TOKEN}",
            provider=PROVIDER,
        )

    assert SETUP_TOKEN not in str(exc_info.value)


def test_claim_url_credentials_message_withholds_the_setup_token() -> None:
    with pytest.raises(UrlValidationError) as exc_info:
        _ = validate_claim_url(
            ROOT, f"https://u:p@simplefin.invalid/simplefin/claim/{SETUP_TOKEN}", provider=PROVIDER
        )

    assert SETUP_TOKEN not in str(exc_info.value)


def test_query_string_message_withholds_the_setup_token() -> None:
    with pytest.raises(UrlValidationError) as exc_info:
        _ = parse_url(f"https://simplefin.invalid/simplefin/claim/{SETUP_TOKEN}?redirect=1")

    assert SETUP_TOKEN not in str(exc_info.value)


LOOPBACK_CASES: list[tuple[str, bool]] = [
    ("http://127.0.0.1/simplefin/claim/tok", True),
    # The entry is rooted at http, so https is a different origin string and
    # does not match it.
    ("https://127.0.0.1/simplefin/claim/tok", False),
    ("http://localhost/simplefin/claim/tok", False),
    ("http://127.0.0.2/simplefin/claim/tok", False),
]


@pytest.mark.parametrize(("raw", "accepted"), LOOPBACK_CASES)
def test_validate_claim_url_loopback_table(raw: str, *, accepted: bool) -> None:
    if accepted:
        _ = validate_claim_url(LOOPBACK_ROOT, raw, provider=PROVIDER)
    else:
        with pytest.raises(UrlValidationError):
            _ = validate_claim_url(LOOPBACK_ROOT, raw, provider=PROVIDER)


def test_validate_access_url_accepts_the_root_itself_with_credentials() -> None:
    url = validate_access_url(
        ROOT, "https://user:pass@simplefin.invalid/simplefin", provider=PROVIDER
    )
    assert url.username == "user"
    assert url.password == "pass"  # noqa: S105


ACCESS_URLS_WITHOUT_CREDENTIALS = [
    "https://simplefin.invalid/simplefin",
    # A userinfo section that authenticates as empty/empty is not credentials.
    "https://:@simplefin.invalid/simplefin",
    "https://@simplefin.invalid/simplefin",
]


@pytest.mark.parametrize("raw", ACCESS_URLS_WITHOUT_CREDENTIALS)
def test_validate_access_url_requires_credentials(raw: str) -> None:
    with pytest.raises(UrlValidationError, match="must contain credentials"):
        _ = validate_access_url(ROOT, raw, provider=PROVIDER)


def test_validate_access_url_rejects_different_host() -> None:
    with pytest.raises(UrlValidationError):
        _ = validate_access_url(ROOT, "https://user:pass@evil.example/simplefin", provider=PROVIDER)


def test_validate_access_url_rejects_fragment() -> None:
    with pytest.raises(UrlValidationError):
        _ = validate_access_url(
            ROOT, "https://user:pass@simplefin.invalid/simplefin#frag", provider=PROVIDER
        )


def test_validate_access_url_rejects_trailing_question_mark() -> None:
    # Must never reach the store: a stored URL ending in "?" would swallow a
    # request path appended to it.
    with pytest.raises(UrlValidationError):
        _ = validate_access_url(
            ROOT, "https://user:pass@simplefin.invalid/simplefin?", provider=PROVIDER
        )


def test_access_url_origin_and_path_excludes_credentials() -> None:
    url = validate_access_url(
        ROOT, "https://user:hunter2@simplefin.invalid/simplefin", provider=PROVIDER
    )
    assert url.origin_and_path == "https://simplefin.invalid/simplefin"
    assert "hunter2" not in url.origin_and_path


def test_access_url_error_message_excludes_credentials() -> None:
    with pytest.raises(UrlValidationError) as exc_info:
        _ = validate_access_url(
            ROOT, "https://user:hunter2@evil.example/simplefin", provider=PROVIDER
        )
    assert "hunter2" not in str(exc_info.value)


def test_parse_root_appends_missing_trailing_slash() -> None:
    assert parse_root("https://simplefin.invalid/simplefin").origin_and_path == (
        "https://simplefin.invalid/simplefin/"
    )


def test_parse_root_keeps_existing_trailing_slash() -> None:
    assert parse_root("https://simplefin.invalid/simplefin/").origin_and_path == (
        "https://simplefin.invalid/simplefin/"
    )


def test_parse_root_with_no_path_gets_a_slash() -> None:
    assert parse_root("https://simplefin.invalid").origin_and_path == "https://simplefin.invalid/"


def test_parse_root_accepts_http_for_literal_loopback() -> None:
    assert parse_root("http://127.0.0.1:8888/simplefin").origin_and_path == (
        "http://127.0.0.1:8888/simplefin/"
    )


def test_parse_root_accepts_http_for_ipv6_loopback_and_keeps_brackets() -> None:
    assert parse_root("http://[::1]/simplefin").origin_and_path == "http://[::1]/simplefin/"


def test_parse_root_rejects_http_for_localhost_by_name() -> None:
    with pytest.raises(UrlValidationError, match="loopback"):
        _ = parse_root("http://localhost/simplefin")


def test_parse_root_rejects_http_for_non_loopback_host() -> None:
    with pytest.raises(UrlValidationError, match="loopback"):
        _ = parse_root("http://simplefin.invalid/simplefin")


def test_parse_root_rejects_non_http_scheme() -> None:
    with pytest.raises(UrlValidationError):
        _ = parse_root("ftp://simplefin.invalid/simplefin")


def test_parse_root_rejects_credentials() -> None:
    with pytest.raises(UrlValidationError, match="must not contain credentials"):
        _ = parse_root("https://user:pass@simplefin.invalid/simplefin")


def test_parse_root_rejects_query() -> None:
    with pytest.raises(UrlValidationError):
        _ = parse_root("https://simplefin.invalid/simplefin?x=1")


def test_parse_url_brackets_ipv6_host() -> None:
    url = parse_url("https://[2001:db8::1]:8443/simplefin")
    assert url.origin_and_path == "https://[2001:db8::1]:8443/simplefin"
    assert url.host == "2001:db8::1"


def test_parse_url_drops_the_default_port_for_the_scheme() -> None:
    assert parse_url("https://simplefin.invalid:443/x").origin_and_path == (
        "https://simplefin.invalid/x"
    )
    assert parse_url("http://127.0.0.1:80/x").origin_and_path == "http://127.0.0.1/x"


def test_parse_url_keeps_a_non_default_port() -> None:
    assert parse_url("https://simplefin.invalid:8443/x").origin_and_path == (
        "https://simplefin.invalid:8443/x"
    )


def test_parse_url_preserves_punycode_without_decoding() -> None:
    assert parse_url("https://xn--sslfin-r3ad.invalid/x").host == "xn--sslfin-r3ad.invalid"


def test_parse_url_punycodes_a_unicode_host() -> None:
    url = parse_url(f"https://{HOMOGRAPH_HOST}/simplefin")

    assert url.host == HOMOGRAPH_HOST.encode("idna").decode("ascii")
    assert url.origin_and_path == f"https://{url.host}/simplefin"


def test_parse_url_rejects_malformed_port() -> None:
    """A port that is not a number leaves no origin to compare, so there is nothing to match."""
    with pytest.raises(UrlValidationError, match="not a valid URL"):
        _ = parse_url("https://simplefin.invalid:notaport/x")


MALFORMED_HOST_URLS = [
    # An unbalanced bracket: neither an IPv6 literal nor a name.
    "https://simplefin.invalid]/simplefin/claim/tok",
    "https://[simplefin.invalid/simplefin/claim/tok",
    "https://[not-an-address]/simplefin/claim/tok",
]


@pytest.mark.parametrize("raw", MALFORMED_HOST_URLS)
def test_parse_url_rejects_a_malformed_bracketed_host(raw: str) -> None:
    """Rejected through this module's own error, which is what every caller handles."""
    with pytest.raises(UrlValidationError):
        _ = parse_url(raw)


PORT_RANGE_CASES: list[tuple[str, bool]] = [
    ("https://simplefin.invalid:1/x", True),
    ("https://simplefin.invalid:65535/x", True),
    # Nothing listens on port 0 -- it means "pick one for me" when binding --
    # but urlsplit's range is 0-65535 and this takes its word for it.
    # Behaviour, not a requirement.
    ("https://simplefin.invalid:0/x", True),
    ("https://simplefin.invalid:65536/x", False),
    ("https://simplefin.invalid:-1/x", False),
    ("https://simplefin.invalid:99999999999/x", False),
]


@pytest.mark.parametrize(("raw", "accepted"), PORT_RANGE_CASES)
def test_parse_url_requires_a_port_in_range(raw: str, *, accepted: bool) -> None:
    if accepted:
        _ = parse_url(raw)
    else:
        with pytest.raises(UrlValidationError, match="not a valid URL"):
            _ = parse_url(raw)


def test_a_password_ending_the_authority_early_is_not_detected() -> None:
    """Behaviour, not a requirement: a deliberate gap, pinned so it reads as known.

    An unencoded "/" in a password ends the authority before the "@", so the
    parse puts the credentials in the host, the port and the path instead --
    where stripping the userinfo cannot reach them, and a message about the URL
    can name part of one. Closing it costs more than the case is worth, and
    such a URL is malformed anyway: a password has to percent-encode "/", or no
    client would send it as a credential.
    """
    url = parse_url("https://user:8443/xyz@simplefin.invalid/simplefin")

    assert url.host == "user"
    assert url.port == 8443  # noqa: PLR2004 -- the password prefix, read as a port
    assert url.has_creds is False


def test_parse_url_rejects_missing_host() -> None:
    with pytest.raises(UrlValidationError, match="no host"):
        _ = parse_url("file:///etc/passwd")


def test_invalid_idna_host_error_does_not_render_the_raw_unicode() -> None:
    """A host too malformed to have a punycode form still may not be drawn on the terminal.

    Every other rejection names the host in punycode. This one cannot -- an
    over-long label has no punycode form -- so the message has to carry the
    input escaped instead of rendered.
    """
    over_long = "\u0440" * 70  # Cyrillic er, drawn like p
    with pytest.raises(UrlValidationError) as exc_info:
        _ = parse_url(f"https://{over_long}.invalid/simplefin")
    message = str(exc_info.value)
    assert over_long not in message
    assert message.isascii()
    assert message.isprintable()


CONTROL_CHARACTER_URLS = [
    "https://simplefin.invalid/simplefin/\x1b[2J\x1b[H-owned",  # ANSI escape
    "https://simplefin.invalid/simplefin/tok\x00nul",
    "https://simplefin.invalid/simplefin/tok\x7fdel",
    # CRLF splicing, which would otherwise be a request-smuggling primitive.
    "https://simplefin.invalid/simplefin/tok\r\nHost: evil.example",
]


@pytest.mark.parametrize("raw", CONTROL_CHARACTER_URLS)
def test_parse_url_rejects_control_characters(raw: str) -> None:
    with pytest.raises(UrlValidationError, match="not a valid URL"):
        _ = parse_url(raw)


SECRET = "s3cret-provider-password"  # noqa: S105

# A password sitting where a password belongs, spelled in ways that make the
# parse fail or make it disagree about where the authority ends. However the
# URL is rejected, the message must not quote the password. The limit is
# `test_a_password_ending_the_authority_early_is_not_detected`: a password that
# lands in the host or the port is no longer distinguishable from one, and can
# be named.
REJECTED_URLS_CARRYING_A_SECRET = [
    f"https://user:{SECRET}/x@simplefin.invalid/simplefin",
    f"https://user:{SECRET}?x@simplefin.invalid/simplefin",
    f"https://user:{SECRET}#x@simplefin.invalid/simplefin",
    f"https://user:pa[{SECRET}]word@simplefin.invalid/simplefin",
    f"https://user:{SECRET}@simplefin.invalid:notaport/simplefin",
    f"https://user:{SECRET}@simplefin.invalid/simplefin/\x1b[2Jowned",
    f"https://user:{SECRET}@{'\u0440' * 70}.invalid/simplefin",
    f"user:{SECRET}@simplefin.invalid/simplefin",
    f"https://user:1024?{SECRET}@simplefin.invalid/simplefin",
    f"https://user:{SECRET}@simplefin.invalid:99999/simplefin",
    f"https://user:{SECRET}@simplefin.invalid/simplefin/../evil",
]


@pytest.mark.parametrize("raw", REJECTED_URLS_CARRYING_A_SECRET)
def test_rejected_url_message_does_not_quote_the_password(raw: str) -> None:
    with pytest.raises(UrlValidationError) as exc_info:
        _ = parse_url(raw)

    message = str(exc_info.value)
    assert SECRET not in message
    assert message.isascii()
    assert message.isprintable()


# Characters a URL may not carry literally, which are percent-encoded rather
# than rejected. The requirement is only that the result is safe to print --
# see test_accepted_urls_render_as_printable_ascii; the exact encoding is
# httpx2's, so these expectations are behaviour, not a requirement.
PERCENT_ENCODED_URLS = [
    ("https://simplefin.invalid/simplefin/tok with space", "/simplefin/tok%20with%20space"),
    ("https://simplefin.invalid/simplefin/café", "/simplefin/caf%C3%A9"),
    # A zero-width space: invisible in a terminal if it were passed through.
    ("https://simplefin.invalid/simplefin/tok\u200b", "/simplefin/tok%E2%80%8B"),
]


@pytest.mark.parametrize(("raw", "expected_path"), PERCENT_ENCODED_URLS)
def test_parse_url_percent_encodes_characters_not_legal_in_a_path(
    raw: str, expected_path: str
) -> None:
    assert parse_url(raw).origin_and_path == f"https://simplefin.invalid{expected_path}"


def test_claim_url_with_escapes_on_the_right_host_is_still_rejected() -> None:
    # Passing the root check is not enough to be echoed safely.
    with pytest.raises(UrlValidationError):
        _ = validate_claim_url(
            ROOT, "https://simplefin.invalid/simplefin/\x1b[2Jowned", provider=PROVIDER
        )


# Listed outright rather than filtered out of the matching tables above.
# Filtering by calling parse_url would make the test below vacuous: it could
# only ever be handed URLs parse_url accepts, so a regression would silently
# shrink the corpus instead of failing.
ACCEPTED_URLS = [
    "https://simplefin.invalid",
    # parse_url does not restrict the scheme; a non-http one simply fails to
    # match any root. Behaviour, not a requirement.
    "ftp://127.0.0.1/simplefin/claim/tok",
    "https://simplefin.invalid/simplefin/claim/tok",
    "https://SIMPLEFIN.INVALID/simplefin/claim/tok",
    "https://simplefin.invalid./simplefin/claim/tok",
    "https://simplefin.invalid:443/simplefin/claim/tok",
    "https://simplefin.invalid:8443/simplefin/claim/tok",
    "https://simplefin.invalid//simplefin/claim/tok",
    "https://user:hunter2@simplefin.invalid/simplefin",
    "http://127.0.0.1/simplefin/claim/tok",
    "http://localhost/simplefin/claim/tok",
    "https://[2001:db8::1]:8443/simplefin",
    f"https://{HOMOGRAPH_HOST}/simplefin",
    "https://xn--sslfin-r3ad.invalid/x",
    "https://simplefin.invalid/tok%zz",  # a malformed escape, passed through as written
    *DOTTED_BUT_LEGITIMATE_URLS,
    *(raw for raw, _ in PERCENT_ENCODED_URLS),
]


@pytest.mark.parametrize("raw", ACCEPTED_URLS)
def test_accepted_urls_render_as_printable_ascii(raw: str) -> None:
    """Whatever is accepted must be safe to print in an error message.

    Both strings are shown to the user, so however exotic the input, neither
    may come back carrying an ANSI escape, an invisible character, or a
    Unicode host that draws like another one.
    """
    url = parse_url(raw)

    assert url.origin_and_path.isascii()
    assert url.origin_and_path.isprintable()
    assert url.origin.isascii()
    assert url.origin.isprintable()
