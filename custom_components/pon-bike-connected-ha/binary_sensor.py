from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
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

    model = bike.get("displayName") or bike.get("sku") or "Connected Bike"
    serial = bike.get("frameNumber") or None

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

    entities: list[BinarySensorEntity] = []
    data = coordinator.data or {}
    bikes: list[dict[str, Any]] = data.get("bikes", [])

    for bike in bikes:
        bike_id = str(bike.get("bikeId") or "")
        if not bike_id:
            continue
        entities.append(PonBikeBatteryChargingBinarySensor(coordinator, entry, bike))

    async_add_entities(entities)


class _PonBikeBaseBinarySensor(CoordinatorEntity[PonBikeCoordinator], BinarySensorEntity):
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


class PonBikeBatteryChargingBinarySensor(_PonBikeBaseBinarySensor):
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(self, coordinator: PonBikeCoordinator, entry: ConfigEntry, bike: dict[str, Any]) -> None:
        super().__init__(coordinator, entry, bike)
        self._attr_name = f"{self._bike_name} Bike battery charging"
        self._attr_unique_id = f"{entry.entry_id}_{self._bike_id}_bike_battery_charging"
        self._attr_suggested_object_id = f"ponbike_{entry.entry_id}_{self._bike_id}_bike_battery_charging"

    @property
    def is_on(self) -> bool | None:
        bt = self._state.get("bikeTelemetry") or {}
        battery = bt.get("battery") or {}
        charging = battery.get("charging")

        if charging is None:
            return None
        return bool(charging)