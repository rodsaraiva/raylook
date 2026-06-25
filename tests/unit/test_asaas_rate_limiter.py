import threading
import time

from integrations.asaas.rate_limiter import RateLimiter


def test_serial_calls_spaced_by_min_interval():
    rl = RateLimiter(0.05)
    start = time.monotonic()
    for _ in range(4):
        rl.wait()
    # 4 chamadas => 3 intervalos entre elas
    assert time.monotonic() - start >= 3 * 0.05


def test_concurrent_calls_are_serialized():
    rl = RateLimiter(0.05)
    n = 6
    start = time.monotonic()
    threads = [threading.Thread(target=rl.wait) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert time.monotonic() - start >= (n - 1) * 0.05


def test_zero_interval_does_not_block():
    rl = RateLimiter(0)
    start = time.monotonic()
    for _ in range(100):
        rl.wait()
    assert time.monotonic() - start < 0.5
