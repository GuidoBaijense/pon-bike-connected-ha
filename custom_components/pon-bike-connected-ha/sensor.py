from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricPotential,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PonBikeCoordinator


def _bike_name(bike: dict[str, Any]) -> str:
    nickname = bike.get("nickName") or bike.get("nickname")
    frame = bike.get("frameNumber")
    bike_id = bike.get("bikeId")

    if nickname and frame:
        return f"{nickname} ({frame})"
    if nickname:
        return nickname
    if frame:
        return frame
    if bike_id:
        return bike_id
    return "Bike"


def _device_info(bike: dict[str, Any]) -> dict[str, Any]:
    bike_id = str(bike.get("bikeId") or "")
    manufacturer_id = bike.get("manufacturerId")

    manufacturer = "PON"
    if manufacturer_id == "UA":
        manufacturer = "Urban Arrow"

    # Show human-friendly model in the device card
    model = bike.get("displayName") or bike.get("sku") or "Connected Bike"
    serial = bike.get("frameNumber") or None

    # Concatenate hardware attributes into hw_version
    hw_parts = [
        bike.get("category"),
        bike.get("type"),
        bike.get("color"),
        bike.get("driveUnitType"),
    ]
    hw_parts = [str(p) for p in hw_parts if p]
    hw_version = "-".join(hw_parts) if hw_parts else None

    info: dict[str, Any] = {
        "identifiers": {(DOMAIN, bike_id)},
        "name": _bike_name(bike),
        "manufacturer": manufacturer,
        "model": model,
        "serial_number": serial,
    }

    if hw_version:
        info["hw_version"] = hw_version

    return info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PonBikeCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[SensorEntity] = []
    data = coordinator.data or {}
    bikes: list[dict[str, Any]] = data.get("bikes", [])

    for bike in bikes:
        bike_id = str(bike.get("bikeId") or "")
        if not bike_id:
            continue
        entities.append(PonBikeOdometerSensor(coordinator, entry, bike))
        entities.append(PonBikeModuleChargeSensor(coordinator, entry, bike))
        entities.append(PonBikeBatteryChargeSensor(coordinator, entry, bike))
        entities.append(PonBikeAssistLevelSensor(coordinator, entry, bike))
        entities.append(PonBikeRangeSensor(coordinator, entry, bike))
        # MQTT-sourced sensors (return None until first MQTT event arrives)
        entities.append(PonBikeSpeedSensor(coordinator, entry, bike))
        entities.append(PonBikeBatteryVoltageSensor(coordinator, entry, bike))
        entities.append(PonBikeIotModuleVoltageSensor(coordinator, entry, bike))
        entities.append(PonBikeIotTemperatureSensor(coordinator, entry, bike))
        entities.append(PonBikeGsmSignalSensor(coordinator, entry, bike))
        entities.append(PonBikeStateSensor(coordinator, entry, bike))
        
    async_add_entities(entities)


class _PonBikeBaseSensor(CoordinatorEntity[PonBikeCoordinator], SensorEntity):
    def __init__(self, coordinator: PonBikeCoordinator, entry: ConfigEntry, bike: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._bike = bike
        self._bike_id = str(bike.get("bikeId") or "")
        self._bike_name = _bike_name(bike)
        self._attr_device_info = _device_info(bike)

    @property
    def _state(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return (data.get("states_by_bike_id") or {}).get(self._bike_id, {})


class PonBikeOdometerSensor(_PonBikeBaseSensor):
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator: PonBikeCoordinator, entry: ConfigEntry, bike: dict[str, Any]) -> None:
        super().__init__(coordinator, entry, bike)
        self._attr_name = f"{self._bike_name} Odometer"
        self._attr_unique_id = f"{entry.entry_id}_{self._bike_id}_odometer"
        self._attr_suggested_object_id = f"ponbike_{entry.entry_id}_{self._bike_id}_odometer"
        self._attr_native_unit_of_measurement = "km"
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> float | None:
        bt = self._state.get("bikeTelemetry") or {}
        odo = bt.get("odometer")
        try:
            return float(odo) if odo is not None else None
        except (TypeError, ValueError):
            return None


class PonBikeModuleChargeSensor(_PonBikeBaseSensor):
    _attr_icon = "mdi:battery"

    def __init__(self, coordinator: PonBikeCoordinator, entry: ConfigEntry, bike: dict[str, Any]) -> None:
        super().__init__(coordinator, entry, bike)
        self._attr_name = f"{self._bike_name} IOT Module charge"
        self._attr_unique_id = f"{entry.entry_id}_{self._bike_id}_module_charge"
        self._attr_suggested_object_id = f"ponbike_{entry.entry_id}_{self._bike_id}_module_charge"
        self._attr_native_unit_of_measurement = "%"
        self._attr_device_class = SensorDeviceClass.BATTERY

    @property
    def native_value(self) -> int | None:
        it = self._state.get("iotTelemetry") or {}
        mc = it.get("moduleCharge")
        try:
            return int(mc) if mc is not None else None
        except (TypeError, ValueError):
            return None


class PonBikeBatteryChargeSensor(_PonBikeBaseSensor):
    _attr_icon = "mdi:battery"

    def __init__(self, coordinator: PonBikeCoordinator, entry: ConfigEntry, bike: dict[str, Any]) -> None:
        super().__init__(coordinator, entry, bike)
        self._attr_name = f"{self._bike_name} Bike battery charge"
        self._attr_unique_id = f"{entry.entry_id}_{self._bike_id}_bike_battery_charge"
        self._attr_suggested_object_id = f"ponbike_{entry.entry_id}_{self._bike_id}_bike_battery_charge"
        self._attr_native_unit_of_measurement = "%"
        self._attr_device_class = SensorDeviceClass.BATTERY

    @property
    def native_value(self) -> int | None:
        bt = self._state.get("bikeTelemetry") or {}
        battery = bt.get("battery") or {}
        charge = battery.get("charge")
        try:
            return int(charge) if charge is not None else None
        except (TypeError, ValueError):
            return None

class PonBikeAssistLevelSensor(_PonBikeBaseSensor):
    _attr_icon = "mdi:speedometer"

    def __init__(self, coordinator: PonBikeCoordinator, entry: ConfigEntry, bike: dict[str, Any]) -> None:
        super().__init__(coordinator, entry, bike)
        self._attr_name = f"{self._bike_name} Assist level"
        self._attr_unique_id = f"{entry.entry_id}_{self._bike_id}_assist_level"
        self._attr_suggested_object_id = f"ponbike_{entry.entry_id}_{self._bike_id}_assist_level"

    @property
    def native_value(self) -> int | None:
        bt = self._state.get("bikeTelemetry") or {}
        assist = bt.get("assistLevel")
        try:
            return int(assist) if assist is not None else None
        except (TypeError, ValueError):
            return None


class PonBikeRangeSensor(_PonBikeBaseSensor):
    _attr_icon = "mdi:map-marker-distance"

    def __init__(self, coordinator: PonBikeCoordinator, entry: ConfigEntry, bike: dict[str, Any]) -> None:
        super().__init__(coordinator, entry, bike)
        self._attr_name = f"{self._bike_name} Range"
        self._attr_unique_id = f"{entry.entry_id}_{self._bike_id}_range"
        self._attr_suggested_object_id = f"ponbike_{entry.entry_id}_{self._bike_id}_range"
        self._attr_native_unit_of_measurement = "km"
        self._attr_device_class = SensorDeviceClass.DISTANCE

    @property
    def native_value(self) -> float | None:
        bt = self._state.get("bikeTelemetry") or {}
        rng = bt.get("range")
        try:
            return float(rng) if rng is not None else None
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# MQTT-sourced sensors (populated by real-time events; None until first push)
# ---------------------------------------------------------------------------

class PonBikeSpeedSensor(_PonBikeBaseSensor):
    _attr_icon = "mdi:speedometer"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.SPEED
    _attr_native_unit_of_measurement = UnitOfSpeed.KILOMETERS_PER_HOUR

    def __init__(self, coordinator: PonBikeCoordinator, entry: ConfigEntry, bike: dict[str, Any]) -> None:
        super().__init__(coordinator, entry, bike)
        self._attr_name = f"{self._bike_name} Speed"
        self._attr_unique_id = f"{entry.entry_id}_{self._bike_id}_speed"
        self._attr_suggested_object_id = f"ponbike_{entry.entry_id}_{self._bike_id}_speed"

    @property
    def native_value(self) -> float | None:
        bt = self._state.get("bikeTelemetry") or {}
        val = bt.get("speedInKmh")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None


class PonBikeBatteryVoltageSensor(_PonBikeBaseSensor):
    _attr_icon = "mdi:flash"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT

    def __init__(self, coordinator: PonBikeCoordinator, entry: ConfigEntry, bike: dict[str, Any]) -> None:
        super().__init__(coordinator, entry, bike)
        self._attr_name = f"{self._bike_name} Battery voltage"
        self._attr_unique_id = f"{entry.entry_id}_{self._bike_id}_battery_voltage"
        self._attr_suggested_object_id = f"ponbike_{entry.entry_id}_{self._bike_id}_battery_voltage"

    @property
    def native_value(self) -> float | None:
        bt = self._state.get("bikeTelemetry") or {}
        battery = bt.get("battery") or {}
        val = battery.get("voltage")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None


class PonBikeIotModuleVoltageSensor(_PonBikeBaseSensor):
    _attr_icon = "mdi:flash"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT

    def __init__(self, coordinator: PonBikeCoordinator, entry: ConfigEntry, bike: dict[str, Any]) -> None:
        super().__init__(coordinator, entry, bike)
        self._attr_name = f"{self._bike_name} IOT Module voltage"
        self._attr_unique_id = f"{entry.entry_id}_{self._bike_id}_module_voltage"
        self._attr_suggested_object_id = f"ponbike_{entry.entry_id}_{self._bike_id}_module_voltage"

    @property
    def native_value(self) -> float | None:
        it = self._state.get("iotTelemetry") or {}
        val = it.get("moduleVoltage")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None


class PonBikeIotTemperatureSensor(_PonBikeBaseSensor):
    _attr_icon = "mdi:thermometer"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: PonBikeCoordinator, entry: ConfigEntry, bike: dict[str, Any]) -> None:
        super().__init__(coordinator, entry, bike)
        self._attr_name = f"{self._bike_name} IOT Module temperature"
        self._attr_unique_id = f"{entry.entry_id}_{self._bike_id}_module_temperature"
        self._attr_suggested_object_id = f"ponbike_{entry.entry_id}_{self._bike_id}_module_temperature"

    @property
    def native_value(self) -> float | None:
        it = self._state.get("iotTelemetry") or {}
        val = it.get("temperatureInC")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None


class PonBikeGsmSignalSensor(_PonBikeBaseSensor):
    _attr_icon = "mdi:signal"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: PonBikeCoordinator, entry: ConfigEntry, bike: dict[str, Any]) -> None:
        super().__init__(coordinator, entry, bike)
        self._attr_name = f"{self._bike_name} GSM signal strength"
        self._attr_unique_id = f"{entry.entry_id}_{self._bike_id}_gsm_signal"
        self._attr_suggested_object_id = f"ponbike_{entry.entry_id}_{self._bike_id}_gsm_signal"

    @property
    def native_value(self) -> int | None:
        it = self._state.get("iotTelemetry") or {}
        val = it.get("gsmSignalStrength")
        try:
            return int(val) if val is not None else None
        except (TypeError, ValueError):
            return None


class PonBikeStateSensor(_PonBikeBaseSensor):
    _attr_icon = "mdi:bike"

    def __init__(self, coordinator: PonBikeCoordinator, entry: ConfigEntry, bike: dict[str, Any]) -> None:
        super().__init__(coordinator, entry, bike)
        self._attr_name = f"{self._bike_name} State"
        self._attr_unique_id = f"{entry.entry_id}_{self._bike_id}_bike_state"
        self._attr_suggested_object_id = f"ponbike_{entry.entry_id}_{self._bike_id}_bike_state"

    @property
    def native_value(self) -> str | None:
        return self._state.get("bikeState")