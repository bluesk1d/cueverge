from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI, Request
from starlette.staticfiles import StaticFiles

from cueverge.config import ControllerConfig
from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.director import ShowDirector
from cueverge.core.loader import load_show_package
from cueverge.gui.config import GuiConfig
from cueverge.gui.device_router import create_device_enrollment_router
from cueverge.gui.embedded_device_listener import EmbeddedDeviceListener
from cueverge.gui.router import create_gui_router
from cueverge.gui.service import GuiServices
from cueverge.integrations.mqtt import FrigateMqttIngress
from cueverge.lighting.ddp import DdpUdpSink
from cueverge.lighting.sink import FrameSink, FrameSinkRouter, MemoryFrameSink
from cueverge.observation.presence import AudiencePresenceEstimator
from cueverge.protocol.device_v2 import (
    ControllerDiscoveryDocument,
    DiscoveryProtocolRange,
)


class EventIngress(Protocol):
    def start(self) -> None: ...

    async def stop(self) -> None: ...


def create_app(
    runtime: ControllerRuntime | None = None,
    event_ingress: EventIngress | None = None,
    gui_services: GuiServices | None = None,
) -> FastAPI:
    controller, configured_ingress = (
        (runtime, event_ingress) if runtime is not None else _configured_components()
    )
    ingress = event_ingress or configured_ingress
    configured_gui = gui_services
    if configured_gui is None and runtime is None:
        gui_config = GuiConfig.from_environment()
        if gui_config is not None:
            configured_gui = GuiServices.create(gui_config, controller)
    device_listener = (
        EmbeddedDeviceListener(
            configured_gui.config,
            configured_gui.repository,
            configured_gui.device_sessions,
            controller,
        )
        if configured_gui is not None and configured_gui.config.device_listener_enabled
        else None
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        controller.start_background_tasks()
        if ingress is not None:
            ingress.start()
        if device_listener is not None:
            await device_listener.start()
        try:
            yield
        finally:
            if device_listener is not None:
                await device_listener.stop()
            if ingress is not None:
                await ingress.stop()
            await controller.shutdown()

    app = FastAPI(title="Cueverge Controller", version="0.1.0", lifespan=lifespan)
    app.state.runtime = controller
    if configured_gui is not None:
        app.include_router(create_gui_router(configured_gui))
        if configured_gui.device_enrollment is not None:
            app.include_router(create_device_enrollment_router(configured_gui.device_enrollment))

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready(request: Request) -> dict[str, object]:
        controller: ControllerRuntime = request.app.state.runtime
        controller.check_timeouts()
        return {"status": "ready", "connected_agents": controller.connected_agent_count}

    @app.get(
        "/api/device/v2/discovery",
        response_model=ControllerDiscoveryDocument,
    )
    async def device_discovery() -> ControllerDiscoveryDocument:
        return ControllerDiscoveryDocument(
            software_version="0.1.0",
            protocol=DiscoveryProtocolRange(minimum=2, maximum=2),
        )

    if configured_gui is not None and configured_gui.config.web_root is not None:
        app.mount(
            "/",
            StaticFiles(directory=configured_gui.config.web_root, html=True),
            name="gui-web",
        )

    return app


def _configured_components() -> tuple[ControllerRuntime, EventIngress | None]:
    config = ControllerConfig.from_environment()
    catalog = load_show_package(config.show_package)
    audience = AudiencePresenceEstimator(catalog.audience) if catalog.audience is not None else None
    if config.output_deployment is not None:
        sink: FrameSink = FrameSinkRouter(
            {
                output.resource: DdpUdpSink(
                    host=output.host,
                    port=output.port,
                    destination_id=output.destination_id,
                    max_payload_bytes=output.max_payload_bytes,
                    expected_pixel_count=output.expected_pixel_count,
                    brightness_limit=output.brightness_limit,
                )
                for output in config.output_deployment.lighting
            }
        )
    elif config.ddp.enabled:
        sink = DdpUdpSink(
            host=config.ddp.host or "",
            port=config.ddp.port,
            destination_id=config.ddp.destination_id,
            max_payload_bytes=config.ddp.max_payload_bytes,
            expected_pixel_count=config.ddp.expected_pixel_count,
            brightness_limit=config.ddp.brightness_limit,
        )
    else:
        sink = MemoryFrameSink()
    runtime = ControllerRuntime(
        sink=sink,
        catalog=catalog,
        lighting_delay_ms=config.lighting_delay_ms,
        director=(
            ShowDirector(
                catalog.program,
                context_provider=audience.context if audience is not None else None,
            )
            if catalog.program is not None
            else None
        ),
        audience=audience,
    )
    ingress = (
        FrigateMqttIngress(config.frigate_mqtt, runtime.ingest_event)
        if config.frigate_mqtt.enabled
        else None
    )
    return runtime, ingress


app = create_app()
