# ADR 0006: Use declarative encounter transitions and priority arbitration

- Status: Accepted
- Date: 2026-10-08

## Context

External detectors can emit duplicated, delayed, and noisy observations. A
group should normally produce one encounter, and automatic reactions must not
override a higher-priority operator command.

## Decision

Show packages define encounter states and event transitions declaratively. The
engine validates generic events, applies TTL, idempotency, per-type debounce,
minimum state age, inactivity timeout, cooldown, and ambient fallback. A
controller-owned timer advances time-based transitions without new events.

Automatic scene requests compare logical resource claims and scene priority.
Higher priority replaces lower priority; lower-priority `queue` requests are
deduplicated and retained; other lower-priority requests are ignored. Explicit
scene-start API calls remain operator commands and force replacement.

## Consequences

Detector-specific payloads stay outside the core. Tests can reproduce
encounters without cameras or Home Assistant. Version 1 still has one active
coordinated scene and does not claim simultaneous video compositing. Durable
event history and distributed deduplication remain future work.
