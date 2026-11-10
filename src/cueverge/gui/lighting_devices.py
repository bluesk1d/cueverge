"""Installation-owned network lighting devices: LIFX bulbs and WLED controllers.

Registrations are persisted under the controller GUI data root and restored at
startup. The GUI (Installation > Devices > Add a device) and MCP tools share this
service, so validation, revision checks and audit records are identical.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from cueverge.config import LifxResourceConfig, WledResourceConfig, validate_lan_ipv4
from cueverge.controller.runtime import ControllerRuntime, NetworkLightSink
from cueverge.gui.repository import GuiRepository
from cueverge.gui.wled_status import WledStatus, WledStatusObserver
from cueverge.lighting.ddp import DdpUdpSink
from cueverge.lighting.lifx import LifxUdpSink
from cueverge.lighting.sink import FrameSinkRouter

LightingDeviceConfig = Annotated[
    LifxResourceConfig | WledResourceConfig, Field(discriminator="kind")
]
# cast: mypy's view of Annotated unions as TypeAdapter input differs between runs.
lighting_device_adapter = TypeAdapter[LifxResourceConfig | WledResourceConfig](
    cast(Any, LightingDeviceConfig)
)
_legacy_adapter = TypeAdapter(tuple[LifxResourceConfig, ...])
DEVICE_KINDS: dict[str, str] = {
    "lifx": "LIFX bulb (stationary point light, LAN protocol)",
    "wled": "WLED controller (LED pixel output over DDP)",
}


class _StoredDevices(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    devices: tuple[LightingDeviceConfig, ...] = ()


class LightingDeviceError(Exception):
    """A rejected registration change. status mirrors the HTTP response code."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def _sink(config: LifxResourceConfig | WledResourceConfig) -> NetworkLightSink:
    if isinstance(config, LifxResourceConfig):
        return LifxUdpSink(
            **config.model_dump(exclude={"kind", "resource", "expected_pixel_count"})
        )
    return DdpUdpSink(**config.model_dump(exclude={"kind", "resource"}))


def _same_driver(current: object, config: LifxResourceConfig | WledResourceConfig) -> bool:
    if isinstance(config, LifxResourceConfig):
        return (
            isinstance(current, LifxUdpSink)
            and current.host == config.host
            and current.target.hex() == config.target
        )
    return (
        isinstance(current, DdpUdpSink)
        and current.host == config.host
        and current.port == config.port
    )


class LightingDeviceRegistry:
    FILE_NAME = "lighting-devices.json"
    LEGACY_FILE_NAME = "lifx-outputs.json"

    def __init__(
        self,
        root: Path,
        runtime: ControllerRuntime,
        repository: GuiRepository | None = None,
        wled_status: WledStatusObserver | None = None,
    ) -> None:
        self.root = root
        self.path = root / self.FILE_NAME
        self.runtime = runtime
        self.repository = repository
        self.wled_status = wled_status or WledStatusObserver()

    def _read(self) -> _StoredDevices:
        if self.path.exists():
            return _StoredDevices.model_validate_json(self.path.read_bytes())
        legacy = self.root / self.LEGACY_FILE_NAME
        if legacy.exists():
            return _StoredDevices(
                revision=1, devices=_legacy_adapter.validate_json(legacy.read_bytes())
            )
        return _StoredDevices()

    def _write(self, stored: _StoredDevices) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(stored.model_dump(mode="json"), indent=2))
        temporary.replace(self.path)

    def load(self) -> None:
        """Restore registered devices into the runtime, migrating the LIFX-only file."""
        stored = self._read()
        legacy = self.root / self.LEGACY_FILE_NAME
        if not self.path.exists() and legacy.exists():
            self._write(stored)
            legacy.replace(legacy.with_suffix(".json.migrated"))
        for config in stored.devices:
            if isinstance(self.runtime.sink, FrameSinkRouter) and _same_driver(
                self.runtime.sink.sinks.get(config.resource), config
            ):
                continue
            sink = _sink(config)
            try:
                self.runtime.add_lighting_output(
                    config.resource, sink, target=config.kind, restore=True
                )
            except Exception:
                sink.close()
                raise

    def view(self) -> dict[str, object]:
        stored = self._read()
        transport = self.runtime.lighting_transport_status()
        return {
            "revision": stored.revision,
            "kinds": DEVICE_KINDS,
            "devices": [
                {
                    **config.model_dump(mode="json"),
                    "pixel_count": config.expected_pixel_count,
                    "in_use": self.runtime.lighting_output_in_use(config.resource),
                    "transport": transport.get(config.resource),
                }
                for config in stored.devices
            ],
        }

    def _check_revision(self, stored: _StoredDevices, expected_revision: int | None) -> None:
        if expected_revision is not None and expected_revision != stored.revision:
            raise LightingDeviceError(
                f"Lighting devices changed (revision {stored.revision}); reload and retry", 412
            )

    def _commit(
        self, previous: _StoredDevices, devices: tuple[LifxResourceConfig | WledResourceConfig, ...]
    ) -> _StoredDevices:
        stored = _StoredDevices(revision=previous.revision + 1, devices=devices)
        self._write(stored)
        return stored

    def _rollback(self, previous: _StoredDevices) -> None:
        if previous.revision == 0 and not previous.devices:
            self.path.unlink(missing_ok=True)
        else:
            self._write(previous)

    def _audit(self, actor: str, action: str, target: str, context: dict[str, JsonValue]) -> None:
        if self.repository is None:
            return
        self.repository.append_audit(
            actor=actor,
            action=f"installation.lighting_device.{action}",
            target=target,
            result="success",
            occurred_at=datetime.now(UTC).isoformat(),
            authorization_context=context,
        )

    def create(
        self,
        config: LifxResourceConfig | WledResourceConfig,
        *,
        expected_revision: int | None = None,
        actor: str = "controller",
        context: dict[str, JsonValue] | None = None,
    ) -> dict[str, object]:
        previous = self._read()
        self._check_revision(previous, expected_revision)
        if any(
            c.resource == config.resource
            or c.host == config.host
            or (
                isinstance(c, LifxResourceConfig)
                and isinstance(config, LifxResourceConfig)
                and c.target == config.target
            )
            for c in previous.devices
        ):
            raise LightingDeviceError("Resource, target or address is already registered")
        # Persist before exposing the output; a rejected runtime change restores the
        # original file and closes the unowned socket.
        sink = _sink(config)
        written = False
        try:
            self._commit(previous, (*previous.devices, config))
            written = True
            self.runtime.add_lighting_output(config.resource, sink, target=config.kind)
        except Exception as error:
            sink.close()
            if written:
                self._rollback(previous)
            raise _translate(error) from error
        self._audit(actor, "create", config.resource, {"kind": config.kind, **(context or {})})
        return self._device(config.resource)

    def update(
        self,
        resource: str,
        config: LifxResourceConfig | WledResourceConfig,
        *,
        expected_revision: int,
        actor: str = "controller",
        context: dict[str, JsonValue] | None = None,
    ) -> dict[str, object]:
        previous = self._read()
        self._check_revision(previous, expected_revision)
        current = next((c for c in previous.devices if c.resource == resource), None)
        if current is None:
            raise LightingDeviceError("Registered lighting device not found", 404)
        if config.resource != resource or config.kind != current.kind:
            raise LightingDeviceError("A device's resource name and type cannot change", 422)
        if config.expected_pixel_count != current.expected_pixel_count:
            raise LightingDeviceError("Remove and re-add the device to change its pixel count", 422)
        if any(
            c.resource != resource
            and (
                c.host == config.host
                or (
                    isinstance(c, LifxResourceConfig)
                    and isinstance(config, LifxResourceConfig)
                    and c.target == config.target
                )
            )
            for c in previous.devices
        ):
            raise LightingDeviceError("Target or address is already registered")
        sink = _sink(config)
        written = False
        try:
            self._commit(
                previous,
                tuple(config if c.resource == resource else c for c in previous.devices),
            )
            written = True
            self.runtime.replace_lighting_output(resource, sink)
        except Exception as error:
            sink.close()
            if written:
                self._rollback(previous)
            raise _translate(error) from error
        self._audit(actor, "update", resource, {"kind": config.kind, **(context or {})})
        return self._device(resource)

    def remove(
        self,
        resource: str,
        *,
        expected_revision: int,
        actor: str = "controller",
        context: dict[str, JsonValue] | None = None,
    ) -> None:
        previous = self._read()
        self._check_revision(previous, expected_revision)
        current = next((c for c in previous.devices if c.resource == resource), None)
        if current is None:
            raise LightingDeviceError("Registered lighting device not found", 404)
        written = False
        try:
            self._commit(previous, tuple(c for c in previous.devices if c.resource != resource))
            written = True
            self.runtime.remove_lighting_output(resource)
        except Exception as error:
            if written:
                self._rollback(previous)
            raise _translate(error) from error
        self._audit(actor, "remove", resource, {"kind": current.kind, **(context or {})})

    async def detect_wled(self, host: str) -> WledStatus:
        try:
            address = validate_lan_ipv4(host)
        except ValueError as error:
            raise LightingDeviceError(str(error), 422) from error
        return await self.wled_status.observe(address)

    def _device(self, resource: str) -> dict[str, object]:
        view = self.view()
        devices = view["devices"]
        assert isinstance(devices, list)
        device = next(d for d in devices if d["resource"] == resource)
        return {**device, "revision": view["revision"]}


def _translate(error: Exception) -> Exception:
    if isinstance(error, LightingDeviceError):
        return error
    if isinstance(error, (ValueError, RuntimeError, OSError)):
        return LightingDeviceError(str(error))
    return error
