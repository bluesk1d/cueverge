# Operations and simulation

For task-oriented service documentation, start with the
[service administration guide](service-administration.md) or the
[installation and show operator guide](operator-guide.md). This document is
the supporting engine/deployment reference, not the GUI walkthrough.

Production GUI operating limits and the administrator observability contract
are documented in [GUI scale budgets](scale-budgets.md).

## Local users and project access

The configured administrator remains the bootstrap and recovery identity. It
cannot be replaced through the GUI. Administrators create controller-managed
local users in Settings; passwords are stored only as salted scrypt hashes.
Disabling an account invalidates its existing sessions on their next request,
and role changes also take effect on the next request.

Local users see only projects with an explicit membership. Project roles govern
drafts, comments, change requests, rendering, simulation, and publication on
the server. The separate `can_live_output` membership permission is required
for operator actions and for activating, deactivating, or rolling back a
published package. Grant it only to operators who are authorized to affect the
physical installation. Review links contain high-entropy, hashed-at-rest
tokens, expose only revision identity metadata, and stop resolving after their
configured expiry.

## MCP-assisted authoring

The controller exposes the stateless Streamable HTTP MCP endpoint at `/mcp/`
when the GUI authoring service is enabled. Terminate TLS at the trusted reverse
proxy and configure its public HTTPS origin in `CUEVERGE_GUI_ALLOWED_ORIGINS`.
Cueverge uses that origin for the MCP resource URL and restricts MCP Host and
Origin headers to configured/local controller hosts.

Create client tokens from a project's **MCP-assisted authoring** workspace.
Token creation/revocation and human proposal-review actions are authenticated
GUI mutations and must include the session's `X-CSRF-Token` header. A
`csrf_failed` 403 is not an MCP bearer-token or device-certificate failure;
keep CSRF protection enabled and check the browser request/session.
The token secret is displayed once, stored only as a SHA-256 digest, bound to
one project and user, and limited to a maximum 90-day expiry. The safe default
is the `read` scope. Additional independent scopes are:

- `draft_edit`: create isolated proposals; never changes the working draft.
- `direct_edit`: save a working draft immediately as a new revision, without a
  proposal or human approval/merge. Requires designer, publisher, or administrator
  project access.
- `simulation`: run bounded deterministic simulations without hardware.
- `publish_prepare`: inspect publication readiness and package status.
- `publish`: update release metadata/approval, publish a validated immutable
  revision, and stage its artifacts. Requires publisher or administrator access.
- `activate`: activate a staged package's artifacts. Requires publisher or
  administrator project access **and** that project's live-output grant; global
  administrators bypass those membership checks, as in the GUI.
- `live_operation`: reserved; it does not grant direct editing, publishing,
  activation, scene transport, device enrollment, or access administration.

All optional scopes start unchecked. The three new write/deployment scopes are
independent: `direct_edit` does not grant `publish` or `activate`, and `publish`
does not grant `activate`. Existing tokens gain no new permissions. Issue a new
token with the intended scopes, update the client, and revoke its old token.
Grant these permissions only to trusted clients: direct edits and release
approvals can happen through chat without a subsequent GUI confirmation.

Current local-user status, project membership, role, and live-output grant are
rechecked on every MCP request. Disabling a user, removing project membership,
reducing the role, or revoking a token therefore takes effect immediately.
Rotate a client credential by creating a replacement, updating the client, and
revoking the old token. Revoke all affected tokens immediately if a secret may
have been disclosed.

Model output, filenames, asset metadata, comments, and imported text remain
untrusted data. MCP has no unrestricted filesystem, network, browser-internal,
device-enrollment, access-administration, or scene-transport tool. Proposals store a complete
schema-validated candidate draft, base revision, changed JSON paths, client/user
identity, diagnostics, and simulation result separately from the working
revision. A human designer or administrator must validate, approve with a
rationale, and merge through the cookie/CSRF-protected GUI API. Merge fails
closed if the working revision changed after proposal creation. Failed and
rejected proposals never affect the working revision or live show.

### Direct editing and deployment through chat

1. Use `get_project_draft` to read the complete working draft and revision,
   and `get_authoring_schema` for its authoritative JSON Schema.
2. With `direct_edit`, call `update_project_draft(base_revision, draft)` with the
   **complete** edited draft. It uses the same schema and transactional revision
   checks as GUI draft saves, creates a new immutable revision, and returns its
   revision and changed paths. Invalid, unchanged, or stale edits are rejected.
   On a conflict, reread and reconcile; do not blindly retry against a newer
   revision. Draft saves do not themselves compile, publish, or affect live output.
3. With `publish_prepare`, `publish`, or `activate`, inspect
   `get_publication_status` for release metadata, validation, and package states.
4. With `publish`, call `update_revision_release` for an explicit revision,
   providing release metadata and `approval_state="approved"` when ready.
   Then call `publish_revision(revision)`. Existing structural, semantic, asset,
   capability, bake, and cache validation still applies; a scope cannot bypass it.
5. With `publish`, call `stage_published_package(package_id)`. With the separate
   `activate` scope, call `activate_published_package(package_id)` to activate
   staged artifacts. Cross-project package IDs are rejected, and
   `simulation_mode=true` refuses activation.

Activation can change physical device output. Follow the same supervised
physical-rehearsal and safety procedures as GUI activation. It does **not**
replace the running controller scene catalog, start a scene, or restart the
controller. Catalog handover still requires an administrator to update
`CUEVERGE_SHOW_PACKAGE` and recreate/restart the controller.

The proposal/validation/simulation/human-merge workflow remains available with
`draft_edit`. A direct edit can make an outstanding proposal stale; that
proposal must still pass its original human review and revision-conflict checks.
Direct editing and release approval never implicitly approve or merge a proposal.

The server enforces a 256 KiB request limit, 60 tool calls per token per minute,
four concurrent calls per token, 20 active proposals per project, 100 returned
assets, five-minute timeline windows, 200 simulation steps, and ten minutes of
simulated time. Audit history records token creation/revocation, each requested
tool with an argument digest (not raw potentially sensitive content), proposal
diffs and validation, human approval/rejection, merge, and final disposition.
During an incident, revoke the token, disable the owning account if necessary,
review `mcp.tool.call`, `mcp.draft.update`, `mcp.revision.release.update`,
`mcp.package.*`, and `mcp.proposal.*` audit events, and inspect edited revisions,
release approvals, deployed packages, and every non-final proposal before
restoring access.

## Operating modes

Cueverge starts in `automatic` mode. The engine supports:

- `automatic`: external observations may drive audience, encounter, and
  opportunity behavior.
- `disabled`: stops the active scene and rejects automatic observations.
- `maintenance`: stops the active scene and rejects automatic observations
  while leaving direct operator scene control available.
- `blackout`: performs normal stop handling, emits an explicit all-black pixel
  frame, and rejects automatic observations.

The authenticated GUI uses `POST /api/gui/v1/operate/actions` with an `action`
of `automatic`, `maintenance`, or `blackout`; the current GUI does not expose
disabled mode. Its `reset` action starts the encounter ambient scene, or the
program initial scene when no encounter is configured. Reset and direct
engine scene starts do not silently change the selected operating mode.
Browser mutations require the authenticated session, allowed origin, CSRF
header, and live-output authorization. The old `/api/v1/...` endpoints are
removed, not aliases for these actions.

Observation ingress may remain connected outside automatic mode. The runtime
rejects those observations before they can alter presence, opportunities,
encounters, or scene selection.

## Status and health

The authenticated `GET /api/gui/v1/runtime/status` response wraps runtime
facts in `status`, alongside `observed_at`. Runtime facts include:

- operating mode and whether automatic show behavior is enabled
- active and queued scenes
- director pool, recent selection history, eligible scenes, conditions,
  availability, and remaining cooldowns
- audience state, counters, and the 20 most recent observation summaries
- pending, qualifying, consumed, and expired engagement opportunities
- media connection, command, lifecycle, rendering, and timing diagnostics

Recent observation summaries include event type, camera, zone, timestamps, and
whether the observation was relevant. They intentionally omit detector object
IDs.

Health reporting is capability-aware:

- media-agent connectivity is reported as connected or disconnected
- the configured lighting sink type is reported
- projector and audio are `not_independently_detectable`

Cueverge does not report undetectable devices as healthy. Downstream electrical
provisioning and device-local safety remain installation responsibilities.

## Deployment dependency inventory

The machine-readable source for controller image construction and preflight is
[`deploy/controller-native-dependencies.json`](../deploy/controller-native-dependencies.json).
Update that manifest in the same change whenever controller code adds a native
executable, codec, filter, system library, build tool, or persistent path. This
section explains the policy; the Dockerfile and image-preflight checks
consume or validate the manifest rather than maintaining an independent list.

The controller Dockerfile derives its required and optional Debian package
specifications from this inventory. The startup preflight reads the same file
and fails closed when required executables, FFmpeg encoders/filters, FFprobe
features, or required ClamAV definitions are unavailable. Keep the inventory
synchronized with runtime subprocess calls rather than adding package or
capability lists directly to Docker or shell files:

| Tool | Image role | Requirement |
| --- | --- | --- |
| Python 3.12+ and the locked `pyproject.toml`/`uv.lock` environment | Controller runtime | Required |
| `ffmpeg` and `ffprobe` (normally supplied by the distribution's `ffmpeg` package) | Asset metadata, loudness analysis, thumbnails, review proxies, waveforms, and render jobs | Required for asset processing and rendering |
| `clamscan` and current ClamAV definitions | Uploaded-asset malware scanning | Optional, but production deployments that require malware scanning must install both and arrange definition updates |
| Node.js and npm | Building the React/Vite application | Build-stage only; do not retain them in the final controller image |
| CA certificates | TLS connections to external services and devices | Required in the runtime image |

Base images are digest-pinned and required native package versions are pinned
in the inventory. Update each package version and its base-image digest
together after rebuilding and running the dependency inventory tests. Startup
reports `ffmpeg`, `ffprobe`, and configured scanner versions and verifies the
declared codecs, filters, and probe features used by generated artifacts.
The Dockerfile must also create writable persistent mounts for GUI data,
original assets, derived artifacts, logs, and other configured state; these
must not live only in the container layer.

`mpv` is a media-agent dependency, not a headless controller-image dependency.
It needs access to the host display, audio devices, GPU/decoder stack, and IPC,
so install and validate it on each Windows or Linux media host as described in
the media-agent setup guides. If controller-side rendering later introduces
fonts, color profiles, GPU runtimes, or additional codecs and filters, add them
to this inventory and to reproducibility/preflight checks before relying on
them.

### Controller container

The repository Dockerfile builds the React application and locked Python
environment in separate stages. The final image runs as an unprivileged user,
contains `ffmpeg`, `ffprobe`, and CA certificates, exposes the existing
liveness/readiness endpoints as its container healthcheck, and stores mutable
GUI state under `/var/lib/cueverge`. It includes the synthetic demo show as a
safe default; mount a private show package read-only and set
`CUEVERGE_SHOW_PACKAGE` to use another package.

Build and start the controller-only default:

```bash
docker compose build
docker compose up -d
docker compose ps
```

The GUI remains disabled unless its required authentication and origin
settings are supplied. Generate the administrator hash outside the container,
store it in a private Compose environment file or secret-management system,
and set at least:

```dotenv
CUEVERGE_GUI_ENABLED=true
CUEVERGE_GUI_ADMIN_PASSWORD_HASH=scrypt$...
CUEVERGE_GUI_ALLOWED_ORIGINS=https://cueverge.example.test
CUEVERGE_GUI_SECURE_COOKIES=true
```

Terminate TLS at a trusted reverse proxy for non-localhost browser access.
Do not publish the mTLS device port unless device enrollment and listener
identity settings are fully configured.

ClamAV is deliberately an image variant because its package and definitions
materially increase image and update size:

```bash
CUEVERGE_INSTALL_CLAMAV=true docker compose build
```

Arrange regular signature updates in the deployment platform and persist or
inject the definition directory. Set `CUEVERGE_CLAMAV_REQUIRED=true` only after
definitions are available; container preflight then fails closed if `clamscan`
or its definitions are missing. Without that setting, scanner absence remains
visible in asset processing as documented above.

## Accelerated simulation

`cueverge.simulation.ShowSimulation` runs a loaded `SceneCatalog` with:

- a manual monotonic and event clock
- `FakeMediaAgent`
- `MemoryFrameSink`
- deterministic director selection
- synthetic observations
- automatic scene completion while advancing simulated time
- operator scene starts and automatic scene requests
- media-agent disconnect and reconnect

The harness never constructs MQTT, mpv, DDP, or physical output adapters.

```python
simulation = ShowSimulation(catalog)
await simulation.start()
await simulation.observe(
    "frigate.zone.entered",
    attributes={
        "source": "frigate",
        "object_id": "synthetic-1",
        "label": "person",
        "camera": "doorbell",
        "zone": "Porch",
    },
)
await simulation.advance(30 * 60 * 1000)
status = simulation.runtime.status()
await simulation.shutdown()
```

Simulation results are available through `scene_history`, the in-memory
lighting frames, recorded agent commands, and normal runtime status.

## Publication and client cache contract

Publishing snapshots an approved project revision into an immutable,
content-addressed directory under `/var/lib/cueverge/published`. Its manifest
records the source revision, revision name and summary, creator, timestamp,
compiled package digest, per-file schema version/size/checksum, referenced
asset checksums, render artifact identities, required capabilities, and
validation results. Publication never changes the active show.

Deployment is deliberately split into four attributable operations:

1. **Publish** creates the immutable controller package.
2. **Stage** transfers only missing checksummed render members. Clients resume
   partial range downloads, verify size and SHA-256, and atomically rename
   completed temporary files.
3. **Activate** is allowed only after staging, activates rendered artifacts
   with required client acknowledgements, and updates the active publication
   record.
4. **Deactivate/roll back** retains the previous package and reactivates a
   previously staged or inactive package.

These operations do not reload the running controller's scene catalog.
Deactivation updates publication state and does not stop physical playback.
Scene-logic handover and rollback require an explicit package configuration
change and controller restart; see
[release handover](service-administration.md#release-handover-and-upgrades).

Every stage command carries the complete protected digest set for the
candidate, active, and retained rollback packages. The media agent also
protects the currently playing digest. When capacity is insufficient it
evicts only other artifacts, oldest access first; it never deletes partial
metadata, the active file, a rollback file, or a candidate file. If safe
reclamation cannot satisfy the manifest, staging fails explicitly and the
active package remains unchanged. Inventory distinguishes verified cache
members, active transfers, pinning/in-use state, free capacity, and faults.
Operators interact only with package/cache identities and never arbitrary
filesystem paths.

## Backup, restore, and disaster recovery

Back up the complete `/var/lib/cueverge` volume as one consistency unit. It
contains the configured GUI data root, normally `/var/lib/cueverge/gui` in
the supplied image. Paths below are relative to that GUI data root:

- `gui.sqlite3` plus its SQLite WAL/SHM files: projects, revisions,
  approvals, device identities, assignments, render metadata, publication
  state, and audit history.
- `assets/`: original uploads, installation reference images, and generated
  review artifacts.
- `renders/`: immutable baked projector artifacts.
- `compiled/` and `published/`: reproducible compiled and published packages.
- `device-ca/`: the controller device CA and signing key material.
- `simulation-scenarios/`: saved deterministic rehearsal inputs.

Use a storage snapshot or stop the controller before a file-level backup.
Never copy only the SQLite main file while the controller is writing. Encrypt
backups containing the device CA and restrict them as production credentials.
Retain at least one known-good backup outside the Docker host.

Restore procedure:

1. Stop the controller and preserve the failed volume for forensic recovery.
2. Restore the entire saved volume into a fresh, empty volume with ownership
   matching the unprivileged `cueverge` user.
3. Start the same application/image version first so repository migrations run
   in their original order.
4. Verify `/health/ready`, project revision history, published manifest
   checksums, asset availability, device CA presence, and audit continuity.
5. Reconnect clients and wait for fresh cache inventories. Do not activate
   until assignments, capabilities, free space, pinning support, checksums, and
   current bake validation all pass.
6. Rehearse the retained rollback package, then stage and activate explicitly.

If the device CA is lost, enrolled device identities cannot be reconstructed
from project data; provision a new CA and re-enroll every device. If derived
artifacts are lost but originals and revision history survive, rerun bakes and
publish a new package rather than manufacturing files under an old digest.
