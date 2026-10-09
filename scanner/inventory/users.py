"""Users, including deactivated ones, so owners can be named and flagged."""

from __future__ import annotations

from typing import Any

from scanner.inventory.base import UserInfo, field_of

USER_PAGE_SIZE = 1000


def load_users(client: Any) -> dict[int, UserInfo]:
    users = {}
    for row in client.users.list(account_status="all", iterator=True, limit=USER_PAGE_SIZE):
        user_id = field_of(row, "id")
        if user_id is None:
            continue
        users[user_id] = UserInfo(
            name=field_of(row, "name") or field_of(row, "user"),
            active=field_of(row, "active"),
        )
    return users


def scanning_user(client: Any) -> tuple[int | None, str | None]:
    """The user the scan runs as. Uses get_me, which exists from civis 2.8.0 and in v3."""
    me = client.users.get_me()
    return field_of(me, "id"), field_of(me, "username") or field_of(me, "name")
