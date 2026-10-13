"""A synthetic stand-in for requests.Session, serving made-up GitHub and notebook files.

Routes:

- ``https://api.github.com/repos/<owner>/<repo>/tarball[/<ref>]`` answers with a
  302 redirect to codeload.github.com, as GitHub does. Repos and refs come from
  the "github" section of the synthetic instance. A missing repo is a 404. A
  repo marked "rate_limited" answers 403 with X-RateLimit-Remaining: 0.
- ``https://codeload.github.com/...`` answers with a gzipped tarball built in
  memory, with GitHub's top-level folder.
- Notebook links listed in "notebook_files" answer with the notebook JSON.

Any other URL raises ConnectionError, so a test fails loudly if the scanner
tries to reach somewhere it should not.
"""

from __future__ import annotations

import io
import json
import tarfile
from urllib.parse import unquote, urlsplit


class SyntheticResponse:
    def __init__(self, status_code: int, body: bytes = b"", headers: dict | None = None):
        self.status_code = status_code
        self._body = body
        self.headers = dict(headers or {})
        self.closed = False

    def iter_content(self, chunk_size: int = 65536):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]

    def close(self) -> None:
        self.closed = True


def build_tarball(top: str, files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, text in sorted(files.items()):
            data = text.encode("utf-8")
            info = tarfile.TarInfo(f"{top}/{path}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class SyntheticSession:
    def __init__(self, data: dict, token: str | None = "synthetic-github-token"):
        self.github = data.get("github", {})
        self.notebooks = data.get("notebook_files", {})
        self.token = token
        self.requests: list[tuple[str, dict]] = []  # (url without query, headers) for tests.

    def get(self, url, headers=None, stream=False, allow_redirects=True, timeout=None):
        headers = dict(headers or {})
        parts = urlsplit(url)
        self.requests.append((f"{parts.scheme}://{parts.netloc}{parts.path}", headers))
        if parts.netloc == "api.github.com":
            return self._github_api(parts.path, headers)
        if parts.netloc == "codeload.github.com":
            return self._codeload(parts.path)
        if url in self.notebooks:
            return SyntheticResponse(200, json.dumps(self.notebooks[url]).encode("utf-8"))
        if parts.netloc.endswith(".amazonaws.com"):
            return SyntheticResponse(404)
        raise ConnectionError("The synthetic session does not serve this host.")

    def _github_api(self, path: str, headers: dict) -> SyntheticResponse:
        pieces = [unquote(p) for p in path.strip("/").split("/")]
        if len(pieces) < 4 or pieces[0] != "repos" or pieces[3] != "tarball":
            return SyntheticResponse(404)
        owner, name = pieces[1], pieces[2]
        ref = pieces[4] if len(pieces) > 4 else None
        if self.token and headers.get("Authorization") != f"Bearer {self.token}":
            return SyntheticResponse(401)
        repo = self.github.get(f"{owner}/{name}")
        if repo is None:
            return SyntheticResponse(404)
        if repo.get("rate_limited"):
            return SyntheticResponse(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "4102444800"})
        ref = ref or repo.get("default", "main")
        if ref not in repo.get("refs", {}):
            return SyntheticResponse(404)
        return SyntheticResponse(302, headers={"Location": f"https://codeload.github.com/{owner}/{name}/legacy.tar.gz/{ref}"})

    def _codeload(self, path: str) -> SyntheticResponse:
        pieces = [unquote(p) for p in path.strip("/").split("/")]
        owner, name, ref = pieces[0], pieces[1], pieces[3]
        files = self.github[f"{owner}/{name}"]["refs"][ref]
        return SyntheticResponse(200, build_tarball(f"{owner}-{name}-0a1b2c3", files))
