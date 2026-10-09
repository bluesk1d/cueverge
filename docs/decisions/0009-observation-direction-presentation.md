# ADR 0009: Separate observation, show direction, and presentation

## Status

Accepted

## Context

An autonomous show must interpret noisy external observations, choose varied
content over time, and execute synchronized media and lighting. Combining these
responsibilities would couple Frigate payloads, programming policy, and output
timing into one state machine.

## Decision

Cueverge separates three layers:

- Observation normalizes external inputs into audience state and engagement
  opportunities.
- Show direction selects and schedules scenes using declarative programming,
  history, priorities, and valid opportunities.
- Presentation executes the selected scene using authoritative media time and
  deterministic lighting.

The presentation runtime exposes lifecycle and scene-selection boundaries but
does not choose content. The director consumes semantic audience information
but does not parse Frigate messages or render pixels. Observation code does not
know scene IDs or lighting geometry.

## Consequences

- Scheduling can be simulated without media or hardware.
- Lighting can be tested without cameras.
- External observation sources remain replaceable.
- Installation and Halloween concepts remain private show configuration.
- Cross-layer requests require explicit typed contracts and observability.
