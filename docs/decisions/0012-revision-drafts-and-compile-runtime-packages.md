# ADR 0012: Store immutable draft revisions and compile runtime packages

- Status: Accepted
- Date: 2026-10-12

## Context

The runtime loads declarative files directly and has no durable editor model.
Editing active package files would couple authoring to execution, leak
editor-only state, and make concurrent saves unsafe.

## Decision

Store projects and immutable draft revisions in SQLite. A project points to a
monotonic head revision. Every update supplies the expected revision through
`If-Match` and atomically appends a new revision; stale updates fail with a
structured conflict.

GUI-00 uses the standard-library SQLite API for its small initial schema. Move
the repository implementation to SQLAlchemy 2.x typed models and Alembic
migrations before GUI-02 expands persistence into assets and processing jobs.
Keep Pydantic API contracts separate from persistence models. Continue using
SQLite with foreign keys, WAL journaling, explicit transactions, and tested
backup and migration procedures for the initial single-controller deployment.
Do not introduce PostgreSQL until measured concurrency or operational
requirements justify its additional deployment cost.

Compile a supported draft into ordinary runtime JSON files in temporary
storage, validate them with the existing `load_show_package`, hash the emitted
documents, and atomically promote an immutable compiled package. Do not emit
editor metadata, modify the active package, or activate hardware.

Draft schema version 1 compiles only existing installation schema v1 and scene
schema v1/v2. Any future editor feature that cannot compile without loss
requires an explicit draft migration and, when necessary, a versioned runtime
schema proposal.

## Consequences

Autosave, undo, redo, review, and publication can build on one append-only
transaction model. Existing show packages remain valid and unchanged.
Installation-binding separation, managed assets, and release activation remain
future work rather than implicit behavior in the draft store.

The SQLAlchemy migration changes the repository implementation, not the
revision or API contract. Alembic becomes the authoritative database migration
history before the schema grows beyond GUI-00.
