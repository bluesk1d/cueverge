from pathlib import Path

import pytest
from pydantic import ValidationError

from cueverge.config import ControllerConfig, DdpOutputConfig, MediaAgentConfig


def test_hardware_output_is_disabled_by_default() -> None:
    config = ControllerConfig()

    assert not config.ddp.enabled
    assert config.ddp.host is None
    assert config.ddp.brightness_limit == 1.0
    assert config.lighting_delay_ms == 0
    assert config.show_package == Path("shows/demo")


def test_enabled_ddp_requires_explicit_host_and_pixel_count() -> None:
    with pytest.raises(ValidationError, match="requires a host"):
        DdpOutputConfig(enabled=True)
    with pytest.raises(ValidationError, match="expected_pixel_count"):
        DdpOutputConfig(enabled=True, host="wled.local")


def test_media_agent_requires_websocket_controller_url() -> None:
    with pytest.raises(ValidationError, match="ws or wss"):
        MediaAgentConfig(
            agent_id="agent",
            controller_url="http://controller/api/v1/agent/ws",
            asset_root=Path("assets"),
            mpv_executable=Path("mpv.exe"),
        )
