# SPDX-License-Identifier: GPL-3.0-only

"""Tests for url_validation.py's provider-origin matching.

`.invalid` (RFC 2606) is reserved and guaranteed never to resolve, so it is
safe as a stand-in provider host with no risk of a real lookup.

Cases marked "behavior, not a requirement" pin what the module does today in
a situation where doing something else would be at least as correct -- an input it
declines to normalize, or a gap it deliberately leaves open. A future
implementation may change them; they are here so that such a change shows up
in a diff rather than passing unnoticed.
"""

from __future__ import annotations

import pytest

from sf_agg import cli
from sf_agg.url_validation import (
    UrlValidationError,
    is_loopback_host,
    parse_origin,
    parse_url,
    validate_access_url,
    validate_claim_url,
)


ORIGIN = parse_origin("https://simplefin.invalid")
LOOPBACK_ORIGIN = parse_origin("http://127.0.0.1")
# A second provider origin, for the case where the pasted token is genuine but
# the user picked the wrong entry from the claim menu.
OTHER_ORIGIN = parse_origin("https://other-provider.invalid")

# Stands in for the one-time setup token a real claim URL carries in its path.
SETUP_TOKEN = "s3cret-setup-token"  # noqa: S105

PROVIDER = "test-provider"


CLAIM_URL_CASES: list[tuple[str, bool]] = [
    ("https://simplefin.invalid/simplefin/claim/tok", True),
    # Scheme and host are case-insensitive to DNS and to TLS alike, so a URL
    # differing from the origin only in their case names the same origin.
    ("https://SIMPLEFIN.INVALID/simplefin/claim/tok", True),
    # Any path on the origin: what the provider serves there is its own.
    ("https://simplefin.invalid/SIMPLEFIN/claim/tok", True),
    ("https://simplefin.invalid/elsewhere/claim/tok", True),
    # A trailing dot names the same host to DNS, but is left as a distinct
    # string. Behavior, not a requirement: normalizing it would be as correct.
    ("https://simplefin.invalid./simplefin/claim/tok", False),
    ("https://simplefin.invalid@evil.example/simplefin/claim/tok", False),
    ("https://simplefin.invalid.evil.example/simplefin/claim/tok", False),
    ("https://evil.example/simplefin.invalid/claim/tok", False),
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
]


@pytest.mark.parametrize(("raw", "accepted"), CLAIM_URL_CASES)
def test_validate_claim_url_table(raw: str, *, accepted: bool) -> None:
    if accepted:
        _ = validate_claim_url(ORIGIN, raw, provider=PROVIDER)
    else:
        with pytest.raises(UrlValidationError):
            _ = validate_claim_url(ORIGIN, raw, provider=PROVIDER)


HOMOGRAPH_HOST = "simplefin.invalid".replace("p", "\u0440")  # Cyrillic er, drawn like p


def test_validate_claim_url_rejects_unicode_homograph() -> None:
    """A host that merely draws like the provider's host is a different host, and must not match."""
    with pytest.raises(UrlValidationError) as exc_info:
        _ = validate_claim_url(
            ORIGIN, f"https://{HOMOGRAPH_HOST}/simplefin/claim/tok", provider=PROVIDER
        )
    assert "is not valid for provider" in str(exc_info.value)


def test_validate_claim_url_error_never_renders_a_unicode_host() -> None:
    """The message names the punycode form: legible, and not mistakable for the real host."""
    with pytest.raises(UrlValidationError) as exc_info:
        _ = validate_claim_url(
            ORIGIN, f"https://{HOMOGRAPH_HOST}/simplefin/claim/tok", provider=PROVIDER
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
        _ = validate_claim_url(ORIGIN, raw, provider=PROVIDER)


EMPTY_USERINFO_URLS = [
    "https://@simplefin.invalid/simplefin/claim/tok",
    "https://:@simplefin.invalid/simplefin/claim/tok",
]


@pytest.mark.parametrize("raw", EMPTY_USERINFO_URLS)
def test_validate_claim_url_accepts_an_empty_userinfo_section(raw: str) -> None:
    """An empty section carries no credential, and names the same URL as one without it.

    Behavior, not a requirement: rejecting the section outright would be
    equally correct, and no provider emits one.
    """
    url = validate_claim_url(ORIGIN, raw, provider=PROVIDER)

    assert url.has_creds is False
    assert url.origin_and_path == "https://simplefin.invalid/simplefin/claim/tok"


def test_validate_claim_url_message_names_origin_provider_and_expected_origin() -> None:
    with pytest.raises(UrlValidationError) as exc_info:
        _ = validate_claim_url(
            ORIGIN, "https://evil.example/simplefin/claim/tok", provider=PROVIDER
        )
    message = str(exc_info.value)
    assert "https://evil.example" in message
    assert PROVIDER in message
    assert f"expected {ORIGIN}" in message


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


PATHS_THAT_A_SERVER_MIGHT_RESOLVE = [
    "https://simplefin.invalid/simplefin/../../evil.example/claim/tok",
    "https://simplefin.invalid/simplefin/%2e%2e/%2e%2e/evil.example/claim/tok",
    "https://simplefin.invalid//evil.example/claim/tok",
    "https://simplefin.invalid/%2f%2fevil.example/claim/tok",
    "https://simplefin.invalid/simplefin/%2f../evil.example/claim/tok",
]


@pytest.mark.parametrize("raw", PATHS_THAT_A_SERVER_MIGHT_RESOLVE)
def test_a_path_cannot_move_the_fetched_url_off_the_matched_origin(raw: str) -> None:
    url = validate_claim_url(ORIGIN, raw, provider=PROVIDER)
    with cli._build_claim_client() as claim_client:  # pyright: ignore[reportPrivateUsage]
        request = claim_client.build_request("POST", url.origin_and_path)

    assert f"{request.url.scheme}://{request.url.netloc.decode('ascii')}" == ORIGIN


def test_origin_excludes_the_path() -> None:
    url = parse_url(f"https://simplefin.invalid:8443/simplefin/claim/{SETUP_TOKEN}")

    assert url.origin == "https://simplefin.invalid:8443"
    assert SETUP_TOKEN in url.origin_and_path


def test_mismatch_message_withholds_the_setup_token() -> None:
    """The paste-error case: a genuine token, but the wrong menu entry selected.

    That token is still live and unexchanged, so printing it into terminal
    scrollback would hand a bearer credential to anyone who reads it.
    """
    with pytest.raises(UrlValidationError) as exc_info:
        _ = validate_claim_url(
            OTHER_ORIGIN,
            f"https://simplefin.invalid/simplefin/claim/{SETUP_TOKEN}",
            provider=PROVIDER,
        )

    assert SETUP_TOKEN not in str(exc_info.value)


def test_claim_url_credentials_message_withholds_the_setup_token() -> None:
    with pytest.raises(UrlValidationError) as exc_info:
        _ = validate_claim_url(
            ORIGIN,
            f"https://u:p@simplefin.invalid/simplefin/claim/{SETUP_TOKEN}",
            provider=PROVIDER,
        )

    assert SETUP_TOKEN not in str(exc_info.value)


def test_query_string_message_withholds_the_setup_token() -> None:
    with pytest.raises(UrlValidationError) as exc_info:
        _ = parse_url(f"https://simplefin.invalid/simplefin/claim/{SETUP_TOKEN}?redirect=1")

    assert SETUP_TOKEN not in str(exc_info.value)


LOOPBACK_CASES: list[tuple[str, bool]] = [
    ("http://127.0.0.1/simplefin/claim/tok", True),
    # The entry's origin is http, so https is a different origin and does not
    # match it.
    ("https://127.0.0.1/simplefin/claim/tok", False),
    ("http://localhost/simplefin/claim/tok", False),
    ("http://127.0.0.2/simplefin/claim/tok", False),
]


@pytest.mark.parametrize(("raw", "accepted"), LOOPBACK_CASES)
def test_validate_claim_url_loopback_table(raw: str, *, accepted: bool) -> None:
    if accepted:
        _ = validate_claim_url(LOOPBACK_ORIGIN, raw, provider=PROVIDER)
    else:
        with pytest.raises(UrlValidationError):
            _ = validate_claim_url(LOOPBACK_ORIGIN, raw, provider=PROVIDER)


def test_validate_access_url_accepts_credentials_on_the_provider_origin() -> None:
    url = validate_access_url(
        ORIGIN, "https://user:pass@simplefin.invalid/simplefin", provider=PROVIDER
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
        _ = validate_access_url(ORIGIN, raw, provider=PROVIDER)


def test_validate_access_url_rejects_different_host() -> None:
    with pytest.raises(UrlValidationError):
        _ = validate_access_url(
            ORIGIN, "https://user:pass@evil.example/simplefin", provider=PROVIDER
        )


def test_validate_access_url_rejects_fragment() -> None:
    with pytest.raises(UrlValidationError):
        _ = validate_access_url(
            ORIGIN, "https://user:pass@simplefin.invalid/simplefin#frag", provider=PROVIDER
        )


def test_validate_access_url_rejects_trailing_question_mark() -> None:
    # Must never reach the store: a stored URL ending in "?" would swallow a
    # request path appended to it.
    with pytest.raises(UrlValidationError):
        _ = validate_access_url(
            ORIGIN, "https://user:pass@simplefin.invalid/simplefin?", provider=PROVIDER
        )


def test_access_url_origin_and_path_excludes_credentials() -> None:
    url = validate_access_url(
        ORIGIN, "https://user:hunter2@simplefin.invalid/simplefin", provider=PROVIDER
    )
    assert url.origin_and_path == "https://simplefin.invalid/simplefin"
    assert "hunter2" not in url.origin_and_path


def test_access_url_error_message_excludes_credentials() -> None:
    with pytest.raises(UrlValidationError) as exc_info:
        _ = validate_access_url(
            ORIGIN, "https://user:hunter2@evil.example/simplefin", provider=PROVIDER
        )
    assert "hunter2" not in str(exc_info.value)


PARSE_ORIGIN_CASES = [
    ("https://simplefin.invalid", "https://simplefin.invalid"),
    ("https://simplefin.invalid/", "https://simplefin.invalid"),
    ("https://SIMPLEFIN.INVALID:443", "https://simplefin.invalid"),
    ("https://simplefin.invalid:8443", "https://simplefin.invalid:8443"),
    ("http://127.0.0.1:8888", "http://127.0.0.1:8888"),
    ("http://[::1]", "http://[::1]"),
]


@pytest.mark.parametrize(("raw", "expected"), PARSE_ORIGIN_CASES)
def test_parse_origin_returns_the_normalized_origin(raw: str, expected: str) -> None:
    assert parse_origin(raw) == expected


def test_parse_origin_rejects_a_path_without_naming_it() -> None:
    with pytest.raises(UrlValidationError, match="must not have a path") as exc_info:
        _ = parse_origin("https://simplefin.invalid/capability-token")

    assert "capability-token" not in str(exc_info.value)


def test_parse_origin_rejects_http_for_localhost_by_name() -> None:
    with pytest.raises(UrlValidationError, match="loopback"):
        _ = parse_origin("http://localhost")


def test_parse_origin_rejects_http_for_non_loopback_host() -> None:
    with pytest.raises(UrlValidationError, match="loopback"):
        _ = parse_origin("http://simplefin.invalid")


def test_parse_origin_rejects_non_http_scheme() -> None:
    with pytest.raises(UrlValidationError, match="must use https"):
        _ = parse_origin("ftp://simplefin.invalid")


def test_parse_origin_rejects_credentials() -> None:
    with pytest.raises(UrlValidationError, match="must not contain credentials"):
        _ = parse_origin("https://user:pass@simplefin.invalid")


def test_parse_origin_rejects_query() -> None:
    with pytest.raises(UrlValidationError, match="query string"):
        _ = parse_origin("https://simplefin.invalid/?x=1")


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
    # Behavior, not a requirement.
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


UNENCODED_DELIMITER_IN_PASSWORD_URLS = [
    # Digits before the delimiter read as a port, leaving no credentials.
    "https://user:8443/xyz@simplefin.invalid/simplefin",
    "https://user:8443?xyz@simplefin.invalid/simplefin",
    "https://user:8443#xyz@simplefin.invalid/simplefin",
    # Anything else there is not a port.
    "https://user:pa/ss@simplefin.invalid/simplefin",
]


@pytest.mark.parametrize("raw", UNENCODED_DELIMITER_IN_PASSWORD_URLS)
def test_access_url_with_a_delimiter_unencoded_in_its_password_is_rejected(raw: str) -> None:
    with pytest.raises(UrlValidationError):
        _ = validate_access_url(ORIGIN, raw, provider=PROVIDER)


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
# URL is rejected, the message must not quote the password. The limit is the
# one `UrlValidationError` states: digits read as a port can be named.
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
# than rejected. The requirement is only that the result is safe to print and
# to send -- see test_accepted_urls_render_as_printable_ascii; the exact encoding is
# httpx2's, so these expectations are behavior, not a requirement.
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
    # Passing the origin check is not enough to be echoed safely.
    with pytest.raises(UrlValidationError):
        _ = validate_claim_url(
            ORIGIN, "https://simplefin.invalid/simplefin/\x1b[2Jowned", provider=PROVIDER
        )


# Listed outright rather than filtered out of the matching tables above.
# Filtering by calling parse_url would make the test below vacuous: it could
# only ever be handed URLs parse_url accepts, so a regression would silently
# shrink the corpus instead of failing.
ACCEPTED_URLS = [
    "https://simplefin.invalid",
    # parse_url does not restrict the scheme; a non-http one simply fails to
    # match any provider. Behavior, not a requirement.
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
    "https://simplefin.invalid/simplefin/a.b/claim/tok",
    *PATHS_THAT_A_SERVER_MIGHT_RESOLVE,
    *(raw for raw, _ in PERCENT_ENCODED_URLS),
]


@pytest.mark.parametrize("raw", ACCEPTED_URLS)
def test_accepted_urls_render_as_printable_ascii(raw: str) -> None:
    """Whatever is accepted is safe to print in an error message and to send.

    `origin` is shown to the user and `origin_and_path` goes on the wire, so
    however exotic the input, neither may come back carrying an ANSI escape, an
    invisible character, or a Unicode host that draws like another one.
    """
    url = parse_url(raw)

    assert url.origin_and_path.isascii()
    assert url.origin_and_path.isprintable()
    assert url.origin.isascii()
    assert url.origin.isprintable()
