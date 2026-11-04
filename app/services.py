"""Connects the pages to a data layer: Civis inside the Service, or local preview files.

Inside Civis, a new civis APIClient is made for each request, because Civis
rotates the Service's API key when it wakes and when it is redeployed.

The results database, credential, and schema come from the scanner job's
parameters (app/job_settings.py). Each worker keeps the job's settings for
``JOB_SETTINGS_SECONDS`` (a failed read for ``JOB_SETTINGS_RETRY_SECONDS``),
and the Setup page and the "Set up results storage" button read them fresh.
When they change, the results cache is cleared.

Each gunicorn worker process has its own Services object (its own cache,
progress, and locks). Requests inside a worker run in threads, so shared state
here is guarded by locks: ``progress_lock`` for parsed scan progress,
``start_lock`` so two requests in one worker cannot both pass the "no scan is
running" check and start two scans, and ``settings_lock`` for the job settings.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from flask import g

from app.backends.base import Jobs, Progress, ResultsUnavailable
from app.backends.cache import CachedResults
from app.job_settings import JobSettings
from app.settings import Settings

JOB_SETTINGS_SECONDS = 300.0
JOB_SETTINGS_RETRY_SECONDS = 20.0


@dataclass
class Services:
    settings: Settings
    results: CachedResults | None
    jobs_provider: Callable[[], Jobs] | None
    local: bool = False
    local_user: str | None = None
    setup_provider: Callable[[], Any] | None = None  # The Setup page's reads (app/setup.py).
    clock: Callable[[], float] = time.monotonic
    progress: dict[int, Progress] = field(default_factory=dict)
    progress_lock: threading.Lock = field(default_factory=threading.Lock)
    start_lock: threading.Lock = field(default_factory=threading.Lock)
    settings_lock: threading.Lock = field(default_factory=threading.Lock)
    _job_settings: tuple[float, JobSettings] | None = None

    def jobs(self) -> Jobs | None:
        return self.jobs_provider() if self.jobs_provider else None

    def job_settings(self, fresh: bool = False) -> JobSettings | None:
        """The scanner job's settings, cached per worker. None when SCANNER_JOB_ID is not set."""
        jobs = self.jobs()
        if jobs is None:
            return None
        with self.settings_lock:
            cached = self._job_settings
            if cached and not fresh:
                age = self.clock() - cached[0]
                if age < (JOB_SETTINGS_SECONDS if cached[1].problem != "job_unreadable" else JOB_SETTINGS_RETRY_SECONDS):
                    return cached[1]
        value = jobs.settings()
        with self.settings_lock:
            changed = self._job_settings is not None and self._job_settings[1].target != value.target
            self._job_settings = (self.clock(), value)
        if changed and self.results is not None:
            self.results.clear()
        return value

    def results_backend(self, make: Callable[[JobSettings], Any]) -> Any:
        """The results backend for this request, or ResultsUnavailable if the job's settings cannot be used."""
        settings = self.job_settings()
        if settings is None:
            raise ResultsUnavailable("not_configured")
        if settings.problem == "job_unreadable":
            raise ResultsUnavailable("job_unreadable")
        if settings.problem:
            raise ResultsUnavailable("not_configured")
        return make(settings)


def _request_client() -> Any:
    """One civis APIClient per request, made only when a page needs Civis."""
    if "civis_client" not in g:
        import civis  # In the Service image.

        g.civis_client = civis.APIClient()
    return g.civis_client


def _civis_io() -> Any:
    import civis  # In the Service image.

    return civis.io


def civis_services(
    settings: Settings,
    client_factory: Callable[[], Any] = _request_client,
    io_factory: Callable[[], Any] = _civis_io,
) -> Services:
    """The Civis data layer. The factories are replaced only by tests."""
    from app.backends.civis_backend import CivisJobs, CivisResults

    job_id = settings.scanner_job_id
    jobs_provider = (lambda: CivisJobs(client_factory(), job_id)) if job_id else None
    services = Services(settings=settings, results=None, jobs_provider=jobs_provider)

    def make(job: JobSettings) -> CivisResults:
        return CivisResults(client_factory(), io_factory(), job.database_id, job.schema, job.credential_id)

    if job_id:
        services.results = CachedResults(lambda: services.results_backend(make))
    return services


def local_services(folder, state: str = "normal") -> Services:
    from app.backends.local_backend import LocalJobs, LocalResults

    results_backend = LocalResults(folder, state)
    jobs_backend = LocalJobs(folder, state)
    settings = Settings(scanner_job_id=jobs_backend.job_id)
    services = Services(
        settings=settings,
        results=None,
        jobs_provider=(lambda: jobs_backend) if jobs_backend.job_id else None,
        local=True,
        local_user="local preview user",
    )
    if jobs_backend.job_id:
        services.results = CachedResults(lambda: services.results_backend(lambda job: results_backend))
    return services
