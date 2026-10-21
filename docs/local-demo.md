# Persistent local UI sandbox

Use this sandbox to exercise real uploads, mapper saves, linked viewpoints,
projector previews, and asset processing without production data or hardware.
Unlike `web/demo.html`, this is a real authenticated controller with persistent
SQLite state, not a browser fixture.

## Start

From the repository root, with the existing Python development environment,
frontend dependencies, Node, `ffmpeg`, and `ffprobe` installed:

```bash
.venv/bin/python examples/local_demo.py prepare
npm --prefix web run build -- --configLoader runner \
  --outDir "$HOME/.local/share/cueverge/local-demo/web"
.venv/bin/python examples/local_demo.py serve
```

In another terminal:

```bash
.venv/bin/python examples/local_demo.py seed
```

Open <http://127.0.0.1:7000/>. The username is `demo`; its randomly generated
password is stored in the owner-only file
`~/.local/share/cueverge/local-demo/credentials.json`. Do not commit or share
that file. For development against live source, the existing Vite proxy also
works with this controller: open the normal application at
<http://127.0.0.1:4173/> rather than `/demo.html`, or start the usual Vite server
on port 5173.

The [helper](../examples/local_demo.py) accepts `--root PATH` on each command.
Use the same root for all commands and adjust the frontend build output to
`PATH/web`. It refuses nonempty directories without its sandbox marker.

## Included data

- Two original synthetic house reference images: front and angled views.
- Eight linked roofline segments in both views, with the Halloween allocation:
  162, 34, 46, 46, 89, 87, 72, and 69 pixels, totaling 605.
- Lower/upper strand assignments, fixture/group/path associations, and saved
  lighting surfaces for allocation and calibration testing.
- Two linked projector surfaces with a generated calibration-grid image.
- A `Halloween UI Sandbox` project copied from the stock synthetic scenes.
- Managed image, four-second color-motion video, and quiet four-second test-tone
  assets, processed through the actual upload and review-artifact pipeline.
- A second 480-pixel output for trying new mappings without overlapping the
  populated roofline output.

In **Installation**, choose either reference view and select a segment to edit
its allocation. Create a new segment on `porch.demo` to test tracing/generation,
or overlap `roofline.demo` deliberately to exercise warnings. In **Shows**, open
the sandbox project to browse and preview the managed assets. The copied stock
scenes are starting templates; uploading media does not automatically replace
their media references or publish anything.

## Persistence and isolation

All state, generated source assets, credentials, the private copy of the stock
show, and the frontend build live under the sandbox root, outside the checkout.
Restart with `serve`; no reseeding is required. Rerunning `seed` skips existing
named photos, geometry, project, and media rather than resetting edits. It does
not restore deleted/modified content to an exact baseline.

The launcher clears inherited `CUEVERGE_*` settings, binds only to loopback,
uses an in-memory lighting sink, and disables DDP, MQTT, enrollment, and the
device listener. It does not load the private controller environment or enroll
the production clients. Disconnected synthetic media targets are expected.
Maintenance patterns and mapper traversal remain simulations, not live output.
HTTP cookies are allowed only for this local development setup; do not expose
this launcher through a public reverse proxy.

Stop the foreground controller with Ctrl+C. Rebuild the isolated frontend after
source changes, or use Vite for hot reload. Do not seed or run show rehearsals
against production as part of this workflow.
