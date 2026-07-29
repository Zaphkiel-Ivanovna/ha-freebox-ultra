"""Tests for the Freebox Ultra setup and entity creation."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.freebox_ultra.const import DOMAIN
from custom_components.freebox_ultra.exceptions import (
    FreeboxConnectionError,
    FreeboxInvalidToken,
)

from .conftest import SYSTEM_PAYLOAD


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add the entry to hass and set it up."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("mock_client")
async def test_setup_creates_device_and_entities(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The box becomes one device carrying the network and system entities."""
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, SYSTEM_PAYLOAD["serial"])}
    )
    assert device is not None
    assert device.sw_version == "4.9.7"
    assert device.connections == set()

    entities = er.async_entries_for_config_entry(
        er.async_get(hass), mock_config_entry.entry_id
    )
    unique_ids = {entity.unique_id for entity in entities}
    serial = SYSTEM_PAYLOAD["serial"]
    assert f"{serial}_rate_down" in unique_ids
    assert f"{serial}_wan_state" in unique_ids
    assert f"{serial}_last_boot" in unique_ids
    assert f"{serial}_sensors_temp_cpum" in unique_ids
    assert f"{serial}_fans_fan0_speed" in unique_ids


@pytest.mark.usefixtures("mock_client")
async def test_values_are_read_from_payloads(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Value functions map the raw payloads onto the right native values."""
    await _setup(hass, mock_config_entry)
    registry = er.async_get(hass)
    serial = SYSTEM_PAYLOAD["serial"]

    def state_of(unique_id: str) -> str:
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        if entity_id is None:
            entity_id = registry.async_get_entity_id("binary_sensor", DOMAIN, unique_id)
        assert entity_id is not None, unique_id
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        return state.state

    assert state_of(f"{serial}_rate_down") == "0.98"
    assert state_of(f"{serial}_bandwidth_down") == "1000.0"
    assert state_of(f"{serial}_wan_state") == "on"
    assert state_of(f"{serial}_sensors_temp_cpum") == "52"
    assert state_of(f"{serial}_fans_fan0_speed") == "1800"
    assert state_of(f"{serial}_sfp_pwr_rx") == "-15.67"


async def test_revoked_token_triggers_reauth(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """A revoked app_token puts the entry in the reauth state."""
    mock_client.async_open_session.side_effect = FreeboxInvalidToken("revoked")
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_unreachable_box_retries_later(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """A network failure is retryable, not a configuration error."""
    mock_client.async_open_session.side_effect = FreeboxConnectionError("down")
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
