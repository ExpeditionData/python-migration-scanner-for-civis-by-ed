# Copyright 2026 Expedition Data LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Install script for Python Migration Scanner for Civis Platform by Expedition Data.

Run this file by hand as a Civis Python script, signed in as the Robot user
(README "Install" and "Update to a new release"). It creates the tool's
scanner job and Service (MODE "install"), updates them to a new release
(MODE "update"), or shows what either would do and changes nothing
(MODE "check", the default).

How it is built so it cannot touch anything else in your Civis (SECURITY.md,
"The install script"):

- Its only write calls are scripts.post_containers, scripts.patch_containers,
  services.post, and services.patch. Every other call is a read: the running
  user, the results credential, the database lookup, the GitHub credential's
  type, and the two objects. It sends no SQL, calls nothing in civis.io, reads
  no table, file, run log, or other script, and does not read a credential's
  secret.
- A patch goes only to an object that passed the identity check in the same
  run, read fresh right before the patch. The scanner job: owned by the
  running user, image civisanalytics/datascience-python, and command exactly
  "cd /app && python -m scanner". The Service: owned by the running user,
  image civisanalytics/civis-services-flask, and either its repository is this
  tool's repository or its SCANNER_JOB_ID equals JOB_ID. In install mode, a
  patch goes only to an object the same run just created.
- Every check runs before the first write. A failed check stops the script,
  prints which check failed with a fixed label, and says "Nothing changed."
- It never deletes, archives, or shares anything, and never starts a scanner
  run or a Service deployment. On update it changes only the fields the README
  lists under "What the install script sets", and keeps RESULTS_DB and
  RESULTS_SCHEMA exactly as they are.
- It prints only pass or fail, field names, fixed labels, and error types. The
  IDs of new objects print on one line that starts with "KEEP IN CIVIS:".

It uses only the Python standard library and civis-python. It imports nothing
else from this repository.
"""

# ---------------------------------------------------------------------------
# Settings. Set these, then run this script.
# ---------------------------------------------------------------------------

MODE = "check"  # "check" changes nothing; "install" creates both objects; "update" updates them.
TAG = "<release tag>"  # The release tag this file came from, for example "v0.1.0". Never "main" or a branch.
RESULTS_DATABASE = None  # Install only: the results database's name (in quotes) or its ID.
RESULTS_CREDENTIAL = None  # Install only: the ID of the Robot user's database credential for that database.
RESULTS_SCHEMA = None  # Install only: a new schema used only for this tool, for example "python_migration_results".
GITHUB_CREDENTIAL = None  # None (no token, the normal setup), a Custom credential's ID, or "clear" (update only).
LOOKBACK_DAYS = None  # None leaves it as it is (the default is 400 days), or a whole number from 1 to 3650.
RETENTION_DAYS = None  # None leaves it as it is (the default keeps every scan), a number of days, or "clear" (update).
JOB_ID = None  # Update only (and install with INSTALL_SERVICE_ONLY): the scanner job ID from the KEEP IN CIVIS line.
SERVICE_ID = None  # Update only: the Service ID from the KEEP IN CIVIS line.
INSTALL_SERVICE_ONLY = False  # Install only: True creates only the Service, for the scanner job in JOB_ID.

# ---------------------------------------------------------------------------
# Do not change anything below this line.
# ---------------------------------------------------------------------------

import json  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402

# What the README sets. tests/test_install_script.py checks each against common/release.py.
REPOSITORY_URL = "https://github.com/ExpeditionData/python-migration-scanner-for-civis-by-ed.git"
SCANNER_COMMAND = "cd /app && python -m scanner"
SCANNER_IMAGE = ("civisanalytics/datascience-python", "9.1.1")
SERVICE_IMAGE = ("civisanalytics/civis-services-flask", "0.1.0")
HEALTH_PATH = "/healthz"
DEFAULT_LOOKBACK_DAYS = 400
LOOKBACK_DAYS_RANGE = (1, 3650)
RETENTION_DAYS_RANGE = (0, 36500)
PARAMETER_TYPES = {
    "RESULTS_DB": "database",
    "RESULTS_SCHEMA": "string",
    "GITHUB": "credential_custom",
    "LOOKBACK_DAYS": "integer",
    "MAX_CONCURRENCY": "integer",
    "REQUEST_BUDGET": "integer",
    "RETENTION_DAYS": "integer",
}
REQUIRED_PARAMETERS = ("RESULTS_DB", "RESULTS_SCHEMA")
DEFAULT_THREADS = 4
MEMORY_PER_EXTRA_THREAD_MB = 512
# (name, CPU shares, memory in MB, disk in GB)
PROFILE_WITH_TOKEN = ("with_token", 1000, 4096, 4)
PROFILE_WITHOUT_TOKEN = ("without_token", 500, 2048, 1)
RELEASE_TAG = re.compile(r"^v\d+(\.\d+)*(-rc\d+)?$")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,126}$")

JOB_NAME = "Python Migration Scanner: scanner job"
SERVICE_NAME = "Python Migration Scanner: web app"
RETIRED_PARAMETER = "RUN_ID"
MODES = ("check", "install", "update")
ON_DEMAND = {"runtimePlan": "on_demand", "recurrences": []}
# The keys a parameter definition may carry in a request (civis 2.10 API spec, ScriptParamCreateRequest).
PARAM_KEYS = ("name", "label", "description", "type", "required", "value", "default", "allowedValues")
# Fields Civis changes by itself, never listed as "other fields changed".
VOLATILE = {
    "scanner_job": {"updated_at", "last_run", "state", "finished_at"},
    "service": {"updated_at", "current_deployment", "current_url"},
}
FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ERROR_NAME = re.compile(r"^[A-Z][A-Za-z0-9_]{0,63}$")


# ---------------------------------------------------------------------------
# Output: fixed labels, pass or fail, field names, and error types only.
# ---------------------------------------------------------------------------


def say(line):
    print(line, flush=True)


def error_text(exc):
    """The error's class name and HTTP status. Never its message."""
    name = type(exc).__name__
    if not ERROR_NAME.match(name):
        name = "Error"
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        return f"error={name} status={status}"
    return f"error={name}"


def field_list(names):
    """Field names that look like API field names; anything else counts as "other"."""
    names = sorted(set(names))
    shown = [n for n in names if isinstance(n, str) and FIELD_NAME.match(n)]
    others = len(names) - len(shown)
    return ",".join(shown + ([f"other({others})"] if others else [])) or "none"


class Stop(Exception):
    """A check failed. The line has already been printed."""


# ---------------------------------------------------------------------------
# Reading Civis responses
# ---------------------------------------------------------------------------


def plain(value):
    """A civis response as plain dicts and lists, with snake_case keys (civis keeps the case of the keys inside
    "arguments" and "environment_variables")."""
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    to_json = getattr(value, "json", None)
    if callable(to_json) and not isinstance(value, (str, bytes)):
        return plain(to_json())
    return value


def positive_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number > 0 else None
    return None


def user_id(value):
    return positive_int(value.get("id")) if isinstance(value, dict) else None


def normalize_repo(url):
    text = str(url or "").strip().lower()
    text = re.sub(r"^[a-z]+://", "", text)
    text = re.sub(r"^www\.", "", text)
    text = text.rstrip("/")
    if text.endswith(".git"):
        text = text[:-4]
    return text.rstrip("/")


def is_this_repository(url):
    return bool(url) and normalize_repo(url) == normalize_repo(REPOSITORY_URL)


def params_of(job):
    return [p for p in (job.get("params") or []) if isinstance(p, dict)]


def arguments_of(job):
    arguments = job.get("arguments")
    return dict(arguments) if isinstance(arguments, dict) else {}


def parameter_value(job, name):
    """The job's saved argument, or a fixed value set on the parameter itself."""
    arguments = arguments_of(job)
    if arguments.get(name) not in (None, ""):
        return arguments[name]
    for param in params_of(job):
        if param.get("name") == name and param.get("value") not in (None, ""):
            return param["value"]
    return None


def param_request(param):
    """A parameter definition from a response, in the form a request takes."""
    out = {}
    for key in PARAM_KEYS:
        value = param.get("allowed_values", param.get(key)) if key == "allowedValues" else param.get(key)
        if value is None or value == []:
            continue
        out[key] = value
    return out


def results_db_value(value):
    """(database ID, credential ID, shape label) from a RESULTS_DB value, as app/job_settings.py reads it."""
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            value = json.loads(value)
        except ValueError:
            return None, None, "not_recognized"
        shape = "json_text"
    else:
        shape = "object"
    if not isinstance(value, dict):
        return None, None, "not_recognized"
    database = positive_int(value.get("database"))
    credential = positive_int(value.get("credential"))
    if database is None:
        return None, None, "not_recognized"
    return database, credential, shape if credential is not None else "object_without_credential"


def profile_for(github_attached):
    return PROFILE_WITH_TOKEN if github_attached else PROFILE_WITHOUT_TOKEN


def threads_of(job):
    value = parameter_value(job, "MAX_CONCURRENCY") if job else None
    number = positive_int(value)
    return number if number is not None else DEFAULT_THREADS


def wanted_resources(profile, threads):
    """required_resources as a request takes it (nested keys in camelCase, as Civis expects)."""
    _, cpu, memory, disk = profile
    memory += MEMORY_PER_EXTRA_THREAD_MB * max(0, threads - DEFAULT_THREADS)
    return {"cpu": cpu, "memory": memory, "diskSpace": disk}


def resources_match(current, wanted):
    current = current if isinstance(current, dict) else {}
    pairs = (("cpu", "cpu"), ("memory", "memory"), ("disk_space", "diskSpace"))
    for have_key, want_key in pairs:
        have = current.get(have_key, current.get(want_key))
        if isinstance(have, bool) or not isinstance(have, (int, float)) or float(have) != float(wanted[want_key]):
            return False
    return True


def on_demand(schedule):
    schedule = schedule if isinstance(schedule, dict) else {}
    plan = schedule.get("runtime_plan", schedule.get("runtimePlan"))
    return plan == "on_demand" and not schedule.get("recurrences")


# ---------------------------------------------------------------------------
# The installer
# ---------------------------------------------------------------------------


def current_settings():
    return {
        "MODE": MODE, "TAG": TAG, "RESULTS_DATABASE": RESULTS_DATABASE, "RESULTS_CREDENTIAL": RESULTS_CREDENTIAL,
        "RESULTS_SCHEMA": RESULTS_SCHEMA, "GITHUB_CREDENTIAL": GITHUB_CREDENTIAL, "LOOKBACK_DAYS": LOOKBACK_DAYS,
        "RETENTION_DAYS": RETENTION_DAYS, "JOB_ID": JOB_ID, "SERVICE_ID": SERVICE_ID,
        "INSTALL_SERVICE_ONLY": INSTALL_SERVICE_ONLY,
    }


class Installer:
    def __init__(self, client, settings):
        self.client = client
        self.raw = settings
        self.mode = None
        self.plan = None  # "install", "install_service_only", or "update"
        self.tag = None
        self.github = None  # None, a credential ID, or "clear"
        self.lookback = None
        self.retention = None  # None, a number, or "clear"
        self.job_id = None
        self.service_id = None
        self.me = None
        self.database_id = None
        self.credential_id = None
        self.schema = None
        self.job = None  # The scanner job as read in the checks (update and install_service_only).
        self.service = None  # The Service as read in the checks (update).
        self.created = {}  # Objects this run created: {"scanner_job": id, "service": id}
        self.wrote = False
        self.results_ok = True

    # --- Checks: every one runs before the first write --------------------------------------------------------

    def check(self, label, failure=None):
        if failure:
            say(f"CHECK {label}: fail {failure}")
            raise Stop(label)
        say(f"CHECK {label}: pass")

    def read(self, label, call, *args):
        try:
            return plain(call(*args))
        except Exception as exc:  # The error type only; never the message.
            self.check(label, error_text(exc))

    def check_settings(self):
        s = self.raw
        failure = self._settings_failure(s)
        if failure:
            self.check("settings", f"reason={failure}")
        say(f"CHECK settings: pass mode={self.mode} plan={self.plan}")

    def _settings_failure(self, s):
        mode = s.get("MODE")
        if mode not in MODES:
            return "mode_not_recognized"
        self.mode = mode
        tag = s.get("TAG")
        if not isinstance(tag, str) or not RELEASE_TAG.match(tag.strip()):
            return "tag_not_a_release_tag"
        self.tag = tag.strip()
        service_only = s.get("INSTALL_SERVICE_ONLY")
        if not isinstance(service_only, bool):
            return "install_service_only_not_true_or_false"
        job_id, service_id = s.get("JOB_ID"), s.get("SERVICE_ID")
        if service_only:
            if mode == "update":
                return "install_service_only_is_for_install"
            self.plan = "install_service_only"
        elif mode == "update" or (mode == "check" and (job_id is not None or service_id is not None)):
            self.plan = "update"
        else:
            self.plan = "install"
        results = [s.get(k) for k in ("RESULTS_DATABASE", "RESULTS_CREDENTIAL", "RESULTS_SCHEMA")]
        if self.plan == "install":
            if job_id is not None or service_id is not None:
                return "job_id_and_service_id_are_for_update"
            database = results[0]
            if positive_int(database) is not None:
                self.database_id = positive_int(database)
            elif not (isinstance(database, str) and database.strip() and len(database) <= 255
                      and not re.search(r"[\x00-\x1f\x7f]", database)):
                return "results_database_missing"
            self.credential_id = positive_int(results[1])
            if self.credential_id is None:
                return "results_credential_missing"
            if not isinstance(results[2], str) or not IDENTIFIER.match(results[2].strip()):
                return "results_schema_not_a_plain_name"
            self.schema = results[2].strip()
        else:
            if any(value is not None for value in results):
                return "results_settings_are_for_install"
            self.job_id = positive_int(job_id)
            if self.job_id is None:
                return "job_id_missing"
            if self.plan == "update":
                self.service_id = positive_int(service_id)
                if self.service_id is None:
                    return "service_id_missing"
            elif service_id is not None:
                return "service_id_is_for_update"
        github, lookback, retention = s.get("GITHUB_CREDENTIAL"), s.get("LOOKBACK_DAYS"), s.get("RETENTION_DAYS")
        if self.plan == "install_service_only":
            if any(value is not None for value in (github, lookback, retention)):
                return "job_settings_not_used_with_install_service_only"
            return None
        if github == "clear" or retention == "clear":
            if self.plan != "update":
                return "clear_is_for_update"
        if github is not None and github != "clear":
            github = positive_int(github)
            if github is None:
                return "github_credential_not_an_id"
        self.github = github
        if lookback is not None:
            if isinstance(lookback, bool) or not isinstance(lookback, int):
                return "lookback_days_not_a_whole_number"
            if not LOOKBACK_DAYS_RANGE[0] <= lookback <= LOOKBACK_DAYS_RANGE[1]:
                return "lookback_days_out_of_range"
        self.lookback = lookback
        if retention is not None and retention != "clear":
            if isinstance(retention, bool) or not isinstance(retention, int):
                return "retention_days_not_a_whole_number"
            if not RETENTION_DAYS_RANGE[0] <= retention <= RETENTION_DAYS_RANGE[1]:
                return "retention_days_out_of_range"
        self.retention = retention
        return None

    def check_running_user(self):
        users = self.client.users
        read_me = getattr(users, "get_me", None) or users.list_me  # get_me in civis 2.8 and later; list_me before.
        me = self.read("running_user", read_me)
        self.me = user_id(me)
        self.check("running_user", None if self.me is not None else "reason=no_user_id")

    def check_job_identity(self, label="scanner_job"):
        job = self.read(label, self.client.scripts.get_containers, self.job_id)
        mismatches = job_mismatches(job, self.me)
        self.check(label, f"reason={','.join(mismatches)}" if mismatches else None)
        return job

    def check_service_identity(self, label="service"):
        service = self.read(label, self.client.services.get, self.service_id)
        mismatches = service_mismatches(service, self.me, self.job_id)
        self.check(label, f"reason={','.join(mismatches)}" if mismatches else None)
        return service

    def check_results(self):
        if self.plan != "install":
            database, credential, shape = results_db_value(parameter_value(self.job, "RESULTS_DB"))
            if database is None:
                self.check("results_db_value", "reason=not_recognized")
            say(f"CHECK results_db_value: pass shape={shape}")
            self.database_id, self.credential_id = database, credential
            schema = parameter_value(self.job, "RESULTS_SCHEMA")
            self.schema = schema.strip() if isinstance(schema, str) else None
        if self.credential_id is None:
            self.check("results_credential", "reason=no_credential_in_results_db")
        credential = self.read("results_credential", self.client.credentials.get, self.credential_id)
        kind = str((credential or {}).get("type") or "").strip().lower()
        self.check("results_credential", None if kind == "database" else "reason=not_a_database_credential")
        if self.database_id is not None:
            database = self.read("results_database", self.client.databases.get, self.database_id)
            found = positive_int((database or {}).get("id"))
            self.check("results_database", None if found is not None else "reason=not_found")
            self.database_id = found
        else:
            listed = self.read("results_database", self.client.databases.list) or []
            wanted = self.raw["RESULTS_DATABASE"].strip()
            rows = [d for d in listed if isinstance(d, dict)]
            matches = [d for d in rows if str(d.get("name") or "") == wanted]
            if not matches:
                matches = [d for d in rows if str(d.get("name") or "").lower() == wanted.lower()]
            if len(matches) != 1:
                self.check("results_database", "reason=" + ("not_found" if not matches else "several_match"))
            self.database_id = positive_int(matches[0].get("id"))
            self.check("results_database", None if self.database_id is not None else "reason=not_found")
        ok = isinstance(self.schema, str) and bool(IDENTIFIER.match(self.schema))
        self.check("results_schema", None if ok else "reason=not_a_plain_name")

    def check_github(self):
        if self.plan == "install_service_only":
            return
        if self.github is None:
            say("CHECK github_credential: pass (none set)")
            return
        if self.github == "clear":
            say("CHECK github_credential: pass (clear)")
            return
        credential = self.read("github_credential", self.client.credentials.get, self.github)
        kind = str((credential or {}).get("type") or "").strip().lower()
        self.check("github_credential", None if kind == "custom" else "reason=not_custom")

    def run_checks(self):
        self.check_settings()
        self.check_running_user()
        if self.plan != "install":
            self.job = self.check_job_identity()
        if self.plan == "install_service_only":
            same = str(self.job.get("repo_ref") or "").strip() == self.tag
            self.check("scanner_job_tag", None if same else "reason=differs_from_tag")
        self.check_results()
        self.check_github()
        if self.plan == "update":
            self.service = self.check_service_identity()

    # --- What would change ---------------------------------------------------------------------------------

    def github_attached(self, job):
        if isinstance(self.github, int):
            return True
        if self.github == "clear" or job is None:
            return False
        return parameter_value(job, "GITHUB") is not None

    def install_params(self):
        params = [{"name": name, "type": PARAMETER_TYPES[name], "required": True} for name in REQUIRED_PARAMETERS]
        arguments = {"RESULTS_DB": {"database": self.database_id, "credential": self.credential_id},
                     "RESULTS_SCHEMA": self.schema}
        for name, value in (("GITHUB", self.github), ("LOOKBACK_DAYS", self.lookback),
                            ("RETENTION_DAYS", self.retention)):
            if value is not None:
                params.append({"name": name, "type": PARAMETER_TYPES[name], "required": False})
                arguments[name] = value
        return params, arguments

    def job_post_fields(self):
        profile = profile_for(self.github_attached(None))
        return {
            "name": JOB_NAME, "required_resources": wanted_resources(profile, DEFAULT_THREADS),
            "docker_image_name": SCANNER_IMAGE[0], "docker_image_tag": SCANNER_IMAGE[1],
            "docker_command": SCANNER_COMMAND,
        }

    def job_install_patch(self):
        params, arguments = self.install_params()
        return {"repo_http_uri": REPOSITORY_URL, "repo_ref": self.tag, "params": params, "arguments": arguments}

    def job_update_patch(self, job):
        """The fields to change on the scanner job, and whether RUN_ID was there."""
        patch = {}
        if not is_this_repository(job.get("repo_http_uri")):
            patch["repo_http_uri"] = REPOSITORY_URL
        if str(job.get("repo_ref") or "").strip() != self.tag:
            patch["repo_ref"] = self.tag
        if str(job.get("docker_image_tag") or "").strip() != SCANNER_IMAGE[1]:
            patch["docker_image_tag"] = SCANNER_IMAGE[1]
        resources = wanted_resources(profile_for(self.github_attached(job)), threads_of(job))
        if not resources_match(job.get("required_resources"), resources):
            patch["required_resources"] = resources
        current = [param_request(p) for p in params_of(job)]
        arguments = arguments_of(job)
        run_id_found = any(p.get("name") == RETIRED_PARAMETER for p in current) or RETIRED_PARAMETER in arguments
        params = [p for p in current if p.get("name") != RETIRED_PARAMETER]
        new_arguments = {k: v for k, v in arguments.items() if k != RETIRED_PARAMETER}
        for name, value in (("GITHUB", self.github), ("LOOKBACK_DAYS", self.lookback),
                            ("RETENTION_DAYS", self.retention)):
            if value is None:
                continue
            if value == "clear":
                params = [p for p in params if p.get("name") != name]
                new_arguments.pop(name, None)
                continue
            if not any(p.get("name") == name for p in params):
                params.append({"name": name, "type": PARAMETER_TYPES[name], "required": False})
            new_arguments[name] = value
        if params != current or new_arguments != arguments:
            patch["params"] = params
            patch["arguments"] = new_arguments
        return patch, run_id_found

    def service_post_fields(self):
        return {"name": SERVICE_NAME, "docker_image_name": SERVICE_IMAGE[0], "docker_image_tag": SERVICE_IMAGE[1]}

    def service_install_patch(self, job_id):
        return {
            "git_repo_url": REPOSITORY_URL, "git_repo_ref": self.tag,
            "environment_variables": {"SCANNER_JOB_ID": str(job_id), "CIVIS_SERVICE_HEALTH_PATH": HEALTH_PATH,
                                      "CIVIS_FRAME_ANCESTORS_REPORT_ONLY": "false"},
            "schedule": dict(ON_DEMAND, recurrences=[]), "credentials": [],
        }

    def service_update_patch(self, service):
        patch = {}
        if not is_this_repository(service.get("git_repo_url")):
            patch["git_repo_url"] = REPOSITORY_URL
        if str(service.get("git_repo_ref") or "").strip() != self.tag:
            patch["git_repo_ref"] = self.tag
        if str(service.get("docker_image_tag") or "").strip() != SERVICE_IMAGE[1]:
            patch["docker_image_tag"] = SERVICE_IMAGE[1]
        current = service.get("environment_variables")
        current = dict(current) if isinstance(current, dict) else {}
        wanted = dict(current, SCANNER_JOB_ID=str(self.job_id), CIVIS_SERVICE_HEALTH_PATH=HEALTH_PATH)
        if wanted != current:
            patch["environment_variables"] = wanted
        if not on_demand(service.get("schedule")):
            patch["schedule"] = dict(ON_DEMAND, recurrences=[])
        if service.get("credentials"):
            patch["credentials"] = []
        return patch

    def print_plan(self):
        if self.plan == "install":
            profile = profile_for(self.github_attached(None))
            say(f"PLAN scanner_job: create post={field_list(self.job_post_fields())} "
                f"patch={field_list(self.job_install_patch())} profile={profile[0]}")
            say(f"PLAN scanner_job params: {','.join(p['name'] for p in self.install_params()[0])}")
        if self.plan in ("install", "install_service_only"):
            say(f"PLAN service: create post={field_list(self.service_post_fields())} "
                f"patch={field_list(self.service_install_patch(0))}")
            return
        patch, run_id_found = self.job_update_patch(self.job)
        profile = profile_for(self.github_attached(self.job))
        say(f"PLAN scanner_job: update fields={field_list(patch)} profile={profile[0]} "
            f"run_id={'remove' if run_id_found else 'no_parameter'}")
        say(f"PLAN service: update fields={field_list(self.service_update_patch(self.service))}")

    # --- Writes --------------------------------------------------------------------------------------------

    # Each write is a direct call to one of the four write methods, so it is easy to find and check.

    def install(self):
        if self.plan == "install":
            job = self.create_job()
            job_id = job["id"]
        else:
            job_id = self.job_id
        self.create_service(job_id)

    def create_job(self):
        fields = self.job_post_fields()
        try:
            self.wrote = True  # Set before each write: a call that fails may still have changed something.
            created = plain(self.client.scripts.post_containers(**fields))
        except Exception as exc:
            say(f"WRITE scanner_job create: fail {error_text(exc)}")
            say("RECOVER: Civis did not confirm a new scanner job. If Civis lists a new job named "
                f"\"{JOB_NAME}\", archive it, then run MODE \"install\" again.")
            raise Stop("scanner_job_create")
        job_id = positive_int((created or {}).get("id"))
        if job_id is None:
            say("WRITE scanner_job create: fail reason=no_id_returned")
            say(f"RECOVER: Archive any new job named \"{JOB_NAME}\" in Civis, then run MODE \"install\" again.")
            raise Stop("scanner_job_create")
        self.created["scanner_job"] = job_id
        say("WRITE scanner_job: created")
        patch = self.job_install_patch()
        try:
            self.client.scripts.patch_containers(job_id, **patch)
        except Exception as exc:
            say(f"WRITE scanner_job patch: fail {error_text(exc)}")
            self.keep_line()
            say("RECOVER: The scanner job was created, but its repository and parameters were not set. Archive "
                "it in Civis (its ID is on the KEEP IN CIVIS line), then run MODE \"install\" again.")
            raise Stop("scanner_job_patch")
        say(f"WRITE scanner_job: patched fields={field_list(patch)}")
        after = self.read_back("scanner_job", self.client.scripts.get_containers, job_id)
        self.verify_job(created, after, patch, resources=fields["required_resources"])
        return {"id": job_id}

    def create_service(self, job_id):
        fields = self.service_post_fields()
        try:
            self.wrote = True
            created = plain(self.client.services.post(**fields))
        except Exception as exc:
            say(f"WRITE service create: fail {error_text(exc)}")
            self.service_recovery(job_id, created_service=False)
            raise Stop("service_create")
        service_id = positive_int((created or {}).get("id"))
        if service_id is None:
            say("WRITE service create: fail reason=no_id_returned")
            self.service_recovery(job_id, created_service=False)
            raise Stop("service_create")
        self.created["service"] = service_id
        say("WRITE service: created")
        patch = self.service_install_patch(job_id)
        try:
            self.client.services.patch(service_id, **patch)
        except Exception as exc:
            say(f"WRITE service patch: fail {error_text(exc)}")
            self.service_recovery(job_id, created_service=True)
            raise Stop("service_patch")
        say(f"WRITE service: patched fields={field_list(patch)}")
        after = self.read_back("service", self.client.services.get, service_id)
        self.verify_service(created, after, patch, job_id)

    def service_recovery(self, job_id, created_service):
        self.keep_line()
        if created_service:
            say("RECOVER: The Service was created, but its repository, variables, and schedule were not set. "
                "Archive it in Civis (its ID is on the KEEP IN CIVIS line).")
        else:
            say("RECOVER: Civis did not confirm a new Service. If Civis lists a new Service named "
                f"\"{SERVICE_NAME}\", archive it.")
        say("RECOVER: To create only the Service, set JOB_ID to the scanner job's ID, set "
            "INSTALL_SERVICE_ONLY = True, and run MODE \"install\" again.")
        say("RECOVER: For later changes to the scanner job, run MODE \"update\" with JOB_ID and SERVICE_ID.")

    def update(self):
        # The scanner job: read fresh and checked right before its patch.
        job = self.check_job_identity("scanner_job_before_update")
        patch, run_id_found = self.job_update_patch(job)
        if patch:
            try:
                self.wrote = True
                self.client.scripts.patch_containers(self.job_id, **patch)
            except Exception as exc:
                say(f"WRITE scanner_job patch: fail {error_text(exc)}")
                say("RECOVER: Run MODE \"check\" with the same JOB_ID and SERVICE_ID to see what is left, then "
                    "MODE \"update\" again.")
                raise Stop("scanner_job_patch")
            say(f"WRITE scanner_job: patched fields={field_list(patch)}")
        else:
            say("WRITE scanner_job: no change")
        after = self.read_back("scanner_job", self.client.scripts.get_containers, self.job_id)
        self.verify_job(job, after, patch, resources=wanted_resources(
            profile_for(self.github_attached(job)), threads_of(job)), before_update=job, run_id_found=run_id_found)
        # The Service: read fresh and checked right before its patch.
        try:
            service = self.check_service_identity("service_before_update")
        except Stop:
            say("RECOVER: The scanner job was updated; the Service was not. Fix the Service, then run MODE "
                "\"update\" again.")
            raise
        patch = self.service_update_patch(service)
        if patch:
            try:
                self.wrote = True
                self.client.services.patch(self.service_id, **patch)
            except Exception as exc:
                say(f"WRITE service patch: fail {error_text(exc)}")
                say("RECOVER: The scanner job was updated; the Service was not. Run MODE \"update\" again.")
                raise Stop("service_patch")
            say(f"WRITE service: patched fields={field_list(patch)}")
        else:
            say("WRITE service: no change")
        after = self.read_back("service", self.client.services.get, self.service_id)
        self.verify_service(service, after, patch, self.job_id)

    # --- Read back and report ------------------------------------------------------------------------------

    def read_back(self, label, call, object_id):
        try:
            return plain(call(object_id))
        except Exception as exc:
            say(f"RESULT {label} read_back: fail {error_text(exc)}")
            self.results_ok = False
            return None

    def result(self, label, name, ok, extra=""):
        if not ok:
            self.results_ok = False
        say(f"RESULT {label} {name}: {'pass' if ok else 'fail'}{(' ' + extra) if extra else ''}")

    def other_fields(self, label, before, after, patch):
        if not isinstance(before, dict) or not isinstance(after, dict):
            return
        skip = set(patch) | VOLATILE[label] | {"id"}
        changed = [k for k in set(before) | set(after)
                   if k not in skip and k in before and k in after and before.get(k) != after.get(k)]
        say(f"RESULT {label} other_fields_changed: {field_list(changed)}")

    def verify_job(self, before, after, patch, resources, before_update=None, run_id_found=False):
        label = "scanner_job"
        if not isinstance(after, dict):
            return
        self.result(label, "repository_set", is_this_repository(after.get("repo_http_uri")))
        self.result(label, "tag_set", str(after.get("repo_ref") or "").strip() == self.tag)
        self.result(label, "image_set", (str(after.get("docker_image_name") or "").strip(),
                                         str(after.get("docker_image_tag") or "").strip()) == SCANNER_IMAGE)
        profile = profile_for(self.github_attached(before_update))
        self.result(label, "resources_set", resources_match(after.get("required_resources"), resources),
                    f"profile={profile[0]}")
        by_name = {p.get("name"): p for p in params_of(after)}
        ok = all(name in by_name and str(by_name[name].get("type") or "").lower() == PARAMETER_TYPES[name]
                 for name in REQUIRED_PARAMETERS)
        ok = ok and RETIRED_PARAMETER not in by_name and RETIRED_PARAMETER not in arguments_of(after)
        for name, value in (("GITHUB", self.github), ("LOOKBACK_DAYS", self.lookback),
                            ("RETENTION_DAYS", self.retention)):
            if value == "clear":
                ok = ok and name not in by_name
            elif value is not None:
                ok = ok and name in by_name and arguments_of(after).get(name) == value
        known = [n for n in by_name if n in PARAMETER_TYPES]
        others = len(by_name) - len(known)
        names = ",".join(n for n in PARAMETER_TYPES if n in known) + (f",other({others})" if others else "")
        self.result(label, "params", ok, f"names={names}")
        shape = results_db_value(parameter_value(after, "RESULTS_DB"))[2]
        say(f"RESULT {label} results_db_shape: {shape}")
        if before_update is not None:
            kept = all(parameter_value(after, name) == parameter_value(before_update, name)
                       for name in REQUIRED_PARAMETERS)
            for name in REQUIRED_PARAMETERS:
                old = next((param_request(p) for p in params_of(before_update) if p.get("name") == name), None)
                new = next((param_request(p) for p in params_of(after) if p.get("name") == name), None)
                kept = kept and old == new
            self.result(label, "results_kept", kept)
            say(f"RESULT {label} run_id_removed: {'yes' if run_id_found else 'no_parameter'}")
        self.other_fields(label, before, after, patch)

    def verify_service(self, before, after, patch, job_id):
        label = "service"
        if not isinstance(after, dict):
            return
        self.result(label, "repository_set", is_this_repository(after.get("git_repo_url")))
        self.result(label, "tag_set", str(after.get("git_repo_ref") or "").strip() == self.tag)
        self.result(label, "image_set", (str(after.get("docker_image_name") or "").strip(),
                                         str(after.get("docker_image_tag") or "").strip()) == SERVICE_IMAGE)
        env = after.get("environment_variables")
        env = env if isinstance(env, dict) else {}
        self.result(label, "env_set", str(env.get("SCANNER_JOB_ID") or "").strip() == str(job_id)
                    and env.get("CIVIS_SERVICE_HEALTH_PATH") == HEALTH_PATH)
        self.result(label, "on_demand", on_demand(after.get("schedule")))
        self.result(label, "credentials_none", not after.get("credentials"))
        self.other_fields(label, before, after, patch)

    def keep_line(self):
        if not self.created:
            return
        parts = []
        if "scanner_job" in self.created:
            parts.append(f"scanner job ID {self.created['scanner_job']}")
        if "service" in self.created:
            parts.append(f"Service ID {self.created['service']}")
        say(f"KEEP IN CIVIS: {', '.join(parts)}. You need these IDs for every later update. Keep them in Civis, "
            "and do not send them to anyone who supports your setup.")

    def next_steps(self):
        if self.plan == "update":
            steps = [
                "Check that the scanner job and the Service are shared with Civis admins only, and remove the "
                "default group if Civis added one.",
                "On the Service's page in Civis, click Update Deployment.",
                "Open the app's Setup page and fix any line that is not a pass.",
                "When you are done, archive this script's copy in Civis.",
            ]
        else:
            steps = [
                "Share the scanner job and the Service with Civis admins only, and remove the default group if "
                "Civis added one.",
                "On the Service's page in Civis, click Start Deployment.",
                "Open the app and click Set up results storage.",
                "Open the app's Setup page and fix any line that is not a pass.",
                "When you are done, archive this script's copy in Civis.",
            ]
        for number, step in enumerate(steps, start=1):
            say(f"NEXT {number}: {step}")

    def run(self):
        try:
            self.run_checks()
        except Stop:
            say("Nothing changed.")
            return 1
        try:
            self.print_plan()
        except Exception as exc:
            say(f"CHECK plan: fail {error_text(exc)}")
            say("Nothing changed.")
            return 1
        if self.mode == "check":
            say("Nothing changed. This was MODE \"check\". To make these changes, set MODE to \"install\" or "
                "\"update\" and run this script again.")
            return 0
        try:
            if self.plan == "update":
                self.update()
            else:
                self.install()
        except Stop:
            if not self.wrote:
                say("Nothing changed.")
            return 1
        except Exception as exc:  # Anything unexpected: the error type only, and what this run created.
            say(f"STOPPED: {error_text(exc)}")
            if self.wrote:
                self.keep_line()
                say("RECOVER: Run MODE \"check\" to see what is left before you run the script again.")
            else:
                say("Nothing changed.")
            return 1
        self.keep_line()
        if not self.results_ok:
            say("RESULT: some settings did not take. Run MODE \"check\" with JOB_ID and SERVICE_ID, then MODE "
                "\"update\".")
            return 1
        self.next_steps()
        return 0


def job_mismatches(job, me):
    """Why a container script is not this tool's scanner job owned by the running user: owner, image, command."""
    if not isinstance(job, dict):
        return ("not_read",)
    mismatches = []
    if me is None or user_id(job.get("author")) != me:
        mismatches.append("owner")
    if str(job.get("docker_image_name") or "").strip() != SCANNER_IMAGE[0]:
        mismatches.append("image")
    if str(job.get("docker_command") or "").strip() != SCANNER_COMMAND:
        mismatches.append("command")
    return tuple(mismatches)


def service_mismatches(service, me, job_id):
    """Why a Service is not this tool's Service owned by the running user: owner, image, repository_and_job_id."""
    if not isinstance(service, dict):
        return ("not_read",)
    mismatches = []
    if me is None or user_id(service.get("user")) != me:
        mismatches.append("owner")
    if str(service.get("docker_image_name") or "").strip() != SERVICE_IMAGE[0]:
        mismatches.append("image")
    env = service.get("environment_variables")
    env = env if isinstance(env, dict) else {}
    same_job = job_id is not None and str(env.get("SCANNER_JOB_ID") or "").strip() == str(job_id)
    if not is_this_repository(service.get("git_repo_url")) and not same_job:
        mismatches.append("repository_and_job_id")
    return tuple(mismatches)


def main(client, settings=None):
    """Run the install script with a civis APIClient (or a test's fake client). Returns 0 or 1."""
    say("Python Migration Scanner install script")
    try:
        return Installer(client, current_settings() if settings is None else settings).run()
    except Exception as exc:  # Anything unexpected: the error type only.
        say(f"STOPPED: {error_text(exc)}")
        return 1


if __name__ == "__main__":
    import civis

    sys.exit(main(civis.APIClient()))
