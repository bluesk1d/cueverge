import subprocess
from pathlib import Path

import pytest

from cueverge.gui.artifact_preview import (
    ArtifactPreviewError,
    extract_artifact_preview_frame,
)


def test_extract_artifact_preview_frame_uses_exact_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.mp4"
    artifact.write_bytes(b"video")
    captured: list[str] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        captured.extend(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=b"\x89PNG\r\n\x1a\nframe",
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", run)

    frame = extract_artifact_preview_frame(
        artifact,
        timestamp_ms=1250,
        duration_ms=5000,
    )

    assert frame == b"\x89PNG\r\n\x1a\nframe"
    assert captured[captured.index("-ss") + 1] == "1.250000"
    assert captured[captured.index("-i") + 1] == str(artifact)


def test_extract_artifact_preview_frame_rejects_invalid_time(tmp_path: Path) -> None:
    with pytest.raises(ArtifactPreviewError, match="outside duration"):
        extract_artifact_preview_frame(
            tmp_path / "artifact.mp4",
            timestamp_ms=5000,
            duration_ms=5000,
        )
