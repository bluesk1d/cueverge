# ADR 0005: Load versioned declarative show packages

- Status: Accepted
- Date: 2026-10-08

## Context

Hardcoded scene registration mixes reusable engine behavior with installation
content and cannot validate resource or asset mistakes before playback.

## Decision

Load versioned JSON scene documents and a separate installation binding
document at startup. Scenes claim logical resources; installation bindings map
those names to adapter targets. Loading rejects unknown fields, duplicate IDs,
unbound or incorrectly typed resources, unsupported effects, invalid values,
missing assets, and asset paths outside the package root.

JSON is used for the first executable schema because it needs no additional
parser dependency. YAML can be added later as an equivalent input format if
authoring experience justifies it.

## Consequences

The controller no longer imports show-specific scene constants. Invalid
packages fail before an agent receives commands. Schema v1 remains deliberately
small and supports only behavior implemented by the simulation.
