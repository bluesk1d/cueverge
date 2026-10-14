import asyncio
import json
from typing import Any

from cueverge.gui.wled_status import WledStatusObserver


class FixtureObserver(WledStatusObserver):
    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        super().__init__()
        self.responses = responses

    def _get_json(self, url: str) -> dict[str, object]:
        return json.loads(json.dumps(self.responses[url]))


def test_observes_wled_health_without_mutation() -> None:
    observer = FixtureObserver(
        {
            "http://wled-001/json/info": {
                "ver": "16.0.1",
                "leds": {"count": 605},
                "wifi": {"signal": 74},
            },
            "http://wled-001/json/state": {"on": False},
        }
    )

    status = asyncio.run(observer.observe("wled-001"))

    assert status.connected
    assert status.software_version == "16.0.1"
    assert status.reported_pixel_count == 605
    assert status.signal_percent == 74
    assert status.output_on is False
    assert status.diagnostic is None
    assert status.latency_ms is not None
