"""Tests for url_validation.py's provider-root matching.

`.invalid` (RFC 2606) is reserved and guaranteed never to resolve, so it is
safe as a stand-in provider host with no risk of a real lookup.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx2
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
SETUP_TOKEN = "s3cret-setup-token"

PROVIDER = "test-provider"


CLAIM_URL_CASES: list[tuple[str, bool]] = [
    ("https://simplefin.invalid/simplefin/claim/tok", True),
    # urlsplit lowercases the scheme and host before we see them, so a
    # case-folded URL matches. DNS and TLS hostname matching are both
    # case-insensitive, so this is the same host by every mechanism that binds
    # identity -- untidy input we tolerate, not a different origin we accept.
    ("https://SIMPLEFIN.INVALID/simplefin/claim/tok", True),
    # A trailing dot resolves identically but is a different string, and we do
    # not repair provider-supplied input.
    ("https://simplefin.invalid./simplefin/claim/tok", False),
    ("https://simplefin.invalid@evil.example/simplefin/claim/tok", False),
    ("https://simplefin.invalid.evil.example/simplefin/claim/tok", False),
    ("https://evil.example/simplefin.invalid/claim/tok", False),
    ("https://simplefin.invalid/simplefin-evil/claim/tok", False),
    ("https://simplefin.invalid:8443/simplefin/claim/tok", False),
    ("https://simplefin.invalid:443/simplefin/claim/tok", False),
    ("https://simplefin.invalid:notaport/simplefin/claim/tok", False),
    ("http://simplefin.invalid/simplefin/claim/tok", False),
    ("ftp://127.0.0.1/simplefin/claim/tok", False),
    ("file:///etc/passwd", False),
    ("https://simplefin.invalid/simplefin/claim/tok?x=1", False),
    ("https://simplefin.invalid/simplefin/claim/tok#frag", False),
    # Bare delimiters: SplitResult.query/.fragment are both "" here, so a
    # truthiness test on them would let these through.
    ("https://simplefin.invalid/simplefin/claim/tok?", False),
    ("https://simplefin.invalid/simplefin/claim/tok#", False),
    ("https://simplefin.invalid//simplefin/claim/tok", False),
]


@pytest.mark.parametrize(("raw", "accepted"), CLAIM_URL_CASES)
def test_validate_claim_url_table(raw: str, *, accepted: bool) -> None:
    if accepted:
        _ = validate_claim_url(ROOT, raw, provider=PROVIDER)
    else:
        with pytest.raises(UrlValidationError):
            _ = validate_claim_url(ROOT, raw, provider=PROVIDER)


def test_validate_claim_url_rejects_unicode_homograph() -> None:
    homograph = "simplefin.invalid".replace("p", "р")  # noqa: RUF001 -- homograph for p
    with pytest.raises(UrlValidationError, match="non-ASCII"):
        _ = validate_claim_url(ROOT, f"https://{homograph}/simplefin/claim/tok", provider=PROVIDER)


def test_validate_claim_url_error_never_renders_a_unicode_host() -> None:
    homograph = "simplefin.invalid".replace("p", "р")  # noqa: RUF001 -- homograph for p
    with pytest.raises(UrlValidationError) as exc_info:
        _ = validate_claim_url(ROOT, f"https://{homograph}/simplefin/claim/tok", provider=PROVIDER)
    assert homograph not in str(exc_info.value)


def test_validate_claim_url_rejects_userinfo() -> None:
    with pytest.raises(UrlValidationError, match="must not contain credentials"):
        _ = validate_claim_url(
            ROOT, "https://user:pass@simplefin.invalid/simplefin/claim/tok", provider=PROVIDER
        )


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
    """The prefix test reads a path literally; an HTTP client resolves it.

    Without this rejection "/simplefin/../../evil" prefix-matches a
    "/simplefin/" root as a string, and httpx2 then sends the provider's
    credentials to "/evil".
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
    assert url.password == "pass"


def test_validate_access_url_requires_credentials() -> None:
    with pytest.raises(UrlValidationError, match="must contain credentials"):
        _ = validate_access_url(ROOT, "https://simplefin.invalid/simplefin", provider=PROVIDER)


def test_validate_access_url_rejects_different_host() -> None:
    with pytest.raises(UrlValidationError):
        _ = validate_access_url(ROOT, "https://user:pass@evil.example/simplefin", provider=PROVIDER)


def test_validate_access_url_rejects_fragment() -> None:
    with pytest.raises(UrlValidationError):
        _ = validate_access_url(
            ROOT, "https://user:pass@simplefin.invalid/simplefin#frag", provider=PROVIDER
        )


def test_validate_access_url_rejects_trailing_question_mark() -> None:
    # Must never reach the cache: a stored URL ending in "?" would swallow a
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


def test_parse_url_does_not_normalize_default_port() -> None:
    assert parse_url("https://simplefin.invalid:443/x").origin_and_path == (
        "https://simplefin.invalid:443/x"
    )
    assert parse_url("https://simplefin.invalid/x").origin_and_path == "https://simplefin.invalid/x"


def test_parse_url_preserves_punycode_without_decoding() -> None:
    assert parse_url("https://xn--sslfin-r3ad.invalid/x").host == "xn--sslfin-r3ad.invalid"


def test_parse_url_rejects_malformed_port() -> None:
    with pytest.raises(UrlValidationError, match="malformed port"):
        _ = parse_url("https://simplefin.invalid:notaport/x")


def test_parse_url_rejects_missing_host() -> None:
    with pytest.raises(UrlValidationError, match="no host"):
        _ = parse_url("file:///etc/passwd")


def test_non_ascii_host_error_names_the_host_in_punycode() -> None:
    homograph = "simplefin.invalid".replace("p", "р")  # noqa: RUF001 -- homograph for p
    with pytest.raises(UrlValidationError) as exc_info:
        _ = parse_url(f"https://{homograph}/simplefin")
    message = str(exc_info.value)
    assert homograph not in message
    assert homograph.encode("idna").decode("ascii") in message


def test_non_ascii_host_error_falls_back_when_punycode_encoding_fails() -> None:
    # The IDNA codec rejects an over-long label, so the message cannot name it.
    over_long = "р" * 70  # noqa: RUF001 -- homograph for p
    with pytest.raises(UrlValidationError, match="non-ASCII"):
        _ = parse_url(f"https://{over_long}.invalid/simplefin")


CONTROL_CHARACTER_URLS = [
    "https://simplefin.invalid/simplefin/\x1b[2J\x1b[H-owned",  # ANSI escape
    "https://simplefin.invalid/simplefin/tok\x00nul",
    "https://simplefin.invalid/simplefin/tok\x7fdel",
    "https://simplefin.invalid/simplefin/tok with space",
    "https://simplefin.invalid/simplefin/café",  # non-ASCII outside the host
]


@pytest.mark.parametrize("raw", CONTROL_CHARACTER_URLS)
def test_parse_url_rejects_characters_outside_printable_ascii(raw: str) -> None:
    with pytest.raises(UrlValidationError, match="printable ASCII"):
        _ = parse_url(raw)


def test_control_character_error_message_is_safe_to_print() -> None:
    with pytest.raises(UrlValidationError) as exc_info:
        _ = parse_url("https://simplefin.invalid/simplefin/\x1b[2Jowned")
    # The escape must be rendered as text, never emitted as a live escape.
    assert "\x1b" not in str(exc_info.value)
    assert "\\x1b" in str(exc_info.value)


def test_claim_url_with_escapes_on_the_right_host_is_still_rejected() -> None:
    # Passing the allowlist check is not enough to be echoed safely.
    with pytest.raises(UrlValidationError):
        _ = validate_claim_url(
            ROOT, "https://simplefin.invalid/simplefin/\x1b[2Jowned", provider=PROVIDER
        )


def _parses(raw: str) -> bool:
    try:
        _ = parse_url(raw)
    except UrlValidationError:
        return False
    return True


# The tables above mix URLs parse_url accepts with ones it rejects (query
# strings, fragments, no host), so filter rather than assume.
ACCEPTED_URLS = [
    raw
    for raw in (
        *(raw for raw, _ in CLAIM_URL_CASES),
        *(raw for raw, _ in LOOPBACK_CASES),
        *DOTTED_BUT_LEGITIMATE_URLS,
        # Filtered out today. If the dot-segment rejection is ever removed
        # these become "accepted" and the invariant below fails, which is the
        # point: it catches a regression of exactly that bug.
        *DOT_SEGMENT_URLS,
        "https://simplefin.invalid",
        "https://simplefin.invalid:443/x",
        "https://[2001:db8::1]:8443/simplefin",
    )
    if _parses(raw)
]


def test_the_accepted_url_corpus_is_not_empty() -> None:
    """Guard against the filter above quietly emptying the invariant test."""
    assert len(ACCEPTED_URLS) > 10  # noqa: PLR2004


@pytest.mark.parametrize("raw", ACCEPTED_URLS)
def test_accepted_urls_are_fetched_from_the_path_they_matched_on(raw: str) -> None:
    """The invariant the dot-segment rejection exists to protect.

    Matching compares `origin_and_path` as a string, while httpx2 resolves the
    path when it builds a request. Where those two disagree, a URL can match
    one root and be fetched from somewhere else -- which is what
    "/simplefin/../../evil" did. Asserting it here means a future httpx2 that
    rewrites some other form cannot reopen the hole silently.
    """
    matched = parse_url(raw).origin_and_path

    assert httpx2.URL(matched).path == (urlsplit(matched).path or "/")


@pytest.mark.parametrize("raw", DOT_SEGMENT_URLS)
def test_rejected_dot_segment_urls_would_have_been_fetched_elsewhere(raw: str) -> None:
    """The rejection is load-bearing: httpx2 really would request a different path."""
    assert httpx2.URL(raw).path != urlsplit(raw).path
