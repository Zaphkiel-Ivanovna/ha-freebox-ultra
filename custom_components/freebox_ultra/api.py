"""Async client for the Freebox OS API: transport, TLS, authentication.

Everything Freebox-specific and Home-Assistant-agnostic lives here, so the
platforms only ever deal with plain dicts.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
import hmac
import logging
from pathlib import Path
import ssl
from typing import Any, Self

from aiohttp import ClientError, ClientSession, ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_MAJOR_MAX,
    API_MAJOR_MIN,
    APP_ID,
    APP_NAME,
    APP_VERSION,
    AUTH_POLL_INTERVAL,
    AUTH_TIMEOUT,
    CERT_FILENAME,
    DISCOVERY_URL,
    HTTP_TIMEOUT,
)
from .exceptions import (
    FreeboxAuthDenied,
    FreeboxAuthError,
    FreeboxConnectionError,
    FreeboxError,
    FreeboxInsufficientRights,
    FreeboxInvalidToken,
    FreeboxPendingAuth,
)

_LOGGER = logging.getLogger(__name__)

_CERT_PATH = Path(__file__).parent / CERT_FILENAME

_STALE_SESSION_CODES = frozenset({"auth_required", "invalid_session"})
_DEAD_TOKEN_CODES = frozenset(
    {"invalid_token", "apps_denied", "new_apps_denied", "denied_from_external_ip"}
)


@cache
def _build_ssl_context() -> ssl.SSLContext:
    """Build an SSL context trusting the Freebox and Iliadbox root CAs.

    Blocking (reads the PEM from disk and loads the system store), so it must
    run in an executor. Cached: one context is enough for the whole process.

    Clearing `VERIFY_X509_STRICT` is required from Python 3.13 on, where the
    strict checks it enables reject the Freebox gateway certificate.
    """
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(_CERT_PATH))
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


async def async_get_ssl_context(hass: HomeAssistant) -> ssl.SSLContext:
    """Return the shared Freebox SSL context."""
    return await hass.async_add_executor_job(_build_ssl_context)


@dataclass(frozen=True, slots=True, kw_only=True)
class BoxDescriptor:
    """Payload of `GET /api_version`, also carried in the mDNS TXT record."""

    api_domain: str
    uid: str
    https_port: int
    https_available: bool
    api_base_url: str
    api_version: str
    device_name: str
    device_type: str
    box_model: str | None = None
    box_model_name: str | None = None

    @property
    def box_api_major(self) -> int:
        """Major API version advertised by the box."""
        return int(self.api_version.split(".", 1)[0])

    @property
    def api_major(self) -> int:
        """Major API version we address, clamped to what we support."""
        return min(self.box_api_major, API_MAJOR_MAX)

    @property
    def is_supported(self) -> bool:
        """Whether the box is recent enough for this integration."""
        return self.box_api_major >= API_MAJOR_MIN

    @classmethod
    def from_api(cls, payload: Mapping[str, Any]) -> Self:
        """Build from a `GET /api_version` JSON payload."""
        return cls(
            api_domain=payload["api_domain"],
            uid=payload["uid"],
            https_port=int(payload["https_port"]),
            https_available=bool(payload["https_available"]),
            api_base_url=payload["api_base_url"],
            api_version=payload["api_version"],
            device_name=payload.get("device_name", "Freebox Server"),
            device_type=payload.get("device_type", ""),
            box_model=payload.get("box_model"),
            box_model_name=payload.get("box_model_name"),
        )

    @classmethod
    def from_zeroconf(cls, properties: Mapping[str, Any]) -> Self:
        """Build from mDNS TXT properties, where every value is a string."""
        return cls(
            api_domain=properties["api_domain"],
            uid=properties["uid"],
            https_port=int(properties["https_port"]),
            https_available=str(properties["https_available"]).lower()
            in ("1", "true", "yes"),
            api_base_url=properties["api_base_url"],
            api_version=properties["api_version"],
            device_name=properties.get("device_name", "Freebox Server"),
            device_type=properties.get("device_type", ""),
            box_model=properties.get("box_model"),
            box_model_name=properties.get("box_model_name"),
        )

    def as_entry_data(self) -> dict[str, Any]:
        """Serialize the parts worth persisting in the config entry."""
        return {
            "api_domain": self.api_domain,
            "uid": self.uid,
            "https_port": self.https_port,
            "https_available": self.https_available,
            "api_base_url": self.api_base_url,
            "api_version": self.api_version,
            "device_name": self.device_name,
            "device_type": self.device_type,
            "box_model": self.box_model,
            "box_model_name": self.box_model_name,
        }


async def async_discover(hass: HomeAssistant) -> BoxDescriptor:
    """Discover the box over plain HTTP on `mafreebox.freebox.fr`.

    Only usable from the LAN, and only for this single endpoint.
    """
    session = async_get_clientsession(hass)
    try:
        response = await session.get(
            DISCOVERY_URL, timeout=ClientTimeout(total=HTTP_TIMEOUT)
        )
        payload = await response.json(content_type=None)
    except (ClientError, TimeoutError, ValueError) as err:
        raise FreeboxConnectionError(f"Discovery failed: {err}") from err
    return BoxDescriptor.from_api(payload)


async def async_probe(hass: HomeAssistant, host: str, port: int) -> BoxDescriptor:
    """Fetch `/api_version` over TLS on an explicit host:port."""
    session = async_get_clientsession(hass)
    context = await async_get_ssl_context(hass)
    try:
        response = await session.get(
            f"https://{host}:{port}/api_version",
            ssl=context,
            timeout=ClientTimeout(total=HTTP_TIMEOUT),
        )
        payload = await response.json(content_type=None)
    except (ClientError, TimeoutError, ValueError) as err:
        raise FreeboxConnectionError(f"Cannot reach {host}:{port}: {err}") from err
    return BoxDescriptor.from_api(payload)


class FreeboxClient:
    """Authenticated Freebox OS API client.

    Holds the long-lived `app_token` and the short-lived `session_token`, and
    transparently reopens a session when the box answers `auth_required`.
    """

    def __init__(
        self,
        session: ClientSession,
        ssl_context: ssl.SSLContext,
        descriptor: BoxDescriptor,
        *,
        host: str | None = None,
        port: int | None = None,
        app_token: str | None = None,
        timeout: int = HTTP_TIMEOUT,
    ) -> None:
        """Initialize the client. `host` overrides the advertised api_domain."""
        self._session = session
        self._ssl = ssl_context
        self.descriptor = descriptor
        self.host = host or descriptor.api_domain
        self.port = port or descriptor.https_port
        self.app_token = app_token
        self._timeout = ClientTimeout(total=timeout)
        self._session_token: str | None = None
        self._permissions: dict[str, bool] = {}
        self._session_lock = asyncio.Lock()

    @classmethod
    async def async_create(
        cls,
        hass: HomeAssistant,
        descriptor: BoxDescriptor,
        *,
        host: str | None = None,
        port: int | None = None,
        app_token: str | None = None,
    ) -> Self:
        """Build a client reusing Home Assistant's shared aiohttp session."""
        return cls(
            async_get_clientsession(hass),
            await async_get_ssl_context(hass),
            descriptor,
            host=host,
            port=port,
            app_token=app_token,
        )

    @property
    def base_url(self) -> str:
        """Root of every versioned API call."""
        return (
            f"https://{self.host}:{self.port}"
            f"{self.descriptor.api_base_url}v{self.descriptor.api_major}/"
        )

    @property
    def permissions(self) -> dict[str, bool]:
        """Permissions the box granted when the session was opened."""
        return dict(self._permissions)


    async def async_request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: Mapping[str, Any] | None = None,
        authenticated: bool = True,
        _retried: bool = False,
    ) -> Any:
        """Perform one API call and unwrap the `{success, result}` envelope."""
        if authenticated and self._session_token is None:
            await self.async_open_session()

        headers: dict[str, str] = {}
        if authenticated and self._session_token:
            headers["X-Fbx-App-Auth"] = self._session_token

        url = f"{self.base_url}{path.lstrip('/')}"
        try:
            response = await self._session.request(
                method,
                url,
                json=json,
                params=params,
                headers=headers,
                ssl=self._ssl,
                timeout=self._timeout,
            )
            payload = await response.json(content_type=None)
        except (ClientError, TimeoutError) as err:
            raise FreeboxConnectionError(f"{method} {path} failed: {err}") from err
        except ValueError as err:
            raise FreeboxError(f"{method} {path} returned invalid JSON: {err}") from err

        if not isinstance(payload, dict):
            raise FreeboxError(f"{method} {path} returned an unexpected payload")
        if payload.get("success"):
            return payload.get("result")

        code = payload.get("error_code")
        message = payload.get("msg") or code or "unknown error"

        if code in _STALE_SESSION_CODES and authenticated and not _retried:
            _LOGGER.debug("Session expired on %s, reopening", path)
            self._session_token = None
            return await self.async_request(
                method,
                path,
                json=json,
                params=params,
                authenticated=authenticated,
                _retried=True,
            )
        if code in _DEAD_TOKEN_CODES:
            raise FreeboxInvalidToken(message, error_code=code)
        if code == "pending_token":
            raise FreeboxPendingAuth(message, error_code=code)
        if code == "insufficient_rights":
            raise FreeboxInsufficientRights(
                message, error_code=code, missing_right=payload.get("missing_right")
            )
        raise FreeboxError(f"{method} {path}: {message}", error_code=code)


    async def async_request_app_token(self, device_name: str) -> tuple[str, int]:
        """Ask the box for an app_token. Returns `(app_token, track_id)`.

        The token is useless until the user validates it on the box's screen.
        """
        result = await self.async_request(
            "POST",
            "login/authorize/",
            json={
                "app_id": APP_ID,
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
                "device_name": device_name,
            },
            authenticated=False,
        )
        return result["app_token"], int(result["track_id"])

    async def async_get_authorization_status(self, track_id: int) -> str:
        """Return `unknown`, `pending`, `timeout`, `granted` or `denied`."""
        result = await self.async_request(
            "GET", f"login/authorize/{track_id}", authenticated=False
        )
        return str(result["status"])

    async def async_wait_for_authorization(self, track_id: int) -> None:
        """Poll until the user validates the pairing on the box's screen.

        Raises `FreeboxAuthDenied` if the user refuses or lets the prompt time
        out, `FreeboxInvalidToken` if the box forgot the request altogether.
        """
        try:
            async with asyncio.timeout(AUTH_TIMEOUT):
                while True:
                    status = await self.async_get_authorization_status(track_id)
                    if status == "granted":
                        return
                    if status == "denied":
                        raise FreeboxAuthDenied("Authorization refused on the box")
                    if status == "timeout":
                        raise FreeboxAuthDenied("Authorization timed out on the box")
                    if status == "unknown":
                        raise FreeboxInvalidToken(
                            "The box does not know this token request"
                        )
                    await asyncio.sleep(AUTH_POLL_INTERVAL)
        except TimeoutError as err:
            raise FreeboxAuthDenied(
                "Gave up waiting for validation on the box"
            ) from err

    async def async_open_session(self) -> None:
        """Exchange the app_token for a session token.

        The password is `hmac_sha1(app_token, challenge).hexdigest()`.
        """
        if not self.app_token:
            raise FreeboxAuthError("No app_token: the integration is not paired")

        async with self._session_lock:
            if self._session_token:
                return
            challenge = (
                await self.async_request("GET", "login/", authenticated=False)
            )["challenge"]
            password = hmac.new(
                self.app_token.encode(), challenge.encode(), "sha1"
            ).hexdigest()
            result = await self.async_request(
                "POST",
                "login/session/",
                json={"app_id": APP_ID, "password": password},
                authenticated=False,
            )
            self._session_token = result["session_token"]
            self._permissions = result.get("permissions") or {}
            _LOGGER.debug("Session opened, permissions: %s", self._permissions)

    async def async_refresh_permissions(self) -> dict[str, bool]:
        """Reopen the session to pick up permissions changed in Freebox OS."""
        self._session_token = None
        await self.async_open_session()
        return self.permissions

    async def async_close_session(self) -> None:
        """Close the session server-side. Best effort."""
        if not self._session_token:
            return
        try:
            await self.async_request("POST", "login/logout/")
        except FreeboxError as err:
            _LOGGER.debug("Logout failed, ignoring: %s", err)
        finally:
            self._session_token = None


    async def async_get_system(self) -> dict[str, Any]:
        """`GET /system/` — firmware, uptime, sensors[], fans[], disk_status."""
        return await self.async_request("GET", "system/")

    async def async_get_connection(self) -> dict[str, Any]:
        """`GET /connection/` — state, rates, byte counters, IPs."""
        return await self.async_request("GET", "connection/")

    async def async_get_connection_config(self) -> dict[str, Any]:
        """`GET /connection/config/` — remote access, WoL, adblock."""
        return await self.async_request("GET", "connection/config/")

    async def async_get_ftth(self) -> dict[str, Any]:
        """`GET /connection/ftth/` — SFP presence, link, optical power."""
        return await self.async_request("GET", "connection/ftth/")

    async def async_get_lan_interfaces(self) -> list[dict[str, Any]]:
        """`GET /lan/browser/interfaces/`."""
        return await self.async_request("GET", "lan/browser/interfaces/")

    async def async_get_lan_hosts(self, interface: str = "pub") -> list[dict[str, Any]]:
        """`GET /lan/browser/{interface}/` — one entry per known host."""
        return await self.async_request("GET", f"lan/browser/{interface}/")

    async def async_wake_on_lan(self, mac: str, interface: str = "pub") -> None:
        """`POST /lan/wol/{interface}/`."""
        await self.async_request(
            "POST", f"lan/wol/{interface}/", json={"mac": mac, "password": ""}
        )

    async def async_get_wifi_config(self) -> dict[str, Any]:
        """`GET /wifi/config/` — global enable flag, MAC filter state."""
        return await self.async_request("GET", "wifi/config/")

    async def async_set_wifi_enabled(self, enabled: bool) -> dict[str, Any]:
        """`PUT /wifi/config/`."""
        return await self.async_request(
            "PUT", "wifi/config/", json={"enabled": enabled}
        )

    async def async_get_wifi_aps(self) -> list[dict[str, Any]]:
        """`GET /wifi/ap/` — one entry per radio (2.4 / 5 / 6 GHz)."""
        return await self.async_request("GET", "wifi/ap/")

    async def async_get_wifi_stations(self, ap_id: int) -> list[dict[str, Any]]:
        """`GET /wifi/ap/{id}/stations/` — associated clients with RSSI."""
        return await self.async_request("GET", f"wifi/ap/{ap_id}/stations/")

    async def async_get_disks(self) -> list[dict[str, Any]]:
        """`GET /storage/disk/` — disks with their nested partitions."""
        return await self.async_request("GET", "storage/disk/")

    async def async_get_home_nodes(self) -> list[dict[str, Any]]:
        """`GET /home/nodes/` — every Freebox Home node. Undocumented API."""
        return await self.async_request("GET", "home/nodes/")

    async def async_get_home_endpoint(self, node_id: int, endpoint_id: int) -> Any:
        """`GET /home/endpoints/{node}/{endpoint}` — read one signal."""
        result = await self.async_request(
            "GET", f"home/endpoints/{node_id}/{endpoint_id}"
        )
        return result.get("value") if isinstance(result, dict) else result

    async def async_set_home_endpoint(
        self, node_id: int, endpoint_id: int, value: Any
    ) -> Any:
        """`PUT /home/endpoints/{node}/{endpoint}` — actuate one slot."""
        return await self.async_request(
            "PUT", f"home/endpoints/{node_id}/{endpoint_id}", json={"value": value}
        )

    async def async_get_phones(self) -> list[dict[str, Any]]:
        """`GET /phone/` — FXS and DECT handsets, ringing state."""
        return await self.async_request("GET", "phone/")

    async def async_get_calls(self) -> list[dict[str, Any]]:
        """`GET /call/log/` — full call log, newest first."""
        return await self.async_request("GET", "call/log/")

    async def async_mark_calls_read(self) -> None:
        """`POST /call/log/mark_all_as_read/`."""
        await self.async_request("POST", "call/log/mark_all_as_read/")

    async def async_get_download_stats(self) -> dict[str, Any]:
        """`GET /downloads/stats/` — aggregate download manager throughput."""
        return await self.async_request("GET", "downloads/stats/")

    async def async_get_vpn_servers(self) -> list[dict[str, Any]]:
        """`GET /vpn/` — VPN server configurations. Marked UNSTABLE upstream."""
        return await self.async_request("GET", "vpn/")

    async def async_get_vpn_connections(self) -> list[dict[str, Any]]:
        """`GET /vpn/connection/` — currently connected VPN clients."""
        return await self.async_request("GET", "vpn/connection/")

    async def async_get_profiles(self) -> list[dict[str, Any]]:
        """`GET /profile/` — parental-control profiles. Undocumented."""
        return await self.async_request("GET", "profile/")

    async def async_get_players(self) -> list[dict[str, Any]]:
        """`GET /player/` — attached Freebox Players."""
        return await self.async_request("GET", "player/")

    async def async_reboot(self) -> None:
        """`POST /system/reboot/`. Requires the `settings` permission."""
        await self.async_request("POST", "system/reboot/")
