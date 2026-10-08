from dataclasses import dataclass

from cueverge.protocol.models import MediaPosition, PlaybackState


@dataclass(frozen=True)
class TelemetryDecision:
    accepted: bool
    reason: str | None = None


@dataclass(frozen=True)
class TimeEstimate:
    position_ms: int
    state: PlaybackState
    sample_age_ms: int
    degraded: bool
    reason: str | None


@dataclass(frozen=True)
class _Anchor:
    telemetry: MediaPosition
    received_at: float


class TimecodeFollower:
    def __init__(self, stale_after_ms: int = 500) -> None:
        if stale_after_ms <= 0:
            raise ValueError("stale_after_ms must be positive")
        self.stale_after_ms = stale_after_ms
        self._scene_instance_id: str | None = None
        self._playback_session_id: str | None = None
        self._anchor: _Anchor | None = None
        self._disconnected = False

    def begin(self, scene_instance_id: str, playback_session_id: str) -> None:
        self._scene_instance_id = scene_instance_id
        self._playback_session_id = playback_session_id
        self._anchor = None
        self._disconnected = False

    def ingest(self, telemetry: MediaPosition, received_at: float) -> TelemetryDecision:
        if (
            telemetry.scene_instance_id != self._scene_instance_id
            or telemetry.playback_session_id != self._playback_session_id
        ):
            return TelemetryDecision(False, "session_fence")
        if self._anchor is not None and telemetry.sequence <= self._anchor.telemetry.sequence:
            return TelemetryDecision(False, "out_of_order")
        self._anchor = _Anchor(telemetry, received_at)
        self._disconnected = False
        return TelemetryDecision(True)

    def disconnect(self) -> None:
        self._disconnected = True

    def estimate(self, now: float) -> TimeEstimate | None:
        if self._anchor is None:
            return None

        telemetry = self._anchor.telemetry
        age_ms = max(0, round((now - self._anchor.received_at) * 1000))
        stale = age_ms > self.stale_after_ms
        can_advance = telemetry.state is PlaybackState.PLAYING and not self._disconnected
        elapsed_ms = min(age_ms, self.stale_after_ms) if can_advance else 0
        position_ms = telemetry.position_ms + round(elapsed_ms * telemetry.playback_rate)
        if telemetry.duration_ms is not None:
            position_ms = min(position_ms, telemetry.duration_ms)

        reason = (
            "agent_disconnected" if self._disconnected else "telemetry_stale" if stale else None
        )
        return TimeEstimate(
            position_ms=position_ms,
            state=telemetry.state,
            sample_age_ms=age_ms,
            degraded=reason is not None,
            reason=reason,
        )
