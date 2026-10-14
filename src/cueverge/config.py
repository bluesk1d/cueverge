import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, SecretStr, model_validator


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MediaAgentConfig(RuntimeConfig):
    asset_root: Path
    mpv_executable: Path
    mpv_pipe: str = Field(default_factory=lambda: _default_mpv_pipe())
    telemetry_hz: float = Field(default=15.0, ge=1.0, le=60.0)
    reconnect_delay_ms: int = Field(default=1000, ge=100)
    fullscreen_screen: int | None = Field(default=None, ge=0)
    audio_device: str | None = None
    audio_delay_ms: int = Field(default=0, ge=-5000, le=5000)
    device_transport_url: AnyUrl | None = None
    device_credential_root: Path | None = None

    @model_validator(mode="after")
    def validate_device_transport(self) -> "MediaAgentConfig":
        presence_values = (self.device_transport_url, self.device_credential_root)
        if sum(value is not None for value in presence_values) == 1:
            raise ValueError(
                "device_transport_url and device_credential_root must be configured together"
            )
        if self.device_transport_url is not None and self.device_transport_url.scheme != "wss":
            raise ValueError("device_transport_url must use wss")
        return self

    @classmethod
    def from_environment(cls) -> "MediaAgentConfig":
        return cls.model_validate(
            {
                "asset_root": _required_env("CUEVERGE_ASSET_ROOT"),
                "mpv_executable": _required_env("CUEVERGE_MPV_EXECUTABLE"),
                "mpv_pipe": os.environ.get(
                    "CUEVERGE_MPV_PIPE",
                    _default_mpv_pipe(),
                ),
                "fullscreen_screen": _optional_int(os.environ.get("CUEVERGE_FULLSCREEN_SCREEN")),
                "audio_device": os.environ.get("CUEVERGE_AUDIO_DEVICE"),
                "audio_delay_ms": int(os.environ.get("CUEVERGE_AUDIO_DELAY_MS", "0")),
                "device_transport_url": os.environ.get("CUEVERGE_DEVICE_TRANSPORT_URL"),
                "device_credential_root": os.environ.get("CUEVERGE_DEVICE_CREDENTIAL_ROOT"),
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


class DdpResourceConfig(RuntimeConfig):
    resource: str = Field(min_length=1)
    kind: Literal["ddp"] = "ddp"
    host: str = Field(min_length=1)
    port: int = Field(default=4048, ge=1, le=65535)
    destination_id: int = Field(default=1, ge=1, le=255)
    max_payload_bytes: int = Field(default=1440, ge=3, le=65535)
    expected_pixel_count: int = Field(gt=0)
    brightness_limit: float = Field(default=1.0, gt=0, le=1)


class OutputDeploymentConfig(RuntimeConfig):
    schema_version: Literal[1]
    lighting: tuple[DdpResourceConfig, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_resources(self) -> "OutputDeploymentConfig":
        resources = [output.resource for output in self.lighting]
        if len(resources) != len(set(resources)):
            raise ValueError("output deployment lighting resources must be unique")
        return self


class FrigateMqttConfig(RuntimeConfig):
    enabled: bool = False
    host: str | None = None
    port: int = Field(default=1883, ge=1, le=65535)
    topic: str = Field(default="frigate/events", min_length=1)
    username: str | None = None
    password: SecretStr | None = None
    reconnect_delay_ms: int = Field(default=5000, ge=100)
    accepted_labels: tuple[str, ...] = ("person",)

    @model_validator(mode="after")
    def validate_connection(self) -> "FrigateMqttConfig":
        if self.enabled and not self.host:
            raise ValueError("enabled Frigate MQTT ingress requires a host")
        if self.password is not None and self.username is None:
            raise ValueError("Frigate MQTT password requires a username")
        if not self.accepted_labels or any(not label for label in self.accepted_labels):
            raise ValueError("Frigate MQTT accepted_labels must not be empty")
        return self


class ControllerConfig(RuntimeConfig):
    show_package: Path = Path("shows/demo")
    ddp: DdpOutputConfig = DdpOutputConfig()
    output_deployment: OutputDeploymentConfig | None = None
    output_config_file: Path | None = None
    frigate_mqtt: FrigateMqttConfig = FrigateMqttConfig()
    lighting_delay_ms: int = Field(default=0, ge=0, le=5000)

    @model_validator(mode="after")
    def validate_output_configuration(self) -> "ControllerConfig":
        if self.ddp.enabled and self.output_deployment is not None:
            raise ValueError(
                "configure either legacy CUEVERGE_DDP_* settings or CUEVERGE_OUTPUT_CONFIG"
            )
        return self

    @classmethod
    def from_environment(cls) -> "ControllerConfig":
        ddp_enabled = _env_enabled("CUEVERGE_DDP_ENABLED")
        frigate_enabled = _env_enabled("CUEVERGE_FRIGATE_MQTT_ENABLED")
        output_config_value = os.environ.get("CUEVERGE_OUTPUT_CONFIG")
        output_config_file = Path(output_config_value) if output_config_value else None
        output_deployment = (
            OutputDeploymentConfig.model_validate_json(
                output_config_file.read_text(encoding="utf-8")
            )
            if output_config_file is not None
            else None
        )
        return cls(
            show_package=Path(os.environ.get("CUEVERGE_SHOW_PACKAGE", "shows/demo")),
            lighting_delay_ms=int(os.environ.get("CUEVERGE_LIGHTING_DELAY_MS", "0")),
            output_deployment=output_deployment,
            output_config_file=output_config_file,
            ddp=DdpOutputConfig(
                enabled=ddp_enabled,
                host=os.environ.get("CUEVERGE_DDP_HOST"),
                port=int(os.environ.get("CUEVERGE_DDP_PORT", "4048")),
                expected_pixel_count=_optional_int(os.environ.get("CUEVERGE_DDP_PIXEL_COUNT")),
                brightness_limit=float(os.environ.get("CUEVERGE_DDP_BRIGHTNESS_LIMIT", "1.0")),
            ),
            frigate_mqtt=FrigateMqttConfig(
                enabled=frigate_enabled,
                host=os.environ.get("CUEVERGE_FRIGATE_MQTT_HOST"),
                port=int(os.environ.get("CUEVERGE_FRIGATE_MQTT_PORT", "1883")),
                topic=os.environ.get("CUEVERGE_FRIGATE_MQTT_TOPIC", "frigate/events"),
                username=os.environ.get("CUEVERGE_FRIGATE_MQTT_USERNAME"),
                password=_optional_secret(os.environ.get("CUEVERGE_FRIGATE_MQTT_PASSWORD")),
                reconnect_delay_ms=int(
                    os.environ.get("CUEVERGE_FRIGATE_MQTT_RECONNECT_DELAY_MS", "5000")
                ),
                accepted_labels=_csv_values(
                    os.environ.get("CUEVERGE_FRIGATE_ACCEPTED_LABELS", "person")
                ),
            ),
        )


def _optional_int(value: str | None) -> int | None:
    return int(value) if value is not None else None


def _default_mpv_pipe() -> str:
    return r"\\.\pipe\cueverge-mpv" if sys.platform == "win32" else "/tmp/cueverge-mpv.sock"


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"missing media agent environment setting: {name}")
    return value


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}


def _csv_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _optional_secret(value: str | None) -> SecretStr | None:
    return SecretStr(value) if value is not None else None
