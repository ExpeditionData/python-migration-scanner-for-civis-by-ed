"""Code shared by the scanner job and the web app.

Everything in this package must run on Python 3.12 (Service image) and
Python 3.14 (scanner image), and may only call civis methods that exist in
civis 2.7.1. See tests/test_civis_271_compat.py.
"""
