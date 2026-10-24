import asyncio
import json
import logging
import os
import stat
import time
from collections.abc import Awaitable, Callable, Mapping
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from cueverge.config import FrigateMqttConfig
from cueverge.core.encounter import ObservedEventOutcome
from cueverge.core.events import ExternalEvent
from cueverge.core.models import SceneCatalog
from cueverge.gui.contracts import ConfigureInputSourceRequest, InputSourceSettings
from cueverge.integrations.frigate import FrigateEventError, FrigateEventNormalizer
from cueverge.integrations.mqtt import (
    MAX_INPUT_PAYLOAD_BYTES,
    FrigateMqttIngress,
    InputDelivery,
    MqttConnector,
)
from cueverge.observation.presence import AudiencePresenceEstimator

LOGGER = logging.getLogger(__name__)


class InputSourceError(RuntimeError):
    pass


class InputRuntime(Protocol):
    catalog: SceneCatalog

    def status(self) -> dict[str, object]: ...


class _StoredSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: InputSourceSettings
    credential_id: str | None = None
    revision: int = Field(default=1, ge=1)
    installation_id: str | None = None


class InputSourceService:
    def __init__(
        self,
        root: Path,
        runtime: InputRuntime,
        *,
        mode: Literal["gui", "environment"] = "gui",
        environment: FrigateMqttConfig | None = None,
        connector: MqttConnector | None = None,
        handler: Callable[[ExternalEvent], Awaitable[ObservedEventOutcome]] | None = None,
        installation_id: str | None = None,
    ) -> None:
        self.root = root
        self.runtime = runtime
        self.mode = mode
        self.installation_id = installation_id
        self._connector = connector
        self._handler = handler
        self._lock = asyncio.Lock()
        self._started = False
        self._credential_id: str | None = None
        self._revision = 0
        self._storage_error: str | None = None
        self._config = (
            environment
            if mode == "environment" and environment is not None
            else FrigateMqttConfig()
        )
        if mode == "gui" and root.exists():
            try:
                self._ensure_root()
                if (root / "settings.json").exists():
                    stored = _StoredSource.model_validate_json(self._read("settings.json"))
                    if stored.installation_id != self.installation_id:
                        raise InputSourceError("Input settings belong to a different installation")
                    password = self._read_credential(stored.credential_id)
                    self._config = self._build(stored.settings, password)
                    self._credential_id = stored.credential_id
                    self._revision = stored.revision
                elif any(root.iterdir()):
                    raise InputSourceError("Protected input storage is incomplete")
            except (OSError, ValueError, InputSourceError):
                self._storage_error = (
                    "Saved input settings or protected credentials cannot be read. Restore "
                    "owner-only permissions and the matching credential backup, then restart."
                )
                LOGGER.error("camera input protected storage could not be loaded")
        self._ingress = self._make_ingress(self._config)

    def _make_ingress(self, config: FrigateMqttConfig) -> FrigateMqttIngress | None:
        return (
            FrigateMqttIngress(config, self._deliver, connector=self._connector)
            if config.enabled
            else None
        )

    async def _deliver(self, event: ExternalEvent) -> InputDelivery:
        return await deliver_input_event(
            self.runtime,
            self._handler,
            event,
            time.time_ns() // 1_000_000,
        )

    def start(self) -> None:
        self._started = True
        if self._ingress is not None:
            self._ingress.start()

    def broker_config(self) -> FrigateMqttConfig:
        """Current broker connection settings, shared with the Home Assistant bridge."""
        return self._config

    async def stop(self) -> None:
        self._started = False
        if self._ingress is not None:
            await self._ingress.stop()

    @staticmethod
    def _build(settings: InputSourceSettings, password: SecretStr | None) -> FrigateMqttConfig:
        try:
            return FrigateMqttConfig(**settings.model_dump(), password=password)
        except ValidationError:
            raise InputSourceError(
                "A stored or replacement password requires a broker username"
            ) from None

    async def configure(self, request: ConfigureInputSourceRequest) -> dict[str, object]:
        if self.mode != "gui":
            raise InputSourceError(
                "This source is environment-managed; change controller environment settings "
                "and restart"
            )
        if self._storage_error:
            raise InputSourceError(self._storage_error)
        async with self._lock:
            if (
                request.expected_revision is not None
                and request.expected_revision != self._revision
            ):
                raise InputSourceError(
                    "Input settings changed elsewhere. Reload saved settings before saving again."
                )
            settings = InputSourceSettings.model_validate(
                request.model_dump(exclude={"password", "clear_password", "expected_revision"})
            )
            password = None if request.clear_password else request.password or self._config.password
            candidate = self._build(settings, password)
            credential_id = self._credential_id
            new_credential: str | None = None
            try:
                self._ensure_root()
                if request.password is not None:
                    new_credential = f"credential-{uuid4().hex}.json"
                    self._write(
                        new_credential,
                        json.dumps({"password": request.password.get_secret_value()}),
                    )
                    credential_id = new_credential
                elif request.clear_password:
                    credential_id = None
                stored = _StoredSource(
                    settings=settings,
                    credential_id=credential_id,
                    revision=self._revision + 1,
                    installation_id=self.installation_id,
                )
                self._write("settings.json", stored.model_dump_json())
            except (OSError, InputSourceError):
                if new_credential is not None and (self.root / new_credential).exists():
                    (self.root / new_credential).unlink()
                raise InputSourceError(
                    "Protected input settings could not be saved; active settings are unchanged. "
                    "Check storage ownership, permissions and free space."
                ) from None
            if self._ingress is not None:
                await self._ingress.stop()
            previous_credential = self._credential_id
            self._config = candidate
            self._revision += 1
            self._credential_id = credential_id
            self._ingress = self._make_ingress(candidate)
            if self._started and self._ingress is not None:
                self._ingress.start()
            if previous_credential and previous_credential != credential_id:
                try:
                    (self.root / previous_credential).unlink()
                except OSError:
                    LOGGER.error("retired camera input credential could not be removed")
                    raise InputSourceError(
                        "Settings applied, but a retired credential could not be removed; "
                        "ask the operator to inspect protected input storage"
                    ) from None
            return self.snapshot()

    def snapshot(self) -> dict[str, object]:
        now = time.time_ns() // 1_000_000
        ingress = self._ingress
        audience = self.runtime.catalog.audience
        freshness_ms = audience.observation_freshness_ms if audience is not None else 5000
        last_event = ingress.last_event_at_ms if ingress else None
        status = self.runtime.status()
        settings = self._config.model_dump(
            mode="json",
            exclude={"password", "reconnect_delay_ms"},
        )
        settings["host"] = self._config.host or ""
        return {
            "observed_at_ms": now,
            "installation_id": self.installation_id,
            "management": self.mode,
            "configuration_revision": self._revision,
            "validation_context_digest": self._validation_context_digest(),
            "settings": settings,
            "credential_present": self._config.password is not None,
            "state": "unavailable"
            if self._storage_error
            or (self._started and ingress is not None and not ingress.running)
            else "disabled"
            if not self._config.enabled
            else "subscribed"
            if ingress and ingress.connected
            else "connecting"
            if self._started
            else "stopped",
            "error": self._storage_error or (ingress.last_error if ingress else None),
            "freshness": "no_events"
            if last_event is None
            else "fresh"
            if now - last_event <= freshness_ms
            else "stale",
            "freshness_ms": freshness_ms,
            "messages_received": ingress.messages_received if ingress else 0,
            "invalid_messages": ingress.invalid_messages if ingress else 0,
            "filtered_messages": ingress.filtered_messages if ingress else 0,
            "events_delivered": ingress.events_emitted if ingress else 0,
            "controller_accepted": ingress.controller_accepted if ingress else 0,
            "controller_rejected": ingress.controller_rejected if ingress else 0,
            "audience_matches": ingress.audience_matches if ingress else 0,
            "connection_attempts": ingress.connection_attempts if ingress else 0,
            "last_received_at_ms": ingress.last_received_at_ms if ingress else None,
            "recent_events": list(ingress.recent) if ingress else [],
            "loaded_rules": {
                "audience": audience.model_dump(mode="json") if audience is not None else None,
                "encounter": self.runtime.catalog.encounter is not None,
                "opportunities": self.runtime.catalog.opportunities is not None,
                "show_mode": status.get("show_mode"),
                "audience_state": status.get("audience"),
            },
            "recovery": (
                "Check Frigate MQTT publishing and this topic if no events arrive. Compare "
                "camera/zone names with the loaded show's visitor rules. Connection or accepted "
                "input does not guarantee scene playback."
            ),
        }

    def validate_payload(self, payload: Mapping[str, object]) -> dict[str, object]:
        now = time.time_ns() // 1_000_000
        if len(json.dumps(payload).encode()) > MAX_INPUT_PAYLOAD_BYTES:
            raise InputSourceError("Test payload exceeds the 256 KiB input limit")
        try:
            events = FrigateEventNormalizer(accepted_labels=self._config.accepted_labels).normalize(
                json.dumps(payload),
                received_at_ms=now,
            )
        except FrigateEventError:
            raise InputSourceError(
                "Invalid Frigate payload. Expected type new/update/end and an after object "
                "with id, label, camera and start_time."
            ) from None
        definition = self.runtime.catalog.audience
        estimator = AudiencePresenceEstimator(definition) if definition is not None else None
        results: list[dict[str, object]] = []
        for event in events:
            stale = (
                definition is not None
                and now - event.occurred_at_ms > definition.observation_freshness_ms
            )
            update = estimator.ingest(event, now) if estimator is not None and not stale else None
            results.append(
                {
                    "event_type": event.event_type,
                    "camera": event.attributes.get("camera"),
                    "zone": event.attributes.get("zone"),
                    "stale": stale,
                    "audience_relevant": update.relevant if update is not None else None,
                }
            )
        return {
            "simulation": True,
            "validation_context_digest": self._validation_context_digest(),
            "installation_id": self.installation_id,
            "hardware_commands_sent": False,
            "uses": "loaded_controller_rules",
            "label_filtered": not events,
            "audience_configured": definition is not None,
            "events": results,
            "note": (
                "Isolated normalization and visitor-rule check only. This does not connect "
                "to MQTT, inject a live event, change visitor state or prove end-to-end "
                "reception or scene playback."
            ),
        }

    def _validation_context_digest(self) -> str:
        audience = self.runtime.catalog.audience
        context = {
            "installation_id": self.installation_id,
            "configuration_revision": self._revision,
            "accepted_labels": self._config.accepted_labels,
            "audience": audience.model_dump(mode="json") if audience is not None else None,
        }
        return sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()

    def _ensure_root(self) -> None:
        if os.name != "posix":
            raise InputSourceError(
                "Protected GUI credential storage requires enforced POSIX owner-only "
                "permissions; use environment management on this platform"
            )
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = self.root.parent.stat()
        if parent.st_mode & 0o022 or parent.st_uid != os.geteuid():
            raise InputSourceError(
                "The controller data directory must be controller-owned and not "
                "group/world-writable"
            )
        self._check(self.root, directory=True)

    @staticmethod
    def _check(path: Path, *, directory: bool = False) -> None:
        info = path.lstat()
        correct_kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not correct_kind or info.st_mode & 0o077 or info.st_uid != os.geteuid():
            raise InputSourceError(
                "Input storage must be owner-only, owned by the controller, and not a symbolic link"
            )

    def _read(self, name: str) -> str:
        self._ensure_root()
        path = self.root / name
        self._check(path)
        with path.open(encoding="utf-8") as source:
            return source.read()

    def _read_credential(self, name: str | None) -> SecretStr | None:
        if name is None:
            return None
        if (
            not name.startswith("credential-")
            or not name.endswith(".json")
            or len(name) != 48
            or "/" in name
            or "\\" in name
        ):
            raise InputSourceError("Invalid stored credential reference")
        value = json.loads(self._read(name))
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("password"), str)
            or not value["password"]
        ):
            raise InputSourceError("Invalid stored credential")
        return SecretStr(value["password"])

    def _write(self, name: str, content: str) -> None:
        temporary = self.root / f".pending-{uuid4().hex}"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.root / name)
        finally:
            temporary.unlink(missing_ok=True)


async def deliver_input_event(
    runtime: InputRuntime,
    handler: Callable[[ExternalEvent], Awaitable[ObservedEventOutcome]] | None,
    event: ExternalEvent,
    received_at_ms: int,
) -> InputDelivery:
    audience = runtime.catalog.audience
    if (
        audience is not None
        and received_at_ms - event.occurred_at_ms > audience.observation_freshness_ms
    ):
        return InputDelivery(False, "stale_observation", False)
    if handler is None:
        return InputDelivery(False, "controller_unavailable", None)
    observed = await handler(event)
    result = observed.outcome
    matched = observed.audience_relevant
    reason = result.reason
    if audience is not None and not matched and reason in {None, "irrelevant"}:
        if event.attributes.get("label") not in audience.labels:
            reason = "audience_label_filtered"
        elif audience.cameras and event.attributes.get("camera") not in audience.cameras:
            reason = "camera_not_matched"
        elif audience.zones and event.attributes.get("zone") not in audience.zones:
            reason = "waiting_for_matching_zone"
        else:
            reason = "observation_not_matched"
    return InputDelivery(result.accepted, reason, matched)
