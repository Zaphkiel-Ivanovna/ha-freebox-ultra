"""Base entity for the Freebox Ultra integration."""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import FreeboxCoordinator, FreeboxUltraData


class FreeboxUltraEntity(CoordinatorEntity[FreeboxCoordinator]):
    """An entity backed by one category coordinator of the box itself."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FreeboxCoordinator,
        data: FreeboxUltraData,
        key: str,
    ) -> None:
        """Initialize the entity, anchoring it to the box device."""
        super().__init__(coordinator)
        self._data = data
        self._attr_unique_id = f"{data.serial}_{key}"
        self._attr_device_info = data.device_info
