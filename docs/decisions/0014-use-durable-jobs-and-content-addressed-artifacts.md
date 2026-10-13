# ADR 0014: Use durable jobs and content-addressed artifacts

- Status: Accepted
- Date: 2026-10-12

## Context

The complete GUI needs asset inspection, thumbnails, waveforms, proxies,
validation, simulation, package compilation, media baking, and deployment
staging. These operations can outlive an HTTP request and produce large binary
artifacts. Storing those binaries in SQLite or running unbounded work inside
FastAPI request handlers would make recovery and resource control unreliable.

## Decision

Introduce a bounded durable background-job service when GUI-02 begins asset
processing. Start with a SQLite-backed queue and worker suitable for the
single-controller deployment. Jobs have typed inputs and results, explicit
states, progress, cancellation where safe, retry policy, resource limits,
correlation IDs, and crash recovery. Do not require Redis, Celery, or a
distributed queue until measured workload or deployment topology requires one.

Store original assets, proxies, waveforms, compiled packages, and baked media
in configured content-addressed filesystem roots. Identify immutable artifact
revisions by SHA-256, write into temporary storage, verify size and digest, and
atomically promote them. SQLite stores metadata, references, job state,
provenance, retention, and pinning rather than large binary payloads.

Active, staged, playing, and rollback-critical artifacts are pinned against
cleanup. API clients never receive arbitrary filesystem paths or browse the
host filesystem.

## Consequences

Long work remains observable and recoverable across browser disconnects and
controller restarts. Identical artifacts can be deduplicated and verified.
Backups must include both the relational database and referenced artifact
roots, with consistency and restoration tests.

The first GUI-00 compiler may remain synchronous because its fixture is small
and hardware-free. Moving compilation behind the job boundary becomes
necessary once packages include managed assets or expensive processing.
