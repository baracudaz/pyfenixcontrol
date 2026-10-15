"""Exceptions raised by pyfenixcontrol."""

from __future__ import annotations


class FenixControlError(Exception):
    """Base class for all pyfenixcontrol errors."""


class FenixControlConnectionError(FenixControlError):
    """The cloud could not be reached (network error, timeout, DNS)."""


class FenixControlAuthError(FenixControlError):
    """Authentication failed or the credentials/tokens are no longer valid.

    Callers should treat this as "user action required" (e.g. a reauth flow).
    """


class FenixControlResponseError(FenixControlError):
    """The cloud returned an unexpected status or an unparsable body."""

    def __init__(self, message: str, status: int | None = None) -> None:
        """Initialize with an optional HTTP status code."""
        super().__init__(message)
        self.status = status
