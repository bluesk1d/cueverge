import subprocess
from pathlib import Path


class ArtifactPreviewError(RuntimeError):
    pass


def extract_artifact_preview_frame(
    path: Path,
    *,
    timestamp_ms: int,
    duration_ms: int,
    ffmpeg: str = "ffmpeg",
) -> bytes:
    if timestamp_ms < 0 or timestamp_ms >= duration_ms:
        raise ArtifactPreviewError("artifact preview timestamp is outside duration")
    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp_ms / 1000:.6f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "pipe:1",
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if completed.returncode != 0 or not completed.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ArtifactPreviewError(
            f"ffmpeg could not decode the artifact preview frame: {message or 'no frame'}"
        )
    return completed.stdout
