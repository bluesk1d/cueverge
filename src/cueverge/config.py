import os
from pathlib import Path

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, model_validator


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MediaAgentConfig(RuntimeConfig):
    agent_id: str = Field(min_length=1)
    controller_url: AnyUrl
    asset_root: Path
    mpv_executable: Path
    mpv_pipe: str = r"\\.\pipe\cueverge-mpv"
    telemetry_hz: float = Field(default=15.0, ge=1.0, le=60.0)
    reconnect_delay_ms: int = Field(default=1000, ge=100)
    fullscreen_screen: int | None = Field(default=None, ge=0)
    audio_device: str | None = None
    audio_delay_ms: int = Field(default=0, ge=-5000, le=5000)

    @model_validator(mode="after")
    def validate_controller_url(self) -> "MediaAgentConfig":
        if self.controller_url.scheme not in {"ws", "wss"}:
            raise ValueError("controller_url must use ws or wss")
        return self

    @classmethod
    def from_environment(cls) -> "MediaAgentConfig":
        return cls.model_validate(
            {
                "agent_id": _required_env("CUEVERGE_AGENT_ID"),
                "controller_url": _required_env("CUEVERGE_CONTROLLER_URL"),
                "asset_root": _required_env("CUEVERGE_ASSET_ROOT"),
                "mpv_executable": _required_env("CUEVERGE_MPV_EXECUTABLE"),
                "mpv_pipe": os.environ.get(
                    "CUEVERGE_MPV_PIPE",
                    r"\\.\pipe\cueverge-mpv",
                ),
                "fullscreen_screen": _optional_int(os.environ.get("CUEVERGE_FULLSCREEN_SCREEN")),
                "audio_device": os.environ.get("CUEVERGE_AUDIO_DEVICE"),
                "audio_delay_ms": int(os.environ.get("CUEVERGE_AUDIO_DELAY_MS", "0")),
            }
        )


class DdpOutputConfig(RuntimeConfig):
    enabled: bool = False
    host: str | None = None
    port: int = Field(default=4048, ge=1, le=65535)
    destination_id: int = Field(default=1, ge=1, le=255)
    max_payload_bytes: int = Field(default=1440, ge=3, le=65535)
    expected_pixel_count: int | None = Field(default=None, gt=0)
    brightness_limit: float = Field(default=1.0, gt=0, le=1)

    @model_validator(mode="after")
    def validate_enabled_host(self) -> "DdpOutputConfig":
        if self.enabled and not self.host:
            raise ValueError("enabled DDP output requires a host")
        if self.enabled and self.expected_pixel_count is None:
            raise ValueError("enabled DDP output requires expected_pixel_count")
        return self


class ControllerConfig(RuntimeConfig):
    show_package: Path = Path("shows/demo")
    ddp: DdpOutputConfig = DdpOutputConfig()
    lighting_delay_ms: int = Field(default=0, ge=0, le=5000)

    @classmethod
    def from_environment(cls) -> "ControllerConfig":
        enabled = os.environ.get("CUEVERGE_DDP_ENABLED", "").lower() in {"1", "true", "yes"}
        return cls(
            show_package=Path(os.environ.get("CUEVERGE_SHOW_PACKAGE", "shows/demo")),
            lighting_delay_ms=int(os.environ.get("CUEVERGE_LIGHTING_DELAY_MS", "0")),
            ddp=DdpOutputConfig(
                enabled=enabled,
                host=os.environ.get("CUEVERGE_DDP_HOST"),
                port=int(os.environ.get("CUEVERGE_DDP_PORT", "4048")),
                expected_pixel_count=_optional_int(os.environ.get("CUEVERGE_DDP_PIXEL_COUNT")),
                brightness_limit=float(os.environ.get("CUEVERGE_DDP_BRIGHTNESS_LIMIT", "1.0")),
            ),
        )


def _optional_int(value: str | None) -> int | None:
    return int(value) if value is not None else None


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"missing media agent environment setting: {name}")
    return value
