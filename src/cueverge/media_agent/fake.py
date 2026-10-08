from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from cueverge.protocol.models import (
    AgentHeartbeat,
    AgentHello,
    AgentWelcome,
    CommandAck,
    Discontinuity,
    MediaPosition,
    PlaybackSnapshot,
    PlaybackState,
)


@dataclass
class FakeMediaAgent:
    agent_id: str = "fake-media-1"
    agent_session_id: str = ""
    controller_epoch: str | None = None
    sequence: int = 0
    heartbeat_sequence: int = 0
    scene_instance_id: str | None = None
    playback_session_id: str | None = None

    def __post_init__(self) -> None:
        if not self.agent_session_id:
            self.agent_session_id = str(uuid4())

    def hello(self, playback: PlaybackSnapshot | None = None) -> AgentHello:
        return AgentHello(
            agent_id=self.agent_id,
            agent_session_id=self.agent_session_id,
            playback=playback,
        )

    def accept_welcome(self, welcome: AgentWelcome) -> None:
        self.controller_epoch = welcome.controller_epoch

    def attach(self, scene_instance_id: str, playback_session_id: str) -> None:
        self.scene_instance_id = scene_instance_id
        self.playback_session_id = playback_session_id
        self.sequence = 0

    def reconnect(self) -> None:
        self.agent_session_id = str(uuid4())
        self.heartbeat_sequence = 0

    def snapshot(
        self,
        position_ms: int,
        *,
        state: PlaybackState = PlaybackState.PLAYING,
        duration_ms: int = 4000,
    ) -> PlaybackSnapshot:
        if (
            self.controller_epoch is None
            or self.scene_instance_id is None
            or self.playback_session_id is None
        ):
            raise RuntimeError("fake agent has no active controller playback session")
        return PlaybackSnapshot(
            controller_epoch=self.controller_epoch,
            scene_instance_id=self.scene_instance_id,
            playback_session_id=self.playback_session_id,
            sequence=self.sequence,
            position_ms=position_ms,
            state=state,
            duration_ms=duration_ms,
            agent_monotonic_ms=position_ms,
        )

    def heartbeat(self, agent_monotonic_ms: int = 0) -> AgentHeartbeat:
        if self.controller_epoch is None:
            raise RuntimeError("fake agent has not accepted a controller welcome")
        self.heartbeat_sequence += 1
        return AgentHeartbeat(
            agent_id=self.agent_id,
            agent_session_id=self.agent_session_id,
            controller_epoch=self.controller_epoch,
            sequence=self.heartbeat_sequence,
            agent_monotonic_ms=agent_monotonic_ms,
        )

    def acknowledge(
        self,
        request_id: str,
        *,
        status: Literal["completed", "error"] = "completed",
        error_code: str | None = None,
        detail: str | None = None,
    ) -> CommandAck:
        if self.controller_epoch is None:
            raise RuntimeError("fake agent has not accepted a controller welcome")
        return CommandAck(
            request_id=request_id,
            agent_id=self.agent_id,
            agent_session_id=self.agent_session_id,
            controller_epoch=self.controller_epoch,
            status=status,
            error_code=error_code,
            detail=detail,
        )

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
        if (
            self.controller_epoch is None
            or self.scene_instance_id is None
            or self.playback_session_id is None
        ):
            raise RuntimeError("fake agent must be attached to a playback session")
        self.sequence += 1
        return MediaPosition(
            agent_id=self.agent_id,
            agent_session_id=self.agent_session_id,
            controller_epoch=self.controller_epoch,
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
