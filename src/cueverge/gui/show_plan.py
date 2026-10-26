"""Plain-language show plan: how scenes are put together into a running show.

The plan is a projection over existing runtime definitions (program start, scene
completion and the default program pool). Edits write only those fields so the
runtime package format is unchanged.
"""

from typing import Literal, cast

from cueverge.core.models import (
    CompletionDefinition,
    ProgramScene,
    ScenePool,
    ShowProgramDefinition,
)
from cueverge.gui.compiler import ambient_compositions_on_scene_timelines
from cueverge.gui.contracts import DraftProject, ShowPlanEditRequest
from cueverge.gui.interactions import describe_interactions

Ending = Literal["repeat", "play_next", "shuffle", "freeze", "stop"]

Policy = Literal["loop", "select_next", "transition_to", "hold", "stop"]

ENDING_FROM_POLICY: dict[str, Ending] = {
    "loop": "repeat",
    "transition_to": "play_next",
    "select_next": "shuffle",
    "hold": "freeze",
    "stop": "stop",
}
POLICY_FROM_ENDING: dict[Ending, Policy] = {
    "repeat": "loop",
    "play_next": "transition_to",
    "shuffle": "select_next",
    "freeze": "hold",
    "stop": "stop",
}
ENDING_CHOICES: dict[Ending, str] = {
    "repeat": "Repeat this scene",
    "play_next": "Play a specific scene next",
    "shuffle": "Let the show pick next (shuffle list and visitor responses)",
    "freeze": "Stay on the last moment",
    "stop": "Stop the show",
}
DEFAULT_SHUFFLE_POOL_ID = "main"


class ShowPlanError(ValueError):
    pass


def _ending(draft: DraftProject, scene_id: str) -> dict[str, object]:
    names = {scene.id: scene.name for scene in draft.scenes}
    completion = next(scene for scene in draft.scenes if scene.id == scene_id).completion
    kind = ENDING_FROM_POLICY[completion.policy]
    label = {
        "repeat": "Repeats",
        "shuffle": "Then the show picks the next scene",
        "freeze": "Stays on the last moment",
        "stop": "Stops the show",
    }.get(kind)
    if kind == "play_next":
        target = completion.target_scene_id or ""
        label = f"Then plays {names.get(target, target)}"
    return {
        "kind": kind,
        "next_scene_id": completion.target_scene_id,
        "label": label,
    }


def _scene_summary(draft: DraftProject, scene_id: str) -> dict[str, object]:
    scene = next(scene for scene in draft.scenes if scene.id == scene_id)
    return {
        "id": scene.id,
        "name": scene.name,
        "duration_ms": scene.duration_ms,
        "ending": _ending(draft, scene.id),
    }


def _default_pool(draft: DraftProject) -> ScenePool | None:
    if draft.program is None:
        return None
    return next(pool for pool in draft.program.pools if pool.id == draft.program.default_pool_id)


def describe_show_plan(draft: DraftProject) -> dict[str, object]:
    scenes = {scene.id: scene for scene in draft.scenes}
    program = draft.program
    main: list[str] = []
    main_end: dict[str, object] = {"kind": "none", "scene_id": None}
    if program is not None:
        current = program.initial_scene_id
        while True:
            main.append(current)
            completion = scenes[current].completion
            if completion.policy != "transition_to":
                main_end = {"kind": ENDING_FROM_POLICY[completion.policy], "scene_id": current}
                break
            target = completion.target_scene_id or ""
            if target not in scenes:
                main_end = {"kind": "missing", "scene_id": target}
                break
            if target in main:
                main_end = {"kind": "back_to", "scene_id": target}
                break
            current = target

    pool = _default_pool(draft)
    shuffle_entries: list[dict[str, object]] = []
    if pool is not None:
        available = [entry for entry in pool.scenes if entry.available]
        total = sum(entry.weight for entry in available)
        for entry in pool.scenes:
            shuffle_entries.append(
                {
                    **_scene_summary(draft, entry.scene_id),
                    "weight": entry.weight,
                    "chance_percent": round(entry.weight * 100 / total)
                    if entry.available and total
                    else 0,
                    "available": entry.available,
                    "has_extra_rules": bool(entry.cooldown_ms or entry.conditions)
                    or not entry.available,
                }
            )

    interactions = describe_interactions(draft)
    responses = [
        {
            "key": card["key"],
            "kind": card["kind"],
            "name": card["name"],
            "when": card["when"],
            "scene_id": card["scene_id"],
            "scene_name": card["scene_name"],
            "afterward": card["afterward"],
        }
        for card in cast(list[dict[str, object]], interactions["cards"])
    ]

    used: set[str] = set(main)
    if program is not None:
        used.update(entry.scene_id for item in program.pools for entry in item.scenes)
    if draft.opportunities is not None:
        used.update(item.scene_id for item in draft.opportunities.opportunities)
    if draft.encounter is not None:
        used.add(draft.encounter.ambient_scene_id)
        used.update(
            item.scene_id for item in draft.encounter.transitions if item.scene_id is not None
        )
    used.intersection_update(scenes)
    pending = list(used)
    while pending:
        scene = scenes[pending.pop()]
        for linked in (scene.completion.target_scene_id, scene.failure_scene_id):
            if linked is not None and linked in scenes and linked not in used:
                used.add(linked)
                pending.append(linked)

    warnings: list[str] = []
    if program is None:
        warnings.append(
            "Choose a starting scene so the show can start on its own when you press Start."
        )
    shuffling = [scene.name for scene in draft.scenes if scene.completion.policy == "select_next"]
    if program is None and shuffling:
        warnings.append(
            f"{', '.join(shuffling)} ends by letting the show pick next, but there is no "
            "shuffle list yet. Choose a starting scene first."
        )
    if pool is not None and not any(entry.available for entry in pool.scenes):
        warnings.append("Every scene in the shuffle list is turned off, so nothing can be picked.")
    guided = draft.opportunities is not None and bool(draft.opportunities.opportunities)
    if guided and not any(
        scenes[scene_id].completion.policy == "select_next"
        for scene_id in {*main, *(str(entry["id"]) for entry in shuffle_entries)}
    ):
        warnings.append(
            "Visitor responses only start when a scene ends with “Let the show pick next”. "
            "No scene in the main show does, so visitor responses will not play."
        )
    compositions = draft.editor_metadata.get("compositions")
    for composition_id, scene_ids in ambient_compositions_on_scene_timelines(draft).items():
        composition = compositions.get(composition_id) if isinstance(compositions, dict) else None
        name = (
            composition.get("name") if isinstance(composition, dict) else None
        ) or composition_id
        placed = ", ".join(scenes[scene_id].name for scene_id in scene_ids if scene_id in scenes)
        warnings.append(
            f"The ambient lighting composition “{name}” is also placed on the timeline of "
            f"{placed}. That copy follows scene time and restarts whenever the scene repeats; "
            "the ambient already runs under every scene that inherits it. Remove it from the "
            "scene timeline (Timeline editor > Compositions) to keep it continuous."
        )

    return {
        "starting_scene_id": program.initial_scene_id if program else None,
        "main_show": {
            "scenes": [_scene_summary(draft, scene_id) for scene_id in main],
            "end": main_end,
        },
        "shuffle_list": {
            "pool_id": pool.id if pool else None,
            "scenes": shuffle_entries,
        },
        "visitor_responses": responses,
        "unused_scenes": [
            _scene_summary(draft, scene.id) for scene in draft.scenes if scene.id not in used
        ],
        "ending_choices": [
            {"kind": kind, "label": label} for kind, label in ENDING_CHOICES.items()
        ],
        "warnings": warnings,
    }


def apply_show_plan_edit(draft: DraftProject, request: ShowPlanEditRequest) -> DraftProject:
    scene_ids = {scene.id for scene in draft.scenes}
    document = draft.model_dump(mode="json")
    if request.operation == "starting_scene":
        assert request.scene_id is not None
        if request.scene_id not in scene_ids:
            raise ShowPlanError("The starting scene does not exist")
        if draft.program is not None:
            program = draft.program.model_copy(update={"initial_scene_id": request.scene_id})
        else:
            program = ShowProgramDefinition(
                schema_version=1,
                initial_scene_id=request.scene_id,
                default_pool_id=DEFAULT_SHUFFLE_POOL_ID,
                pools=(
                    ScenePool(
                        id=DEFAULT_SHUFFLE_POOL_ID,
                        category="ambient",
                        scenes=(ProgramScene(scene_id=request.scene_id),),
                    ),
                ),
            )
        document["program"] = program.model_dump(mode="json")
    elif request.operation == "scene_ending":
        assert request.scene_id is not None and request.ending is not None
        if request.scene_id not in scene_ids:
            raise ShowPlanError("The scene no longer exists")
        if request.ending == "play_next":
            if request.next_scene_id not in scene_ids:
                raise ShowPlanError("Choose which scene plays next")
            if request.next_scene_id == request.scene_id:
                raise ShowPlanError("Use “Repeat this scene” to play the same scene again")
        if request.ending == "shuffle" and draft.program is None:
            raise ShowPlanError(
                "Choose a starting scene first; that creates the shuffle list the show picks from"
            )
        completion = CompletionDefinition(
            policy=POLICY_FROM_ENDING[request.ending],
            target_scene_id=request.next_scene_id if request.ending == "play_next" else None,
        )
        document["scenes"] = [
            scene.model_copy(update={"completion": completion}).model_dump(mode="json")
            if scene.id == request.scene_id
            else scene.model_dump(mode="json")
            for scene in draft.scenes
        ]
    else:
        assert request.shuffle is not None
        pool = _default_pool(draft)
        if draft.program is None or pool is None:
            raise ShowPlanError("Choose a starting scene first; that creates the shuffle list")
        missing = {entry.scene_id for entry in request.shuffle}.difference(scene_ids)
        if missing:
            raise ShowPlanError(f"Unknown scenes in the shuffle list: {sorted(missing)}")
        previous = {entry.scene_id: entry for entry in pool.scenes}
        entries = tuple(
            previous[entry.scene_id].model_copy(update={"weight": entry.weight})
            if entry.scene_id in previous
            else ProgramScene(scene_id=entry.scene_id, weight=entry.weight)
            for entry in request.shuffle
        )
        pools = tuple(
            item.model_copy(update={"scenes": entries}) if item.id == pool.id else item
            for item in draft.program.pools
        )
        document["program"] = draft.program.model_copy(update={"pools": pools}).model_dump(
            mode="json"
        )
    return DraftProject.model_validate(document)
