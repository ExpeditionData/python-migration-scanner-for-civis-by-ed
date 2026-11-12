"""Download a notebook file through the time-limited link the Civis API returns.

Each ``notebooks.get`` call returns a new link that expires 5 minutes later
(``X-Amz-Expires=300``, seen in the live test, docs/PLAN.md 17.9). Inventory
ends many minutes before the code and log scan reaches a notebook, so a link
read during inventory has expired by then. ``NotebookLinks`` asks Civis for a
fresh link right before each download, through the scan's request budget, and
downloads it at once. If S3 refuses that download with HTTP 403 or 400, it asks
for one more link and downloads that once.

The file is held in memory and handed to the engine. The link is never logged,
stored, or put in an exception, because it carries a signature.
"""

from __future__ import annotations

import threading
from typing import Any

from scanner.inventory.base import describe_error, field_of
from scanner.retrieval.http import CIVIS_FILE_HOSTS, FetchError, SafeHttp

NOTEBOOK_MAX_BYTES = 50 * 1024 * 1024
# A refused download is retried once with a new link only for these statuses: S3 answers 403 for an
# expired link (AccessDenied, "Request has expired") and 400 for some malformed or expired requests.
RETRY_STATUSES = frozenset({400, 403})
NO_LINK = "Civis did not return a link to the notebook file."
LINK_FAILED = "The link to the notebook file could not be requested from Civis."


class NotebookUnavailable(Exception):
    """``reason`` is safe to log and store; it never holds a URL. ``status`` is the HTTP status of a refused
    download, when there was one."""

    def __init__(self, reason: str, status: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def fetch_notebook(http: SafeHttp, url: str | None) -> str:
    if not url:
        raise NotebookUnavailable(NO_LINK)
    try:
        data = http.get(url, allowed_hosts=CIVIS_FILE_HOSTS, max_bytes=NOTEBOOK_MAX_BYTES)
    except FetchError as exc:
        raise NotebookUnavailable(f"The notebook file could not be downloaded. {exc.reason}", exc.status_code) from None
    return (data or b"").decode("utf-8", errors="replace")


class NotebookLinks:
    """Fresh notebook links right before each download, with counts for the final RUN line.

    ``links`` counts the notebooks.get calls made for downloads, retries included; ``retries`` counts the
    second links asked for after a refused download. Shared by the scan's worker threads.
    """

    def __init__(self, client: Any):
        self._client = client  # The scan's BudgetedClient, so each call is charged to the request budget.
        self._lock = threading.Lock()
        self.links = 0
        self.retries = 0

    def download(self, http: SafeHttp, notebook_id: int) -> str:
        """The notebook file's text. Raises NotebookUnavailable with a reason that holds no link."""
        try:
            return fetch_notebook(http, self._link(notebook_id))
        except NotebookUnavailable as exc:
            if exc.status not in RETRY_STATUSES:
                raise
        with self._lock:
            self.retries += 1
        return fetch_notebook(http, self._link(notebook_id))

    def _link(self, notebook_id: int) -> str:
        with self._lock:
            self.links += 1
        try:
            detail = self._client.notebooks.get(notebook_id)
        except Exception as exc:  # The same short reason as other detail calls; never the message text.
            raise NotebookUnavailable(f"{LINK_FAILED} {describe_error(exc)}") from None
        url = field_of(detail, "notebook_url")
        if not isinstance(url, str) or not url:
            raise NotebookUnavailable(NO_LINK)
        return url
