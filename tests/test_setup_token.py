import base64

from simplefin_aggregator.setup_token import build_setup_token

from .support import make_config


def test_build_setup_token_encodes_the_claim_url() -> None:
    config = make_config(base_url="http://127.0.0.1:8080", claim_token="my-secret-token")

    setup_token = build_setup_token(config)

    decoded = base64.b64decode(setup_token, validate=True).decode("ascii")
    assert decoded == "http://127.0.0.1:8080/simplefin/claim/my-secret-token"


def test_build_setup_token_strips_trailing_slash_from_base_url() -> None:
    config = make_config(base_url="http://127.0.0.1:8080/", claim_token="my-secret-token")

    setup_token = build_setup_token(config)

    decoded = base64.b64decode(setup_token, validate=True).decode("ascii")
    assert decoded == "http://127.0.0.1:8080/simplefin/claim/my-secret-token"


def test_build_setup_token_uses_base_url_scheme_and_host() -> None:
    config = make_config(base_url="https://aggregator.example.com", claim_token="tok")

    setup_token = build_setup_token(config)

    decoded = base64.b64decode(setup_token, validate=True).decode("ascii")
    assert decoded == "https://aggregator.example.com/simplefin/claim/tok"
