from simplefin_aggregator.id_rewriting import rewrite_ids, unrewrite_ids


def test_unrewrite_ids_is_currently_identity() -> None:
    assert unrewrite_ids(["acc-1", "acc-2"]) == ["acc-1", "acc-2"]


def test_rewrite_ids_is_currently_identity() -> None:
    assert rewrite_ids(b'{"accounts": []}') == b'{"accounts": []}'
