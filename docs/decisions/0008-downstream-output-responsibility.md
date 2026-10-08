# ADR 0008: Trust provisioned downstream output devices

- Status: Accepted
- Date: 2026-10-08

## Context

Cueverge sends pixel data to independently configured output controllers such
as WLED. The engine can validate protocol compatibility, addressing, and frame
shape, but it cannot reliably infer a downstream installation's power supplies,
wiring, fusing, current limits, or safe operating envelope.

Applying a conservative brightness limit by default makes the engine
opinionated about concerns owned by the downstream controller and physical
installation. It also changes authored color values without the show declaring
that intent.

## Decision

Treat enabled downstream output devices as appropriately provisioned and
configured. Cueverge validates the expected pixel count and supported channel
format but sends authored RGB values unscaled by default.

Retain optional output scaling as an installation or artistic control, with a
default of `1.0`. Do not describe or rely on that scaling as electrical
protection. Electrical provisioning, current limiting, fusing, and safe
operating limits remain responsibilities of the downstream controller and
physical installation.

## Consequences

The generic engine preserves authored output and does not encode assumptions
about particular power systems. Operators must commission downstream hardware
before enabling it. Cueverge remains responsible for protocol correctness,
declared channel compatibility, lifecycle behavior, and relinquishing realtime
control cleanly.
