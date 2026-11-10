"""Explicit live production-path test; changes bulbs and restores original state."""

import asyncio
import json
import struct
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lifx_probe import Bulb, inspect

from cueverge.config import LifxResourceConfig
from cueverge.controller.runtime import ControllerRuntime, ShowMode
from cueverge.gui.point_light_outputs import create_output


async def main():
    bulbs = [Bulb(host) for host in ("192.168.40.71", "192.168.40.194")]
    originals = [inspect(bulb) for bulb in bulbs]
    Path("docs/lifx-runtime-originals.json").write_text(json.dumps(originals, indent=2))
    storage = TemporaryDirectory(prefix="cueverge-lifx-test-")
    runtime = ControllerRuntime()
    sinks = ("light.left", "light.right")
    for name, bulb in zip(sinks, bulbs, strict=True):
        create_output(
            Path(storage.name),
            runtime,
            LifxResourceConfig(
                resource=name,
                host=bulb.host,
                target=bulb.target[:6].hex(),
                brightness_limit=0.12,
                duration_ms=50,
            ),
        )
    evidence = {
        "originals": originals,
        "registered_resources": [
            b.model_dump(mode="json")
            for b in runtime.catalog.installation.resources
            if b.resource in sinks
        ],
    }
    try:
        await runtime.set_show_mode(ShowMode.MAINTENANCE)
        status = runtime.start_timing_calibration(
            media=(), lights=tuple(sinks), delays={"light.left": 0, "light.right": 150}
        )
        evidence["calibration_id"] = status.id
        await asyncio.sleep(6)
        runtime.set_timing_calibration_delay("light.right", 0)
        await asyncio.sleep(4)
        evidence["transport_during_calibration"] = runtime.lighting_transport_status()
        await runtime.stop_timing_calibration()
        await asyncio.sleep(0.25)
        evidence["stopped_brightness"] = [
            struct.unpack_from("<4H", b.query(101, 107)[0])[2] for b in bulbs
        ]
        runtime.start_output_test(
            resource="light.left",
            pixel_count=1,
            speed=10,
            duration_seconds=2,
            arm_id="live-test",
            segments=(),
            on_finish=lambda _: None,
        )
        await asyncio.sleep(2.5)
        evidence["physical_test_state"] = runtime.output_test_status().state
        evidence["transport_after_test"] = runtime.lighting_transport_status()
    finally:
        await runtime.shutdown()
        evidence["restored"] = []
        for bulb, original in zip(bulbs, originals, strict=True):
            try:
                bulb.receive(bulb.color(original["hsbk"], ack=True), 45)
                time.sleep(0.1)
                bulb.receive(bulb.send(117, struct.pack("<HI", original["power"], 0), True), 45)
                time.sleep(0.1)
                state, _ = bulb.query(101, 107)
                evidence["restored"].append(
                    list(struct.unpack_from("<4H", state)) == list(original["hsbk"])
                    and struct.unpack_from("<H", state, 10)[0] == original["power"]
                )
            finally:
                bulb.sock.close()
        Path("docs/lifx-runtime-live-test.json").write_text(json.dumps(evidence, indent=2))
        print(json.dumps(evidence, indent=2))
        storage.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
