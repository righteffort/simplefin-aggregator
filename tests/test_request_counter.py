from simplefin_aggregator.request_counter import RequestCounter


def test_request_counter_increments_per_provider() -> None:
    counter = RequestCounter()

    assert counter.record("bank-a") == 1
    assert counter.record("bank-a") == 2
    assert counter.record("bank-b") == 1
    assert counter.record("bank-a") == 3
