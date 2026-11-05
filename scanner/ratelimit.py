"""A shared request budget for Civis API calls.

Civis allows 1,000 general API requests per 5 minutes per user (Civis support,
"API Basics"). The scanner, the web app, and any production jobs that run as
the Robot user share that limit, so the scanner uses only part of it: 500
requests per 5 minutes by default (docs/PLAN.md section 5).

Every Civis API call the scanner makes goes through ``BudgetedClient``, which
waits for room in the budget before each request. Paged list calls are charged
once per page. After each response, the client reads the rate-limit headers
that civis-python exposes (``calls_remaining`` and ``rate_limit``). If fewer
than 20 percent of the user's calls remain, the scanner slows to half speed
until the headroom comes back.

civis-python's own retry still applies on top of this, with the shorter policy
in ``civis_retrying`` (docs/PLAN.md 17.5).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from typing import Any

DEFAULT_REQUESTS = 500
DEFAULT_WINDOW_SECONDS = 300.0
LOW_HEADROOM_RATIO = 0.2

# Retries of one Civis API request (civis_retrying).
RETRY_MAX_TRIES = 4
RETRY_MAX_SECONDS = 120
RETRY_MAX_WAIT_SECONDS = 30


def civis_retrying() -> Any:
    """The scanner's retries policy for civis.APIClient(retries=...), a tenacity.Retrying.

    civis-python's default retries a request up to 10 tries or 600 seconds. In the
    first live test one request to a slow list took about 10 minutes that way, and
    civis 2.10's Endpoint._make_request holds one class-level lock (civis.base,
    shared by every endpoint) for the whole request, retries included, so every
    other API call in the process, including the scanner's worker threads, waited
    for it. This policy stops after RETRY_MAX_TRIES tries or once RETRY_MAX_SECONDS
    have passed (checked after each try), with random exponential waits of at most
    RETRY_MAX_WAIT_SECONDS. civis-python keeps its own rules for which status codes
    and verbs are retried (429, 502, 503, 504 on GET; 429 and 503 on POST), still
    waits at least the Retry-After header, and raises the last error when the
    retries run out.

    Not handled here: civis sets no request timeout, so a request that never gets a
    response holds the lock with no time limit. No change for that in v0.1.0-rc6.

    tenacity is a civis dependency in the scanner image (requirements-scanner.txt).
    It is imported here so the module loads without it.
    """
    import tenacity

    return tenacity.Retrying(
        wait=tenacity.wait_random_exponential(multiplier=2, max=RETRY_MAX_WAIT_SECONDS),
        stop=(tenacity.stop_after_delay(RETRY_MAX_SECONDS) | tenacity.stop_after_attempt(RETRY_MAX_TRIES)),
        retry_error_callback=lambda retry_state: retry_state.outcome.result(),
        reraise=True,
    )


class RequestBudget:
    """At most ``max_requests`` per ``window_seconds``, shared by all threads."""

    def __init__(
        self,
        max_requests: int = DEFAULT_REQUESTS,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if max_requests < 1 or window_seconds <= 0:
            raise ValueError("budget must allow at least one request per window")
        self.max_requests = max_requests
        self.window = window_seconds
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._sent: deque[float] = deque()
        self.slow = False
        self.requests = 0
        self.waited_seconds = 0.0

    @property
    def interval(self) -> float:
        """Even spacing that uses the whole budget."""
        return self.window / self.max_requests

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                while self._sent and now - self._sent[0] >= self.window:
                    self._sent.popleft()
                wait = 0.0
                if len(self._sent) >= self.max_requests:
                    wait = self._sent[0] + self.window - now
                elif self.slow and self._sent:
                    # Half speed: at least two even intervals since the last request.
                    wait = self._sent[-1] + 2 * self.interval - now
                if wait <= 0:
                    self._sent.append(now)
                    self.requests += 1
                    return
            self.waited_seconds += wait
            self._sleep(wait)

    def observe(self, remaining: Any, limit: Any) -> None:
        """Slow down when the Civis rate-limit headers show little headroom left."""
        if not _is_count(remaining) or not _is_count(limit) or limit <= 0:
            return
        self.slow = remaining < LOW_HEADROOM_RATIO * limit


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class BudgetedClient:
    """Wraps a civis APIClient so every endpoint call waits for the budget."""

    def __init__(self, client: Any, budget: RequestBudget):
        self._client = client
        self._budget = budget

    @property
    def raw(self) -> Any:
        return self._client

    def __getattr__(self, name: str) -> Any:
        return _BudgetedEndpoint(getattr(self._client, name), self._budget)


class _BudgetedEndpoint:
    def __init__(self, endpoint: Any, budget: RequestBudget):
        self._endpoint = endpoint
        self._budget = budget

    def __getattr__(self, name: str) -> Any:
        method = getattr(self._endpoint, name)
        if not callable(method):
            return method
        budget = self._budget

        def call(*args, **kwargs):
            budget.acquire()
            result = method(*args, **kwargs)
            if kwargs.get("iterator"):
                return _charge_pages(result, budget, int(kwargs.get("limit") or 20))
            budget.observe(getattr(result, "calls_remaining", None), getattr(result, "rate_limit", None))
            return result

        return call


def _charge_pages(rows: Iterator, budget: RequestBudget, page_size: int) -> Iterator:
    """Charge one request for each page after the first as the iterator fetches it."""
    for count, row in enumerate(rows, start=1):
        yield row
        if count % page_size == 0:
            budget.acquire()
