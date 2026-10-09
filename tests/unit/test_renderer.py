from cueverge.lighting.renderer import SegmentPulse, SyntheticSweep


def test_sweep_is_deterministic_seekable_and_loops() -> None:
    renderer = SyntheticSweep(pixel_count=4, cycle_ms=4000, color=(1, 2, 3))

    assert renderer.render(0) == ((1, 2, 3), (0, 0, 0), (0, 0, 0), (0, 0, 0))
    assert renderer.render(1000) == ((0, 0, 0), (1, 2, 3), (0, 0, 0), (0, 0, 0))
    assert renderer.render(3000) == ((0, 0, 0), (0, 0, 0), (0, 0, 0), (1, 2, 3))
    assert renderer.render(4000) == renderer.render(0)
    assert renderer.render(1000) == renderer.render(1000)


def test_segment_pulse_is_media_time_driven_and_range_bounded() -> None:
    renderer = SegmentPulse(
        pixel_count=6,
        segment_start=2,
        segment_end=5,
        pulse_times_ms=(1000, 2000),
        pulse_duration_ms=100,
        color=(255, 255, 255),
    )

    assert renderer.render(999) == ((0, 0, 0),) * 6
    assert renderer.render(1000) == (
        (0, 0, 0),
        (0, 0, 0),
        (255, 255, 255),
        (255, 255, 255),
        (255, 255, 255),
        (0, 0, 0),
    )
    assert renderer.render(1099) == renderer.render(1000)
    assert renderer.render(1100) == ((0, 0, 0),) * 6
    assert renderer.render(2000) == renderer.render(1000)
