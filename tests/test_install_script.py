"""The install script (install/civis_install.py, docs/PLAN.md 17.14).

The script runs by hand as a Civis Python script, so these tests load it by path and pass it a recording fake
civis client with made-up objects. Nothing here contacts Civis. The spec checks use the API spec bundled with the
installed civis, 2.10 or 2.11 (the scanner image's version, and the version on Civis's default Python image since
9.2), and skip on civis 2.7.1 (the Service image). civis/base.py, civis/_retries.py, and
civis/resources/_resources.py are the same in 2.10 and 2.11, and no call the script makes changed in the 2.11 spec.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import inspect
import json
import re
import sys
from pathlib import Path

import pytest

from common import release, sql

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "install" / "civis_install.py"
MARK = "zq-install-marker-5521"
ME = 7
OTHER_USER = 8
JOB_ID = 1234
SERVICE_ID = 5678
TAG = "v0.1.0-rc11"
# The scanner image tag of a job set up by an earlier release (before v0.1.0-rc13 moved the image to 9.2.0).
EARLIER_SCANNER_TAG = "9.1.1"
SCANNER_TAG = release.pinned_image("scanner")[1]
READS = {("users", "get_me"), ("users", "list_me"), ("credentials", "get"), ("databases", "get"),
         ("databases", "list"), ("scripts", "get_containers"), ("services", "get")}
WRITES = {("scripts", "post_containers"), ("scripts", "patch_containers"), ("services", "post"),
          ("services", "patch")}


def _load():
    spec = importlib.util.spec_from_file_location("civis_install_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


install = _load()


# ---------------------------------------------------------------------------
# A recording fake civis client
# ---------------------------------------------------------------------------


class FakeAPIError(Exception):
    """Like civis.base.CivisAPIError: a message that may hold names, and a status code."""

    def __init__(self, status_code: int, message: str = f"made-up error text {MARK}"):
        super().__init__(message)
        self.status_code = status_code


def _snake(key: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


def _snake_keys(value):
    """Nested keys as civis 2.10's Response.json() returns them."""
    if isinstance(value, dict):
        return {_snake(k): _snake_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_snake_keys(v) for v in value]
    return value


class Endpoint:
    def __init__(self, fake: "FakeCivis", resource: str):
        self._fake = fake
        self._resource = resource

    def __getattr__(self, method: str):
        if method.startswith("_"):
            raise AttributeError(method)
        self._fake.touched.append((self._resource, method))
        handler = getattr(self._fake, f"_{self._resource}__{method}", None)
        if handler is None or (self._resource, method) in self._fake.missing:
            raise AttributeError(method)

        def call(*args, **kwargs):
            self._fake.calls.append((self._resource, method, args, copy.deepcopy(kwargs)))
            self._fake.results.append(None)  # Kept in step with calls; None for a call that raised.
            error = self._fake.errors.get((self._resource, method))
            if error is not None:
                raise error
            result = copy.deepcopy(handler(*args, **kwargs))
            self._fake.results[-1] = result
            return result

        return call


class FakeCivis:
    def __init__(self):
        self.calls: list[tuple] = []
        self.results: list = []
        self.touched: list[tuple] = []
        self.errors: dict = {}
        self.missing: set = set()
        self.side_effects: dict = {}  # resource -> fields a patch also changes (as Civis might)
        self.me = {"id": ME, "username": MARK, "name": MARK, "email": f"{MARK}@example.test"}
        user = {"id": ME, "username": MARK, "name": MARK}
        self.credential_store = {
            41: {"id": 41, "type": "Database", "name": MARK, "username": MARK, "owner": MARK, "user": user,
                 "description": MARK},
            42: {"id": 42, "type": "Custom", "name": MARK, "username": MARK, "owner": MARK, "user": user},
            51: {"id": 51, "type": "Custom", "name": MARK, "username": MARK, "owner": MARK, "user": user},
            52: {"id": 52, "type": "Github", "name": MARK, "username": MARK, "owner": MARK, "user": user},
        }
        self.database_rows = [{"id": 31, "name": "Example Warehouse", "host": MARK},
                          {"id": 32, "name": f"Other {MARK}", "host": MARK}]
        self.containers: dict[int, dict] = {}
        self.services_store: dict[int, dict] = {}
        self.next_id = 9001
        self.clock = 0
        for name in ("users", "credentials", "databases", "scripts", "services"):
            setattr(self, name, Endpoint(self, name))

    def __getattr__(self, name):  # Anything else on the client (civis.io, other endpoints) is recorded and refused.
        if name.startswith("_"):
            raise AttributeError(name)
        self.__dict__.setdefault("touched", []).append((name, None))
        raise AttributeError(name)

    def _tick(self) -> str:
        self.clock += 1
        return f"2026-11-17T12:00:{self.clock:02d}Z"

    # users
    def _users__get_me(self):
        return self.me

    def _users__list_me(self):
        return self.me

    # credentials and databases
    def _credentials__get(self, id):
        if id not in self.credential_store:
            raise FakeAPIError(404)
        return self.credential_store[id]

    def _databases__get(self, id):
        found = [d for d in self.database_rows if d["id"] == id]
        if not found:
            raise FakeAPIError(404)
        return found[0]

    def _databases__list(self):
        return self.database_rows

    # scripts
    def _scripts__get_containers(self, id):
        if id not in self.containers:
            raise FakeAPIError(404)
        return self.containers[id]

    def _scripts__post_containers(self, required_resources, **fields):
        job_id = self.next_id
        self.next_id += 1
        owner = {"id": ME, "username": MARK, "name": MARK}
        job = {"id": job_id, "name": None, "author": owner, "running_as": owner, "docker_image_name": None,
               "docker_image_tag": None, "docker_command": None, "repo_http_uri": None, "repo_ref": None,
               "git_credential_id": None, "params": [], "arguments": {}, "updated_at": self._tick(),
               "required_resources": _snake_keys(required_resources)}
        job.update(fields)
        self.containers[job_id] = job
        return job

    def _scripts__patch_containers(self, id, **fields):
        if id not in self.containers:
            raise FakeAPIError(404)
        job = self.containers[id]
        for key, value in fields.items():
            job[key] = value if key == "arguments" else _snake_keys(value)
        job.update(self.side_effects.get("scripts", {}))
        job["updated_at"] = self._tick()
        return job

    # services
    def _services__get(self, id):
        if id not in self.services_store:
            raise FakeAPIError(404)
        return self.services_store[id]

    def _services__post(self, **fields):
        service_id = self.next_id
        self.next_id += 1
        service = {"id": service_id, "name": None, "user": {"id": ME, "username": MARK},
                   "docker_image_name": None, "docker_image_tag": None, "git_repo_url": None, "git_repo_ref": None,
                   "environment_variables": {}, "schedule": {"runtime_plan": "specific_times", "recurrences": []},
                   "credentials": [], "updated_at": self._tick(), "current_url": None}
        service.update(fields)
        self.services_store[service_id] = service
        return service

    def _services__patch(self, id, **fields):
        if id not in self.services_store:
            raise FakeAPIError(404)
        service = self.services_store[id]
        for key, value in fields.items():
            service[key] = value if key == "environment_variables" else _snake_keys(value)
        service.update(self.side_effects.get("services", {}))
        service["updated_at"] = self._tick()
        return service

    # helpers for the tests
    def writes(self) -> list[tuple]:
        return [c for c in self.calls if (c[0], c[1]) not in READS]


def existing_job(**changes) -> dict:
    """A made-up scanner job from an earlier release: the earlier image tag, RUN_ID left on it, the old resources,
    LOOKBACK_DAYS 30."""
    owner = {"id": ME, "username": MARK, "name": MARK}
    job = {
        "id": JOB_ID, "name": f"Scanner {MARK}", "description": MARK, "author": owner, "running_as": owner,
        "docker_image_name": "civisanalytics/datascience-python", "docker_image_tag": EARLIER_SCANNER_TAG,
        "docker_command": "cd /app && python -m scanner", "repo_http_uri": release.REPOSITORY_URL,
        "repo_ref": "v0.1.0-rc10", "git_credential_id": None, "updated_at": "2026-11-01T00:00:00Z",
        "required_resources": {"cpu": 1000, "memory": 4096, "disk_space": 4.0},
        "params": [
            {"name": "RESULTS_DB", "type": "database", "required": True, "label": MARK, "description": None,
             "value": None, "default": None, "allowed_values": []},
            {"name": "RESULTS_SCHEMA", "type": "string", "required": True, "label": None, "description": MARK,
             "value": None, "default": None, "allowed_values": []},
            {"name": "LOOKBACK_DAYS", "type": "integer", "required": False, "label": None, "description": None,
             "value": None, "default": None, "allowed_values": []},
            {"name": "RUN_ID", "type": "string", "required": False, "label": None, "description": None,
             "value": None, "default": None, "allowed_values": []},
        ],
        "arguments": {"RESULTS_DB": {"database": 31, "credential": 41}, "RESULTS_SCHEMA": "migration_results",
                      "LOOKBACK_DAYS": 30, "RUN_ID": "run-20261101T000000Z-abc123"},
    }
    job.update(changes)
    return job


def existing_service(**changes) -> dict:
    service = {
        "id": SERVICE_ID, "name": f"App {MARK}", "user": {"id": ME, "username": MARK},
        "docker_image_name": "civisanalytics/civis-services-flask", "docker_image_tag": "0.1.0",
        "git_repo_url": release.REPOSITORY_URL, "git_repo_ref": "v0.1.0-rc10", "credentials": [],
        "environment_variables": {"SCANNER_JOB_ID": str(JOB_ID), "CIVIS_SERVICE_HEALTH_PATH": "/healthz",
                                  "CIVIS_FRAME_ANCESTORS_REPORT_ONLY": "false", "CIVIS_PLATFORM_URL": MARK},
        "schedule": {"runtime_plan": "on_demand", "recurrences": []}, "updated_at": "2026-11-01T00:00:00Z",
    }
    service.update(changes)
    return service


def install_settings(**changes) -> dict:
    settings = {"MODE": "install", "TAG": TAG, "RESULTS_DATABASE": "Example Warehouse", "RESULTS_CREDENTIAL": 41,
                "RESULTS_SCHEMA": "python_migration_results", "GITHUB_CREDENTIAL": None, "LOOKBACK_DAYS": None,
                "RETENTION_DAYS": None, "JOB_ID": None, "SERVICE_ID": None, "INSTALL_SERVICE_ONLY": False}
    settings.update(changes)
    return settings


def update_settings(**changes) -> dict:
    settings = install_settings(MODE="update", RESULTS_DATABASE=None, RESULTS_CREDENTIAL=None, RESULTS_SCHEMA=None,
                                JOB_ID=JOB_ID, SERVICE_ID=SERVICE_ID)
    settings.update(changes)
    return settings


def updatable(job=None, service=None) -> FakeCivis:
    fake = FakeCivis()
    fake.containers[JOB_ID] = job if job is not None else existing_job()
    fake.services_store[SERVICE_ID] = service if service is not None else existing_service()
    return fake


def run(fake: FakeCivis, settings: dict, capsys) -> tuple[int, list[str]]:
    code = install.main(fake, settings)
    return code, capsys.readouterr().out.splitlines()


def line(lines: list[str], start: str) -> str:
    return next(text for text in lines if text.startswith(start))


# ---------------------------------------------------------------------------
# Scenarios every safety test runs over
# ---------------------------------------------------------------------------


def _failing(resource, method, status=500):
    def build():
        fake = FakeCivis()
        fake.errors[(resource, method)] = FakeAPIError(status)
        return fake, install_settings()
    return build


SCENARIOS = {
    "check_install": lambda: (FakeCivis(), install_settings(MODE="check")),
    "check_update": lambda: (updatable(), update_settings(MODE="check")),
    "install": lambda: (FakeCivis(), install_settings()),
    "install_token": lambda: (FakeCivis(), install_settings(GITHUB_CREDENTIAL=51, LOOKBACK_DAYS=60,
                                                            RETENTION_DAYS=90)),
    "install_database_id": lambda: (FakeCivis(), install_settings(RESULTS_DATABASE=31)),
    "install_service_only": lambda: (
        updatable(job=existing_job(repo_ref=TAG)), install_settings(
            RESULTS_DATABASE=None, RESULTS_CREDENTIAL=None, RESULTS_SCHEMA=None, JOB_ID=JOB_ID,
            INSTALL_SERVICE_ONLY=True)),
    "update": lambda: (updatable(), update_settings()),
    "update_clear": lambda: (updatable(job=existing_job(arguments=dict(existing_job()["arguments"], GITHUB=51),
                                                        params=existing_job()["params"] + [
                                                            {"name": "GITHUB", "type": "credential_custom"}])),
                             update_settings(GITHUB_CREDENTIAL="clear", RETENTION_DAYS="clear")),
    "update_other_owner": lambda: (updatable(job=existing_job(author={"id": OTHER_USER, "username": MARK})),
                                   update_settings()),
    "update_service_other": lambda: (updatable(service=existing_service(
        git_repo_url="https://github.com/example-org/other.git",
        environment_variables={"SCANNER_JOB_ID": "99", "OTHER": MARK})), update_settings()),
    "job_post_fails": _failing("scripts", "post_containers"),
    "job_patch_fails": _failing("scripts", "patch_containers"),
    "service_post_fails": _failing("services", "post"),
    "service_patch_fails": _failing("services", "patch"),
    "credential_fails": _failing("credentials", "get", 403),
    "databases_fail": _failing("databases", "list", 503),
    "not_custom": lambda: (FakeCivis(), install_settings(GITHUB_CREDENTIAL=52)),
    "bad_settings": lambda: (FakeCivis(), install_settings(TAG="main")),
}


@pytest.fixture(params=sorted(SCENARIOS))
def scenario(request, capsys):
    fake, settings = SCENARIOS[request.param]()
    code, lines = run(fake, settings, capsys)
    return request.param, fake, code, lines


# ---------------------------------------------------------------------------
# Part A: what the script can touch
# ---------------------------------------------------------------------------


def test_only_the_four_write_calls_and_the_listed_reads(scenario):
    _, fake, _, _ = scenario
    called = {(c[0], c[1]) for c in fake.calls}
    assert called <= READS | WRITES, called - READS - WRITES
    assert all(method is not None for _, method in fake.touched), "the script used something other than an endpoint"


def test_the_script_names_only_the_listed_calls():
    """Every civis method the source names is a listed read or one of the four writes."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    named = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) and node.value.attr in {
                "users", "credentials", "databases", "scripts", "services"}:
            named.add((node.value.attr, node.attr))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "users":
            named.add(("users", node.attr))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
            target, name = node.args[0], node.args[1]
            if isinstance(target, ast.Name) and target.id == "users" and isinstance(name, ast.Constant):
                named.add(("users", name.value))
    assert named == READS | WRITES


def test_check_mode_makes_no_write_call(capsys):
    for fake, settings in (SCENARIOS["check_install"](), SCENARIOS["check_update"]()):
        code, lines = run(fake, settings, capsys)
        assert code == 0
        assert fake.writes() == []
        assert lines[-1].startswith("Nothing changed. This was MODE \"check\".")
        assert any(text.startswith("PLAN scanner_job:") for text in lines)
        assert any(text.startswith("PLAN service:") for text in lines)


def test_every_patch_goes_to_a_checked_or_just_created_object(scenario):
    name, fake, _, _ = scenario
    for index, (resource, method, args, _) in enumerate(fake.calls):
        if method not in ("patch_containers", "patch"):
            continue
        previous = fake.calls[index - 1]
        target = args[0]
        if name.startswith("update"):
            read = "get_containers" if resource == "scripts" else "get"
            assert previous[:3] == (resource, read, (target,)), "not read fresh right before the patch"
            assert target == (JOB_ID if resource == "scripts" else SERVICE_ID)
        else:
            post = "post_containers" if resource == "scripts" else "post"
            assert previous[:2] == (resource, post)
            assert fake.results[index - 1]["id"] == target


@pytest.mark.parametrize("job, reason", [
    (existing_job(author={"id": OTHER_USER, "username": MARK}), "reason=owner"),
    (existing_job(docker_image_name="example/other-image"), "reason=image"),
    (existing_job(docker_command="python export.py"), "reason=command"),
    (existing_job(docker_command="cd /app && python -m scanner --other"), "reason=command"),
    (existing_job(author=None, docker_image_name=None), "reason=owner,image"),
])
def test_update_refuses_a_job_that_is_not_this_tools_or_not_the_users(job, reason, capsys):
    fake = updatable(job=job)
    code, lines = run(fake, update_settings(), capsys)
    assert code == 1 and fake.writes() == []
    assert line(lines, "CHECK scanner_job:") == f"CHECK scanner_job: fail {reason}"
    assert lines[-1] == "Nothing changed."


@pytest.mark.parametrize("service, reason", [
    (existing_service(user={"id": OTHER_USER}), "reason=owner"),
    (existing_service(docker_image_name="civisanalytics/civis-services-streamlit"), "reason=image"),
    (existing_service(git_repo_url="https://github.com/example-org/other.git",
                      environment_variables={"SCANNER_JOB_ID": "99"}), "reason=repository_and_job_id"),
    (existing_service(git_repo_url=None, environment_variables={}), "reason=repository_and_job_id"),
])
def test_update_refuses_a_service_that_is_not_this_tools_or_not_the_users(service, reason, capsys):
    fake = updatable(service=service)
    code, lines = run(fake, update_settings(), capsys)
    assert code == 1 and fake.writes() == []
    assert line(lines, "CHECK service:") == f"CHECK service: fail {reason}"
    assert lines[-1] == "Nothing changed."


@pytest.mark.parametrize("service", [
    existing_service(git_repo_url="https://github.com/example-org/other.git"),  # SCANNER_JOB_ID matches.
    existing_service(environment_variables={"SCANNER_JOB_ID": "99"}),  # The repository matches.
    existing_service(git_repo_url=None),  # Cleared repository; SCANNER_JOB_ID matches.
])
def test_a_service_passes_with_this_repository_or_the_job_id(service, capsys):
    fake = updatable(service=service)
    code, lines = run(fake, update_settings(), capsys)
    assert code == 0, lines
    assert line(lines, "CHECK service:") == "CHECK service: pass"
    after = fake.services_store[SERVICE_ID]
    assert release.is_this_repository(after["git_repo_url"])
    assert after["environment_variables"]["SCANNER_JOB_ID"] == str(JOB_ID)


def test_a_service_that_fails_its_fresh_check_is_not_patched(capsys):
    """The checks pass, then the Service changes before its patch: the job was updated, the Service is not."""
    fake = updatable()
    original = fake._scripts__patch_containers

    def patch_and_change_the_service(id, **fields):
        fake.services_store[SERVICE_ID]["user"] = {"id": OTHER_USER}
        return original(id, **fields)

    fake._scripts__patch_containers = patch_and_change_the_service
    code, lines = run(fake, update_settings(), capsys)
    assert code == 1
    assert [c[:2] for c in fake.writes()] == [("scripts", "patch_containers")]
    assert "CHECK service_before_update: fail reason=owner" in lines
    assert any(text.startswith("RECOVER: The scanner job was updated; the Service was not.") for text in lines)


def test_a_failed_check_stops_before_any_write_and_says_nothing_changed(capsys):
    for build in (SCENARIOS["credential_fails"], SCENARIOS["databases_fail"], SCENARIOS["not_custom"],
                  SCENARIOS["bad_settings"]):
        fake, settings = build()
        code, lines = run(fake, settings, capsys)
        assert code == 1 and fake.writes() == []
        assert lines[-1] == "Nothing changed."
        assert any(re.match(r"^CHECK [a-z_]+: fail (reason=[a-z_,]+|error=[A-Za-z]+( status=\d+)?)$", t)
                   for t in lines), lines


def test_settings_are_checked_before_any_civis_call(capsys):
    fake = FakeCivis()
    code, lines = run(fake, install_settings(RESULTS_SCHEMA="bad schema; drop"), capsys)
    assert code == 1 and fake.calls == []
    assert "CHECK settings: fail reason=results_schema_not_a_plain_name" in lines


def test_no_sql_and_no_civis_io_in_any_mode(scenario):
    _, fake, _, _ = scenario
    assert not any(resource == "io" for resource, _ in fake.touched)
    for call in fake.calls:
        assert "query" not in call[1] and "sql" not in call[1]


def test_the_source_sends_no_sql_and_never_names_civis_io():
    text = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(text)
    io_names = {"io", "query_civis", "read_civis", "read_civis_sql", "civis_to_csv", "csv_to_civis",
                "dataframe_to_civis", "file_to_civis", "civis_to_file", "civis_file_to_table"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in io_names, node.attr
        if isinstance(node, ast.Name):
            assert node.id not in io_names, node.id
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not re.match(r"\s*(SELECT|INSERT|CREATE|DELETE|DROP|ALTER|GRANT|UPDATE|TRUNCATE)\s",
                                node.value), node.value[:40]


def test_it_never_calls_a_delete_archive_share_run_or_deployment_endpoint(scenario):
    _, fake, _, _ = scenario
    forbidden = re.compile(r"delete|archive|share|runs|deploy|transfer|clone|token|project|put")
    assert not [c[:2] for c in fake.calls if forbidden.search(c[1])]
    assert not [t for t in fake.touched if t[1] and forbidden.search(t[1])]


def test_the_header_states_the_design():
    header = " ".join(ast.get_docstring(ast.parse(SCRIPT.read_text(encoding="utf-8"))).split())
    for words in ("scripts.post_containers, scripts.patch_containers, services.post, and services.patch",
                  "sends no SQL, calls nothing in civis.io", "read fresh right before the patch",
                  "Every check runs before the first write", "\"Nothing changed.\"",
                  "never deletes, archives, or shares anything", "never starts a scanner run or a Service deployment",
                  "\"KEEP IN CIVIS:\"", "imports nothing else from this repository"):
        assert words in header, words


# ---------------------------------------------------------------------------
# Part B: install
# ---------------------------------------------------------------------------


def test_install_sends_the_expected_post_and_patch_fields_in_order(capsys):
    fake = FakeCivis()
    code, lines = run(fake, install_settings(), capsys)
    assert code == 0, lines
    writes = fake.writes()
    assert [w[:2] for w in writes] == [("scripts", "post_containers"), ("scripts", "patch_containers"),
                                       ("services", "post"), ("services", "patch")]
    job_post, job_patch, service_post, service_patch = (w[3] for w in writes)
    assert job_post == {
        "name": "Python Migration Scanner: scanner job",
        "required_resources": {"cpu": 500, "memory": 2048, "disk_space": 1},
        "docker_image_name": "civisanalytics/datascience-python", "docker_image_tag": SCANNER_TAG,
        "docker_command": "cd /app && python -m scanner",
    }
    job_id = fake.results[[c[1] for c in fake.calls].index("post_containers")]["id"]
    assert writes[1][2] == (job_id,)
    assert job_patch == {
        "repo_http_uri": release.REPOSITORY_URL, "repo_ref": TAG,
        "params": [{"name": "RESULTS_DB", "type": "database", "required": True},
                   {"name": "RESULTS_SCHEMA", "type": "string", "required": True}],
        "arguments": {"RESULTS_DB": {"database": 31, "credential": 41},
                      "RESULTS_SCHEMA": "python_migration_results"},
    }
    assert service_post == {"name": "Python Migration Scanner: web app",
                            "docker_image_name": "civisanalytics/civis-services-flask", "docker_image_tag": "0.1.0"}
    service_id = fake.results[[c[1] for c in fake.calls].index("post")]["id"]
    assert writes[3][2] == (service_id,)
    assert service_patch == {
        "git_repo_url": release.REPOSITORY_URL, "git_repo_ref": TAG,
        "environment_variables": {"SCANNER_JOB_ID": str(job_id), "CIVIS_SERVICE_HEALTH_PATH": "/healthz",
                                  "CIVIS_FRAME_ANCESTORS_REPORT_ONLY": "false"},
        "schedule": {"runtimePlan": "on_demand", "recurrences": []}, "credentials": [],
    }
    assert line(lines, "KEEP IN CIVIS:").startswith(f"KEEP IN CIVIS: scanner job ID {job_id}, Service ID {service_id}.")


def test_install_with_a_github_credential_uses_the_token_profile_and_sets_the_optional_parameters(capsys):
    fake = FakeCivis()
    code, lines = run(fake, install_settings(GITHUB_CREDENTIAL=51, LOOKBACK_DAYS=60, RETENTION_DAYS=90), capsys)
    assert code == 0, lines
    post, patch = fake.writes()[0][3], fake.writes()[1][3]
    assert post["required_resources"] == {"cpu": 1000, "memory": 4096, "disk_space": 4}
    assert [p["name"] for p in patch["params"]] == ["RESULTS_DB", "RESULTS_SCHEMA", "GITHUB", "LOOKBACK_DAYS",
                                                    "RETENTION_DAYS"]
    assert {p["name"]: p["type"] for p in patch["params"]} == {
        "RESULTS_DB": "database", "RESULTS_SCHEMA": "string", "GITHUB": "credential_custom",
        "LOOKBACK_DAYS": "integer", "RETENTION_DAYS": "integer"}
    assert all(p["required"] is False for p in patch["params"][2:])
    assert patch["arguments"] == {"RESULTS_DB": {"database": 31, "credential": 41},
                                  "RESULTS_SCHEMA": "python_migration_results", "GITHUB": 51, "LOOKBACK_DAYS": 60,
                                  "RETENTION_DAYS": 90}
    assert "RESULT scanner_job resources_set: pass profile=with_token" in lines


def test_install_writes_results_db_in_the_object_shape_and_no_run_id(capsys):
    fake = FakeCivis()
    code, lines = run(fake, install_settings(RESULTS_DATABASE=31), capsys)
    assert code == 0, lines
    patch = fake.writes()[1][3]
    assert patch["arguments"]["RESULTS_DB"] == {"database": 31, "credential": 41}
    assert "RUN_ID" not in json.dumps(fake.writes())
    assert "RESULT scanner_job results_db_shape: object" in lines
    # The app reads the same value as the "object" shape.
    pytest.importorskip("flask")
    from app.job_settings import database_value

    assert database_value(patch["arguments"]["RESULTS_DB"]) == (31, 41, "object")


def test_install_prints_the_result_lines_and_the_next_steps(capsys):
    code, lines = run(FakeCivis(), install_settings(), capsys)
    assert code == 0
    for expected in ("RESULT scanner_job repository_set: pass", "RESULT scanner_job tag_set: pass",
                     "RESULT scanner_job image_set: pass",
                     "RESULT scanner_job resources_set: pass profile=without_token",
                     "RESULT scanner_job params: pass names=RESULTS_DB,RESULTS_SCHEMA",
                     "RESULT scanner_job other_fields_changed: none", "RESULT service repository_set: pass",
                     "RESULT service tag_set: pass", "RESULT service image_set: pass", "RESULT service env_set: pass",
                     "RESULT service on_demand: pass", "RESULT service credentials_none: pass",
                     "RESULT service other_fields_changed: none"):
        assert expected in lines, expected
    keep = line(lines, "KEEP IN CIVIS:")
    assert "needed" in keep or "need these IDs" in keep
    assert "do not send them to anyone who supports your setup" in keep
    steps = [t for t in lines if t.startswith("NEXT ")]
    assert steps == [t for t in lines[-len(steps):]]
    text = " ".join(steps)
    for words in ("Civis admins only", "remove the default group", "Start Deployment",
                  "If it shows Set up results storage, click it.",
                  "Setup page", "archive this script's copy"):
        assert words in text, words


def test_a_database_name_is_looked_up_and_must_match_one_database(capsys):
    fake = FakeCivis()
    fake.database_rows.append({"id": 33, "name": "example warehouse"})
    code, lines = run(fake, install_settings(), capsys)  # The exact name wins over the case-insensitive one.
    assert code == 0 and fake.writes()[1][3]["arguments"]["RESULTS_DB"]["database"] == 31
    fake = FakeCivis()
    fake.database_rows = [{"id": 31, "name": "EXAMPLE WAREHOUSE"}, {"id": 33, "name": "example warehouse"}]
    code, lines = run(fake, install_settings(), capsys)
    assert code == 1 and "CHECK results_database: fail reason=several_match" in lines and fake.writes() == []
    fake = FakeCivis()
    code, lines = run(fake, install_settings(RESULTS_DATABASE="No Such Database"), capsys)
    assert code == 1 and "CHECK results_database: fail reason=not_found" in lines and fake.writes() == []


def test_the_results_credential_must_be_a_database_credential(capsys):
    fake = FakeCivis()
    code, lines = run(fake, install_settings(RESULTS_CREDENTIAL=42), capsys)
    assert code == 1 and fake.writes() == []
    assert "CHECK results_credential: fail reason=not_a_database_credential" in lines


@pytest.mark.parametrize("mode", ["install", "update"])
def test_a_github_credential_that_is_not_custom_is_refused(mode, capsys):
    fake = updatable() if mode == "update" else FakeCivis()
    settings = update_settings(GITHUB_CREDENTIAL=52) if mode == "update" else install_settings(GITHUB_CREDENTIAL=52)
    code, lines = run(fake, settings, capsys)
    assert code == 1 and fake.writes() == []
    assert "CHECK github_credential: fail reason=not_custom" in lines


def test_the_credential_secret_is_never_asked_for(scenario):
    _, fake, _, _ = scenario
    assert not any("secret" in c[1] or "password" in c[1] or "temporary" in c[1] for c in fake.calls)


@pytest.mark.parametrize("failing, created, recover", [
    (("scripts", "post_containers"), [], "Civis did not confirm a new scanner job."),
    (("scripts", "patch_containers"), ["scanner job ID"], "Archive it in Civis"),
    (("services", "post"), ["scanner job ID"], "INSTALL_SERVICE_ONLY = True"),
    (("services", "patch"), ["scanner job ID", "Service ID"], "INSTALL_SERVICE_ONLY = True"),
])
def test_a_failure_after_a_write_prints_what_was_created_and_how_to_recover(failing, created, recover, capsys):
    fake = FakeCivis()
    fake.errors[failing] = FakeAPIError(503)
    code, lines = run(fake, install_settings(), capsys)
    assert code == 1
    assert any(t.startswith("WRITE ") and t.endswith("fail error=FakeAPIError status=503") for t in lines)
    keep = [t for t in lines if t.startswith("KEEP IN CIVIS:")]
    if created:
        assert len(keep) == 1 and all(words in keep[0] for words in created)
    else:
        assert keep == []
    assert any(t.startswith("RECOVER:") and recover in t for t in lines), lines
    if failing[0] == "services":
        assert any("MODE \"update\"" in t for t in lines if t.startswith("RECOVER:"))
        assert any("MODE \"install\"" in t for t in lines if t.startswith("RECOVER:"))


def test_a_failed_first_write_does_not_claim_nothing_changed(capsys):
    """A write that fails may still have changed something, so the script never says "Nothing changed." after one."""
    for build in (SCENARIOS["job_post_fails"], SCENARIOS["install_service_only"]):
        fake, settings = build()
        fake.errors[("services", "post")] = FakeAPIError(504)
        code, lines = run(fake, settings, capsys)
        assert code == 1 and "Nothing changed." not in lines
        assert any(t.startswith("RECOVER:") and "archive it" in t for t in lines)


def test_install_service_only_creates_only_the_service_for_the_checked_job(capsys):
    fake, settings = SCENARIOS["install_service_only"]()
    code, lines = run(fake, settings, capsys)
    assert code == 0, lines
    assert [w[:2] for w in fake.writes()] == [("services", "post"), ("services", "patch")]
    assert fake.writes()[1][3]["environment_variables"]["SCANNER_JOB_ID"] == str(JOB_ID)
    assert line(lines, "KEEP IN CIVIS:").startswith("KEEP IN CIVIS: Service ID ")
    fake = updatable()  # The job is still on another tag.
    code, lines = run(fake, settings, capsys)
    assert code == 1 and fake.writes() == [] and "CHECK scanner_job_tag: fail reason=differs_from_tag" in lines


@pytest.mark.parametrize("changes, reason", [
    ({"MODE": "reinstall"}, "mode_not_recognized"),
    ({"TAG": "<release tag>"}, "tag_not_a_release_tag"),
    ({"TAG": "main"}, "tag_not_a_release_tag"),
    ({"RESULTS_CREDENTIAL": None}, "results_credential_missing"),
    ({"RESULTS_DATABASE": ""}, "results_database_missing"),
    ({"RESULTS_SCHEMA": "Robert'); DROP"}, "results_schema_not_a_plain_name"),
    ({"JOB_ID": JOB_ID}, "job_id_and_service_id_are_for_update"),
    ({"GITHUB_CREDENTIAL": "clear"}, "clear_is_for_update"),
    ({"GITHUB_CREDENTIAL": "my-token"}, "github_credential_not_an_id"),
    ({"LOOKBACK_DAYS": 0}, "lookback_days_out_of_range"),
    ({"LOOKBACK_DAYS": "30"}, "lookback_days_not_a_whole_number"),
    ({"RETENTION_DAYS": -1}, "retention_days_out_of_range"),
    ({"INSTALL_SERVICE_ONLY": "yes"}, "install_service_only_not_true_or_false"),
    ({"MODE": "update", "JOB_ID": JOB_ID, "SERVICE_ID": SERVICE_ID}, "results_settings_are_for_install"),
])
def test_settings_that_are_not_valid_are_refused(changes, reason, capsys):
    fake = FakeCivis()
    code, lines = run(fake, install_settings(**changes), capsys)
    assert code == 1 and fake.calls == []
    assert f"CHECK settings: fail reason={reason}" in lines


def test_update_needs_both_ids(capsys):
    for changes, reason in (({"JOB_ID": None}, "job_id_missing"), ({"SERVICE_ID": None}, "service_id_missing")):
        code, lines = run(FakeCivis(), update_settings(**changes), capsys)
        assert code == 1 and f"CHECK settings: fail reason={reason}" in lines


def test_the_default_settings_are_check_mode_with_placeholders():
    assert install.MODE == "check" and install.TAG == "<release tag>"
    assert install.RESULTS_DATABASE is None and install.RESULTS_CREDENTIAL is None and install.RESULTS_SCHEMA is None
    assert install.GITHUB_CREDENTIAL is None and install.JOB_ID is None and install.SERVICE_ID is None
    assert install.INSTALL_SERVICE_ONLY is False
    text = SCRIPT.read_text(encoding="utf-8")
    settings, below = text.split("# Do not change anything below this line.")
    for name in ("MODE", "TAG", "RESULTS_DATABASE", "RESULTS_CREDENTIAL", "RESULTS_SCHEMA", "GITHUB_CREDENTIAL",
                 "LOOKBACK_DAYS", "RETENTION_DAYS", "JOB_ID", "SERVICE_ID", "INSTALL_SERVICE_ONLY"):
        assert re.search(rf"^{name} = .+  # \S", settings, re.MULTILINE), name
    assert "\nimport " not in settings


def test_check_mode_on_the_module_defaults_stops_at_the_placeholder_tag(capsys):
    fake = FakeCivis()
    code = install.main(fake)
    lines = capsys.readouterr().out.splitlines()
    assert code == 1 and fake.calls == [] and "CHECK settings: fail reason=tag_not_a_release_tag" in lines


def test_get_me_falls_back_to_list_me(capsys):
    fake = FakeCivis()
    fake.missing.add(("users", "get_me"))
    code, _ = run(fake, install_settings(MODE="check"), capsys)
    assert code == 0 and ("users", "list_me") in [c[:2] for c in fake.calls]
    assert ("users", "get_me") not in [c[:2] for c in fake.calls]


# ---------------------------------------------------------------------------
# Part B: update
# ---------------------------------------------------------------------------


def test_update_keeps_results_settings_removes_run_id_and_sets_the_listed_fields(capsys):
    fake = updatable()
    before = copy.deepcopy(fake.containers[JOB_ID])
    code, lines = run(fake, update_settings(), capsys)
    assert code == 0, lines
    after = fake.containers[JOB_ID]
    assert after["arguments"]["RESULTS_DB"] == before["arguments"]["RESULTS_DB"]
    assert after["arguments"]["RESULTS_SCHEMA"] == before["arguments"]["RESULTS_SCHEMA"]
    assert [p for p in after["params"] if p["name"] in ("RESULTS_DB", "RESULTS_SCHEMA")] == [
        {"name": "RESULTS_DB", "type": "database", "required": True, "label": MARK},
        {"name": "RESULTS_SCHEMA", "type": "string", "required": True, "description": MARK}]
    assert "RUN_ID" not in after["arguments"] and all(p["name"] != "RUN_ID" for p in after["params"])
    assert after["arguments"]["LOOKBACK_DAYS"] == 30  # Kept: LOOKBACK_DAYS None leaves it as it is.
    assert after["repo_ref"] == TAG
    assert after["required_resources"] == {"cpu": 500, "memory": 2048, "disk_space": 1}
    patch = fake.writes()[0][3]
    assert sorted(patch) == ["arguments", "docker_image_tag", "params", "repo_ref", "required_resources"]
    assert after["docker_image_tag"] == SCANNER_TAG
    service_patch = fake.writes()[1][3]
    assert sorted(service_patch) == ["git_repo_ref"]
    for expected in ("RESULT scanner_job results_kept: pass", "RESULT scanner_job run_id_removed: yes",
                     "RESULT scanner_job resources_set: pass profile=without_token",
                     "RESULT scanner_job other_fields_changed: none", "RESULT service other_fields_changed: none",
                     "RESULT service env_set: pass", "RESULT service on_demand: pass"):
        assert expected in lines, expected
    assert not any(t.startswith("KEEP IN CIVIS:") for t in lines)
    next_text = " ".join(t for t in lines if t.startswith("NEXT "))
    assert "Update Deployment" in next_text and "Start Deployment if the Service is stopped" in next_text


def test_update_without_run_id_reports_no_parameter(capsys):
    job = existing_job(params=existing_job()["params"][:3],
                       arguments={k: v for k, v in existing_job()["arguments"].items() if k != "RUN_ID"})
    fake = updatable(job=job)
    code, lines = run(fake, update_settings(), capsys)
    assert code == 0 and "RESULT scanner_job run_id_removed: no_parameter" in lines
    assert "params" not in fake.writes()[0][3]  # Nothing to change in the parameters.


def test_update_picks_the_profile_from_the_jobs_github_argument(capsys):
    job = existing_job(params=existing_job()["params"] + [{"name": "GITHUB", "type": "credential_custom"}],
                       arguments=dict(existing_job()["arguments"], GITHUB=51))
    fake = updatable(job=job)
    code, lines = run(fake, update_settings(), capsys)
    assert code == 0
    assert "required_resources" not in fake.writes()[0][3]  # Already the profile with a token.
    assert "RESULT scanner_job resources_set: pass profile=with_token" in lines
    fake = updatable(job=copy.deepcopy(job))
    code, lines = run(fake, update_settings(GITHUB_CREDENTIAL="clear"), capsys)
    assert code == 0
    after = fake.containers[JOB_ID]
    assert "GITHUB" not in after["arguments"] and all(p["name"] != "GITHUB" for p in after["params"])
    assert after["required_resources"] == {"cpu": 500, "memory": 2048, "disk_space": 1}


def test_resources_match_reads_either_disk_key():
    wanted = {"cpu": 500, "memory": 2048, "disk_space": 1}
    assert install.resources_match({"cpu": 500, "memory": 2048, "disk_space": 1.0}, wanted)
    assert install.resources_match({"cpu": 500, "memory": 2048, "diskSpace": 1}, wanted)
    assert not install.resources_match({"cpu": 500, "memory": 2048, "diskSpace": 4}, wanted)
    assert not install.resources_match({"cpu": 500, "memory": 2048}, wanted)


def test_more_threads_add_memory_in_both_profiles(capsys):
    job = existing_job(arguments=dict(existing_job()["arguments"], MAX_CONCURRENCY="8"),
                       params=existing_job()["params"] + [{"name": "MAX_CONCURRENCY", "type": "integer"}])
    fake = updatable(job=job)
    run(fake, update_settings(), capsys)
    assert fake.containers[JOB_ID]["required_resources"] == {"cpu": 500, "memory": 4096, "disk_space": 1}
    fake = updatable(job=copy.deepcopy(job))
    run(fake, update_settings(GITHUB_CREDENTIAL=51), capsys)
    assert fake.containers[JOB_ID]["required_resources"] == {"cpu": 1000, "memory": 6144, "disk_space": 4}


def test_update_sets_optional_parameters_and_clears_them(capsys):
    fake = updatable()
    code, _ = run(fake, update_settings(LOOKBACK_DAYS=60, RETENTION_DAYS=90, GITHUB_CREDENTIAL=51), capsys)
    assert code == 0
    after = fake.containers[JOB_ID]
    assert {k: after["arguments"][k] for k in ("LOOKBACK_DAYS", "RETENTION_DAYS", "GITHUB")} == {
        "LOOKBACK_DAYS": 60, "RETENTION_DAYS": 90, "GITHUB": 51}
    fake2 = updatable(job=copy.deepcopy(after))
    code, _ = run(fake2, update_settings(RETENTION_DAYS="clear"), capsys)
    assert code == 0 and "RETENTION_DAYS" not in fake2.containers[JOB_ID]["arguments"]
    assert all(p["name"] != "RETENTION_DAYS" for p in fake2.containers[JOB_ID]["params"])


def test_update_restores_a_cleared_repository_and_sets_the_service(capsys):
    fake = updatable(job=existing_job(repo_http_uri=None),
                     service=existing_service(schedule={"runtime_plan": "always_on", "recurrences": []},
                                              credentials=[{"id": 77, "name": MARK}],
                                              environment_variables={"SCANNER_JOB_ID": str(JOB_ID)}))
    code, lines = run(fake, update_settings(), capsys)
    assert code == 0, lines
    assert fake.containers[JOB_ID]["repo_http_uri"] == release.REPOSITORY_URL
    service = fake.services_store[SERVICE_ID]
    assert service["schedule"] == {"runtime_plan": "on_demand", "recurrences": []}
    assert service["credentials"] == []
    assert service["environment_variables"] == {"SCANNER_JOB_ID": str(JOB_ID), "CIVIS_SERVICE_HEALTH_PATH": "/healthz"}
    patch = fake.writes()[1][3]
    assert patch["schedule"] == {"runtimePlan": "on_demand", "recurrences": []}


def test_update_keeps_other_environment_variables(capsys):
    fake = updatable(service=existing_service(environment_variables={
        "SCANNER_JOB_ID": "99", "CIVIS_FRAME_ANCESTORS_REPORT_ONLY": "true", "CIVIS_PLATFORM_URL": MARK}))
    code, _ = run(fake, update_settings(), capsys)
    assert code == 0
    assert fake.services_store[SERVICE_ID]["environment_variables"] == {
        "SCANNER_JOB_ID": str(JOB_ID), "CIVIS_FRAME_ANCESTORS_REPORT_ONLY": "true", "CIVIS_PLATFORM_URL": MARK,
        "CIVIS_SERVICE_HEALTH_PATH": "/healthz"}


def test_update_lists_other_fields_that_changed(capsys):
    fake = updatable()
    fake.side_effects["scripts"] = {"running_as": {"id": OTHER_USER, "username": MARK}, f"x {MARK}": 1}
    code, lines = run(fake, update_settings(), capsys)
    assert code == 0
    assert "RESULT scanner_job other_fields_changed: running_as" in lines


def test_a_setting_that_did_not_take_fails_the_result_line(capsys):
    fake = updatable()
    fake.side_effects["services"] = {"schedule": {"runtime_plan": "always_on", "recurrences": []}}
    fake.services_store[SERVICE_ID]["schedule"] = {"runtime_plan": "always_on", "recurrences": []}
    code, lines = run(fake, update_settings(), capsys)
    assert code == 1
    assert "RESULT service on_demand: fail" in lines
    assert any(t.startswith("RESULT: some settings did not take.") for t in lines)


def test_results_db_changed_by_civis_fails_results_kept(capsys):
    fake = updatable()
    fake.side_effects["scripts"] = {"arguments": {"RESULTS_SCHEMA": "migration_results"}}
    code, lines = run(fake, update_settings(), capsys)
    assert code == 1 and "RESULT scanner_job results_kept: fail" in lines


def test_update_with_nothing_to_change_writes_nothing(capsys):
    job = existing_job(repo_ref=TAG, docker_image_tag=SCANNER_TAG,
                       required_resources={"cpu": 500, "memory": 2048, "disk_space": 1.0},
                       params=existing_job()["params"][:3],
                       arguments={k: v for k, v in existing_job()["arguments"].items() if k != "RUN_ID"})
    fake = updatable(job=job, service=existing_service(git_repo_ref=TAG))
    code, lines = run(fake, update_settings(), capsys)
    assert code == 0 and fake.writes() == []
    assert "WRITE scanner_job: no change" in lines and "WRITE service: no change" in lines


def test_update_from_an_earlier_image_tag_patches_only_the_tag_and_the_branch(capsys):
    # Set up as the README sets it (no RUN_ID, the profile without a token), but by an earlier release: the earlier
    # image tag and an earlier repo_ref. This is the update from v0.1.0-rc12 to v0.1.0-rc13 (docs/PLAN.md 17.16).
    job = existing_job(required_resources={"cpu": 500, "memory": 2048, "disk_space": 1.0},
                       params=existing_job()["params"][:3],
                       arguments={k: v for k, v in existing_job()["arguments"].items() if k != "RUN_ID"})
    assert job["docker_image_tag"] == EARLIER_SCANNER_TAG != SCANNER_TAG and job["repo_ref"] != TAG
    fake = updatable(job=job)
    code, lines = run(fake, update_settings(GITHUB_CREDENTIAL=None), capsys)
    assert code == 0, lines
    assert line(lines, "PLAN scanner_job: update ").startswith(
        "PLAN scanner_job: update fields=docker_image_tag,repo_ref ")
    assert "WRITE scanner_job: patched fields=docker_image_tag,repo_ref" in lines
    assert "RESULT scanner_job image_set: pass" in lines
    job_patches = [w for w in fake.writes() if w[:2] == ("scripts", "patch_containers")]
    assert len(job_patches) == 1 and job_patches[0][3] == {"docker_image_tag": SCANNER_TAG, "repo_ref": TAG}
    assert fake.containers[JOB_ID]["docker_image_tag"] == SCANNER_TAG


# ---------------------------------------------------------------------------
# Output: no content from Civis
# ---------------------------------------------------------------------------


def test_no_printed_line_holds_a_marker_or_a_message(scenario):
    _, _, _, lines = scenario
    for text in lines:
        assert MARK not in text, text
        assert "made-up error text" not in text, text
    for text in lines:
        if text.startswith(("CHECK ", "PLAN ", "WRITE ", "RESULT ")):
            assert re.match(r"^[A-Z]+ [a-z_ ]+(:| )", text), text


def test_the_keep_line_holds_only_the_new_ids(capsys):
    code, lines = run(FakeCivis(), install_settings(), capsys)
    keep = [t for t in lines if t.startswith("KEEP IN CIVIS:")]
    assert code == 0 and len(keep) == 1
    assert re.findall(r"\d+", keep[0]) == ["9001", "9002"]


def test_error_text_is_the_class_name_and_status_only():
    assert install.error_text(FakeAPIError(404, MARK)) == "error=FakeAPIError status=404"
    assert install.error_text(ValueError(MARK)) == "error=ValueError"


# ---------------------------------------------------------------------------
# The script against the civis 2.10 API spec
# ---------------------------------------------------------------------------


def _spec():
    civis = pytest.importorskip("civis")
    pytest.importorskip("civis._retries")  # civis 2.10 or 2.11 (the scanner image and Civis's Python image); not 2.7.1.
    from civis.resources._resources import generate_classes_maybe_cached

    path = Path(civis.__file__).parent / "resources" / "civis_api_spec.json"
    return generate_classes_maybe_cached(str(path), "unused", "1.0"), json.loads(path.read_text(encoding="utf-8"))


def _definition(raw: dict, path: str, verb: str) -> dict:
    body = next(p for p in raw["paths"][path][verb]["parameters"] if p["in"] == "body")
    return raw["definitions"][body["schema"]["$ref"].split("/")[-1]]


def test_every_call_exists_in_the_civis_2_10_spec_with_the_arguments_it_sends(capsys):
    classes, _ = _spec()
    seen = set()
    for name in sorted(SCENARIOS):
        fake, settings = SCENARIOS[name]()
        run(fake, settings, capsys)
        for resource, method, args, kwargs in fake.calls:
            function = getattr(classes[resource], method, None)
            assert function is not None, (resource, method)
            inspect.signature(function).bind(None, *args, **kwargs)  # Raises TypeError for a wrong argument.
            seen.add((resource, method))
    assert seen == READS - {("users", "list_me")} | WRITES


def test_nested_fields_use_the_live_tested_keys(capsys):
    """required_resources uses disk_space and schedule uses runtimePlan: the forms the live test saved
    (docs/PLAN.md 17.14). Parameter definitions use only the spec's keys."""
    _, raw = _spec()
    schedule = raw["definitions"]["SchedulingMultiScheduleCreateRequest"]
    param_keys = set(raw["definitions"]["ScriptParamCreateRequest"]["properties"])
    for name in ("install", "install_token", "update", "update_clear"):
        fake, settings = SCENARIOS[name]()
        run(fake, settings, capsys)
        for _, method, _, kwargs in fake.writes():
            if "required_resources" in kwargs:
                assert set(kwargs["required_resources"]) == {"cpu", "memory", "disk_space"}
            if "schedule" in kwargs:
                assert set(kwargs["schedule"]) == set(schedule["required"]) == {"runtimePlan", "recurrences"}
            for param in kwargs.get("params", []):
                assert set(param) <= param_keys and {"name", "type"} <= set(param)
                assert param["type"] in {"database", "string", "integer", "credential_custom"}


def test_the_post_calls_send_what_the_spec_requires():
    _, raw = _spec()
    assert _definition(raw, "/scripts/containers", "post")["required"] == ["requiredResources"]
    assert _definition(raw, "/services/", "post").get("required") in (None, [])
    script = install.Installer(FakeCivis(), install_settings())
    script._settings_failure(install_settings())
    assert "required_resources" in script.job_post_fields()


# ---------------------------------------------------------------------------
# Constants and imports
# ---------------------------------------------------------------------------


def test_the_constants_equal_common_release():
    assert install.REPOSITORY_URL == release.REPOSITORY_URL
    assert install.SCANNER_COMMAND == release.SCANNER_COMMAND
    assert install.SCANNER_IMAGE == release.pinned_image("scanner")
    assert install.SERVICE_IMAGE == release.pinned_image("service")
    assert install.HEALTH_PATH == release.HEALTH_PATH
    assert install.DEFAULT_LOOKBACK_DAYS == release.DEFAULT_LOOKBACK_DAYS
    assert install.LOOKBACK_DAYS_RANGE == release.LOOKBACK_DAYS_RANGE
    assert install.RETENTION_DAYS_RANGE == release.RETENTION_DAYS_RANGE
    assert install.PARAMETER_TYPES == release.PARAMETER_TYPES
    assert install.REQUIRED_PARAMETERS == release.REQUIRED_PARAMETERS
    assert install.DEFAULT_THREADS == release.DEFAULT_THREADS
    assert install.MEMORY_PER_EXTRA_THREAD_MB == release.MEMORY_PER_EXTRA_THREAD_MB
    for script_profile, profile in ((install.PROFILE_WITH_TOKEN, release.PROFILE_WITH_TOKEN),
                                    (install.PROFILE_WITHOUT_TOKEN, release.PROFILE_WITHOUT_TOKEN)):
        assert script_profile == (profile.name, profile.cpu, profile.memory_mb, profile.disk_gb)
    assert install.RELEASE_TAG.pattern == release.RELEASE_TAG.pattern
    assert install.IDENTIFIER.pattern == sql._IDENTIFIER.pattern


@pytest.mark.parametrize("url", [
    release.REPOSITORY_URL, "github.com/ExpeditionData/python-migration-scanner-for-civis-by-ed",
    "https://www.github.com/expeditiondata/python-migration-scanner-for-civis-by-ed/", "https://github.com/x/y.git",
    "", None,
])
def test_the_repository_check_matches_the_app(url):
    assert install.is_this_repository(url) == release.is_this_repository(url)


@pytest.mark.parametrize("changes", [
    {}, {"docker_image_name": "example/other"}, {"docker_command": "python -m scanner"},
    {"docker_command": "  cd /app && python -m scanner  "},
])
def test_the_job_image_and_command_check_matches_the_app(changes):
    job = existing_job(**changes)
    app_says = {m for m in release.scanner_job_mismatches(job) if m in ("image", "command")}
    assert set(install.job_mismatches(job, ME)) == app_says


def test_the_script_imports_only_the_standard_library_and_civis():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports"
            imported.add((node.module or "").split(".")[0])
    assert "civis" in imported
    assert imported - {"civis"} <= set(sys.stdlib_module_names), imported
    assert not imported & {"app", "common", "scanner", "install", "tests"}
