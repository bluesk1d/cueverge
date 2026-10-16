# Cueverge

Cueverge is an early-stage, self-hosted reactive show engine. A controller owns
scene selection and lifecycle, while a media agent reports the authoritative
playback position used to render synchronized lighting.

This repository contains the software vertical slice, declarative autonomous
show direction, aggregate audience presence, safe transition arbitration,
deterministic semantic lighting, persistent looks, operational controls, and
accelerated in-memory simulation. Windows/Linux mpv, RGB DDP, and Frigate MQTT
adapters are opt-in; no physical output or observation ingress is enabled by
default.

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

Control endpoints bind wherever Uvicorn is configured. Media agents connect
only through the certificate-authenticated protocol-v2 listener.
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
The future browser-based operator and authoring experience is tracked in the
[web controller and show designer roadmap](docs/gui-roadmap.md).
Media-host setup is documented for
[Windows](docs/windows-media-agent.md) and [Linux](docs/linux-media-agent.md).

## Browser GUI foundation

GUI-00 adds an opt-in authenticated authoring API and a separate React/Vite
workspace. The GUI is disabled by default and does not add browser live-control
actions. See [the GUI foundation analysis](docs/gui-foundation.md) for the
accepted technology stack and the security, persistence, compilation, and
compatibility boundaries.

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

Use TLS and secure cookies for every non-localhost browser deployment. GUI-00
does not authenticate the legacy agent protocol or convert discovery into
trust; do not expose the existing local controller surface as an enrolled
device-management API.
