"""Diagnostics support for the Freebox Ultra integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_API_DOMAIN, CONF_APP_TOKEN, CONF_BOX_UID
from .coordinator import FreeboxUltraConfigEntry

TO_REDACT = {
    CONF_API_DOMAIN,
    CONF_APP_TOKEN,
    CONF_BOX_UID,
    "ipv4",
    "ipv6",
    "l2ident",
    "l3connectivities",
    "mac",
    "number",
    "serial",
    "sfp_serial",
    "ssid",
    "uid",
    "primary_name",
    "hostname",
    "names",
    "label",
    "name",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FreeboxUltraConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "client": {
            "base_url_api_major": data.client.descriptor.api_major,
            "box_api_version": data.client.descriptor.api_version,
            "box_model": data.client.descriptor.box_model,
            "device_type": data.client.descriptor.device_type,
            "permissions": data.client.permissions,
        },
        "coordinators": {
            category.value: {
                "update_interval": (
                    coordinator.update_interval.total_seconds()
                    if coordinator.update_interval
                    else None
                ),
                "last_update_success": coordinator.last_update_success,
                "missing_right": coordinator.missing_right,
                "data": async_redact_data(coordinator.data or {}, TO_REDACT),
            }
            for category, coordinator in data.coordinators.items()
        },
    }
