"""Fixtures for the Freebox Ultra tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.freebox_ultra.api import BoxDescriptor
from custom_components.freebox_ultra.const import CONF_APP_TOKEN, DOMAIN

API_VERSION_PAYLOAD = {
    "box_model_name": "Freebox v9 (r1)",
    "api_base_url": "/api/",
    "https_port": 43210,
    "device_name": "Freebox Server",
    "https_available": True,
    "box_model": "fbxgw9-r1/full",
    "api_domain": "abcdefgh.fbxos.fr",
    "uid": "abcdefgh0123456789abcdef01234567",
    "api_version": "12.0",
    "device_type": "FreeboxServer9,1",
}

SYSTEM_PAYLOAD = {
    "firmware_version": "4.9.7",
    "mac": "AA:BB:CC:DD:EE:FF",
    "serial": "2444XXXXXXXXXXX",
    "uptime_val": 123456,
    "board_name": "fbxgw9r1",
    "disk_status": "active",
    "box_flavor": "full",
    "sensors": [
        {"id": "temp_cpum", "name": "Température CPU M", "value": 52},
        {"id": "temp_sw", "name": "Température Switch", "value": 48},
    ],
    "fans": [{"id": "fan0_speed", "name": "Ventilateur 1", "value": 1800}],
}

CONNECTION_PAYLOAD = {
    "state": "up",
    "type": "ethernet",
    "media": "ftth",
    "ipv4": "88.170.0.1",
    "ipv6": "2a01:e0a::1",
    "rate_up": 125000,
    "rate_down": 980000,
    "bandwidth_up": 700000000,
    "bandwidth_down": 8000000000,
    "bytes_up": 123456789,
    "bytes_down": 987654321,
}

FTTH_PAYLOAD = {
    "sfp_present": True,
    "sfp_alim_ok": True,
    "sfp_has_power_report": True,
    "sfp_has_signal": True,
    "link": True,
    "sfp_serial": "XXXXXXXX",
    "sfp_model": "GPON",
    "sfp_vendor": "FREEBOX",
    "sfp_pwr_tx": 234,
    "sfp_pwr_rx": -1567,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> None:
    """Load the integration from `custom_components/` during tests."""


@pytest.fixture
def descriptor() -> BoxDescriptor:
    """Return a descriptor for a Freebox Ultra."""
    return BoxDescriptor.from_api(API_VERSION_PAYLOAD)


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry as the config flow would have created it."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Freebox v9 (r1)",
        unique_id=API_VERSION_PAYLOAD["uid"],
        data={
            "host": API_VERSION_PAYLOAD["api_domain"],
            "port": API_VERSION_PAYLOAD["https_port"],
            CONF_APP_TOKEN: "an-app-token",
            **API_VERSION_PAYLOAD,
        },
        options={"categories": ["connection", "system"], "scan_intervals": {}},
    )


@pytest.fixture
def mock_client() -> Generator[AsyncMock]:
    """Patch `FreeboxClient` everywhere it is imported.

    Only the transport is mocked: the coordinators, entity descriptions and
    value functions all run for real against the payloads above.
    """
    with (
        patch(
            "custom_components.freebox_ultra.FreeboxClient.async_create",
            autospec=False,
        ) as create,
        patch(
            "custom_components.freebox_ultra.config_flow.FreeboxClient.async_create",
            autospec=False,
        ) as flow_create,
    ):
        client = AsyncMock()
        client.host = API_VERSION_PAYLOAD["api_domain"]
        client.port = API_VERSION_PAYLOAD["https_port"]
        client.permissions = {"settings": True, "home": False}
        client.async_get_system.return_value = SYSTEM_PAYLOAD
        client.async_get_connection.return_value = CONNECTION_PAYLOAD
        client.async_get_ftth.return_value = FTTH_PAYLOAD
        client.async_request_app_token.return_value = ("an-app-token", 42)
        create.return_value = client
        flow_create.return_value = client
        yield client
