import base64
import copy
import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest
from PIL import Image

from cueverge.gui.asset_upload import AssetUploadService
from cueverge.gui.contracts import DraftProject
from cueverge.gui.projector_renderer import preview_projector_composition
from cueverge.gui.scene_media_preview import (
    SceneMediaPreviewError,
    _media_position,
    _Output,
    evaluate_scene_media,
)
from tests.unit.media_fixtures import write_video
from tests.unit.test_projector_renderer import _draft, _write_wave


def test_scene_media_preview_uses_scene_composition_frames_and_audio(tmp_path: Path) -> None:
    services = _services(tmp_path)
    document = _draft()
    metadata = cast(dict[str, Any], document["editor_metadata"])
    metadata["scene_compositions"] = {"ambient": ["projector-composition"]}
    draft = DraftProject.model_validate(document)

    preview = evaluate_scene_media(
        cast(Any, services),
        project_id="demo-project",
        source_revision=4,
        draft=draft,
        scene_id="ambient",
        timestamp_ms=500,
    )

    assert preview["hardware_commands_sent"] is False
    output = cast(list[dict[str, Any]], preview["outputs"])[0]
    assert output["output_id"] == "left"
    assert output["state"] == "playing"
    assert output["source"]["kind"] == "composition"
    assert output["source"]["composition_id"] == "projector-composition"
    assert output["media_position_ms"] == 500
    assert [item["asset_id"] for item in output["audio"]] == ["audio-a", "audio-b"]
    assert output["audio"][0]["source_position_ms"] == 500
    assert output["audio"][0]["gain_db"] == -6
    expected = preview_projector_composition(
        project_id="demo-project",
        draft=draft,
        composition_id="projector-composition",
        timestamp_ms=500,
        assets=cast(AssetUploadService, services.asset_upload),
    )
    assert np.array_equal(_decode(output["frame"]), expected)


def test_scene_media_preview_plays_assigned_assets_after_start_delay(tmp_path: Path) -> None:
    services = _services(tmp_path)
    document = _draft()
    scene = cast(list[dict[str, Any]], document["scenes"])[0]
    scene["media"][0].update({"asset": "clip", "start_delay_ms": 200, "end_behavior": "stop"})
    draft = DraftProject.model_validate(document)

    def media_at(timestamp_ms: int) -> dict[str, Any]:
        preview = evaluate_scene_media(
            cast(Any, services),
            project_id="demo-project",
            source_revision=4,
            draft=draft,
            scene_id="ambient",
            timestamp_ms=timestamp_ms,
        )
        return cast(list[dict[str, Any]], preview["outputs"])[0]

    waiting = media_at(100)
    assert waiting["state"] == "waiting"
    assert waiting["frame"] is None
    assert waiting["source"] == {
        "kind": "asset",
        "asset_id": "clip",
        "name": "Clip",
        "media_type": "video/mp4",
    }
    playing = media_at(450)
    assert playing["state"] == "playing"
    assert playing["media_position_ms"] == 250
    assert _decode(playing["frame"])[0, 0, :3].tolist() == pytest.approx([0, 128, 0], abs=6)
    assert playing["audio"] == []


def test_scene_media_preview_reports_unassigned_conflicts_and_unknown_outputs(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path)
    document = _draft()
    metadata = cast(dict[str, Any], document["editor_metadata"])
    compositions = cast(dict[str, Any], metadata["compositions"])
    compositions["second"] = {
        **copy.deepcopy(compositions["projector-composition"]),
        "id": "second",
        "name": "Second",
    }
    metadata["timeline_transform_layers"]["second"] = []
    draft = DraftProject.model_validate(document)
    unassigned = evaluate_scene_media(
        cast(Any, services),
        project_id="demo-project",
        source_revision=4,
        draft=draft,
        scene_id="ambient",
        timestamp_ms=0,
        include_frames=False,
    )
    assert cast(list[dict[str, Any]], unassigned["outputs"])[0]["state"] == "unassigned"

    metadata["scene_compositions"] = {"ambient": ["projector-composition", "second"]}
    conflicted = evaluate_scene_media(
        cast(Any, services),
        project_id="demo-project",
        source_revision=4,
        draft=DraftProject.model_validate(document),
        scene_id="ambient",
        timestamp_ms=0,
    )
    output = cast(list[dict[str, Any]], conflicted["outputs"])[0]
    assert output["state"] == "error"
    assert "Several scene compositions" in output["message"]
    with pytest.raises(SceneMediaPreviewError):
        evaluate_scene_media(
            cast(Any, services),
            project_id="demo-project",
            source_revision=4,
            draft=draft,
            scene_id="ambient",
            timestamp_ms=0,
            output_id="missing",
        )
    with pytest.raises(SceneMediaPreviewError):
        evaluate_scene_media(
            cast(Any, services),
            project_id="demo-project",
            source_revision=4,
            draft=draft,
            scene_id="ambient",
            timestamp_ms=1000,
        )


@pytest.mark.parametrize(
    ("timestamp_ms", "end_behavior", "expected"),
    [
        (50, "loop", ("waiting", None)),
        (100, "loop", ("playing", 0)),
        (1099, "stop", ("playing", 999)),
        (1250, "loop", ("looping", 150)),
        (1250, "hold_last_frame", ("holding", 999)),
        (1250, "stop", ("stopped", None)),
    ],
)
def test_media_position_follows_start_delay_and_end_behavior(
    timestamp_ms: int, end_behavior: str, expected: tuple[str, int | None]
) -> None:
    output = _Output("left", "projector.left", "clip", True, 100, end_behavior)
    assert _media_position(timestamp_ms, output, 1000) == expected
    assert _media_position(timestamp_ms + 5000, output, None)[0] == "playing"


class _Assets:
    def __init__(self, sources: dict[str, tuple[str, Path]]) -> None:
        self.sources = sources

    def get_original(self, asset_id: str) -> tuple[SimpleNamespace, Path]:
        return self.get_asset(asset_id), self.sources[asset_id][1]

    def get_asset(self, asset_id: str) -> SimpleNamespace:
        media_type, path = self.sources[asset_id]
        return SimpleNamespace(
            id=asset_id,
            name=asset_id.replace("-", " ").capitalize(),
            project_id="demo-project",
            digest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            media_type=media_type,
            metadata={"duration_ms": 1000, "audio_codecs": []},
            trashed_at=None,
        )


def _services(tmp_path: Path) -> SimpleNamespace:
    still = tmp_path / "still.png"
    Image.fromarray(np.full((36, 64, 4), [0, 0, 255, 128], dtype=np.uint8), mode="RGBA").save(still)
    video = tmp_path / "opaque.mp4"
    write_video(
        video,
        [np.full((36, 64, 3), [0, 128, 0], dtype=np.uint8)] * 4,
        rate=4,
        codec="libx264",
        pixel_format="yuv420p",
    )
    alpha = tmp_path / "alpha.mkv"
    write_video(
        alpha,
        [np.full((36, 64, 4), [255, 0, 0, 128], dtype=np.uint8)] * 4,
        rate=4,
        codec="ffv1",
        pixel_format="bgra",
    )
    audio = tmp_path / "audio.wav"
    _write_wave(audio, 440)
    return SimpleNamespace(
        asset_upload=_Assets(
            {
                "still": ("image/png", still),
                "alpha-video": ("video/x-matroska", alpha),
                "opaque-video": ("video/mp4", video),
                "clip": ("video/mp4", video),
                "audio-a": ("audio/wav", audio),
                "audio-b": ("audio/wav", audio),
            }
        )
    )


def _decode(frame: dict[str, Any]) -> np.ndarray:
    assert frame["media_type"] == "image/png"
    with Image.open(BytesIO(base64.b64decode(frame["data_base64"]))) as image:
        return np.asarray(image.convert("RGBA"))


def test_media_position_plays_repeat_count_times_before_end_behavior() -> None:
    output = _Output("left", "projector.left", "clip", True, 0, "hold_last_frame", 3)
    assert _media_position(2500, output, 1000) == ("playing", 500)
    assert _media_position(2999, output, 1000) == ("playing", 999)
    assert _media_position(3000, output, 1000) == ("holding", 999)


def test_scene_media_preview_reports_which_repeat_is_playing(tmp_path: Path) -> None:
    services = _services(tmp_path)
    document = _draft()
    scene = cast(list[dict[str, Any]], document["scenes"])[0]
    scene["duration_ms"] = 3000
    scene["media"][0].update({"asset": "clip", "repeat_count": 3})
    preview = evaluate_scene_media(
        cast(Any, services),
        project_id="demo-project",
        source_revision=4,
        draft=DraftProject.model_validate(document),
        scene_id="ambient",
        timestamp_ms=2250,
        include_frames=False,
    )
    output = cast(list[dict[str, Any]], preview["outputs"])[0]
    assert output["state"] == "playing"
    assert output["media_position_ms"] == 250
    assert output["repeat_count"] == 3
    assert output["play_number"] == 3
