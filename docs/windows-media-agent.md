# Windows 11 media agent

The media-agent adapter is implemented but has not been run on the target
laptop. It uses mpv JSON IPC through a Windows named pipe and initiates an
outbound WebSocket connection to the controller.

## Prerequisites requiring local validation

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

Windows named-pipe reads and writes use direct duplex Win32 calls through the
Windows-only `pywin32` dependency. A generic Python file wrapper must not be
used because a blocking read can serialize writes and deadlock command IPC.

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

## Important timing limitation

mpv `time-pos/full` is playback time, not proof that a frame is visible on the
projector or sound has emerged from the speaker. Hardware calibration remains
required.
