"""Home Assistant control over MQTT: discovery, retained state and show commands."""

import asyncio
import json
import logging
import os
import ssl
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

import aiomqtt
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from cueverge.config import FrigateMqttConfig
from cueverge.gui.contracts import OperateActionRequest

LOGGER = logging.getLogger(__name__)
MAX_COMMAND_PAYLOAD_BYTES = 256
STATE_POLL_SECONDS = 1.0
MODE_LABELS = {
    "automatic": "Automatic",
    "disabled": "Stopped",
    "maintenance": "Maintenance",
    "blackout": "Blackout",
}
MODE_ACTIONS: dict[str, Literal["automatic", "stop", "maintenance", "blackout"]] = {
    "Automatic": "automatic",
    "Stopped": "stop",
    "Maintenance": "maintenance",
    "Blackout": "blackout",
}
COMMANDS: dict[str, Literal["start", "stop", "pause", "resume", "blackout"]] = {
    "start": "start",
    "stop": "stop",
    "pause": "pause",
    "resume": "resume",
    "blackout": "blackout",
}
BUTTONS = (
    ("start", "Start show", "mdi:play"),
    ("stop", "Stop show", "mdi:stop"),
    ("pause", "Pause", "mdi:pause"),
    ("resume", "Resume", "mdi:play-pause"),
    ("blackout", "Blackout", "mdi:lightbulb-off"),
)
SENSORS = (
    ("playback", "Playback", "{{ value_json.playback }}", "mdi:play-circle-outline"),
    ("active_scene", "Active scene", "{{ value_json.active_scene_name or 'None' }}", "mdi:movie"),
    ("loaded_show", "Loaded show", "{{ value_json.loaded_show or 'None' }}", "mdi:folder-play"),
)


class HomeAssistantError(RuntimeError):
    pass


class HomeAssistantSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    revision: int = Field(default=0, ge=0)
    topic_prefix: str = Field(default="cueverge", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    discovery_prefix: str = Field(default="homeassistant", pattern=r"^[A-Za-z0-9_-]{1,64}$")


class ControlConnection(Protocol):
    async def publish(self, topic: str, payload: str, *, retain: bool = False) -> None: ...

    async def subscribe(self, topic: str) -> None: ...

    def messages(self) -> AsyncIterator[tuple[str, bytes]]: ...


ControlConnector = Callable[
    [FrigateMqttConfig, str], AbstractAsyncContextManager[ControlConnection]
]
AuditCallback = Callable[[str, str, dict[str, JsonValue]], None]


class HomeAssistantBridge:
    """Publishes Cueverge as an MQTT-discovered Home Assistant device and runs its commands.

    Commands use the same operate path, Apply interlock and audit log as the UI.
    Security relies on the broker's ACLs; commands are only accepted while an
    administrator has enabled the bridge.
    """

    def __init__(
        self,
        root: Path,
        *,
        installation_id: str,
        installation_name: Callable[[], str],
        status: Callable[[], dict[str, object]],
        operate: Callable[[OperateActionRequest], Awaitable[None]],
        broker: Callable[[], FrigateMqttConfig],
        audit: AuditCallback,
        connector: ControlConnector | None = None,
        reconnect_delay_s: float = 5.0,
    ) -> None:
        self.root = root
        self.installation_id = installation_id
        self._installation_name = installation_name
        self._status = status
        self._operate = operate
        self._broker = broker
        self._audit = audit
        self._connector = connector or _connect_aiomqtt
        self._reconnect_delay_s = reconnect_delay_s
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._started = False
        self._settings = self._load()
        self.connected = False
        self.last_error: str | None = None
        self.commands_received = 0
        self.commands_rejected = 0
        self.recent: deque[dict[str, object]] = deque(maxlen=10)
        self._published_scenes: tuple[str, ...] | None = None

    @property
    def settings(self) -> HomeAssistantSettings:
        return self._settings

    @property
    def base_topic(self) -> str:
        return f"{self._settings.topic_prefix}/{self.installation_id}"

    def start(self) -> None:
        self._started = True
        self._restart()

    async def stop(self) -> None:
        self._started = False
        await self._cancel()

    async def configure(self, *, enabled: bool, expected_revision: int | None) -> dict[str, object]:
        async with self._lock:
            if expected_revision is not None and expected_revision != self._settings.revision:
                raise HomeAssistantError(
                    "Home Assistant settings changed elsewhere. Reload before saving again."
                )
            previous = self._settings
            candidate = previous.model_copy(
                update={"enabled": enabled, "revision": previous.revision + 1}
            )
            self._save(candidate)
            await self._cancel()
            if previous.enabled and not enabled:
                await self._remove_discovery(previous)
            self._settings = candidate
            self._published_scenes = None
            self._restart()
            return self.snapshot()

    def snapshot(self) -> dict[str, object]:
        broker = self._broker()
        base = self.base_topic
        return {
            "installation_id": self.installation_id,
            "enabled": self._settings.enabled,
            "configuration_revision": self._settings.revision,
            "state": "disabled"
            if not self._settings.enabled
            else "unavailable"
            if not broker.host
            else "connected"
            if self.connected
            else "connecting",
            "error": None
            if not self._settings.enabled
            else "Set up the MQTT broker under Installation > Inputs first."
            if not broker.host
            else self.last_error,
            "broker": {"host": broker.host or "", "port": broker.port, "tls": broker.tls},
            "topics": {
                "availability": f"{base}/availability",
                "state": f"{base}/state",
                "command": f"{base}/command",
                "scene": f"{base}/scene/set",
                "mode": f"{base}/mode/set",
                "discovery_prefix": self._settings.discovery_prefix,
            },
            "commands": sorted(COMMANDS),
            "modes": list(MODE_ACTIONS),
            "commands_received": self.commands_received,
            "commands_rejected": self.commands_rejected,
            "recent_commands": list(self.recent),
        }

    def _restart(self) -> None:
        if self._started and self._settings.enabled and self._broker().host:
            self._task = asyncio.create_task(self._run(), name="home-assistant-mqtt")

    async def _cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self.connected = False

    async def _run(self) -> None:
        while True:
            broker = self._broker()
            availability = f"{self.base_topic}/availability"
            broker_changed = False
            try:
                async with self._connector(broker, availability) as connection:
                    await connection.subscribe(f"{self.base_topic}/command")
                    await connection.subscribe(f"{self.base_topic}/scene/set")
                    await connection.subscribe(f"{self.base_topic}/mode/set")
                    self._published_scenes = None
                    await self._publish_discovery(connection)
                    await connection.publish(availability, "online", retain=True)
                    self.connected = True
                    self.last_error = None
                    async with asyncio.TaskGroup() as group:
                        group.create_task(self._handle_messages(connection))
                        group.create_task(self._publish_state(connection, broker))
            except* _BrokerChanged:
                broker_changed = True
                LOGGER.info("MQTT broker settings changed; reconnecting Home Assistant bridge")
            except* aiomqtt.MqttError:
                self.last_error = (
                    "Broker connection failed. Check the broker settings under "
                    "Installation > Inputs; reconnecting automatically."
                )
                LOGGER.warning("Home Assistant MQTT connection failed; reconnecting")
            finally:
                self.connected = False
            if not broker_changed:
                await asyncio.sleep(self._reconnect_delay_s)

    async def _publish_state(
        self, connection: ControlConnection, broker: FrigateMqttConfig
    ) -> None:
        last: str | None = None
        while True:
            if self._broker() != broker:
                raise _BrokerChanged
            status = self._status()
            scenes = _scene_options(status)
            if tuple(scenes) != self._published_scenes:
                await self._publish_scene_select(connection, scenes)
            payload = json.dumps(_state(status, scenes), sort_keys=True)
            if payload != last:
                await connection.publish(f"{self.base_topic}/state", payload, retain=True)
                last = payload
            await asyncio.sleep(STATE_POLL_SECONDS)

    async def _handle_messages(self, connection: ControlConnection) -> None:
        async for topic, payload in connection.messages():
            await self._command(topic, payload)

    async def _command(self, topic: str, payload: bytes) -> None:
        self.commands_received += 1
        text = payload[: MAX_COMMAND_PAYLOAD_BYTES + 1].decode("utf-8", "replace").strip()
        received_at = time.time_ns() // 1_000_000
        try:
            if len(payload) > MAX_COMMAND_PAYLOAD_BYTES:
                raise HomeAssistantError("Command payload is too large")
            request = self._parse(topic, text)
            await self._operate(request)
        except (HomeAssistantError, ValidationError, RuntimeError, LookupError) as error:
            self.commands_rejected += 1
            message = str(error) if not isinstance(error, ValidationError) else "Invalid command"
            self.recent.append(
                {
                    "received_at_ms": received_at,
                    "topic": topic,
                    "payload": text[:64],
                    "accepted": False,
                    "error": message[:200],
                }
            )
            self._audit(
                "mqtt.operate",
                "error",
                {"topic": topic, "payload": text[:64], "message": message[:200]},
            )
            LOGGER.warning("rejected Home Assistant MQTT command on %s", topic)
            return
        self.recent.append(
            {
                "received_at_ms": received_at,
                "topic": topic,
                "payload": text[:64],
                "accepted": True,
                "action": request.action,
            }
        )
        self._audit(
            f"mqtt.operate.{request.action}",
            "success",
            {"topic": topic, "scene_id": request.scene_id},
        )

    def _parse(self, topic: str, text: str) -> OperateActionRequest:
        base = self.base_topic
        if topic == f"{base}/command":
            action = COMMANDS.get(text.casefold())
            if action is None:
                raise HomeAssistantError(f"Unknown command {text!r}")
            return OperateActionRequest(action=action)
        if topic == f"{base}/mode/set":
            mode = MODE_ACTIONS.get(text)
            if mode is None:
                raise HomeAssistantError(f"Unknown mode {text!r}")
            return OperateActionRequest(action=mode)
        if topic == f"{base}/scene/set":
            scenes = _scene_options(self._status())
            scene_id = scenes.get(text) or (text if text in scenes.values() else None)
            if scene_id is None:
                raise HomeAssistantError(f"Scene {text!r} is not in the loaded show")
            return OperateActionRequest(action="start", scene_id=scene_id)
        raise HomeAssistantError(f"Unsupported topic {topic}")

    def _device(self) -> dict[str, object]:
        return {
            "identifiers": [f"cueverge_{self.installation_id}"],
            "name": f"Cueverge {self._installation_name()}",
            "manufacturer": "Cueverge",
            "model": "Show controller",
        }

    def _common(self, key: str, name: str) -> dict[str, object]:
        return {
            "name": name,
            "unique_id": f"cueverge_{self.installation_id}_{key}",
            "object_id": f"cueverge_{key}",
            "device": self._device(),
            "availability_topic": f"{self.base_topic}/availability",
        }

    def _config_topic(self, component: str, key: str, settings: HomeAssistantSettings) -> str:
        return (
            f"{settings.discovery_prefix}/{component}/cueverge_{self.installation_id}/{key}/config"
        )

    def _entities(self) -> list[tuple[str, str]]:
        return [
            *(("button", key) for key, _, _ in BUTTONS),
            *(("sensor", key) for key, _, _, _ in SENSORS),
            ("select", "mode"),
            ("select", "scene"),
        ]

    async def _publish_discovery(self, connection: ControlConnection) -> None:
        settings = self._settings
        base = self.base_topic
        for key, name, icon in BUTTONS:
            await connection.publish(
                self._config_topic("button", key, settings),
                json.dumps(
                    {
                        **self._common(key, name),
                        "icon": icon,
                        "command_topic": f"{base}/command",
                        "payload_press": key,
                    }
                ),
                retain=True,
            )
        for key, name, template, icon in SENSORS:
            await connection.publish(
                self._config_topic("sensor", key, settings),
                json.dumps(
                    {
                        **self._common(key, name),
                        "icon": icon,
                        "state_topic": f"{base}/state",
                        "value_template": template,
                    }
                ),
                retain=True,
            )
        await connection.publish(
            self._config_topic("select", "mode", settings),
            json.dumps(
                {
                    **self._common("mode", "Show mode"),
                    "icon": "mdi:theater",
                    "command_topic": f"{base}/mode/set",
                    "state_topic": f"{base}/state",
                    "value_template": "{{ value_json.mode_label }}",
                    "options": list(MODE_ACTIONS),
                }
            ),
            retain=True,
        )

    async def _publish_scene_select(
        self, connection: ControlConnection, scenes: dict[str, str]
    ) -> None:
        settings = self._settings
        topic = self._config_topic("select", "scene", settings)
        if scenes:
            base = self.base_topic
            await connection.publish(
                topic,
                json.dumps(
                    {
                        **self._common("scene", "Start scene"),
                        "icon": "mdi:movie-open",
                        "command_topic": f"{base}/scene/set",
                        "state_topic": f"{base}/state",
                        "value_template": "{{ value_json.active_scene_name }}",
                        "options": list(scenes),
                    }
                ),
                retain=True,
            )
        else:
            await connection.publish(topic, "", retain=True)
        self._published_scenes = tuple(scenes)

    async def _remove_discovery(self, settings: HomeAssistantSettings) -> None:
        broker = self._broker()
        if not broker.host:
            return
        availability = f"{settings.topic_prefix}/{self.installation_id}/availability"
        try:
            async with asyncio.timeout(5):
                async with self._connector(broker, availability) as connection:
                    for component, key in self._entities():
                        await connection.publish(
                            self._config_topic(component, key, settings), "", retain=True
                        )
                    await connection.publish(availability, "offline", retain=True)
        except (aiomqtt.MqttError, TimeoutError):
            LOGGER.warning("could not remove Home Assistant discovery entities")

    def _load(self) -> HomeAssistantSettings:
        path = self.root / "settings.json"
        if not path.exists():
            return HomeAssistantSettings()
        try:
            return HomeAssistantSettings.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            LOGGER.error("Home Assistant settings could not be read; integration stays disabled")
            return HomeAssistantSettings()

    def _save(self, settings: HomeAssistantSettings) -> None:
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = self.root / f".pending-{uuid4().hex}"
            temporary.write_text(settings.model_dump_json(), encoding="utf-8")
            os.replace(temporary, self.root / "settings.json")
        except OSError as error:
            raise HomeAssistantError(
                "Home Assistant settings could not be saved; check controller storage."
            ) from error


class _BrokerChanged(Exception):
    pass


def _scene_options(status: dict[str, object]) -> dict[str, str]:
    operations = status.get("operations")
    raw = operations.get("scene_options") if isinstance(operations, dict) else None
    options: dict[str, str] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        name = str(item.get("name") or item["id"])
        label = name if name not in options else f"{name} ({item['id']})"
        options[label] = item["id"]
    return options


def _state(status: dict[str, object], scenes: dict[str, str]) -> dict[str, object]:
    operations = status.get("operations")
    mode = operations.get("mode") if isinstance(operations, dict) else None
    active = status.get("active_scene")
    loaded = status.get("loaded_catalog")
    loaded = loaded if isinstance(loaded, dict) else {}
    active_name = next((label for label, scene in scenes.items() if scene == active), None)
    return {
        "playback": status.get("playback_state") or ("starting" if active else "idle"),
        "active_scene": active,
        "active_scene_name": active_name or active,
        "mode": mode,
        "mode_label": MODE_LABELS.get(str(mode), str(mode)),
        "loaded_show": loaded.get("project_name") or loaded.get("project_id"),
        "loaded_revision": loaded.get("revision"),
        "degraded": bool(status.get("degraded")),
    }


class _AiomqttControl:
    def __init__(self, client: aiomqtt.Client) -> None:
        self._client = client

    async def publish(self, topic: str, payload: str, *, retain: bool = False) -> None:
        await self._client.publish(topic, payload, qos=1, retain=retain)

    async def subscribe(self, topic: str) -> None:
        await self._client.subscribe(topic, qos=1)

    async def messages(self) -> AsyncIterator[tuple[str, bytes]]:
        async for message in self._client.messages:
            yield str(message.topic), bytes(message.payload)


@asynccontextmanager
async def _connect_aiomqtt(
    config: FrigateMqttConfig, availability_topic: str
) -> AsyncIterator[ControlConnection]:
    password = config.password.get_secret_value() if config.password is not None else None
    async with aiomqtt.Client(
        hostname=config.host or "",
        port=config.port,
        username=config.username,
        password=password,
        identifier=f"cueverge-control-{uuid4().hex[:12]}",
        tls_context=ssl.create_default_context() if config.tls else None,
        will=aiomqtt.Will(availability_topic, "offline", qos=1, retain=True),
    ) as client:
        yield _AiomqttControl(client)
