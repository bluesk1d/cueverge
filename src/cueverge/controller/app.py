import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from typing import Protocol

from fastapi import FastAPI, Request
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from cueverge.config import ControllerConfig
from cueverge.controller.loop_monitor import LoopStallMonitor, LoopStallRequestMiddleware
from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.director import ShowDirector
from cueverge.core.loader import load_show_package
from cueverge.gui.config import GuiConfig
from cueverge.gui.device_router import create_device_enrollment_router
from cueverge.gui.embedded_device_listener import EmbeddedDeviceListener
from cueverge.gui.mcp_server import create_mcp_app
from cueverge.gui.observability import ApiTimer
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

LOGGER = logging.getLogger(__name__)
ASSET_TRASH_SWEEP_INTERVAL_SECONDS = 3600
IDLE_PLAYER_SWEEP_INTERVAL_SECONDS = 30


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
    if configured_gui is not None and (ingress is None or isinstance(ingress, FrigateMqttIngress)):
        ingress = configured_gui.input_source
    mcp_app = create_mcp_app(configured_gui) if configured_gui is not None else None
    device_listener = (
        EmbeddedDeviceListener(
            configured_gui.config,
            configured_gui.repository,
            configured_gui.device_sessions,
            controller,
            configured_gui.agent_updates.catalog,
            configured_gui.agent_settings,
        )
        if configured_gui is not None and configured_gui.config.device_listener_enabled
        else None
    )

    loop_monitor = LoopStallMonitor.from_environment()
    controller.loop_monitor = loop_monitor

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if loop_monitor is not None:
            await loop_monitor.start()
        if configured_gui is not None:
            await configured_gui.apply.recover()
        controller.start_background_tasks()
        if ingress is not None:
            ingress.start()
        if device_listener is not None:
            await device_listener.start()
        if configured_gui is not None and configured_gui.home_assistant is not None:
            configured_gui.home_assistant.start()
        asset_recovery = (
            asyncio.create_task(
                asyncio.to_thread(configured_gui.asset_upload.recover_pending_assets)
            )
            if configured_gui is not None
            else None
        )
        asset_trash_sweeper = (
            asyncio.create_task(_sweep_asset_trash(configured_gui))
            if configured_gui is not None
            else None
        )
        idle_player_sweeper = (
            asyncio.create_task(_stop_idle_players(configured_gui))
            if configured_gui is not None
            else None
        )
        try:
            async with AsyncExitStack() as stack:
                if mcp_app is not None:
                    await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
                yield
        finally:
            if configured_gui is not None:
                if configured_gui.home_assistant is not None:
                    await configured_gui.home_assistant.stop()
                await configured_gui.apply.stop()
            for sweeper in (asset_trash_sweeper, idle_player_sweeper):
                if sweeper is not None:
                    sweeper.cancel()
                    await asyncio.gather(sweeper, return_exceptions=True)
            if asset_recovery is not None:
                await asset_recovery
            if device_listener is not None:
                await device_listener.stop()
            if ingress is not None:
                await ingress.stop()
            await controller.shutdown()
            if loop_monitor is not None:
                await loop_monitor.stop()

    app = FastAPI(title="Cueverge Controller", version="0.1.0", lifespan=lifespan)
    app.state.runtime = controller
    if loop_monitor is not None:
        app.add_middleware(LoopStallRequestMiddleware, monitor=loop_monitor)
    if configured_gui is not None:

        @app.middleware("http")
        async def observe_gui_api(
            request: Request,
            call_next: Callable[[Request], Awaitable[Response]],
        ) -> Response:
            timer = ApiTimer()
            status_code = 500
            try:
                response = await call_next(request)
                status_code = response.status_code
                return response
            finally:
                if request.url.path.startswith("/api/gui/"):
                    configured_gui.observability.record_api(
                        timer.elapsed_ms(),
                        status_code,
                    )

        app.include_router(create_gui_router(configured_gui))
        if mcp_app is not None:
            app.mount("/mcp", mcp_app, name="mcp-authoring")
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


async def _sweep_asset_trash(services: GuiServices) -> None:
    """Purge expired asset trash and unreferenced stored files at startup, then hourly."""
    while True:
        try:
            await asyncio.to_thread(services.asset_upload.sweep_trash)
        except Exception:
            LOGGER.exception("asset trash sweep failed")
        await asyncio.sleep(ASSET_TRASH_SWEEP_INTERVAL_SECONDS)


async def _stop_idle_players(services: GuiServices) -> None:
    """Stop on-demand media players that no playing scene has used for the idle period."""
    while True:
        await asyncio.sleep(IDLE_PLAYER_SWEEP_INTERVAL_SECONDS)
        try:
            stopped = await services.device_players.stop_idle_players()
        except Exception:
            LOGGER.exception("idle media player sweep failed")
            continue
        for name in stopped:
            LOGGER.info("stopped idle on-demand media player on %s", name)
            services.repository.append_audit(
                actor="controller",
                action="device.player.idle_stop",
                target=name,
                result="success",
                occurred_at=datetime.now(UTC).isoformat(),
                authorization_context={"policy": "on_demand"},
            )


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
