"""Sensor platform for the Freebox Ultra integration (MVP: network + system).

Beware of the unit asymmetry in `/connection/`: `rate_down` and `rate_up` are
in byte/s while `bandwidth_down` and `bandwidth_up` are in bit/s. Do not
"unify" those native units — an 8 Gbit/s Ultra reports `bandwidth_down` as
8000000000, which read as byte/s would claim 64 Gbit/s.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    REVOLUTIONS_PER_MINUTE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .const import Category
from .coordinator import FreeboxCoordinator, FreeboxUltraConfigEntry, FreeboxUltraData
from .entity import FreeboxUltraEntity

UPTIME_TOLERANCE = timedelta(seconds=60)


@dataclass(frozen=True, kw_only=True)
class FreeboxSensorDescription(SensorEntityDescription):
    """Describes a Freebox sensor and how to read it from coordinator data."""

    category: Category
    value_fn: Callable[[dict[str, Any]], StateType]


def _connection(key: str) -> Callable[[dict[str, Any]], StateType]:
    """Read a field of the `/connection/` payload."""
    return lambda data: (data.get("status") or {}).get(key)


def _ftth_dbm(key: str) -> Callable[[dict[str, Any]], StateType]:
    """Read an optical power field, reported in hundredths of a dBm."""

    def _value(data: dict[str, Any]) -> StateType:
        ftth = data.get("ftth") or {}
        if (raw := ftth.get(key)) is None:
            return None
        return round(raw / 100, 2)

    return _value


CONNECTION_SENSORS: tuple[FreeboxSensorDescription, ...] = (
    FreeboxSensorDescription(
        key="rate_down",
        translation_key="rate_down",
        category=Category.CONNECTION,
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_connection("rate_down"),
    ),
    FreeboxSensorDescription(
        key="rate_up",
        translation_key="rate_up",
        category=Category.CONNECTION,
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_connection("rate_up"),
    ),
    FreeboxSensorDescription(
        key="bandwidth_down",
        translation_key="bandwidth_down",
        category=Category.CONNECTION,
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_connection("bandwidth_down"),
    ),
    FreeboxSensorDescription(
        key="bandwidth_up",
        translation_key="bandwidth_up",
        category=Category.CONNECTION,
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_connection("bandwidth_up"),
    ),
    FreeboxSensorDescription(
        key="bytes_down",
        translation_key="bytes_down",
        category=Category.CONNECTION,
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=_connection("bytes_down"),
    ),
    FreeboxSensorDescription(
        key="bytes_up",
        translation_key="bytes_up",
        category=Category.CONNECTION,
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=_connection("bytes_up"),
    ),
    FreeboxSensorDescription(
        key="ipv4",
        translation_key="ipv4",
        category=Category.CONNECTION,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_connection("ipv4"),
    ),
    FreeboxSensorDescription(
        key="ipv6",
        translation_key="ipv6",
        category=Category.CONNECTION,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_connection("ipv6"),
    ),
    FreeboxSensorDescription(
        key="sfp_pwr_rx",
        translation_key="sfp_pwr_rx",
        category=Category.CONNECTION,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_ftth_dbm("sfp_pwr_rx"),
    ),
    FreeboxSensorDescription(
        key="sfp_pwr_tx",
        translation_key="sfp_pwr_tx",
        category=Category.CONNECTION,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_ftth_dbm("sfp_pwr_tx"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FreeboxUltraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Freebox Ultra sensors."""
    data = entry.runtime_data
    entities: list[SensorEntity] = []

    if coordinator := data.coordinator(Category.CONNECTION):
        entities.extend(
            FreeboxSensor(coordinator, data, description)
            for description in CONNECTION_SENSORS
        )

    if coordinator := data.coordinator(Category.SYSTEM):
        entities.append(FreeboxUptimeSensor(coordinator, data))
        payload = coordinator.data or {}
        entities.extend(
            FreeboxProbeSensor(coordinator, data, probe, "sensors")
            for probe in payload.get("sensors", [])
        )
        entities.extend(
            FreeboxProbeSensor(coordinator, data, fan, "fans")
            for fan in payload.get("fans", [])
        )

    async_add_entities(entities)


class FreeboxSensor(FreeboxUltraEntity, SensorEntity):
    """A sensor read straight out of a coordinator payload."""

    entity_description: FreeboxSensorDescription

    def __init__(
        self,
        coordinator: FreeboxCoordinator,
        data: FreeboxUltraData,
        description: FreeboxSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, data, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Return the current value."""
        return self.entity_description.value_fn(self.coordinator.data or {})


class FreeboxProbeSensor(FreeboxUltraEntity, SensorEntity):
    """A temperature or fan probe advertised by `/system/`."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: FreeboxCoordinator,
        data: FreeboxUltraData,
        probe: dict[str, Any],
        source: str,
    ) -> None:
        """Initialize from one entry of the `sensors[]` or `fans[]` array."""
        self._probe_id = probe["id"]
        self._source = source
        super().__init__(coordinator, data, f"{source}_{self._probe_id}")
        self._attr_name = probe.get("name") or self._probe_id
        if source == "fans":
            self._attr_native_unit_of_measurement = REVOLUTIONS_PER_MINUTE
            self._attr_icon = "mdi:fan"
        else:
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> StateType:
        """Return the probe reading, or None if the probe disappeared."""
        for probe in (self.coordinator.data or {}).get(self._source, []):
            if probe.get("id") == self._probe_id:
                return probe.get("value")
        return None


class FreeboxUptimeSensor(FreeboxUltraEntity, SensorEntity):
    """Boot time derived from `uptime_val`."""

    _attr_translation_key = "last_boot"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: FreeboxCoordinator, data: FreeboxUltraData
    ) -> None:
        """Initialize the uptime sensor."""
        super().__init__(coordinator, data, "last_boot")
        self._boot_time: datetime | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Recompute the boot time, ignoring sub-minute jitter."""
        if (uptime := (self.coordinator.data or {}).get("uptime_val")) is not None:
            computed = dt_util.utcnow() - timedelta(seconds=uptime)
            if (
                self._boot_time is None
                or abs(computed - self._boot_time) > UPTIME_TOLERANCE
            ):
                self._boot_time = computed
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> datetime | None:
        """Return the last boot time."""
        if self._boot_time is None and (
            uptime := (self.coordinator.data or {}).get("uptime_val")
        ):
            self._boot_time = dt_util.utcnow() - timedelta(seconds=uptime)
        return self._boot_time
