# Cueverge

Cueverge is an early-stage, self-hosted reactive show engine. A controller owns
scene selection and lifecycle, while a media agent reports the authoritative
playback position used to render synchronized lighting. Scenes can instead
explicitly use the controller's monotonic clock, including LED-only shows
with no media agent. See [controller-timed scenes](docs/scene-schema.md#controller-timed-scenes)
and the [LED-only example](shows/led-only-demo).

This repository contains the software vertical slice, declarative autonomous
show direction, aggregate audience presence, safe transition arbitration,
deterministic semantic lighting, persistent looks, operational controls, and
accelerated in-memory simulation. Windows/Linux mpv, RGB DDP, and Frigate MQTT
adapters are opt-in; no physical output or observation ingress is enabled by
default.

## Service documentation

Start with the [documentation index](docs/README.md), or choose your role:

- [Service administration guide](docs/service-administration.md): requirements,
  deployment, networking, media hosts, users and permissions, monitoring,
  upgrades, backup, and recovery.
- [Installation and show operator guide](docs/operator-guide.md): installation
  records and mapping, assets, scene/timeline and autonomous authoring,
  rehearsal, release handover, and show-day operation.

Both guides include integrated-browser screenshots from an isolated synthetic
controller and distinguish implemented behavior from current limitations.

## Development

Requirements:

- [uv](https://docs.astral.sh/uv/)
- Python 3.12 or newer (uv can install it)

```bash
uv sync --all-groups --python 3.12
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest
```

Pytest adds the repository root to its import path so the suite can also test
the local-demo tooling in `examples/`, which is not part of the installed engine
package. CI uses the same configuration and commands.

Run the controller:

```bash
uv run uvicorn cueverge.controller.app:app --host 127.0.0.1 --port 7000
```

The browser GUI and certificate-enrollment API remain disabled unless
explicitly configured. When enabled, the controller process also hosts the
separately secured mTLS-only protocol-v2 listener on its configured port. The
listener requires device enrollment, verified TLS termination for the
enrollment API, a private CA-key password, and explicit listener DNS/IP
identities. See `examples/controller.env.example`.

The authenticated GUI API binds wherever Uvicorn is configured. The old
unauthenticated protocol-v1 HTTP and WebSocket surfaces have been removed.
Media agents connect only through the certificate-authenticated protocol-v2 listener.
See [operations and simulation](docs/operations.md) for mode semantics,
diagnostics, container deployment, native tool dependencies, and the
hardware-free test harness.

## Repository boundaries

- `src/cueverge/` is the generic, reusable engine.
- `shows/demo/` is a redistributable synthetic example.
- `shows/multi-output-demo/` is a hardware-free schema-v2 example with two
  media agents and two lighting outputs.
- `shows/halloween/` is only a placeholder for a private reference
  implementation. No installation details or copyrighted media belong in the
  engine or public demo.

See [the architecture](docs/architecture.md), [the protocol](docs/protocol.md),
[the milestone plan](docs/milestones.md), and the
[show creator guide](docs/show-creator-guide.md). Autonomous show capabilities
are tracked in the
[show readiness roadmap](docs/show-readiness-roadmap.md).
The browser operator and authoring implementation plan is tracked in the
[web controller and show designer roadmap](docs/gui-roadmap.md).
Media-host setup is documented for
[Windows](docs/windows-media-agent.md) and [Linux](docs/linux-media-agent.md).

## Browser GUI

The opt-in GUI includes installation mapping, revisioned show authoring,
collaboration, simulation, rendering, publication, and authorized live controls.
It remains disabled by default. See the
[service administration guide](docs/service-administration.md) for deployment
and access control, and [the GUI foundation analysis](docs/gui-foundation.md)
for the original architectural boundaries.

Generate a private administrator password hash:

```bash
uv run cueverge-gui-hash-password
```

Configure the `CUEVERGE_GUI_*` settings shown in
`examples/controller.env.example`, start the controller, and run the frontend
development server separately:

```bash
cd web
npm ci
npm run dev
```

Use TLS and secure cookies for every non-localhost browser deployment.
Discovery does not establish trust: device enrollment, resource assignment,
and the separately configured mTLS listener are required for device operation.
Publishing or activating rendered device artifacts does not reload the
running controller's scene catalog; follow the documented release handover.
