import asyncio
import ssl

import uvicorn

from cueverge.controller.runtime import ControllerRuntime
from cueverge.gui.config import GuiConfig
from cueverge.gui.device_app import create_device_app
from cueverge.gui.device_ca import DeviceCertificateAuthority
from cueverge.gui.device_session import DeviceSessionRegistry
from cueverge.gui.device_uvicorn import PeerCertificateWebSocketProtocol
from cueverge.gui.repository import GuiRepository


class _EmbeddedServer(uvicorn.Server):
    def install_signal_handlers(self) -> None:
        pass


class EmbeddedDeviceListener:
    def __init__(
        self,
        config: GuiConfig,
        repository: GuiRepository,
        sessions: DeviceSessionRegistry,
        runtime: ControllerRuntime,
    ) -> None:
        if not config.device_listener_enabled:
            raise ValueError("embedded device listener requires an enabled listener")
        password = config.device_ca_key_password
        if password is None:
            raise RuntimeError("validated device CA password is missing")
        authority = DeviceCertificateAuthority(config.data_root / "device-ca", password)
        authority.provision()
        certificate_path, key_path = authority.provision_server_certificate(
            config.device_listener_identities
        )
        self.server = _EmbeddedServer(
            uvicorn.Config(
                create_device_app(repository, sessions, runtime, authority),
                host=config.device_listener_host,
                port=config.device_listener_port,
                ws=PeerCertificateWebSocketProtocol,
                lifespan="off",
                log_level="info",
                proxy_headers=False,
                server_header=False,
                ssl_certfile=certificate_path,
                ssl_keyfile=key_path,
                ssl_keyfile_password=password.get_secret_value(),
                ssl_cert_reqs=ssl.CERT_REQUIRED,
                ssl_ca_certs=authority.certificate_path,
            )
        )
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("embedded device listener is already started")
        self._task = asyncio.create_task(
            self.server.serve(),
            name="device-mtls-listener",
        )
        try:
            async with asyncio.timeout(10):
                while not self.server.started:
                    if self._task.done():
                        await self._task
                        raise RuntimeError("device listener stopped during startup")
                    await asyncio.sleep(0.01)
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self.server.should_exit = True
        await task
        self._task = None
