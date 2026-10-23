import asyncio
import json
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import SecretStr

from cueverge.config import FrigateMqttConfig
from cueverge.core.events import ExternalEvent
from cueverge.integrations.mqtt import FrigateMqttIngress, MqttConnection, _connect_aiomqtt


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


def test_mqtt_stop_finishes_inflight_controller_delivery_before_disconnect() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        finish = asyncio.Event()
        delivered: list[ExternalEvent] = []
        connection = FakeConnection((_payload(),))

        async def handle(event: ExternalEvent) -> None:
            started.set()
            await finish.wait()
            delivered.append(event)

        @asynccontextmanager
        async def connect(_: FrigateMqttConfig) -> AsyncIterator[MqttConnection]:
            yield connection

        ingress = FrigateMqttIngress(
            FrigateMqttConfig(enabled=True, host="loopback-example"),
            handle,
            connector=connect,
        )
        ingress.start()
        await asyncio.wait_for(started.wait(), timeout=1)
        stop = asyncio.create_task(ingress.stop())
        await asyncio.sleep(0)
        assert not stop.done()
        finish.set()
        await asyncio.wait_for(stop, timeout=1)
        assert len(delivered) == 1
        assert ingress.events_emitted == 1
        assert not ingress.connected

    asyncio.run(scenario())


@pytest.mark.parametrize("tls", [False, True])
def test_mqtt_connector_uses_explicit_tls_and_write_only_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tls: bool,
) -> None:
    captured: dict[str, Any] = {}

    class Client:
        def __init__(self, **values: Any) -> None:
            captured.update(values)

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *arguments: object) -> None:
            return None

    monkeypatch.setattr("cueverge.integrations.mqtt.aiomqtt.Client", Client)

    async def scenario() -> None:
        config = FrigateMqttConfig(
            enabled=True,
            host="loopback-example",
            username="original-test-user",
            password=SecretStr("original-test-only-password"),
            tls=tls,
        )
        async with _connect_aiomqtt(config):
            assert captured["password"] == "original-test-only-password"
            context = captured["tls_context"]
            if tls:
                assert isinstance(context, ssl.SSLContext)
                assert context.check_hostname
                assert context.verify_mode == ssl.CERT_REQUIRED
            else:
                assert context is None

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
