from pathlib import Path

import av.error

from cueverge.gui import media_tools


class ArtifactPreviewError(RuntimeError):
    pass


def extract_artifact_preview_frame(
    path: Path,
    *,
    timestamp_ms: int,
    duration_ms: int,
) -> bytes:
    if timestamp_ms < 0 or timestamp_ms >= duration_ms:
        raise ArtifactPreviewError("artifact preview timestamp is outside duration")
    try:
        return media_tools.extract_frame_png(path, timestamp_ms=timestamp_ms)
    except (av.error.FFmpegError, media_tools.MediaToolError, OSError) as error:
        raise ArtifactPreviewError(
            f"the artifact preview frame could not be decoded: {media_tools.describe_error(error)}"
        ) from error
