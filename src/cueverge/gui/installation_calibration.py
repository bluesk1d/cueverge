"""Installation timing calibration: reference clips, delivery, and the live run's lifecycle."""

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import numpy as np
from pydantic import AnyUrl, TypeAdapter

from cueverge.controller.timing_calibration import (
    DEFAULT_FLASH_MS,
    DEFAULT_PERIOD_MS,
    CalibrationMediaTarget,
    TimingCalibrationRunError,
    TimingCalibrationRunStatus,
)
from cueverge.core.models import SceneCatalog
from cueverge.gui import media_tools
from cueverge.gui.artifact_deployment import device_protected_digests
from cueverge.gui.contracts import (
    CalibrationOutputResponse,
    OutputTimingResponse,
    Role,
    TimingCalibrationRunResponse,
)
from cueverge.gui.device_players import DevicePlayerError, DevicePlayerService
from cueverge.gui.device_session import DeviceSessionError, DeviceSessionRegistry
from cueverge.gui.installation_timing import (
    InstallationTimingError,
    InstallationTimingService,
    OutputTiming,
    OutputTimingKind,
    timing_kind,
    validate_delay,
)
from cueverge.gui.repository import GuiRepository
from cueverge.protocol.device_v2 import DeviceArtifactStageAck, DeviceArtifactStageCommand

CLIP_VERSION = 1
CLIP_SIZE = (1280, 720)
CLIP_FPS = 30
PLAYER_READY_SECONDS = 15.0
ClipVariant = Literal["click", "silent"]
CLIP_VARIANTS: tuple[ClipVariant, ...] = ("click", "silent")


class InstallationCalibrationError(RuntimeError):
    pass


class CalibrationRuntime(Protocol):
    @property
    def catalog(self) -> SceneCatalog: ...

    def status(self) -> dict[str, object]: ...

    def set_output_timing(self, delays: dict[str, int]) -> None: ...

    def start_timing_calibration(
        self,
        *,
        media: tuple[CalibrationMediaTarget, ...],
        lights: tuple[str, ...],
        delays: dict[str, int],
    ) -> TimingCalibrationRunStatus: ...

    def timing_calibration_status(self) -> TimingCalibrationRunStatus | None: ...

    def set_timing_calibration_delay(
        self, resource: str, delay_ms: int
    ) -> TimingCalibrationRunStatus: ...

    def resync_timing_calibration(self) -> TimingCalibrationRunStatus: ...

    async def stop_timing_calibration(
        self, *, reason: str = "operator"
    ) -> TimingCalibrationRunStatus | None: ...


@dataclass(frozen=True)
class ReferenceClip:
    variant: ClipVariant
    digest_sha256: str
    size_bytes: int
    path: Path


@dataclass(frozen=True)
class MediaChoice:
    resource: str
    click: bool = True


def reference_clip_root(data_root: Path) -> Path:
    return data_root / "timing-calibration"


def ensure_reference_clips(root: Path) -> dict[ClipVariant, ReferenceClip]:
    """The beat clips (white flash, optional click) stored by digest; generated once."""
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / f"reference-v{CLIP_VERSION}.json"
    clips: dict[ClipVariant, ReferenceClip] = {}
    if manifest_path.is_file():
        stored = json.loads(manifest_path.read_text())
        for variant in CLIP_VARIANTS:
            item = stored.get(variant, {})
            path = root / str(item.get("digest_sha256", ""))
            if path.is_file() and path.stat().st_size == item.get("size_bytes"):
                clips[variant] = ReferenceClip(variant, path.name, path.stat().st_size, path)
    for variant in CLIP_VARIANTS:
        if variant not in clips:
            clips[variant] = _render_clip(root, variant)
    manifest_path.write_text(
        json.dumps(
            {
                variant: {"digest_sha256": clip.digest_sha256, "size_bytes": clip.size_bytes}
                for variant, clip in clips.items()
            },
            sort_keys=True,
        )
    )
    return clips


def _render_clip(root: Path, variant: ClipVariant) -> ReferenceClip:
    width, height = CLIP_SIZE
    frame_count = DEFAULT_PERIOD_MS * CLIP_FPS // 1000
    flash_frames = max(1, round(DEFAULT_FLASH_MS * CLIP_FPS / 1000))
    frames: list[np.ndarray] = []
    for index in range(frame_count):
        if index < flash_frames:
            frame = np.full((height, width, 3), 255, dtype=np.uint8)
        else:
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            # A sweeping bar shows the loop is running and where the next flash lands.
            frame[height - 24 :, : round(width * index / frame_count)] = 70
        frames.append(frame)
    samples = np.zeros((DEFAULT_PERIOD_MS * 48, 2), dtype=np.float32)
    if variant == "click":
        burst = np.arange(480) / 48_000
        click = 0.8 * np.sin(2 * np.pi * 2000 * burst) * np.exp(-burst * 600)
        samples[: click.shape[0], 0] = click
        samples[: click.shape[0], 1] = click
    partial = root / f".reference-{variant}-{uuid4().hex}.mp4"
    media_tools.encode_mp4(
        frames=tuple(frames),
        audio_samples=samples,
        frame_rate=Fraction(CLIP_FPS),
        video_profile="main",
        video_level="4.0",
        pixel_format="yuv420p",
        video_bitrate_kbps=1500,
        keyframe_interval_frames=CLIP_FPS,
        b_frames=0,
        target=partial,
    )
    digest = hashlib.sha256(partial.read_bytes()).hexdigest()
    path = root / digest
    partial.replace(path)
    return ReferenceClip(variant, digest, path.stat().st_size, path)


class InstallationCalibrationService:
    """Runs one live calibration for the runtime installation and saves accepted delays."""

    def __init__(
        self,
        *,
        repository: GuiRepository,
        runtime: CalibrationRuntime,
        sessions: DeviceSessionRegistry,
        players: DevicePlayerService,
        timing: InstallationTimingService,
        clip_root: Path,
        public_url: str | None,
    ) -> None:
        self.repository = repository
        self.runtime = runtime
        self.sessions = sessions
        self.players = players
        self.timing = timing
        self.clip_root = clip_root
        self.public_url = public_url
        self._lock = asyncio.Lock()
        self._installation_id: str | None = None

    # Saved timing ------------------------------------------------------------------------

    def outputs(self) -> tuple[tuple[str, OutputTimingKind], ...]:
        """The runtime installation's outputs that take timing, in installation order."""
        result: list[tuple[str, OutputTimingKind]] = []
        for binding in self.runtime.catalog.installation.resources:
            kind = timing_kind(binding.kind.value)
            if kind is not None:
                result.append((binding.resource, kind))
        return tuple(result)

    def saved(self, installation_id: str) -> tuple[OutputTiming, ...]:
        return self.timing.list(installation_id, self.outputs())

    def push_to_runtime(self) -> None:
        installation_id = self.repository.get_runtime_installation().id
        self.runtime.set_output_timing(self.timing.delays(installation_id))

    def set_saved(
        self,
        *,
        installation_id: str,
        resource: str,
        delay_ms: int,
        expected_revision: int | None,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> OutputTiming:
        kind = dict(self.outputs()).get(resource)
        if kind is None:
            raise InstallationTimingError(f"{resource} is not a projector or LED output")
        saved = self.timing.set(
            installation_id=installation_id,
            resource=resource,
            kind=kind,
            delay_ms=delay_ms,
            expected_revision=expected_revision,
            actor=actor,
            role=role,
            occurred_at=occurred_at,
        )
        if installation_id == self.repository.get_runtime_installation().id:
            self.push_to_runtime()
        return saved

    # Live run ----------------------------------------------------------------------------

    def status(self) -> TimingCalibrationRunStatus | None:
        return self.runtime.timing_calibration_status()

    async def start(
        self,
        *,
        installation_id: str,
        media: tuple[MediaChoice, ...],
        lights: tuple[str, ...],
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> TimingCalibrationRunStatus:
        async with self._lock:
            if installation_id != self.repository.get_runtime_installation().id:
                raise InstallationCalibrationError(
                    "Timing calibration runs on the installation the controller is using"
                )
            kinds = dict(self.outputs())
            for choice in media:
                if kinds.get(choice.resource) != "media":
                    raise InstallationCalibrationError(f"{choice.resource} is not a projector")
            for resource in lights:
                if kinds.get(resource) != "lighting":
                    raise InstallationCalibrationError(f"{resource} is not an LED output")
            if not media and not lights:
                raise InstallationCalibrationError("Choose at least one projector or LED output")
            if len({choice.resource for choice in media}) != len(media):
                raise InstallationCalibrationError("Each projector can be chosen once")
            current = self.runtime.timing_calibration_status()
            if current is not None and current.state in {"starting", "active"}:
                raise InstallationCalibrationError("A timing calibration is already running")
            status = self.runtime.status()
            operations = status.get("operations")
            mode = operations.get("mode") if isinstance(operations, dict) else None
            if mode != "maintenance" or status.get("active_scene") is not None:
                raise InstallationCalibrationError(
                    "Switch to maintenance mode with nothing playing before calibrating"
                )
            clips = await asyncio.to_thread(ensure_reference_clips, self.clip_root)
            targets = await asyncio.gather(
                *(self._prepare_projector(choice, clips) for choice in media)
            )
            try:
                run = self.runtime.start_timing_calibration(
                    media=tuple(targets),
                    lights=tuple(lights),
                    delays=self.timing.delays(installation_id),
                )
            except TimingCalibrationRunError as error:
                raise InstallationCalibrationError(str(error)) from error
            self._installation_id = installation_id
            self.repository.append_audit(
                actor=actor,
                action="installation.timing_calibration.start",
                target=installation_id,
                result="success",
                occurred_at=occurred_at,
                authorization_context={
                    "role": role.value,
                    "run_id": run.id,
                    "projectors": [choice.resource for choice in media],
                    "leds": list(lights),
                },
            )
            return run

    def adjust(self, resource: str, delay_ms: int) -> TimingCalibrationRunStatus:
        run = self._running()
        output = run.outputs.get(resource)
        if output is None:
            raise InstallationCalibrationError(f"{resource} is not part of this calibration")
        try:
            validate_delay(output.kind, delay_ms)
            return self.runtime.set_timing_calibration_delay(resource, delay_ms)
        except (InstallationTimingError, TimingCalibrationRunError) as error:
            raise InstallationCalibrationError(str(error)) from error

    def resync(self) -> TimingCalibrationRunStatus:
        self._running()
        return self.runtime.resync_timing_calibration()

    async def finish(
        self, *, accept: bool, actor: str, role: Role, occurred_at: str
    ) -> tuple[TimingCalibrationRunStatus, tuple[OutputTiming, ...]]:
        async with self._lock:
            run = self.runtime.timing_calibration_status()
            if run is None or run.state not in {"starting", "active"}:
                raise InstallationCalibrationError("No timing calibration is running")
            installation_id = self._installation_id or self.repository.get_runtime_installation().id
            proposed = {resource: output.delay_ms for resource, output in run.outputs.items()}
            kinds = {resource: output.kind for resource, output in run.outputs.items()}
            stopped = await self.runtime.stop_timing_calibration(
                reason="accepted" if accept else "cancelled"
            )
            saved: list[OutputTiming] = []
            if accept:
                current = self.timing.delays(installation_id)
                for resource, delay in sorted(proposed.items()):
                    if current.get(resource, 0) == delay:
                        continue
                    saved.append(
                        self.timing.set(
                            installation_id=installation_id,
                            resource=resource,
                            kind=kinds[resource],
                            delay_ms=delay,
                            expected_revision=None,
                            actor=actor,
                            role=role,
                            occurred_at=occurred_at,
                            reason=f"calibration:{run.id}",
                        )
                    )
                self.push_to_runtime()
            self.repository.append_audit(
                actor=actor,
                action=f"installation.timing_calibration.{'accept' if accept else 'cancel'}",
                target=installation_id,
                result="success",
                occurred_at=occurred_at,
                authorization_context={
                    "role": role.value,
                    "run_id": run.id,
                    "proposed_ms": dict(proposed),
                    "saved": [item.resource for item in saved],
                },
            )
            return stopped or run, tuple(saved)

    async def stop(self, reason: str) -> None:
        await self.runtime.stop_timing_calibration(reason=reason)

    def _running(self) -> TimingCalibrationRunStatus:
        run = self.runtime.timing_calibration_status()
        if run is None or run.state not in {"starting", "active"}:
            raise InstallationCalibrationError("No timing calibration is running")
        return run

    async def _prepare_projector(
        self, choice: MediaChoice, clips: dict[ClipVariant, ReferenceClip]
    ) -> CalibrationMediaTarget:
        """Start the projector's player and put the reference clip in its cache."""
        assignment = next(
            (
                item
                for item in self.repository.list_resource_assignments()
                if item.resource == choice.resource and item.capability_kind == "media_playback"
            ),
            None,
        )
        if assignment is None:
            raise InstallationCalibrationError(f"{choice.resource} has no assigned client")
        device_id = UUID(assignment.device_id)
        if device_id not in self.sessions.connected_device_ids():
            raise InstallationCalibrationError(f"{choice.resource}'s client is not connected")
        agent_id = next(
            (
                binding.target
                for binding in self.runtime.catalog.installation.resources
                if binding.resource == choice.resource
            ),
            None,
        )
        if agent_id is None:
            raise InstallationCalibrationError(f"{choice.resource} has no runtime player")
        clip = clips["click" if choice.click else "silent"]
        await self._stage(assignment.device_id, clip)
        try:
            await self.players.start(assignment.device_id)
        except DevicePlayerError as error:
            raise InstallationCalibrationError(
                f"{choice.resource}'s player did not start: {error}"
            ) from error
        deadline = asyncio.get_running_loop().time() + PLAYER_READY_SECONDS
        while agent_id not in _connected_agents(self.runtime.status()):
            if asyncio.get_running_loop().time() > deadline:
                raise InstallationCalibrationError(
                    f"{choice.resource}'s player did not connect for playback"
                )
            await asyncio.sleep(0.2)
        return CalibrationMediaTarget(choice.resource, agent_id, clip.digest_sha256)

    async def _stage(self, device_id: str, clip: ReferenceClip) -> None:
        if self.public_url is None:
            raise InstallationCalibrationError("The device listener URL is not configured")
        held = tuple(
            artifact.digest_sha256 for artifact in self.repository.list_device_artifacts(device_id)
        )
        if clip.digest_sha256 in held:
            return
        netloc = urlsplit(self.public_url).netloc
        request_id = uuid4()
        command = DeviceArtifactStageCommand(
            request_id=request_id,
            device_session_id=UUID(int=0),
            controller_epoch=self.sessions.controller_epoch,
            activation_identity=uuid4(),
            resource="timing-calibration",
            digest_sha256=clip.digest_sha256,
            size_bytes=clip.size_bytes,
            download_url=TypeAdapter(AnyUrl).validate_python(
                urlunsplit(
                    (
                        "https",
                        netloc,
                        f"/api/device/v2/calibration-media/{clip.digest_sha256}",
                        "",
                        "",
                    )
                )
            ),
            # Keep every file the client already holds pinned; the clip joins them.
            protected_digests=device_protected_digests(
                self.repository, device_id, (*held, clip.digest_sha256), {clip.digest_sha256}
            ),
        )
        try:
            response = await self.sessions.request(UUID(device_id), request_id, command)
            ack = DeviceArtifactStageAck.model_validate(response)
        except (DeviceSessionError, ValueError) as error:
            raise InstallationCalibrationError(
                f"The calibration clip could not be sent to the client: {error}"
            ) from error
        if ack.status != "completed" or ack.digest_sha256 != clip.digest_sha256:
            raise InstallationCalibrationError(
                f"The client could not verify the calibration clip: {ack.error or ack.status}"
            )


def _connected_agents(status: dict[str, object]) -> set[str]:
    agents = status.get("connected_agents")
    return {str(agent) for agent in agents} if isinstance(agents, list) else set()


def output_timing_response(item: OutputTiming) -> OutputTimingResponse:
    return OutputTimingResponse(
        resource=item.resource,
        kind=item.kind,
        delay_ms=item.delay_ms,
        minimum_ms=item.minimum_ms,
        maximum_ms=item.maximum_ms,
        revision=item.revision,
        updated_by=item.updated_by,
        updated_at=item.updated_at,
    )


def calibration_run_response(
    run: TimingCalibrationRunStatus, saved: Mapping[str, int]
) -> TimingCalibrationRunResponse:
    return TimingCalibrationRunResponse(
        id=run.id,
        state=run.state,
        period_ms=run.period_ms,
        flash_ms=run.flash_ms,
        cycles=run.cycles,
        started_at=run.started_at,
        error=run.error,
        stop_reason=run.stop_reason,
        outputs=tuple(
            CalibrationOutputResponse(
                resource=output.resource,
                kind=output.kind,
                delay_ms=output.delay_ms,
                saved_ms=saved.get(output.resource, 0),
                state=output.state,
                error=output.error,
                drift_ms=output.drift_ms,
            )
            for output in run.outputs.values()
        ),
    )
