"""Download a notebook file through the time-limited link the Civis API returns.

The file is held in memory and handed to the engine. The link is never logged
or stored, because it carries a signature.
"""

from __future__ import annotations

from scanner.retrieval.http import CIVIS_FILE_HOSTS, FetchError, SafeHttp

NOTEBOOK_MAX_BYTES = 50 * 1024 * 1024


class NotebookUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def fetch_notebook(http: SafeHttp, url: str | None) -> str:
    if not url:
        raise NotebookUnavailable("Civis did not return a link to the notebook file.")
    try:
        data = http.get(url, allowed_hosts=CIVIS_FILE_HOSTS, max_bytes=NOTEBOOK_MAX_BYTES)
    except FetchError as exc:
        raise NotebookUnavailable(f"The notebook file could not be downloaded. {exc.reason}") from None
    return (data or b"").decode("utf-8", errors="replace")
