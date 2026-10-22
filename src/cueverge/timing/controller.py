from cueverge.protocol.models import PlaybackState
from cueverge.timing.follower import TimeEstimate


class ControllerClock:
    def __init__(self, duration_ms: int, now: float) -> None:
        self.duration_ms = duration_ms
        self._position_ms = 0
        self._anchored_at = now
        self._playing = True
        self._started = False

    def start(self, now: float) -> None:
        if not self._started:
            self._started = True
            self._anchored_at = now

    def pause(self, now: float) -> None:
        self._position_ms = self.estimate(now).position_ms
        self._anchored_at = now
        self._playing = False

    def resume(self, now: float) -> None:
        if not self._playing:
            self._anchored_at = now
            self._playing = True

    def seek(self, position_ms: int, now: float) -> None:
        if position_ms < 0:
            raise ValueError("position_ms must be non-negative")
        self._position_ms = min(position_ms, self.duration_ms)
        self._anchored_at = now

    def estimate(self, now: float) -> TimeEstimate:
        playing = self._started and self._playing
        elapsed_ms = max(0, round((now - self._anchored_at) * 1000)) if playing else 0
        position_ms = min(self._position_ms + elapsed_ms, self.duration_ms)
        state = PlaybackState.PLAYING if playing else PlaybackState.PAUSED
        if position_ms == self.duration_ms:
            state = PlaybackState.COMPLETED
        return TimeEstimate(position_ms, state, 0, False, None)
