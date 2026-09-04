"""Tests for url_validation.py's provider-root matching.

`.invalid` (RFC 2606) is reserved and guaranteed never to resolve, so it is
safe as a stand-in provider host with no risk of a real lookup.
"""

from __future__ import annotations

import pytest

from simplefin_aggregator.url_validation import (
    UrlValidationError,
    parse_root,
    parse_url,
    validate_access_url,
    validate_claim_url,
)


ROOT = parse_root("https://simplefin.invalid/simplefin")
LOOPBACK_ROOT = parse_root("http://127.0.0.1/simplefin")

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
    # Substitutes a Cyrillic 'er' (U+0440) for the Latin 'p' in "simplefin".
    homograph = "simplefin.invalid".replace("p", "р")  # noqa: RUF001
    with pytest.raises(UrlValidationError, match="non-ASCII"):
        _ = validate_claim_url(ROOT, f"https://{homograph}/simplefin/claim/tok", provider=PROVIDER)


def test_validate_claim_url_error_never_renders_a_unicode_host() -> None:
    homograph = "simplefin.invalid".replace("p", "р")  # noqa: RUF001
    with pytest.raises(UrlValidationError) as exc_info:
        _ = validate_claim_url(ROOT, f"https://{homograph}/simplefin/claim/tok", provider=PROVIDER)
    assert homograph not in str(exc_info.value)


def test_validate_claim_url_rejects_userinfo() -> None:
    with pytest.raises(UrlValidationError, match="must not contain credentials"):
        _ = validate_claim_url(
            ROOT, "https://user:pass@simplefin.invalid/simplefin/claim/tok", provider=PROVIDER
        )


def test_validate_claim_url_message_names_url_provider_and_expected_root() -> None:
    with pytest.raises(UrlValidationError) as exc_info:
        _ = validate_claim_url(ROOT, "https://evil.example/simplefin/claim/tok", provider=PROVIDER)
    message = str(exc_info.value)
    assert "https://evil.example/simplefin/claim/tok" in message
    assert PROVIDER in message
    assert ROOT.origin_and_path in message


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
    homograph = "simplefin.invalid".replace("p", "р")  # noqa: RUF001
    with pytest.raises(UrlValidationError) as exc_info:
        _ = parse_url(f"https://{homograph}/simplefin")
    message = str(exc_info.value)
    assert homograph not in message
    assert homograph.encode("idna").decode("ascii") in message


def test_non_ascii_host_error_falls_back_when_punycode_encoding_fails() -> None:
    # The IDNA codec rejects an over-long label, so the message cannot name it.
    over_long = "р" * 70  # noqa: RUF001
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
