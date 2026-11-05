"""Users, so owners can be named and flagged when deactivated.

``load_users`` asks for every user, including deactivated ones
(``account_status="all"``). A scanning user who is not allowed that list gets
HTTP 403 or 404 (the first live test, docs/PLAN.md 17.5), so the scan then lists
active users only. If that also fails, the scan continues with no user lookup.

Owner active status by source:

- ``all``: from each user's ``active`` field. An owner not in the list is unknown.
- ``active``: an owner in the list is active. An owner not in it is unknown
  (None), never False, because the list does not show deactivated users.
- ``none``: every owner is unknown.

Owner names always come first from each object's own owner field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scanner.inventory.base import UserInfo, field_of

USER_PAGE_SIZE = 1000  # The API's maximum is 10000.
FALLBACK_STATUSES = (403, 404)

# Fixed coverage notes (coverage row_kind "users"). Only the HTTP status is added, never error text.
USERS_ACTIVE_NOTE = (
    "Owner active status is only partly known. The scanning user could not list deactivated users{status}, "
    "so owners in the list of active users are marked active and all other owners are unknown."
)
USERS_NONE_NOTE = (
    "Owner active status is not known. The scanning user could not list users{status}, "
    "so every owner's active status is unknown."
)


@dataclass
class UserLookup:
    """The users found, which list they came from, and the HTTP status of the call that failed."""

    users: dict[int, UserInfo] = field(default_factory=dict)
    source: str = "all"  # all, active, or none (scanner/scan_log.py, USER_SOURCES).
    status: int | None = None

    def note(self) -> str | None:
        """The coverage note for this lookup, or None when every user was listed."""
        if self.source == "all":
            return None
        status = f" (HTTP {self.status})" if self.status is not None else ""
        return (USERS_ACTIVE_NOTE if self.source == "active" else USERS_NONE_NOTE).format(status=status)


def _status(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _list(client: Any, account_status: str) -> list:
    # The whole list is read before it is used, so a page that fails partway leaves nothing half read.
    return list(client.users.list(account_status=account_status, iterator=True, limit=USER_PAGE_SIZE))


def load_users(client: Any) -> UserLookup:
    """Every user if allowed, else active users, else none. A failure of the "all" list other than
    HTTP 403 or 404 is raised, as before."""
    try:
        rows = _list(client, "all")
    except Exception as exc:
        status = _status(exc)
        if status not in FALLBACK_STATUSES:
            raise
    else:
        users = {}
        for row in rows:
            user_id = field_of(row, "id")
            if user_id is not None:
                users[user_id] = UserInfo(name=field_of(row, "name") or field_of(row, "user"),
                                          active=field_of(row, "active"))
        return UserLookup(users, "all")

    try:
        rows = _list(client, "active")
    except Exception as exc:  # The scan continues with no user lookup.
        return UserLookup({}, "none", _status(exc))
    users = {}
    for row in rows:
        user_id = field_of(row, "id")
        if user_id is not None:
            users[user_id] = UserInfo(name=field_of(row, "name") or field_of(row, "user"), active=True)
    return UserLookup(users, "active", status)


def scanning_user(client: Any) -> tuple[int | None, str | None]:
    """The user the scan runs as. Uses get_me, which exists from civis 2.8.0 and in v3."""
    me = client.users.get_me()
    return field_of(me, "id"), field_of(me, "username") or field_of(me, "name")
