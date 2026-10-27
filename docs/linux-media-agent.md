# Linux media agent

The media agent supports mpv JSON IPC through a Unix-domain socket and makes
the same outbound WebSocket connection to the Cueverge controller as the
Windows agent.

## Prerequisites

1. Install Python 3.12 or newer, `uv`, and a current mpv package.
2. Place show media in a local asset directory.
3. Confirm mpv can open fullscreen on the projector from the intended graphical
   login session.
4. Run `mpv --audio-device=help` if the agent also owns an audio output.
5. Confirm TCP connectivity from the media host to the controller.

Copy `examples/media-agent-linux.env.example` into a private environment file.
Do not commit host addresses, device names, or installation paths.

```bash
uv sync --all-groups --python 3.12
uv run cueverge-media-agent
```

The default Linux IPC path is `/tmp/cueverge-mpv.sock`; override it with
`CUEVERGE_MPV_PIPE` when multiple agent processes share a host. The agent
launches mpv with `--input-ipc-server` and connects after mpv creates the Unix
socket.

Run the agent inside the graphical user session that owns the projector.
Before creating an unattended user service, validate the distribution's
Wayland or X11 environment, GPU permissions, display selection, and behavior
after logout. A system-level service without access to the graphical session
may connect to the controller but fail to create a usable fullscreen window.

Linux support is covered by transport and protocol tests but has not yet been
qualified across deployment hardware. Validate your media host and display.
Hardware validation must cover fullscreen placement, decode performance, audio routing, prepare/play/seek/
stop behavior, reconnect reconciliation, and sustained playback.

## Repeat counts and seamless scene changes

The agent advertises the `seamless_transition` and `repeat_count` media
features when it connects. Update the controller before the agents: an older
controller rejects the new hello field. With these features, mpv:

- plays `repeat_count` back to back with `loop-file` and reports one continuous
  position across the plays;
- preloads the next scene's file as a second playlist entry with its own
  per-file options (named `loadfile` arguments, so mpv 0.37 and later behave the
  same) and `prefetch-playlist`, and either continues into it at the end of the
  current file or switches with `playlist-next` on `advance`;
- reports a held final frame (`keep-open`) as completed, so scenes whose clock
  output holds its last frame now finish and continue to their next scene.

When validating hardware, confirm the cut between two scenes on the actual
projector: there should be no black frame between the last frame of one clip
and the first frame of the next. Record the mpv version you validated.

## Ubuntu/Debian user-session deployment

The supplied templates assume:

- the repository is checked out at `~/cueverge`
- `uv` is installed at `~/.local/bin/uv`
- private settings are stored at
  `~/.config/cueverge/media-agent.env`
- the desktop user logs in automatically for unattended show operation

Adjust the templates if any path differs.

```bash
mkdir -p ~/.config/cueverge ~/.config/systemd/user ~/.config/autostart
cp examples/media-agent-linux.env.example \
  ~/.config/cueverge/media-agent.env
cp examples/cueverge-media-agent.service \
  ~/.config/systemd/user/cueverge-media-agent.service
cp examples/cueverge-media-agent.desktop \
  ~/.config/autostart/cueverge-media-agent.desktop
systemctl --user daemon-reload
```

Edit the private environment file, then test from a terminal inside the media host's
desktop session before relying on autostart:

```bash
systemctl --user start cueverge-media-agent.service
systemctl --user status cueverge-media-agent.service
journalctl --user -u cueverge-media-agent.service -f
```

Confirm the user service received the graphical environment:

```bash
systemctl --user show-environment
```

On X11, the environment normally includes `DISPLAY`; on Wayland it normally
includes `WAYLAND_DISPLAY` and `XDG_RUNTIME_DIR`. Distribution and desktop
behavior varies, so absence of the relevant variables is a deployment error,
not a reason to launch mpv as root.

The XDG autostart entry starts the supervised user service after desktop login.
Do not additionally enable the service under `default.target` unless testing
shows that the user manager receives the graphical environment before it
starts; otherwise two startup paths can race or the service can start too
early.

To stop or restart the agent:

```bash
systemctl --user stop cueverge-media-agent.service
systemctl --user restart cueverge-media-agent.service
```

The template uses `Restart=always` with no start limit, so the agent comes back
within a few seconds after any exit, including a clean one, for as long as the
desktop session runs. An explicit `systemctl --user stop` is honored and stays
stopped until the next start or login. Hosts installed from an older template
(`Restart=on-failure`) only restarted after crashes; update the unit, then run
`systemctl --user daemon-reload`.

Before unattended use, reboot the media host and verify:

1. Desktop login completes without interaction.
2. Exactly one agent process connects to the controller.
3. mpv opens fullscreen on the projector rather than the maintenance display.
4. Unplugging and reconnecting the network causes agent reconnection.
5. Restarting the controller produces the expected playback reconciliation.
6. Logging out stops access to the projector; logging back in starts one clean
   agent session.

## Ubuntu Desktop 24.04 GNOME/Wayland target

Install mpv and the basic checkout prerequisites, then install `uv` using its
official installation instructions:

```bash
sudo apt update
sudo apt install git mpv
mpv --version
uv --version
```

Log into GNOME locally and verify that the session is active Wayland rather
than SSH, a text console, or a remote desktop session:

```bash
printf 'session=%s wayland=%s runtime=%s\n' \
  "$XDG_SESSION_TYPE" "$WAYLAND_DISPLAY" "$XDG_RUNTIME_DIR"
loginctl show-session "$XDG_SESSION_ID" -p Active -p Remote -p Type
```

Expected values include `session=wayland`, a non-empty `WAYLAND_DISPLAY`,
`Active=yes`, `Remote=no`, and `Type=wayland`.

Before starting Cueverge, test the exact show media directly:

```bash
mpv --idle=yes --force-window=yes --fullscreen=yes \
  --fs-screen=0 /path/to/test-media.mp4
```

Repeat with the projector attached and adjust `--fs-screen` until fullscreen
placement is reliable after a reboot. Record the validated index as
`CUEVERGE_FULLSCREEN_SCREEN` in the private agent environment file. Do not
assume GNOME preserves an index after changing cable ports, display order, or
mirroring settings.

In GNOME Settings:

1. Use **Join Displays**, not mirror mode, unless mirrored playback is
   intentionally desired.
2. Set the projector's native resolution and refresh rate.
3. Disable display blanking and automatic suspend for the show account.
4. Decide deliberately whether automatic login is acceptable for the physical
   security of the media host.

After the service starts, verify its inherited environment and process tree:

```bash
systemctl --user show-environment |
  grep -E '^(WAYLAND_DISPLAY|XDG_RUNTIME_DIR|DBUS_SESSION_BUS_ADDRESS)='
systemctl --user status cueverge-media-agent.service
journalctl --user -u cueverge-media-agent.service --since today
```

The service is coupled to `graphical-session.target` and the XDG autostart
entry starts it after GNOME login. This avoids starting mpv from a system
service that lacks access to the user's Wayland compositor.

## Media-host hardware checklist

Record results alongside the private installation configuration. Do not commit
local addresses, serial numbers, credentials, or copyrighted test media.

### 1. Inventory before installation

- [ ] Record media host model, CPU, memory, storage, firmware version, and GPU.
- [ ] Record projector model, native resolution, supported refresh rates, and
      the HDMI cable/adapter path.
- [ ] Confirm wired Ethernet availability and record whether the media host must use
      Wi-Fi.
- [ ] Confirm the media host clock, firmware, and Ubuntu installation can recover
      after power loss.
- [ ] Decide whether the show account may use GNOME automatic login.

Useful inventory commands:

```bash
uname -a
lscpu
free -h
lsblk
lspci -nnk | grep -A3 -E 'VGA|3D|Display'
sudo dmidecode -t system
```

### 2. Ubuntu and software baseline

- [ ] Install Ubuntu Desktop 24.04 LTS and all current updates.
- [ ] Install mpv, Git, `uv`, and Cueverge.
- [ ] Confirm `uv sync --all-groups --python 3.12` succeeds.
- [ ] Run the Cueverge test suite before attaching show hardware.
- [ ] Confirm `mpv --version` and record the version and package source.
- [ ] Confirm hardware decoding support with `mpv --hwdec=auto` and the actual
      show codec, resolution, frame rate, and bitrate.
- [ ] Verify sustained playback does not drop frames or thermally throttle.

During direct mpv playback, press `i` to inspect decode and dropped-frame
statistics. Also capture:

```bash
journalctl -k --since today
```

### 3. Projector and GNOME display setup

- [ ] Confirm GNOME reports a local active Wayland session.
- [ ] Select **Join Displays** and set the projector's native mode.
- [ ] Test every plausible `--fs-screen` index with actual show media.
- [ ] Reboot with the projector connected and verify the selected index.
- [ ] Reboot with the projector initially off, then power it on and verify
      recovery behavior.
- [ ] Disconnect and reconnect HDMI and verify whether the display index
      changes.
- [ ] Disable suspend, screen blanking, notifications, and disruptive update
      prompts for the show account.
- [ ] Verify there is no visible desktop, cursor, window decoration, or
      compositor transition during playback.

### 4. Agent configuration and startup

- [ ] Verify the codecs, containers, and output count actually supported by
      this host; copy and edit
      `examples/direct-media-agent-capabilities.json` rather than declaring
      untested capabilities.
- [ ] When the controller administrator has created a single-use pairing
      token, enroll through the trusted HTTPS GUI endpoint:

      ```bash
      cueverge-device-enroll \
        --enrollment-url https://cueverge.example/api/device/v2/enroll \
        --display-name CuevergeClient01 \
        --capabilities ./direct-media-agent-capabilities.json
      ```

      Enter the pairing token at the hidden prompt. The command creates the
      private key locally, verifies the issued certificate identity and
      returned CA chain, and atomically writes credentials under
      `~/.config/cueverge/device-v2/` with restrictive permissions. It refuses
      to       overwrite an existing credential directory.
- [ ] Configure `CUEVERGE_DEVICE_TRANSPORT_URL` and
      `CUEVERGE_DEVICE_CREDENTIAL_ROOT` together to maintain authenticated
      protocol-v2 media session. The transport URL must use `wss://`; this
      authenticated socket carries heartbeat, commands, acknowledgements, and
      playback telemetry.
- [ ] Rotate credentials before expiry, then restart the agent to reload its
      TLS context:

      ```bash
      cueverge-device-rotate \
        --transport-url wss://controller-host:7443/api/device/v2/agent/ws
      systemctl --user restart cueverge-media-agent.service
      ```
- [ ] Copy all media into the local asset root.
- [ ] Verify every v2 scene asset referenced by this agent exists locally.
- [ ] Set the validated fullscreen screen index.
- [ ] Set audio output only if this media host owns an audio destination.
- [ ] Run the agent manually from a GNOME terminal.
- [ ] Verify prepare, play, pause, seek, resume, stop, and completion.
- [ ] Install the user service and XDG autostart templates.
- [ ] Reboot and confirm exactly one media-agent process starts.
- [ ] Confirm repeated agent restarts retain exactly one mpv process; startup
      adopts a responsive player already serving the configured IPC socket.
- [ ] Confirm logs contain no reconnect loop, missing asset, IPC socket, or
      display errors.

### 5. Controller and two-projector integration

- [ ] Add distinct logical projector resources to `installation.json`.
- [ ] Convert one placeholder scene to schema version 2 with both media
      outputs and an explicit required clock source.
- [ ] Confirm both agents appear in `connected_agents`. An agent whose
      projector the loaded show doesn't use stays connected and idle (shown as
      **Connected · idle**) and joins once such a show is applied.
- [ ] Confirm status lists a distinct playback session for each output.
- [ ] Confirm both agents receive `prepare` before either receives `play`.
- [ ] Confirm pause, resume, seek, replacement, blackout, and stop fan out.
- [ ] Confirm disconnecting the follower projector leaves the clock-source
      scene running degraded.
- [ ] Confirm disconnecting the clock source freezes time-based lighting.
- [ ] Confirm controller and individual-agent restarts reconcile or stop stale
      playback safely.

### 6. Synchronization measurement

- [ ] Create a calibration clip with simultaneous frame-accurate visual
      flashes on both projector assets.
- [ ] Record both projections in one high-frame-rate camera view.
- [ ] Measure startup skew over at least 20 starts.
- [ ] Measure drift near the beginning, middle, and end of the longest scene.
- [ ] Record median, worst case, direction, and whether drift accumulates.
- [ ] Compare visible skew with Cueverge's reported per-output `drift_ms`.
- [ ] Repeat after a reboot and after an agent reconnect.
- [ ] Leave correction diagnostic-only until these measurements define an
      acceptable threshold and demonstrate whether seek or rate adjustment is
      visually safer.

### 7. Soak and failure testing

- [ ] Run representative scenes continuously for at least the expected show
      duration.
- [ ] Watch CPU, memory, temperature, dropped frames, disk errors, and agent
      reconnect counts.
- [ ] Interrupt network connectivity to one follower and verify degraded
      continuation.
- [ ] Restart one follower agent and verify clean reconciliation.
- [ ] Restart the controller during playback and verify stale sessions are not
      adopted incorrectly.
- [ ] Power-cycle the projector and document whether manual intervention is
      required.
- [ ] Reboot the media host and confirm unattended return to a connected idle agent.
- [ ] Verify explicit stop and shutdown leave every projector and lighting
      output in the intended installation-safe state.

### 8. Acceptance record

Do not promote the media host to show use until the following are recorded:

- [ ] Validated Ubuntu, kernel, mpv, Cueverge, and GPU driver versions.
- [ ] Validated display index, resolution, refresh rate, and decode mode.
- [ ] Maximum observed dropped frames, startup skew, and long-run drift.
- [ ] Agent and controller restart outcomes.
- [ ] Known HDMI/projector power-order limitations.
- [ ] Final private environment-file location and service status.
- [ ] A rollback plan to a known-good hardware and software configuration.
