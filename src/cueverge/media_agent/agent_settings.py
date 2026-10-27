"""Controller-managed agent settings: stored overrides, effective values and host discovery."""

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from pydantic import ValidationError

from cueverge.config import MediaAgentConfig
from cueverge.protocol.device_v2 import (
    AGENT_SETTING_NAMES,
    AgentAudioDevice,
    AgentDisplay,
    AgentEffectiveSettings,
    AgentSettingName,
    AgentSettings,
    AgentSettingsState,
)

LOGGER = logging.getLogger(__name__)
SETTINGS_FILE = "controller-settings.json"
_AUDIO_LINE = re.compile(r"^\s*'(?P<name>[^']+)'\s*\((?P<description>.*)\)\s*$")


def host_defaults(config: MediaAgentConfig) -> AgentEffectiveSettings:
    return AgentEffectiveSettings(
        keep_awake=config.keep_awake,
        player_mode=config.player_mode,
        fullscreen_screen=config.fullscreen_screen,
        audio_device=config.audio_device,
        audio_delay_ms=config.audio_delay_ms,
        telemetry_hz=config.telemetry_hz,
    )


class AgentSettingsStore:
    """Controller overrides persisted beside the device credentials.

    The agent starts with them even when the controller is unreachable. Fields the controller
    left unset fall back to the host's environment file.
    """

    def __init__(self, path: Path, defaults: AgentEffectiveSettings) -> None:
        self.path = path
        self.host_defaults = defaults
        self.revision = 0
        self.overrides = AgentSettings()
        self._load()

    @property
    def effective(self) -> AgentEffectiveSettings:
        return self.host_defaults.merged(self.overrides)

    def replace(self, revision: int, overrides: AgentSettings) -> tuple[AgentSettingName, ...]:
        """Store new overrides and return the settings whose effective value changed."""
        before = self.effective
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(
            json.dumps({"revision": revision, "settings": overrides.model_dump(mode="json")}),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        self.revision, self.overrides = revision, overrides
        after = self.effective
        return tuple(
            name for name in AGENT_SETTING_NAMES if getattr(before, name) != getattr(after, name)
        )

    def state(
        self,
        *,
        pending_player_restart: tuple[AgentSettingName, ...] = (),
        displays: tuple[AgentDisplay, ...] = (),
        audio_devices: tuple[AgentAudioDevice, ...] = (),
    ) -> AgentSettingsState:
        return AgentSettingsState(
            revision=self.revision,
            effective=self.effective,
            host_defaults=self.host_defaults,
            pending_player_restart=pending_player_restart,
            displays=displays,
            audio_devices=audio_devices,
        )

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.revision = int(data["revision"])
            self.overrides = AgentSettings.model_validate(data["settings"])
        except (OSError, ValueError, KeyError, TypeError, ValidationError) as error:
            LOGGER.warning("ignoring unreadable controller settings %s: %s", self.path, error)
            self.revision, self.overrides = 0, AgentSettings()


def discover_audio_devices(mpv_executable: Path) -> tuple[AgentAudioDevice, ...]:
    """The audio outputs mpv itself can open, by the names --audio-device accepts."""
    try:
        result = subprocess.run(
            [str(mpv_executable), "--audio-device=help"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        LOGGER.warning("could not list mpv audio devices: %s", error)
        return ()
    devices: list[AgentAudioDevice] = []
    for line in result.stdout.splitlines():
        match = _AUDIO_LINE.match(line)
        if match and len(devices) < 64:
            devices.append(
                AgentAudioDevice(name=match["name"][:512], description=match["description"][:256])
            )
    return tuple(devices)


def discover_displays() -> tuple[AgentDisplay, ...]:
    try:
        if sys.platform == "win32":
            return _windows_displays()
        if sys.platform.startswith("linux"):
            return _linux_displays(Path("/sys/class/drm"))
    except (OSError, ValueError) as error:
        LOGGER.warning("could not list displays: %s", error)
    return ()


def _linux_displays(root: Path) -> tuple[AgentDisplay, ...]:
    """Connected DRM connectors in connector order; mode is the preferred (first) mode."""
    displays: list[AgentDisplay] = []
    connectors = sorted(
        (path for path in root.glob("card*-*") if (path / "status").is_file()),
        key=lambda path: path.name,
    )
    for connector in connectors:
        if (connector / "status").read_text(encoding="utf-8").strip() != "connected":
            continue
        width = height = None
        modes = (
            (connector / "modes").read_text(encoding="utf-8").split()
            if (connector / "modes").is_file()
            else []
        )
        if modes and (match := re.match(r"^(\d+)x(\d+)", modes[0])):
            width, height = int(match[1]), int(match[2])
        name = connector.name.split("-", 1)[1]
        displays.append(
            AgentDisplay(index=len(displays), name=name[:128], width=width, height=height)
        )
    return tuple(displays[:32])


def _windows_displays() -> tuple[AgentDisplay, ...]:
    """Monitors in EnumDisplayMonitors order, which mpv uses for --fs-screen on Windows."""
    import ctypes
    from ctypes import wintypes

    user32 = getattr(ctypes, "WinDLL")("user32", use_last_error=True)  # noqa: B009

    class MonitorInfo(ctypes.Structure):
        _fields_ = (
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        )

    displays: list[AgentDisplay] = []
    callback_type = getattr(ctypes, "WINFUNCTYPE")(  # noqa: B009
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    def visit(monitor: int, _dc: int, _rect: object, _data: int) -> bool:
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)) and len(displays) < 32:
            rect = info.rcMonitor
            displays.append(
                AgentDisplay(
                    index=len(displays),
                    name=(info.szDevice or f"Display {len(displays) + 1}")[:128],
                    width=max(1, rect.right - rect.left),
                    height=max(1, rect.bottom - rect.top),
                    primary=bool(info.dwFlags & 1),
                )
            )
        return True

    user32.EnumDisplayMonitors(None, None, callback_type(visit), 0)
    return tuple(displays)
