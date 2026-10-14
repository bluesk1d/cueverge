from pathlib import Path

import pytest

from cueverge.gui.contracts import Role
from cueverge.gui.repository import (
    ActiveInstallationDeleteError,
    GuiRepository,
    InstallationConflictError,
)
from cueverge.protocol.device_v2 import DeviceKind


def test_installation_crud_is_revisioned_and_protects_runtime_active_record(
    tmp_path: Path,
) -> None:
    repository = GuiRepository(tmp_path / "gui.sqlite3")
    repository.migrate()
    home = repository.ensure_runtime_installation(
        name="Home",
        occurred_at="2026-10-14T00:00:00Z",
    )
    draft = repository.create_installation(
        name="Warehouse",
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T00:01:00Z",
    )

    renamed = repository.update_installation(
        installation_id=draft.id,
        expected_revision=1,
        name="Warehouse Test",
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T00:02:00Z",
    )
    assert renamed.revision == 2
    assert renamed.name == "Warehouse Test"
    with pytest.raises(InstallationConflictError):
        repository.update_installation(
            installation_id=draft.id,
            expected_revision=1,
            name="Stale",
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-14T00:03:00Z",
        )
    with pytest.raises(ActiveInstallationDeleteError):
        repository.delete_installation(
            installation_id=home.id,
            expected_revision=1,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-14T00:04:00Z",
        )

    repository.delete_installation(
        installation_id=draft.id,
        expected_revision=2,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T00:05:00Z",
    )
    assert repository.list_installations() == (home,)


def test_discovered_endpoint_is_untrusted_and_rejectable(tmp_path: Path) -> None:
    repository = GuiRepository(tmp_path / "gui.sqlite3")
    repository.migrate()
    discovered = repository.upsert_discovered_endpoint(
        base_url="https://controller.local",
        device_kind=DeviceKind.DOWNSTREAM_CONTROLLER,
        software_version="0.1.0",
        protocol_minimum=2,
        protocol_maximum=2,
        capability_kinds=("downstream_control",),
        compatible=True,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T00:00:00Z",
    )

    assert discovered.state == "discovered"
    assert discovered.compatible
    repository.create_pairing_token(
        token="linked-pairing-token",
        device_kind=DeviceKind.DOWNSTREAM_CONTROLLER,
        created_by="admin",
        created_at="2026-10-14T00:00:30Z",
        expires_at=2_000_000_000,
        role=Role.ADMINISTRATOR,
        discovered_endpoint_id=discovered.id,
    )
    assert repository.get_discovered_endpoint(discovered.id).state == "pairing"
    rejected = repository.reject_discovered_endpoint(
        endpoint_id=discovered.id,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-14T00:01:00Z",
    )
    assert rejected.state == "rejected"
    assert not repository.pairing_token_is_usable(
        token="linked-pairing-token",
        now=1_900_000_000,
        device_kind=DeviceKind.DOWNSTREAM_CONTROLLER,
    )
