"""Sensor platform for SolarPredict."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import SolarPredictCoordinator


@dataclass(frozen=True, kw_only=True)
class SolarPredictSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]


SENSORS: tuple[SolarPredictSensorDescription, ...] = (
    SolarPredictSensorDescription(
        key="power_now",
        translation_key="power_now",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda s: s["power_now_w"],
    ),
    SolarPredictSensorDescription(
        key="energy_today",
        translation_key="energy_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda s: s["energy_today_kwh"],
    ),
    SolarPredictSensorDescription(
        key="energy_today_remaining",
        translation_key="energy_today_remaining",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda s: s["energy_today_remaining_kwh"],
    ),
    SolarPredictSensorDescription(
        key="energy_tomorrow",
        translation_key="energy_tomorrow",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda s: s["energy_tomorrow_kwh"],
    ),
    SolarPredictSensorDescription(
        key="energy_next_hour",
        translation_key="energy_next_hour",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda s: s["energy_next_hour_kwh"],
    ),
    SolarPredictSensorDescription(
        key="peak_power_today",
        translation_key="peak_power_today",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda s: s["peak_power_today_w"],
    ),
    SolarPredictSensorDescription(
        key="peak_time_today",
        translation_key="peak_time_today",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: dt_util.parse_datetime(s["peak_time_today"])
        if s.get("peak_time_today")
        else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SolarPredictCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SolarPredictSensor(coordinator, entry, description) for description in SENSORS
    )


class SolarPredictSensor(CoordinatorEntity[SolarPredictCoordinator], SensorEntity):
    """A forecast value exposed as a sensor."""

    entity_description: SolarPredictSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolarPredictCoordinator,
        entry: ConfigEntry,
        description: SolarPredictSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="SolarPredict",
            manufacturer="SolarPredict",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=coordinator.host,
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data["summary"])
