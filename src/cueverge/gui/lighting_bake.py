import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

from cueverge.gui.asset_upload import AssetUploadService
from cueverge.gui.contracts import DraftProject
from cueverge.gui.lighting_preview import (
    evaluate_lighting_preview,
    resolve_lighting_preview_surface,
)
from cueverge.gui.projector_renderer import prepare_media_layers
from cueverge.lighting.sequence import MAX_SEQUENCE_BYTES

LIGHTING_FRAME_RATE = 30


def render_lighting_composition(
    *,
    project_id: str,
    source_revision: int,
    draft: DraftProject,
    composition_id: str,
    assets: AssetUploadService,
    mapper_export: dict[str, object],
    output_directory: Path,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> dict[str, object]:
    metadata = cast(dict[str, object], draft.editor_metadata)
    compositions = cast(dict[str, dict[str, object]], metadata["compositions"])
    composition = compositions[composition_id]
    duration = int(cast(int, composition["duration_ms"]))
    surface = resolve_lighting_preview_surface(
        draft=draft,
        composition_id=composition_id,
        mapper_export=mapper_export,
    )
    count = len(cast(list[object], surface["pixels"]))
    frame_count = (duration * LIGHTING_FRAME_RATE + 999) // 1000
    if count == 0 or frame_count * count * 3 > MAX_SEQUENCE_BYTES:
        raise ValueError("lighting bake requires samples and must fit the 256 MiB sequence budget")
    transforms = cast(dict[str, list[object]], metadata.get("timeline_transform_layers", {}))
    prepared = prepare_media_layers(
        project_id=project_id,
        raw_layers=transforms.get(composition_id, []),
        assets=assets,
    )
    if prepared.audio_layers:
        raise ValueError("lighting compositions do not render audio layers")
    output_directory.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".lighting-", dir=output_directory))
    try:
        indices: list[int] = []
        with (temporary / "frames.rgb").open("wb") as stream:
            for index in range(frame_count):
                if should_cancel is not None and should_cancel():
                    raise InterruptedError("lighting bake canceled")
                if on_progress is not None and index % LIGHTING_FRAME_RATE == 0:
                    on_progress(0.1 + 0.8 * index / frame_count)
                preview = evaluate_lighting_preview(
                    project_id=project_id,
                    draft=draft,
                    composition_id=composition_id,
                    timestamp_ms=index * 1000 // LIGHTING_FRAME_RATE,
                    assets=assets,
                    mapper_export=mapper_export,
                    prepared_media=prepared,
                )
                diagnostics = cast(dict[str, object], preview["diagnostics"])
                if diagnostics["unmapped_pixels"] or diagnostics["duplicate_output_indices"]:
                    raise ValueError("lighting bake contains unmapped or duplicate pixels")
                samples = cast(list[dict[str, object]], preview["samples"])
                indices = [cast(int, sample["output_index"]) for sample in samples]
                stream.write(
                    bytes(
                        channel
                        for sample in samples
                        for channel in cast(list[int], sample["requested_color"])
                    )
                )
        data = temporary / "frames.rgb"
        digest = hashlib.sha256(data.read_bytes()).hexdigest()
        manifest: dict[str, object] = {
            "schema_version": 1,
            "kind": "lighting_sequence",
            "source_revision": source_revision,
            "composition_id": composition_id,
            "composition": composition,
            "source_checksums": prepared.source_checksums,
            "mapping": mapper_export,
            "resource": surface["resource"],
            "output_indices": indices,
            "frame_rate": LIGHTING_FRAME_RATE,
            "frame_count": frame_count,
            "duration_ms": duration,
            "path": "frames.rgb",
            "digest_sha256": digest,
            "size_bytes": data.stat().st_size,
            "members": [],
        }
        identity = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        manifest["activation_identity"] = identity
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        destination = output_directory / identity
        if destination.exists():
            shutil.rmtree(temporary)
        else:
            os.replace(temporary, destination)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
