from dataclasses import dataclass

from cueverge.protocol.models import (
    AgentHello,
    Discontinuity,
    MediaPosition,
    PlaybackState,
)


@dataclass
class FakeMediaAgent:
    agent_id: str = "fake-media-1"
    sequence: int = 0
    scene_instance_id: str | None = None
    playback_session_id: str | None = None

    def hello(self) -> AgentHello:
        return AgentHello(agent_id=self.agent_id)

    def attach(self, scene_instance_id: str, playback_session_id: str) -> None:
        self.scene_instance_id = scene_instance_id
        self.playback_session_id = playback_session_id
        self.sequence = 0

    def position(
        self,
        position_ms: int,
        *,
        state: PlaybackState = PlaybackState.PLAYING,
        playback_rate: float = 1.0,
        duration_ms: int = 4000,
        agent_monotonic_ms: int | None = None,
        discontinuity: Discontinuity | None = None,
    ) -> MediaPosition:
        if self.scene_instance_id is None or self.playback_session_id is None:
            raise RuntimeError("fake agent must be attached to a playback session")
        self.sequence += 1
        return MediaPosition(
            agent_id=self.agent_id,
            scene_instance_id=self.scene_instance_id,
            playback_session_id=self.playback_session_id,
            sequence=self.sequence,
            position_ms=position_ms,
            playback_rate=playback_rate,
            state=state,
            duration_ms=duration_ms,
            agent_monotonic_ms=agent_monotonic_ms
            if agent_monotonic_ms is not None
            else position_ms,
            discontinuity=discontinuity,
        )
