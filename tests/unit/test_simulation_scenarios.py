import asyncio
from pathlib import Path

from cueverge.core.loader import load_show_package
from cueverge.gui.simulation_scenarios import SimulationScenarioService


def test_saved_simulation_scenario_repeats_without_hardware(tmp_path: Path) -> None:
    service = SimulationScenarioService(tmp_path / "scenarios")
    catalog = load_show_package(Path("shows/demo"))
    resource = next(
        output.definition.resource
        for scene in catalog.scenes.values()
        for output in scene.lighting_outputs
    )
    scenario = service.save(
        project_id="demo-project",
        name="Disconnect and output failure",
        source_revision=3,
        steps=(
            {"kind": "advance", "delta_ms": 100},
            {"kind": "disconnect"},
            {"kind": "reconnect"},
            {"kind": "output_failure", "resource": resource, "failed": True},
            {"kind": "output_failure", "resource": resource, "failed": False},
        ),
        expected={"clock_ms": 100, "scene_history_contains": ["ambient"]},
    )

    loaded = service.get("demo-project", scenario.id)
    first = asyncio.run(service.run(loaded, catalog))
    second = asyncio.run(service.run(loaded, catalog))

    assert first["passed"] is True
    assert first["actual"] == second["actual"]
    assert first["hardware_adapters_opened"] is False
    assert first["hardware_commands_sent"] is False
    actual = first["actual"]
    assert isinstance(actual, dict)
    status = actual["status"]
    assert isinstance(status, dict)
    assert status["lighting_output_error_count"] == 1
    assert service.list("demo-project") == (loaded,)
