from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION: Literal[1] = 1


class ProtocolMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlaybackState(StrEnum):
    PLAYING = "playing"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"


class Discontinuity(StrEnum):
    SEEK = "seek"
    LOOP = "loop"


class PlaybackSnapshot(ProtocolMessage):
    controller_epoch: str = Field(min_length=1, max_length=128)
    scene_instance_id: str = Field(min_length=1, max_length=128)
    playback_session_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    position_ms: int = Field(ge=0)
    playback_rate: float = Field(default=1.0, ge=0.1, le=4.0)
    state: PlaybackState
    duration_ms: int | None = Field(default=None, gt=0)
    agent_monotonic_ms: int = Field(ge=0)


class AgentHello(ProtocolMessage):
    type: Literal["agent.hello"] = "agent.hello"
    protocol_version: Literal[1] = PROTOCOL_VERSION
    agent_id: str = Field(min_length=1, max_length=128)
    agent_session_id: str = Field(min_length=1, max_length=128)
    capabilities: tuple[str, ...] = ("media.playback",)
    playback: PlaybackSnapshot | None = None


class AgentWelcome(ProtocolMessage):
    type: Literal["agent.welcome"] = "agent.welcome"
    protocol_version: Literal[1] = PROTOCOL_VERSION
    controller_epoch: str
    heartbeat_interval_ms: int = Field(gt=0)
    heartbeat_timeout_ms: int = Field(gt=0)
    reconciliation: Literal["idle", "continue", "stop"]


class MediaPosition(ProtocolMessage):
    type: Literal["media.position"] = "media.position"
    protocol_version: Literal[1] = PROTOCOL_VERSION
    agent_id: str = Field(min_length=1, max_length=128)
    agent_session_id: str = Field(min_length=1, max_length=128)
    controller_epoch: str = Field(min_length=1, max_length=128)
    scene_instance_id: str = Field(min_length=1, max_length=128)
    playback_session_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    position_ms: int = Field(ge=0)
    playback_rate: float = Field(default=1.0, ge=0.1, le=4.0)
    state: PlaybackState
    duration_ms: int | None = Field(default=None, gt=0)
    agent_monotonic_ms: int = Field(ge=0)
    discontinuity: Discontinuity | None = None


class AgentCommand(ProtocolMessage):
    type: Literal["agent.command"] = "agent.command"
    protocol_version: Literal[1] = PROTOCOL_VERSION
    request_id: str
    command: Literal["prepare", "play", "pause", "resume", "seek", "stop", "get_status"]
    agent_id: str
    agent_session_id: str
    controller_epoch: str
    scene_id: str
    scene_instance_id: str
    playback_session_id: str
    asset_id: str | None = None
    end_behavior: Literal["stop", "hold_last_frame", "loop"] | None = None
    target_position_ms: int | None = Field(default=None, ge=0)


class CommandAck(ProtocolMessage):
    type: Literal["command.ack"] = "command.ack"
    protocol_version: Literal[1] = PROTOCOL_VERSION
    request_id: str
    agent_id: str
    agent_session_id: str
    controller_epoch: str
    status: Literal["completed", "error"]
    error_code: str | None = None
    detail: str | None = None


class AgentHeartbeat(ProtocolMessage):
    type: Literal["agent.heartbeat"] = "agent.heartbeat"
    protocol_version: Literal[1] = PROTOCOL_VERSION
    agent_id: str
    agent_session_id: str
    controller_epoch: str
    sequence: int = Field(ge=0)
    agent_monotonic_ms: int = Field(ge=0)


class HeartbeatAck(ProtocolMessage):
    type: Literal["agent.heartbeat.ack"] = "agent.heartbeat.ack"
    protocol_version: Literal[1] = PROTOCOL_VERSION
    sequence: int
    accepted: bool
    reason: str | None = None


class TelemetryAck(ProtocolMessage):
    type: Literal["telemetry.ack"] = "telemetry.ack"
    protocol_version: Literal[1] = PROTOCOL_VERSION
    sequence: int
    accepted: bool
    reason: str | None = None
