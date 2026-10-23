from cueverge.core.models import LoadedLightingOutput
from cueverge.lighting.renderer import Rgb, SegmentPulse, SyntheticSweep, TargetPulse
from cueverge.lighting.timeline import LightingTimeline


def evaluate_lighting_output(
    output: LoadedLightingOutput,
    *,
    position_ms: int,
    duration_ms: int,
    base_frame: tuple[Rgb, ...] | None = None,
    brightness_limit: float | None = None,
) -> tuple[Rgb, ...]:
    lighting = output.definition
    limit = lighting.brightness_limit if brightness_limit is None else brightness_limit
    if output.spatial_sequences:
        pixels = list(
            LightingTimeline(pixel_count=output.pixel_count, cues=output.lighting_cues).render(
                position_ms, base_frame=base_frame
            )
        )
        for sequence in output.spatial_sequences:
            frame = sequence.frame(position_ms)
            if frame is None:
                continue
            for offset, index in enumerate(sequence.reference.output_indices):
                pixels[index] = (
                    frame[offset * 3],
                    frame[offset * 3 + 1],
                    frame[offset * 3 + 2],
                )
        return tuple(
            (round(red * limit), round(green * limit), round(blue * limit))
            for red, green, blue in pixels
        )
    if lighting.timeline:
        return LightingTimeline(
            pixel_count=output.pixel_count, cues=output.lighting_cues, brightness_limit=limit
        ).render(position_ms, base_frame=base_frame)
    if lighting.effect == "segment_pulse":
        if output.lighting_target_indices:
            return TargetPulse(
                pixel_count=output.pixel_count,
                target_indices=output.lighting_target_indices,
                pulse_times_ms=lighting.pulse_times_ms,
                pulse_duration_ms=lighting.pulse_duration_ms,
                color=output.color,
            ).render(position_ms)
        if output.lighting_segment is not None:
            return SegmentPulse(
                pixel_count=output.pixel_count,
                segment_start=output.lighting_segment.start,
                segment_end=output.lighting_segment.end,
                pulse_times_ms=lighting.pulse_times_ms,
                pulse_duration_ms=lighting.pulse_duration_ms,
                color=output.color,
            ).render(position_ms)
        raise RuntimeError("segment_pulse scene has no bound pixel target")
    return SyntheticSweep(
        pixel_count=output.pixel_count, cycle_ms=duration_ms, color=output.color
    ).render(position_ms)
