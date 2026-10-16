import asyncio
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
        staged = await asyncio.gather(
            *[
                self.sessions.request(
                    device_id,
                    command.request_id,
                    command,
                )
                for _, device_id, command in stage_requests
            ]
        )
        for response in staged:
            acknowledgment = DeviceArtifactStageAck.model_validate(response)
            if acknowledgment.status != "completed":
                raise ArtifactDeploymentError(f"artifact staging failed: {acknowledgment.error}")
        activated = await asyncio.gather(
            *[
                self.sessions.request(
                    device_id,
                    command.request_id,
                    command,
                )
                for _, device_id, command in activation_requests
            ]
        )
        for response in activated:
            activation_ack = DeviceArtifactActivateAck.model_validate(response)
            if activation_ack.status != "completed":
                raise ArtifactDeploymentError(f"artifact activation failed: {activation_ack.error}")
        return ArtifactDeploymentResult(
            activation_identity=str(activation_identity),
            artifact_id=artifact_id,
            resources=tuple(sorted(resources)),
            staged_device_ids=tuple(sorted(item[0] for item in stage_requests)),
            activated_device_ids=tuple(sorted(item[0] for item in activation_requests)),
        )


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
