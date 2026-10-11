import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cueverge.config import (
    ControllerConfig,
    DdpOutputConfig,
    FrigateMqttConfig,
    MediaAgentConfig,
    OutputDeploymentConfig,
)
from cueverge.controller.app import _configured_components
from cueverge.lighting.ddp import DdpUdpSink
from cueverge.lighting.sink import FrameSinkRouter


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


def test_controller_reads_private_multi_output_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_config = tmp_path / "outputs.json"
    output_config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "lighting": [
                    {
                        "resource": "roofline",
                        "kind": "ddp",
                        "host": "roofline-wled.local",
                        "expected_pixel_count": 605,
                    },
                    {
                        "resource": "porch",
                        "kind": "ddp",
                        "host": "porch-wled.local",
                        "port": 4049,
                        "expected_pixel_count": 150,
                        "brightness_limit": 0.6,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CUEVERGE_OUTPUT_CONFIG", str(output_config))

    config = ControllerConfig.from_environment()

    assert config.output_config_file == output_config
    assert config.output_deployment is not None
    assert [output.resource for output in config.output_deployment.lighting] == [
        "roofline",
        "porch",
    ]
    assert config.output_deployment.lighting[1].port == 4049
    assert config.output_deployment.lighting[1].brightness_limit == 0.6


def test_output_configuration_rejects_duplicate_resources() -> None:
    with pytest.raises(ValidationError, match="resources must be unique"):
        OutputDeploymentConfig.model_validate(
            {
                "schema_version": 1,
                "lighting": [
                    {
                        "resource": "roofline",
                        "host": "first.local",
                        "expected_pixel_count": 8,
                    },
                    {
                        "resource": "roofline",
                        "host": "second.local",
                        "expected_pixel_count": 8,
                    },
                ],
            }
        )


def test_controller_rejects_legacy_and_file_output_configuration_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_config = tmp_path / "outputs.json"
    output_config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "lighting": [
                    {
                        "resource": "roofline",
                        "host": "roofline.local",
                        "expected_pixel_count": 8,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CUEVERGE_OUTPUT_CONFIG", str(output_config))
    monkeypatch.setenv("CUEVERGE_DDP_ENABLED", "true")
    monkeypatch.setenv("CUEVERGE_DDP_HOST", "legacy.local")
    monkeypatch.setenv("CUEVERGE_DDP_PIXEL_COUNT", "8")

    with pytest.raises(ValidationError, match="configure either legacy"):
        ControllerConfig.from_environment()


def test_controller_factory_builds_multi_ddp_router(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_config = tmp_path / "outputs.json"
    output_config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "lighting": [
                    {
                        "resource": "roofline.demo",
                        "host": "127.0.0.1",
                        "expected_pixel_count": 8,
                    },
                    {
                        "resource": "porch.demo",
                        "host": "127.0.0.2",
                        "port": 4049,
                        "expected_pixel_count": 4,
                        "brightness_limit": 0.5,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CUEVERGE_SHOW_PACKAGE", "shows/multi-output-demo")
    monkeypatch.setenv("CUEVERGE_OUTPUT_CONFIG", str(output_config))
    monkeypatch.delenv("CUEVERGE_DDP_ENABLED", raising=False)

    runtime, ingress = _configured_components()

    assert ingress is None
    assert isinstance(runtime.sink, FrameSinkRouter)
    assert set(runtime.sink.sinks) == {"roofline.demo", "porch.demo"}
    roofline = runtime.sink.sinks["roofline.demo"]
    porch = runtime.sink.sinks["porch.demo"]
    assert isinstance(roofline, DdpUdpSink)
    assert roofline.expected_pixel_count == 8
    assert isinstance(porch, DdpUdpSink)
    assert porch.port == 4049
    assert porch.brightness_limit == 0.5
    asyncio.run(runtime.shutdown())
