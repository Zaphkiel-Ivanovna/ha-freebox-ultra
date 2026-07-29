"""The Freebox Ultra integration."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo

from .api import BoxDescriptor, FreeboxClient
from .const import (
    CONF_APP_TOKEN,
    DEFAULT_CATEGORIES,
    DOMAIN,
    MANUFACTURER,
    OPT_CATEGORIES,
    OPT_SCAN_INTERVALS,
    PLATFORMS,
    Category,
)
from .coordinator import (
    FreeboxUltraConfigEntry,
    FreeboxUltraData,
    async_build_coordinators,
)
from .exceptions import FreeboxConnectionError, FreeboxError, FreeboxInvalidToken

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: FreeboxUltraConfigEntry
) -> bool:
    """Set up Freebox Ultra from a config entry."""
    descriptor = BoxDescriptor.from_api(entry.data)
    client = await FreeboxClient.async_create(
        hass,
        descriptor,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        app_token=entry.data[CONF_APP_TOKEN],
    )

    try:
        await client.async_open_session()
    except FreeboxInvalidToken as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except FreeboxConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    system: dict[str, str] = {}
    try:
        system = await client.async_get_system()
    except FreeboxError as err:
        _LOGGER.debug("Could not read /system/ at setup: %s", err)

    serial = system.get("serial") or descriptor.uid
    device_info = DeviceInfo(
        identifiers={(DOMAIN, serial)},
        manufacturer=MANUFACTURER,
        name=descriptor.box_model_name or descriptor.device_name,
        model=descriptor.box_model_name,
        model_id=descriptor.box_model,
        serial_number=serial,
        sw_version=system.get("firmware_version"),
        configuration_url=f"https://{client.host}:{client.port}/",
    )

    for category in Category:
        ir.async_delete_issue(hass, DOMAIN, f"missing_permission_{category.value}")

    categories = [
        category
        for category in Category
        if category.value
        in entry.options.get(
            OPT_CATEGORIES, [default.value for default in DEFAULT_CATEGORIES]
        )
    ]
    coordinators = await async_build_coordinators(
        hass,
        entry,
        client,
        categories,
        overrides=entry.options.get(OPT_SCAN_INTERVALS, {}),
    )

    entry.runtime_data = FreeboxUltraData(
        client=client,
        serial=serial,
        device_info=device_info,
        coordinators=coordinators,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: FreeboxUltraConfigEntry
) -> bool:
    """Unload a config entry, closing the Freebox session."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.client.async_close_session()
    return unload_ok
