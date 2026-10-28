import hashlib
import json
import sqlite3
import subprocess
import sys
import wave
from fractions import Fraction
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from pydantic import SecretStr

import cueverge.gui.asset_upload as asset_upload_module
from cueverge.core.loader import load_show_package
from cueverge.gui import media_tools
from cueverge.gui.asset_upload import (
    AssetMetadataConflictError,
    AssetMetadataLockedError,
    AssetPurgeConfirmationError,
    AssetRemovalBlockedError,
    AssetTrashStateError,
    AssetUploadConflictError,
    AssetUploadError,
    AssetUploadService,
)
from cueverge.gui.config import GuiConfig
from cueverge.gui.contracts import DraftProject, Role
from cueverge.gui.repository import GuiRepository
from tests.unit.media_fixtures import stream_types


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

    operations: list[str] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert command[:3] == [sys.executable, "-m", "cueverge.gui.media_tools"]
        operations.append(command[3])
        if command[3] == "probe":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(probe_document),
                stderr="",
            )
        if command[3] == "loudness":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"integrated_lufs": -18.2}),
                stderr="",
            )
        Path(command[-1]).write_bytes(b"generated-artifact")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(asset_upload_module.shutil, "which", which)
    monkeypatch.setattr(asset_upload_module.subprocess, "run", run)

    assert service.recover_pending_assets() == (asset_id,)
    assert operations == ["probe", "loudness", "thumbnail", "video-review", "waveform"]
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
        lambda command: None,
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


@pytest.mark.parametrize(
    ("filename", "media_type", "expected"),
    [
        (
            "clip.mp4",
            "video/mp4",
            {
                "review": "video/mp4",
                "thumbnail": "image/jpeg",
                "waveform": "image/png",
            },
        ),
        ("tone.mp3", "audio/mpeg", {"review": "audio/mpeg", "waveform": "image/png"}),
        ("tone.wav", "audio/wav", {"review": "audio/mpeg", "waveform": "image/png"}),
        ("still.png", "image/png", {"review": "image/jpeg", "thumbnail": "image/jpeg"}),
    ],
)
def test_processing_generates_real_metadata_and_artifacts_with_pyav(
    tmp_path: Path,
    filename: str,
    media_type: str,
    expected: dict[str, str],
) -> None:
    _, service, project_id = _service(
        tmp_path,
        asset_max_bytes=1024 * 1024,
        asset_store_quota_bytes=16 * 1024 * 1024,
        asset_chunk_max_bytes=1024 * 1024,
    )
    source = tmp_path / filename
    _write_media_fixture(source)
    content = source.read_bytes()
    asset, upload = service.begin(
        project_id=project_id,
        name=filename,
        original_filename=filename,
        media_type=media_type,
        total_bytes=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-23T18:00:00Z",
    )
    service.append(
        upload_id=upload.id,
        offset=0,
        chunk=content,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-23T18:00:01Z",
    )

    service.process_asset(asset.id, occurred_at="2026-10-23T18:00:02Z")

    processed = service.get_asset(asset.id)
    assert processed.status == "ready", processed.error
    assert processed.metadata is not None
    has_audio = "waveform" in expected
    if media_type != "image/png":
        assert processed.metadata["duration_ms"] == pytest.approx(1000, abs=50)
    if has_audio:
        assert processed.metadata["sample_rate"] == 48000
        loudness = processed.metadata["loudness_lufs"]
        assert isinstance(loudness, float)
        assert -30 < loudness < 0
        assert processed.metadata["loudness_status"] == "ready"
    else:
        assert processed.metadata["loudness_status"] == "not_applicable"
    if not media_type.startswith("audio/"):
        assert (processed.metadata["width"], processed.metadata["height"]) == (64, 36)
    artifacts = processed.metadata["artifacts"]
    assert isinstance(artifacts, dict)
    assert set(artifacts) == set(expected)
    for kind, expected_media_type in expected.items():
        path, artifact_media_type = service.get_artifact(asset.id, kind)
        assert artifact_media_type == expected_media_type
        assert path.stat().st_size > 0
        if expected_media_type.startswith("image/"):
            with Image.open(path) as image:
                assert image.format == expected_media_type.split("/")[1].upper()
        else:
            assert stream_types(path) >= (
                {"video"} if kind == "review" and media_type == "video/mp4" else {"audio"}
            )


@pytest.mark.parametrize(
    ("filename", "declared", "stored"),
    [
        ("take.wav", "audio/wav", "audio/wav"),
        ("take.wav", "audio/x-wav", "audio/wav"),
        ("take.wav", "audio/wave", "audio/wav"),
        ("take.WAV", "", "audio/wav"),
        ("take.flac", "application/octet-stream", "audio/flac"),
        ("photo.jpg", "image/jpg", "image/jpeg"),
        ("song.mp3", "audio/mp3", "audio/mpeg"),
    ],
)
def test_upload_accepts_browser_and_platform_media_type_names(
    tmp_path: Path, filename: str, declared: str, stored: str
) -> None:
    _, service, project_id = _service(tmp_path)
    _, upload = service.begin(
        project_id=project_id,
        name=filename,
        original_filename=filename,
        media_type=declared,
        total_bytes=16,
        expected_sha256=None,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-27T18:00:00Z",
    )
    with service._connect() as connection:
        row = connection.execute(
            "SELECT media_type FROM asset_uploads WHERE id = ?", (upload.id,)
        ).fetchone()
    assert row["media_type"] == stored


def test_upload_still_rejects_mismatched_and_unsupported_types(tmp_path: Path) -> None:
    _, service, project_id = _service(tmp_path)
    for filename, declared, message in (
        ("take.wav", "video/mp4", "does not match filename type audio/wav"),
        ("notes.txt", "", "media type is not allowed"),
        ("take.aiff", "audio/aiff", "media type is not allowed: audio/aiff"),
    ):
        with pytest.raises(AssetUploadError, match=message):
            service.begin(
                project_id=project_id,
                name=filename,
                original_filename=filename,
                media_type=declared,
                total_bytes=16,
                expected_sha256=None,
                actor="admin",
                role=Role.ADMINISTRATOR,
                occurred_at="2026-10-27T18:00:00Z",
            )


def _write_media_fixture(path: Path) -> None:
    samples = (np.sin(2 * np.pi * 440 * np.arange(48000) / 48000) * 0.25).astype(np.float32)
    if path.suffix == ".png":
        Image.new("RGB", (64, 36), "orange").save(path)
    elif path.suffix in {".mp3", ".wav"}:
        wav = path.with_suffix(".wav")
        with wave.open(str(wav), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(48000)
            output.writeframes((samples * 32767).astype("<i2").tobytes())
        if path.suffix == ".mp3":
            media_tools.write_audio_review(wav, path)
    else:
        frames = tuple(
            np.full((36, 64, 3), [index * 20, 80, 160], dtype=np.uint8) for index in range(10)
        )
        media_tools.encode_mp4(
            frames=frames,
            audio_samples=np.stack([samples, samples], axis=1),
            frame_rate=Fraction(10),
            video_profile="baseline",
            video_level="3.1",
            pixel_format="yuv420p",
            video_bitrate_kbps=500,
            keyframe_interval_frames=10,
            b_frames=0,
            target=path,
        )


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


def _service(
    tmp_path: Path,
    *,
    asset_max_bytes: int = 1024,
    asset_store_quota_bytes: int = 4096,
    asset_chunk_max_bytes: int = 512,
) -> tuple[GuiRepository, AssetUploadService, str]:
    config = GuiConfig(
        data_root=tmp_path,
        admin_password_hash=SecretStr("unused"),
        allowed_origins=("http://localhost",),
        secure_cookies=False,
        asset_max_bytes=asset_max_bytes,
        asset_store_quota_bytes=asset_store_quota_bytes,
        asset_chunk_max_bytes=asset_chunk_max_bytes,
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


def test_trash_restore_and_purge_keep_shared_content_until_last_reference(
    tmp_path: Path,
) -> None:
    repository, service, project_id = _service(tmp_path)
    service.object_cleanup_grace_seconds = 0
    first_id = _completed_asset(service, project_id)
    second_id = _completed_asset(service, project_id)
    first = service.get_asset(first_id)
    assert first.digest_sha256 is not None
    object_path = service.object_root / first.digest_sha256[:2] / first.digest_sha256
    assert first.unused and first.removal_blockers == ()

    trashed = service.trash(
        project_id=project_id,
        asset_id=first_id,
        expected_revision=first.metadata_revision,
        actor="designer",
        role=Role.DESIGNER,
        occurred_at="2026-10-20T12:00:00Z",
    )
    assert trashed.trashed_at == "2026-10-20T12:00:00Z"
    assert trashed.trashed_by == "designer"
    assert trashed.purge_after == "2026-11-19T12:00:00Z"
    assert [item.id for item in service.list_assets(project_id)] == [second_id]
    assert [item.id for item in service.list_assets(project_id, trashed=True)] == [first_id]
    assert {item.id for item in service.list_assets(project_id, trashed=None)} == {
        first_id,
        second_id,
    }
    with pytest.raises(AssetMetadataLockedError):
        service.update_metadata(
            asset_id=first_id,
            expected_revision=trashed.metadata_revision,
            owner=None,
            license_name=None,
            provenance=None,
            review_status="unreviewed",
            expires_on=None,
            folder=None,
            tags=(),
            favorite=False,
            actor="designer",
            role=Role.DESIGNER,
            occurred_at="2026-10-20T12:01:00Z",
        )
    with pytest.raises(AssetTrashStateError):
        service.trash(
            project_id=project_id,
            asset_id=first_id,
            expected_revision=trashed.metadata_revision,
            actor="designer",
            role=Role.DESIGNER,
            occurred_at="2026-10-20T12:02:00Z",
        )
    with pytest.raises(AssetUploadError):
        service.restore(
            project_id="another-project",
            asset_id=first_id,
            expected_revision=trashed.metadata_revision,
            actor="designer",
            role=Role.DESIGNER,
            occurred_at="2026-10-20T12:03:00Z",
        )

    restored = service.restore(
        project_id=project_id,
        asset_id=first_id,
        expected_revision=trashed.metadata_revision,
        actor="designer",
        role=Role.DESIGNER,
        occurred_at="2026-10-20T12:04:00Z",
    )
    assert restored.trashed_at is None and restored.purge_after is None
    assert restored.digest_sha256 == first.digest_sha256
    with pytest.raises(AssetMetadataConflictError):
        service.trash(
            project_id=project_id,
            asset_id=first_id,
            expected_revision=trashed.metadata_revision,
            actor="designer",
            role=Role.DESIGNER,
            occurred_at="2026-10-20T12:05:00Z",
        )
    trashed = service.trash(
        project_id=project_id,
        asset_id=first_id,
        expected_revision=restored.metadata_revision,
        actor="designer",
        role=Role.DESIGNER,
        occurred_at="2026-10-20T12:06:00Z",
    )
    with pytest.raises(AssetPurgeConfirmationError):
        service.purge(
            project_id=project_id,
            asset_id=first_id,
            expected_revision=trashed.metadata_revision,
            confirmation_name="wrong name",
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-20T12:07:00Z",
        )
    purged = service.purge(
        project_id=project_id,
        asset_id=first_id,
        expected_revision=trashed.metadata_revision,
        confirmation_name="Synthetic media",
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-20T12:08:00Z",
    )
    assert purged.released_objects == 0
    assert object_path.is_file()
    with pytest.raises(AssetUploadError):
        service.get_asset(first_id)

    second = service.get_asset(second_id)
    second_trashed = service.trash(
        project_id=project_id,
        asset_id=second_id,
        expected_revision=second.metadata_revision,
        actor="designer",
        role=Role.DESIGNER,
        occurred_at="2026-10-20T12:09:00Z",
    )
    purged = service.purge(
        project_id=project_id,
        asset_id=second_id,
        expected_revision=second_trashed.metadata_revision,
        confirmation_name="Synthetic media",
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-20T12:10:00Z",
    )
    assert purged.released_objects == 1
    assert purged.released_bytes == len(b"synthetic-media")
    assert not object_path.exists()
    actions = [event.action for event in repository.list_audit_events()]
    assert {"asset.trash", "asset.restore", "asset.purge"}.issubset(actions)


def test_trash_is_blocked_by_live_draft_use_but_not_revision_history(tmp_path: Path) -> None:
    repository, service, project_id = _service(tmp_path)
    asset_id = _completed_asset(service, project_id)
    head = repository.get_revision(project_id)
    bindings_only = repository.update_draft(
        project_id=project_id,
        expected_revision=head.revision,
        draft=head.draft.model_copy(
            update={"editor_metadata": {"asset_bindings": {asset_id: 1}}},
        ),
        actor="designer",
        role=Role.DESIGNER,
        occurred_at="2026-10-20T12:00:00Z",
    )
    assert service.get_asset(asset_id).unused is True
    used = repository.update_draft(
        project_id=project_id,
        expected_revision=bindings_only.revision,
        draft=bindings_only.draft.model_copy(
            update={
                "editor_metadata": {
                    "asset_bindings": {asset_id: 1},
                    "scene_presets": {
                        "preset": {
                            "id": "preset",
                            "name": "Window preset",
                            "template": {"media": {"asset": asset_id}},
                        }
                    },
                }
            },
        ),
        actor="designer",
        role=Role.DESIGNER,
        occurred_at="2026-10-20T12:01:00Z",
    )
    asset = service.get_asset(asset_id)
    assert asset.unused is False
    assert asset.references == ()
    assert asset.draft_usage == ("scene presets",)
    assert "scene presets" in asset.removal_blockers[0]
    with pytest.raises(AssetRemovalBlockedError) as blocked:
        service.trash(
            project_id=project_id,
            asset_id=asset_id,
            expected_revision=asset.metadata_revision,
            actor="designer",
            role=Role.DESIGNER,
            occurred_at="2026-10-20T12:02:00Z",
        )
    assert blocked.value.blockers == asset.removal_blockers

    repository.update_draft(
        project_id=project_id,
        expected_revision=used.revision,
        draft=used.draft.model_copy(update={"editor_metadata": {}}),
        actor="designer",
        role=Role.DESIGNER,
        occurred_at="2026-10-20T12:03:00Z",
    )
    trashed = service.trash(
        project_id=project_id,
        asset_id=asset_id,
        expected_revision=asset.metadata_revision,
        actor="designer",
        role=Role.DESIGNER,
        occurred_at="2026-10-20T12:04:00Z",
    )
    assert trashed.trashed_at is not None
    assert trashed.purge_blockers == ()


def test_trash_sweep_purges_expired_assets_and_retains_published_releases(
    tmp_path: Path,
) -> None:
    repository, service, project_id = _service(tmp_path)
    service.object_cleanup_grace_seconds = 0
    asset_id = _completed_asset(service, project_id)
    asset = service.get_asset(asset_id)
    assert asset.digest_sha256 is not None
    object_path = service.object_root / asset.digest_sha256[:2] / asset.digest_sha256
    stray_digest = hashlib.sha256(b"orphan").hexdigest()
    stray_path = service.object_root / stray_digest[:2] / stray_digest
    stray_path.parent.mkdir(parents=True, exist_ok=True)
    stray_path.write_bytes(b"orphan")
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            """
            CREATE TABLE published_packages (
                id TEXT PRIMARY KEY, project_id TEXT, revision INTEGER, state TEXT,
                manifest_json TEXT, created_at TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO published_packages VALUES ('package', ?, 1, 'inactive', ?, 'now')",
            (project_id, json.dumps({"assets": [{"id": asset_id}]})),
        )
    trashed = service.trash(
        project_id=project_id,
        asset_id=asset_id,
        expected_revision=asset.metadata_revision,
        actor="designer",
        role=Role.DESIGNER,
        occurred_at="2026-10-01T00:00:00Z",
    )
    assert trashed.removal_blockers == ()
    assert "Kept for rollback" in trashed.purge_blockers[0]

    early = service.sweep_trash(occurred_at="2026-10-30T00:00:00Z")
    assert early.purged_asset_ids == ()
    assert early.retained == {}
    assert early.released_objects == 1
    assert not stray_path.exists()

    retained = service.sweep_trash(occurred_at="2026-11-01T00:00:00Z")
    assert retained.purged_asset_ids == ()
    assert "Kept for rollback" in retained.retained[asset_id][0]
    assert object_path.is_file()

    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("DELETE FROM published_packages")
    swept = service.sweep_trash(occurred_at="2026-11-01T00:00:00Z")
    assert swept.purged_asset_ids == (asset_id,)
    assert swept.released_objects == 1
    assert not object_path.exists()
    with pytest.raises(AssetUploadError):
        service.get_asset(asset_id)


def test_trash_cancels_an_unfinished_upload(tmp_path: Path) -> None:
    _, service, project_id = _service(tmp_path)
    content = b"partial-upload"
    asset, upload = service.begin(
        project_id=project_id,
        name="Abandoned",
        original_filename="clip.mp4",
        media_type="video/mp4",
        total_bytes=len(content),
        expected_sha256=None,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T01:00:00Z",
    )
    service.append(
        upload_id=upload.id,
        offset=0,
        chunk=content[:4],
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T01:01:00Z",
    )
    assert len(list(service.upload_root.iterdir())) == 1
    trashed = service.trash(
        project_id=project_id,
        asset_id=asset.id,
        expected_revision=asset.metadata_revision,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T01:02:00Z",
    )
    assert trashed.status == "failed"
    assert service.get_upload(upload.id).state == "failed"
    assert list(service.upload_root.iterdir()) == []
    with pytest.raises(AssetUploadError):
        service.append(
            upload_id=upload.id,
            offset=4,
            chunk=content[4:],
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-14T01:03:00Z",
        )


def test_draft_usage_reports_scenes_and_editor_data_but_not_stale_bindings() -> None:
    document = {
        "scenes": [{"id": "ambient", "name": "Ambient", "media": [{"asset": "asset-1"}]}],
        "editor_metadata": {
            "asset_bindings": {"asset-1": 1, "asset-2": 1},
            "timeline_transform_layers": {"stage": [{"source_id": "asset-1"}]},
        },
    }
    assert asset_upload_module._draft_usage(document, "asset-1") == (
        'scene "Ambient"',
        "timeline media layers",
    )
    assert asset_upload_module._draft_usage(document, "asset-2") == ()


def _png(color: tuple[int, int, int], size: tuple[int, int] = (8, 6)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def _ready_png(
    service: AssetUploadService,
    project_id: str,
    name: str,
    content: bytes,
) -> str:
    asset, upload = service.begin(
        project_id=project_id,
        name=name,
        original_filename=f"{name}.png",
        media_type="image/png",
        total_bytes=len(content),
        expected_sha256=None,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-25T10:00:00Z",
    )
    service.append(
        upload_id=upload.id,
        offset=0,
        chunk=content,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-25T10:00:01Z",
    )
    service.process_asset(asset.id, occurred_at="2026-10-25T10:00:02Z")
    ready = service.get_asset(asset.id)
    assert ready.status == "ready", ready.error
    return asset.id


def _upload_replacement(
    service: AssetUploadService,
    project_id: str,
    asset_id: str,
    content: bytes,
    *,
    filename: str = "edited.png",
    media_type: str = "image/png",
) -> None:
    asset = service.get_asset(asset_id)
    _, upload = service.begin_replacement(
        project_id=project_id,
        asset_id=asset_id,
        expected_revision=asset.metadata_revision,
        original_filename=filename,
        media_type=media_type,
        total_bytes=len(content),
        expected_sha256=None,
        actor="designer",
        role=Role.DESIGNER,
        occurred_at="2026-10-25T11:00:00Z",
    )
    service.append(
        upload_id=upload.id,
        offset=0,
        chunk=content,
        actor="designer",
        role=Role.DESIGNER,
        occurred_at="2026-10-25T11:00:01Z",
    )


def test_replacement_keeps_asset_id_and_promotes_only_after_processing(tmp_path: Path) -> None:
    repository, service, project_id = _service(
        tmp_path, asset_max_bytes=64 * 1024, asset_store_quota_bytes=4 * 1024 * 1024
    )
    original = _png((200, 20, 20))
    edited = _png((20, 200, 20))
    asset_id = _ready_png(service, project_id, "poster", original)
    before = service.get_asset(asset_id)

    _upload_replacement(service, project_id, asset_id, edited)

    pending = service.get_asset(asset_id)
    assert pending.id == asset_id
    assert pending.status == "ready"
    assert pending.current_revision == 1
    assert pending.digest_sha256 == before.digest_sha256
    assert pending.pending_revision == 2
    assert pending.replacement_status == "processing"
    assert pending.metadata_revision == before.metadata_revision + 1
    with pytest.raises(AssetUploadError, match="still processing"):
        _upload_replacement(service, project_id, asset_id, _png((1, 2, 3)))

    service.process_asset(asset_id, occurred_at="2026-10-25T11:00:02Z")

    replaced = service.get_asset(asset_id)
    assert replaced.status == "ready", replaced.error
    assert replaced.current_revision == 2
    assert replaced.pending_revision is None
    assert replaced.replacement_status is None
    assert replaced.name == "poster"
    assert replaced.original_filename == "edited.png"
    assert replaced.digest_sha256 == hashlib.sha256(edited).hexdigest()
    assert [(item.revision, item.status) for item in replaced.revisions] == [
        (2, "ready"),
        (1, "ready"),
    ]
    path, _ = service.get_artifact(asset_id, "review")
    assert hashlib.sha256(edited).hexdigest() in str(path)

    with pytest.raises(AssetMetadataConflictError):
        service.revert_revision(
            project_id=project_id,
            asset_id=asset_id,
            revision=1,
            expected_revision=before.metadata_revision,
            actor="designer",
            role=Role.DESIGNER,
            occurred_at="2026-10-25T12:00:00Z",
        )
    reverted = service.revert_revision(
        project_id=project_id,
        asset_id=asset_id,
        revision=1,
        expected_revision=replaced.metadata_revision,
        actor="designer",
        role=Role.DESIGNER,
        occurred_at="2026-10-25T12:00:00Z",
    )
    assert reverted.current_revision == 1
    assert reverted.digest_sha256 == before.digest_sha256
    actions = [event.action for event in repository.list_audit_events()]
    assert {"asset.replace.begin", "asset.replace.process", "asset.revert"} <= set(actions)


def test_replacement_requires_same_media_kind_and_supersedes_unfinished_upload(
    tmp_path: Path,
) -> None:
    _, service, project_id = _service(
        tmp_path, asset_max_bytes=64 * 1024, asset_store_quota_bytes=4 * 1024 * 1024
    )
    asset_id = _ready_png(service, project_id, "poster", _png((200, 20, 20)))
    asset = service.get_asset(asset_id)
    with pytest.raises(AssetUploadError, match="must be image media"):
        service.begin_replacement(
            project_id=project_id,
            asset_id=asset_id,
            expected_revision=asset.metadata_revision,
            original_filename="clip.mp4",
            media_type="video/mp4",
            total_bytes=10,
            expected_sha256=None,
            actor="designer",
            role=Role.DESIGNER,
            occurred_at="2026-10-25T11:00:00Z",
        )
    _, abandoned = service.begin_replacement(
        project_id=project_id,
        asset_id=asset_id,
        expected_revision=asset.metadata_revision,
        original_filename="first.jpg",
        media_type="image/jpeg",
        total_bytes=10,
        expected_sha256=None,
        actor="designer",
        role=Role.DESIGNER,
        occurred_at="2026-10-25T11:00:00Z",
    )
    assert service.get_asset(asset_id).replacement_status == "uploading"

    edited = _png((20, 20, 200))
    _upload_replacement(service, project_id, asset_id, edited)

    assert service.get_upload(abandoned.id).state == "failed"
    assert not (service.upload_root / f"{abandoned.id}.part").exists()
    service.process_asset(asset_id, occurred_at="2026-10-25T11:00:02Z")
    replaced = service.get_asset(asset_id)
    assert replaced.current_revision == 2
    assert replaced.digest_sha256 == hashlib.sha256(edited).hexdigest()


def test_failed_replacement_is_discarded_and_current_file_stays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, service, project_id = _service(
        tmp_path, asset_max_bytes=64 * 1024, asset_store_quota_bytes=4 * 1024 * 1024
    )
    asset_id = _ready_png(service, project_id, "poster", _png((200, 20, 20)))
    before = service.get_asset(asset_id)
    _upload_replacement(service, project_id, asset_id, b"not really a png")

    service.process_asset(asset_id, occurred_at="2026-10-25T11:00:02Z")

    after = service.get_asset(asset_id)
    assert after.status == "ready"
    assert after.current_revision == 1
    assert after.digest_sha256 == before.digest_sha256
    assert after.pending_revision is None
    assert after.replacement_error is not None
    assert after.replacement_error.startswith("Replacement failed")
    assert [item.revision for item in after.revisions] == [1]
    with pytest.raises(AssetUploadError, match="does not exist"):
        service.revert_revision(
            project_id=project_id,
            asset_id=asset_id,
            revision=2,
            expected_revision=after.metadata_revision,
            actor="designer",
            role=Role.DESIGNER,
            occurred_at="2026-10-25T12:00:00Z",
        )

    edited = _png((20, 200, 20))
    _upload_replacement(service, project_id, asset_id, edited)
    service.process_asset(asset_id, occurred_at="2026-10-25T12:00:02Z")
    retried = service.get_asset(asset_id)
    assert retried.current_revision == 2
    assert retried.replacement_error is None
    assert retried.digest_sha256 == hashlib.sha256(edited).hexdigest()


def test_replace_from_asset_reuses_processed_file_without_copying(tmp_path: Path) -> None:
    _, service, project_id = _service(
        tmp_path, asset_max_bytes=64 * 1024, asset_store_quota_bytes=4 * 1024 * 1024
    )
    target_id = _ready_png(service, project_id, "poster", _png((200, 20, 20)))
    edited = _png((20, 200, 20))
    source_id = _ready_png(service, project_id, "poster-edit", edited)
    target = service.get_asset(target_id)
    objects_before = sorted(path.name for path in service.object_root.rglob("*") if path.is_file())

    replaced = service.replace_from_asset(
        project_id=project_id,
        asset_id=target_id,
        source_asset_id=source_id,
        expected_revision=target.metadata_revision,
        actor="designer",
        role=Role.DESIGNER,
        occurred_at="2026-10-25T11:00:00Z",
    )

    assert replaced.id == target_id
    assert replaced.name == "poster"
    assert replaced.status == "ready"
    assert replaced.current_revision == 2
    assert replaced.digest_sha256 == hashlib.sha256(edited).hexdigest()
    assert replaced.metadata is not None and replaced.metadata["status"] == "ready"
    service.get_artifact(target_id, "review")
    assert service.get_asset(source_id).current_revision == 1
    objects_after = sorted(path.name for path in service.object_root.rglob("*") if path.is_file())
    assert objects_after == objects_before
    with pytest.raises(AssetUploadError, match="different asset"):
        service.replace_from_asset(
            project_id=project_id,
            asset_id=target_id,
            source_asset_id=target_id,
            expected_revision=replaced.metadata_revision,
            actor="designer",
            role=Role.DESIGNER,
            occurred_at="2026-10-25T11:00:00Z",
        )


def test_trash_releases_unfinished_replacement_and_blocks_new_ones(tmp_path: Path) -> None:
    _, service, project_id = _service(
        tmp_path, asset_max_bytes=64 * 1024, asset_store_quota_bytes=4 * 1024 * 1024
    )
    asset_id = _ready_png(service, project_id, "poster", _png((200, 20, 20)))
    asset = service.get_asset(asset_id)
    _, upload = service.begin_replacement(
        project_id=project_id,
        asset_id=asset_id,
        expected_revision=asset.metadata_revision,
        original_filename="edited.png",
        media_type="image/png",
        total_bytes=10,
        expected_sha256=None,
        actor="designer",
        role=Role.DESIGNER,
        occurred_at="2026-10-25T11:00:00Z",
    )
    trashed = service.trash(
        project_id=project_id,
        asset_id=asset_id,
        expected_revision=asset.metadata_revision + 1,
        actor="designer",
        role=Role.DESIGNER,
        occurred_at="2026-10-25T11:01:00Z",
    )
    assert trashed.pending_revision is None
    assert trashed.current_revision == 1
    assert service.get_upload(upload.id).state == "failed"
    with pytest.raises(AssetTrashStateError):
        service.begin_replacement(
            project_id=project_id,
            asset_id=asset_id,
            expected_revision=trashed.metadata_revision,
            original_filename="edited.png",
            media_type="image/png",
            total_bytes=10,
            expected_sha256=None,
            actor="designer",
            role=Role.DESIGNER,
            occurred_at="2026-10-25T11:02:00Z",
        )
