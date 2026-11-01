"""Factotum: a self-hosted task triage service.

Principles (see SPEC.md §2): SQLite is the source of truth; Claude decides, Python writes;
Claude proposes, the tray applies; every task has a close signal or an expiry.
"""

__version__ = "0.1.0"
