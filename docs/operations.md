# Operations and simulation

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

## Operating modes

Cueverge starts in `automatic` mode. `POST /api/v1/show/mode` accepts:

- `automatic`: external observations may drive audience, encounter, and
  opportunity behavior.
- `disabled`: stops the active scene and rejects automatic observations.
- `maintenance`: stops the active scene and rejects automatic observations
  while leaving direct operator scene control available.
- `blackout`: performs normal stop handling, emits an explicit all-black pixel
  frame, and rejects automatic observations.

`POST /api/v1/blackout` is a shortcut for blackout mode.
`POST /api/v1/show/reset` starts the encounter ambient scene, or the program
initial scene when no encounter is configured. Reset and direct scene starts do
not silently change the selected operating mode.

Observation ingress may remain connected outside automatic mode. The runtime
rejects those observations before they can alter presence, opportunities,
encounters, or scene selection.

## Status and health

`GET /api/v1/status` includes:

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
section explains the policy; the eventual Dockerfile and image-preflight test
must consume or validate the manifest rather than maintaining an independent
list.

The eventual controller Dockerfile must install and version the non-Python
tools used by enabled features. Keep this inventory synchronized with runtime
subprocess calls and verify required executables during image build and
container startup rather than discovering omissions after an upload:

| Tool | Image role | Requirement |
| --- | --- | --- |
| Python 3.12+ and the locked `pyproject.toml`/`uv.lock` environment | Controller runtime | Required |
| `ffmpeg` and `ffprobe` (normally supplied by the distribution's `ffmpeg` package) | Asset metadata, loudness analysis, thumbnails, review proxies, and waveforms; future render jobs | Required when the asset workspace is enabled |
| `clamscan` and current ClamAV definitions | Uploaded-asset malware scanning | Optional, but production deployments that require malware scanning must install both and arrange definition updates |
| Node.js and npm | Building the React/Vite application | Build-stage only; do not retain them in the final controller image |
| CA certificates | TLS connections to external services and devices | Required in the runtime image |

Pin the base image and package versions or digests where practical, record
`ffmpeg -version`, `ffprobe -version`, and scanner/version information in build
provenance, and test the codecs and filters used by the generated artifacts.
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
3. **Activate** is allowed only after staging and atomically replaces the
   active package after every required client acknowledges activation.
4. **Deactivate/roll back** retains the previous package and reactivates a
   previously staged or inactive package.

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
contains:

- `gui/gui.sqlite3` plus its SQLite WAL/SHM files: projects, revisions,
  approvals, device identities, assignments, render metadata, publication
  state, and audit history.
- `assets/`: original uploads and generated review artifacts.
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
