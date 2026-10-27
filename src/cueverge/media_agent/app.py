import asyncio
import logging
import sys
from contextlib import suppress

from cueverge.config import MediaAgentConfig
from cueverge.media_agent.agent_settings import (
    SETTINGS_FILE,
    AgentSettingsStore,
    discover_audio_devices,
    discover_displays,
    host_defaults,
)
from cueverge.media_agent.agent_update import AgentInstallation, AgentRestartRequested
from cueverge.media_agent.client import MediaAgentClient
from cueverge.media_agent.device_presence import DevicePresenceClient
from cueverge.media_agent.keep_awake import KeepAwake
from cueverge.media_agent.launcher import RESTART_EXIT_CODE
from cueverge.media_agent.mpv import MpvProcess, create_mpv_transport
from cueverge.media_agent.player_host import PlayerHost


async def run() -> None:
    config = MediaAgentConfig.from_environment()
    if config.device_transport_url is None or config.device_credential_root is None:
        raise RuntimeError("media agent requires protocol-v2 device credentials")
    settings = AgentSettingsStore(
        config.device_credential_root / SETTINGS_FILE, host_defaults(config)
    )
    effective = settings.effective
    process = MpvProcess(executable=config.mpv_executable, pipe_path=config.mpv_pipe)
    host = PlayerHost(process, lambda: create_mpv_transport(config.mpv_pipe), config.asset_root)
    host.configure(effective)
    keep_awake = KeepAwake(effective.keep_awake)
    await keep_awake.start()
    keep_awake_task = asyncio.create_task(keep_awake.maintain())
    try:
        if effective.player_mode == "always":
            await host.start()
        displays = await asyncio.to_thread(discover_displays)
        audio_devices = await asyncio.to_thread(discover_audio_devices, config.mpv_executable)
        media_client = MediaAgentClient(config, host)
        await DevicePresenceClient(
            config.device_transport_url,
            config.device_credential_root,
            reconnect_delay_ms=config.reconnect_delay_ms,
            media_client=media_client,
            player_host=host,
            keep_awake=keep_awake,
            installation=AgentInstallation.from_environment(),
            settings=settings,
            displays=displays,
            audio_devices=audio_devices,
        ).run_forever()
    finally:
        keep_awake_task.cancel()
        with suppress(asyncio.CancelledError):
            await keep_awake_task
        await host.stop_player()
        await keep_awake.stop()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        with suppress(KeyboardInterrupt):
            asyncio.run(run())
    except AgentRestartRequested:
        logging.getLogger(__name__).info("restarting into the updated agent")
        sys.exit(RESTART_EXIT_CODE)


if __name__ == "__main__":
    main()
