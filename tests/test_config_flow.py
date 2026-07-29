"""Tests for the Freebox Ultra config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.freebox_ultra.api import BoxDescriptor
from custom_components.freebox_ultra.const import CONF_APP_TOKEN, DOMAIN
from custom_components.freebox_ultra.exceptions import (
    FreeboxAuthDenied,
    FreeboxConnectionError,
)

from .conftest import API_VERSION_PAYLOAD


def _zeroconf_info() -> ZeroconfServiceInfo:
    """Build an mDNS announcement equivalent to a real `_fbx-api._tcp` record."""
    return ZeroconfServiceInfo(
        ip_address="192.168.1.254",
        ip_addresses=["192.168.1.254"],
        hostname="Freebox-Server.local.",
        name="Freebox Server._fbx-api._tcp.local.",
        port=80,
        type="_fbx-api._tcp.local.",
        properties={
            key: str(value) if not isinstance(value, bool) else str(int(value))
            for key, value in API_VERSION_PAYLOAD.items()
        },
    )


async def _run_pairing(hass: HomeAssistant, flow_id: str) -> dict:
    """Walk through the link → authorize → finish legs of the flow."""
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    if result["type"] is FlowResultType.SHOW_PROGRESS:
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(flow_id)
    return result


@pytest.mark.usefixtures("mock_client")
async def test_user_flow_pairs_and_creates_entry(hass: HomeAssistant) -> None:
    """A manual setup ends on a config entry holding the app_token."""
    with patch(
        "custom_components.freebox_ultra.config_flow.async_discover",
        return_value=None,
        side_effect=FreeboxConnectionError("no autodiscovery"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch(
        "custom_components.freebox_ultra.config_flow.async_probe",
        return_value=BoxDescriptor.from_api(API_VERSION_PAYLOAD),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "abcdefgh.fbxos.fr", CONF_PORT: 43210},
        )
    assert result["step_id"] == "link"

    result = await _run_pairing(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_APP_TOKEN] == "an-app-token"
    assert result["result"].unique_id == API_VERSION_PAYLOAD["uid"]


@pytest.mark.usefixtures("mock_client")
async def test_zeroconf_discovery_starts_at_link(hass: HomeAssistant) -> None:
    """Discovery gives us everything, so no host/port form is shown."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zeroconf_info()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "link"


async def test_zeroconf_aborts_when_already_configured(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A second announcement of a known box is ignored."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zeroconf_info()
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_refused_on_box_returns_to_link(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """Refusing on the box's screen shows the error and offers a retry."""
    mock_client.async_wait_for_authorization.side_effect = FreeboxAuthDenied("nope")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zeroconf_info()
    )
    result = await _run_pairing(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "link"
    assert result["errors"] == {"base": "authorization_denied"}
