import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

from cueverge.media_agent.agent_update import AgentInstallation
from cueverge.media_agent.device_enrollment import StoredDeviceIdentity
from cueverge.media_agent.device_presence import (
    DevicePresenceClient,
    calculate_timing_metrics,
    scan_artifact_inventory,
)
from cueverge.protocol.device_v2 import (
    DeviceCapabilityDocument,
    DeviceHello,
    DeviceKind,
    DeviceWelcome,
    KeepAwakeStatus,
    MediaPlaybackCapability,
    ProtocolRange,
)

DEVICE_ID = UUID("9c004799-7704-48f1-b5f4-f083628c7faa")


class FakeWebSocket:
    def __init__(self, welcome: DeviceWelcome) -> None:
        self.messages = [
            welcome.model_dump_json(),
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


def test_calculates_round_trip_and_clock_offset() -> None:
    round_trip_ms, clock_offset_ms = calculate_timing_metrics(
        sent_monotonic_ms=1_000,
        sent_unix_ms=100_000,
        received_monotonic_ms=1_042,
        received_unix_ms=100_042,
        controller_receive_unix_ms=100_030,
        controller_send_unix_ms=100_032,
    )

    assert round_trip_ms == 40
    assert clock_offset_ms == 10


def test_scans_content_addressed_inventory_without_paths(tmp_path: Path) -> None:
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    (asset_root / "first.mp4").write_bytes(b"same-content")
    (asset_root / "duplicate.mp4").write_bytes(b"same-content")
    digest = hashlib.sha256(b"same-content").hexdigest()
    (asset_root / ".cueverge-cache-state.json").write_text(
        json.dumps(
            {
                "supports_pinning": True,
                "supports_resumable_transfer": True,
                "pinned_digests": [digest],
                "transfers": [
                    {
                        "digest_sha256": "e" * 64,
                        "received_bytes": 500,
                        "total_bytes": 1000,
                        "state": "active",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    total, free, supports_pinning, supports_resume, artifacts, transfers, faults = (
        scan_artifact_inventory(asset_root)
    )

    assert total > 0
    assert free >= 0
    assert supports_pinning
    assert supports_resume
    assert len(artifacts) == 1
    assert artifacts[0].size_bytes == len(b"same-content")
    assert artifacts[0].pinned
    assert transfers[0].received_bytes == 500
    assert faults == ()


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
        media_features=("seamless_transition", "repeat_count"),
    )
    client._heartbeat_sent = {}
    client.player_host = None
    keep_awake = KeepAwakeStatus(mode="display", active=True)
    client.keep_awake = SimpleNamespace(status=keep_awake)  # type: ignore[assignment]
    client.installation = AgentInstallation(None, None)
    client.settings = None
    client.displays = ()
    client.audio_devices = ()
    client._settings_reported = None
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
    assert hello.media_features == ("seamless_transition", "repeat_count")
    assert hello.keep_awake == keep_awake
    assert hello.agent_release is not None and not hello.agent_release.managed
    assert client.media_client.agent_session_id == str(hello.device_session_id)
    assert client.media_client.controller_epoch == str(welcome.controller_epoch)


def _capabilities() -> DeviceCapabilityDocument:
    return DeviceCapabilityDocument(
        protocol=ProtocolRange(minimum=2, maximum=2),
        device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
        software_version="0.1.0",
        capabilities=(MediaPlaybackCapability(codecs=("h264",), containers=("mp4",)),),
    )


def test_failed_agent_update_reports_error_and_restores_the_player() -> None:
    from cueverge.media_agent.agent_update import AgentUpdateError
    from cueverge.protocol.device_v2 import DeviceAgentUpdateCommand

    calls: list[str] = []

    class Host:
        state = "ready"

        async def stop_player(self) -> str:
            calls.append("stop")
            return "stopped"

        async def start(self) -> str:
            calls.append("start")
            return "ready"

    class FailingInstallation:
        managed = True

        def install(self, command: object, context: object) -> None:
            raise AgentUpdateError("disk full")

    client = object.__new__(DevicePresenceClient)
    client.player_host = Host()  # type: ignore[assignment]
    client.installation = FailingInstallation()  # type: ignore[assignment]
    client.ssl_context = None  # type: ignore[assignment]
    client.media_client = SimpleNamespace(_clear_playback=lambda: calls.append("clear"))
    welcome = DeviceWelcome(
        device_id=DEVICE_ID,
        controller_epoch=uuid4(),
        heartbeat_interval_ms=1,
        heartbeat_timeout_ms=100,
    )
    session_id = uuid4()
    command = DeviceAgentUpdateCommand(
        request_id=uuid4(),
        device_session_id=session_id,
        controller_epoch=welcome.controller_epoch,
        version="0.2.0",
        platform="linux_x86_64",
        digest_sha256="c" * 64,
        size_bytes=10,
        download_url="https://controller:7443/api/device/v2/agent-bundles/" + "c" * 64,
    )
    ack = asyncio.run(client._update_agent(command, session_id, welcome))
    assert (ack.status, ack.error) == ("error", "disk full")
    assert calls == ["clear", "stop", "start"]
