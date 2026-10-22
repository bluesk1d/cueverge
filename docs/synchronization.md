# Synchronization

## Media-synchronized scenes

For a playing sample received at controller time `r` with media position `p`
and playback rate `v`, the estimated position at time `t` is:

```text
p + min(max(t - r, 0), stale_after) * v
```

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
