import asyncio
from pathlib import Path

from cueverge.core.loader import load_show_package
from cueverge.core.models import (
    AudiencePresenceDefinition,
    CompletionDefinition,
    OpportunityDefinition,
    OpportunityProgramDefinition,
    ProgramScene,
    SceneCatalog,
    ScenePool,
    SchedulingDefinition,
    ShowProgramDefinition,
    TimeInterval,
)
from cueverge.simulation import ShowSimulation


def test_accelerated_thirty_minute_show_scenario_is_deterministic_and_safe() -> None:
    async def scenario() -> None:
        simulation = ShowSimulation(_simulation_catalog())
        await simulation.start()

        await simulation.observe(
            "frigate.zone.entered",
            attributes=_person("person-1"),
        )
        await simulation.observe(
            "frigate.zone.entered",
            attributes=_person("person-2"),
        )
        await simulation.advance(7_000)
        await simulation.observe(
            "frigate.object.end",
            attributes=_person("person-1"),
        )
        await simulation.observe(
            "frigate.object.end",
            attributes=_person("person-2"),
        )
        await simulation.advance(3_000)
        await simulation.observe(
            "frigate.zone.entered",
            attributes=_person("person-1"),
        )
        await simulation.advance(2_000)

        await simulation.operator_start("ambient")
        await simulation.advance(1_000)
        assert await simulation.request_scene("encounter-reaction") == "queue"
        await simulation.advance(3_000)
        assert "encounter-reaction" in simulation.scene_history

        await simulation.operator_start("operator-override")
        assert simulation.scene_history[-1] == "operator-override"
        simulation.disconnect_agent()
        assert simulation.runtime.status()["health"]["media_agent"] == "disconnected"
        simulation.reconnect_agent()
        assert simulation.runtime.status()["health"]["media_agent"] == "connected"

        await simulation.observe(
            "frigate.object.end",
            attributes=_person("person-1"),
        )
        await simulation.advance(1_784_000)

        status = simulation.runtime.status()
        assert simulation.clock.now_ms == 1_800_000
        assert status["scene_completions"] > 400
        assert status["audience"]["state"] == "absent"
        assert status["audience"]["semantic_events_emitted"] >= 4
        assert status["opportunities"]["created_count"] == 1
        assert status["opportunities"]["consumed_count"] == 1
        assert status["scene_completion_errors"] == 0
        assert status["health"]["lighting_output"] == "MemoryFrameSink"
        assert {"ambient", "synthetic-sweep", "encounter-reaction"}.issubset(
            simulation.scene_history
        )
        await simulation.shutdown()

    asyncio.run(scenario())


def _simulation_catalog() -> SceneCatalog:
    catalog = load_show_package(Path("shows/demo"))
    scenes = {}
    for scene_id, scene in catalog.scenes.items():
        definition = scene.definition.model_copy(
            update={"completion": CompletionDefinition(policy="select_next")}
        )
        if scene_id == "ambient":
            definition = definition.model_copy(
                update={
                    "scheduling": SchedulingDefinition(
                        protected_intervals=(TimeInterval(start_ms=0, end_ms=3000),)
                    )
                }
            )
        scenes[scene_id] = scene.model_copy(update={"definition": definition})
    program = ShowProgramDefinition(
        schema_version=1,
        initial_scene_id="ambient",
        default_pool_id="ambient",
        recent_history_size=1,
        random_seed=2025,
        pools=(
            ScenePool(
                id="ambient",
                category="ambient",
                scenes=(
                    ProgramScene(scene_id="ambient"),
                    ProgramScene(scene_id="synthetic-sweep"),
                ),
            ),
        ),
    )
    audience = AudiencePresenceDefinition(
        schema_version=1,
        cameras=("doorbell",),
        zones=("Porch",),
        observation_freshness_ms=5000,
        arrival_confirmation_ms=1000,
        absence_grace_ms=5000,
        long_idle_ms=5000,
    )
    opportunities = OpportunityProgramDefinition(
        schema_version=1,
        opportunities=(
            OpportunityDefinition(
                id="sustained-presence",
                scene_id="encounter-reaction",
                trigger_event="audience.arrived",
                required_context={"audience": True},
                ttl_ms=30_000,
                event_ttl_ms=3000,
                qualification_window_ms=30_000,
                min_presence_ms=5000,
                acknowledgement_cooldown_ms=60_000,
                priority=80,
            ),
        ),
    )
    return catalog.model_copy(
        update={
            "scenes": scenes,
            "program": program,
            "audience": audience,
            "opportunities": opportunities,
        }
    )


def _person(object_id: str) -> dict[str, str]:
    return {
        "source": "frigate",
        "object_id": object_id,
        "label": "person",
        "camera": "doorbell",
        "zone": "Porch",
    }
