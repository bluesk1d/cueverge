# ADR 0010: Isolate the GUI web application and authoring service

- Status: Accepted
- Date: 2026-10-12

## Context

Cueverge needs a browser product without coupling frontend build concerns to
the reusable timing and orchestration engine. Browser code must not become a
second implementation of lighting evaluation, director selection, or media
clock behavior.

## Decision

Build the browser application with React, TypeScript, and Vite in a separate
`web/` workspace. Add versioned browser APIs under `/api/gui/v1` in an isolated
`cueverge.gui` package. The GUI may read controller status through an adapter,
but frontend code does not import engine internals and the authoring service
does not mutate an active `SceneCatalog`.

Use the following browser application layers:

- shadcn/ui components built on Radix UI for ordinary accessible controls;
- Tailwind CSS with Cueverge semantic design tokens;
- Zustand for transient editor and interaction state only;
- TanStack Query for server-owned state, mutations, and cache reconciliation;
- React Hook Form and Zod for form interaction and immediate client feedback;
- FastAPI OpenAPI as the source for generated TypeScript API contracts;
- Vitest and React Testing Library for unit and component tests;
- Playwright and axe-core for browser workflow and accessibility tests;
- Mock Service Worker for frontend API-contract tests.

Durable projects, runtime status, and authorization do not live in Zustand.
Passing client-side Zod validation does not imply that a draft is valid for
compilation or publication. Pydantic, service authorization, the compiler, and
the existing show loader remain authoritative on the server.

The initial controller integration is opt-in. Vite owns development serving;
production static-file hosting remains a deployment choice and is not embedded
in the Python package in GUI-00. Production uses Vite's static output and does
not require a Node.js application server. A reverse proxy may provide TLS,
security headers, compression, and upload limits.

## Consequences

The engine remains usable without Node.js or browser assets. Frontend releases
can be tested and built independently. Deterministic preview must later call
backend evaluators rather than reproducing their semantics in JavaScript.
Generated contracts reduce drift between Pydantic and TypeScript, while the
application retains explicit handling for sessions, CSRF, `If-Match`,
idempotency, correlation IDs, and typed errors.
