# Synchronization

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
