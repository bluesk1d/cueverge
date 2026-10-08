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

## Important timing limitation

mpv `time-pos/full` is playback time, not proof that a frame is visible on the
projector or sound has emerged from the speaker. Hardware calibration remains
required.
