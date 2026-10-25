# Windows 11 media agent

On Windows, the media-agent adapter uses mpv JSON IPC through a named pipe and
initiates an outbound WebSocket connection to the controller.

## Prerequisites

1. Install a current mpv build and record its version/source.
2. Place show media in a local asset directory. Agent commands carry only
   package-relative asset IDs; traversal outside this root is rejected.
3. Run `mpv --audio-device=help` and choose the wired analog output if mpv does
   not select it by default.
4. Determine the projector's Windows display index and validate fullscreen
   placement.
5. Confirm TCP connectivity from laptop to controller.

Copy `examples/media-agent.env.example` into a private environment file and set
real values. Do not commit device names, addresses, or credentials.

```powershell
uv sync --all-groups --python 3.12
uv run cueverge-media-agent
```

The adapter launches mpv with idle, forced-window, fullscreen, and named-pipe
options. It prepares media paused, acknowledges commands, reports actual mpv
properties at the configured telemetry rate, sends heartbeats, and fences
commands by controller epoch and agent session.

Named-pipe commands use a serialized write-then-read request/response loop.
This avoids concurrent operations on a synchronous Windows pipe handle, which
can deadlock when a blocking read begins before the corresponding write.
Intervening unsolicited mpv events are queued while awaiting the response ID.

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

## 2026-10-08 hardware checks

The target Windows 11 laptop was checked with portable mpv 0.41.0:

- `C:\Program Files\mpv\mpv.exe` launches successfully.
- `--fs-screen=1` places fullscreen mpv on the extended projector only.
- The wired Home Max AUX path is audible through the Realtek **Headphones**
  WASAPI endpoint.
- The headphones endpoint appears only after the 3.5 mm cable is connected.
  Device enumeration and agent startup must therefore happen after cabling, or
  readiness must fail with an actionable device error.

The machine-specific WASAPI GUID is intentionally not committed. It belongs in
the laptop's private environment configuration.

`CUEVERGE_AUDIO_DELAY_MS` optionally shifts audio relative to video through
mpv's audio-delay setting. Positive values delay audio; negative values advance
it. This is an installation calibration value and defaults to zero.

The agent is deployed as a Task Scheduler job configured for the logged-in
user's interactive desktop. A process launched directly by OpenSSH runs in a
non-interactive Windows session and cannot reliably create the projector GPU
swapchain. Remote administration therefore starts and stops the scheduled task
rather than hosting the media process in the SSH session.

On a multi-purpose laptop, register this task without boot or logon triggers.
Start it only while preparing for a show and stop it after the show:

```powershell
C:\Cueverge\start-agent.ps1
C:\Cueverge\stop-agent.ps1
```

A dedicated appliance may use an automatic interactive-logon trigger, but that
is an installation-specific choice and must not be assumed by the generic
agent.

Agent startup probes the configured Cueverge IPC pipe before launching mpv and
adopts an existing responsive player rather than spawning another instance.
The on-demand stop script terminates only mpv processes whose command line
uses Cueverge's named pipe.

Rotate the laptop credential while its agent task is stopped, then use
`C:\Cueverge\start-agent.ps1` to activate the replacement certificate:

```powershell
cueverge-device-rotate `
  --transport-url wss://controller-host:7443/api/device/v2/agent/ws
```

Live validation covered prepare/play, authoritative position telemetry, pause,
seek while paused, resume, explicit stop, and controller restart
reconciliation. The integrated lighting loop maintained 25 FPS without missed
local deadlines and returned to an idle state after stop.

The controller's `CUEVERGE_LIGHTING_DELAY_MS` setting independently delays
rendered lighting relative to authoritative media time without changing status
timecode. It defaults to zero and must be based on measured installation
latency rather than assumed frame accuracy.

## Important timing limitation

mpv `time-pos/full` is playback time, not proof that a frame is visible on the
projector or sound has emerged from the speaker. Hardware calibration remains
required.
