from cueverge.gui.installation_geometry import InstallationGeometryService
from cueverge.gui.lighting_surface import LightingSurfaceService
from cueverge.lighting.output_test import OutputTestError, OutputTestSegment, traversal_color


def mapped_test_segments(
    geometry: InstallationGeometryService,
    lighting: LightingSurfaceService,
    installation_id: str,
    resource: str,
    pixel_count: int,
) -> tuple[OutputTestSegment, ...]:
    mapped: dict[str, tuple[str, int, int]] = {}
    for item in geometry.list(installation_id):
        if item.kind != "pixel_segment":
            continue
        surface = lighting.get(item.id)
        if surface.resource != resource:
            continue
        if item.start_index is None or item.pixel_count is None:
            raise OutputTestError(f"{item.name}: save a valid pixel range before testing")
        start, end = item.start_index, item.start_index + item.pixel_count
        if not 0 <= start < end <= pixel_count:
            raise OutputTestError(f"{item.name}: range exceeds the configured output")
        previous = mapped.get(item.semantic_id)
        if previous is not None and previous[1:] != (start, end):
            raise OutputTestError(f"{item.name}: linked photo views disagree on pixel range")
        mapped[item.semantic_id] = (item.name, start, end)
    ordered = sorted(mapped.items(), key=lambda item: (item[1][1], item[0]))
    segments = tuple(
        OutputTestSegment(name, start, end, traversal_color(index))
        for index, (_, (name, start, end)) in enumerate(ordered)
    )
    end = 0
    for segment in segments:
        if segment.start < end:
            raise OutputTestError(f"{segment.name}: resolve overlapping allocations before testing")
        end = segment.end
    return segments
