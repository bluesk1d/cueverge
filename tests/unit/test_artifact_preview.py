from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from cueverge.gui.artifact_preview import (
    ArtifactPreviewError,
    extract_artifact_preview_frame,
)
from tests.unit.media_fixtures import write_video


@pytest.mark.parametrize(
    ("timestamp_ms", "expected_index"),
    [(0, 0), (1250, 5), (1499, 5), (1500, 6), (2900, 11)],
)
def test_extract_artifact_preview_frame_holds_the_displayed_frame(
    tmp_path: Path,
    timestamp_ms: int,
    expected_index: int,
) -> None:
    artifact = tmp_path / "artifact.mp4"
    frames = [np.full((16, 16, 3), index * 20, dtype=np.uint8) for index in range(12)]
    write_video(artifact, frames, rate=4, codec="libx264", pixel_format="yuv444p")

    frame = extract_artifact_preview_frame(
        artifact,
        timestamp_ms=timestamp_ms,
        duration_ms=3000,
    )

    assert frame.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(frame)) as image:
        assert image.size == (16, 16)
        luminance = int(np.asarray(image.convert("L")).mean())
    assert abs(luminance - expected_index * 20) <= 4


def test_extract_artifact_preview_frame_rejects_invalid_time(tmp_path: Path) -> None:
    with pytest.raises(ArtifactPreviewError, match="outside duration"):
        extract_artifact_preview_frame(
            tmp_path / "artifact.mp4",
            timestamp_ms=5000,
            duration_ms=5000,
        )


def test_extract_artifact_preview_frame_reports_undecodable_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.mp4"
    artifact.write_bytes(b"not a video")
    with pytest.raises(ArtifactPreviewError, match="could not be decoded"):
        extract_artifact_preview_frame(artifact, timestamp_ms=0, duration_ms=1000)
