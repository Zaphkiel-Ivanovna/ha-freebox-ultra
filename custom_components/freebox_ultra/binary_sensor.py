"""Binary sensor platform for the Freebox Ultra integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import Category
from .coordinator import FreeboxCoordinator, FreeboxUltraConfigEntry, FreeboxUltraData
from .entity import FreeboxUltraEntity


@dataclass(frozen=True, kw_only=True)
class FreeboxBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Freebox binary sensor."""

    category: Category
    value_fn: Callable[[dict[str, Any]], bool | None]


def _ftth_flag(key: str) -> Callable[[dict[str, Any]], bool | None]:
    """Read a boolean flag of the `/connection/ftth/` payload."""

    def _value(data: dict[str, Any]) -> bool | None:
        ftth = data.get("ftth")
        return None if ftth is None else bool(ftth.get(key))

    return _value


CONNECTION_BINARY_SENSORS: tuple[FreeboxBinarySensorDescription, ...] = (
    FreeboxBinarySensorDescription(
        key="wan_state",
        translation_key="wan_state",
        category=Category.CONNECTION,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: (data.get("status") or {}).get("state") == "up",
    ),
    FreeboxBinarySensorDescription(
        key="sfp_present",
        translation_key="sfp_present",
        category=Category.CONNECTION,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_ftth_flag("sfp_present"),
    ),
    FreeboxBinarySensorDescription(
        key="sfp_has_signal",
        translation_key="sfp_has_signal",
        category=Category.CONNECTION,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_ftth_flag("sfp_has_signal"),
    ),
    FreeboxBinarySensorDescription(
        key="ftth_link",
        translation_key="ftth_link",
        category=Category.CONNECTION,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_ftth_flag("link"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FreeboxUltraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Freebox Ultra binary sensors."""
    data = entry.runtime_data
    if (coordinator := data.coordinator(Category.CONNECTION)) is None:
        return
    async_add_entities(
        FreeboxBinarySensor(coordinator, data, description)
        for description in CONNECTION_BINARY_SENSORS
    )


class FreeboxBinarySensor(FreeboxUltraEntity, BinarySensorEntity):
    """A binary sensor read straight out of a coordinator payload."""

    entity_description: FreeboxBinarySensorDescription

    def __init__(
        self,
        coordinator: FreeboxCoordinator,
        data: FreeboxUltraData,
        description: FreeboxBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, data, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the current state."""
        return self.entity_description.value_fn(self.coordinator.data or {})
