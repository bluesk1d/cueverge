import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class WledStatus:
    connected: bool
    latency_ms: float | None
    software_version: str | None
    reported_pixel_count: int | None
    signal_percent: int | None
    output_on: bool | None
    diagnostic: str | None


class WledStatusObserver:
    def __init__(self, *, timeout_seconds: float = 2.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def observe(self, host: str) -> WledStatus:
        try:
            return await asyncio.to_thread(self._observe, host)
        except urllib.error.URLError as error:
            return _unavailable(f"WLED HTTP request failed: {error.reason}")
        except (TimeoutError, json.JSONDecodeError, ValueError, TypeError, KeyError) as error:
            return _unavailable(f"WLED status response was invalid: {error}")

    def _observe(self, host: str) -> WledStatus:
        started = time.monotonic()
        info = self._get_json(f"http://{host}/json/info")
        state = self._get_json(f"http://{host}/json/state")
        leds = info["leds"]
        wifi = info["wifi"]
        if not isinstance(leds, dict) or not isinstance(wifi, dict):
            raise ValueError("info.leds and info.wifi must be objects")
        return WledStatus(
            connected=True,
            latency_ms=round((time.monotonic() - started) * 1000, 1),
            software_version=str(info["ver"]),
            reported_pixel_count=int(leds["count"]),
            signal_percent=int(wifi["signal"]),
            output_on=bool(state["on"]),
            diagnostic=None,
        )

    def _get_json(self, url: str) -> dict[str, object]:
        with urllib.request.urlopen(url, timeout=self.timeout_seconds) as response:
            payload = response.read(1_048_577)
        if len(payload) > 1_048_576:
            raise ValueError("response exceeds one MiB")
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("response must be a JSON object")
        return value


def _unavailable(diagnostic: str) -> WledStatus:
    return WledStatus(
        connected=False,
        latency_ms=None,
        software_version=None,
        reported_pixel_count=None,
        signal_percent=None,
        output_on=None,
        diagnostic=diagnostic,
    )
