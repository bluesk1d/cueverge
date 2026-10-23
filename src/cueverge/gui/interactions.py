from uuid import uuid4

from cueverge.core.models import (
    CompletionDefinition,
    OpportunityDefinition,
    OpportunityProgramDefinition,
    ProgramScene,
    ScenePool,
    ShowProgramDefinition,
)
from cueverge.gui.contracts import DraftProject, InteractionEditRequest

EVENT_NAMES = {
    "audience.arrived": "Visitors arrive",
    "audience.present": "Visitors are first confirmed present",
    "audience.departed": "Visitors leave",
    "audience.long_idle": "The display stays quiet after visitors leave",
    "frigate.object.new": "A camera detects a new object",
    "frigate.object.updated": "A camera updates an object",
    "frigate.zone.entered": "An object enters a camera zone",
    "frigate.object.end": "A camera stops tracking an object",
}
GUIDED_EVENTS = frozenset(name for name in EVENT_NAMES if name.startswith("audience."))


class InteractionError(ValueError):
    pass


def response_is_guided(response: OpportunityDefinition) -> bool:
    return (
        len(response.id) <= 128
        and response.trigger_event in GUIDED_EVENTS
        and not response.trigger_attributes
        and response.required_context
        in (
            {},
            {"audience": True},
            {"audience": False},
            {"audience_state": "present"},
            {"audience_state": "absent"},
        )
    )


def describe_interactions(draft: DraftProject) -> dict[str, object]:
    scenes = {scene.id: scene for scene in draft.scenes}
    names = draft.editor_metadata.get("interaction_names", {})
    if not isinstance(names, dict):
        names = {}
    cards: list[dict[str, object]] = []
    for response in draft.opportunities.opportunities if draft.opportunities else ():
        scene = scenes[response.scene_id]
        cards.append(
            {
                "key": f"response:{response.id}",
                "kind": "response",
                "id": response.id,
                "name": names[response.id]
                if isinstance(names.get(response.id), str)
                else scene.name,
                "when": EVENT_NAMES.get(response.trigger_event, response.trigger_event),
                "scene_id": scene.id,
                "scene_name": scene.name,
                "timing": "Waits for a scene that chooses what plays next to finish.",
                "afterward": completion_summary(draft, scene.id),
                "editable": response_is_guided(response),
                "definition": response.model_dump(mode="json"),
                "note": (
                    "Other visitor responses and state sequences can compete. "
                    "Detection is not a promise of playback."
                ),
            }
        )
    if draft.encounter is not None:
        for index, transition in enumerate(draft.encounter.transitions):
            transition_scene = scenes.get(transition.scene_id or "")
            cards.append(
                {
                    "key": f"transition:{index}",
                    "kind": "transition",
                    "index": index,
                    "name": transition_scene.name if transition_scene else "Change visitor state",
                    "when": EVENT_NAMES.get(transition.event_type, transition.event_type),
                    "scene_id": transition.scene_id,
                    "scene_name": transition_scene.name if transition_scene else None,
                    "timing": (
                        "Requests a response when this state step matches; "
                        "scene interruption and queue rules still apply."
                    ),
                    "afterward": completion_summary(draft, transition_scene.id)
                    if transition_scene
                    else "No scene requested by this step.",
                    "editable": True,
                    "definition": transition.model_dump(mode="json"),
                    "note": (
                        f"Part of a state sequence: {', '.join(transition.from_states)} "
                        f"to {transition.to_state}. Only the response scene is edited here; "
                        "the sequence is preserved."
                    ),
                }
            )
    program = draft.program
    return {
        "cards": cards,
        "visitors": draft.audience.model_dump(mode="json") if draft.audience else None,
        "normal_playback": {
            "starting_scene_id": program.initial_scene_id if program else None,
            "pools": [pool.model_dump(mode="json") for pool in program.pools] if program else [],
            "default_pool_id": program.default_pool_id if program else None,
        },
        "warnings": [
            "Some scenes do not choose the next response at completion; inspect their end behavior."
        ]
        if any(scene.completion.policy != "select_next" for scene in draft.scenes)
        else [],
    }


def completion_summary(draft: DraftProject, scene_id: str) -> str:
    scene = next(scene for scene in draft.scenes if scene.id == scene_id)
    completion = scene.completion
    if completion.policy == "transition_to":
        target = next(scene for scene in draft.scenes if scene.id == completion.target_scene_id)
        return f"Then plays {target.name}."
    if completion.policy == "select_next":
        return "Then considers waiting visitor responses and the normal show selection."
    return {
        "hold": "Holds at the end; it does not return to normal selection.",
        "stop": "Stops at the end.",
        "loop": "Repeats this scene; it does not choose waiting visitor responses.",
    }[completion.policy]


def apply_interaction_edit(
    draft: DraftProject,
    request: InteractionEditRequest,
    *,
    new_response_id: str | None = None,
) -> DraftProject:
    document = draft.model_dump(mode="json")
    if request.operation == "visitors":
        assert request.visitors is not None
        document["audience"] = request.visitors.model_dump(mode="json")
    elif request.operation == "transition":
        if draft.encounter is None or request.transition_index is None:
            raise InteractionError("This show has no matching state sequence")
        if request.transition_index >= len(draft.encounter.transitions):
            raise InteractionError("The selected state step no longer exists")
        transitions = list(draft.encounter.transitions)
        if request.scene_id is not None and not any(
            scene.id == request.scene_id for scene in draft.scenes
        ):
            raise InteractionError("The response scene does not exist")
        transitions[request.transition_index] = transitions[request.transition_index].model_copy(
            update={"scene_id": request.scene_id},
        )
        document["encounter"] = draft.encounter.model_copy(
            update={"transitions": tuple(transitions)},
        ).model_dump(mode="json")
    else:
        response = request.response
        assert response is not None
        if not any(scene.id == response.scene_id for scene in draft.scenes):
            raise InteractionError("The response scene does not exist")
        existing = list(draft.opportunities.opportunities) if draft.opportunities else []
        previous = next((item for item in existing if item.id == response.id), None)
        if response.id is not None and previous is None:
            raise InteractionError("The selected response no longer exists")
        if previous is not None and not response_is_guided(previous):
            raise InteractionError(
                "This response has additional matching rules; use advanced controls"
            )
        if draft.audience is None:
            if request.visitors is None:
                raise InteractionError("Choose a visitor camera or zone first")
            document["audience"] = request.visitors.model_dump(mode="json")
        elif request.visitors is not None and request.visitors != draft.audience:
            raise InteractionError(
                "Edit the shared visitor area separately; it affects every response"
            )
        identifier = previous.id if previous else new_response_id or f"visitor-{uuid4().hex}"
        if previous is None and any(item.id == identifier for item in existing):
            raise InteractionError("The new response identifier already exists")
        updated = (
            previous
            or OpportunityDefinition(
                id=identifier,
                scene_id=response.scene_id,
                trigger_event=response.trigger_event,
                ttl_ms=response.ttl_ms,
            )
        ).model_copy(
            update={
                "scene_id": response.scene_id,
                "trigger_event": response.trigger_event,
                "ttl_ms": response.ttl_ms,
                "min_presence_ms": response.min_presence_ms,
                "acknowledgement_cooldown_ms": response.acknowledgement_cooldown_ms,
                "priority": response.priority,
            }
        )
        changes = {}
        for field in ("min_absence_before_arrival_ms", "qualification_window_ms", "event_ttl_ms"):
            value = getattr(response, field)
            if value is not None:
                changes[field] = value
        if response.qualification_context != "preserve":
            changes["required_context"] = (
                {}
                if response.qualification_context == "any"
                else {"audience": response.qualification_context == "present"}
            )
        updated = updated.model_copy(update=changes)
        document["opportunities"] = OpportunityProgramDefinition(
            schema_version=1,
            opportunities=tuple(updated if item.id == identifier else item for item in existing)
            if previous
            else (*existing, updated),
        ).model_dump(mode="json")
        names = document["editor_metadata"].get("interaction_names", {})
        if not isinstance(names, dict):
            raise InteractionError(
                "Interaction names contain unsupported metadata; use advanced controls"
            )
        document["editor_metadata"]["interaction_names"] = {**names, identifier: response.name}
        if draft.program is None and previous is None:
            if response.fallback_scene_id is None or not response.prepare_completion:
                raise InteractionError(
                    "Choose normal playback and confirm the shared scene end-behavior change"
                )
            if not any(scene.id == response.fallback_scene_id for scene in draft.scenes):
                raise InteractionError("The normal-playback scene does not exist")
            document["program"] = ShowProgramDefinition(
                schema_version=1,
                initial_scene_id=response.fallback_scene_id,
                default_pool_id="visitor-normal",
                pools=(
                    ScenePool(
                        id="visitor-normal",
                        category="ambient",
                        scenes=(ProgramScene(scene_id=response.fallback_scene_id),),
                    ),
                ),
            ).model_dump(mode="json")
        if response.prepare_completion:
            selected = {response.scene_id}
            if draft.program is None and previous is None and response.fallback_scene_id:
                selected.add(response.fallback_scene_id)
            if draft.program is not None:
                selected.add(draft.program.initial_scene_id)
                selected.update(
                    item.scene_id
                    for pool in draft.program.pools
                    if pool.id == draft.program.default_pool_id
                    for item in pool.scenes
                )
            document["scenes"] = [
                scene.model_copy(
                    update={"completion": CompletionDefinition(policy="select_next")}
                ).model_dump(mode="json")
                if scene.id in selected
                else scene.model_dump(mode="json")
                for scene in draft.scenes
            ]
    return DraftProject.model_validate(document)
