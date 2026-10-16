from typing import Final

RENDER_PIPELINE_REVISION: Final = 1
RENDER_PIPELINE_STAGES: Final = (
    {
        "id": "decode_conform",
        "description": (
            "Decode and conform sources to composition frame rate, resolution, "
            "alpha interpretation, and working color space."
        ),
    },
    {
        "id": "logical_composite",
        "description": (
            "Evaluate transforms, opacity, masks, and alpha composition in "
            "logical composition coordinates."
        ),
    },
    {
        "id": "physical_object_mapping",
        "description": ("Map layers onto calibrated physical objects and apply occlusion masks."),
    },
    {
        "id": "projector_native_warp",
        "description": ("Apply projector-native geometry warp, crop, and orientation."),
    },
    {
        "id": "overlap_blend",
        "description": ("Apply multi-projector overlap and edge-blend masks."),
    },
    {
        "id": "projector_color",
        "description": ("Apply projector brightness, black-level, and color correction."),
    },
    {
        "id": "audio_encode_mux",
        "description": ("Mix and conform audio, then encode and mux the target artifact."),
    },
)


def render_pipeline_manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "render_pipeline_revision": RENDER_PIPELINE_REVISION,
        "consumers": ["preview", "bake"],
        "stages": list(RENDER_PIPELINE_STAGES),
    }
