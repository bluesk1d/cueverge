from collections.abc import Sequence
from pathlib import Path

import av
import numpy as np
from numpy.typing import NDArray


def write_video(
    path: Path,
    frames: Sequence[NDArray[np.uint8]],
    *,
    rate: int,
    codec: str,
    pixel_format: str,
) -> None:
    height, width, channels = frames[0].shape
    with av.open(str(path), "w") as output:
        stream = output.add_stream(codec, rate=rate)
        stream.width = width
        stream.height = height
        stream.pix_fmt = pixel_format
        for index, frame in enumerate(frames):
            video_frame = av.VideoFrame.from_ndarray(
                np.ascontiguousarray(frame),
                format="rgba" if channels == 4 else "rgb24",
            )
            video_frame.pts = index
            output.mux(stream.encode(video_frame))
        output.mux(stream.encode(None))


def stream_types(path: Path) -> set[str]:
    with av.open(str(path)) as container:
        return {str(stream.type) for stream in container.streams}
