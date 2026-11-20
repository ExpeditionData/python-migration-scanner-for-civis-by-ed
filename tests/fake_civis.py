"""Kept for test imports. The synthetic client lives in scanner/synthetic/."""

from scanner.synthetic.client import FIXTURES, FakeAPIError, FakeClient

__all__ = ["FIXTURES", "FakeAPIError", "FakeClient", "items_by_id"]


def items_by_id(inventory) -> dict:
    """Inventory items by object ID, for tests on the synthetic instance, where no two objects share an ID.

    The scanner itself identifies an object by type and ID (docs/PLAN.md 17.19), so this helper fails if two
    items have the same ID."""
    items = {item.obj.object_id: item for item in inventory.items}
    assert len(items) == len(inventory.items), "two inventory items share an ID; look them up by type and ID"
    return items
