from pathlib import Path

import pytest
from pydantic import ValidationError

from cueverge.config import (
    ControllerConfig,
    DdpOutputConfig,
    FrigateMqttConfig,
    MediaAgentConfig,
)


def test_hardware_output_is_disabled_by_default() -> None:
    config = ControllerConfig()

    assert not config.ddp.enabled
    assert not config.frigate_mqtt.enabled
    assert config.ddp.host is None
    assert config.ddp.brightness_limit == 1.0
    assert config.lighting_delay_ms == 0
    assert config.show_package == Path("shows/demo")


def test_enabled_ddp_requires_explicit_host_and_pixel_count() -> None:
    with pytest.raises(ValidationError, match="requires a host"):
        DdpOutputConfig(enabled=True)
    with pytest.raises(ValidationError, match="expected_pixel_count"):
        DdpOutputConfig(enabled=True, host="wled.local")


def test_enabled_frigate_mqtt_requires_connection_settings() -> None:
    with pytest.raises(ValidationError, match="requires a host"):
        FrigateMqttConfig(enabled=True)
    with pytest.raises(ValidationError, match="requires a username"):
        FrigateMqttConfig(password="secret")


def test_controller_reads_frigate_mqtt_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUEVERGE_FRIGATE_MQTT_ENABLED", "true")
    monkeypatch.setenv("CUEVERGE_FRIGATE_MQTT_HOST", "mqtt.local")
    monkeypatch.setenv("CUEVERGE_FRIGATE_MQTT_USERNAME", "cueverge")
    monkeypatch.setenv("CUEVERGE_FRIGATE_MQTT_PASSWORD", "private")
    monkeypatch.setenv("CUEVERGE_FRIGATE_ACCEPTED_LABELS", "person, car")

    config = ControllerConfig.from_environment().frigate_mqtt

    assert config.enabled
    assert config.host == "mqtt.local"
    assert config.username == "cueverge"
    assert config.password is not None
    assert config.password.get_secret_value() == "private"
    assert config.accepted_labels == ("person", "car")
    assert "private" not in repr(config)


def test_media_agent_requires_websocket_controller_url() -> None:
    with pytest.raises(ValidationError, match="ws or wss"):
        MediaAgentConfig(
            agent_id="agent",
            controller_url="http://controller/api/v1/agent/ws",
            asset_root=Path("assets"),
            mpv_executable=Path("mpv.exe"),
        )
