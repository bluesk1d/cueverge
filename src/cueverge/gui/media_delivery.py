import hashlib
import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID, uuid4

from pydantic import AnyUrl, TypeAdapter, ValidationError

from cueverge.gui.artifact_deployment import ArtifactDeploymentError
from cueverge.gui.contracts import DraftProject
from cueverge.gui.stage_renderer import bakes_lighting_only
from cueverge.protocol.device_v2 import DeviceArtifactStageAck, DeviceArtifactStageCommand

if TYPE_CHECKING:
    from cueverge.gui.asset_upload import AssetUploadService
    from cueverge.gui.device_session import DeviceSessionRegistry
    from cueverge.gui.render_jobs import RenderJobService
    from cueverge.gui.repository import GuiRepository


def media_members(
    draft: DraftProject,
    project_id: str,
    revision: int,
    assets: "AssetUploadService",
    jobs: "RenderJobService",
    repository: "GuiRepository",
    lighting_artifacts: dict[str, Path] | None = None,
) -> list[dict[str, object]]:
    roots = draft.editor_metadata.get("scene_compositions", {})
    instances = draft.editor_metadata.get("composition_instances", {})
    compositions = draft.editor_metadata.get("compositions", {})
    lighting_only = {
        key
        for key, value in (compositions.items() if isinstance(compositions, dict) else ())
        if isinstance(value, dict) and bakes_lighting_only(value)
    }
    available = jobs.list(project_id)
    assignments = {item.resource: item for item in repository.list_resource_assignments()}
    members: list[dict[str, object]] = []
    for scene in draft.scenes:
        values = roots.get(scene.id, []) if isinstance(roots, dict) else []
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ArtifactDeploymentError("Scene composition roots must be a list of identifiers")
        root_ids = [str(item) for item in values]
        scene_instances = instances.get(scene.id, []) if isinstance(instances, dict) else []
        if not isinstance(scene_instances, list):
            raise ArtifactDeploymentError("Scene composition instances must be a list")
        for item in scene_instances:
            identifier = item.get("composition_id") if isinstance(item, dict) else None
            if not isinstance(identifier, str):
                raise ArtifactDeploymentError("Composition instances require an identifier")
            root_ids.append(identifier)
        root_ids = list(dict.fromkeys(root_ids))
        outputs = scene.media if isinstance(scene.media, tuple) else (scene.media,)
        for output in outputs:
            output_id = getattr(output, "id", "media")
            choices: list[dict[str, object]] = []
            for root_id in root_ids:
                if root_id in lighting_only:
                    continue
                job = next(
                    (
                        item
                        for item in available
                        if item.composition_id == root_id
                        and item.source_revision == revision
                        and item.state == "completed"
                        and item.artifact_id
                        and (
                            not lighting_artifacts
                            or root_id not in lighting_artifacts
                            or item.artifact_id == lighting_artifacts[root_id].name
                        )
                    ),
                    None,
                )
                if job is None or job.artifact_id is None:
                    continue
                manifest = repository.get_render_artifact_manifest(job.artifact_id)
                raw_members = manifest.get("members")
                if not isinstance(raw_members, list):
                    raise ArtifactDeploymentError("Baked media member manifest is invalid")
                choices.extend(
                    {
                        "render_artifact_id": job.artifact_id,
                        "source_asset_id": None,
                        "digest_sha256": value["digest_sha256"],
                        "size_bytes": value["size_bytes"],
                    }
                    for value in raw_members
                    if isinstance(value, dict) and value.get("resource") == output.resource
                )
            if len(choices) > 1:
                raise ArtifactDeploymentError(
                    "Multiple baked roots target one media output; "
                    "bake a combined native composition before Apply"
                )
            if choices:
                selected = choices[0]
            elif output.asset is not None:
                asset, path = assets.get_original(output.asset)
                if (
                    asset.project_id != project_id
                    or asset.digest_sha256 is None
                    or asset.size_bytes is None
                ):
                    raise ArtifactDeploymentError(
                        "Media asset is outside this project or is not verified"
                    )
                metadata = asset.metadata or {}
                formats = metadata.get("format_name")
                codecs = metadata.get("video_codecs", [])
                assignment = assignments.get(output.resource)
                if assignment is None and getattr(output, "required", True):
                    raise ArtifactDeploymentError(f"Media output {output.resource} is unassigned")
                device = repository.get_device(assignment.device_id) if assignment else None
                from cueverge.protocol.device_v2 import MediaPlaybackCapability

                capability = (
                    next(
                        (
                            item
                            for item in device.capabilities.capabilities
                            if isinstance(item, MediaPlaybackCapability)
                        ),
                        None,
                    )
                    if device
                    else None
                )
                if getattr(output, "required", True) and (
                    capability is None
                    or not isinstance(formats, str)
                    or not set(formats.split(",")).intersection(capability.containers)
                    or not isinstance(codecs, list)
                    or not set(codecs).issubset(capability.codecs)
                ):
                    raise ArtifactDeploymentError(
                        "Original media format is not declared by its device; "
                        "bake a compatible native output before Apply"
                    )
                if hashlib.sha256(path.read_bytes()).hexdigest() != asset.digest_sha256:
                    raise ArtifactDeploymentError("Media asset checksum changed")
                selected = {
                    "render_artifact_id": None,
                    "source_asset_id": asset.id,
                    "digest_sha256": asset.digest_sha256,
                    "size_bytes": asset.size_bytes,
                }
            else:
                continue
            identifier = hashlib.sha256(
                json.dumps(
                    [scene.id, output_id, output.resource, selected["digest_sha256"]]
                ).encode()
            ).hexdigest()
            members.append(
                {
                    **selected,
                    "member_id": identifier,
                    "scene_id": scene.id,
                    "output_id": output_id,
                    "resource": output.resource,
                    "required": getattr(output, "required", True),
                }
            )
    return members


def published_media_member(
    repository: "GuiRepository", package_id: str, member_id: str
) -> dict[str, object]:
    with sqlite3.connect(repository.database_path) as connection:
        row = connection.execute(
            "SELECT manifest_json FROM published_packages WHERE id = ?", (package_id,)
        ).fetchone()
    if row is None:
        raise ValueError("Published package does not exist")
    manifest = TypeAdapter(dict[str, object]).validate_json(row[0])
    members = TypeAdapter(list[dict[str, object]]).validate_python(
        manifest.get("media_members", [])
    )
    member = next((item for item in members if item.get("member_id") == member_id), None)
    if member is None:
        raise ValueError("Published media member does not exist")
    return member


async def stage_media_members(
    repository: "GuiRepository",
    sessions: "DeviceSessionRegistry",
    public_url: str | None,
    package_id: str,
    members: list[dict[str, object]],
    protected_digests: tuple[str, ...],
) -> list[str]:
    warnings: list[str] = []
    if not members:
        return warnings
    if public_url is None:
        if all(member.get("required", True) is False for member in members):
            return [
                "Optional media was not delivered because authenticated transport is unavailable."
            ]
        raise ArtifactDeploymentError("Authenticated media transport URL is unavailable")
    assignments = {item.resource: item for item in repository.list_resource_assignments()}
    parsed_url = urlsplit(public_url)
    download_base = urlunsplit(("https", parsed_url.netloc, "", "", ""))
    for member in members:
        resource = str(member["resource"])
        assignment = assignments.get(resource)
        if assignment is None:
            if member.get("required", True) is False:
                warnings.append(
                    f"Optional media {resource} has no assignment and was not delivered."
                )
                continue
            raise ArtifactDeploymentError(f"Media output {resource} is unassigned")
        request_id = uuid4()
        command = DeviceArtifactStageCommand(
            request_id=request_id,
            device_session_id=UUID(int=0),
            controller_epoch=sessions.controller_epoch,
            activation_identity=UUID(package_id),
            resource=resource,
            digest_sha256=str(member["digest_sha256"]),
            size_bytes=TypeAdapter(int).validate_python(member["size_bytes"]),
            download_url=AnyUrl(
                f"{download_base}/api/device/v2/published-media/"
                f"{quote(package_id, safe='')}/{quote(str(member['member_id']), safe='')}"
            ),
            protected_digests=protected_digests,
        )
        from cueverge.gui.device_session import DeviceSessionError

        try:
            response = await sessions.request(UUID(assignment.device_id), request_id, command)
            ack = DeviceArtifactStageAck.model_validate(response)
            if (
                ack.status != "completed"
                or ack.request_id != request_id
                or ack.digest_sha256 != member["digest_sha256"]
                or ack.activation_identity != UUID(package_id)
            ):
                raise ArtifactDeploymentError(
                    "A device could not verify its published media; "
                    "Apply has not interrupted playback"
                )
        except (DeviceSessionError, ArtifactDeploymentError, ValidationError) as error:
            if member.get("required", True) is not False:
                raise ArtifactDeploymentError(
                    f"Media transfer was not verified: {error}"
                ) from error
            warnings.append(
                f"Optional media {resource} was not delivered: {error}. "
                "Reapply when it is available."
            )
    return warnings
