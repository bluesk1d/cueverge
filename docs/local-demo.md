# Persistent local UI sandbox

Use this sandbox to exercise real uploads, mapper saves, linked viewpoints,
projector previews, and asset processing without production data or hardware.
Unlike `web/demo.html`, this is a real authenticated controller with persistent
SQLite state, not a browser fixture.

The sandbox supports managed **Apply saved changes**, retained restore and the
opt-in **Operate** output monitor. The launcher disables DDP, enrollment and the
device listener; camera ingress starts disabled. Verify the memory sink and
intended fixture before starting a test show. The default controller memory
history is bounded to 120 frames, independently of the monitor's 32-resource/
4096-sample budget. Managed restart recovery verifies the retained show and
leaves playback stopped; changing the bootstrap package alone does not replace it.

## Start

From the repository root, with the existing Python development environment,
frontend dependencies, and Node installed (demo media is generated with the
PyAV-bundled FFmpeg libraries, so no `ffmpeg` executable is needed):

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

## Live source development

Keep the local controller running on port 7000 and use the Vite frontend on port
4173 while working through the usability milestones. Open the normal application
at <http://127.0.0.1:4173/>, sign in with the local `demo` account, and use the
same persistent sandbox data as the built application. The production/shared
remote installation is not involved.

From the repository root, this launch command uses the existing Vite
configuration with a writable sandbox cache. It also avoids writing bundled
configuration or dependency caches into a pre-existing root-owned `node_modules`:

```bash
node --input-type=module <<'JS'
import { createServer } from './web/node_modules/vite/dist/node/index.js';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';

const server = await createServer({
  root: resolve('web'),
  configFile: resolve('web/vite.config.ts'),
  configLoader: 'runner',
  cacheDir: join(homedir(), '.local/share/cueverge/local-demo/vite-cache'),
  server: { host: '127.0.0.1', port: 4173, strictPort: true },
});
await server.listen();
server.printUrls();
JS
```

Use an installed Node version supported by the project's Vite version. Stop Vite
with Ctrl+C when no longer needed. Keep both processes available between
milestones; Vite applies source changes without rebuilding the static frontend.
For browser tests, an existing server on port 4173 is reused. If the checkout's
test output is not writable, pass Playwright a writable `--output` directory
inside the sandbox rather than changing shared directory permissions.

For UP-00 visual verification, the local `UP-00 Layout Regression` project has a
three-minute synthetic scene. Open its **Autonomous show logic designer** and
**Rehearsal, publication, and rollback** sections together, then choose **Open
timeline editor**. The diagram stays within the card, the full-screen timeline
scrolls independently, and table columns and bottom controls remain reachable. Do not publish or run
physical tests merely to inspect layout.

UP-01 also provides a local **UP-01 Preservation Baseline** project, created
from the original synthetic
[shared regression fixture](../tests/fixtures/usability-authoring.json).
It contains a raw ten-primitive timeline, a schema-v2 legacy sweep, and a
schema-v1 pulse. In its timeline editor (**Scenes → Edit timeline**), select **Original synthetic legacy
sweep**: the stored zoom is 100 pixels/second and snap is 250 ms, and both survive
Save timeline. This checks preservation, not the planned UP-03 effect editor.
The local scoped MCP proposal/simulation check is isolated and unmerged; its
temporary access token was revoked after verification.

UP-02 is visible in **Overview**: it uses a saved synthetic photo, distinguishes
Stopped from controller connectivity, provides display shortcuts, and updates
its Last checked time automatically. Account/role/sign-out are in the avatar
disclosure. Missing fake-device observations remain unknown rather than being
treated as healthy hardware. Optional setup and technical details are below
the main summary. REST/MCP snapshot reads were checked against the same local
runtime using a temporary, subsequently revoked read-only project token.

UP-03 is visible under **Shows**. Choose **Open show** on
**UP-03 Scene Editor Demo**: this original synthetic project was populated with
56 raw effects through MCP and adapted to the sandbox's 605-pixel allocation.
It is not a copy of private production scenes or media. A browser brightness
edit was read back through MCP with every unrelated field unchanged, and
evaluated output changed as intended. All 605 saved photo locations were shown,
no package was published, and no hardware command was issued.
The verification token was revoked; the project remains for hands-on review.
Press **Play scene preview**, choose effects, or inspect the legacy scenes.
Saving stays separate from live output.

UP-04 is visible in **Installation > Inputs & connections**. A fresh sandbox uses
GUI-managed setup with camera events off. Open its setup disclosure to inspect
broker/TLS settings and optional login; reception and isolated examples have
separate disclosures. Do not enable a real production broker for UI verification.
The local REST/MCP configure/read/example workflow was verified with ingress
disabled, the original settings restored, and the temporary scoped token revoked.
The stock loaded sandbox has no audience rules: its example can normalize input
but honestly reports that no visitor rules are loaded. Tests exercise actual
receiver delivery/reconnect and qualification with an isolated connector/runtime;
they do not establish deployment-specific live reception.

UP-05 adds **UP-05 Visitor Response Demo**, an original two-scene 605-pixel show.
Open **Visitor responses** to inspect **Welcome porch visitors**, or **Try it**
to open the shared visitor Preview. Example camera `porch`, zone `walkway`:
arrive, advance one second to qualify, then four seconds to select the welcome
scene at the ambient boundary. Advance another three seconds to return to ambient.
Scene preview separately plays the LED animation; visitor checkpoints are
step-by-step evidence, not continuous physical playback.

The response was authored through the real browser guide, including explicit
shared completion consent. Its unsaved candidate was tried before Save.
MCP readback confirmed that only the requested visitor/response/completion/name
fields changed, and verified qualification, welcome selection and return. Saved
head is revision 3. Live camera ingress remains off, live playback remains stopped,
and temporary verification tokens were revoked. No production project or hardware
was contacted. Like other hand-created verification projects, this demo is retained
in the current sandbox; `seed` does not recreate or overwrite it.

UP-06 is available in **Installation** with separate **Photos & Layout**, **Devices**
and **Inputs & connections** sections. The default gallery has one description
field; card details contain optional image metadata. **Open Layout** selects that
photo and starts in Select. Add an LED segment/projector area explicitly; existing
objects can be selected on the photo or in the list. Strand labels and calibration
are contextual. Device details and Settings administration no longer fill the page.

The retained original **UP-06 Porch setup · mapped LEDs** image contains
**UP-06 porch test strip**, three LEDs at indices 400–402 on the memory-backed
`porch.demo` output. It was uploaded and mapped through the shared browser.
Its description was edited in the browser and separately through a temporary
`installation_edit` MCP token, then restored. Photo/view identity, image metadata
and every existing geometry record were verified unchanged by description edits.
The photo's saved revision is 4; tokens were revoked. This is a synthetic setup
fixture, not a live output test, and is retained in the current sandbox rather
than recreated by `seed`.

Changing custom-hook structure during source development can leave React Fast
Refresh with stale hook state. A full reload restores a clean module instance;
only reload once unsaved local editor changes have been handled. Saved sandbox
data persists. The UP-02 shared tab was checked after a clean reload with no
page errors and automatic refresh continuing.

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
uses an in-memory lighting sink, and disables DDP, enrollment, and the
device listener. MQTT starts off in a fresh sandbox; GUI-managed input settings
persist across restarts, so leave camera events off unless an isolated test broker
is explicitly authorized. It does not load the private controller environment or enroll
the production clients. Disconnected synthetic media targets are expected.
Maintenance patterns and mapper traversal remain simulations, not live output.
HTTP cookies are allowed only for this local development setup; do not expose
this launcher through a public reverse proxy.

Stop the foreground controller with Ctrl+C. Rebuild the isolated frontend after
source changes, or use Vite for hot reload. Do not seed or run show rehearsals
against production as part of this workflow.
