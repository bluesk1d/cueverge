import asyncio
import logging
from contextlib import suppress

from cueverge.config import MediaAgentConfig
from cueverge.media_agent.client import MediaAgentClient
from cueverge.media_agent.device_presence import DevicePresenceClient
from cueverge.media_agent.mpv import MpvProcess, create_mpv_transport
from cueverge.media_agent.player_host import PlayerHost


async def run() -> None:
    config = MediaAgentConfig.from_environment()
    if config.device_transport_url is None or config.device_credential_root is None:
        raise RuntimeError("media agent requires protocol-v2 device credentials")
    process = MpvProcess(
        executable=config.mpv_executable,
        pipe_path=config.mpv_pipe,
        fullscreen_screen=config.fullscreen_screen,
        audio_device=config.audio_device,
        audio_delay_seconds=config.audio_delay_ms / 1000,
    )
    host = PlayerHost(process, lambda: create_mpv_transport(config.mpv_pipe), config.asset_root)
    if config.player_mode == "always":
        await host.start()
    try:
        media_client = MediaAgentClient(config, host)
        await DevicePresenceClient(
            config.device_transport_url,
            config.device_credential_root,
            reconnect_delay_ms=config.reconnect_delay_ms,
            media_client=media_client,
            player_host=host,
        ).run_forever()
    finally:
        await host.stop_player()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    with suppress(KeyboardInterrupt):
        asyncio.run(run())
