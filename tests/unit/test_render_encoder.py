import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from cueverge.gui.render_encoder import (
    EncoderProfile,
    RenderEncodingError,
    encode_output_set,
)


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required",
)
def test_output_set_encodes_common_video_and_audio_only_on_owner(
    tmp_path: Path,
) -> None:
    profile = EncoderProfile(
        video_profile="baseline",
        video_level="3.1",
        pixel_format="yuv420p",
        max_video_bitrate_kbps=1000,
        keyframe_interval_frames=2,
        b_frames=0,
        loudness_target_lufs=-16,
    )
    left_frames = _frames((255, 0, 0))
    right_frames = _frames((0, 255, 0))
    audio = np.zeros((9600, 2), dtype=np.float32)

    manifest = encode_output_set(
        output_directory=tmp_path,
        members={
            "projector.left": left_frames,
            "projector.right": right_frames,
        },
        audio_owner="projector.left",
        audio_samples=audio,
        frame_rate_numerator=10,
        frame_rate_denominator=1,
        duration_ms=200,
        profile=profile,
        source_revision=7,
        composition_id="stage-main",
        composition_digest="b" * 64,
        source_checksums={"ghost.png": "a" * 64},
        calibration_revisions={"projector.left": 3, "projector.right": 4},
        media_end_behavior="hold_last_frame",
    )

    output_root = tmp_path / str(manifest["activation_identity"])
    stored = json.loads((output_root / "render-manifest.json").read_text(encoding="utf-8"))
    assert stored["audio_owner"] == "projector.left"
    assert stored["duration_ms"] == 200
    assert stored["media_end_behavior"] == "hold_last_frame"
    assert stored["hold_last_frame"] is True
    assert stored["frame_rate"] == {"numerator": 10, "denominator": 1}
    assert stored["calibration_revisions"] == {
        "projector.left": 3,
        "projector.right": 4,
    }
    assert stored["composition_digest"] == "b" * 64
    assert stored["source_checksums"] == {"ghost.png": "a" * 64}
    assert stored["encoder"]["identity"].startswith("ffmpeg version ")
    members = {item["resource"]: item for item in stored["members"]}
    assert members["projector.left"]["has_audio"] is True
    assert members["projector.right"]["has_audio"] is False
    assert members["projector.left"]["streams"]["audio"]["boundary"]["metadata_inspected"] is True
    assert members["projector.left"]["streams"]["audio"]["boundary"]["first_packet_pts_time"] <= 0
    for resource, member in members.items():
        probe = _probe(output_root / member["filename"])
        types = [stream["codec_type"] for stream in probe["streams"]]
        assert "video" in types
        assert ("audio" in types) is (resource == "projector.left")
        assert member["digest_sha256"]
        assert member["size_bytes"] > 0
        assert member["streams"]["video"]["frame_count"] == 2
        assert (member["streams"]["audio"] is not None) is (resource == "projector.left")

    repeated = encode_output_set(
        output_directory=tmp_path / "repeated",
        members={
            "projector.left": left_frames,
            "projector.right": right_frames,
        },
        audio_owner="projector.left",
        audio_samples=audio,
        frame_rate_numerator=10,
        frame_rate_denominator=1,
        duration_ms=200,
        profile=profile,
        source_revision=7,
        composition_id="stage-main",
        composition_digest="b" * 64,
        source_checksums={"ghost.png": "a" * 64},
        calibration_revisions={"projector.left": 3, "projector.right": 4},
        media_end_behavior="hold_last_frame",
    )
    assert {member["resource"]: member["digest_sha256"] for member in stored["members"]} == {
        member["resource"]: member["digest_sha256"] for member in repeated["members"]
    }


def _frames(color: tuple[int, int, int]) -> tuple[np.ndarray, ...]:
    frame = np.zeros((36, 64, 4), dtype=np.uint8)
    frame[..., :3] = color
    frame[..., 3] = 255
    return (frame.copy(), frame.copy())


def _probe(path: Path) -> dict[str, object]:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(completed.stdout)


def test_output_set_rejects_invalid_gapless_loop_before_encoding(
    tmp_path: Path,
) -> None:
    profile = EncoderProfile("baseline", "3.1", "yuv420p", 1000, 2, 0, -16)
    frames = list(_frames((255, 0, 0)))
    frames[-1] = np.zeros_like(frames[-1])
    with pytest.raises(RenderEncodingError, match="gapless loop validation failed"):
        encode_output_set(
            output_directory=tmp_path,
            members={"projector.left": tuple(frames)},
            audio_owner="projector.left",
            audio_samples=np.zeros((9600, 2), dtype=np.float32),
            frame_rate_numerator=10,
            frame_rate_denominator=1,
            duration_ms=200,
            profile=profile,
            source_revision=1,
            composition_id="loop",
            composition_digest="b" * 64,
            source_checksums={},
            calibration_revisions={"projector.left": 1},
            media_end_behavior="loop",
        )
