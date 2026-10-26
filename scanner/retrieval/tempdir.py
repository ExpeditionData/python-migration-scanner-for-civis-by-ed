"""Private temporary directories for git checkouts and downloaded files.

The directory is created with owner-only permissions (0700) and deleted in a
``finally`` block, so it is removed even when the scan fails partway through.
Read-only files (git marks its object files read-only) are made writable and
removed on retry.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

PREFIX = "pmsc-"


def _make_writable_and_retry(func, path, _exc) -> None:
    try:
        os.chmod(path, stat.S_IRWXU)
        func(path)
    except FileNotFoundError:
        pass


@contextmanager
def private_tempdir() -> Iterator[Path]:
    path = tempfile.mkdtemp(prefix=PREFIX)  # mkdtemp already uses mode 0700 on POSIX.
    try:
        os.chmod(path, stat.S_IRWXU)
        yield Path(path)
    finally:
        shutil.rmtree(path, onexc=_make_writable_and_retry)
