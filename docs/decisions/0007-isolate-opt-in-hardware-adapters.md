# ADR 0007: Isolate and explicitly enable hardware adapters

- Status: Accepted
- Date: 2026-10-08

## Context

The Windows laptop choice permits a concrete mpv IPC transport, and WLED
documents DDP reception, but actual device names, network addresses, pixel
format, power limits, and restoration behavior remain unknown.

## Decision

Implement mpv named-pipe IPC, the outbound media-agent protocol, and RGB DDP
packetization behind typed adapter interfaces. Resolve media only under a
configured local asset root. Keep DDP disabled by default and require an
explicit host to enable it. Validate adapters with fake pipes, players,
WebSockets, and exact packet bytes rather than touching hardware.

## Consequences

Protocol and lifecycle defects can be found before deployment. Windows device
selection and WLED behavior still require opt-in hardware validation. RGBW,
authentication, calibration, ownership restoration, and deployment service
configuration remain incomplete and must not be inferred from adapter tests.
