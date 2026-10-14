import asyncio
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

from cueverge.media_agent.device_enrollment import StoredDeviceIdentity
from cueverge.media_agent.device_presence import DevicePresenceClient
from cueverge.protocol.device_v2 import (
    DeviceCapabilityDocument,
    DeviceHello,
    DeviceKind,
    DeviceWelcome,
    MediaPlaybackCapability,
    ProtocolRange,
)

DEVICE_ID = UUID("9c004799-7704-48f1-b5f4-f083628c7faa")


class FakeWebSocket:
    def __init__(self, welcome: DeviceWelcome) -> None:
        self.messages = [
            welcome.model_dump_json(),
            json.dumps(
                {
                    "type": "device.heartbeat.ack",
                    "protocol_version": 2,
                    "sequence": 0,
                    "accepted": True,
                    "reason": None,
                }
            ),
        ]
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if not self.messages:
            raise RuntimeError("no queued message")
        return self.messages.pop(0)

    def __aiter__(self) -> "FakeWebSocket":
        return self

    async def __anext__(self) -> str:
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)


def test_presence_client_uses_enrolled_identity_and_fenced_heartbeats() -> None:
    asyncio.run(_presence_scenario())


async def _presence_scenario() -> None:
    client = object.__new__(DevicePresenceClient)
    client.identity = StoredDeviceIdentity(
        device_id=DEVICE_ID,
        enrollment_url="https://controller/api/device/v2/enroll",
        capabilities=_capabilities(),
    )
    client.media_client = SimpleNamespace(
        agent_session_id="",
        controller_epoch=None,
    )
    welcome = DeviceWelcome(
        device_id=DEVICE_ID,
        controller_epoch=uuid4(),
        heartbeat_interval_ms=1,
        heartbeat_timeout_ms=100,
    )
    websocket = FakeWebSocket(welcome)

    await client._run_connection(websocket)  # type: ignore[arg-type]

    hello = DeviceHello.model_validate_json(websocket.sent[0])
    assert hello.device_id == DEVICE_ID
    assert hello.capabilities == _capabilities()
    assert client.media_client.agent_session_id == str(hello.device_session_id)
    assert client.media_client.controller_epoch == str(welcome.controller_epoch)


def _capabilities() -> DeviceCapabilityDocument:
    return DeviceCapabilityDocument(
        protocol=ProtocolRange(minimum=2, maximum=2),
        device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
        software_version="0.1.0",
        capabilities=(MediaPlaybackCapability(codecs=("h264",), containers=("mp4",)),),
    )
