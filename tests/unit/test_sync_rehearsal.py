from pathlib import Path

from cueverge.gui.repository import GuiRepository
from cueverge.gui.sync_rehearsal import SyncRehearsalService


def test_sync_rehearsal_admission_checks_tolerance_age_and_context(
    tmp_path: Path,
) -> None:
    repository = GuiRepository(tmp_path / "gui.sqlite3")
    repository.migrate()
    service = SyncRehearsalService(repository)
    context = {
        "projector.left": {
            "device_id": "client-02",
            "firmware": "1.0",
            "routing": "left",
        },
        "projector.right": {
            "device_id": "client-01",
            "firmware": "1.0",
            "routing": "right",
        },
    }
    resources = ("projector.left", "projector.right")

    assert (
        service.evaluate(
            resources=resources,
            current_context=context,
            now="2026-10-15T12:00:00Z",
        ).code
        == "sync_rehearsal_missing"
    )
    passing = service.record(
        resources=resources,
        start_error_ms=20,
        drift_ms_per_minute=10,
        measurement_duration_seconds=600,
        context=context,
        notes="Two-projector rehearsal",
        actor="operator",
        measured_at="2026-10-15T12:00:00Z",
    )
    admitted = service.evaluate(
        resources=tuple(reversed(resources)),
        current_context=context,
        now="2026-11-14T12:00:00Z",
    )
    assert admitted.allowed is True
    assert admitted.rehearsal == passing

    assert (
        service.evaluate(
            resources=resources,
            current_context=context,
            now="2026-11-14T12:00:01Z",
        ).code
        == "sync_rehearsal_stale"
    )
    changed = {**context, "projector.right": {**context["projector.right"], "firmware": "1.1"}}
    assert (
        service.evaluate(
            resources=resources,
            current_context=changed,
            now="2026-10-16T12:00:00Z",
        ).code
        == "sync_rehearsal_context_changed"
    )

    service.record(
        resources=resources,
        start_error_ms=20.1,
        drift_ms_per_minute=5,
        measurement_duration_seconds=600,
        context=context,
        notes=None,
        actor="operator",
        measured_at="2026-10-17T12:00:00Z",
    )
    assert (
        service.evaluate(
            resources=resources,
            current_context=context,
            now="2026-10-17T13:00:00Z",
        ).code
        == "sync_start_error_exceeded"
    )
    service.record(
        resources=resources,
        start_error_ms=10,
        drift_ms_per_minute=10.1,
        measurement_duration_seconds=600,
        context=context,
        notes=None,
        actor="operator",
        measured_at="2026-10-18T12:00:00Z",
    )
    assert (
        service.evaluate(
            resources=resources,
            current_context=context,
            now="2026-10-18T13:00:00Z",
        ).code
        == "sync_drift_exceeded"
    )
