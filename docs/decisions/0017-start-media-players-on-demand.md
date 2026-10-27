# ADR 0017: Start media players on demand over the device connection

- Status: Proposed
- Date: 2026-10-26
- Related: [mTLS device identities](0015-use-mtls-device-identities.md),
  [session fencing](0004-session-fencing-and-reconciliation.md),
  [Linux media agent](../linux-media-agent.md),
  [Windows media agent](../windows-media-agent.md)

## Context

A media host can be online while its Cueverge agent is not running. Operators
then have to log in to the host and start the service or scheduled task by
hand. The controller can't help: agents connect outbound to the controller over
mTLS, and the controller holds no host credentials and has no inbound access to
media hosts. The **On demand** connection policy is only an expectation label;
it doesn't start anything.

Today one process does everything. `cueverge-media-agent` starts mpv
fullscreen on the projector first, then opens the authenticated device
connection (`DevicePresenceClient`). So "connected" also means "a fullscreen
player owns the projector", which is why multi-purpose hosts (for example a
Windows laptop) stop the whole agent between shows.

Platform constraints:

- The player has to run in the logged-in user's graphical session. A Windows
  process started from an OpenSSH session cannot reliably create the projector
  GPU swapchain, and on Linux mpv needs the session's Wayland/X11 environment.
- Hosts are operator-owned and deliberately keep no controller-reachable
  remote-execution credentials.

Options considered:

1. **Controller-driven SSH** (`systemctl --user start`, `schtasks /run`). It's
   quick, but it would put a private key for every host on the controller and
   need inbound access to each host. Commands differ per OS, and it adds a
   second trust path outside device enrollment. Rejected as the default; it
   could be an operator-only extra later.
2. **Wake-on-LAN.** Helps only when the host is asleep or off. It complements
   this decision but doesn't replace it.
3. **Always-connected agent with an on-demand player.** The chosen option.

## Decision

Split the media agent into a lightweight, always-running **device presence**
component and a **player** that it starts and stops on controller request.

- The presence component starts with the desktop session (systemd user service
  plus XDG autostart on Linux; a logon-triggered interactive scheduled task on
  Windows). It keeps the existing mTLS connection, heartbeats, timing, artifact
  cache, and credential rotation, and it doesn't open a window.
- The player is a separate, supervised child process: mpv plus its IPC client.
  The presence component starts it in its own session, so the graphical
  environment is already correct.
- Protocol v2 gains `device.player.start` and `device.player.stop` requests with
  acknowledgements. Each is fenced by device session and controller epoch like
  other commands. The device advertises a `player_control` capability, and the
  heartbeat reports the player state: `stopped`, `starting`, `ready`, `stopping`,
  or `failed` with a reason.
- Only `ready` players register as runtime media targets. Stopping the player
  unregisters the target and abandons pending commands, using the same path as
  a disconnect. Reconciliation rules are unchanged.
- Connection policy gains its operational meaning:
  - **Continuous:** the controller keeps the player running and restarts it after
    failures, with backoff.
  - **On demand:** the player is started for Apply, Start show, or an explicit
    operator request, and stopped after a configurable idle time once no loaded
    scene uses it.
- Start and stop are available to operators through a **Start player** / **Stop
  player** action on the device card and as MCP operator tools. Both use the
  existing role checks, CSRF protection, revision and arming rules, and audit
  logging. Device Health shows player state separately from connection state:
  "Connected · player stopped" is not "ready".
- Presence-only connections stay authorized even when the loaded show doesn't
  use the device. The idle-connection behavior already provides this.

## Consequences

- Operators can bring projectors up from the controller UI or MCP without host
  logins, as long as the host is on and its desktop session is running. Keeping
  the session logged in remains a host setup step (automatic login on a
  dedicated appliance; an interactive logon task on a shared laptop).
- No new credentials, inbound ports, or trust paths. All control rides the
  enrolled mTLS identity, and suspension or revocation still ends control on
  the next message.
- The projector shows the desktop until a player starts, and a held black
  frame is no longer the default idle state. The site procedure must say
  whether idle projectors are powered down or show a blank desktop.
- Old agents that don't advertise `player_control` keep today's behavior: the
  player always runs and connection implies ready. The controller has to
  handle both until those agents are upgraded.
- The work needs protocol models, an agent process split with supervision,
  Windows and Linux startup changes, controller policy and state tracking,
  UI/MCP actions with tests, and documentation updates.

## Interim measure

Until this is built, the Linux service template uses `Restart=always` with no
start limit, so the combined agent comes back after any exit while the desktop
session runs. An explicit `systemctl --user stop` is still honored.
