from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
)
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session
from homeassistant.helpers.event import async_track_time_interval

from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, PLATFORMS, MQTT_BROKER, MQTT_PORT, MQTT_TOPIC_PREFIX, MQTT_TOKEN_REFRESH_INTERVAL
from .api import PonBikeApi, PonBikeApiError
from .coordinator import PonBikeCoordinator
from .mqtt_client import PonBikeMqttClient

_LOGGER = logging.getLogger(__name__)


def _extract_http_status(err: PonBikeApiError) -> int | None:
    """Parse HTTP status from PonBikeApiError message (best effort)."""
    msg = str(err)
    if not msg.startswith("HTTP "):
        return None
    try:
        return int(msg.split(" ", 2)[1])
    except Exception:  # noqa: BLE001
        return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PON Bike Connected from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    try:
        implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    except config_entry_oauth2_flow.ImplementationUnavailableError as err:
        raise ConfigEntryNotReady from err

    oauth_session = OAuth2Session(hass, entry, implementation)
    api = PonBikeApi(oauth_session)

    # Proof-of-life API call once at setup (keep baseline behavior)
    try:
        bikes_info = await api.async_get_bikes_info()
        if isinstance(bikes_info, list):
            _LOGGER.info("PON bikes/info OK. Returned %s items", len(bikes_info))
        elif isinstance(bikes_info, dict):
            _LOGGER.info("PON bikes/info OK. Top-level keys: %s", list(bikes_info.keys()))
        else:
            _LOGGER.info("PON bikes/info OK. Response type: %s", type(bikes_info))
    except PonBikeApiError as err:
        status = _extract_http_status(err)
        if status in (401, 403):
            _LOGGER.warning("PON API unauthorized (%s). Triggering re-auth. %s", status, err)
            raise ConfigEntryAuthFailed from err
        if status == 404:
            _LOGGER.error("PON API endpoint not found (404). Check BASE_URL/path. %s", err)
            return False
        _LOGGER.error("PON bikes/info failed (%s): %s", status, err)
        raise ConfigEntryNotReady from err
    except Exception as err:  # noqa: BLE001
        _LOGGER.exception("Unexpected error during PON API setup: %s", err)
        raise ConfigEntryNotReady from err

    # Coordinator (REST polling every 5 minutes as fallback)
    coordinator = PonBikeCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()
    dev_reg = dr.async_get(hass)
    for bike in coordinator.data.get("bikes", []):
        device = dev_reg.async_get_device(identifiers={(DOMAIN, bike.get("bikeId"))})
        if device:
            dev_reg.async_update_device(
                device.id,
                model=bike.get("displayName") or device.model,
                hw_version="-".join(
                    str(v) for v in (
                        bike.get("category"),
                        bike.get("type"),
                        bike.get("color"),
                        bike.get("driveUnitType"),
                    ) if v
                ) or device.hw_version,
            )

    # ------------------------------------------------------------------
    # MQTT — real-time event push
    # ------------------------------------------------------------------
    mqtt_client: PonBikeMqttClient | None = None
    cancel_token_refresh = None

    try:
        await oauth_session.async_ensure_token_valid()
        access_token: str = oauth_session.token.get("access_token", "")

        if access_token:
            mqtt_client = PonBikeMqttClient(
                broker=MQTT_BROKER,
                port=MQTT_PORT,
                topic_prefix=MQTT_TOPIC_PREFIX,
                password=access_token,
                on_message=coordinator.handle_mqtt_message,
                client_id=f"ha-pon-bike-{entry.entry_id[:8]}",
            )
            await mqtt_client.async_start()
            _LOGGER.info("PON MQTT client started")

            # Refresh the MQTT connection before the OAuth token expires
            async def _do_token_refresh() -> None:
                try:
                    await oauth_session.async_ensure_token_valid()
                    new_token = oauth_session.token.get("access_token", "")
                    if new_token and mqtt_client is not None:
                        await mqtt_client.async_reconnect(new_token)
                        _LOGGER.debug("PON MQTT token refreshed and reconnected")
                except Exception as refresh_err:  # noqa: BLE001
                    _LOGGER.warning("PON MQTT token refresh failed: %s", refresh_err)

            @callback
            def _schedule_token_refresh(_now=None) -> None:
                hass.async_create_task(_do_token_refresh())

            cancel_token_refresh = async_track_time_interval(
                hass,
                _schedule_token_refresh,
                timedelta(seconds=MQTT_TOKEN_REFRESH_INTERVAL),
            )
        else:
            _LOGGER.warning("PON MQTT: no access token available; skipping MQTT setup")

    except Exception as mqtt_err:  # noqa: BLE001
        # MQTT is best-effort — REST polling continues even if MQTT fails to start
        _LOGGER.warning("PON MQTT setup failed (REST polling still active): %s", mqtt_err)

    hass.data[DOMAIN][entry.entry_id] = {
        "oauth_session": oauth_session,
        "api": api,
        "coordinator": coordinator,
        "mqtt_client": mqtt_client,
        "mqtt_cancel_token_refresh": cancel_token_refresh,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})

    # Cancel periodic token-refresh timer
    cancel_refresh = entry_data.get("mqtt_cancel_token_refresh")
    if cancel_refresh is not None:
        cancel_refresh()

    # Stop the MQTT client cleanly
    mqtt_client: PonBikeMqttClient | None = entry_data.get("mqtt_client")
    if mqtt_client is not None:
        await mqtt_client.async_stop()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok

