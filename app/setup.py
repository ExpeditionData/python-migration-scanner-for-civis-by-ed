"""The Setup page: ten read-only checks of the scanner job, the Service, and the results storage.

``gather`` collects the facts through a source (app/backends/civis_backend.py
inside Civis, app/backends/local_backend.py in local preview). Every call is a
read; none of them writes. ``evaluate`` turns the facts into one line per
check, each with a result (pass, warning, fail, or review), a one-line reason,
and the fix. It calls nothing.

The page is for Civis admins only (like the whole app). It may show user and
group names from sharing, escaped by the template. The test summary
(app/summary.py) uses only each line's number, short ID, and result.
Errors are kept as an exception class name and HTTP status only, and nothing
here is logged.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.backends.base import StorageFacts, error_token
from app.job_settings import DATABASE_PARAMETER, SCHEMA_PARAMETER, JobSettings, lookback_days, parameter_value, parameters
from common import release, sql
from common.schema import TABLES

RESULTS = ("pass", "warning", "fail", "review")
_RANK = {"pass": 0, "review": 1, "warning": 2, "fail": 3}
# How the app found its own Service. Civis documents no environment variable with a Service's ID (the
# "Advanced Deployments" help names CIVIS_SERVICE_PORT, CIVIS_SERVICE_HEALTH_PATH, CIVIS_SERVICE_VERSION,
# CIVIS_PERMISSION_SET_ID, and REPO_PATH_DIR), so "environment_variable" is never used today.
SERVICE_METHODS = ("environment_variable", "single_match", "several_matches", "none", "list_failed")
LINE_KEYS = (
    "job_found", "job_settings", "job_git_credential", "job_parameters", "one_owner", "sharing", "service",
    "frame_rule", "results_storage", "latest_scan",
)
LATEST_STATES = ("unknown", "none", "queued", "running", "succeeded", "failed", "cancelled", "tables_failed")
OPTIONAL_PARAMETERS = ("GITHUB", "LOOKBACK_DAYS", "MAX_CONCURRENCY", "REQUEST_BUDGET", "RETENTION_DAYS", "RUN_ID")
README_PARAMETERS = (DATABASE_PARAMETER, SCHEMA_PARAMETER) + OPTIONAL_PARAMETERS
TOKEN_PARAMETER = OPTIONAL_PARAMETERS[0]  # The optional GitHub token parameter. The app never reads its value.
MAX_SERVICES = 1000
# Civis puts the deploying user's GitHub token into a Service under this name when that user's Civis profile has
# GitHub credentials ("Civis Service Deployment" help). The app checks only that it is set and not empty; it never
# keeps, shows, or logs the value, and never contacts GitHub.
REPO_TOKEN_VARIABLE = "REPO_HTTP_TOKEN"
REPO_TOKEN_REASON = ("Civis added the deploying user's GitHub token to this Service; the app does not read it or "
                     "contact GitHub.")
REPO_TOKEN_FIX = "To keep it out, deploy the Service as a user with no GitHub credentials on their Civis profile."
TABLE_NAMES = tuple(t.name for t in TABLES)


@dataclass(frozen=True)
class Fetch:
    """One read: its value, or the error type if it failed."""

    value: Any = None
    error: str | None = None


def fetch(call: Callable[..., Any], *args: Any) -> Fetch:
    try:
        return Fetch(call(*args))
    except Exception as exc:  # The error type only; never the message, never logged.
        return Fetch(error=error_token(exc))


@dataclass
class SetupFacts:
    job_id: int | None
    settings: JobSettings | None
    me: Fetch = field(default_factory=Fetch)
    credential: Fetch | None = None
    job_shares: Fetch | None = None  # None: not checked, because the job could not be read.
    service_method: str = "none"
    service_matches: int = 0
    service: Fetch | None = None
    service_shares: Fetch | None = None
    password_env_count: int = 0
    repo_token_present: bool = False
    report_only: bool = False
    storage: StorageFacts | None = None
    latest: str = "none"

    @property
    def job(self) -> dict | None:
        return self.settings.job if self.settings is not None and self.settings.job is not None else None


@dataclass(frozen=True)
class Line:
    number: int
    key: str
    title: str
    result: str
    reason: str
    fix: str | None = None
    details: tuple[str, ...] = ()


def password_env_count(env: Mapping[str, str] | None = None) -> int:
    """How many environment variables in this process end in _PASSWORD. A count only: no names, no values."""
    env = os.environ if env is None else env
    return sum(1 for name in env if name.upper().endswith("_PASSWORD"))


def repo_token_present(env: Mapping[str, str] | None = None) -> bool:
    """Whether Civis put a GitHub token into this Service: set and not empty. The value is not kept."""
    env = os.environ if env is None else env
    return bool((env.get(REPO_TOKEN_VARIABLE) or "").strip())


def gather(source: Any, job_id: int | None, settings: JobSettings | None, *, report_only: bool, latest: str,
           env: Mapping[str, str] | None = None) -> SetupFacts:
    """Read everything the checks need. ``source`` makes read calls only."""
    facts = SetupFacts(job_id=job_id, settings=settings, report_only=report_only,
                       password_env_count=password_env_count(env), repo_token_present=repo_token_present(env),
                       latest=latest if latest in LATEST_STATES else "unknown")
    if source is None:
        return facts
    readable = settings is not None and settings.problem != "job_unreadable"
    facts.me = fetch(source.me)
    if readable:
        if settings.credential_id is not None:
            facts.credential = fetch(source.credential, settings.credential_id)
        facts.job_shares = fetch(source.job_shares)
    listed = fetch(source.services)
    if listed.error:
        facts.service_method = "list_failed"
    else:
        matches = [s for s in (listed.value or []) if isinstance(s, dict) and release.is_this_repository(s.get("git_repo_url"))]
        facts.service_matches = len(matches)
        if len(matches) == 1:
            facts.service_method = "single_match"
            service_id = matches[0].get("id")
            facts.service = fetch(source.service, service_id)
            facts.service_shares = fetch(source.service_shares, service_id)
        else:
            facts.service_method = "several_matches" if matches else "none"
    if settings is not None and settings.usable:
        try:
            facts.storage = source.storage(settings)
        except Exception as exc:
            facts.storage = StorageFacts(error=error_token(exc), step="schema")
    return facts


# ---------------------------------------------------------------------------
# The ten checks
# ---------------------------------------------------------------------------


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "es" if noun.endswith(("ch", "s")) else "s")


def _sentence(texts: list[str]) -> str:
    text = "; ".join(texts)
    return (text[:1].upper() + text[1:] + ".") if text else ""


def _worst(results: list[str]) -> str:
    return max(results, key=lambda r: _RANK[r]) if results else "pass"


def _not_checked(number: int, key: str, title: str) -> Line:
    return Line(number, key, title, "review", "Not checked, because the scanner job could not be read.",
                "Fix line 1 first.")


def evaluate(facts: SetupFacts) -> list[Line]:
    job = facts.job
    lines = [_job_found(facts)]
    if job is None:
        lines += [
            _not_checked(2, "job_settings", "Scanner job settings match the README"),
            _not_checked(3, "job_git_credential", "Scanner job git credential is blank"),
            _not_checked(4, "job_parameters", "Scanner job parameters"),
        ]
    else:
        lines += [_job_settings(job), _git_credential(job), _parameters(job, facts.settings)]
    lines += [_one_owner(facts), _sharing(facts), _service(facts), _frame_rule(facts), _storage(facts),
              _latest(facts)]
    return lines


def _job_found(facts: SetupFacts) -> Line:
    title = "Scanner job found and readable"
    if facts.settings is None:
        return Line(1, "job_found", title, "fail", "SCANNER_JOB_ID is not set on this Service.",
                    "Add SCANNER_JOB_ID with the scanner job's ID (README, \"Create the Service\", step 5), then "
                    "click Update Deployment.")
    if facts.settings.problem == "job_unreadable":
        return Line(1, "job_found", title, "fail",
                    f"Civis did not return the scanner job (error type: {facts.settings.error_type}).",
                    "Check that SCANNER_JOB_ID is the scanner job's ID and that the Service owner can see the job.")
    mismatches = release.scanner_job_mismatches(facts.job)
    if mismatches:
        return Line(1, "job_found", title, "fail",
                    f"Job {facts.job_id} is not this tool's scanner job: its {', '.join(mismatches)} differ from the "
                    "README. Run scan is off, and the app uses none of its settings.",
                    "Set SCANNER_JOB_ID to the scanner job's ID (README \"Create the Service\", step 5), or fix the "
                    "job as line 2 says.")
    return Line(1, "job_found", title, "pass", f"The scanner job ({facts.job_id}) was read.")


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _threads(job: dict) -> int:
    value = _number(parameter_value(job, "MAX_CONCURRENCY"))
    return int(value) if value and value >= 1 else release.DEFAULT_THREADS


def _job_settings(job: dict) -> Line:
    title = "Scanner job settings match the README"
    issues: list[tuple[str, str]] = []  # (result, text)
    mismatches = release.scanner_job_mismatches(job)  # The same identity check as line 1 and the start path.
    pinned = release.pinned_image("scanner")
    if pinned is None:
        issues.append(("fail", "the pinned image could not be read from requirements-scanner.txt"))
    else:
        if "image" in mismatches:
            issues.append(("fail", f"Docker Image Name is not {pinned[0]}"))
        if (job.get("docker_image_tag") or "").strip() != pinned[1]:
            issues.append(("fail", f"Docker Image Tag is not {pinned[1]}"))
    if "repository" in mismatches:
        issues.append(("fail", "GitHub Repository URL is not this repository"))
    ref = (job.get("repo_ref") or "").strip()
    if not release.is_release_tag(ref):
        if ref.startswith("v"):
            issues.append(("warning", "GitHub Repository Reference starts with v but is not a release tag "
                                      "name such as v0.1.0"))
        else:
            issues.append(("fail", "GitHub Repository Reference is main, a branch, or blank, not a release tag"))
    if "command" in mismatches:
        issues.append(("fail", f"Command is not {release.SCANNER_COMMAND}"))
    resources = job.get("required_resources") or {}
    threads = _threads(job)
    memory_needed = release.SCANNER_MEMORY_MB + release.MEMORY_PER_EXTRA_THREAD_MB * max(0, threads - release.DEFAULT_THREADS)
    for label, key, expected, unit in (
        ("CPU", "cpu", release.SCANNER_CPU, ""), ("Memory", "memory", memory_needed, " MB"),
        ("Disk Space", "disk_space", release.SCANNER_DISK_GB, " GB"),
    ):
        value = _number(resources.get(key) if isinstance(resources, dict) else None)
        if value is None or value < expected:
            issues.append(("fail", f"{label} is below {expected}{unit}"))
        elif value > expected:
            issues.append(("warning", f"{label} is larger than {expected}{unit} (more than needed)"))
    if not issues:
        return Line(2, "job_settings", title, "pass",
                    "Image, repository, release tag, command, CPU, memory, and disk match the README.")
    result = _worst([r for r, _ in issues])
    return Line(2, "job_settings", title, result, _sentence([text for _, text in issues]),
                "Change these on the scanner job to the values in README \"Create the scanner job\", step 3, "
                "signed in as the Robot user.")


def _git_credential(job: dict) -> Line:
    title = "Scanner job git credential is blank"
    if job.get("git_credential_id"):
        return Line(3, "job_git_credential", title, "warning", "A git credential is set on the scanner job.",
                    "Clear it. The repository is public and needs none (README \"Create the scanner job\", step 3).")
    return Line(3, "job_git_credential", title, "pass", "No git credential is set.")


def _parameters(job: dict, settings: JobSettings | None) -> Line:
    title = "Scanner job parameters"
    by_name = {p.get("name"): p for p in parameters(job) if isinstance(p.get("name"), str)}
    issues: list[tuple[str, str]] = []
    details: list[str] = []
    for name, wanted in ((DATABASE_PARAMETER, "database"), (SCHEMA_PARAMETER, "string")):
        param = by_name.get(name)
        if param is None:
            issues.append(("fail", f"{name} is missing"))
        elif str(param.get("type") or "").lower() != wanted:
            issues.append(("fail", f"{name} is not the {wanted.capitalize()} type"))
    shape = settings.database_shape if settings is not None else "missing"
    if DATABASE_PARAMETER in by_name:
        if shape == "missing":
            issues.append(("fail", f"{DATABASE_PARAMETER} has no value"))
        elif shape == "not_recognized":
            issues.append(("fail", f"the shape of the {DATABASE_PARAMETER} value was not recognized"))
    if SCHEMA_PARAMETER in by_name:
        raw = parameter_value(job, SCHEMA_PARAMETER)
        if raw in (None, ""):
            issues.append(("fail", f"{SCHEMA_PARAMETER} has no value"))
        else:
            try:
                sql.identifier(str(raw).strip())
            except sql.UnsafeValueError:
                issues.append(("fail", f"{SCHEMA_PARAMETER} is not a plain schema name"))
    token = by_name.get(TOKEN_PARAMETER)
    if token is not None and str(token.get("type") or "").lower() != "credential_custom":
        issues.append(("warning", f"{TOKEN_PARAMETER} is not the Custom Credential type"))
    unknown = sorted(name for name in by_name if name not in README_PARAMETERS)
    if unknown:
        issues.append(("warning", f"{_plural(len(unknown), 'parameter name')} not in the README (a typo?)"))
        details.append("Parameter names not in the README: " + ", ".join(unknown))
    lookback = by_name.get(release.LOOKBACK_PARAMETER)
    if lookback is not None and str(lookback.get("type") or "").lower() != "integer":
        issues.append(("warning", f"{release.LOOKBACK_PARAMETER} is not the Integer type"))
    if lookback_days(job) is None:
        issues.append(("fail", f"{release.LOOKBACK_PARAMETER} is not a whole number from 1 to 3,650"))
    if parameter_value(job, "RUN_ID") not in (None, ""):
        issues.append(("warning", "RUN_ID has a value, so every scan resumes that run"))
    details.append(f"{DATABASE_PARAMETER} value shape: {shape}")
    if not issues:
        return Line(4, "job_parameters", title, "pass",
                    f"{DATABASE_PARAMETER} and {SCHEMA_PARAMETER} are set, and every parameter name is in the README.",
                    details=tuple(details))
    return Line(4, "job_parameters", title, _worst([r for r, _ in issues]),
                _sentence([text for _, text in issues]),
                "Fix the parameters on the scanner job as in README \"Create the scanner job\", steps 4 and 5. "
                "Clear RUN_ID after a resume.", tuple(details))


def _identity(value: Any) -> tuple[int | None, str | None]:
    if not isinstance(value, dict):
        return None, None
    user_id = value.get("id")
    name = value.get("username")
    return (user_id if isinstance(user_id, int) and not isinstance(user_id, bool) else None,
            name if isinstance(name, str) and name else None)


def _same(a: tuple[int | None, str | None], b: tuple[int | None, str | None]) -> bool | None:
    """Compare by user ID where both have one, otherwise by user name. None if they cannot be compared."""
    if a[0] is not None and b[0] is not None:
        return a[0] == b[0]
    if a[1] is not None and b[1] is not None:
        return a[1] == b[1]
    return None


def _one_owner(facts: SetupFacts) -> Line:
    title = "One owner: the app's user, the scanner job, and the results credential"
    fix = ("Create or transfer the scanner job and the Service to the Robot user, and choose a RESULTS_DB credential "
           "that belongs to it (README \"Before you start\").")
    if facts.job is None:
        return _not_checked(5, "one_owner", title)
    if facts.me.error or not isinstance(facts.me.value, dict):
        return Line(5, "one_owner", title, "review",
                    f"The app's own user could not be read (error type: {facts.me.error or 'Exception'}).", fix)
    app_user = _identity(facts.me.value)
    roles: list[tuple[str, tuple[int | None, str | None]]] = [("job owner", _identity(facts.job.get("author")))]
    if isinstance(facts.job.get("running_as"), dict):
        roles.append(("job runs as", _identity(facts.job.get("running_as"))))
    unknown = []
    if facts.credential is None:
        unknown.append("credential owner (no credential in RESULTS_DB)")
    elif facts.credential.error or not isinstance(facts.credential.value, dict):
        unknown.append(f"credential owner (not readable, error type: {facts.credential.error or 'Exception'})")
    else:
        credential = facts.credential.value
        owner = _identity(credential.get("user"))
        if owner == (None, None) and isinstance(credential.get("owner"), str):
            owner = (None, credential["owner"])
        roles.append(("credential owner", owner))
    differ = []
    for role, identity in roles:
        same = _same(app_user, identity)
        if same is False:
            differ.append(role)
        elif same is None:
            unknown.append(role)
    if differ:
        return Line(5, "one_owner", title, "fail",
                    f"These differ from the user the app runs as: {', '.join(differ)}.", fix)
    if unknown:
        return Line(5, "one_owner", title, "review", f"Could not compare: {', '.join(unknown)}.", fix)
    return Line(5, "one_owner", title, "pass", "The app's user owns the scanner job and the results credential"
                + (", and the job runs as that user." if any(r == "job runs as" for r, _ in roles) else "."))


SHARE_LEVELS = ("readers", "writers", "owners")
SHAPE_NOT_RECOGNIZED = "shape_not_recognized"


def _share_entries(value: Any) -> list[dict] | None:
    """The objects in a shares response, each with readers, writers, and owners. Civis returns one such object
    for scripts.list_containers_shares and services.list_shares (confirmed in the first live test); a list of
    them is accepted too. None if the shape is not recognized."""
    entries = [value] if isinstance(value, dict) else value if isinstance(value, list) else None
    if entries is None:
        return None
    for entry in entries:
        if not isinstance(entry, dict) or not any(isinstance(entry.get(level), dict) for level in SHARE_LEVELS):
            return None
    return entries


def share_members(value: Any) -> tuple[list[str], list[str]] | None:
    """User names and group names with access, from a shares response (readers, writers, and owners).
    None if the response's shape is not recognized."""
    entries = _share_entries(value)
    if entries is None:
        return None
    users: dict[Any, str] = {}
    groups: dict[Any, str] = {}
    for entry in entries:
        for level in SHARE_LEVELS:
            members = entry.get(level) or {}
            if not isinstance(members, dict):
                continue
            for item in members.get("users") or []:
                if isinstance(item, dict):
                    users.setdefault(item.get("id", item.get("name")), str(item.get("name") or item.get("id")))
            for item in members.get("groups") or []:
                if isinstance(item, dict):
                    groups.setdefault(item.get("id", item.get("name")), str(item.get("name") or item.get("id")))
    return sorted(users.values()), sorted(groups.values())


def sharing_counts(facts: SetupFacts) -> dict[str, tuple[int, int] | str | None]:
    """(users, groups) with access to the job and to the Service. None if not read or not checked, and
    SHAPE_NOT_RECOGNIZED if the response was read but its shape was not recognized."""
    counts: dict[str, tuple[int, int] | str | None] = {}
    for label, fetched in (("job", facts.job_shares), ("service", facts.service_shares)):
        if fetched is None or fetched.error:
            counts[label] = None
            continue
        members = share_members(fetched.value)
        counts[label] = (len(members[0]), len(members[1])) if members is not None else SHAPE_NOT_RECOGNIZED
    return counts


def _not_checked_sharing(facts: SetupFacts, label: str) -> str:
    """Why a sharing list was not read, without blaming a read that did not fail."""
    if label == "job":
        if facts.settings is None:
            return "not checked, because SCANNER_JOB_ID is not set"
        if facts.settings.problem == "job_unreadable":
            return "not checked, because the scanner job could not be read"
        return "not checked"
    if facts.service_method != "single_match":
        return "not checked, because the app could not tell which Service it is (line 7)"
    return "not checked"


def _sharing(facts: SetupFacts) -> Line:
    title = "Sharing of the scanner job and the Service"
    parts, details = [], []
    for label, name, fetched in (("job", "Scanner job", facts.job_shares), ("service", "Service", facts.service_shares)):
        if fetched is None:
            parts.append(f"{name}: {_not_checked_sharing(facts, label)}")
            continue
        if fetched.error:
            parts.append(f"{name}: not read (error type: {fetched.error})")
            continue
        members = share_members(fetched.value)
        if members is None:
            parts.append(f"{name}: the sharing list was read but its shape was not recognized")
            continue
        users, groups = members
        parts.append(f"{name}: {_plural(len(users), 'user')} and {_plural(len(groups), 'group')}, including the owner")
        details.append(f"{name} users: " + (", ".join(users) or "none"))
        details.append(f"{name} groups: " + (", ".join(groups) or "none"))
    return Line(6, "sharing", title, "review", ". ".join(parts) + ".",
                "Keep only Civis admins (README \"Create the scanner job\", step 7, and \"Create the Service\", "
                "step 9).", tuple(details))


def _service(facts: SetupFacts) -> Line:
    line = _service_line(facts)
    method = f"Service lookup: {facts.service_method} ({_plural(facts.service_matches, 'match')})"
    result, reason, fix = line.result, line.reason, line.fix
    if facts.repo_token_present:
        # Review, not fail: the deploying user (often the Robot user) may need its GitHub connection elsewhere.
        if result == "pass":
            result, reason, fix = "review", REPO_TOKEN_REASON, REPO_TOKEN_FIX
        else:
            result = _worst([result, "review"])
            reason = f"{reason} {REPO_TOKEN_REASON}"
            fix = f"{fix} {REPO_TOKEN_FIX}" if fix else REPO_TOKEN_FIX
    return Line(line.number, line.key, line.title, result, reason, fix, line.details + (method,))


def _service_line(facts: SetupFacts) -> Line:
    title = "This app's own Service"
    passwords = facts.password_env_count
    password_issue = (("fail", f"{_plural(passwords, 'environment variable')} in the app end in _PASSWORD")
                      if passwords else None)
    fix = ("Use the same release tag on the Service as on the scanner job, the image in README \"Create the Service\", "
           "step 3, and attach no credentials (step 6).")
    if facts.service_method != "single_match" or facts.service is None:
        if facts.service_method == "list_failed":
            reason = "The Services this user can see could not be listed."
        elif facts.service_matches > 1:
            reason = f"{facts.service_matches} Services use this repository, so the app cannot tell which one it is."
        else:
            reason = "No Service this user can see uses this repository."
        if password_issue:
            return Line(7, "service", title, "fail", reason + " " + _sentence([password_issue[1]]), fix)
        return Line(7, "service", title, "review", reason,
                    "Check the Service by hand against README \"Create the Service\", or archive extra copies.")
    if facts.service.error or not isinstance(facts.service.value, dict):
        return Line(7, "service", title, "review",
                    f"The Service could not be read (error type: {facts.service.error or 'Exception'}).", fix)
    service = facts.service.value
    issues: list[tuple[str, str]] = []
    job_ref = (facts.job or {}).get("repo_ref")
    if facts.job is None:
        issues.append(("review", "the scanner job's release tag could not be compared"))
    elif (service.get("git_repo_ref") or "").strip() != (job_ref or "").strip():
        issues.append(("fail", "its Branch is not the scanner job's release tag"))
    pinned = release.pinned_image("service")
    if pinned is None:
        issues.append(("review", "the pinned image could not be read from requirements-service.txt"))
    elif ((service.get("docker_image_name") or "").strip(), (service.get("docker_image_tag") or "").strip()) != pinned:
        issues.append(("fail", f"its image is not {pinned[0]}:{pinned[1]}"))
    credentials = service.get("credentials") or []
    if credentials:
        issues.append(("fail", f"{_plural(len(credentials), 'credential')} attached"))
    if password_issue:
        issues.append(password_issue)
    if not issues:  # Every check above passed.
        return Line(7, "service", title, "pass",
                    "Same release tag as the scanner job, the README image, no credentials, and no _PASSWORD "
                    "variables.")
    return Line(7, "service", title, _worst([r for r, _ in issues]),
                _sentence([text for _, text in issues]), fix)


def _frame_rule(facts: SetupFacts) -> Line:
    title = "Frame rule enforced"
    if facts.report_only:
        return Line(8, "frame_rule", title, "warning",
                    "CIVIS_FRAME_ANCESTORS_REPORT_ONLY is true, so other websites could embed the app.",
                    "Once the app shows correctly inside Civis, set it to false and click Update Deployment "
                    "(README \"Create the Service\", step 10).")
    return Line(8, "frame_rule", title, "pass", "The frame rule is enforced.")


def other_objects_text(count: int | None) -> str:
    if count is None:
        return "other tables or views"
    return f"{count} other table or view" if count == 1 else f"{count} other tables or views"


NOT_DEDICATED_FIX = "Choose a new schema name in the scanner job's RESULTS_SCHEMA parameter."


def schema_dedicated(facts: SetupFacts) -> str:
    """For the test summary: yes, no, or unknown (not checked, or the schema does not exist yet)."""
    storage = facts.storage
    if storage is None or storage.error or not storage.schema_exists or storage.other_objects is None:
        return "unknown"
    return "no" if storage.other_objects else "yes"


def _storage(facts: SetupFacts) -> Line:
    title = "Results storage: a dedicated schema, five tables, and read access"
    storage = facts.storage
    if storage is None:
        return Line(9, "results_storage", title, "review",
                    "Not checked, because the scanner job's RESULTS_DB or RESULTS_SCHEMA cannot be used.",
                    "Fix lines 1 and 4 first.")
    if storage.error:
        return Line(9, "results_storage", title, "fail",
                    f"The check failed at step {storage.step or 'schema'} (error type: {storage.error}).",
                    "Check that the RESULTS_DB credential can connect to the database and read the schema.")
    if not storage.schema_exists:
        return Line(9, "results_storage", title, "fail", "The results schema does not exist.",
                    "Click Set up results storage on the Overview page.")
    if storage.other_objects:
        return Line(9, "results_storage", title, "fail",
                    f"The results schema holds {other_objects_text(storage.other_objects)}, so the tool will not "
                    "use it.", NOT_DEDICATED_FIX)
    missing = [name for name in TABLE_NAMES if name not in (storage.tables or set())]
    if missing:
        return Line(9, "results_storage", title, "warning",
                    f"{len(missing)} of the {len(TABLE_NAMES)} tables do not exist yet.",
                    "Click Set up results storage on the Overview page, or run a scan (it creates them).",
                    (f"Missing tables: {', '.join(missing)}",))
    if not storage.readable:
        return Line(9, "results_storage", title, "fail", "The credential could not read the tables.",
                    "Check that the RESULTS_DB credential's database user can read the schema.")
    return Line(9, "results_storage", title, "pass", "The schema holds only this tool's five tables, and the "
                                                     "credential can read them.")


def _latest(facts: SetupFacts) -> Line:
    title = "Latest scan"
    results = {
        "unknown": ("review", "The newest scan is not known, because the scanner job or its runs could not be read.",
                    "Fix line 1 first."),
        "none": ("review", "No scan has run yet.", "Click Run scan on the Overview page."),
        "queued": ("review", "A scan is queued.", None),
        "running": ("review", "A scan is running.", None),
        "succeeded": ("pass", "The newest scan succeeded.", None),
        "failed": ("fail", "The newest scan failed.", "Read the scanner job's run log in Civis, then run the scan again."),
        "cancelled": ("warning", "The newest scan was cancelled.", "Run a new scan, or resume it (README)."),
        "tables_failed": ("fail", "The newest scan could not save its tables (CSV report only).",
                          "Check line 9, then run the scan again."),
    }
    result, reason, fix = results.get(facts.latest, results["unknown"])
    return Line(10, "latest_scan", title, result, reason, fix)
