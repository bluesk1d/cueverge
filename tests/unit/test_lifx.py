import socket
import struct
from pathlib import Path

import pytest
from pydantic import ValidationError

from cueverge.config import OutputDeploymentConfig
from cueverge.gui.contracts import InstallationGeometryRequest
from cueverge.gui.repository import GuiRepository
from cueverge.lighting.lifx import LifxUdpSink
from cueverge.lighting.sink import RenderedFrame


def test_runtime_calibration_uses_lifx_and_bypasses_interpolation() -> None:
    import asyncio

    from cueverge.controller.runtime import ControllerRuntime, ShowMode
    from cueverge.lighting.sink import FrameSinkRouter, MemoryFrameSink

    async def scenario() -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.setblocking(False)
            sink = LifxUdpSink(
                "127.0.0.1", "d073d58b1a6d", port=receiver.getsockname()[1], duration_ms=500
            )
            runtime = ControllerRuntime(
                sink=FrameSinkRouter(
                    {
                        "left": sink,
                        "roofline.demo": MemoryFrameSink(),
                    }
                )
            )
            try:
                assert "left" in {t.resource for t in runtime.lighting_targets()}
                await runtime.set_show_mode(ShowMode.MAINTENANCE)
                runtime.start_timing_calibration(media=(), lights=("left",), delays={"left": 0})
                loop = asyncio.get_running_loop()
                packets = []
                for _ in range(4):
                    data, address = await asyncio.wait_for(loop.sock_recvfrom(receiver, 1024), 2)
                    packets.append(data)
                    ack = bytearray(data[:36])
                    struct.pack_into("<H", ack, 0, 36)
                    struct.pack_into("<H", ack, 32, 45)
                    receiver.sendto(ack, address)
                colors = [p for p in packets if len(p) == 49]
                assert colors and all(struct.unpack_from("<I", p, 45)[0] == 0 for p in colors)
                runtime.set_timing_calibration_delay("left", 125)
                assert runtime._timing_calibration.status.outputs["left"].delay_ms == 125
                await asyncio.sleep(0.1)
                assert runtime.status()["lighting_transport"]["left"]["reply_recent"]
                await runtime.stop_timing_calibration()
                await asyncio.sleep(0.15)
                remaining = []
                while True:
                    try:
                        remaining.append(receiver.recvfrom(1024)[0])
                    except BlockingIOError:
                        break
                assert struct.unpack_from("<H", remaining[-1], 41)[0] == 0
            finally:
                await runtime.shutdown()

    asyncio.run(scenario())


def test_latest_frame_delivery_and_packet_encoding() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(2)
        sink = LifxUdpSink("127.0.0.1", "d073d58b1a6d", port=receiver.getsockname()[1])
        try:
            with pytest.raises(ValueError):
                sink.send(RenderedFrame("test", 0, ((0, 0, 0), (1, 1, 1))))
            sink.send(RenderedFrame("test", 0, ((255, 0, 0),)))
            power, _ = receiver.recvfrom(1024)
            assert struct.unpack_from("<H", power, 32)[0] == 117
            for position in range(50):
                sink.send(RenderedFrame("test", position, ((0, 255, 0),)))
            sink.close()
            color, _ = receiver.recvfrom(1024)
            assert len(color) == 49
            assert color[8:16] == bytes.fromhex("d073d58b1a6d0000")
            assert struct.unpack_from("<H", color, 32)[0] == 102
            assert struct.unpack_from("<B4HI", color, 36) == (0, 21845, 65535, 65535, 3500, 0)
            assert sink.frames_coalesced >= 49
        finally:
            sink.close()


def test_point_light_contract_and_deployment() -> None:
    shape = dict(
        kind="point_light",
        name="Left",
        points=[dict(x=0.2, y=0.3)],
        closed=False,
        start_index=0,
        pixel_count=1,
    )
    assert InstallationGeometryRequest.model_validate(shape).kind == "point_light"
    for changes in [
        dict(pixel_count=2),
        dict(start_index=1),
        dict(closed=True),
        dict(points=[dict(x=0.2, y=0.3), dict(x=0.4, y=0.5)]),
    ]:
        with pytest.raises(ValidationError):
            InstallationGeometryRequest.model_validate(shape | changes)
    deployment = dict(
        schema_version=1,
        lighting=[dict(resource="left", kind="lifx", host="192.168.40.71", target="d073d58b1a6d")],
    )
    assert OutputDeploymentConfig.model_validate(deployment).lighting[0].expected_pixel_count == 1
    deployment["lighting"][0]["max_hz"] = 60
    with pytest.raises(ValidationError):
        OutputDeploymentConfig.model_validate(deployment)


def test_existing_geometry_database_accepts_point_lights(tmp_path: Path) -> None:
    import sqlite3

    repository = GuiRepository(tmp_path / "gui.sqlite3")
    repository.migrate()
    # Exercise the same rebuild needed by a pre-point-light installation.
    with sqlite3.connect(repository.database_path) as connection:
        schema = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name='installation_geometry'"
        ).fetchone()[0]
        assert "'point_light'" in schema
    repository.migrate()
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    from cueverge.gui.contracts import Role
    from cueverge.gui.installation_geometry import InstallationGeometryService
    from cueverge.gui.lighting_surface import LightingSurfaceService

    installation = repository.ensure_runtime_installation(name="Home", occurred_at="now")
    geometry = InstallationGeometryService(repository)
    point = geometry.create(
        installation_id=installation.id,
        photo_id=None,
        kind="point_light",
        name="Left",
        points=((0.25, 0.75),),
        closed=False,
        semantic_id=None,
        color="#ffffff",
        label_visible=True,
        locked=False,
        hidden=False,
        z_index=0,
        direction="forward",
        start_index=0,
        pixel_count=1,
        stage_points=None,
        actor="test",
        role=Role.ADMINISTRATOR,
        occurred_at="now",
    )
    surfaces = LightingSurfaceService(repository, geometry)
    surface = surfaces.upsert(
        geometry_id=point.id,
        expected_revision=None,
        resource="light.left",
        local_width=1,
        local_height=1,
        local_points=((0.25, 0.75),),
        stage_matrix=(1, 0, 0, 1, 0, 0),
        fixture_id=None,
        group_id=None,
        path_id=None,
        strand_id=None,
        calibration_id=None,
        pixel_footprint_width=0.1,
        pixel_footprint_height=0.1,
        brightness_limit=1,
        timing_offset_ms=0,
        release_offset_ms=0,
        actor="test",
        role=Role.ADMINISTRATOR,
        occurred_at="now",
    )
    assert len(surface.pixels) == 1
    assert surface.pixels[0].output_index == 0
    assert (surface.pixels[0].stage_x, surface.pixels[0].stage_y) == (0.25, 0.75)
    repository.migrate()
    assert surfaces.get(point.id).resource == "light.left"
