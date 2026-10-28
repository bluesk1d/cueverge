# Cueverge

Licensed under [GNU GPL version 3 only](LICENSE). You may use, modify, and
redistribute Cueverge, including commercially, under those terms. Distributing
modified versions requires providing corresponding source under GPL-3.0-only.

Cueverge is an early-stage, self-hosted reactive show engine. A controller owns
scene selection and lifecycle, while a media agent reports the authoritative
playback position used to render synchronized lighting. Scenes can instead
explicitly use the controller's monotonic clock, including LED-only shows
with no media agent. See [controller-timed scenes](https://bluesk1d.github.io/cueverge/docs/scene-schema.html#controller-timed-scenes)
and the [LED-only example](shows/led-only-demo).

This repository contains the software vertical slice, declarative autonomous
show direction, aggregate audience presence, safe transition arbitration,
deterministic semantic lighting, persistent looks, operational controls, and
accelerated in-memory simulation. Windows/Linux mpv, RGB DDP, and Frigate MQTT
adapters are opt-in; no physical output or observation ingress is enabled by
default.

## Documentation

Visit the [Cueverge website](https://bluesk1d.github.io/cueverge/) or start with
the [documentation index](https://bluesk1d.github.io/cueverge/docs/index.html).

- [User guide](https://bluesk1d.github.io/cueverge/docs/show-creator-guide.html):
  creative capabilities, show concepts, and a show brief template.
- [Service administration guide](https://bluesk1d.github.io/cueverge/docs/service-administration.html): requirements,
  deployment, networking, media hosts, users and permissions, monitoring,
  upgrades, backup, and recovery.
- [Installation and show operator guide](https://bluesk1d.github.io/cueverge/docs/operator-guide.html): installation
  records and mapping, assets, scene/timeline and autonomous authoring,
  rehearsal, release handover, and show-day operation.

The administration and operator guides include integrated-browser screenshots
from an isolated synthetic controller and distinguish implemented behavior from
current limitations.

Contributors can edit the [documentation sources](docs/README.md) and follow the
[site development instructions](site/README.md) to build and preview changes locally.

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
package. CI uses the same configuration and commands, running pytest in parallel
with `uv run pytest -n auto` (pytest-xdist); use that locally for faster full runs.

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
See [operations and simulation](https://bluesk1d.github.io/cueverge/docs/operations.html) for mode semantics,
diagnostics, container deployment, native tool dependencies, and the
hardware-free test harness.

## Repository boundaries

- `src/cueverge/` is the generic, reusable engine.
- `shows/demo/` is a redistributable synthetic example.
- `shows/multi-output-demo/` is a hardware-free schema-v2 example with two
  media agents and two lighting outputs.
- Keep installation-specific show packages and media separate from the reusable
  engine and redistributable demos.

See [the architecture](https://bluesk1d.github.io/cueverge/docs/architecture.html), [the protocol](https://bluesk1d.github.io/cueverge/docs/protocol.html),
[the milestone plan](https://bluesk1d.github.io/cueverge/docs/milestones.html), and the
[show creator guide](https://bluesk1d.github.io/cueverge/docs/show-creator-guide.html). Autonomous show capabilities
are tracked in the
[show readiness roadmap](https://bluesk1d.github.io/cueverge/docs/show-readiness-roadmap.html).
The browser operator and authoring implementation plan is tracked in the
[web controller and show designer roadmap](https://bluesk1d.github.io/cueverge/docs/gui-roadmap.html).
Media-host setup is documented for
[Windows](https://bluesk1d.github.io/cueverge/docs/windows-media-agent.html) and [Linux](https://bluesk1d.github.io/cueverge/docs/linux-media-agent.html).

## Browser GUI

The opt-in GUI includes installation mapping, revisioned show authoring,
collaboration, simulation, rendering, publication, and authorized live controls.
It remains disabled by default. See the
[service administration guide](https://bluesk1d.github.io/cueverge/docs/service-administration.html) for deployment
and access control, and [the GUI foundation analysis](https://bluesk1d.github.io/cueverge/docs/gui-foundation.html)
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
