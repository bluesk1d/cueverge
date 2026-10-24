import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

import av.error
import numpy as np
from numpy.typing import NDArray

from cueverge.gui import media_tools
from cueverge.gui.render_pipeline import RENDER_PIPELINE_REVISION
from cueverge.gui.render_verification import (
    VERIFICATION_POLICY_REVISION,
    validate_loop_boundary,
)
from cueverge.lighting.sequence import MAX_SEQUENCE_BYTES, LightingSequenceReference


class RenderEncodingError(RuntimeError):
    pass


@dataclass(frozen=True)
class EncoderProfile:
    video_profile: str
    video_level: str
    pixel_format: str
    max_video_bitrate_kbps: int
    keyframe_interval_frames: int
    b_frames: int
    loudness_target_lufs: float


@dataclass(frozen=True)
class EncodedMember:
    resource: str
    filename: str
    digest_sha256: str
    size_bytes: int
    has_audio: bool
    audio_boundary: dict[str, object] | None


@dataclass(frozen=True)
class LightingMember:
    resource: str
    output_indices: tuple[int, ...]
    data: bytes
    mapping_digest: str


def encode_output_set(
    *,
    output_directory: Path,
    members: dict[str, tuple[NDArray[np.uint8], ...]],
    audio_owner: str,
    audio_samples: NDArray[np.float32],
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    duration_ms: int,
    profile: EncoderProfile,
    source_revision: int,
    composition_id: str,
    composition_digest: str,
    source_checksums: dict[str, str],
    calibration_revisions: dict[str, int],
    media_end_behavior: str,
    lighting_members: tuple[LightingMember, ...] = (),
) -> dict[str, object]:
    if not members or audio_owner not in members:
        raise RenderEncodingError("output set requires an audio-owner member")
    if media_end_behavior not in {"stop", "hold_last_frame", "loop"}:
        raise RenderEncodingError("media end behavior is invalid")
    if sum(len(member.data) for member in lighting_members) > MAX_SEQUENCE_BYTES:
        raise RenderEncodingError("stage lighting exceeds the 256 MiB sequence budget")
    if media_end_behavior == "loop":
        reference_frames = members[audio_owner]
        loop_validation = validate_loop_boundary(
            reference_frames,
            audio_samples,
            keyframe_interval_frames=profile.keyframe_interval_frames,
        )
        if not loop_validation.passed:
            raise RenderEncodingError(
                "gapless loop validation failed: " + "; ".join(loop_validation.diagnostics)
            )
    output_directory.mkdir(parents=True, exist_ok=True)
    activation_identity = str(uuid4())
    temporary_directory = Path(tempfile.mkdtemp(prefix=".render-set-", dir=output_directory))
    encoded: list[EncodedMember] = []
    try:
        for index, (resource, frames) in enumerate(sorted(members.items())):
            filename = f"member-{index + 1:04d}.mp4"
            target = temporary_directory / filename
            encode_mp4(
                frames=frames,
                audio_samples=audio_samples if resource == audio_owner else None,
                frame_rate_numerator=frame_rate_numerator,
                frame_rate_denominator=frame_rate_denominator,
                profile=profile,
                target=target,
            )
            encoded.append(
                EncodedMember(
                    resource=resource,
                    filename=filename,
                    digest_sha256=_sha256(target),
                    size_bytes=target.stat().st_size,
                    has_audio=resource == audio_owner,
                    audio_boundary=(
                        _probe_audio_boundary(target) if resource == audio_owner else None
                    ),
                )
            )
            if (
                media_end_behavior == "loop"
                and encoded[-1].audio_boundary is not None
                and _number_value(encoded[-1].audio_boundary["first_packet_pts_time"]) > 1 / 48000
            ):
                raise RenderEncodingError(
                    "gapless loop validation failed: encoded audio starts after time zero"
                )
        manifest: dict[str, object] = {
            "schema_version": 1,
            "activation_identity": activation_identity,
            "source_revision": source_revision,
            "composition_id": composition_id,
            "composition_digest": composition_digest,
            "source_checksums": source_checksums,
            "duration_ms": duration_ms,
            "frame_rate": {
                "numerator": frame_rate_numerator,
                "denominator": frame_rate_denominator,
            },
            "frame_time_origin_ms": 0,
            "render_pipeline_revision": RENDER_PIPELINE_REVISION,
            "verification_policy_revision": VERIFICATION_POLICY_REVISION,
            "audio_owner": audio_owner,
            "media_end_behavior": media_end_behavior,
            "hold_last_frame": media_end_behavior == "hold_last_frame",
            "calibration_revisions": calibration_revisions,
            "encoder": {
                "identity": media_tools.engine_identity(),
                "name": "ffmpeg/libx264+aac",
                "video_profile": profile.video_profile,
                "video_level": profile.video_level,
                "pixel_format": profile.pixel_format,
                "max_video_bitrate_kbps": profile.max_video_bitrate_kbps,
                "keyframe_interval_frames": profile.keyframe_interval_frames,
                "b_frames": profile.b_frames,
                "audio_codec": "aac",
                "audio_sample_rate": 48000,
                "audio_channel_layout": "stereo",
                "loudness_target_lufs": profile.loudness_target_lufs,
            },
            "members": [
                {
                    "resource": member.resource,
                    "filename": member.filename,
                    "digest_sha256": member.digest_sha256,
                    "size_bytes": member.size_bytes,
                    "has_audio": member.has_audio,
                    "streams": {
                        "video": {
                            "codec": "h264",
                            "width": members[member.resource][0].shape[1],
                            "height": members[member.resource][0].shape[0],
                            "frame_count": len(members[member.resource]),
                            "pixel_format": profile.pixel_format,
                        },
                        "audio": (
                            {
                                "codec": "aac",
                                "sample_rate": 48000,
                                "channel_layout": "stereo",
                                "sample_count": int(audio_samples.shape[0]),
                                "boundary": member.audio_boundary,
                            }
                            if member.has_audio
                            else None
                        ),
                    },
                }
                for member in encoded
            ],
        }
        sequences: list[dict[str, object]] = []
        for index, lighting in enumerate(lighting_members):
            filename = f"lighting-{index + 1:04d}.rgb"
            reference = LightingSequenceReference(
                scene_id="bake",
                resource=lighting.resource,
                output_indices=lighting.output_indices,
                path=filename,
                digest_sha256=hashlib.sha256(lighting.data).hexdigest(),
                frame_rate=frame_rate_numerator,
                frame_rate_denominator=frame_rate_denominator,
                frame_count=len(members[audio_owner]),
                duration_ms=duration_ms,
            )
            expected_size = reference.frame_count * len(reference.output_indices) * 3
            if len(lighting.data) != expected_size:
                raise RenderEncodingError("stage lighting data does not match frame count")
            (temporary_directory / filename).write_bytes(lighting.data)
            sequences.append(
                {
                    **reference.model_dump(
                        mode="json", exclude={"scene_id", "offset_ms", "time_scale"}
                    ),
                    "mapping_digest": lighting.mapping_digest,
                }
            )
        if sequences:
            manifest["lighting_sequences"] = sequences
        manifest_path = temporary_directory / "render-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest["manifest_digest_sha256"] = _sha256(manifest_path)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        destination = output_directory / activation_identity
        os.replace(temporary_directory, destination)
        return manifest
    except Exception:
        if temporary_directory.exists():
            for path in temporary_directory.iterdir():
                path.unlink()
            temporary_directory.rmdir()
        raise


def encode_mp4(
    *,
    frames: tuple[NDArray[np.uint8], ...],
    audio_samples: NDArray[np.float32] | None,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    profile: EncoderProfile,
    target: Path,
) -> None:
    if not frames or frame_rate_numerator <= 0 or frame_rate_denominator <= 0:
        raise RenderEncodingError("encoder requires frames and a positive frame rate")
    height, width, channels = frames[0].shape
    if channels not in {3, 4} or any(
        frame.shape != frames[0].shape or frame.dtype != np.uint8 for frame in frames
    ):
        raise RenderEncodingError("encoder frames must share an RGB/RGBA uint8 shape")
    if audio_samples is not None and (
        audio_samples.ndim != 2 or audio_samples.shape[1] != 2 or audio_samples.dtype != np.float32
    ):
        raise RenderEncodingError("encoder audio must be float32 stereo")
    if width % 2 or height % 2:
        raise RenderEncodingError("encoder frames must have even dimensions")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_target = target.with_suffix(".tmp.mp4")
    try:
        media_tools.encode_mp4(
            frames=frames,
            audio_samples=audio_samples,
            frame_rate=Fraction(frame_rate_numerator, frame_rate_denominator),
            video_profile=profile.video_profile,
            video_level=profile.video_level,
            pixel_format=profile.pixel_format,
            video_bitrate_kbps=profile.max_video_bitrate_kbps,
            keyframe_interval_frames=profile.keyframe_interval_frames,
            b_frames=profile.b_frames,
            target=temporary_target,
        )
    except (av.error.FFmpegError, media_tools.MediaToolError, OSError) as error:
        temporary_target.unlink(missing_ok=True)
        raise RenderEncodingError(
            f"media encoding failed: {media_tools.describe_error(error)}"
        ) from error
    if not temporary_target.is_file() or temporary_target.stat().st_size == 0:
        temporary_target.unlink(missing_ok=True)
        raise RenderEncodingError("media encoder did not produce a target artifact")
    os.replace(temporary_target, target)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_audio_boundary(path: Path) -> dict[str, object]:
    try:
        return media_tools.audio_boundary(path)
    except (av.error.FFmpegError, media_tools.MediaToolError, OSError) as error:
        raise RenderEncodingError(
            f"audio boundary inspection failed: {media_tools.describe_error(error)}"
        ) from error


def _number_value(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise RenderEncodingError("encoded numeric metadata is invalid")
    return float(value)
