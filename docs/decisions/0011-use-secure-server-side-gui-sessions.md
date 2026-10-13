# ADR 0011: Use secure server-side GUI sessions

- Status: Accepted
- Date: 2026-10-12

## Context

The current controller APIs and agent protocol were built for a local software
slice and do not authenticate browser users. Exposing authoring data or
hardware operations through an unauthenticated LAN application would violate
the GUI product and safety contracts.

## Decision

Use opaque server-side browser sessions stored in the GUI database. Store only
a digest of each session token, set the browser token in an HttpOnly,
SameSite=Strict cookie, and expire sessions on the server. Require an
allowed-origin check and a session-bound CSRF token for every state-changing
request. Enforce roles in backend dependencies and audit authentication and
authoring mutations.

GUI-00 bootstraps one configured administrator from a scrypt password hash.
Additional identities, recovery, rotation, and external identity providers are
future account-management work. This decision does not authenticate media
agents; secure enrollment requires a separately versioned protocol decision.

## Consequences

Stealing database contents alone does not reveal reusable session or password
credentials. A frontend cannot gain authority by hiding or showing controls.
Deployments must explicitly configure allowed origins and secure-cookie
behavior, and TLS is required whenever the GUI is not loopback-only.

