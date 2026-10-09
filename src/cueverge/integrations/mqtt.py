import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from typing import Protocol

import aiomqtt

from cueverge.config import FrigateMqttConfig
from cueverge.core.events import ExternalEvent
from cueverge.integrations.frigate import FrigateEventError, FrigateEventNormalizer

LOGGER = logging.getLogger(__name__)

EventHandler = Callable[[ExternalEvent], Awaitable[object]]


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
        self.connected = False
        self.messages_received = 0
        self.invalid_messages = 0
        self.events_emitted = 0

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="frigate-mqtt-ingress")
        self._task.add_done_callback(self._report_failure)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        self.connected = False

    async def _run(self) -> None:
        while True:
            try:
                async with self._connector(self._config) as connection:
                    await connection.subscribe(self._config.topic)
                    self.connected = True
                    LOGGER.info(
                        "subscribed to Frigate MQTT topic %s on %s:%d",
                        self._config.topic,
                        self._config.host,
                        self._config.port,
                    )
                    async for payload in connection.messages():
                        await self._process(payload)
            except aiomqtt.MqttError:
                LOGGER.exception(
                    "Frigate MQTT connection failed for %s:%d",
                    self._config.host,
                    self._config.port,
                )
            finally:
                self.connected = False
            await asyncio.sleep(self._config.reconnect_delay_ms / 1000)

    async def _process(self, payload: bytes) -> None:
        self.messages_received += 1
        try:
            events = self._normalizer.normalize(payload, received_at_ms=time.time_ns() // 1_000_000)
        except FrigateEventError:
            self.invalid_messages += 1
            LOGGER.exception("rejected invalid Frigate MQTT payload")
            return
        for event in events:
            try:
                await self._handler(event)
            except RuntimeError as error:
                LOGGER.warning("controller rejected Frigate event %s: %s", event.event_id, error)
                continue
            self.events_emitted += 1

    @staticmethod
    def _report_failure(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            LOGGER.critical("Frigate MQTT ingress stopped unexpectedly", exc_info=task.exception())


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
    ) as client:
        yield _AiomqttConnection(client)
