from cueverge.lighting.renderer import SyntheticSweep


def test_sweep_is_deterministic_seekable_and_loops() -> None:
    renderer = SyntheticSweep(pixel_count=4, cycle_ms=4000, color=(1, 2, 3))

    assert renderer.render(0) == ((1, 2, 3), (0, 0, 0), (0, 0, 0), (0, 0, 0))
    assert renderer.render(1000) == ((0, 0, 0), (1, 2, 3), (0, 0, 0), (0, 0, 0))
    assert renderer.render(3000) == ((0, 0, 0), (0, 0, 0), (0, 0, 0), (1, 2, 3))
    assert renderer.render(4000) == renderer.render(0)
    assert renderer.render(1000) == renderer.render(1000)
