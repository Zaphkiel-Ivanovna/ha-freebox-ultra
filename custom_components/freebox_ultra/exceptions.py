"""Exceptions raised by the Freebox Ultra API client."""

from __future__ import annotations


class FreeboxError(Exception):
    """Base error: the box answered with success=false."""

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        """Initialize the error, keeping the Freebox error_code around."""
        super().__init__(message)
        self.error_code = error_code


class FreeboxConnectionError(FreeboxError):
    """The box could not be reached (network, TLS, timeout)."""


class FreeboxUnsupportedError(FreeboxError):
    """The box exposes an API version this integration does not support."""


class FreeboxAuthError(FreeboxError):
    """Authentication failed and cannot be recovered by reopening a session."""


class FreeboxInvalidToken(FreeboxAuthError):
    """The app_token was revoked or is invalid: re-authorization is required.

    Maps to `invalid_token`, `apps_denied`, `new_apps_denied`,
    `denied_from_external_ip`. Callers should surface this as
    `ConfigEntryAuthFailed` so Home Assistant starts a reauth flow.
    """


class FreeboxPendingAuth(FreeboxAuthError):
    """The app_token exists but the user has not validated it on the box yet."""


class FreeboxAuthDenied(FreeboxAuthError):
    """The user refused (or let time out) the authorization request."""


class FreeboxInsufficientRights(FreeboxError):
    """The app lacks the permission required by this endpoint.

    Permissions are *not* granted by the pairing flow: they must be toggled by
    hand in Freebox OS (Paramètres > Gestion des accès > Applications).
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        missing_right: str | None = None,
    ) -> None:
        """Initialize the error, keeping the missing permission name around."""
        super().__init__(message, error_code=error_code)
        self.missing_right = missing_right
