# Windows 11 media agent

On Windows, the media-agent adapter uses mpv JSON IPC through a named pipe and
initiates an outbound WebSocket connection to the controller.

## Prerequisites

1. Install a current mpv build and record its version/source. Official mpv
   Windows builds are unsigned, so **Smart App Control** (and other app control
   policies) blocks them. Check **Windows Security > App & browser control**.
   If it's on, either install a Microsoft Store mpv package, which the Store
   signs (for example the community-maintained "mpv (Unofficial)",
   `winget install --id 9P3JFR0CLLL6 --source msstore`), and set
   `CUEVERGE_MPV_EXECUTABLE` to its `%LOCALAPPDATA%\Microsoft\WindowsApps\mpv.exe`
   alias, or turn Smart App Control off, which can't be undone without
   reinstalling Windows. Store packages may lag official mpv releases and come
   from their packager, not the mpv project. The agent reports these blocks as
   "Windows Smart App Control or an app control policy blocked mpv".
2. Place show media in a local asset directory. Agent commands carry only
   package-relative asset IDs; traversal outside this root is rejected.
3. Run `mpv --audio-device=help` and choose the intended audio output if mpv does
   not select it by default.
4. Determine the projector's Windows display index and validate fullscreen
   placement.
5. Confirm TCP connectivity from media host to controller.

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

## Hardware qualification and deployment

Record the Windows, GPU-driver, and mpv versions used for your installation,
then verify:

- The configured mpv executable launches from the show account.
- Fullscreen output reaches the intended display. For example, `--fs-screen=1`
  selects display index 1; discover the correct index on your own host.
- The selected audio endpoint is audible through the intended speakers.
- Device enumeration works with the installation fully connected. Some analog
  endpoints appear only after cabling, and HDMI endpoints can depend on display
  power state. Report unavailable devices rather than silently selecting another.

Keep machine-specific audio endpoint identifiers in the media host's private
environment configuration. No speaker brand or particular audio route is required.

`CUEVERGE_AUDIO_DELAY_MS` optionally shifts audio relative to video through
mpv's audio-delay setting. Positive values delay audio; negative values advance
it. This is an installation calibration value and defaults to zero.

For an interactive Windows deployment, use a Task Scheduler job for the logged-in
user's interactive desktop. A process launched directly by OpenSSH runs in a
non-interactive Windows session and cannot reliably create the projector GPU
swapchain. Remote administration therefore starts and stops the scheduled task
rather than hosting the media process in the SSH session.

### On-demand player on a multi-purpose laptop

Set `CUEVERGE_PLAYER_MODE=on_demand` in the agent environment. The agent then
starts at logon without opening mpv, keeps its authenticated controller
connection, and shows up in Device Health as **Player stopped**.
The controller opens the fullscreen player when:

- an operator selects **Start player** on the device card (or uses the MCP
  `control_device_player` tool);
- **Start** in Operate needs a projector that the device is assigned to.

**Stop player** closes it again. When the device's connection policy is **On
demand**, the controller also stops the player after 10 minutes in which no
playing scene uses it. Applying a show that restarts a running scene does not
start stopped players; start them first, or apply while stopped and then use
**Start**.

Register the task with a logon trigger, a hidden window and automatic restart,
for example:

```powershell
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\Cueverge\run-agent.ps1'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'Cueverge Media Agent' -Action $action `
    -Trigger $trigger -Principal $principal -Settings $settings -Force
```

`C:\Cueverge\start-agent.ps1` and `stop-agent.ps1` still start and stop the
whole agent, for example around maintenance.

Without on-demand mode, the agent opens the player as soon as it starts. On a
laptop used for other things, register that task without triggers and start it
only while preparing for a show. A dedicated appliance may use an automatic
interactive-logon trigger with either mode; that is an installation-specific
choice, not a default of the generic agent.

Agent startup probes the configured Cueverge IPC pipe before launching mpv and
adopts an existing responsive player rather than spawning another instance.
Stopping the player quits it over that pipe, including an adopted one. The stop
script terminates only mpv processes whose command line uses Cueverge's named
pipe.

Rotate the media-host credential while its agent task is stopped, then use
`C:\Cueverge\start-agent.ps1` to activate the replacement certificate:

```powershell
cueverge-device-rotate `
  --transport-url wss://controller-host:7443/api/device/v2/agent/ws
```

Before show use, verify prepare/play, authoritative position telemetry, pause,
seek while paused, resume, explicit stop, and controller restart reconciliation.
Record lighting cadence and missed deadlines during representative playback, and
confirm that stop returns the installation to its intended safe state.

The controller's `CUEVERGE_LIGHTING_DELAY_MS` setting independently delays
rendered lighting relative to authoritative media time without changing status
timecode. It defaults to zero and must be based on measured installation
latency rather than assumed frame accuracy.

## Important timing limitation

mpv `time-pos/full` is playback time, not proof that a frame is visible on the
projector or sound has emerged from the speaker. Hardware calibration remains
required.
