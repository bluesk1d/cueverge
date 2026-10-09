import pytest

from cueverge.integrations.frigate import FrigateEventError, FrigateEventNormalizer


def test_normalizes_new_person_and_entered_zones() -> None:
    normalizer = FrigateEventNormalizer()

    events = normalizer.normalize(
        _payload(
            event_type="new",
            entered_zones=["Frontyard", "Porch"],
            current_zones=["Porch"],
        ),
        received_at_ms=10_500,
    )

    assert [event.event_type for event in events] == [
        "frigate.object.new",
        "frigate.zone.entered",
        "frigate.zone.entered",
    ]
    assert [event.attributes.get("zone") for event in events] == [
        None,
        "Frontyard",
        "Porch",
    ]
    assert all(event.attributes["label"] == "person" for event in events)
    assert all(event.occurred_at_ms == 10_000 for event in events)


def test_update_emits_only_new_zone_and_has_stable_identity() -> None:
    normalizer = FrigateEventNormalizer()
    payload = _payload(
        event_type="update",
        before_zones=["Frontyard"],
        entered_zones=["Frontyard", "Porch"],
        current_zones=["Porch"],
    )

    first = normalizer.normalize(payload, received_at_ms=10_500)
    second = normalizer.normalize(payload, received_at_ms=10_500)

    assert len(first) == 1
    assert first[0].event_type == "frigate.zone.entered"
    assert first[0].attributes["zone"] == "Porch"
    assert first[0].event_id == second[0].event_id


def test_end_and_non_person_filtering() -> None:
    normalizer = FrigateEventNormalizer()

    ended = normalizer.normalize(
        _payload(event_type="end", end_time=10.25),
        received_at_ms=10_500,
    )
    cat = normalizer.normalize(
        _payload(event_type="new", label="cat"),
        received_at_ms=10_500,
    )

    assert [event.event_type for event in ended] == ["frigate.object.end"]
    assert ended[0].occurred_at_ms == 10_250
    assert cat == ()


def test_update_without_zone_entry_emits_presence_update() -> None:
    normalizer = FrigateEventNormalizer()

    events = normalizer.normalize(
        _payload(
            event_type="update",
            before_zones=["Porch"],
            entered_zones=["Porch"],
            current_zones=["Porch"],
        ),
        received_at_ms=10_500,
    )

    assert [event.event_type for event in events] == ["frigate.object.updated"]


def test_rejects_invalid_payload() -> None:
    with pytest.raises(FrigateEventError, match="invalid Frigate event"):
        FrigateEventNormalizer().normalize("{}", received_at_ms=10_500)


def _payload(
    *,
    event_type: str,
    label: str = "person",
    before_zones: list[str] | None = None,
    entered_zones: list[str] | None = None,
    current_zones: list[str] | None = None,
    end_time: float | None = None,
) -> dict[str, object]:
    after = {
        "id": "event-1",
        "label": label,
        "camera": "porch_east",
        "start_time": 9.0,
        "frame_time": 10.0,
        "end_time": end_time,
        "entered_zones": entered_zones or [],
        "current_zones": current_zones or [],
    }
    before = (
        {
            **after,
            "entered_zones": before_zones,
            "current_zones": before_zones,
        }
        if before_zones is not None
        else None
    )
    return {"type": event_type, "before": before, "after": after}
