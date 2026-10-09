import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from cueverge.config import FrigateMqttConfig
from cueverge.core.events import ExternalEvent
from cueverge.integrations.mqtt import FrigateMqttIngress, MqttConnection


@dataclass
class FakeConnection:
    payloads: tuple[bytes, ...]
    subscribed_topics: list[str] = field(default_factory=list)

    async def subscribe(self, topic: str) -> None:
        self.subscribed_topics.append(topic)

    async def messages(self) -> AsyncIterator[bytes]:
        for payload in self.payloads:
            yield payload
        await asyncio.Event().wait()


def test_mqtt_ingress_normalizes_messages_and_stops_cleanly() -> None:
    async def scenario() -> None:
        connection = FakeConnection((_payload(),))
        received: list[ExternalEvent] = []
        delivered = asyncio.Event()

        async def handle(event: ExternalEvent) -> None:
            received.append(event)
            delivered.set()

        @asynccontextmanager
        async def connect(_: FrigateMqttConfig) -> AsyncIterator[MqttConnection]:
            yield connection

        ingress = FrigateMqttIngress(
            FrigateMqttConfig(enabled=True, host="mqtt.local", reconnect_delay_ms=100),
            handle,
            connector=connect,
        )
        ingress.start()
        await asyncio.wait_for(delivered.wait(), timeout=1)
        await ingress.stop()

        assert connection.subscribed_topics == ["frigate/events"]
        assert [event.event_type for event in received] == ["frigate.object.new"]
        assert ingress.messages_received == 1
        assert ingress.events_emitted == 1
        assert not ingress.connected

    asyncio.run(scenario())


def test_mqtt_ingress_rejects_malformed_payload_and_continues() -> None:
    async def scenario() -> None:
        connection = FakeConnection((b"not-json", _payload()))
        received: list[ExternalEvent] = []
        delivered = asyncio.Event()

        async def handle(event: ExternalEvent) -> None:
            received.append(event)
            delivered.set()

        @asynccontextmanager
        async def connect(_: FrigateMqttConfig) -> AsyncIterator[MqttConnection]:
            yield connection

        ingress = FrigateMqttIngress(
            FrigateMqttConfig(enabled=True, host="mqtt.local", reconnect_delay_ms=100),
            handle,
            connector=connect,
        )
        ingress.start()
        await asyncio.wait_for(delivered.wait(), timeout=1)
        await ingress.stop()

        assert len(received) == 1
        assert ingress.messages_received == 2
        assert ingress.invalid_messages == 1

    asyncio.run(scenario())


def _payload() -> bytes:
    return json.dumps(
        {
            "type": "new",
            "after": {
                "id": "object-1",
                "label": "person",
                "camera": "example",
                "start_time": 10.0,
                "frame_time": 10.0,
                "entered_zones": [],
                "current_zones": [],
            },
        }
    ).encode()
