import json
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cueverge.core.events import ExternalEvent


class FrigateEventError(ValueError):
    pass


class _FrigateObject(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    camera: str = Field(min_length=1)
    start_time: float = Field(ge=0)
    end_time: float | None = Field(default=None, ge=0)
    frame_time: float | None = Field(default=None, ge=0)
    current_zones: tuple[str, ...] = ()
    entered_zones: tuple[str, ...] = ()


class _FrigatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["new", "update", "end"]
    before: _FrigateObject | None = None
    after: _FrigateObject


class FrigateEventNormalizer:
    def __init__(self, *, accepted_labels: tuple[str, ...] = ("person",)) -> None:
        if not accepted_labels:
            raise ValueError("accepted_labels must not be empty")
        self.accepted_labels = frozenset(accepted_labels)

    def normalize(
        self,
        payload: bytes | str | dict[str, Any],
        *,
        received_at_ms: int,
    ) -> tuple[ExternalEvent, ...]:
        message = self._parse(payload)
        detected = message.after
        if detected.label not in self.accepted_labels:
            return ()

        occurred_at_ms = self._occurred_at_ms(message, received_at_ms)
        attributes: dict[str, str | int | float | bool] = {
            "source": "frigate",
            "object_id": detected.id,
            "label": detected.label,
            "camera": detected.camera,
        }
        events: list[ExternalEvent] = []
        if message.type == "new":
            events.append(
                self._event(
                    detected.id,
                    "frigate.object.new",
                    occurred_at_ms,
                    attributes,
                )
            )
        elif message.type == "end":
            events.append(
                self._event(
                    detected.id,
                    "frigate.object.end",
                    occurred_at_ms,
                    attributes,
                )
            )
            return tuple(events)

        previous_zones = set(message.before.entered_zones) if message.before else set()
        for zone in sorted(set(detected.entered_zones).difference(previous_zones)):
            events.append(
                self._event(
                    detected.id,
                    "frigate.zone.entered",
                    occurred_at_ms,
                    {**attributes, "zone": zone},
                )
            )
        if message.type == "update" and not events:
            events.append(
                self._event(
                    detected.id,
                    "frigate.object.updated",
                    occurred_at_ms,
                    attributes,
                )
            )
        return tuple(events)

    @staticmethod
    def _parse(payload: bytes | str | dict[str, Any]) -> _FrigatePayload:
        try:
            if isinstance(payload, dict):
                return _FrigatePayload.model_validate(payload)
            return _FrigatePayload.model_validate_json(payload)
        except (ValidationError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FrigateEventError(f"invalid Frigate event: {error}") from error

    @staticmethod
    def _occurred_at_ms(message: _FrigatePayload, received_at_ms: int) -> int:
        detected = message.after
        timestamp = (
            detected.end_time
            if message.type == "end" and detected.end_time is not None
            else detected.frame_time
            if detected.frame_time is not None
            else detected.start_time
        )
        occurred_at_ms = round(timestamp * 1000)
        return min(occurred_at_ms, received_at_ms)

    @staticmethod
    def _event(
        object_id: str,
        event_type: str,
        occurred_at_ms: int,
        attributes: dict[str, str | int | float | bool],
    ) -> ExternalEvent:
        suffix = str(attributes.get("zone", "object"))
        raw_id = f"frigate:{object_id}:{event_type}:{occurred_at_ms}:{suffix}"
        event_id = (
            raw_id if len(raw_id) <= 128 else f"frigate:{sha256(raw_id.encode()).hexdigest()}"
        )
        return ExternalEvent(
            event_id=event_id,
            event_type=event_type,
            occurred_at_ms=occurred_at_ms,
            idempotency_key=event_id,
            attributes=attributes,
        )
