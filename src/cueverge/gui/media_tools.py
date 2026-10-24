"""PyAV-backed media probing, analysis, derivative generation, and encoding.

The controller relies on the FFmpeg libraries bundled in the PyAV wheel instead of
separate ``ffmpeg``/``ffprobe`` executables. Untrusted uploads are processed through
``python -m cueverge.gui.media_tools`` so decoder faults and runaway inputs stay in a
child process that the caller can time out.
"""

import argparse
import io
import json
import math
import re
import sys
import tempfile
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal, cast

import av
import av.error
import av.filter
import numpy as np
from av.audio.stream import AudioStream
from numpy.typing import NDArray
from PIL import Image

REQUIRED_ENCODERS = ("aac", "libmp3lame", "libx264")
REQUIRED_FILTERS = (
    "aformat",
    "ametadata",
    "ebur128",
    "format",
    "scale",
    "showwavespic",
    "thumbnail",
)
REQUIRED_CONTAINER_FORMATS = ("mp3", "mp4")
THUMBNAIL_BOX = 640
IMAGE_REVIEW_BOX = 1280
REVIEW_VIDEO_BOX = (854, 480)
WAVEFORM_SIZE = "1200x240"
WAVEFORM_COLOR = "#6ee7ff"
JPEG_QUALITY = 90
AUDIO_SAMPLE_RATE = 48000
_LOUDNESS_PATTERN = re.compile(r"lavfi\.r128\.I=(-?\d+(?:\.\d+)?)")


class MediaToolError(ValueError):
    pass


def engine_identity() -> str:
    return f"ffmpeg version {av.ffmpeg_version_info} (PyAV {av.__version__})"


def capability_diagnostics(
    *,
    encoders: tuple[str, ...] = REQUIRED_ENCODERS,
    filters: tuple[str, ...] = REQUIRED_FILTERS,
    container_formats: tuple[str, ...] = REQUIRED_CONTAINER_FORMATS,
    decoders: tuple[str, ...] = (),
) -> tuple[str, ...]:
    diagnostics: list[str] = []
    for encoder in encoders:
        if not _codec_available(encoder, "w"):
            diagnostics.append(f"required media encoder is unavailable: {encoder}")
    for decoder in decoders:
        if not _codec_available(decoder, "r"):
            diagnostics.append(f"required media decoder is unavailable: {decoder}")
    for filter_name in filters:
        if filter_name not in av.filter.filters_available:
            diagnostics.append(f"required media filter is unavailable: {filter_name}")
    for container_format in container_formats:
        if container_format not in av.formats_available:
            diagnostics.append(
                f"required media container format is unavailable: {container_format}"
            )
    return tuple(diagnostics)


def probe(path: Path) -> dict[str, object]:
    """Return stream and container facts using ffprobe-compatible field names."""
    with _open_input(path) as container:
        streams: list[dict[str, object]] = []
        for stream in container.streams:
            context = stream.codec_context
            entry: dict[str, object] = {"codec_type": stream.type}
            codec_name = _codec_name(context)
            if codec_name is not None:
                entry["codec_name"] = codec_name
            if stream.duration is not None and stream.time_base is not None:
                entry["duration"] = str(float(stream.duration * stream.time_base))
            if stream.type == "video":
                entry["width"] = int(context.width)
                entry["height"] = int(context.height)
                rate = stream.average_rate
                entry["avg_frame_rate"] = f"{rate.numerator}/{rate.denominator}" if rate else "0/0"
            elif stream.type == "audio":
                entry["channels"] = int(context.layout.nb_channels)
                entry["sample_rate"] = str(int(context.sample_rate))
            streams.append(entry)
        format_value: dict[str, object] = {"format_name": container.format.name}
        if container.duration is not None:
            format_value["duration"] = str(container.duration / av.time_base)
        return {"streams": streams, "format": format_value}


def measure_loudness(path: Path) -> float | None:
    """Return EBU R128 integrated loudness of the first audio stream in LUFS."""
    with tempfile.TemporaryDirectory(prefix="cueverge-loudness-") as root:
        report = Path(root) / "r128.txt"
        with _open_input(path) as container:
            stream = _first_stream(container, "audio")
            graph = av.filter.Graph()
            graph.link_nodes(
                graph.add_abuffer(template=stream),
                graph.add("ebur128", metadata="1"),
                graph.add("ametadata", mode="print", key="lavfi.r128.I", file=str(report)),
                graph.add("abuffersink"),
            ).configure()
            for frame in container.decode(stream):
                graph.push(frame)
                _drain(graph)
            graph.push(None)
            _drain(graph)
            del graph
        text = report.read_text(encoding="utf-8") if report.is_file() else ""
    matches = _LOUDNESS_PATTERN.findall(text)
    if not matches:
        return None
    value = float(matches[-1])
    return round(value, 1) if math.isfinite(value) else None


def write_thumbnail(source: Path, target: Path, *, representative: bool) -> None:
    filters = ["thumbnail"] if representative else []
    frame = _first_filtered_video_frame(
        source,
        [*filters, f"scale={THUMBNAIL_BOX}:{THUMBNAIL_BOX}:force_original_aspect_ratio=decrease"],
    )
    _save_image(frame, target, "JPEG")


def write_image_review(source: Path, target: Path) -> None:
    frame = _first_filtered_video_frame(
        source,
        [f"scale={IMAGE_REVIEW_BOX}:{IMAGE_REVIEW_BOX}:force_original_aspect_ratio=decrease"],
    )
    _save_image(frame, target, "JPEG")


def write_waveform(source: Path, target: Path) -> None:
    with _open_input(source) as container:
        stream = _first_stream(container, "audio")
        graph = av.filter.Graph()
        graph.link_nodes(
            graph.add_abuffer(template=stream),
            graph.add("aformat", channel_layouts="mono"),
            graph.add("showwavespic", s=WAVEFORM_SIZE, colors=WAVEFORM_COLOR),
            graph.add("format", pix_fmts="rgba"),
            graph.add("buffersink"),
        ).configure()
        for frame in container.decode(stream):
            graph.push(frame)
        graph.push(None)
        image = _pull(graph)
    if image is None:
        raise MediaToolError("audio waveform could not be rendered")
    _save_image(image, target, "PNG")


def write_audio_review(source: Path, target: Path) -> None:
    with _open_input(source) as container:
        stream = _first_stream(container, "audio")
        encoder = av.Codec("libmp3lame", "w")
        rate = int(stream.codec_context.sample_rate)
        supported_rates = encoder.audio_rates or ()
        if supported_rates and rate not in supported_rates:
            rate = 44100
        with av.open(str(target), "w", format="mp3") as output:
            audio = cast(
                AudioStream,
                output.add_stream("libmp3lame", rate=rate, layout=_review_layout(stream)),
            )
            audio.bit_rate = 128_000
            for frame in container.decode(stream):
                frame.pts = None
                output.mux(audio.encode(frame))
            output.mux(audio.encode(None))


def write_video_review(source: Path, target: Path) -> None:
    with _open_input(source) as container:
        video_in = _first_stream(container, "video")
        audio_in = next(iter(container.streams.audio), None)
        width, height = _review_dimensions(
            int(video_in.codec_context.width),
            int(video_in.codec_context.height),
        )
        graph = av.filter.Graph()
        graph.link_nodes(
            graph.add_buffer(template=video_in),
            graph.add("scale", f"{width}:{height}"),
            graph.add("format", pix_fmts="yuv420p"),
            graph.add("buffersink"),
        ).configure()
        with av.open(
            str(target),
            "w",
            format="mp4",
            options={"movflags": "+faststart"},
        ) as output:
            rate = video_in.average_rate or video_in.guessed_rate or Fraction(30)
            video_out = output.add_stream(
                "libx264",
                rate=rate,
                options={"preset": "veryfast", "crf": "28"},
            )
            video_out.width = width
            video_out.height = height
            video_out.pix_fmt = "yuv420p"
            video_out.codec_context.time_base = video_in.time_base
            audio_out = None
            if audio_in is not None:
                audio_out = output.add_stream(
                    "aac",
                    rate=int(audio_in.codec_context.sample_rate),
                    layout=_review_layout(audio_in),
                )
                audio_out.bit_rate = 96_000
            last_video_pts: int | None = None
            streams = [video_in] if audio_in is None else [video_in, audio_in]
            for packet in container.demux(streams):
                for decoded in packet.decode():
                    if packet.stream is audio_in and audio_out is not None:
                        output.mux(audio_out.encode(decoded))
                        continue
                    graph.push(decoded)
                    for filtered in _drain(graph):
                        last_video_pts = _encode_review_frame(
                            output, video_out, filtered, last_video_pts
                        )
            graph.push(None)
            for filtered in _drain(graph):
                last_video_pts = _encode_review_frame(output, video_out, filtered, last_video_pts)
            if last_video_pts is None:
                raise MediaToolError("video contains no decodable frames")
            output.mux(video_out.encode(None))
            if audio_out is not None:
                output.mux(audio_out.encode(None))


def extract_frame_png(path: Path, *, timestamp_ms: int) -> bytes:
    """Return the frame displayed at ``timestamp_ms`` using floor/hold timing."""
    target_seconds = timestamp_ms / 1000
    with _open_input(path) as container:
        stream = _first_stream(container, "video")
        if stream.time_base is not None and timestamp_ms > 0:
            container.seek(
                int(Fraction(timestamp_ms, 1000) / stream.time_base),
                stream=stream,
                backward=True,
            )
        selected = None
        for frame in container.decode(stream):
            if (
                selected is not None
                and frame.time is not None
                and frame.time > target_seconds + 1e-6
            ):
                break
            selected = frame
    if selected is None:
        raise MediaToolError("no frame is available at the requested timestamp")
    output = io.BytesIO()
    selected.to_image().save(output, format="PNG")
    return output.getvalue()


def encode_mp4(
    *,
    frames: tuple[NDArray[np.uint8], ...],
    audio_samples: NDArray[np.float32] | None,
    frame_rate: Fraction,
    video_profile: str,
    video_level: str,
    pixel_format: str,
    video_bitrate_kbps: int,
    keyframe_interval_frames: int,
    b_frames: int,
    target: Path,
) -> None:
    height, width = frames[0].shape[:2]
    with av.open(str(target), "w", format="mp4", options={"movflags": "+faststart"}) as output:
        video = output.add_stream(
            "libx264",
            rate=frame_rate,
            options={"profile": video_profile, "level": video_level},
        )
        video.width = width
        video.height = height
        video.pix_fmt = pixel_format
        video.bit_rate = video_bitrate_kbps * 1000
        video.codec_context.gop_size = keyframe_interval_frames
        video.codec_context.max_b_frames = b_frames
        audio = None
        samples: NDArray[np.float32] | None = None
        if audio_samples is not None:
            audio = output.add_stream("aac", rate=AUDIO_SAMPLE_RATE, layout="stereo")
            # Match the video duration, as ffmpeg's -shortest did for the CLI encoder.
            sample_limit = round(len(frames) * AUDIO_SAMPLE_RATE / frame_rate)
            samples = np.ascontiguousarray(audio_samples[:sample_limit])
        written_samples = 0
        for index, frame in enumerate(frames):
            image = av.VideoFrame.from_ndarray(
                np.ascontiguousarray(frame[..., :3]),
                format="rgb24",
            )
            image.pts = index
            output.mux(video.encode(image))
            if audio is not None and samples is not None:
                boundary = min(
                    len(samples),
                    round((index + 1) * AUDIO_SAMPLE_RATE / frame_rate),
                )
                for chunk in _audio_frames(samples, written_samples, boundary):
                    output.mux(audio.encode(chunk))
                written_samples = boundary
        output.mux(video.encode(None))
        if audio is not None and samples is not None:
            for chunk in _audio_frames(samples, written_samples, len(samples)):
                output.mux(audio.encode(chunk))
            output.mux(audio.encode(None))


def audio_boundary(path: Path) -> dict[str, object]:
    with _open_input(path) as container:
        stream = _first_stream(container, "audio")
        first_pts_time: float | None = None
        skip_samples = 0
        discard_padding = 0
        for packet in container.demux(stream):
            if packet.size == 0:
                continue
            if first_pts_time is None:
                if packet.pts is None or packet.time_base is None:
                    raise MediaToolError("encoded audio packet timing is unavailable")
                first_pts_time = float(packet.pts * packet.time_base)
            for side_data in packet.iter_sidedata():
                if side_data.data_type != "skip_samples":
                    continue
                payload = bytes(side_data)
                if len(payload) >= 8:
                    skip_samples = max(skip_samples, int.from_bytes(payload[0:4], "little"))
                    discard_padding = max(
                        discard_padding,
                        int.from_bytes(payload[4:8], "little"),
                    )
    if first_pts_time is None:
        raise MediaToolError("encoded audio stream has no packets")
    return {
        "first_packet_pts_time": first_pts_time,
        "encoder_delay_samples": skip_samples,
        "discard_padding_samples": discard_padding,
        "metadata_inspected": True,
    }


def stream_types(path: Path) -> tuple[str, ...]:
    with _open_input(path) as container:
        return tuple(str(stream.type) for stream in container.streams)


def describe_error(error: BaseException) -> str:
    if isinstance(error, av.error.FFmpegError):
        message = error.strerror or str(error)
        return str(message)
    return str(error) or error.__class__.__name__


def _open_input(path: Path) -> Any:
    try:
        return av.open(str(path), "r")
    except av.error.FFmpegError as error:
        raise MediaToolError(describe_error(error)) from error


def _first_stream(container: Any, kind: str) -> Any:
    streams = getattr(container.streams, kind)
    if not streams:
        raise MediaToolError(f"media contains no {kind} stream")
    return streams[0]


def _codec_name(context: Any) -> str | None:
    codec = getattr(context, "codec", None)
    if codec is None:
        return None
    name = getattr(codec, "canonical_name", None) or getattr(codec, "name", None)
    return str(name) if name else None


def _codec_available(name: str, mode: Literal["r", "w"]) -> bool:
    try:
        av.Codec(name, mode)
    except (av.error.FFmpegError, ValueError):
        return False
    return True


def _first_filtered_video_frame(source: Path, filters: list[str]) -> Any:
    with _open_input(source) as container:
        stream = _first_stream(container, "video")
        graph = av.filter.Graph()
        nodes = [graph.add_buffer(template=stream)]
        for description in filters:
            name, _, arguments = description.partition("=")
            nodes.append(graph.add(name, arguments or None))
        nodes.extend([graph.add("format", pix_fmts="rgb24"), graph.add("buffersink")])
        graph.link_nodes(*nodes).configure()
        for frame in container.decode(stream):
            graph.push(frame)
            filtered = _pull(graph)
            if filtered is not None:
                return filtered
        graph.push(None)
        filtered = _pull(graph)
    if filtered is None:
        raise MediaToolError("media contains no decodable video frame")
    return filtered


def _pull(graph: Any) -> Any:
    try:
        return graph.pull()
    except (av.error.BlockingIOError, av.error.EOFError):
        return None


def _drain(graph: Any) -> Iterator[Any]:
    collected = []
    while (frame := _pull(graph)) is not None:
        collected.append(frame)
    return iter(collected)


def _save_image(frame: Any, target: Path, image_format: str) -> None:
    image: Image.Image = frame.to_image()
    if image_format == "JPEG" and image.mode != "RGB":
        image = image.convert("RGB")
    options: dict[str, object] = {"quality": JPEG_QUALITY} if image_format == "JPEG" else {}
    image.save(target, format=image_format, **options)


def _review_layout(stream: Any) -> str:
    return "mono" if int(stream.codec_context.layout.nb_channels) == 1 else "stereo"


def _review_dimensions(width: int, height: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise MediaToolError("video dimensions are unavailable")
    max_width, max_height = REVIEW_VIDEO_BOX
    scale = min(max_width / width, max_height / height)
    return max(2, math.trunc(scale * width / 2) * 2), max(2, math.trunc(scale * height / 2) * 2)


def _encode_review_frame(
    output: Any,
    stream: Any,
    frame: Any,
    last_pts: int | None,
) -> int | None:
    if frame.pts is None or (last_pts is not None and frame.pts <= last_pts):
        return last_pts
    output.mux(stream.encode(frame))
    return int(frame.pts)


def _audio_frames(
    samples: NDArray[np.float32],
    start: int,
    stop: int,
) -> Iterator[Any]:
    for offset in range(start, stop, 1024):
        chunk = samples[offset : min(offset + 1024, stop)]
        frame = av.AudioFrame.from_ndarray(
            np.ascontiguousarray(chunk.T),
            format="fltp",
            layout="stereo",
        )
        frame.sample_rate = AUDIO_SAMPLE_RATE
        frame.time_base = Fraction(1, AUDIO_SAMPLE_RATE)
        frame.pts = offset
        yield frame


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cueverge.gui.media_tools")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("probe").add_argument("source", type=Path)
    commands.add_parser("loudness").add_argument("source", type=Path)
    thumbnail = commands.add_parser("thumbnail")
    thumbnail.add_argument("--representative", action="store_true")
    for name in ("thumbnail", "image-review", "audio-review", "video-review", "waveform"):
        command = thumbnail if name == "thumbnail" else commands.add_parser(name)
        command.add_argument("source", type=Path)
        command.add_argument("target", type=Path)
    arguments = parser.parse_args(argv)
    try:
        result: object = None
        if arguments.command == "probe":
            result = probe(arguments.source)
        elif arguments.command == "loudness":
            result = {"integrated_lufs": measure_loudness(arguments.source)}
        elif arguments.command == "thumbnail":
            write_thumbnail(
                arguments.source,
                arguments.target,
                representative=arguments.representative,
            )
        elif arguments.command == "image-review":
            write_image_review(arguments.source, arguments.target)
        elif arguments.command == "audio-review":
            write_audio_review(arguments.source, arguments.target)
        elif arguments.command == "video-review":
            write_video_review(arguments.source, arguments.target)
        elif arguments.command == "waveform":
            write_waveform(arguments.source, arguments.target)
    except (av.error.FFmpegError, MediaToolError, OSError) as error:
        print(describe_error(error), file=sys.stderr)
        return 1
    if result is not None:
        print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
