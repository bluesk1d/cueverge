import asyncio
import logging
import ssl
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Protocol

import aiomqtt

from cueverge.config import FrigateMqttConfig
from cueverge.core.events import ExternalEvent
from cueverge.integrations.frigate import FrigateEventError, FrigateEventNormalizer

LOGGER = logging.getLogger(__name__)
MAX_INPUT_PAYLOAD_BYTES = 256 * 1024
MAX_INPUT_RECENT_EVENTS = 20

EventHandler = Callable[[ExternalEvent], Awaitable[object]]


@dataclass(frozen=True)
class InputDelivery:
    accepted: bool
    reason: str | None
    audience_relevant: bool | None


class MqttConnection(Protocol):
    async def subscribe(self, topic: str) -> None: ...

    def messages(self) -> AsyncIterator[bytes]: ...


MqttConnector = Callable[[FrigateMqttConfig], AbstractAsyncContextManager[MqttConnection]]


class FrigateMqttIngress:
    def __init__(
        self,
        config: FrigateMqttConfig,
        handler: EventHandler,
        *,
        connector: MqttConnector | None = None,
    ) -> None:
        if not config.enabled:
            raise ValueError("Frigate MQTT ingress requires enabled configuration")
        self._config = config
        self._handler = handler
        self._connector = connector or _connect_aiomqtt
        self._normalizer = FrigateEventNormalizer(accepted_labels=config.accepted_labels)
        self._task: asyncio.Task[None] | None = None
        self._delivery_lock = asyncio.Lock()
        self.connected = False
        self.messages_received = 0
        self.invalid_messages = 0
        self.events_emitted = 0
        self.filtered_messages = 0
        self.controller_accepted = 0
        self.controller_rejected = 0
        self.audience_matches = 0
        self.connection_attempts = 0
        self.last_received_at_ms: int | None = None
        self.last_event_at_ms: int | None = None
        self.last_error: str | None = None
        self.recent: deque[dict[str, object]] = deque(maxlen=MAX_INPUT_RECENT_EVENTS)

    @property
    def config(self) -> FrigateMqttConfig:
        return self._config

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="frigate-mqtt-ingress")
        self._task.add_done_callback(self._report_failure)

    async def stop(self) -> None:
        async with self._delivery_lock:
            if self._task is None:
                return
            if not self._task.done():
                self._task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._task
            self._task = None
            self.connected = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _run(self) -> None:
        while True:
            try:
                self.connection_attempts += 1
                async with self._connector(self._config) as connection:
                    await connection.subscribe(self._config.topic)
                    self.connected = True
                    self.last_error = None
                    LOGGER.info(
                        "subscribed to Frigate MQTT topic %s on %s:%d",
                        self._config.topic,
                        self._config.host,
                        self._config.port,
                    )
                    async for payload in connection.messages():
                        await self._process(payload)
            except aiomqtt.MqttError:
                self.last_error = (
                    "Broker connection failed. Check the hostname, port, TLS and credentials; "
                    "reconnecting automatically."
                )
                LOGGER.warning("Frigate MQTT connection failed; reconnecting")
            finally:
                self.connected = False
            await asyncio.sleep(self._config.reconnect_delay_ms / 1000)

    async def _process(self, payload: bytes) -> None:
        self.messages_received += 1
        self.last_received_at_ms = time.time_ns() // 1_000_000
        if len(payload) > MAX_INPUT_PAYLOAD_BYTES:
            self.invalid_messages += 1
            self.recent.append(
                {"received_at_ms": self.last_received_at_ms, "reason": "payload_too_large"}
            )
            LOGGER.warning("rejected oversized Frigate MQTT payload")
            return
        try:
            events = self._normalizer.normalize(payload, received_at_ms=self.last_received_at_ms)
        except FrigateEventError:
            self.invalid_messages += 1
            self.recent.append(
                {"received_at_ms": self.last_received_at_ms, "reason": "invalid_payload"}
            )
            LOGGER.warning("rejected invalid Frigate MQTT payload")
            return
        if not events:
            self.filtered_messages += 1
            self.recent.append(
                {"received_at_ms": self.last_received_at_ms, "reason": "label_filtered"}
            )
        for event in events:
            self.last_event_at_ms = event.occurred_at_ms
            receipt: dict[str, object] = {
                "received_at_ms": self.last_received_at_ms,
                "occurred_at_ms": event.occurred_at_ms,
                "event_type": event.event_type,
                "camera": str(event.attributes.get("camera", ""))[:128],
                "zone": str(event.attributes.get("zone", ""))[:128],
                "label": str(event.attributes.get("label", ""))[:128],
            }
            try:
                async with self._delivery_lock:
                    result = await self._handler(event)
            except RuntimeError:
                self.controller_rejected += 1
                self.recent.append(
                    {**receipt, "reason": "controller_unavailable", "accepted": False}
                )
                LOGGER.warning("controller rejected a Frigate event")
                continue
            self.events_emitted += 1
            if isinstance(result, InputDelivery):
                self.controller_accepted += int(result.accepted)
                self.controller_rejected += int(not result.accepted)
                self.audience_matches += int(result.audience_relevant is True)
                receipt.update(
                    accepted=result.accepted,
                    reason=result.reason,
                    audience_relevant=result.audience_relevant,
                )
            self.recent.append(receipt)

    def _report_failure(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            self.last_error = (
                "Camera event listener stopped unexpectedly. Save the connection again "
                "or ask the operator to restart it."
            )
            LOGGER.critical("Frigate MQTT ingress stopped unexpectedly")


class _AiomqttConnection:
    def __init__(self, client: aiomqtt.Client) -> None:
        self._client = client

    async def subscribe(self, topic: str) -> None:
        await self._client.subscribe(topic)

    async def messages(self) -> AsyncIterator[bytes]:
        async for message in self._client.messages:
            yield bytes(message.payload)


@asynccontextmanager
async def _connect_aiomqtt(config: FrigateMqttConfig) -> AsyncIterator[MqttConnection]:
    password = config.password.get_secret_value() if config.password is not None else None
    async with aiomqtt.Client(
        hostname=config.host or "",
        port=config.port,
        username=config.username,
        password=password,
        tls_context=ssl.create_default_context() if config.tls else None,
    ) as client:
        yield _AiomqttConnection(client)
