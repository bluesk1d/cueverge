import asyncio
import logging
from contextlib import suppress

from cueverge.config import MediaAgentConfig
from cueverge.media_agent.client import MediaAgentClient
from cueverge.media_agent.mpv import MpvClient, MpvProcess, create_mpv_transport
from cueverge.media_agent.mpv_player import MpvPlayer


async def run() -> None:
    config = MediaAgentConfig.from_environment()
    process = MpvProcess(
        executable=config.mpv_executable,
        pipe_path=config.mpv_pipe,
        fullscreen_screen=config.fullscreen_screen,
        audio_device=config.audio_device,
        audio_delay_seconds=config.audio_delay_ms / 1000,
    )
    transport = create_mpv_transport(config.mpv_pipe)
    mpv = MpvClient(transport)
    process.start()
    try:
        await transport.connect()
        mpv.start_reader()
        player = MpvPlayer(mpv, config.asset_root)
        await MediaAgentClient(config, player).run_forever()
    finally:
        await mpv.close()
        process.stop()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    with suppress(KeyboardInterrupt):
        asyncio.run(run())
