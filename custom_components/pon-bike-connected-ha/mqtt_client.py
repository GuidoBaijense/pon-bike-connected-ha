from __future__ import annotations

import asyncio
import json
import logging
import ssl
from collections.abc import Callable
from typing import Any

import aiomqtt

_LOGGER = logging.getLogger(__name__)

# Topic suffixes published by the PON broker (see documentation)
_TOPIC_SUFFIXES = ("iot-telemetry", "bike-telemetry", "location")


class PonBikeMqttClient:
    """Persistent MQTT client for PON Bike Connected real-time events.

    Connects to the PON MQTT broker with TLS and an OAuth2 JWT token as the
    password, subscribing to all three topic families for every authorised bike.
    Reconnects automatically with exponential back-off on failure.
    """

    def __init__(
        self,
        broker: str,
        port: int,
        topic_prefix: str,
        password: str,
        on_message: Callable[[str, str, dict[str, Any]], None],
        client_id: str | None = None,
    ) -> None:
        self._broker = broker
        self._port = port
        self._topic_prefix = topic_prefix
        self._password = password
        self._on_message = on_message
        self._client_id = client_id
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Public lifecycle methods
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """Start the MQTT listener in a background asyncio task."""
        self._stop_event.clear()
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def async_stop(self) -> None:
        """Gracefully stop the MQTT listener."""
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._task = None

    async def async_reconnect(self, new_password: str) -> None:
        """Reconnect with a refreshed OAuth2 token."""
        _LOGGER.debug("PON MQTT reconnecting with refreshed token")
        self._password = new_password
        await self.async_stop()
        await self.async_start()

    # ------------------------------------------------------------------
    # Internal implementation
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Connect to the broker, subscribe, and consume messages.

        Retries with exponential back-off (5 s → 10 s → … → 300 s) on any
        connection or protocol error. Stops cleanly when _stop_event is set.
        """
        backoff = 5
        while not self._stop_event.is_set():
            try:
                tls_context = ssl.create_default_context()
                async with aiomqtt.Client(
                    hostname=self._broker,
                    port=self._port,
                    username="token",  # bearer-token auth: username is a placeholder
                    password=self._password,
                    tls_context=tls_context,
                    client_id=self._client_id,
                    timeout=30,
                ) as client:
                    for suffix in _TOPIC_SUFFIXES:
                        await client.subscribe(
                            f"{self._topic_prefix}/+/{suffix}", qos=0
                        )
                    _LOGGER.info(
                        "PON MQTT connected to %s:%s and subscribed to all topics",
                        self._broker,
                        self._port,
                    )
                    backoff = 5  # reset after a successful connect

                    async for message in client.messages:
                        if self._stop_event.is_set():
                            return
                        self._dispatch(str(message.topic), bytes(message.payload))

            except asyncio.CancelledError:
                return
            except Exception as err:  # noqa: BLE001
                if self._stop_event.is_set():
                    return
                _LOGGER.warning(
                    "PON MQTT error: %s — retrying in %s s.", err, backoff
                )
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    return
                backoff = min(backoff * 2, 300)

    def _dispatch(self, topic: str, raw: bytes) -> None:
        """Parse the topic and JSON payload and hand off to the on_message callback.

        Expected topic format: ``{prefix}/{bikeId}/{suffix}``
        """
        parts = topic.split("/")
        if len(parts) != 3:
            _LOGGER.debug("Unexpected MQTT topic structure: %s", topic)
            return
        _, bike_id, suffix = parts
        try:
            data: dict[str, Any] = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as err:
            _LOGGER.debug("Failed to parse MQTT payload on %s: %s", topic, err)
            return
        self._on_message(bike_id, suffix, data)
