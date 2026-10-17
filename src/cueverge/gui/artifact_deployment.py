import asyncio
import time
from dataclasses import dataclass
from typing import Literal, cast
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID, uuid4

from pydantic import AnyUrl

from cueverge.gui.config import GuiConfig
from cueverge.gui.device_session import DeviceSessionRegistry
from cueverge.gui.repository import GuiRepository, ProjectNotFoundError
from cueverge.protocol.device_v2 import (
    DeviceArtifactActivateAck,
    DeviceArtifactActivateCommand,
    DeviceArtifactCleanupAck,
    DeviceArtifactCleanupCommand,
    DeviceArtifactStageAck,
    DeviceArtifactStageCommand,
)


class ArtifactDeploymentError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArtifactDeploymentResult:
    activation_identity: str
    artifact_id: str
    resources: tuple[str, ...]
    staged_device_ids: tuple[str, ...]
    activated_device_ids: tuple[str, ...]


class ArtifactDeploymentService:
    def __init__(
        self,
        config: GuiConfig,
        repository: GuiRepository,
        sessions: DeviceSessionRegistry,
    ) -> None:
        self.config = config
        self.repository = repository
        self.sessions = sessions

    async def deploy(self, artifact_id: str) -> ArtifactDeploymentResult:
        staged = await self.stage(artifact_id)
        activated = await self.activate(artifact_id)
        return ArtifactDeploymentResult(
            activation_identity=activated.activation_identity,
            artifact_id=artifact_id,
            resources=activated.resources,
            staged_device_ids=staged.staged_device_ids,
            activated_device_ids=activated.activated_device_ids,
        )

    async def cleanup(
        self,
        device_id: UUID,
        digests: tuple[str, ...],
    ) -> DeviceArtifactCleanupAck:
        command = DeviceArtifactCleanupCommand(
            request_id=uuid4(),
            device_session_id=_session_placeholder(),
            controller_epoch=self.sessions.controller_epoch,
            digests=digests,
        )
        response = await self.sessions.request(
            device_id,
            command.request_id,
            command,
        )
        return DeviceArtifactCleanupAck.model_validate(response)

    async def stage(
        self,
        artifact_id: str,
        *,
        protected_digests: tuple[str, ...] = (),
    ) -> ArtifactDeploymentResult:
        prepared = self._prepare(
            artifact_id,
            protected_digests=protected_digests,
        )
        staged = await asyncio.gather(
            *[
                self.sessions.request(
                    device_id,
                    command.request_id,
                    command,
                )
                for _, device_id, command in prepared.stage_requests
            ]
        )
        for response in staged:
            acknowledgment = DeviceArtifactStageAck.model_validate(response)
            if acknowledgment.status != "completed":
                raise ArtifactDeploymentError(f"artifact staging failed: {acknowledgment.error}")
        return ArtifactDeploymentResult(
            activation_identity=str(prepared.activation_identity),
            artifact_id=artifact_id,
            resources=prepared.resources,
            staged_device_ids=tuple(sorted(item[0] for item in prepared.stage_requests)),
            activated_device_ids=(),
        )

    async def activate(self, artifact_id: str) -> ArtifactDeploymentResult:
        prepared = self._prepare(artifact_id)
        activation_at_unix_ms = time.time_ns() // 1_000_000 + 2000
        prepare_requests = tuple(
            (
                device_name,
                device_id,
                command.model_copy(
                    update={
                        "phase": "prepare",
                        "activation_at_unix_ms": activation_at_unix_ms,
                    }
                ),
            )
            for device_name, device_id, command in prepared.activation_requests
        )
        prepared_responses = await asyncio.gather(
            *[
                self.sessions.request(
                    device_id,
                    command.request_id,
                    command,
                )
                for _, device_id, command in prepare_requests
            ]
        )
        for response in prepared_responses:
            activation_ack = DeviceArtifactActivateAck.model_validate(response)
            if activation_ack.status != "completed":
                await self._rollback_activation(prepared.activation_requests)
                raise ArtifactDeploymentError(
                    f"artifact activation preparation failed: {activation_ack.error}"
                )
        commit_requests = tuple(
            (
                device_name,
                device_id,
                command.model_copy(
                    update={
                        "request_id": uuid4(),
                        "phase": "commit",
                        "activation_at_unix_ms": activation_at_unix_ms,
                    }
                ),
            )
            for device_name, device_id, command in prepared.activation_requests
        )
        commit_responses = await asyncio.gather(
            *[
                self.sessions.request(
                    device_id,
                    command.request_id,
                    command,
                )
                for _, device_id, command in commit_requests
            ],
            return_exceptions=True,
        )
        failures = [
            response
            for response in commit_responses
            if isinstance(response, BaseException)
            or DeviceArtifactActivateAck.model_validate(response).status != "completed"
        ]
        if failures:
            await self._rollback_activation(commit_requests)
            raise ArtifactDeploymentError(
                "artifact activation commit failed; previous activation was restored"
            )
        return ArtifactDeploymentResult(
            activation_identity=str(prepared.activation_identity),
            artifact_id=artifact_id,
            resources=prepared.resources,
            staged_device_ids=(),
            activated_device_ids=tuple(sorted(item[0] for item in commit_requests)),
        )

    async def _rollback_activation(
        self,
        requests: tuple[tuple[str, UUID, DeviceArtifactActivateCommand], ...],
    ) -> None:
        await asyncio.gather(
            *[
                self.sessions.request(
                    device_id,
                    rollback.request_id,
                    rollback,
                )
                for _, device_id, command in requests
                for rollback in (
                    command.model_copy(
                        update={
                            "request_id": uuid4(),
                            "phase": "rollback",
                            "activation_at_unix_ms": None,
                        }
                    ),
                )
            ],
            return_exceptions=True,
        )

    def _prepare(
        self,
        artifact_id: str,
        *,
        protected_digests: tuple[str, ...] = (),
    ) -> "_PreparedDeployment":
        public_url = self.config.device_listener_public_url
        if public_url is None:
            raise ArtifactDeploymentError("device listener public URL is unavailable")
        try:
            manifest = self.repository.get_render_artifact_manifest(artifact_id)
        except (ProjectNotFoundError, ValueError) as error:
            raise ArtifactDeploymentError(str(error)) from error
        activation_value = manifest.get("activation_identity")
        end_behavior = manifest.get("media_end_behavior")
        members = manifest.get("members")
        duration_ms = manifest.get("duration_ms")
        frame_rate = manifest.get("frame_rate")
        frame_time_origin_ms = manifest.get("frame_time_origin_ms")
        frame_rate_numerator = frame_rate.get("numerator") if isinstance(frame_rate, dict) else None
        frame_rate_denominator = (
            frame_rate.get("denominator") if isinstance(frame_rate, dict) else None
        )
        if (
            not isinstance(activation_value, str)
            or end_behavior not in {"stop", "hold_last_frame", "loop"}
            or not isinstance(members, list)
            or not isinstance(duration_ms, int)
            or not isinstance(frame_rate_numerator, int)
            or not isinstance(frame_rate_denominator, int)
            or not isinstance(frame_time_origin_ms, int)
        ):
            raise ArtifactDeploymentError("render artifact manifest is invalid")
        activation_identity = UUID(activation_value)
        assignments = {
            assignment.resource: assignment
            for assignment in self.repository.list_resource_assignments()
        }
        stage_requests: list[tuple[str, UUID, DeviceArtifactStageCommand]] = []
        activation_requests: list[tuple[str, UUID, DeviceArtifactActivateCommand]] = []
        resources: list[str] = []
        for item in members:
            if not isinstance(item, dict):
                raise ArtifactDeploymentError("render artifact member is invalid")
            resource = item.get("resource")
            digest = item.get("digest_sha256")
            size = item.get("size_bytes")
            if (
                not isinstance(resource, str)
                or not isinstance(digest, str)
                or not isinstance(size, int)
            ):
                raise ArtifactDeploymentError("render artifact member metadata is invalid")
            assignment = assignments.get(resource)
            if assignment is None:
                raise ArtifactDeploymentError(
                    f"render resource {resource} has no device assignment"
                )
            device_id = UUID(assignment.device_id)
            stage_request_id = uuid4()
            activate_request_id = uuid4()
            stage_requests.append(
                (
                    assignment.device_id,
                    device_id,
                    DeviceArtifactStageCommand(
                        request_id=stage_request_id,
                        device_session_id=_session_placeholder(),
                        controller_epoch=self.sessions.controller_epoch,
                        activation_identity=activation_identity,
                        resource=resource,
                        digest_sha256=digest,
                        size_bytes=size,
                        download_url=AnyUrl(
                            _artifact_url(
                                str(public_url),
                                artifact_id,
                                resource,
                            )
                        ),
                        protected_digests=protected_digests,
                    ),
                )
            )
            activation_requests.append(
                (
                    assignment.device_id,
                    device_id,
                    DeviceArtifactActivateCommand(
                        request_id=activate_request_id,
                        device_session_id=_session_placeholder(),
                        controller_epoch=self.sessions.controller_epoch,
                        activation_identity=activation_identity,
                        resource=resource,
                        digest_sha256=digest,
                        media_end_behavior=cast(
                            Literal["stop", "hold_last_frame", "loop"],
                            end_behavior,
                        ),
                        duration_ms=duration_ms,
                        frame_rate_numerator=frame_rate_numerator,
                        frame_rate_denominator=frame_rate_denominator,
                        frame_time_origin_ms=frame_time_origin_ms,
                    ),
                )
            )
            resources.append(resource)
        return _PreparedDeployment(
            activation_identity=activation_identity,
            resources=tuple(sorted(resources)),
            stage_requests=tuple(stage_requests),
            activation_requests=tuple(activation_requests),
        )


@dataclass(frozen=True)
class _PreparedDeployment:
    activation_identity: UUID
    resources: tuple[str, ...]
    stage_requests: tuple[tuple[str, UUID, DeviceArtifactStageCommand], ...]
    activation_requests: tuple[tuple[str, UUID, DeviceArtifactActivateCommand], ...]


def _artifact_url(base: str, artifact_id: str, resource: str) -> str:
    parsed = urlsplit(base)
    return urlunsplit(
        (
            "https",
            parsed.netloc,
            f"/api/device/v2/render-artifacts/{quote(artifact_id)}/{quote(resource)}",
            "",
            "",
        )
    )


def _session_placeholder() -> UUID:
    # The registry rewrites transport session identity validation through the
    # active mTLS session; the field is replaced by the device channel.
    return UUID(int=0)
