"""Ambient (persistent) lighting shared by the controller runtime and previews."""

from cueverge.core.models import LoadedLightingLooks
from cueverge.lighting.renderer import Rgb
from cueverge.lighting.timeline import LightingTimeline


def scene_inherits_ambient(looks: LoadedLightingLooks, scene_id: str | None) -> bool:
    return scene_id is None or scene_id not in looks.definition.excluded_scene_ids


def render_ambient(looks: LoadedLightingLooks, elapsed_ms: int) -> tuple[Rgb, ...]:
    """Ambient frame at a controller-clock time, before the look's brightness limit.

    Baseline cues loop every cycle_ms; a baked baseline sequence loops over its own duration
    on top of them. Both follow the controller clock, so scene changes never restart them.
    """
    elapsed_ms = max(0, elapsed_ms)
    definition = looks.definition
    pixels: list[Rgb] = (
        list(
            LightingTimeline(
                pixel_count=looks.pixel_count,
                cues=looks.baseline_cues,
                brightness_limit=1.0,
            ).render(elapsed_ms % definition.cycle_ms)
        )
        if looks.baseline_cues
        else [(0, 0, 0)] * looks.pixel_count
    )
    if looks.baseline_sequence is not None:
        duration_ms = looks.baseline_sequence.reference.duration_ms
        looks.baseline_sequence.overlay(pixels, elapsed_ms % duration_ms)
    return tuple(pixels)


def render_ambient_output(
    looks: LoadedLightingLooks, elapsed_ms: int, scene_id: str | None
) -> tuple[Rgb, ...]:
    """Frame for an LED output the active scene does not light itself.

    Scenes that inherit the ambient show it (with its brightness limit); scenes that opted out
    show the safe state instead of freezing the last ambient frame.
    """
    if not scene_inherits_ambient(looks, scene_id):
        return render_safe_state(looks)
    limit = looks.definition.brightness_limit
    return tuple(
        (round(red * limit), round(green * limit), round(blue * limit))
        for red, green, blue in render_ambient(looks, elapsed_ms)
    )


def render_safe_state(looks: LoadedLightingLooks) -> tuple[Rgb, ...]:
    return LightingTimeline(
        pixel_count=looks.pixel_count,
        cues=looks.safe_state_cues,
        brightness_limit=looks.definition.brightness_limit,
    ).render(0)
