from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PonBikeApi
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class PonBikeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll bikes/info + last-known-states and merge into one data structure.

    Also accepts real-time updates pushed by the MQTT client via
    ``handle_mqtt_message``, which merges incoming event payloads into the
    cached data dict and immediately notifies all listening entities.
    """

    def __init__(self, hass: HomeAssistant, api: PonBikeApi) -> None:
        self.api = api
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            bikes = await self.api.async_get_bikes_info()
            states = await self.api.async_get_last_known_states()

            # bikes is usually a list; if provider changes, keep it robust
            bikes_list: list[dict[str, Any]] = bikes if isinstance(bikes, list) else []

            states_by_bike_id: dict[str, dict[str, Any]] = {}
            if isinstance(states, list):
                for s in states:
                    bid = s.get("bikeId")
                    if bid:
                        states_by_bike_id[str(bid)] = s

            # Preserve any bikeState values written by MQTT that REST doesn't provide
            if self.data:
                for bike_id, existing in (self.data.get("states_by_bike_id") or {}).items():
                    bike_state = existing.get("bikeState")
                    if bike_state and bike_id in states_by_bike_id:
                        states_by_bike_id[bike_id].setdefault("bikeState", bike_state)

            return {
                "bikes": bikes_list,
                "states_by_bike_id": states_by_bike_id,
            }
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err

    # ------------------------------------------------------------------
    # MQTT message handler
    # ------------------------------------------------------------------

    @callback
    def handle_mqtt_message(
        self, bike_id: str, topic_suffix: str, payload: dict[str, Any]
    ) -> None:
        """Merge an MQTT event payload into the cached state and notify entities.

        This is a synchronous ``@callback`` so it can be called directly from
        the MQTT listener task running in the HA event loop.
        """
        if self.data is None:
            return

        states: dict[str, dict[str, Any]] = self.data.setdefault(
            "states_by_bike_id", {}
        )
        state = states.setdefault(bike_id, {})

        event_type: str = payload.get("event", "")

        if topic_suffix == "bike-telemetry":
            self._merge_bike_telemetry(state, payload, event_type)
        elif topic_suffix == "iot-telemetry":
            self._merge_iot_telemetry(state, payload, event_type)
        elif topic_suffix == "location":
            self._merge_location(state, payload)
        else:
            _LOGGER.debug("Unknown MQTT topic suffix: %s", topic_suffix)
            return

        self.async_set_updated_data(self.data)

    # ------------------------------------------------------------------
    # Per-event merging helpers
    # ------------------------------------------------------------------

    def _merge_bike_telemetry(
        self, state: dict[str, Any], payload: dict[str, Any], event_type: str
    ) -> None:
        """Merge bike-telemetry topic events into the state dict."""
        if event_type == "BikeTelemetryEvents":
            items: list[dict[str, Any]] = payload.get("items") or []
            if not items:
                return
            # Use the item with the highest timestamp (most recent sample)
            item = max(items, key=lambda i: i.get("timestamp", 0))
            bt: dict[str, Any] = state.setdefault("bikeTelemetry", {})
            battery: dict[str, Any] = bt.setdefault("battery", {})

            _set_if_present(bt, "odometer", item.get("totalDistanceInKm"))
            _set_if_present(bt, "assistLevel", item.get("assistLevel"))
            _set_if_present(bt, "range", item.get("remainingRangeInKm"))
            _set_if_present(bt, "speedInKmh", item.get("speedInKmh"))
            _set_if_present(bt, "lightsOn", item.get("lightsOn"))
            _set_if_present(battery, "charge", item.get("chargePercentage"))
            _set_if_present(battery, "charging", item.get("charging"))
            _set_if_present(battery, "voltage", item.get("voltage"))
            if "bikeBatteries" in item:
                battery["bikeBatteries"] = item["bikeBatteries"]

        elif event_type == "BikeControlBoardTotalDistanceChangedEvent":
            bt = state.setdefault("bikeTelemetry", {})
            _set_if_present(bt, "odometer", payload.get("totalDistance"))

        elif event_type in ("BikeBatteryChargeFullEvent", "BikeBatteryChargeLowEvent"):
            # Notification-only events: no numeric payload to merge
            _LOGGER.debug("PON MQTT battery event: %s for bike %s", event_type, payload.get("bikeId"))

        else:
            _LOGGER.debug("Unhandled bike-telemetry event: %s", event_type)

    def _merge_iot_telemetry(
        self, state: dict[str, Any], payload: dict[str, Any], event_type: str
    ) -> None:
        """Merge iot-telemetry topic events into the state dict."""
        if event_type == "BikeControlBoardTelemetryEvents":
            items = payload.get("items") or []
            if not items:
                return
            item = max(items, key=lambda i: i.get("timestamp", 0))
            it: dict[str, Any] = state.setdefault("iotTelemetry", {})

            _set_if_present(it, "moduleCharge", item.get("chargePercentage"))
            _set_if_present(it, "moduleVoltage", item.get("voltage"))
            _set_if_present(it, "temperatureInC", item.get("temperatureInC"))
            _set_if_present(it, "gsmSignalStrength", item.get("gsmSignalStrength"))
            _set_if_present(it, "powerSupplied", item.get("powerSupplied"))

        elif event_type == "BikeActiveEvent":
            state["bikeState"] = "active"

        elif event_type == "BikeSleepingEvent":
            state["bikeState"] = "sleeping"

        elif event_type == "BikeSwitchedOffEvent":
            state["bikeState"] = "off"

        else:
            _LOGGER.debug("Unhandled iot-telemetry event: %s", event_type)

    def _merge_location(
        self, state: dict[str, Any], payload: dict[str, Any]
    ) -> None:
        """Merge location topic events into the state dict."""
        event_type = payload.get("event", "")
        if event_type != "BikeMovedEvents":
            _LOGGER.debug("Unhandled location event: %s", event_type)
            return

        items = payload.get("items") or []
        if not items:
            return
        item = max(items, key=lambda i: i.get("timestamp", 0))
        loc = item.get("location") or {}
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        if lat is None or lon is None:
            return

        # Map to the same nested structure the REST API and device_tracker use
        state["location"] = {
            "coordinate": {
                "latitude": lat,
                "longitude": lon,
            }
        }


# ------------------------------------------------------------------
# Utility
# ------------------------------------------------------------------

def _set_if_present(target: dict[str, Any], key: str, value: Any) -> None:
    """Set *key* in *target* only when *value* is not None."""
    if value is not None:
        target[key] = value

