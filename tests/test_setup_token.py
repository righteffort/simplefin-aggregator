import base64

from simplefin_aggregator.setup_token import build_setup_token


CLAIM_SECRET = "my-secret-token"  # noqa: S105


def _claim_url(setup_token: str) -> str:
    return base64.b64decode(setup_token, validate=True).decode("ascii")


def test_the_token_carries_the_claim_url_this_aggregator_answers_on() -> None:
    setup_token = build_setup_token("http://127.0.0.1:8080", CLAIM_SECRET)

    assert _claim_url(setup_token) == f"http://127.0.0.1:8080/simplefin/claim/{CLAIM_SECRET}"


def test_a_base_url_ending_in_a_slash_names_the_same_claim_url() -> None:
    setup_token = build_setup_token("http://127.0.0.1:8080/", CLAIM_SECRET)

    assert _claim_url(setup_token) == f"http://127.0.0.1:8080/simplefin/claim/{CLAIM_SECRET}"


def test_the_claim_url_keeps_the_base_url_scheme_and_host() -> None:
    setup_token = build_setup_token("https://aggregator.example.com", CLAIM_SECRET)

    assert (
        _claim_url(setup_token) == f"https://aggregator.example.com/simplefin/claim/{CLAIM_SECRET}"
    )
