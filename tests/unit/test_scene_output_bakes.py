import asyncio
import hashlib
import time
import wave
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import av
import numpy as np
from fastapi.testclient import TestClient

from cueverge.gui import media_tools
from cueverge.gui.apply import ApplyRequest
from cueverge.gui.contracts import DraftProject, Role
from cueverge.gui.mcp_authoring import McpScope
from cueverge.gui.scene_output_bakes import scene_output_bakes, scene_output_composition_id
from tests.unit.test_apply import setup, wait


def _upload(services: Any, project_id: str, path: Path, media_type: str) -> Any:
    content = path.read_bytes()
    occurred = datetime.now(UTC).isoformat()
    asset, upload = services.asset_upload.begin(
        project_id=project_id,
        name=path.stem,
        original_filename=path.name,
        media_type=media_type,
        total_bytes=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=occurred,
    )
    services.asset_upload.append(
        upload_id=upload.id,
        offset=0,
        chunk=content,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=occurred,
    )
    services.asset_upload.process_asset(asset.id, occurred_at=occurred)
    processed = services.asset_upload.get_asset(asset.id)
    assert processed.status == "ready", processed.error
    return processed


def _media(tmp_path: Path) -> tuple[Path, Path]:
    video = tmp_path / "base.mp4"
    frames = tuple(np.full((36, 64, 3), index * 10, dtype=np.uint8) for index in range(20))
    media_tools.encode_mp4(
        frames=frames,
        audio_samples=np.zeros((96000, 2), dtype=np.float32),
        frame_rate=Fraction(10),
        video_profile="baseline",
        video_level="3.0",
        pixel_format="yuv420p",
        video_bitrate_kbps=300,
        keyframe_interval_frames=10,
        b_frames=0,
        target=video,
    )
    tone = tmp_path / "tone.wav"
    samples = (np.sin(2 * np.pi * 440 * np.arange(24000) / 48000) * 0.5 * 32767).astype("<i2")
    with wave.open(str(tone), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(samples.tobytes())
    return video, tone


def _layer(source_id: str, *, kind: str = "audio", track: str = "media:left") -> dict[str, Any]:
    return {
        "id": f"layer-{kind}",
        "name": f"{kind} accent",
        "kind": kind,
        "layer_schema_version": 1,
        "source_id": source_id,
        "track_id": track,
        "start_ms": 1000,
        "duration_ms": 500,
        "in_point_ms": 0,
        "out_point_ms": 500,
        "fade_in_ms": 0,
        "fade_out_ms": 0,
        "z_index": 0,
        "playback": "hold",
        "keyframes": [],
        "audio": {"gain_db": -6, "mix_order": 0, "mute": False, "solo": False},
        "alpha_interpretation": None,
        "source_color_space": None,
        "scaling_behavior": None,
    }


def _scene_with_output_audio(
    tmp_path: Path,
    layers: list[dict[str, Any]] | None = None,
    *,
    end_behavior: str | None = None,
    layer_start_ms: int = 1000,
):
    runtime, services, stored = setup(tmp_path)
    video_path, tone_path = _media(tmp_path)
    video = _upload(services, stored.project.id, video_path, "video/mp4")
    tone = _upload(services, stored.project.id, tone_path, "audio/wav")
    media_resource = next(
        item.resource for item in stored.draft.installation.resources if item.kind == "media_agent"
    )
    document = stored.draft.model_dump(mode="json")
    scene = document["scenes"][0]
    scene["resources"].append(media_resource)
    scene["media"] = [
        {"id": "left", "resource": media_resource, "asset": video.id, "required": False}
    ]
    if end_behavior is not None:
        scene["media"][0]["end_behavior"] = end_behavior
    document["editor_metadata"]["timeline_transform_layers"] = {
        scene["id"]: layers
        if layers is not None
        else [{**_layer(tone.id), "start_ms": layer_start_ms}]
    }
    candidate = services.repository.update_draft(
        project_id=stored.project.id,
        expected_revision=1,
        draft=DraftProject.model_validate(document),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    bake_id = scene_output_composition_id(scene["id"], "left")
    return runtime, services, stored.project.id, candidate, video, tone, media_resource, bake_id


def _wait_for_job(services: Any, job_id: str) -> Any:
    for _ in range(600):
        job = services.render_jobs.get(job_id)
        if job.state in {"completed", "failed", "canceled"}:
            return job
        time.sleep(0.05)
    raise AssertionError("bake did not finish")


def _rms(samples: np.ndarray, start_ms: int, end_ms: int) -> float:
    window = samples[start_ms * 48 : end_ms * 48]
    return float(np.sqrt(np.mean(np.square(window))))


def test_scene_output_audio_is_baked_into_a_copy_of_the_output_video(tmp_path: Path) -> None:
    _, services, project_id, candidate, video, _, resource, bake_id = _scene_with_output_audio(
        tmp_path
    )
    [bake] = scene_output_bakes(candidate.draft)
    assert (bake.composition_id, bake.resource, bake.problem()) == (bake_id, resource, None)
    [requirement] = [
        item
        for item in services.publication.bake_requirements(project_id, candidate.revision)
        if item.kind == "scene_output"
    ]
    assert (requirement.id, requirement.state) == (bake_id, "missing")
    codes = {item.code for item in services.publication.validate(project_id, candidate.revision)}
    assert "current_bake_missing" in codes

    job = services.render_jobs.create(
        project_id=project_id, composition_id=bake_id, actor="admin", expected_revision=2
    )
    job = _wait_for_job(services, job.id)
    assert job.state == "completed", job.error
    member = services.repository.get_render_artifact_member(job.artifact_id, resource)
    base_path = services.asset_upload.get_original(video.id)[1]
    with av.open(str(base_path)) as source, av.open(str(member.path)) as baked:
        source_packets = [bytes(p) for p in source.demux(source.streams.video[0]) if p.size]
        baked_packets = [bytes(p) for p in baked.demux(baked.streams.video[0]) if p.size]
        assert source_packets == baked_packets
    from cueverge.gui.frame_compositor import decode_audio_stereo_48k

    mixed = decode_audio_stereo_48k(str(member.path))
    assert _rms(mixed, 100, 900) < 0.01
    accent = _rms(mixed, 1100, 1400)
    assert 0.1 < accent < 0.25  # 0.5 peak sine is ~0.35 RMS; -6 dB halves it.
    assert _rms(mixed, 1600, 1900) < 0.01
    manifest = services.repository.get_render_artifact_manifest(job.artifact_id)
    assert manifest["composition_id"] == bake_id
    assert manifest["scene_output"]["base_asset_id"] == video.id

    statuses = services.publication.bake_requirements(project_id, candidate.revision)
    assert {item.id: item.state for item in statuses}[bake_id] == "current"
    codes = {item.code for item in services.publication.validate(project_id, candidate.revision)}
    assert "current_bake_missing" not in codes
    cached = services.render_jobs.create(
        project_id=project_id, composition_id=bake_id, actor="admin", expected_revision=2
    )
    assert (cached.state, cached.artifact_id) == ("completed", job.artifact_id)


def test_visual_layers_on_a_scene_output_are_reported_not_ignored(tmp_path: Path) -> None:
    _, services, project_id, candidate, video, tone, _, bake_id = _scene_with_output_audio(
        tmp_path, layers=None
    )
    document = candidate.draft.model_dump(mode="json")
    scene_id = document["scenes"][0]["id"]
    document["editor_metadata"]["timeline_transform_layers"][scene_id].append(
        {
            **_layer(video.id, kind="video"),
            "audio": None,
            "alpha_interpretation": "straight",
            "source_color_space": "srgb",
            "scaling_behavior": "fit",
            "keyframes": [
                {
                    "id": "frame",
                    "time_ms": 0,
                    "position": [32, 18],
                    "scale": [1, 1],
                    "rotation_deg": 0,
                    "anchor": [0.5, 0.5],
                    "opacity": 1,
                    "crop": [0, 0, 0, 0],
                    "interpolation": {
                        "kind": "linear",
                        "temporal_bezier": None,
                        "spatial_in": None,
                        "spatial_out": None,
                    },
                }
            ],
        }
    )
    updated = services.repository.update_draft(
        project_id=project_id,
        expected_revision=2,
        draft=DraftProject.model_validate(document),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    [requirement] = [
        item
        for item in services.publication.bake_requirements(project_id, updated.revision)
        if item.kind == "scene_output"
    ]
    assert requirement.state == "unavailable"
    assert "move visual layers into a composition" in (requirement.error or "")
    diagnostics = services.publication.validate(project_id, updated.revision)
    assert "scene_output_layers_unsupported" in {item.code for item in diagnostics}
    del tone, bake_id


def test_apply_bakes_missing_scene_output_audio_before_publishing(tmp_path: Path) -> None:
    runtime, services, project_id, _, _, _, resource, bake_id = _scene_with_output_audio(tmp_path)

    async def scenario() -> dict[str, Any]:
        job = await services.apply.start(
            project_id,
            ApplyRequest(
                source_revision=2,
                expected_head_revision=2,
                confirm_interruption=True,
                approve_release=True,
            ),
            "admin",
        )
        return await wait(services, str(job["id"]))

    result = asyncio.run(scenario())
    assert result["state"] == "completed", result["error"]
    assert [bake["id"] for bake in result["bakes"]] == [bake_id]
    assert result["bakes"][0]["state"] == "completed"
    package = services.publication.get(str(result["package_id"]))
    [member] = package.manifest["media_members"]
    assert member["resource"] == resource
    assert member["source_asset_id"] is None
    assert member["render_artifact_id"] in package.manifest["render_artifact_ids"]
    assert runtime.catalog.require("ambient").media_outputs[0].asset_id == member["digest_sha256"]


def test_apply_fails_clearly_when_a_bake_cannot_run(tmp_path: Path) -> None:
    _, services, project_id, _, _, tone, _, _ = _scene_with_output_audio(tmp_path, layers=None)
    revision = services.repository.get_revision(project_id)
    document = revision.draft.model_dump(mode="json")
    scene_id = document["scenes"][0]["id"]
    # The layer asks for more audio than the clip has, so the bake itself fails.
    document["editor_metadata"]["timeline_transform_layers"][scene_id] = [
        {**_layer(tone.id), "duration_ms": 900, "out_point_ms": 900}
    ]
    services.repository.update_draft(
        project_id=project_id,
        expected_revision=revision.revision,
        draft=DraftProject.model_validate(document),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )

    async def scenario() -> dict[str, Any]:
        job = await services.apply.start(
            project_id,
            ApplyRequest(
                source_revision=3,
                expected_head_revision=3,
                confirm_interruption=True,
                approve_release=True,
            ),
            "admin",
        )
        return await wait(services, str(job["id"]))

    result = asyncio.run(scenario())
    assert result["state"] == "failed"
    assert "Baking" in result["error"] and "shorter than its layer trim" in result["error"]
    assert result["package_id"] is None


def test_gui_and_mcp_list_bake_requirements(tmp_path: Path) -> None:
    from cueverge.controller.app import create_app

    runtime, services, project_id, _, _, _, _, bake_id = _scene_with_output_audio(tmp_path)
    now = datetime.now(UTC)
    read = services.mcp_store.create_token(
        client_id="bakes",
        username="admin",
        project_id=project_id,
        scopes=(McpScope.READ,),
        expires_at=(now + timedelta(hours=1)).isoformat(),
        occurred_at=now.isoformat(),
    ).token
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "get_bake_requirements", "arguments": {}},
            },
            headers={
                "Authorization": f"Bearer {read}",
                "Accept": "application/json, text/event-stream",
            },
        )
        result = response.json()["result"]["structuredContent"]
        assert result["ready"] is False
        assert {item["id"]: item["state"] for item in result["bakes"]}[bake_id] == "missing"
        from tests.unit.test_apply import ORIGIN, PASSWORD

        login = client.post(
            "/api/gui/v1/auth/login",
            json={"username": "admin", "password": PASSWORD},
            headers={"Origin": ORIGIN},
        )
        assert login.status_code == 200
        listed = client.get(f"/api/gui/v1/projects/{project_id}/bake-requirements").json()
        assert {item["id"]: item["kind"] for item in listed["bakes"]}[bake_id] == "scene_output"


def test_full_length_audio_trim_tolerates_millisecond_rounding(tmp_path: Path) -> None:
    from types import SimpleNamespace

    import pytest

    from cueverge.gui.projector_renderer import ProjectorRenderError, prepare_media_layers

    def wav(name: str, frames: int) -> Path:
        path = tmp_path / name
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(48000)
            output.writeframes(np.full(frames, 8000, dtype="<i2").tobytes())
        return path

    # 499.79 ms of audio is stored as a 500 ms asset, and a full-length layer trims 0-500 ms.
    files = {"rounded": wav("rounded.wav", 23990), "short": wav("short.wav", 23000)}

    class Assets:
        def get_original(self, source_id: str) -> tuple[Any, Path]:
            asset = SimpleNamespace(project_id="p", digest_sha256=source_id, media_type="audio/wav")
            return asset, files[source_id]

    def layer(source_id: str) -> dict[str, Any]:
        return {**_layer(source_id), "duration_ms": 500, "out_point_ms": 500}

    prepared = prepare_media_layers(
        project_id="p",
        raw_layers=[layer("rounded")],
        assets=Assets(),  # type: ignore[arg-type]
    )
    [buffer] = prepared.audio_layers
    assert buffer.samples.shape[0] == 500 * 48
    assert float(np.abs(buffer.samples[-1]).max()) == 0.0
    assert float(np.abs(buffer.samples[100]).max()) > 0.1

    with pytest.raises(ProjectorRenderError, match="shorter than its layer trim"):
        prepare_media_layers(
            project_id="p",
            raw_layers=[layer("short")],
            assets=Assets(),  # type: ignore[arg-type]
        )


def test_stale_pins_warn_but_other_client_faults_block_publication(tmp_path: Path) -> None:
    from dataclasses import replace
    from types import SimpleNamespace

    from cueverge.gui.repository import StoredDeviceArtifactStatus

    _, services, project_id, candidate, _, _, resource, _ = _scene_with_output_audio(tmp_path)
    assignment = SimpleNamespace(
        resource=resource,
        device_id="client-1",
        capability_kind="media_playback",
        device_output=None,
    )
    services.repository.list_resource_assignments = lambda: (assignment,)
    status = StoredDeviceArtifactStatus(
        device_id=assignment.device_id,
        observed_at_ms=1,
        total_bytes=10**9,
        free_bytes=10**9,
        artifact_count=0,
        artifact_bytes=0,
        pinned_count=4,
        supports_pinning=True,
        supports_resumable_transfer=True,
        transfers=(),
        faults=("4 pinned artifact(s) are missing",),
    )

    def diagnostics(reported: StoredDeviceArtifactStatus) -> dict[str, str]:
        services.repository.list_device_artifact_statuses = lambda: (reported,)
        return {
            item.code: item.severity
            for item in services.publication.validate(project_id, candidate.revision)
        }

    stale = diagnostics(status)
    assert stale["inventory_stale_pins"] == "warning"
    assert "inventory_fault" not in stale
    unreadable = diagnostics(replace(status, faults=("1 asset(s) could not be read",)))
    assert unreadable["inventory_fault"] == "error"


def test_looping_output_bakes_whole_video_loops_so_audio_is_not_cut_at_the_loop(
    tmp_path: Path,
) -> None:
    """A clip past the end of a looping 2 s video plays once instead of being cut and repeated."""
    from cueverge.gui.frame_compositor import decode_audio_stereo_48k

    _, services, project_id, candidate, video, _, resource, bake_id = _scene_with_output_audio(
        tmp_path, end_behavior="loop", layer_start_ms=2500
    )
    [bake] = scene_output_bakes(candidate.draft)
    expected_loops = max(2, -(-bake.scene_duration_ms // 2000))
    job = _wait_for_job(
        services,
        services.render_jobs.create(
            project_id=project_id, composition_id=bake_id, actor="admin", expected_revision=2
        ).id,
    )
    assert job.state == "completed", job.error
    member = services.repository.get_render_artifact_member(job.artifact_id, resource)
    base_path = services.asset_upload.get_original(video.id)[1]
    with av.open(str(base_path)) as source, av.open(str(member.path)) as baked:
        source_packets = [bytes(p) for p in source.demux(source.streams.video[0]) if p.size]
        baked_stream = baked.streams.video[0]
        baked_packets = [(bytes(p), p.pts) for p in baked.demux(baked_stream) if p.size]
    assert [data for data, _ in baked_packets] == source_packets * expected_loops
    pts = [value for _, value in baked_packets]
    assert pts == sorted(pts) and len(set(pts)) == len(pts)

    mixed = decode_audio_stereo_48k(str(member.path))
    assert mixed.shape[0] >= expected_loops * 2000 * 48 - 2048
    assert _rms(mixed, 100, 2400) < 0.01  # Nothing replays from the clip on the first pass.
    assert 0.1 < _rms(mixed, 2600, 2900) < 0.25  # The clip after the loop point is intact.
    assert _rms(mixed, 3100, 3900) < 0.01
    manifest = services.repository.get_render_artifact_manifest(job.artifact_id)
    assert manifest["scene_output"]["video_loops"] == expected_loops
    assert manifest["duration_ms"] == expected_loops * 2000


def test_audio_past_the_end_of_a_held_output_is_an_error_not_silently_cut(
    tmp_path: Path,
) -> None:
    _, services, project_id, _, _, _, _, bake_id = _scene_with_output_audio(
        tmp_path, end_behavior="hold_last_frame", layer_start_ms=2500
    )
    job = _wait_for_job(
        services,
        services.render_jobs.create(
            project_id=project_id, composition_id=bake_id, actor="admin", expected_revision=2
        ).id,
    )
    assert job.state == "failed"
    assert "Set the output to loop, or move or trim the clip" in (job.error or "")
