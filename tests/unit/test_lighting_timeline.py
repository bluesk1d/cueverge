from cueverge.core.models import LightingCue, LoadedLightingCue
from cueverge.lighting.timeline import LightingTimeline


def test_raygun_timeline_combines_baseline_charge_path_flash_and_fade() -> None:
    timeline = LightingTimeline(
        pixel_count=8,
        brightness_limit=0.8,
        cues=(
            _cue(
                "baseline",
                "solid",
                range(8),
                duration_ms=5000,
                color="#100020",
                precedence=0,
            ),
            _cue(
                "charge",
                "envelope",
                (5, 6, 7),
                start_ms=500,
                duration_ms=1500,
                color="#00FF40",
                fade_in_ms=500,
                precedence=10,
            ),
            _cue(
                "travel",
                "path_sweep",
                (0, 1, 2, 3, 4, 5),
                start_ms=2000,
                duration_ms=1000,
                color="#00FF40",
                width_pixels=2,
                blend_mode="max",
                precedence=20,
            ),
            _cue(
                "flash",
                "pulse",
                range(8),
                start_ms=3000,
                duration_ms=500,
                color="#FFFFFF",
                period_ms=500,
                blend_mode="add",
                precedence=30,
            ),
            _cue(
                "fade",
                "envelope",
                range(8),
                start_ms=3500,
                duration_ms=1500,
                color="#100020",
                fade_out_ms=1500,
                precedence=40,
            ),
        ),
    )

    baseline = timeline.render(0)
    assert baseline == ((13, 0, 26),) * 8

    charged = timeline.render(1000)
    assert charged[5] == (0, 204, 51)
    assert charged[0] == (13, 0, 26)

    traveling = timeline.render(2500)
    assert traveling[2] == (13, 204, 51)
    assert traveling[3] == (13, 204, 51)
    assert traveling[7] == (13, 0, 26)

    flash = timeline.render(3250)
    assert all(max(pixel) > 100 for pixel in flash)
    assert timeline.render(4999)[0][0] <= baseline[0][0]


def test_seeded_flicker_and_sparkle_are_repeatable_under_seek() -> None:
    timeline = LightingTimeline(
        pixel_count=12,
        cues=(
            _cue(
                "flicker",
                "flicker",
                range(6),
                duration_ms=2000,
                seed=42,
                period_ms=100,
            ),
            _cue(
                "sparkle",
                "sparkle",
                range(6, 12),
                duration_ms=2000,
                seed=99,
                period_ms=100,
                density=0.5,
                blend_mode="max",
            ),
        ),
    )

    assert timeline.render(750) == timeline.render(750)
    assert timeline.render(750) != timeline.render(850)


def test_color_transition_chase_ripple_reverse_and_blend_modes() -> None:
    timeline = LightingTimeline(
        pixel_count=6,
        cues=(
            _cue(
                "color",
                "color_transition",
                range(6),
                duration_ms=1000,
                color="#FF0000",
                end_color="#0000FF",
            ),
            _cue(
                "reverse-chase",
                "chase",
                range(6),
                start_ms=1000,
                duration_ms=1000,
                color="#00FF00",
                reverse=True,
                width_pixels=1,
            ),
            _cue(
                "ripple",
                "ripple",
                range(6),
                start_ms=2000,
                duration_ms=1000,
                color="#FFFFFF",
                width_pixels=1,
            ),
            _cue(
                "multiply",
                "solid",
                range(6),
                start_ms=3000,
                duration_ms=1000,
                color="#808080",
                blend_mode="multiply",
                precedence=10,
            ),
        ),
    )

    midpoint = timeline.render(500)
    assert midpoint[0][0] in {127, 128}
    assert midpoint[0][2] in {127, 128}
    assert timeline.render(1000)[5] == (0, 255, 0)
    assert any(pixel == (255, 255, 255) for pixel in timeline.render(2500))
    assert timeline.render(3500) == ((0, 0, 0),) * 6


def _cue(
    cue_id: str,
    primitive: str,
    indices: range | tuple[int, ...],
    *,
    start_ms: int = 0,
    duration_ms: int,
    **values: object,
) -> LoadedLightingCue:
    definition = LightingCue.model_validate(
        {
            "id": cue_id,
            "primitive": primitive,
            "start_ms": start_ms,
            "duration_ms": duration_ms,
            **values,
        }
    )
    return LoadedLightingCue(
        definition=definition,
        target_indices=tuple(indices),
        target_precedence=definition.precedence,
    )
