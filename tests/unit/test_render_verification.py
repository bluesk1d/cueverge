import numpy as np
import pytest

from cueverge.gui.render_verification import (
    compare_audio,
    compare_rgb_frames,
    validate_loop_boundary,
    verification_policy_manifest,
)


def test_frame_verification_uses_conservative_rgb_tolerances() -> None:
    reference = np.zeros((4, 4, 3), dtype=np.uint8)
    acceptable = reference.copy()
    acceptable[0, 0] = [4, 4, 4]
    excessive = reference.copy()
    excessive[0, 0] = [5, 0, 0]

    accepted = compare_rgb_frames(reference, acceptable)
    rejected = compare_rgb_frames(reference, excessive)

    assert accepted.passed is True
    assert accepted.max_rgb_error == pytest.approx(4 / 255)
    assert rejected.passed is False


def test_audio_verification_checks_peak_and_loudness() -> None:
    reference = np.zeros((480, 2), dtype=np.float32)
    acceptable = np.full((480, 2), 0.001, dtype=np.float32)
    excessive = np.full((480, 2), 0.002, dtype=np.float32)

    assert compare_audio(
        reference,
        acceptable,
        reference_loudness_lufs=-16,
        candidate_loudness_lufs=-15.8,
    ).passed
    assert not compare_audio(
        reference,
        excessive,
        reference_loudness_lufs=-16,
        candidate_loudness_lufs=-15.8,
    ).passed
    assert not compare_audio(
        reference,
        acceptable,
        reference_loudness_lufs=-16,
        candidate_loudness_lufs=-15.7,
    ).passed


def test_verification_policy_manifest_is_versioned() -> None:
    assert verification_policy_manifest() == {
        "schema_version": 1,
        "verification_policy_revision": 1,
        "frame": {
            "mean_rgb_error_limit": 1 / 255,
            "max_rgb_error_limit": 4 / 255,
        },
        "audio": {
            "peak_error_dbfs_limit": -60.0,
            "loudness_error_lufs_limit": 0.2,
        },
    }


def test_gapless_loop_validation_checks_frame_audio_and_gop_boundaries() -> None:
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    audio = np.zeros((960, 2), dtype=np.float32)
    accepted = validate_loop_boundary(
        (frame, frame.copy(), frame.copy(), frame.copy()),
        audio,
        keyframe_interval_frames=2,
    )
    assert accepted.passed

    changed_frame = frame.copy()
    changed_frame[0, 0] = [10, 0, 0]
    changed_audio = audio.copy()
    changed_audio[-480:] = 0.01
    rejected = validate_loop_boundary(
        (frame, frame.copy(), changed_frame),
        changed_audio,
        keyframe_interval_frames=2,
    )
    assert rejected.passed is False
    assert any("frame mismatch" in item for item in rejected.diagnostics)
    assert any("GOP" in item for item in rejected.diagnostics)
    assert any("audio boundary" in item for item in rejected.diagnostics)
