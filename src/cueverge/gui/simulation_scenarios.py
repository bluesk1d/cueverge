import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from pydantic import JsonValue

from cueverge.core.models import SceneCatalog
from cueverge.lighting.sink import RenderedFrame
from cueverge.simulation import ShowSimulation


class SimulationScenarioError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredSimulationScenario:
    id: str
    project_id: str
    name: str
    source_revision: int
    steps: tuple[dict[str, JsonValue], ...]
    expected: dict[str, JsonValue]
    created_at: str
    updated_at: str


class SimulationScenarioService:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(
        self,
        *,
        project_id: str,
        name: str,
        source_revision: int,
        steps: tuple[dict[str, JsonValue], ...],
        expected: dict[str, JsonValue],
    ) -> StoredSimulationScenario:
        scenario_id = str(uuid4())
        occurred_at = datetime.now(UTC).isoformat()
        scenario = StoredSimulationScenario(
            id=scenario_id,
            project_id=project_id,
            name=name,
            source_revision=source_revision,
            steps=steps,
            expected=expected,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        directory = self.root / project_id
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{scenario_id}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(_scenario_document(scenario), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
        return scenario

    def list(self, project_id: str) -> tuple[StoredSimulationScenario, ...]:
        directory = self.root / project_id
        if not directory.is_dir():
            return ()
        return tuple(
            sorted(
                (self._load(path) for path in directory.glob("*.json")),
                key=lambda scenario: scenario.created_at,
                reverse=True,
            )
        )

    def get(self, project_id: str, scenario_id: str) -> StoredSimulationScenario:
        if Path(scenario_id).name != scenario_id:
            raise SimulationScenarioError("simulation scenario ID is invalid")
        path = self.root / project_id / f"{scenario_id}.json"
        if not path.is_file():
            raise SimulationScenarioError("simulation scenario does not exist")
        return self._load(path)

    async def run(
        self,
        scenario: StoredSimulationScenario,
        catalog: SceneCatalog,
    ) -> dict[str, JsonValue]:
        visitor_workflow = any(step.get("kind") == "visitor" for step in scenario.steps)
        origin = 600000 if visitor_workflow else 0
        if visitor_workflow:
            duration = sum(
                _integer(step.get("delta_ms"))
                for step in scenario.steps
                if step.get("kind") == "advance"
            )
            boundaries = duration // min(
                scene.definition.duration_ms for scene in catalog.scenes.values()
            )
            work = sum(
                output.pixel_count * (len(output.definition.timeline) + 1)
                for scene in catalog.scenes.values()
                for output in scene.lighting_outputs
            )
            if (
                len(scenario.steps) > 200
                or duration > 600000
                or boundaries > 1000
                or 2 * work * (boundaries + len(scenario.steps) + 1) > 20000000
            ):
                raise SimulationScenarioError(
                    "Visitor scenario exceeds the interactive step/time/pixel budget"
                )
        simulation = ShowSimulation(catalog, automatic_rendering=not visitor_workflow)
        simulation.clock.now_ms = origin
        decisions: list[str] = []
        checkpoints: list[dict[str, object]] = []
        try:
            if catalog.program is None:
                await simulation.operator_start(sorted(catalog.scenes)[0])
            else:
                await simulation.start()
            for step in scenario.steps:
                if visitor_workflow:
                    from cueverge.gui.contracts import SimulationStepRequest
                    from cueverge.gui.interaction_preview import _apply_step, _trace_status

                    observed = await _apply_step(
                        simulation, SimulationStepRequest.model_validate(step)
                    )
                    if simulation.runtime.active_scene is not None:
                        simulation.report_position()
                        simulation.runtime.render_frame()
                        await simulation.runtime.wait_for_lifecycle_idle()
                        simulation._sync_active()
                        await simulation._acknowledge_prepares()
                    checkpoints.append(
                        {
                            "at_ms": simulation.clock.now_ms - origin,
                            "observations": observed,
                            "status": _trace_status(simulation),
                        }
                    )
                    continue
                kind = _string(step.get("kind"))
                if kind == "advance":
                    await simulation.advance(_integer(step.get("delta_ms")))
                elif kind == "observe":
                    await simulation.observe(
                        _string(step.get("event_type")),
                        attributes=cast(
                            dict[str, str | int | float | bool],
                            _mapping(step.get("attributes")),
                        ),
                    )
                elif kind == "operator_start":
                    await simulation.operator_start(_string(step.get("scene_id")))
                elif kind == "request_scene":
                    decisions.append(await simulation.request_scene(_string(step.get("scene_id"))))
                elif kind == "disconnect":
                    simulation.disconnect_agent(_optional_string(step.get("agent_id")))
                elif kind == "reconnect":
                    simulation.reconnect_agent(_optional_string(step.get("agent_id")))
                elif kind == "output_failure":
                    resource = _string(step.get("resource"))
                    simulation.set_output_failure(
                        resource,
                        failed=_boolean(step.get("failed")),
                    )
                    if simulation.runtime.active_scene is not None:
                        simulation.report_position()
                        simulation.runtime.render_frame()
                else:
                    raise SimulationScenarioError(f"unsupported simulation step kind: {kind}")
            status = _deterministic_status(simulation.runtime.status())
            actual = cast(
                dict[str, JsonValue],
                {
                    "clock_ms": simulation.clock.now_ms - origin,
                    **({"visitor_checkpoints": checkpoints} if visitor_workflow else {}),
                    "scene_history": list(simulation.scene_history),
                    "request_decisions": decisions,
                    "lighting_frame_counts": {
                        resource: len(sink.frames) for resource, sink in simulation.sinks.items()
                    },
                    "lighting_frame_digests": {
                        resource: _frame_digest(sink.frames)
                        for resource, sink in simulation.sinks.items()
                    },
                    "status": status,
                },
            )
            mismatches = _expected_mismatches(scenario.expected, actual)
            return {
                "scenario_id": scenario.id,
                "source_revision": scenario.source_revision,
                "passed": not mismatches,
                "mismatches": mismatches,
                "actual": actual,
                "hardware_adapters_opened": False,
                "hardware_commands_sent": False,
            }
        finally:
            await simulation.shutdown()

    def _load(self, path: Path) -> StoredSimulationScenario:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SimulationScenarioError(
                f"simulation scenario is unreadable: {path.name}"
            ) from error
        if not isinstance(value, dict):
            raise SimulationScenarioError("simulation scenario document is invalid")
        steps = value.get("steps")
        expected = value.get("expected")
        if not isinstance(steps, list) or not isinstance(expected, dict):
            raise SimulationScenarioError("simulation scenario content is invalid")
        if any(not isinstance(step, dict) for step in steps):
            raise SimulationScenarioError("simulation scenario step is invalid")
        return StoredSimulationScenario(
            id=_string(value.get("id")),
            project_id=_string(value.get("project_id")),
            name=_string(value.get("name")),
            source_revision=_integer(value.get("source_revision")),
            steps=tuple(cast(dict[str, JsonValue], step) for step in steps),
            expected=cast(dict[str, JsonValue], expected),
            created_at=_string(value.get("created_at")),
            updated_at=_string(value.get("updated_at")),
        )


def _expected_mismatches(
    expected: dict[str, JsonValue],
    actual: dict[str, JsonValue],
) -> list[JsonValue]:
    mismatches: list[JsonValue] = []
    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if key == "scene_history_contains" and isinstance(expected_value, list):
            history = actual.get("scene_history")
            if not isinstance(history, list) or not all(
                value in history for value in expected_value
            ):
                mismatches.append(
                    {
                        "field": key,
                        "expected": expected_value,
                        "actual": history,
                    }
                )
        elif actual_value != expected_value:
            mismatches.append(
                {
                    "field": key,
                    "expected": expected_value,
                    "actual": actual_value,
                }
            )
    return mismatches


def _deterministic_status(status: dict[str, object]) -> dict[str, JsonValue]:
    keys = (
        "active_scene",
        "clock_authority",
        "estimated_position_ms",
        "playback_state",
        "connected_agents",
        "scene_starts",
        "scene_completions",
        "scene_completion_errors",
        "command_errors",
        "lighting_output_error_count",
        "rendered_frames",
        "degraded_reason",
        "health",
        "operations",
        "director",
        "audience",
        "encounter",
        "opportunities",
    )
    return cast(
        dict[str, JsonValue],
        {key: status.get(key) for key in keys},
    )


def _frame_digest(frames: Sequence[RenderedFrame]) -> str:
    digest = hashlib.sha256()
    for frame in frames:
        digest.update(
            json.dumps(
                {
                    "position_ms": frame.position_ms,
                    "resource": frame.resource,
                    "pixels": frame.pixels,
                },
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _scenario_document(scenario: StoredSimulationScenario) -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": scenario.id,
        "project_id": scenario.project_id,
        "name": scenario.name,
        "source_revision": scenario.source_revision,
        "steps": scenario.steps,
        "expected": scenario.expected,
        "created_at": scenario.created_at,
        "updated_at": scenario.updated_at,
    }


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise SimulationScenarioError("simulation scenario string is invalid")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _string(value)


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SimulationScenarioError("simulation scenario integer is invalid")
    return value


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise SimulationScenarioError("simulation scenario boolean is invalid")
    return value
