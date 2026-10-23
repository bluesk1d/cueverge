import hashlib
import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

from cueverge.core.loader import load_show_package
from cueverge.gui.compiler import DraftCompiler
from cueverge.gui.contracts import InteractionPreviewRequest, SimulationStepRequest
from cueverge.gui.input_sources import deliver_input_event
from cueverge.gui.interactions import InteractionError, apply_interaction_edit
from cueverge.gui.scene_preview import _preview_slot
from cueverge.gui.simulation_scenarios import _deterministic_status
from cueverge.integrations.frigate import FrigateEventNormalizer
from cueverge.simulation import ShowSimulation

if TYPE_CHECKING:
    from cueverge.gui.service import GuiServices

MAX_PREVIEW_DURATION_MS = 600000
MAX_PREVIEW_WORK = 20_000_000
MAX_PREVIEW_BOUNDARIES = 1000


async def preview_interactions(
    services: "GuiServices",
    project_id: str,
    request: InteractionPreviewRequest,
) -> dict[str, object]:
    stored = services.repository.get_revision(project_id, request.source_revision)
    draft = request.proposed_draft or stored.draft
    if request.proposed_edit is not None:
        if request.proposed_edit.source_revision != request.source_revision:
            raise InteractionError("The proposed edit has a different source revision")
        identifier = hashlib.sha256(request.proposed_edit.model_dump_json().encode()).hexdigest()
        draft = apply_interaction_edit(
            draft, request.proposed_edit, new_response_id=f"preview-{identifier[:32]}"
        )
    if draft.editor_metadata.get("installation_id") != stored.draft.editor_metadata.get(
        "installation_id"
    ):
        raise InteractionError("Preview cannot change the installation binding")
    duration = sum(step.delta_ms or 0 for step in request.steps if step.kind == "advance")
    if duration > MAX_PREVIEW_DURATION_MS:
        raise InteractionError("Visitor preview is limited to ten minutes")
    boundaries = duration // min(scene.duration_ms for scene in draft.scenes)
    counts = {
        resource.resource: resource.pixel_count or 0 for resource in draft.installation.resources
    }
    work = sum(
        counts.get(output.resource, 0) * (len(output.timeline) + 1)
        for scene in draft.scenes
        for output in (scene.lighting if isinstance(scene.lighting, tuple) else (scene.lighting,))
    )
    editor_cues = draft.editor_metadata.get("timeline_cues")
    if isinstance(editor_cues, dict):
        work += max(counts.values(), default=0) * sum(
            len(cues) for cues in editor_cues.values() if isinstance(cues, list)
        )
    if request.resource is not None and counts.get(request.resource, 0) <= 0:
        raise InteractionError("Choose an LED output that exists in this installation")
    media_ticks = sum(
        resource.kind == "media_agent" for resource in draft.installation.resources
    ) * (duration // 100 + 1)
    if media_ticks > 20000:
        raise InteractionError(
            "Visitor preview exceeds its media telemetry budget; shorten the test"
        )
    if (
        boundaries > MAX_PREVIEW_BOUNDARIES
        or 2 * work * (boundaries + len(request.steps) + 1) > MAX_PREVIEW_WORK
    ):
        raise InteractionError(
            "Visitor preview exceeds its scene/pixel work budget; shorten the test"
        )
    if sum(counts.values()) > 65536:
        raise InteractionError("Visitor preview exceeds 65536 installation pixels")
    selected = len(request.steps) if request.frame_index is None else request.frame_index
    if selected > len(request.steps):
        raise InteractionError("The selected preview checkpoint does not exist")
    document_digest = hashlib.sha256(draft.model_dump_json().encode()).hexdigest()
    with (
        _preview_slot(),
        tempfile.TemporaryDirectory(prefix="cueverge-visitor-preview-") as directory,
    ):
        compiled = DraftCompiler(Path(directory)).compile(
            project_id=project_id,
            revision=stored.revision,
            draft=draft,
        )
        catalog = load_show_package(compiled.package_root)
        simulation = ShowSimulation(catalog, automatic_rendering=False)
        origin = MAX_PREVIEW_DURATION_MS
        simulation.clock.now_ms = origin
        trace: list[dict[str, object]] = []
        selected_frame: dict[str, object] | None = None
        frame_reason: str | None = None
        missing_bakes = (compiled.package_root / "runtime" / "lighting-bake-required.json").exists()
        try:
            if request.starting_scene_id is not None:
                await simulation.operator_start(request.starting_scene_id)
            elif catalog.program is not None:
                await simulation.start()
            else:
                await simulation.operator_start(draft.scenes[0].id)
            for index in range(len(request.steps) + 1):
                outcomes: list[dict[str, object]] = []
                step = request.steps[index - 1] if index else None
                if step is not None:
                    outcomes = await _apply_step(simulation, step)
                if simulation.runtime.active_scene is not None:
                    simulation.report_position()
                    simulation.runtime.render_frame(
                        render_pixels=index == selected and not missing_bakes
                    )
                    await simulation.runtime.wait_for_lifecycle_idle()
                    simulation._sync_active()
                    await simulation._acknowledge_prepares()
                status = _trace_status(simulation)
                trace.append(
                    {
                        "index": index,
                        "at_ms": simulation.clock.now_ms - origin,
                        "action": step.kind if step is not None else "start",
                        "observations": outcomes,
                        "status": status,
                        "scene_history": list(simulation.scene_history),
                    }
                )
                if index == selected:
                    if simulation.runtime.active_scene is not None and not missing_bakes:
                        simulation.report_position()
                        simulation.runtime.render_frame()
                    selected_frame, frame_reason = _frame(
                        simulation,
                        project_id,
                        stored.revision,
                        draft != stored.draft,
                        request,
                        missing_bakes,
                    )
                for sink in simulation.sinks.values():
                    sink.frames.clear()
            return {
                "project_id": project_id,
                "source_revision": stored.revision,
                "draft_digest": document_digest,
                "unsaved": draft != stored.draft,
                "trace": trace,
                "selected_index": selected,
                "frame": selected_frame,
                "frame_unavailable_reason": frame_reason,
                "scene_timeline": [
                    {**item, "at_ms": cast(int, item["at_ms"]) - origin}
                    for item in simulation.scene_timeline
                ],
                "hardware_adapters_opened": False,
                "hardware_commands_sent": False,
                "limitations": [
                    "Isolated test using healthy fake media agents and declared scene clocks.",
                    "Visitor qualification is ticked every 100 ms, like the controller. "
                    "No real input or device readiness is established.",
                    "Scene changes inside a wait are recorded in scene_timeline; "
                    "checkpoints show state after each action.",
                    "Candidate visitor labels are used; verify real source filters separately "
                    "under Inputs & connections.",
                ],
            }
        finally:
            await simulation.shutdown()


async def _apply_step(
    simulation: ShowSimulation, step: SimulationStepRequest
) -> list[dict[str, object]]:
    if step.kind == "advance":
        remaining = step.delta_ms or 0
        while remaining:
            delta = min(100, remaining)
            await simulation.advance(delta, render_frames=False)
            remaining -= delta
        return []
    if step.kind == "visitor":
        timestamp = (simulation.clock.now_ms - step.age_ms) / 1000
        zones = [step.zone] if step.zone else []
        detected = {
            "id": step.object_id,
            "camera": step.camera,
            "label": step.label,
            "start_time": timestamp,
            "frame_time": timestamp,
            "entered_zones": zones,
            **({"end_time": timestamp} if step.observation == "leave" else {}),
        }
        payload = {
            "type": {"arrive": "new", "stay": "update", "leave": "end"}[step.observation],
            "after": detected,
            "before": simulation.visitor_objects.get(step.object_id)
            if step.observation != "arrive"
            else None,
        }
        if step.observation == "leave":
            simulation.visitor_objects.pop(step.object_id, None)
        else:
            simulation.visitor_objects[step.object_id] = detected
        audience = simulation.runtime.catalog.audience
        labels = audience.labels if audience is not None else ("person",)
        events = FrigateEventNormalizer(accepted_labels=labels).normalize(
            json.dumps(payload),
            received_at_ms=simulation.clock.now_ms,
        )
        if not events:
            return [{"accepted": False, "audience_relevant": False, "reason": "label_filtered"}]
        results = []
        for event in events:
            if audience is None and simulation.runtime.catalog.encounter is None:
                results.append(
                    {
                        "event_type": event.event_type,
                        "accepted": False,
                        "reason": "no_visitor_rules",
                    }
                )
                continue
            outcome = await deliver_input_event(
                simulation.runtime,
                simulation.runtime.ingest_event_with_presence,
                event,
                simulation.clock.now_ms,
            )
            results.append(
                {
                    "event_type": event.event_type,
                    "accepted": outcome.accepted,
                    "audience_relevant": outcome.audience_relevant,
                    "reason": outcome.reason,
                    "camera": step.camera,
                    "zone": step.zone,
                }
            )
        simulation._sync_active()
        await simulation._acknowledge_prepares()
        return results
    if step.kind == "observe":
        await simulation.observe(step.event_type or "", attributes=step.attributes)
        return [{"note": "Direct semantic test event; camera normalization was bypassed."}]
    if step.kind == "request_scene":
        return [{"decision": await simulation.request_scene(step.scene_id or "")}]
    if step.kind == "operator_start":
        await simulation.operator_start(step.scene_id or "")
    elif step.kind == "disconnect":
        simulation.disconnect_agent(step.agent_id)
    elif step.kind == "reconnect":
        simulation.reconnect_agent(step.agent_id)
    elif step.kind == "output_failure":
        simulation.set_output_failure(step.resource or "", failed=bool(step.failed))
    return []


def _trace_status(simulation: ShowSimulation) -> dict[str, object]:
    source = _deterministic_status(simulation.runtime.status())
    result: dict[str, object] = {
        key: source.get(key)
        for key in (
            "active_scene",
            "estimated_position_ms",
            "playback_state",
            "operations",
            "health",
            "degraded_reason",
            "opportunities",
        )
    }
    active = simulation.runtime.active_scene
    result["completion_policy"] = active.definition.completion.policy if active else None
    audience = simulation.runtime.audience
    result["audience"] = (
        {key: value for key, value in audience.status().items() if key != "recent_observations"}
        if audience is not None
        else None
    )
    encounter = simulation.runtime.encounter
    result["encounter"] = (
        {key: value for key, value in encounter.status().items() if key != "encounter_id"}
        if encounter is not None
        else None
    )
    result["director"] = (
        simulation.runtime.director.status() if simulation.runtime.director else None
    )
    return result


def _frame(
    simulation: ShowSimulation,
    project_id: str,
    revision: int,
    unsaved: bool,
    request: InteractionPreviewRequest,
    missing_bakes: bool,
) -> tuple[dict[str, object] | None, str | None]:
    active = simulation.runtime.active_scene
    if active is None:
        return None, "No scene is playing at this checkpoint."
    if missing_bakes:
        return (
            None,
            "This show has unbaked spatial lighting. The decision trace is available; "
            "use advanced composition preview for colors.",
        )
    outputs = [
        output
        for output in active.scene.lighting_outputs
        if request.resource in (None, output.definition.resource)
    ]
    if not outputs:
        return None, "This scene has no matching LED output. Media is simulated, not streamed."
    if sum(min(output.pixel_count, request.pixel_limit) for output in outputs) > 4096:
        raise InteractionError(
            "Select one output or fewer pixels; frames are limited to 4096 samples"
        )
    rendered = []
    position = simulation.runtime.status().get("estimated_position_ms")
    if not isinstance(position, int):
        return None, "No authoritative scene clock is available at this checkpoint."
    for output in outputs:
        frames = simulation.sinks[output.definition.resource].frames
        frame = next(
            (
                frame
                for frame in reversed(frames)
                if frame.scene_instance_id == active.scene_instance_id
            ),
            None,
        )
        if frame is None:
            return (
                None,
                "No evaluated frame is available at this checkpoint; "
                "inspect device/timecode status in the trace.",
            )
        colors = frame.pixels[: request.pixel_limit]
        rendered.append(
            {
                "output_id": output.definition.id,
                "resource": output.definition.resource,
                "pixel_count": output.pixel_count,
                "pixel_offset": 0,
                "output_position_ms": frame.position_ms,
                "colors": [list(color) for color in colors],
                "next_pixel_offset": len(colors) if len(colors) < output.pixel_count else None,
                "limitations": [],
            }
        )
    return {
        "project_id": project_id,
        "source_revision": revision,
        "scene_id": active.definition.id,
        "timestamp_ms": position,
        "duration_ms": active.definition.duration_ms,
        "unsaved": unsaved,
        "evaluation": "production_lighting_renderer",
        "hardware_commands_sent": False,
        "outputs": rendered,
    }, None
