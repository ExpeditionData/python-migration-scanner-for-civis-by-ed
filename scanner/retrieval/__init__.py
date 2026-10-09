"""Code retrieval: inline code, notebook files, GitHub repos, template fallback.

Built in Phase 3. Fetched code stays in memory or in a temporary directory that
is deleted when the run ends. It is never written to run outputs, tables, or logs.
"""
