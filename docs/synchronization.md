# Synchronization

## Media-synchronized scenes

For a playing sample received at controller time `r` with media position `p`
and playback rate `v`, the estimated position at time `t` is:

```text
p + min(max(t - r, 0), stale_after) * v
```

Every media output of a scene starts together: the controller sends `play` only
after each device acknowledges `prepare` (or `advance` for a preloaded change),
and mpv agents acknowledge only once the file is open and its first frame is
decoded. Devices that load files at different speeds therefore start on the
same command instead of the faster one running ahead by the difference. Until
then, LED outputs with an ambient look keep showing it (see
[scene lifecycle](scene-schema.md)), and the scene's own lighting starts with
its media.

mpv closes its audio output for files without audio and on `stop`, and opening
one again can take about a second on Windows. Once an mpv agent has played
sound it therefore keeps its audio output open: files without an audio track
play with a silent 48 kHz WAV of the same length (written once to the system
temp folder under `cueverge-silence`), `stop` parks the player on a silent WAV
in its black window instead of unloading, and every file uses gapless audio so
mpv reuses the output from one file to the next. A device that never plays
sound never opens an audio output.

The estimate is also bounded by the media duration when known. Paused,
completed, errored, and disconnected sessions do not extrapolate. Once sample
age exceeds `stale_after`, the estimate freezes at the bound and status becomes
degraded.

This is a software clock estimate, not a claim about visible projector frames
or audible output. HDMI, projector, speaker DSP, WLED, and network latency
remain unmeasured. M2 must introduce calibrated display offsets and record
actual jitter before stating synchronization tolerances.

## Controller-timed scenes

Schema-v2 `controller_timed` scenes explicitly use `clock_source: "controller"`.
The controller computes scene milliseconds from its local monotonic clock
without stale-telemetry limits, holds position while paused, and reanchors on
seek/resume. Completion occurs at `duration_ms`, not on a media-completed sample.
The clock starts immediately for LED-only scenes (`media: []`), or when the
prepare barrier opens for scenes with media outputs. Per-output media startup
delays do not delay the scene clock.

Media outputs in this mode receive transport commands but do not discipline
scene time. Their reported positions are used for drift diagnostics only;
there is no ongoing drift correction or physical synchronization guarantee.
Use media-synchronized mode for lighting that must follow buffering or actual
playback. Agent loss never automatically changes clock authority.

Independently of scene mode, mpv media agents hold media with `end_behavior:
loop` or a `repeat_count` above 1 to their own wall clock. mpv loses some time
every time such media wraps to its start, which otherwise adds up to a steady
drift (several milliseconds per second for short loops). The agent anchors
playback on its first position sample after play, resume, seek or a file
change, smooths the error (positions arrive in whole video frames), then nudges
mpv's `speed` in 0.5% steps by up to ±4% once the error passes 30 ms, returns to
normal speed under 10 ms, and seeks when it exceeds 500 ms. Its reported
`playback_rate` shows the adjusted speed.

A loop ends on the file's last whole video frame. Some mpv/FFmpeg builds ignore
the MP4 edit list that trims AAC encoder priming, so they report the file about
21 ms longer than its video; looping on that length made every loop late by the
difference (about 10 ms/s on the 2 s calibration clip). The agent reports the
frame-aligned loop length as `duration_ms`, which the controller also uses to
count loops of looping clock media.
Single-play media is not adjusted, and outputs are never corrected against each
other.
