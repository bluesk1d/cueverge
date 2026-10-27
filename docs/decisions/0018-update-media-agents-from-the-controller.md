# ADR 0018: Update media agents from the controller

- Status: Accepted; implemented
- Date: 2026-10-27
- Related: [mTLS device identities](0015-use-mtls-device-identities.md),
  [durable jobs and content-addressed artifacts](0014-use-durable-jobs-and-content-addressed-artifacts.md),
  [on-demand players](0017-start-media-players-on-demand.md),
  [Linux media agent](../linux-media-agent.md),
  [Windows media agent](../windows-media-agent.md)

## Context

Each media host ran the agent from its own git checkout with `uv`. Updating an
installation meant logging in to every host to pull, sync and restart, and
nothing told the controller which version a host was actually running: the
reported `software_version` came from the capability document fixed at
enrollment. Agent features often require a matching controller (new hello
fields are rejected by older controllers), so hosts drifting out of step with
the controller is a real failure mode.

Constraints:

- Hosts are operator-owned, connect outbound only, and may have no internet
  access. The controller holds no host credentials.
- The agent depends on platform-specific native wheels (PyAV, NumPy,
  cryptography, Pillow) and must run in the logged-in graphical session.
- An update restarts the agent, so the projector goes dark briefly. A broken
  update must not strand a host that the controller can no longer reach.

## Decision

The controller image ships one offline **agent bundle** per supported platform
(`linux_x86_64`, `linux_aarch64`, `windows_amd64`). A bundle is a zip holding a
manifest (version, build label, platform, Python minor version) and a
`site-packages` tree resolved for that platform from `uv.lock` with
`uv pip install --python-platform --target`. Environment markers are evaluated
for the target, so Windows bundles get Windows-only dependencies and Linux
bundles get `jeepney`. Bundles are built by `cueverge-agent-bundle-build` in a
dedicated Docker stage and served from `CUEVERGE_AGENT_BUNDLE_ROOT`. Each
bundle's identity is its SHA-256 digest.

Hosts run a small **launcher** that uses only the standard library and is copied
to the agent home (`~/.local/share/cueverge/agent` or
`%LOCALAPPDATA%\Cueverge\agent`). It runs the current version from
`versions/<digest>/` with the launcher's Python 3.12 and `PYTHONPATH`; hosts need
no pip, virtual environment, compiler or package index. `cueverge-agent-install`
sets this up once per host from the controller's bundle.

An update is started by a global administrator (GUI **Agent software updates**,
`POST /api/gui/v1/devices/agent-updates`, or MCP `update_device_agents` with the
new administrator-only `device_update` scope) and only in maintenance mode with
no scene playing. The controller sends `device.agent.update` to agents that
advertise the `agent_update` media feature. The agent:

1. stops its player, downloads the bundle from
   `/api/device/v2/agent-bundles/<digest>` over its mTLS identity, and verifies
   size and SHA-256;
2. extracts it with path checks, requires the manifest's platform and Python to
   match, and runs an import self-check with the new tree;
3. records it as the launcher's **trial** version, acknowledges `restarting`,
   and exits with code 75.

The launcher starts the trial. Once the new version receives a controller
welcome it writes a confirmation marker and the launcher promotes it, keeping
the previous version for the next rollback. If the trial exits or does not
confirm within 180 seconds, the launcher restores the previous version and
records a `rolled_back` result with the reason. Agents report an
`agent_release` (running version, platform, bundle digest, managed flag, last
update result) in hello and heartbeats, so the controller shows actual versions
and update outcomes rather than enrollment metadata.

The update trusts the enrolled controller: the bundle digest comes over the
authenticated device session and the download over the same mTLS listener that
already delivers show media. The device listener now exposes the verified
client certificate to HTTPS requests as well as WebSockets.

## Consequences

- One controller upgrade carries the matching agent for every host; operators
  update hosts from the GUI or MCP instead of logging in to each one.
- A compromised controller can run code on enrolled media hosts. This extends
  the existing trust in the controller (which already chooses what hosts play)
  and is limited to enrolled hosts that opted in with `cueverge-agent-install`.
  Release signing pinned on hosts remains a possible future hardening.
- The controller image grows by roughly 70 MB per platform.
- Each host still needs a one-time setup (enrollment and
  `cueverge-agent-install`), Python 3.12 via `uv`, and mpv from the operating
  system. mpv and the operating system are not updated.
- Launcher changes take effect at the next service start; a confirmed version
  installs its launcher into the agent home.
- Agents run from a git checkout keep working and report `unmanaged`.
- Agents with this feature send `agent_release`, so update the controller
  before the agents, as with other hello fields.
