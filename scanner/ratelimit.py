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
in ``civis_retrying`` (docs/PLAN.md 17.5), and every Civis API request has a
connect and read timeout (``install_request_timeout``, docs/PLAN.md 17.12).
"""

from __future__ import annotations

import functools
import importlib
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from typing import Any

from scanner import scan_log

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

    civis sets no request timeout, so before docs/PLAN.md 17.12 a request that never
    got a response held the lock with no time limit. install_request_timeout gives
    every Civis API request a connect and read timeout; see its docstring for the
    longest one request can hold the lock now.

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


# A timeout on every Civis API request (docs/PLAN.md 17.12): (connect, read) for requests.
REQUEST_CONNECT_TIMEOUT_SECONDS = 15
REQUEST_READ_TIMEOUT_SECONDS = 120
# civis modules that call open_session by name: Endpoint._make_request (every API call) in civis.base, and
# the API spec download when the client is made in civis.resources._resources (imported there by name).
_SESSION_MODULES = ("civis.base", "civis.resources._resources")
_WRAPPED = "_pmsc_request_timeout"


class _Count:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.value = 0

    def add(self) -> None:
        with self._lock:
            self.value += 1


_request_timeouts = _Count()


def request_timeouts() -> int:
    """Civis API requests that ended in a timeout in this process (the RUN line counts this scan's share)."""
    return _request_timeouts.value


def install_request_timeout() -> bool:
    """Give every civis-python API request a timeout. Returns True when installed.

    civis 2.10 makes a new requests session for each request with ``civis.base.open_session`` and sends it
    with ``sess.send(prepared)`` and no timeout (``Endpoint._make_request``, through
    ``civis._retries.retry_request``); the API spec download when the client is made does the same, with
    ``open_session`` imported by name into ``civis.resources._resources``. This replaces ``open_session``
    in those two modules with a wrapper that returns the same session with a ``send`` that sets
    ``timeout=(REQUEST_CONNECT_TIMEOUT_SECONDS, REQUEST_READ_TIMEOUT_SECONDS)`` when the caller gave none
    (a caller's own timeout is kept), and counts requests that end in a timeout (``request_timeouts``)
    before raising the error again. requests is never patched globally: SafeHttp and civis.io's own file
    transfers keep their own timeouts. Calling it again wraps nothing twice. If either module has no
    ``open_session`` (another civis version), nothing is installed. Logs ``REQUEST_TIMEOUT installed=yes
    connect=15 read=120`` or ``REQUEST_TIMEOUT installed=no``.

    What a timeout does: requests raises ``ConnectTimeout`` (no connection within 15 seconds) or
    ``ReadTimeout`` (no byte from the server for 120 seconds) out of the API call. civis's retry policy
    retries only on status codes, so a timeout is not retried and raises at once, and civis's lock is
    released. A timeout while the response body is being read surfaces from requests as a
    ``ConnectionError`` caused by urllib3's ``ReadTimeoutError``; it is counted too.

    The longest one request can hold civis's lock: civis_retrying checks its 120 seconds after each try.
    A try that answers with a retried status (for example 503) just before 120 seconds, a wait of at most
    30 seconds, and one more try of at most 15 + 120 seconds give about 285 seconds (4 minutes 45 seconds).
    Two limits remain: the read timeout is the time between bytes, so a server that keeps sending slowly
    can take longer, and civis waits at least a ``Retry-After`` header before a retry, whatever it says.
    """
    modules = []
    for name in _SESSION_MODULES:
        try:
            module = importlib.import_module(name)
        except ImportError:
            module = None
        if module is None or not callable(getattr(module, "open_session", None)):
            scan_log.request_timeout(installed=False)
            return False
        modules.append(module)
    for module in modules:
        if not getattr(module.open_session, _WRAPPED, False):
            module.open_session = _open_session_with_timeout(module.open_session)
    scan_log.request_timeout(installed=True, connect=REQUEST_CONNECT_TIMEOUT_SECONDS,
                             read=REQUEST_READ_TIMEOUT_SECONDS)
    return True


def _timed_out(exc: BaseException) -> bool:
    """requests' Timeout (ConnectTimeout, ReadTimeout), or its ConnectionError for a timeout while the body is
    read. Matched by class name, so this module imports no HTTP client (only SafeHttp does)."""
    classes = {(cls.__module__, cls.__name__) for cls in type(exc).__mro__}
    if ("requests.exceptions", "Timeout") in classes:
        return True
    return ("requests.exceptions", "ConnectionError") in classes and any(
        type(arg).__name__ == "ReadTimeoutError" for arg in exc.args)


def _open_session_with_timeout(open_session: Callable) -> Callable:
    @functools.wraps(open_session)
    def open_session_with_timeout(*args: Any, **kwargs: Any) -> Any:
        session = open_session(*args, **kwargs)
        send = session.send

        def send_with_timeout(request: Any, **send_kwargs: Any) -> Any:
            if send_kwargs.get("timeout") is None:
                send_kwargs["timeout"] = (REQUEST_CONNECT_TIMEOUT_SECONDS, REQUEST_READ_TIMEOUT_SECONDS)
            try:
                return send(request, **send_kwargs)
            except Exception as exc:
                if _timed_out(exc):
                    _request_timeouts.add()
                raise

        session.send = send_with_timeout
        return session

    setattr(open_session_with_timeout, _WRAPPED, True)
    return open_session_with_timeout


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
