from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

VERIFICATION_POLICY_REVISION: Final = 1
MEAN_RGB_ERROR_LIMIT: Final = 1 / 255
MAX_RGB_ERROR_LIMIT: Final = 4 / 255
AUDIO_PEAK_ERROR_DBFS_LIMIT: Final = -60.0
LOUDNESS_ERROR_LUFS_LIMIT: Final = 0.2


@dataclass(frozen=True)
class FrameComparison:
    mean_rgb_error: float
    max_rgb_error: float
    passed: bool


@dataclass(frozen=True)
class AudioComparison:
    peak_error_dbfs: float
    loudness_error_lufs: float
    passed: bool


@dataclass(frozen=True)
class LoopBoundaryValidation:
    passed: bool
    diagnostics: tuple[str, ...]


def compare_rgb_frames(
    reference: NDArray[np.uint8],
    candidate: NDArray[np.uint8],
) -> FrameComparison:
    if reference.shape != candidate.shape or reference.ndim != 3:
        raise ValueError("reference frames must have identical HxWxC shapes")
    if reference.shape[2] < 3:
        raise ValueError("reference frames must contain RGB channels")
    difference = np.abs(
        reference[..., :3].astype(np.float32) - candidate[..., :3].astype(np.float32)
    ) / np.float32(255)
    mean_error = float(np.mean(difference))
    max_error = float(np.max(difference))
    return FrameComparison(
        mean_rgb_error=mean_error,
        max_rgb_error=max_error,
        passed=(
            mean_error <= MEAN_RGB_ERROR_LIMIT + 1e-7 and max_error <= MAX_RGB_ERROR_LIMIT + 1e-7
        ),
    )


def compare_audio(
    reference: NDArray[np.float32],
    candidate: NDArray[np.float32],
    *,
    reference_loudness_lufs: float,
    candidate_loudness_lufs: float,
) -> AudioComparison:
    if reference.shape != candidate.shape:
        raise ValueError("reference audio must have identical sample shapes")
    peak = float(np.max(np.abs(reference - candidate))) if reference.size else 0.0
    peak_dbfs = -float("inf") if peak == 0 else float(20 * np.log10(peak))
    loudness_error = abs(reference_loudness_lufs - candidate_loudness_lufs)
    return AudioComparison(
        peak_error_dbfs=peak_dbfs,
        loudness_error_lufs=loudness_error,
        passed=(
            peak_dbfs <= AUDIO_PEAK_ERROR_DBFS_LIMIT + 1e-6
            and loudness_error <= LOUDNESS_ERROR_LUFS_LIMIT + 1e-6
        ),
    )


def verification_policy_manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "verification_policy_revision": VERIFICATION_POLICY_REVISION,
        "frame": {
            "mean_rgb_error_limit": MEAN_RGB_ERROR_LIMIT,
            "max_rgb_error_limit": MAX_RGB_ERROR_LIMIT,
        },
        "audio": {
            "peak_error_dbfs_limit": AUDIO_PEAK_ERROR_DBFS_LIMIT,
            "loudness_error_lufs_limit": LOUDNESS_ERROR_LUFS_LIMIT,
        },
    }


def validate_loop_boundary(
    frames: tuple[NDArray[np.uint8], ...],
    audio_samples: NDArray[np.float32],
    *,
    keyframe_interval_frames: int,
) -> LoopBoundaryValidation:
    diagnostics: list[str] = []
    if len(frames) < 2:
        diagnostics.append("loop requires at least two video frames")
    else:
        comparison = compare_rgb_frames(frames[0], frames[-1])
        if not comparison.passed:
            diagnostics.append(
                "first/last frame mismatch exceeds "
                f"mean {MEAN_RGB_ERROR_LIMIT:.6f} or max "
                f"{MAX_RGB_ERROR_LIMIT:.6f}"
            )
    if keyframe_interval_frames <= 0 or len(frames) % keyframe_interval_frames != 0:
        diagnostics.append("frame count must end on the configured GOP/keyframe interval")
    if audio_samples.ndim != 2 or audio_samples.shape[1] != 2:
        diagnostics.append("loop audio must be stereo")
    elif audio_samples.shape[0] < 960:
        diagnostics.append("loop audio requires at least 20 ms of samples")
    else:
        window = min(480, audio_samples.shape[0] // 2)
        peak = float(np.max(np.abs(audio_samples[:window] - audio_samples[-window:])))
        peak_dbfs = -float("inf") if peak == 0 else float(20 * np.log10(peak))
        if peak_dbfs > AUDIO_PEAK_ERROR_DBFS_LIMIT + 1e-6:
            diagnostics.append(
                f"audio boundary peak error {peak_dbfs:.1f} dBFS exceeds "
                f"{AUDIO_PEAK_ERROR_DBFS_LIMIT:.1f} dBFS"
            )
    return LoopBoundaryValidation(not diagnostics, tuple(diagnostics))
