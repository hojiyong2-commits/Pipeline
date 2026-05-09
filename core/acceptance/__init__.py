"""Acceptance harness v2.

Agents may propose tests, but this package executes and scores them.
"""

from .runner import run_acceptance

__all__ = ["run_acceptance"]
