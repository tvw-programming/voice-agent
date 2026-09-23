from voice_agent.breaker import CircuitBreaker


def test_opens_and_half_opens():
    now = [0.0]
    b = CircuitBreaker(3, 60, clock=lambda: now[0])
    for _ in range(3):
        b.failure("x")
    assert b.is_open("x")
    now[0] = 61
    assert not b.is_open("x")          # half-open trial allowed
    b.failure("x")
    assert b.is_open("x")              # one failure re-opens
    now[0] = 200
    assert not b.is_open("x")
    b.success("x")
    b.failure("x")
    assert not b.is_open("x")
