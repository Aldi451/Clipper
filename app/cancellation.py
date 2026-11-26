"""Shared cancellation signal for background download and render tasks."""
from __future__ import annotations


class OperationCancelled(Exception):
    """Raised when a user asks a running operation to stop."""
