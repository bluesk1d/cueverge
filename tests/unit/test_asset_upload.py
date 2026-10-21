import hashlib
import json
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pydantic import SecretStr

import cueverge.gui.asset_upload as asset_upload_module
from cueverge.core.loader import load_show_package
from cueverge.gui.asset_upload import (
    AssetUploadConflictError,
    AssetUploadError,
    AssetUploadService,
)
from cueverge.gui.config import GuiConfig
from cueverge.gui.contracts import DraftProject, Role
from cueverge.gui.repository import GuiRepository


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_gif_upload_preserves_animated_review_and_inspects_duration(tmp_path: Path) -> None:
    _, service, project_id = _service(tmp_path)
    output = BytesIO()
    Image.new("RGB", (2, 2), "red").save(
        output,
        format="GIF",
        save_all=True,
        append_images=[Image.new("RGB", (2, 2), "green")],
        duration=[100, 300],
        loop=0,
    )
    content = output.getvalue()
    asset, upload = service.begin(
        project_id=project_id,
        name="Animated overlay",
        original_filename="overlay.gif",
        media_type="image/gif",
        total_bytes=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-21T18:00:00Z",
    )
    service.append(
        upload_id=upload.id,
        offset=0,
        chunk=content,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-21T18:00:01Z",
    )
    service.process_asset(asset.id, occurred_at="2026-10-21T18:00:02Z")
    processed = service.get_asset(asset.id)
    assert processed.status == "ready", processed.error
    assert processed.metadata is not None
    assert processed.metadata["duration_ms"] == 400
    review, media_type = service.get_artifact(asset.id, "review")
    assert media_type == "image/gif"
    assert review.read_bytes() == content


def test_chunked_upload_resumes_verifies_and_deduplicates(tmp_path: Path) -> None:
    repository, service, project_id = _service(tmp_path)
    content = b"synthetic-media"
    digest = hashlib.sha256(content).hexdigest()
    asset, upload = service.begin(
        project_id=project_id,
        name="Synthetic media",
        original_filename="clip.mp4",
        media_type="video/mp4",
        total_bytes=len(content),
        expected_sha256=digest,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T01:00:00Z",
    )
    assert asset.status == "uploading"

    _, progress = service.append(
        upload_id=upload.id,
        offset=0,
        chunk=content[:5],
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T01:01:00Z",
    )
    assert progress.received_bytes == 5
    with pytest.raises(AssetUploadConflictError) as conflict:
        service.append(
            upload_id=upload.id,
            offset=0,
            chunk=content[5:],
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-14T01:02:00Z",
        )
    assert conflict.value.received_bytes == 5

    completed, final_upload = service.append(
        upload_id=upload.id,
        offset=5,
        chunk=content[5:],
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T01:03:00Z",
    )
    assert final_upload.state == "completed"
    assert completed.status == "uploading"
    assert completed.digest_sha256 == digest
    assert completed.scan_status == "unavailable"

    second, second_upload = service.begin(
        project_id=project_id,
        name="Duplicate content",
        original_filename="duplicate.mp4",
        media_type="video/mp4",
        total_bytes=len(content),
        expected_sha256=digest,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T01:04:00Z",
    )
    duplicated, _ = service.append(
        upload_id=second_upload.id,
        offset=0,
        chunk=content,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T01:05:00Z",
    )
    assert second.status == "uploading"
    assert duplicated.digest_sha256 == digest
    assert len(list((service.object_root / digest[:2]).iterdir())) == 1
    assert len(repository.list_audit_events()) >= 4


def test_upload_rejects_paths_types_sizes_and_bad_digest(tmp_path: Path) -> None:
    _, service, project_id = _service(tmp_path)
    with pytest.raises(AssetUploadError, match="plain filename"):
        service.begin(
            project_id=project_id,
            name="Escape",
            original_filename="../escape.mp4",
            media_type="video/mp4",
            total_bytes=1,
            expected_sha256=None,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-14T01:00:00Z",
        )
    with pytest.raises(AssetUploadError, match="not allowed"):
        service.begin(
            project_id=project_id,
            name="Executable",
            original_filename="payload.exe",
            media_type="application/octet-stream",
            total_bytes=1,
            expected_sha256=None,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-14T01:00:00Z",
        )

    _, upload = service.begin(
        project_id=project_id,
        name="Bad digest",
        original_filename="clip.mp4",
        media_type="video/mp4",
        total_bytes=3,
        expected_sha256="0" * 64,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T01:00:00Z",
    )
    asset, failed = service.append(
        upload_id=upload.id,
        offset=0,
        chunk=b"bad",
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T01:01:00Z",
    )
    assert failed.state == "failed"
    assert asset.status == "failed"
    assert asset.error is not None


def test_derived_artifacts_count_toward_store_quota(tmp_path: Path) -> None:
    _, service, project_id = _service(tmp_path)
    generated = service.artifact_root / "fixture.bin"
    generated.write_bytes(b"x" * 4090)

    with pytest.raises(AssetUploadError, match="quota"):
        service.begin(
            project_id=project_id,
            name="Over quota",
            original_filename="clip.mp4",
            media_type="video/mp4",
            total_bytes=14,
            expected_sha256=None,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-14T01:00:00Z",
        )


def test_processing_recovers_metadata_and_loudness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, service, project_id = _service(tmp_path)
    asset_id = _completed_asset(service, project_id)
    probe_document = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30000/1001",
                "duration": "2.5",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "sample_rate": "48000",
                "duration": "2.5",
            },
        ],
        "format": {"duration": "2.5", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    }

    def which(command: str) -> str | None:
        return None if command == "clamscan" else f"/usr/bin/{command}"

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if "ffprobe" in command[0]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(probe_document),
                stderr="",
            )
        if command[-1] != "-":
            Path(command[-1]).write_bytes(b"generated-artifact")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="",
            stderr="Summary:\n  I:         -18.2 LUFS\n",
        )

    monkeypatch.setattr(asset_upload_module.shutil, "which", which)
    monkeypatch.setattr(asset_upload_module.subprocess, "run", run)

    assert service.recover_pending_assets() == (asset_id,)
    processed = service.get_asset(asset_id)
    assert processed.status == "ready"
    assert processed.scan_status == "unavailable"
    assert processed.metadata is not None
    assert processed.metadata | {"artifacts": None} == {
        "duration_ms": 2500,
        "width": 1920,
        "height": 1080,
        "frame_rate": 29.97,
        "video_codecs": ["h264"],
        "audio_codecs": ["aac"],
        "channels": 2,
        "sample_rate": 48000,
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "loudness_lufs": -18.2,
        "loudness_status": "ready",
        "status": "ready",
        "artifacts": None,
    }
    artifacts = processed.metadata["artifacts"]
    assert isinstance(artifacts, dict)
    assert set(artifacts) == {"review", "thumbnail", "waveform"}
    assert processed.unused is True
    artifact_paths: dict[str, Path] = {}
    for kind, expected_media_type in (
        ("review", "video/mp4"),
        ("thumbnail", "image/jpeg"),
        ("waveform", "image/png"),
    ):
        path, media_type = service.get_artifact(asset_id, kind)
        artifact_paths[kind] = path
        assert path.read_bytes() == b"generated-artifact"
        assert media_type == expected_media_type
    assert processed.digest_sha256 is not None
    original = service.object_root / processed.digest_sha256[:2] / processed.digest_sha256
    assert original.read_bytes() == b"synthetic-media"
    revision = repository.get_revision(project_id)
    scenes = list(revision.draft.scenes)
    scene = scenes[0]
    if scene.schema_version == 1:
        scenes[0] = scene.model_copy(
            update={"media": scene.media.model_copy(update={"asset": asset_id})}
        )
    else:
        outputs = list(scene.media)
        outputs[0] = outputs[0].model_copy(update={"asset": asset_id})
        scenes[0] = scene.model_copy(update={"media": tuple(outputs)})
    repository.update_draft(
        project_id=project_id,
        expected_revision=revision.revision,
        draft=revision.draft.model_copy(update={"scenes": tuple(scenes)}),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T02:01:00Z",
    )
    referenced = service.get_asset(asset_id)
    assert referenced.unused is False
    assert referenced.references[0].scene_id == scene.id
    original.unlink()
    artifact_paths["waveform"].unlink()
    missing = service.get_asset(asset_id)
    assert missing.original_file_status == "missing"
    assert missing.artifact_faults == ("waveform: missing",)
    assert any(event.action == "asset.process" for event in repository.list_audit_events())


def test_processing_records_precise_invalid_media_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, service, project_id = _service(tmp_path)
    asset_id = _completed_asset(service, project_id)
    monkeypatch.setattr(
        asset_upload_module.shutil,
        "which",
        lambda command: None if command == "clamscan" else "/usr/bin/ffprobe",
    )
    monkeypatch.setattr(
        asset_upload_module.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="fixture.mp4: Invalid data found when processing input\n",
        ),
    )

    service.process_asset(asset_id, occurred_at="2026-10-14T02:00:00Z")

    processed = service.get_asset(asset_id)
    assert processed.status == "failed"
    assert processed.scan_status == "unavailable"
    assert processed.error == (
        "media metadata extraction failed: fixture.mp4: Invalid data found when processing input"
    )


def test_processing_quarantines_malware(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, service, project_id = _service(tmp_path)
    asset_id = _completed_asset(service, project_id)
    monkeypatch.setattr(
        asset_upload_module.shutil,
        "which",
        lambda command: "/usr/bin/clamscan" if command == "clamscan" else None,
    )
    monkeypatch.setattr(
        asset_upload_module.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            1,
            stdout=f"{command[-1]}: Test-Signature FOUND\n",
            stderr="",
        ),
    )

    service.process_asset(asset_id, occurred_at="2026-10-14T02:00:00Z")

    processed = service.get_asset(asset_id)
    assert processed.status == "quarantined"
    assert processed.scan_status == "quarantined"


def _completed_asset(service: AssetUploadService, project_id: str) -> str:
    content = b"synthetic-media"
    asset, upload = service.begin(
        project_id=project_id,
        name="Synthetic media",
        original_filename="clip.mp4",
        media_type="video/mp4",
        total_bytes=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T01:00:00Z",
    )
    service.append(
        upload_id=upload.id,
        offset=0,
        chunk=content,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T01:01:00Z",
    )
    return asset.id


def _service(tmp_path: Path) -> tuple[GuiRepository, AssetUploadService, str]:
    config = GuiConfig(
        data_root=tmp_path,
        admin_password_hash=SecretStr("unused"),
        allowed_origins=("http://localhost",),
        secure_cookies=False,
        asset_max_bytes=1024,
        asset_store_quota_bytes=4096,
        asset_chunk_max_bytes=512,
    )
    repository = GuiRepository(tmp_path / "gui.sqlite3")
    repository.migrate()
    catalog = load_show_package(Path("shows/demo"))
    revision = repository.create_project(
        name="Fixture",
        draft=DraftProject(
            schema_version=1,
            installation=catalog.installation,
            scenes=tuple(
                catalog.scenes[scene_id].definition for scene_id in sorted(catalog.scenes)
            ),
        ),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T00:00:00Z",
    )
    return repository, AssetUploadService(config, repository), revision.project.id
