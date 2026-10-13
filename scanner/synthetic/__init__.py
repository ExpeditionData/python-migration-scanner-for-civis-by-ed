"""Synthetic Civis and GitHub stand-ins for unit tests and dry runs.

Everything here is made up. Nothing in this package calls the network, and
none of its data comes from a Civis instance.
"""

from scanner.synthetic.client import FIXTURES, FakeAPIError, FakeClient
from scanner.synthetic.http import SyntheticSession

__all__ = ["FIXTURES", "FakeAPIError", "FakeClient", "SyntheticSession"]
