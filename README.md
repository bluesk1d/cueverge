# Cueverge

Cueverge is an early-stage, self-hosted reactive show engine. A controller owns
scene selection and lifecycle, while a media agent reports the authoritative
playback position used to render synchronized lighting.

This repository contains the software vertical slice, declarative autonomous
show direction, aggregate audience presence, safe transition arbitration,
deterministic semantic lighting, persistent looks, operational controls, and
accelerated in-memory simulation. Windows mpv, RGB DDP, and Frigate MQTT
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
uv run uvicorn cueverge.controller.app:app --host 127.0.0.1 --port 8000
```

Control endpoints bind wherever Uvicorn is configured; authentication and
deployment configuration are planned before any non-loopback deployment.
See [operations and simulation](docs/operations.md) for mode semantics,
diagnostics, and the hardware-free test harness.

## Repository boundaries

- `src/cueverge/` is the generic, reusable engine.
- `shows/demo/` is a redistributable synthetic example.
- `shows/halloween/` is only a placeholder for a private reference
  implementation. No installation details or copyrighted media belong in the
  engine or public demo.

See [the architecture](docs/architecture.md), [the protocol](docs/protocol.md),
[the milestone plan](docs/milestones.md), and the
[show creator guide](docs/show-creator-guide.md). Autonomous show capabilities
are tracked in the
[show readiness roadmap](docs/show-readiness-roadmap.md).
