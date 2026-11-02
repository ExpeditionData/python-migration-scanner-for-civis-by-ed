"""A synthetic civis APIClient for unit tests and dry runs. No network calls.

It serves made-up responses from a JSON file (synthetic_instance.json). Detail responses use the
field names in civis-python's API spec. List calls return only the fields the
real list endpoints return, so code that needs a detail-only field (such as
``source``) must call the detail endpoint, as it would against Civis.

Fixture conventions, per object:
- "_listed": false  -> readable by ID, but not returned by list calls
- "_error": 403     -> the detail call raises FakeAPIError(403)

Git files served through Civis ("civis_git", keyed "python3/<id>" or
"workflows/<id>"): "commits" is the list the commits endpoint returns, and
"files" maps a full commit SHA to the file content (a shortened SHA finds it by
prefix) or to {"content": ..., "size": ...} to set the reported size.
"commits_error" or "file_error" makes that call raise FakeAPIError.
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).parent

# Fields returned by list endpoints, from civis-python's typed client stub.
SCRIPT_LIST_FIELDS = {
    "id", "name", "type", "created_at", "updated_at", "author", "state", "finished_at",
    "projects", "parent_id", "is_template", "from_template_id", "links", "time_zone",
    "last_run", "archived", "template_script_id",
}
CUSTOM_LIST_FIELDS = {
    "id", "name", "type", "backing_script_type", "created_at", "updated_at", "author", "state",
    "finished_at", "projects", "parent_id", "from_template_id", "time_zone", "last_run",
    "archived", "last_successful_run",
}
NOTEBOOK_LIST_FIELDS = {
    "id", "name", "language", "description", "user", "created_at", "updated_at",
    "most_recent_deployment", "archived",
}
SERVICE_LIST_FIELDS = {
    "id", "name", "description", "user", "type", "created_at", "updated_at", "git_repo_url",
    "git_repo_ref", "git_path_dir", "current_deployment", "archived",
}
WORKFLOW_LIST_FIELDS = {
    "id", "name", "description", "valid", "file_id", "user", "state", "schedule",
    "allow_concurrent_executions", "time_zone", "next_execution_at", "archived", "created_at", "updated_at",
}


class FakeAPIError(Exception):
    """Stands in for civis.base.CivisAPIError, which carries status_code."""

    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def _public(row: dict, allowed: set[str] | None = None) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("_") and (allowed is None or k in allowed)}


class _Endpoint:
    def __init__(self, client: "FakeClient", name: str):
        self._client = client
        self._name = name

    def __getattr__(self, method: str):
        handler = getattr(self._client, f"_{self._name}__{method}", None)
        if handler is None:
            raise AttributeError(f"FakeClient has no {self._name}.{method}")

        def call(*args, **kwargs):
            self._client.calls.append((f"{self._name}.{method}", args, kwargs))
            return handler(*args, **kwargs)

        return call


class FakeClient:
    def __init__(self, data: dict):
        self.data = data
        self.calls: list[tuple[str, tuple, dict]] = []
        for name in ("scripts", "templates", "notebooks", "services", "workflows", "jobs", "users"):
            setattr(self, name, _Endpoint(self, name))

    @classmethod
    def from_fixture(cls, name: str) -> "FakeClient":
        return cls(json.loads((FIXTURES / name).read_text(encoding="utf-8")))

    def called(self, endpoint_method: str) -> list[tuple[tuple, dict]]:
        return [(a, k) for name, a, k in self.calls if name == endpoint_method]

    # -- helpers ----------------------------------------------------------

    def _list(self, rows: list[dict], fields: set[str], hidden=None, archived="false", **_ignored):
        assert archived == "false", "inventory must only list non-archived objects"
        result = []
        for row in rows:
            if row.get("_listed", True) is False or row.get("archived", False):
                continue
            if bool(row.get("hidden", False)) != bool(hidden):
                continue
            result.append(_public(row, fields))
        return iter(result)

    def _get(self, rows: list[dict] | dict, object_id: int) -> dict:
        if isinstance(rows, dict):
            row = rows.get(str(object_id))
        else:
            row = next((r for r in rows if r["id"] == object_id), None)
        if row is None:
            raise FakeAPIError(404)
        if row.get("_error"):
            raise FakeAPIError(row["_error"])
        return _public(row)

    # -- scripts ----------------------------------------------------------

    def _scripts__list(self, type=None, **kwargs):
        rows = self.data.get({"python3": "python3", "containers": "containers", "dbt": "dbt"}[type], [])
        return self._list(rows, SCRIPT_LIST_FIELDS, **kwargs)

    def _scripts__list_custom(self, **kwargs):
        return self._list(self.data.get("custom", []), CUSTOM_LIST_FIELDS, **kwargs)

    def _scripts__get_python3(self, object_id):
        return self._get(self.data.get("python3", []), object_id)

    def _scripts__get_python3_git(self, object_id):
        git = self.data.get("python3_git", {}).get(str(object_id))
        if git is None:
            return {"git_ref": None, "git_branch": None, "git_path": None, "git_repo": None, "pull_from_git": False}
        return git

    def _scripts__list_python3_git_commits(self, object_id):
        return self._git_commits(f"python3/{object_id}")

    def _scripts__get_python3_git_commits(self, object_id, commit_hash):
        return self._git_file(f"python3/{object_id}", commit_hash)

    def _scripts__get_containers(self, object_id):
        return self._get(self.data.get("containers", []), object_id)

    def _scripts__get_dbt(self, object_id):
        return self._get(self.data.get("dbt", []), object_id)

    def _scripts__get_custom(self, object_id):
        return self._get(self.data.get("custom", []), object_id)

    # -- git files read through Civis --------------------------------------

    def _git_entry(self, key: str) -> dict:
        entry = self.data.get("civis_git", {}).get(key)
        if entry is None:
            raise FakeAPIError(404)
        return entry

    def _git_commits(self, key: str) -> list[dict]:
        entry = self._git_entry(key)
        if entry.get("commits_error"):
            raise FakeAPIError(entry["commits_error"])
        return [dict(c) for c in entry.get("commits", [])]  # An array, as civis-python treats this path.

    def _git_file(self, key: str, commit_hash: str) -> dict:
        entry = self._git_entry(key)
        if entry.get("file_error"):
            raise FakeAPIError(entry["file_error"])
        found = [v for sha, v in entry.get("files", {}).items() if sha.startswith(commit_hash)]
        if len(found) != 1:
            raise FakeAPIError(404)
        file = found[0] if isinstance(found[0], dict) else {"content": found[0]}
        content = file.get("content", "")
        size = file.get("size", len(content.encode("utf-8")))
        return {"content": content, "type": "file", "size": size, "file_hash": "0" * 40}

    # -- other endpoints ----------------------------------------------------

    def _templates__get_scripts(self, template_id):
        return self._get(self.data.get("templates", {}), template_id)

    def _notebooks__list(self, **kwargs):
        return self._list(self.data.get("notebooks", []), NOTEBOOK_LIST_FIELDS, **kwargs)

    def _notebooks__get(self, object_id):
        return self._get(self.data.get("notebooks", []), object_id)

    def _services__list(self, **kwargs):
        return self._list(self.data.get("services", []), SERVICE_LIST_FIELDS, **kwargs)

    def _services__get(self, object_id):
        return self._get(self.data.get("services", []), object_id)

    def _workflows__list(self, **kwargs):
        return self._list(self.data.get("workflows", []), WORKFLOW_LIST_FIELDS, **kwargs)

    def _workflows__get(self, object_id):
        return self._get(self.data.get("workflows", []), object_id)

    def _workflows__list_git_commits(self, object_id):
        return self._git_commits(f"workflows/{object_id}")

    def _workflows__get_git_commits(self, object_id, commit_hash):
        return self._git_file(f"workflows/{object_id}", commit_hash)

    def _jobs__get(self, job_id):
        return self._get(self.data.get("jobs", {}), job_id)

    def _users__list(self, account_status=None, iterator=False, limit=None):
        rows = self.data.get("users", [])
        if account_status != "all":
            rows = [r for r in rows if r.get("active")]
        return iter([_public(r) for r in rows])

    def _users__get_me(self):
        return self.data["me"]

    def _jobs__list_runs_logs(self, job_id, run_id, last_id=None, limit=None):
        key = f"{job_id}/{run_id}"
        if key in self.data.get("log_errors", {}):
            raise FakeAPIError(self.data["log_errors"][key])
        entries = sorted(self.data.get("run_logs", {}).get(key, []), key=lambda e: e["id"])
        if last_id is not None:
            entries = [e for e in entries if e["id"] > last_id]
        return entries[: limit or 10000]

    def _services__list_deployments_logs(self, service_id, deployment_id, start_at=None, end_at=None, limit=None):
        entries = self.data.get("deployment_logs", {}).get(f"{service_id}/{deployment_id}", [])
        return entries[: limit or 10000]
