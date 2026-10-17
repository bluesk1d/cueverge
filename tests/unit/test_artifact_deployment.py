import asyncio
import hashlib
import io
import json
import ssl
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

from cueverge.gui.artifact_deployment import ArtifactDeploymentService
from cueverge.gui.config import GuiConfig
from cueverge.gui.device_session import DeviceSessionRegistry
from cueverge.gui.repository import GuiRepository
from cueverge.media_agent.device_presence import DevicePresenceClient
from cueverge.protocol.device_v2 import (
    DeviceArtifactActivateAck,
    DeviceArtifactActivateCommand,
    DeviceArtifactStageAck,
    DeviceArtifactStageCommand,
)


def test_deployment_stages_every_member_before_group_activation() -> None:
    async def scenario() -> None:
        sessions = _FixtureSessions()
        service = ArtifactDeploymentService(
            cast(
                GuiConfig,
                SimpleNamespace(
                    device_listener_public_url=(
                        "wss://controller.example:7443/api/device/v2/agent/ws"
                    )
                ),
            ),
            cast(GuiRepository, _FixtureRepository()),
            cast(DeviceSessionRegistry, sessions),
        )

        deployment = asyncio.create_task(service.deploy(ACTIVATION))
        await sessions.all_stages.wait()
        assert sessions.activation_commands == []
        sessions.release_stages.set()
        result = await deployment

        assert result.activation_identity == ACTIVATION
        assert result.resources == ("projector.left", "projector.right")
        assert len(sessions.stage_commands) == 2
        assert len(sessions.activation_commands) == 4
        assert [command.phase for command in sessions.activation_commands] == [
            "prepare",
            "prepare",
            "commit",
            "commit",
        ]
        activation_times = {
            command.activation_at_unix_ms for command in sessions.activation_commands
        }
        assert len(activation_times) == 1
        assert all(
            str(command.download_url).startswith(
                f"https://controller.example:7443/api/device/v2/render-artifacts/{ACTIVATION}/"
            )
            for command in sessions.stage_commands
        )

    asyncio.run(scenario())


def test_agent_resumes_verifies_and_activates_cached_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    content = b"complete-render-artifact"
    digest = hashlib.sha256(content).hexdigest()
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    partial = asset_root / f".{digest}.part"
    partial.write_bytes(content[:8])
    observed_range: list[str | None] = []

    def open_request(request, **_):
        observed_range.append(request.headers.get("Range"))
        return _FixtureResponse(content[8:], 206)

    monkeypatch.setattr("urllib.request.urlopen", open_request)
    client = DevicePresenceClient.__new__(DevicePresenceClient)
    client.ssl_context = ssl.create_default_context()
    client.media_client = SimpleNamespace(config=SimpleNamespace(asset_root=asset_root))
    command = DeviceArtifactStageCommand(
        request_id=uuid4(),
        device_session_id=uuid4(),
        controller_epoch=uuid4(),
        activation_identity=UUID(ACTIVATION),
        resource="projector.left",
        digest_sha256=digest,
        size_bytes=len(content),
        download_url="https://controller.example/artifact",
    )

    client._stage_artifact(command)

    assert observed_range == ["bytes=8-"]
    assert (asset_root / digest).read_bytes() == content
    cache_state = json.loads(
        (asset_root / ".cueverge-cache-state.json").read_text(encoding="utf-8")
    )
    assert cache_state["supports_resumable_transfer"] is True
    assert digest in cache_state["pinned_digests"]

    activate = DeviceArtifactActivateCommand(
        request_id=uuid4(),
        device_session_id=command.device_session_id,
        controller_epoch=command.controller_epoch,
        activation_identity=command.activation_identity,
        resource=command.resource,
        digest_sha256=digest,
        media_end_behavior="hold_last_frame",
        duration_ms=1000,
        frame_rate_numerator=30,
        frame_rate_denominator=1,
        frame_time_origin_ms=0,
    )
    client._activate_artifact(activate)
    activation = json.loads((asset_root / ".cueverge-activation.json").read_text(encoding="utf-8"))
    assert activation["activation_identity"] == ACTIVATION
    assert activation["digest_sha256"] == digest
    assert activation["media_end_behavior"] == "hold_last_frame"
    assert activation["duration_ms"] == 1000
    assert activation["frame_rate"] == {"numerator": 30, "denominator": 1}


def test_agent_evicts_only_unprotected_lru_and_redownloads_corrupt_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    content = b"replacement-render-artifact"
    digest = hashlib.sha256(content).hexdigest()
    rollback_content = b"rollback-critical"
    rollback_digest = hashlib.sha256(rollback_content).hexdigest()
    evictable_content = b"old-unpinned" * 10
    evictable_digest = hashlib.sha256(evictable_content).hexdigest()
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    (asset_root / rollback_digest).write_bytes(rollback_content)
    (asset_root / evictable_digest).write_bytes(evictable_content)
    (asset_root / digest).write_bytes(b"corrupt")
    (asset_root / ".cueverge-cache-state.json").write_text(
        json.dumps(
            {
                "supports_pinning": True,
                "supports_resumable_transfer": True,
                "pinned_digests": [rollback_digest, evictable_digest],
                "transfers": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "cueverge.media_agent.device_presence.shutil.disk_usage",
        lambda _: SimpleNamespace(total=100, used=100, free=0),
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _FixtureResponse(content, 200),
    )
    client = DevicePresenceClient.__new__(DevicePresenceClient)
    client.ssl_context = ssl.create_default_context()
    client.media_client = SimpleNamespace(config=SimpleNamespace(asset_root=asset_root))
    command = DeviceArtifactStageCommand(
        request_id=uuid4(),
        device_session_id=uuid4(),
        controller_epoch=uuid4(),
        activation_identity=UUID(ACTIVATION),
        resource="projector.left",
        digest_sha256=digest,
        size_bytes=len(content),
        download_url="https://controller.example/artifact",
        protected_digests=(rollback_digest,),
    )

    client._stage_artifact(command)

    assert (asset_root / digest).read_bytes() == content
    assert (asset_root / rollback_digest).read_bytes() == rollback_content
    assert not (asset_root / evictable_digest).exists()
    state = json.loads((asset_root / ".cueverge-cache-state.json").read_text(encoding="utf-8"))
    assert state["pinned_digests"] == sorted([digest, rollback_digest])


def test_agent_two_phase_activation_restores_previous_state_on_rollback(
    tmp_path: Path,
) -> None:
    content = b"candidate"
    digest = hashlib.sha256(content).hexdigest()
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    (asset_root / digest).write_bytes(content)
    previous_state = {
        "activation_identity": "87654321-4321-6789-a234-567812345678",
        "resource": "projector.left",
        "digest_sha256": "f" * 64,
    }
    (asset_root / ".cueverge-activation.json").write_text(
        json.dumps(previous_state),
        encoding="utf-8",
    )
    client = DevicePresenceClient.__new__(DevicePresenceClient)
    client.media_client = SimpleNamespace(config=SimpleNamespace(asset_root=asset_root))
    activation_at = time.time_ns() // 1_000_000
    command = DeviceArtifactActivateCommand(
        request_id=uuid4(),
        device_session_id=uuid4(),
        controller_epoch=uuid4(),
        activation_identity=UUID(ACTIVATION),
        resource="projector.left",
        digest_sha256=digest,
        media_end_behavior="hold_last_frame",
        duration_ms=1000,
        frame_rate_numerator=30,
        frame_rate_denominator=1,
        frame_time_origin_ms=0,
        phase="prepare",
        activation_at_unix_ms=activation_at,
    )

    client._activate_artifact(command)
    client._activate_artifact(
        command.model_copy(
            update={"phase": "commit", "request_id": uuid4()},
        )
    )
    assert (
        json.loads((asset_root / ".cueverge-activation.json").read_text(encoding="utf-8"))[
            "digest_sha256"
        ]
        == digest
    )

    client._activate_artifact(
        command.model_copy(
            update={
                "phase": "rollback",
                "request_id": uuid4(),
                "activation_at_unix_ms": None,
            }
        )
    )
    assert (
        json.loads((asset_root / ".cueverge-activation.json").read_text(encoding="utf-8"))
        == previous_state
    )


ACTIVATION = "12345678-1234-5678-9234-567812345678"


class _FixtureRepository:
    def get_render_artifact_manifest(self, artifact_id: str):
        assert artifact_id == ACTIVATION
        return {
            "activation_identity": ACTIVATION,
            "media_end_behavior": "hold_last_frame",
            "duration_ms": 1000,
            "frame_rate": {"numerator": 30, "denominator": 1},
            "frame_time_origin_ms": 0,
            "members": [
                {
                    "resource": "projector.left",
                    "digest_sha256": "a" * 64,
                    "size_bytes": 100,
                },
                {
                    "resource": "projector.right",
                    "digest_sha256": "b" * 64,
                    "size_bytes": 200,
                },
            ],
        }

    def list_resource_assignments(self):
        return (
            SimpleNamespace(resource="projector.left", device_id=str(LEFT_DEVICE)),
            SimpleNamespace(resource="projector.right", device_id=str(RIGHT_DEVICE)),
        )


LEFT_DEVICE = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
RIGHT_DEVICE = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


class _FixtureSessions:
    controller_epoch = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

    def __init__(self) -> None:
        self.stage_commands: list[DeviceArtifactStageCommand] = []
        self.activation_commands: list[DeviceArtifactActivateCommand] = []
        self.release_stages = asyncio.Event()
        self.all_stages = asyncio.Event()

    async def request(self, device_id, request_id, message, **_):
        if isinstance(message, DeviceArtifactStageCommand):
            self.stage_commands.append(message)
            if len(self.stage_commands) == 2:
                self.all_stages.set()
            await self.release_stages.wait()
            return DeviceArtifactStageAck(
                request_id=request_id,
                device_session_id=uuid4(),
                controller_epoch=self.controller_epoch,
                activation_identity=message.activation_identity,
                digest_sha256=message.digest_sha256,
                status="completed",
            )
        self.activation_commands.append(message)
        assert isinstance(message, DeviceArtifactActivateCommand)
        return DeviceArtifactActivateAck(
            request_id=request_id,
            device_session_id=uuid4(),
            controller_epoch=self.controller_epoch,
            activation_identity=message.activation_identity,
            status="completed",
        )


class _FixtureResponse:
    def __init__(self, content: bytes, status: int) -> None:
        self.content = io.BytesIO(content)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, size: int) -> bytes:
        return self.content.read(size)
