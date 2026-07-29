"""Coordinators for the Freebox Ultra integration.

One coordinator per data category, all sharing a single `FreeboxClient`. The
categories have wildly different natural refresh rates (bandwidth every few
seconds, disk usage every few minutes), so a single coordinator would either
hammer the box or feel sluggish.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FreeboxClient
from .const import CATEGORY_META, DOMAIN, Category
from .exceptions import (
    FreeboxConnectionError,
    FreeboxError,
    FreeboxInsufficientRights,
    FreeboxInvalidToken,
)

_LOGGER = logging.getLogger(__name__)

type FetchFn = Callable[[FreeboxClient], Awaitable[dict[str, Any]]]


@dataclass
class FreeboxUltraData:
    """Everything the platforms need, stored on `entry.runtime_data`."""

    client: FreeboxClient
    serial: str
    device_info: DeviceInfo
    coordinators: dict[Category, FreeboxCoordinator] = field(default_factory=dict)

    def coordinator(self, category: Category) -> FreeboxCoordinator | None:
        """Return the coordinator for a category, if that category is enabled."""
        return self.coordinators.get(category)


type FreeboxUltraConfigEntry = ConfigEntry[FreeboxUltraData]


class FreeboxCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll one category of Freebox data."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: FreeboxClient,
        category: Category,
        fetch: FetchFn,
        *,
        interval: timedelta | None = None,
    ) -> None:
        """Initialize the coordinator for a single category."""
        self.client = client
        self.category = category
        self.missing_right: str | None = None
        self._fetch = fetch
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {category.value}",
            update_interval=interval or CATEGORY_META[category].interval,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch this category, translating Freebox errors to HA semantics."""
        try:
            return await self._fetch(self.client)
        except FreeboxInvalidToken as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except FreeboxInsufficientRights as err:
            self._report_missing_right(err)
            raise UpdateFailed(str(err)) from err
        except (FreeboxConnectionError, FreeboxError) as err:
            raise UpdateFailed(str(err)) from err

    def _report_missing_right(self, err: FreeboxInsufficientRights) -> None:
        """Surface a missing permission as a repair issue, and stop polling.

        Pairing grants no optional permission: they have to be toggled by hand
        in Freebox OS. Retrying on a schedule would only spam the box, so this
        category goes dormant until the entry is reloaded.
        """
        if self.missing_right is not None:
            return
        right = (
            err.missing_right or CATEGORY_META[self.category].permission or "unknown"
        )
        self.missing_right = right
        self.update_interval = None
        _LOGGER.warning(
            "Category %s disabled: the app is missing the '%s' permission",
            self.category.value,
            right,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"missing_permission_{self.category.value}",
            translation_key="missing_permission",
            translation_placeholders={
                "category": self.category.value,
                "permission": right,
            },
            severity=ir.IssueSeverity.WARNING,
            is_fixable=False,
        )


async def _fetch_connection(client: FreeboxClient) -> dict[str, Any]:
    """Fetch connection status, plus optical details when the box is FTTH."""
    data: dict[str, Any] = {"status": await client.async_get_connection()}
    try:
        data["ftth"] = await client.async_get_ftth()
    except FreeboxError as err:
        _LOGGER.debug("No FTTH data: %s", err)
        data["ftth"] = None
    return data


async def _fetch_system(client: FreeboxClient) -> dict[str, Any]:
    """Raw `/system/` payload: firmware, uptime, sensors[], fans[]."""
    return await client.async_get_system()


async def _fetch_lan(client: FreeboxClient) -> dict[str, Any]:
    """Every known LAN host, merged across browsable interfaces."""
    hosts: dict[str, dict[str, Any]] = {}
    try:
        interfaces = await client.async_get_lan_interfaces()
    except FreeboxError as err:
        if err.error_code == "nodev":
            return {"hosts": {}}
        raise
    for interface in interfaces or []:
        name = interface.get("name")
        if not name:
            continue
        for host in await client.async_get_lan_hosts(name) or []:
            host["_interface"] = name
            hosts[host["id"]] = host
    return {"hosts": hosts}


async def _fetch_wifi(client: FreeboxClient) -> dict[str, Any]:
    """Global Wi-Fi config, radios, and the clients associated to each."""
    aps = await client.async_get_wifi_aps() or []
    stations: dict[int, list[dict[str, Any]]] = {}
    for access_point in aps:
        ap_id = access_point["id"]
        try:
            stations[ap_id] = await client.async_get_wifi_stations(ap_id) or []
        except FreeboxError as err:
            _LOGGER.debug("No stations for AP %s: %s", ap_id, err)
            stations[ap_id] = []
    return {
        "config": await client.async_get_wifi_config(),
        "aps": {access_point["id"]: access_point for access_point in aps},
        "stations": stations,
    }


async def _fetch_storage(client: FreeboxClient) -> dict[str, Any]:
    """Disks with their nested partitions."""
    disks = await client.async_get_disks() or []
    return {"disks": {disk["id"]: disk for disk in disks}}


async def _fetch_home(client: FreeboxClient) -> dict[str, Any]:
    """All Freebox Home nodes, indexed by id."""
    nodes = await client.async_get_home_nodes() or []
    return {"nodes": {node["id"]: node for node in nodes}}


async def _fetch_phone(client: FreeboxClient) -> dict[str, Any]:
    """DECT and FXS handsets."""
    phones = await client.async_get_phones() or []
    return {"phones": {phone["id"]: phone for phone in phones}}


async def _fetch_calls(client: FreeboxClient) -> dict[str, Any]:
    """Call log, newest first, with a convenience missed-call count."""
    calls = await client.async_get_calls() or []
    return {
        "calls": calls,
        "missed": [call for call in calls if call.get("type") == "missed"],
    }


async def _fetch_vpn(client: FreeboxClient) -> dict[str, Any]:
    """VPN server configs and live client connections."""
    return {
        "servers": await client.async_get_vpn_servers() or [],
        "connections": await client.async_get_vpn_connections() or [],
    }


async def _fetch_downloads(client: FreeboxClient) -> dict[str, Any]:
    """Download manager aggregate stats."""
    return {"stats": await client.async_get_download_stats()}


async def _fetch_profiles(client: FreeboxClient) -> dict[str, Any]:
    """Parental-control profiles."""
    profiles = await client.async_get_profiles() or []
    return {"profiles": {profile["id"]: profile for profile in profiles}}


async def _fetch_player(client: FreeboxClient) -> dict[str, Any]:
    """Attached Freebox Players."""
    players = await client.async_get_players() or []
    return {"players": {player["id"]: player for player in players}}


CATEGORY_FETCHERS: dict[Category, FetchFn] = {
    Category.CONNECTION: _fetch_connection,
    Category.SYSTEM: _fetch_system,
    Category.LAN: _fetch_lan,
    Category.WIFI: _fetch_wifi,
    Category.STORAGE: _fetch_storage,
    Category.HOME: _fetch_home,
    Category.PHONE: _fetch_phone,
    Category.CALLS: _fetch_calls,
    Category.VPN: _fetch_vpn,
    Category.DOWNLOADS: _fetch_downloads,
    Category.PROFILES: _fetch_profiles,
    Category.PLAYER: _fetch_player,
}


async def async_build_coordinators(
    hass: HomeAssistant,
    entry: ConfigEntry,
    client: FreeboxClient,
    categories: list[Category],
    overrides: dict[str, int] | None = None,
) -> dict[Category, FreeboxCoordinator]:
    """Create and prime a coordinator for each enabled category.

    `CONNECTION` is load-bearing: if it fails, setup is retried later. Every
    other category is best effort — a box without a disk or without Freebox
    Home must not prevent the integration from loading, so those coordinators
    use `async_refresh()` and their entities simply start unavailable.
    """
    overrides = overrides or {}
    coordinators: dict[Category, FreeboxCoordinator] = {}
    for category in categories:
        if (fetch := CATEGORY_FETCHERS.get(category)) is None:
            continue
        seconds = overrides.get(category.value)
        coordinators[category] = FreeboxCoordinator(
            hass,
            entry,
            client,
            category,
            fetch,
            interval=timedelta(seconds=seconds) if seconds else None,
        )

    if required := coordinators.get(Category.CONNECTION):
        await required.async_config_entry_first_refresh()

    for category, coordinator in coordinators.items():
        if category is Category.CONNECTION:
            continue
        await coordinator.async_refresh()
        if not coordinator.last_update_success:
            _LOGGER.debug(
                "Category %s is unavailable at setup: %s",
                category.value,
                coordinator.last_exception,
            )

    return coordinators
