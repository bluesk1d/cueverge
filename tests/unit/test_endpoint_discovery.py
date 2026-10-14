import json

import pytest

from cueverge.gui.endpoint_discovery import (
    EndpointDiscoveryError,
    EndpointDiscoveryService,
)


class FixtureDiscovery(EndpointDiscoveryService):
    def __init__(self, addresses: tuple[str, ...]) -> None:
        super().__init__()
        self.addresses = addresses

    def _resolve(self, host: str) -> tuple[str, ...]:
        return self.addresses

    def _fetch(self, url: str, addresses: tuple[str, ...]) -> bytes:
        assert addresses == self.addresses
        return json.dumps(
            {
                "service": "cueverge",
                "schema_version": 1,
                "device_kind": "downstream_controller",
                "software_version": "0.1.0",
                "protocol": {"minimum": 2, "maximum": 2},
                "capabilities": ["downstream_control"],
            }
        ).encode()


def test_probes_explicit_private_https_controller() -> None:
    result = FixtureDiscovery(("192.168.1.5",)).probe("https://controller.local/")

    assert result.base_url == "https://controller.local"
    assert result.document.device_kind == "downstream_controller"
    assert result.document.protocol.minimum == 2


def test_rejects_non_https_and_public_targets() -> None:
    with pytest.raises(EndpointDiscoveryError, match="HTTPS origin"):
        FixtureDiscovery(("192.168.1.5",)).probe("http://controller.local")
    with pytest.raises(EndpointDiscoveryError, match="private"):
        FixtureDiscovery(("8.8.8.8",)).probe("https://example.test")
