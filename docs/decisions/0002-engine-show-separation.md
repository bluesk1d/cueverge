# ADR 0002: Separate engine and show packages

- Status: Accepted
- Date: 2026-10-08

## Context

Installations have their own geometry, addresses, and media. The engine must
support unrelated installations and safe open-source distribution.

## Decision

Reusable orchestration, timing, protocol, and rendering code lives under
`src/cueverge`. Redistributable synthetic content lives under `shows/demo`.
Installation-specific definitions and private asset references belong under a
separate private show package and are not imported by the engine.

## Consequences

Core tests cannot depend on a house, holiday, brand, or copyrighted asset.
Installation validation can evolve independently without turning reference
content into engine behavior.
