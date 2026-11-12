import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from app.services.rate_limit import (
    RateLimitExceeded,
    RollingRateLimiter,
    embedding_limiter,
    generation_limiter,
)


class RollingRateLimiterTests(unittest.TestCase):
    def test_configured_free_tier_limits_are_shared(self):
        self.assertEqual(generation_limiter.requests_per_minute, 15)
        self.assertEqual(generation_limiter.requests_per_day, 500)
        self.assertEqual(embedding_limiter.requests_per_minute, 100)
        self.assertEqual(embedding_limiter.requests_per_day, 1000)
        self.assertEqual(embedding_limiter.tokens_per_minute, 30_000)

    def test_concurrent_requests_cannot_exceed_rolling_limit(self):
        limiter = RollingRateLimiter(
            requests_per_minute=3,
            requests_per_day=100,
            max_wait_seconds=0,
            max_queued_requests=20,
        )
        barrier = threading.Barrier(12)

        def acquire():
            barrier.wait()
            try:
                limiter.acquire()
                return True
            except RateLimitExceeded:
                return False

        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(lambda _: acquire(), range(12)))

        self.assertEqual(sum(results), 3)
        self.assertEqual(len(limiter._events), 3)

    def test_requests_queue_until_their_slot_is_available(self):
        limiter = RollingRateLimiter(
            requests_per_minute=1,
            requests_per_day=10,
            request_window_seconds=0.1,
            max_wait_seconds=0.5,
            max_queued_requests=2,
        )
        limiter.acquire()
        started = time.monotonic()

        limiter.acquire()

        self.assertGreaterEqual(time.monotonic() - started, 0.08)
        self.assertEqual(len(limiter._events), 2)

    def test_requests_are_rejected_after_maximum_queue_wait(self):
        limiter = RollingRateLimiter(
            requests_per_minute=1,
            requests_per_day=10,
            max_wait_seconds=0.01,
            max_queued_requests=2,
        )
        limiter.acquire()

        with self.assertRaises(RateLimitExceeded) as error:
            limiter.acquire()

        self.assertGreaterEqual(error.exception.retry_after, 1)
        self.assertEqual(len(limiter._events), 1)

    def test_token_capacity_uses_measured_token_count(self):
        limiter = RollingRateLimiter(
            requests_per_minute=100,
            requests_per_day=100,
            tokens_per_minute=10,
            max_wait_seconds=0,
            max_queued_requests=2,
        )
        limiter.acquire(tokens=8)

        with self.assertRaises(RateLimitExceeded) as error:
            limiter.acquire(tokens=3)

        self.assertEqual(error.exception.retry_after, 60)
        self.assertEqual(sum(event.tokens for event in limiter._events), 8)

    def test_daily_request_limit_is_enforced(self):
        limiter = RollingRateLimiter(
            requests_per_minute=10,
            requests_per_day=1,
            day_window_seconds=3600,
            max_wait_seconds=0,
        )
        limiter.acquire()

        with self.assertRaises(RateLimitExceeded) as error:
            limiter.acquire()

        self.assertGreaterEqual(error.exception.retry_after, 3600)


if __name__ == "__main__":
    unittest.main()
