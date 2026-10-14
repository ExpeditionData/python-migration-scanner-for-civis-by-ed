"""Connects the pages to a data layer: Civis inside the Service, or local preview files.

Inside Civis, a new civis APIClient is made for each request, because Civis
rotates the Service's API key when it wakes and when it is redeployed.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from flask import g

from app.backends.base import Jobs, Progress
from app.backends.cache import CachedResults
from app.settings import Settings


@dataclass
class Services:
    settings: Settings
    results: CachedResults | None
    jobs_provider: Callable[[], Jobs] | None
    local: bool = False
    local_user: str | None = None
    progress: dict[int, Progress] = field(default_factory=dict)
    progress_lock: threading.Lock = field(default_factory=threading.Lock)

    def jobs(self) -> Jobs | None:
        return self.jobs_provider() if self.jobs_provider else None


def _request_client() -> Any:
    """One civis APIClient per request, made only when a page needs Civis."""
    if "civis_client" not in g:
        import civis  # In the Service image.

        g.civis_client = civis.APIClient()
    return g.civis_client


def civis_services(settings: Settings) -> Services:
    from app.backends.civis_backend import CivisJobs, CivisResults

    results = None
    if settings.results_configured:
        def provider():
            import civis

            return CivisResults(
                _request_client(), civis.io, settings.results_database, settings.results_schema,
                settings.results_credential_id,
            )

        results = CachedResults(provider)
    jobs_provider = None
    if settings.scanner_job_id:
        def jobs_provider():
            return CivisJobs(_request_client(), settings.scanner_job_id)
    return Services(settings=settings, results=results, jobs_provider=jobs_provider)


def local_services(folder, state: str = "normal") -> Services:
    from app.backends.local_backend import LOCAL_SCHEMA, LocalJobs, LocalResults

    results_backend = LocalResults(folder, state)
    jobs_backend = LocalJobs(folder, state)
    settings = Settings(
        scanner_job_id=jobs_backend.job_id, results_database="local preview", results_schema=LOCAL_SCHEMA,
    )
    return Services(
        settings=settings,
        results=CachedResults(lambda: results_backend),
        jobs_provider=(lambda: jobs_backend) if jobs_backend.job_id else None,
        local=True,
        local_user="local preview user",
    )
