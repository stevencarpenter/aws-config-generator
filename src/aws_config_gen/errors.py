"""Exceptions shared across discovery modules."""

from __future__ import annotations


class DiscoveryDataError(Exception):
    """Raised when the token cache or an SSO portal response is malformed."""
