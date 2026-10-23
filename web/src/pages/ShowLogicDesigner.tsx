import { useEffect, useMemo, useRef, useState } from "react";

import {
  AudiencePresenceDefinition,
  EncounterDefinition,
  EncounterTransitionDefinition,
  OpportunityDefinition,
  OpportunityProgramDefinition,
  ProgramSceneDefinition,
  ProjectDraftRecord,
  runSimulationScenario,
  saveSimulationScenario,
  ScenePoolDefinition,
  ShowProgramDefinition,
  SimulationRun,
  SimulationStep,
} from "../api";

type Scene = ProjectDraftRecord["draft"]["scenes"][number];
type Diagnostic = {
  code: string;
  message: string;
  severity: "error" | "warning" | "info";
};

const SEMANTIC_CONDITIONS = [
  "audience",
  "audience_state",
  "audience_active_objects",
  "audience_present_ms",
  "audience_previous_absence_ms",
] as const;

const SEMANTIC_EVENTS = [
  "audience.arrived",
  "audience.present",
  "audience.departed",
  "audience.long_idle",
] as const;

export function ShowLogicDesigner({
  canEdit,
  draft,
  onSave,
  projectId,
  onInputs,
  openRequest = 0,
}: {
  canEdit: boolean;
  draft: ProjectDraftRecord;
  onSave: (document: ProjectDraftRecord["draft"]) => Promise<void>;
  projectId: string;
  onInputs?: () => void;
  openRequest?: number;
}) {
  const disclosure = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    if (openRequest && disclosure.current) {
      disclosure.current.open = true;
      disclosure.current.scrollIntoView({ block: "start" });
    }
  }, [openRequest]);
  const scenes = draft.draft.scenes;
  const [program, setProgram] = useState<ShowProgramDefinition>(() =>
    initialProgram(draft),
  );
  const [audience, setAudience] = useState<AudiencePresenceDefinition>(() =>
    initialAudience(draft),
  );
  const [opportunities, setOpportunities] =
    useState<OpportunityProgramDefinition>(() => initialOpportunities(draft));
  const [encounter, setEncounter] = useState<EncounterDefinition>(() =>
    initialEncounter(draft),
  );
  const [editedScenes, setEditedScenes] = useState<Scene[]>(() =>
    structuredClone(scenes),
  );
  const [newPoolId, setNewPoolId] = useState("ambient");
  const [newPoolCategory, setNewPoolCategory] = useState("ambient");
  const [newOpportunityId, setNewOpportunityId] = useState("visitor");
  const [newTransitionEvent, setNewTransitionEvent] =
    useState("audience.arrived");
  const [newTransitionFrom, setNewTransitionFrom] = useState("idle");
  const [newTransitionTo, setNewTransitionTo] = useState("engaged");
  const [newTransitionScene, setNewTransitionScene] = useState(
    scenes[0]?.id ?? "",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<SimulationRun | null>(null);

  useEffect(() => {
    setProgram(initialProgram(draft));
    setAudience(initialAudience(draft));
    setOpportunities(initialOpportunities(draft));
    setEncounter(initialEncounter(draft));
    setEditedScenes(structuredClone(draft.draft.scenes));
    setAnalysis(null);
  }, [draft.revision]);

  const diagnostics = useMemo(
    () =>
      showLogicDiagnostics(
        editedScenes,
        program,
        audience,
        opportunities,
        encounter,
        draft.draft.installation,
      ),
    [
      audience,
      draft.draft.installation,
      editedScenes,
      encounter,
      opportunities,
      program,
    ],
  );
  const hasErrors = diagnostics.some(
    (diagnostic) => diagnostic.severity === "error",
  );
  const states = encounterStates(encounter);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await onSave({
        ...draft.draft,
        scenes: editedScenes,
        program,
        audience,
        opportunities,
        encounter,
      });
    } catch (saveError) {
      setError(
        saveError instanceof Error
          ? saveError.message
          : "Autonomous show logic could not be saved.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function runAnalysis(acceptanceScenario: boolean) {
    setBusy(true);
    setError(null);
    try {
      const steps = acceptanceScenario
        ? acceptanceSimulationSteps(audience, program, opportunities, editedScenes)
        : ([{ kind: "advance", delta_ms: 1_800_000 }] satisfies SimulationStep[]);
      const saved = await saveSimulationScenario(projectId, {
        name: acceptanceScenario
          ? "Autonomous visitor acceptance rehearsal"
          : "Thirty-minute autonomous analysis",
        steps,
        expected: {},
      });
      setAnalysis(await runSimulationScenario(projectId, saved.id));
    } catch (analysisError) {
      setError(
        analysisError instanceof Error
          ? analysisError.message
          : "Autonomous simulation analysis failed.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <details ref={disclosure} className="show-logic-designer">
      <summary>Autonomous show logic designer</summary>
      {onInputs && <p>Visitor responses need camera events. <button onClick={onInputs}>Inputs & connections</button></p>}
      <div className="show-logic-toolbar">
        <button
          disabled={!canEdit}
          onClick={() =>
            applyTemplate(
              "ambient",
              editedScenes,
              setEditedScenes,
              setProgram,
              setAudience,
              setOpportunities,
              setEncounter,
            )
          }
        >
          Ambient loop template
        </button>
        <button
          disabled={!canEdit}
          onClick={() =>
            applyTemplate(
              "visitor",
              editedScenes,
              setEditedScenes,
              setProgram,
              setAudience,
              setOpportunities,
              setEncounter,
            )
          }
        >
          Visitor interaction template
        </button>
        <button
          disabled={!canEdit}
          onClick={() =>
            applyTemplate(
              "manual",
              editedScenes,
              setEditedScenes,
              setProgram,
              setAudience,
              setOpportunities,
              setEncounter,
            )
          }
        >
          Manual operator template
        </button>
        <button
          disabled={!canEdit}
          onClick={() =>
            applyTemplate(
              "scheduled",
              editedScenes,
              setEditedScenes,
              setProgram,
              setAudience,
              setOpportunities,
              setEncounter,
            )
          }
        >
          Scheduled main show template
        </button>
        <button disabled={!canEdit || busy || hasErrors} onClick={() => void save()}>
          Save autonomous logic
        </button>
      </div>

      <section className="show-logic-section" aria-label="Program pools">
        <h4>Program pools and deterministic selection</h4>
        <label>
          Initial scene
          <select
            aria-label="Program initial scene"
            disabled={!canEdit}
            value={program.initial_scene_id}
            onChange={(event) =>
              setProgram({ ...program, initial_scene_id: event.target.value })
            }
          >
            {editedScenes.map((scene) => (
              <option key={scene.id} value={scene.id}>
                {scene.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Default pool
          <select
            aria-label="Program default pool"
            disabled={!canEdit}
            value={program.default_pool_id}
            onChange={(event) =>
              setProgram({ ...program, default_pool_id: event.target.value })
            }
          >
            {program.pools.map((pool) => (
              <option key={pool.id} value={pool.id}>
                {pool.id}
              </option>
            ))}
          </select>
        </label>
        <label>
          Recent-history exclusion
          <input
            aria-label="Recent history exclusion"
            disabled={!canEdit}
            min="0"
            max="100"
            type="number"
            value={program.recent_history_size}
            onChange={(event) =>
              setProgram({
                ...program,
                recent_history_size: Number(event.target.value),
              })
            }
          />
        </label>
        <label>
          Deterministic random seed
          <input
            aria-label="Program random seed"
            disabled={!canEdit}
            type="number"
            value={program.random_seed}
            onChange={(event) =>
              setProgram({ ...program, random_seed: Number(event.target.value) })
            }
          />
        </label>
        {program.pools.map((pool, poolIndex) => (
          <PoolEditor
            canEdit={canEdit}
            key={pool.id}
            pool={pool}
            scenes={editedScenes}
            onChange={(updated) =>
              setProgram({
                ...program,
                pools: program.pools.map((candidate, index) =>
                  index === poolIndex ? updated : candidate,
                ),
              })
            }
            onRemove={() => {
              const pools = program.pools.filter((_, index) => index !== poolIndex);
              setProgram({
                ...program,
                pools,
                default_pool_id:
                  program.default_pool_id === pool.id
                    ? pools[0]?.id ?? ""
                    : program.default_pool_id,
              });
            }}
          />
        ))}
        <div className="show-logic-create-row">
          <input
            aria-label="New program pool ID"
            value={newPoolId}
            onChange={(event) => setNewPoolId(event.target.value)}
          />
          <input
            aria-label="New program pool category"
            value={newPoolCategory}
            onChange={(event) => setNewPoolCategory(event.target.value)}
          />
          <button
            disabled={
              !canEdit ||
              !newPoolId.trim() ||
              program.pools.some((pool) => pool.id === newPoolId)
            }
            onClick={() =>
              setProgram({
                ...program,
                default_pool_id: program.default_pool_id || newPoolId,
                pools: [
                  ...program.pools,
                  {
                    id: newPoolId,
                    category: newPoolCategory || newPoolId,
                    scenes: [],
                  },
                ],
              })
            }
          >
            Add pool
          </button>
        </div>
      </section>

      <AudienceEditor
        audience={audience}
        canEdit={canEdit}
        onChange={setAudience}
      />

      <section className="show-logic-section" aria-label="Audience opportunities">
        <h4>Semantic engagement opportunities</h4>
        {opportunities.opportunities.map((opportunity, index) => (
          <OpportunityEditor
            canEdit={canEdit}
            key={opportunity.id}
            opportunity={opportunity}
            scenes={editedScenes}
            onChange={(updated) =>
              setOpportunities({
                ...opportunities,
                opportunities: opportunities.opportunities.map(
                  (candidate, candidateIndex) =>
                    candidateIndex === index ? updated : candidate,
                ),
              })
            }
            onRemove={() =>
              setOpportunities({
                ...opportunities,
                opportunities: opportunities.opportunities.filter(
                  (_, candidateIndex) => candidateIndex !== index,
                ),
              })
            }
          />
        ))}
        <div className="show-logic-create-row">
          <input
            aria-label="New opportunity ID"
            value={newOpportunityId}
            onChange={(event) => setNewOpportunityId(event.target.value)}
          />
          <button
            disabled={
              !canEdit ||
              !newOpportunityId.trim() ||
              opportunities.opportunities.some(
                (opportunity) => opportunity.id === newOpportunityId,
              )
            }
            onClick={() =>
              setOpportunities({
                ...opportunities,
                opportunities: [
                  ...opportunities.opportunities,
                  defaultOpportunity(
                    newOpportunityId,
                    editedScenes[0]?.id ?? "",
                  ),
                ],
              })
            }
          >
            Add opportunity
          </button>
        </div>
      </section>

      <section className="show-logic-section" aria-label="Encounter flow editor">
        <h4>Encounter state flow</h4>
        <EncounterSettings
          canEdit={canEdit}
          encounter={encounter}
          scenes={editedScenes}
          onChange={setEncounter}
        />
        <EncounterGraph encounter={encounter} />
        <div
          className="show-logic-table-scroll"
          role="region"
          aria-label="Encounter transitions"
          tabIndex={0}
        >
          <table aria-label="Accessible encounter transition editor">
            <thead>
              <tr>
                <th>Event</th>
                <th>From states</th>
                <th>To state</th>
                <th>Scene</th>
                <th>Minimum state age</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {encounter.transitions.map((transition, index) => (
                <EncounterTransitionRow
                  canEdit={canEdit}
                  key={`${index}:${transition.event_type}`}
                  onChange={(updated) =>
                    setEncounter({
                      ...encounter,
                      transitions: encounter.transitions.map(
                        (candidate, candidateIndex) =>
                          candidateIndex === index ? updated : candidate,
                      ),
                    })
                  }
                  onRemove={() =>
                    setEncounter({
                      ...encounter,
                      transitions: encounter.transitions.filter(
                        (_, candidateIndex) => candidateIndex !== index,
                      ),
                    })
                  }
                  scenes={editedScenes}
                  transition={transition}
                />
              ))}
            </tbody>
          </table>
        </div>
        <div className="show-logic-create-row">
          <select
            aria-label="New encounter event"
            value={newTransitionEvent}
            onChange={(event) => setNewTransitionEvent(event.target.value)}
          >
            {SEMANTIC_EVENTS.map((eventType) => (
              <option key={eventType}>{eventType}</option>
            ))}
          </select>
          <input
            aria-label="New encounter from state"
            value={newTransitionFrom}
            onChange={(event) => setNewTransitionFrom(event.target.value)}
          />
          <input
            aria-label="New encounter to state"
            value={newTransitionTo}
            onChange={(event) => setNewTransitionTo(event.target.value)}
          />
          <select
            aria-label="New encounter scene"
            value={newTransitionScene}
            onChange={(event) => setNewTransitionScene(event.target.value)}
          >
            <option value="">No scene request</option>
            {editedScenes.map((scene) => (
              <option key={scene.id} value={scene.id}>
                {scene.name}
              </option>
            ))}
          </select>
          <button
            disabled={
              !canEdit || !newTransitionFrom.trim() || !newTransitionTo.trim()
            }
            onClick={() =>
              setEncounter({
                ...encounter,
                transitions: [
                  ...encounter.transitions,
                  {
                    event_type: newTransitionEvent,
                    from_states: [newTransitionFrom],
                    to_state: newTransitionTo,
                    scene_id: newTransitionScene || null,
                    min_state_age_ms: 0,
                    attributes: {},
                  },
                ],
              })
            }
          >
            Add transition
          </button>
        </div>
        <small>
          Node and table views are equivalent. Cooldown expiry and encounter
          timeout transitions are runtime-controlled and therefore not duplicated
          as authored edges.
        </small>
      </section>

      <SceneLogicTable
        canEdit={canEdit}
        onChange={setEditedScenes}
        scenes={editedScenes}
      />

      <section className="show-logic-section" aria-label="Autonomous logic validation">
        <h4>Publication-blocking logic analysis</h4>
        <ul>
          {diagnostics.map((diagnostic) => (
            <li
              className={`show-logic-diagnostic show-logic-diagnostic--${diagnostic.severity}`}
              key={`${diagnostic.code}:${diagnostic.message}`}
            >
              <code>{diagnostic.code}</code> · {diagnostic.message}
            </li>
          ))}
        </ul>
        <small>
          Quiet hours and wall-clock schedules are intentionally unavailable:
          the runtime does not yet define an authoritative scheduling model.
          The scheduled template organizes the pool but cannot author a clock
          trigger.
        </small>
      </section>

      <section className="show-logic-section" aria-label="Autonomous simulation analysis">
        <h4>Deterministic accelerated analysis</h4>
        <button disabled={busy || hasErrors} onClick={() => void runAnalysis(false)}>
          Run thirty-minute analysis
        </button>
        <button disabled={busy || hasErrors} onClick={() => void runAnalysis(true)}>
          Run visitor interruption rehearsal
        </button>
        {analysis && (
          <AutonomousAnalysis
            analysis={analysis}
            diagnostics={diagnostics}
            program={program}
          />
        )}
      </section>
      {error && <p role="alert">{error}</p>}
      <small>
        States: {states.join(", ")} · editor uses semantic audience events and
        context, never raw detector payload expressions.
      </small>
    </details>
  );
}

function PoolEditor({
  canEdit,
  onChange,
  onRemove,
  pool,
  scenes,
}: {
  canEdit: boolean;
  onChange: (pool: ScenePoolDefinition) => void;
  onRemove: () => void;
  pool: ScenePoolDefinition;
  scenes: Scene[];
}) {
  return (
    <article className="show-logic-pool">
      <label>
        Pool ID
        <input
          aria-label={`Pool ID ${pool.id}`}
          disabled={!canEdit}
          value={pool.id}
          onChange={(event) => onChange({ ...pool, id: event.target.value })}
        />
      </label>
      <label>
        Category
        <input
          aria-label={`Pool category ${pool.id}`}
          disabled={!canEdit}
          value={pool.category}
          onChange={(event) =>
            onChange({ ...pool, category: event.target.value })
          }
        />
      </label>
      <button disabled={!canEdit} onClick={onRemove}>
        Remove pool
      </button>
      {pool.scenes.map((entry, index) => (
        <ProgramEntryEditor
          canEdit={canEdit}
          entry={entry}
          key={`${entry.scene_id}:${index}`}
          onChange={(updated) =>
            onChange({
              ...pool,
              scenes: pool.scenes.map((candidate, candidateIndex) =>
                candidateIndex === index ? updated : candidate,
              ),
            })
          }
          onRemove={() =>
            onChange({
              ...pool,
              scenes: pool.scenes.filter(
                (_, candidateIndex) => candidateIndex !== index,
              ),
            })
          }
          scenes={scenes}
        />
      ))}
      <button
        disabled={!canEdit || !scenes.length}
        onClick={() =>
          onChange({
            ...pool,
            scenes: [
              ...pool.scenes,
              {
                scene_id: scenes.find(
                  (scene) =>
                    !pool.scenes.some((entry) => entry.scene_id === scene.id),
                )?.id ?? scenes[0].id,
                weight: 1,
                cooldown_ms: 0,
                available: true,
                conditions: {},
              },
            ],
          })
        }
      >
        Add scene to pool
      </button>
    </article>
  );
}

function ProgramEntryEditor({
  canEdit,
  entry,
  onChange,
  onRemove,
  scenes,
}: {
  canEdit: boolean;
  entry: ProgramSceneDefinition;
  onChange: (entry: ProgramSceneDefinition) => void;
  onRemove: () => void;
  scenes: Scene[];
}) {
  return (
    <div className="show-logic-program-entry">
      <select
        aria-label="Pool scene"
        disabled={!canEdit}
        value={entry.scene_id}
        onChange={(event) =>
          onChange({ ...entry, scene_id: event.target.value })
        }
      >
        {scenes.map((scene) => (
          <option key={scene.id} value={scene.id}>
            {scene.name}
          </option>
        ))}
      </select>
      <label>
        Weight
        <input
          aria-label={`Weight for ${entry.scene_id}`}
          disabled={!canEdit}
          min="1"
          max="1000"
          type="number"
          value={entry.weight}
          onChange={(event) =>
            onChange({ ...entry, weight: Number(event.target.value) })
          }
        />
      </label>
      <label>
        Cooldown ms
        <input
          aria-label={`Cooldown for ${entry.scene_id}`}
          disabled={!canEdit}
          min="0"
          type="number"
          value={entry.cooldown_ms}
          onChange={(event) =>
            onChange({ ...entry, cooldown_ms: Number(event.target.value) })
          }
        />
      </label>
      <label>
        Authored availability
        <input
          aria-label={`Available ${entry.scene_id}`}
          checked={entry.available}
          disabled={!canEdit}
          type="checkbox"
          onChange={(event) =>
            onChange({ ...entry, available: event.target.checked })
          }
        />
      </label>
      <SemanticConditions
        canEdit={canEdit}
        conditions={entry.conditions}
        onChange={(conditions) => onChange({ ...entry, conditions })}
      />
      <button disabled={!canEdit} onClick={onRemove}>
        Remove entry
      </button>
    </div>
  );
}

function SemanticConditions({
  canEdit,
  conditions,
  onChange,
}: {
  canEdit: boolean;
  conditions: Record<string, string | number | boolean>;
  onChange: (conditions: Record<string, string | number | boolean>) => void;
}) {
  const entries = Object.entries(conditions);
  return (
    <div className="show-logic-conditions">
      <strong>Semantic eligibility</strong>
      {entries.map(([key, value], index) => (
        <div key={`${key}:${index}`}>
          <select
            aria-label="Semantic condition"
            disabled={!canEdit}
            value={key}
            onChange={(event) => {
              const updated = { ...conditions };
              delete updated[key];
              updated[event.target.value] = value;
              onChange(updated);
            }}
          >
            {SEMANTIC_CONDITIONS.map((condition) => (
              <option key={condition}>{condition}</option>
            ))}
          </select>
          <input
            aria-label={`Semantic condition value ${key}`}
            disabled={!canEdit}
            value={String(value)}
            onChange={(event) =>
              onChange({
                ...conditions,
                [key]: semanticValue(key, event.target.value),
              })
            }
          />
          <button
            aria-label={`Remove semantic condition ${key}`}
            disabled={!canEdit}
            onClick={() => {
              const updated = { ...conditions };
              delete updated[key];
              onChange(updated);
            }}
          >
            Remove
          </button>
        </div>
      ))}
      <button
        disabled={!canEdit || entries.length >= SEMANTIC_CONDITIONS.length}
        onClick={() => {
          const key =
            SEMANTIC_CONDITIONS.find(
              (candidate) => !(candidate in conditions),
            ) ?? "audience_state";
          onChange({
            ...conditions,
            [key]: key === "audience" ? true : key === "audience_state" ? "present" : 0,
          });
        }}
      >
        Add semantic condition
      </button>
    </div>
  );
}

function AudienceEditor({
  audience,
  canEdit,
  onChange,
}: {
  audience: AudiencePresenceDefinition;
  canEdit: boolean;
  onChange: (audience: AudiencePresenceDefinition) => void;
}) {
  return (
    <section className="show-logic-section" aria-label="Audience presence model">
      <h4>Semantic audience presence</h4>
      {(
        [
          ["Labels", "labels"],
          ["Cameras", "cameras"],
          ["Zones", "zones"],
        ] as const
      ).map(([label, field]) => (
        <label key={field}>
          {label}
          <input
            aria-label={`Audience ${label.toLowerCase()}`}
            disabled={!canEdit}
            value={audience[field].join(", ")}
            onChange={(event) =>
              onChange({
                ...audience,
                [field]: commaList(event.target.value),
              })
            }
          />
        </label>
      ))}
      {(
        [
          ["Observation freshness", "observation_freshness_ms"],
          ["Arrival confirmation", "arrival_confirmation_ms"],
          ["Absence grace", "absence_grace_ms"],
          ["Long idle", "long_idle_ms"],
        ] as const
      ).map(([label, field]) => (
        <label key={field}>
          {label} ms
          <input
            aria-label={`Audience ${label.toLowerCase()}`}
            disabled={!canEdit}
            min={field === "arrival_confirmation_ms" || field === "absence_grace_ms" ? 0 : 1}
            type="number"
            value={audience[field]}
            onChange={(event) =>
              onChange({ ...audience, [field]: Number(event.target.value) })
            }
          />
        </label>
      ))}
      <small>
        Detector payloads are reduced to presence, absence, duration, and prior
        absence context before program eligibility is evaluated.
      </small>
    </section>
  );
}

function OpportunityEditor({
  canEdit,
  onChange,
  onRemove,
  opportunity,
  scenes,
}: {
  canEdit: boolean;
  onChange: (opportunity: OpportunityDefinition) => void;
  onRemove: () => void;
  opportunity: OpportunityDefinition;
  scenes: Scene[];
}) {
  return (
    <article className="show-logic-opportunity">
      <label>
        Opportunity ID
        <input
          disabled={!canEdit}
          value={opportunity.id}
          onChange={(event) =>
            onChange({ ...opportunity, id: event.target.value })
          }
        />
      </label>
      <label>
        Trigger
        <select
          aria-label={`Opportunity trigger ${opportunity.id}`}
          disabled={!canEdit}
          value={opportunity.trigger_event}
          onChange={(event) =>
            onChange({ ...opportunity, trigger_event: event.target.value })
          }
        >
          {SEMANTIC_EVENTS.map((eventType) => (
            <option key={eventType}>{eventType}</option>
          ))}
          {!SEMANTIC_EVENTS.some((eventType) => eventType === opportunity.trigger_event) &&
            <option value={opportunity.trigger_event}>Custom event: {opportunity.trigger_event}</option>}
        </select>
      </label>
      <label>
        Scene
        <select
          aria-label={`Opportunity scene ${opportunity.id}`}
          disabled={!canEdit}
          value={opportunity.scene_id}
          onChange={(event) =>
            onChange({ ...opportunity, scene_id: event.target.value })
          }
        >
          {scenes.map((scene) => (
            <option key={scene.id} value={scene.id}>
              {scene.name}
            </option>
          ))}
        </select>
      </label>
      {(
        [
          ["Opportunity lifetime", "ttl_ms"],
          ["Semantic event lifetime", "event_ttl_ms"],
          ["Qualification window", "qualification_window_ms"],
          ["Minimum presence", "min_presence_ms"],
          ["Prior absence", "min_absence_before_arrival_ms"],
          ["Acknowledgement cooldown", "acknowledgement_cooldown_ms"],
          ["Priority", "priority"],
        ] as const
      ).map(([label, field]) => (
        <label key={field}>
          {label}
          <input
            aria-label={`${label} ${opportunity.id}`}
            disabled={!canEdit}
            min="0"
            type="number"
            value={opportunity[field]}
            onChange={(event) =>
              onChange({
                ...opportunity,
                [field]: Number(event.target.value),
              })
            }
          />
        </label>
      ))}
      <label>
        Required audience state
        <select
          aria-label={`Required audience state ${opportunity.id}`}
          disabled={!canEdit}
          value={String(opportunity.required_context.audience_state ??
            (opportunity.required_context.audience === true ? "present" :
              opportunity.required_context.audience === false ? "absent" : ""))}
          onChange={(event) => {
            const context = { ...opportunity.required_context };
            delete context.audience;
            delete context.audience_state;
            onChange({
              ...opportunity,
              required_context: event.target.value
                ? { ...context, audience_state: event.target.value }
                : context,
            });
          }}
        >
          <option value="">Any semantic state</option>
          <option value="absent">Absent</option>
          <option value="candidate">Candidate</option>
          <option value="present">Present</option>
        </select>
      </label>
      <button disabled={!canEdit} onClick={onRemove}>
        Remove opportunity
      </button>
    </article>
  );
}

function EncounterSettings({
  canEdit,
  encounter,
  onChange,
  scenes,
}: {
  canEdit: boolean;
  encounter: EncounterDefinition;
  onChange: (encounter: EncounterDefinition) => void;
  scenes: Scene[];
}) {
  return (
    <div className="show-logic-encounter-settings">
      <label>
        Initial state
        <input
          aria-label="Encounter initial state"
          disabled={!canEdit}
          value={encounter.initial_state}
          onChange={(event) =>
            onChange({ ...encounter, initial_state: event.target.value })
          }
        />
      </label>
      <label>
        Cooldown state
        <input
          aria-label="Encounter cooldown state"
          disabled={!canEdit}
          value={encounter.cooldown_state}
          onChange={(event) =>
            onChange({ ...encounter, cooldown_state: event.target.value })
          }
        />
      </label>
      <label>
        Ambient recovery scene
        <select
          aria-label="Encounter ambient scene"
          disabled={!canEdit}
          value={encounter.ambient_scene_id}
          onChange={(event) =>
            onChange({ ...encounter, ambient_scene_id: event.target.value })
          }
        >
          {scenes.map((scene) => (
            <option key={scene.id} value={scene.id}>
              {scene.name}
            </option>
          ))}
        </select>
      </label>
      {(
        [
          ["Encounter timeout", "encounter_timeout_ms"],
          ["Encounter cooldown", "cooldown_ms"],
          ["Event debounce", "debounce_ms"],
          ["Event lifetime", "event_ttl_ms"],
        ] as const
      ).map(([label, field]) => (
        <label key={field}>
          {label}
          <input
            aria-label={label}
            disabled={!canEdit}
            min="0"
            type="number"
            value={encounter[field]}
            onChange={(event) =>
              onChange({ ...encounter, [field]: Number(event.target.value) })
            }
          />
        </label>
      ))}
    </div>
  );
}

function EncounterGraph({ encounter }: { encounter: EncounterDefinition }) {
  const states = encounterStates(encounter);
  const positions = new Map(
    states.map((state, index) => {
      const angle = (index * Math.PI * 2) / Math.max(1, states.length);
      return [
        state,
        [300 + Math.cos(angle) * 210, 170 + Math.sin(angle) * 120] as const,
      ] as const;
    }),
  );
  return (
    <svg
      aria-label="Encounter node flow"
      className="show-logic-encounter-graph"
      role="img"
      viewBox="0 0 600 340"
    >
      {encounter.transitions.flatMap((transition, transitionIndex) =>
        transition.from_states.map((fromState) => {
          const from = positions.get(fromState);
          const to = positions.get(transition.to_state);
          return from && to ? (
            <g key={`${transitionIndex}:${fromState}`}>
              <line
                className="show-logic-edge"
                x1={from[0]}
                x2={to[0]}
                y1={from[1]}
                y2={to[1]}
              />
              <text
                className="show-logic-edge-label"
                x={(from[0] + to[0]) / 2}
                y={(from[1] + to[1]) / 2}
              >
                {transition.event_type}
              </text>
            </g>
          ) : [];
        }),
      )}
      {states.map((state) => {
        const position = positions.get(state)!;
        return (
          <g key={state}>
            <rect
              className={
                state === encounter.initial_state
                  ? "show-logic-node show-logic-node--initial"
                  : state === encounter.cooldown_state
                    ? "show-logic-node show-logic-node--cooldown"
                    : "show-logic-node"
              }
              height="42"
              rx="8"
              width="120"
              x={position[0] - 60}
              y={position[1] - 21}
            />
            <text
              className="show-logic-node-label"
              textAnchor="middle"
              x={position[0]}
              y={position[1] + 4}
            >
              {state}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function EncounterTransitionRow({
  canEdit,
  onChange,
  onRemove,
  scenes,
  transition,
}: {
  canEdit: boolean;
  onChange: (transition: EncounterTransitionDefinition) => void;
  onRemove: () => void;
  scenes: Scene[];
  transition: EncounterTransitionDefinition;
}) {
  return (
    <tr>
      <td>
        <select
          aria-label="Encounter transition event"
          disabled={!canEdit}
          value={transition.event_type}
          onChange={(event) =>
            onChange({ ...transition, event_type: event.target.value })
          }
        >
          {SEMANTIC_EVENTS.map((eventType) => (
            <option key={eventType}>{eventType}</option>
          ))}
        </select>
      </td>
      <td>
        <input
          aria-label="Encounter transition from states"
          disabled={!canEdit}
          value={transition.from_states.join(", ")}
          onChange={(event) =>
            onChange({
              ...transition,
              from_states: commaList(event.target.value),
            })
          }
        />
      </td>
      <td>
        <input
          aria-label="Encounter transition to state"
          disabled={!canEdit}
          value={transition.to_state}
          onChange={(event) =>
            onChange({ ...transition, to_state: event.target.value })
          }
        />
      </td>
      <td>
        <select
          aria-label="Encounter transition scene"
          disabled={!canEdit}
          value={transition.scene_id ?? ""}
          onChange={(event) =>
            onChange({ ...transition, scene_id: event.target.value || null })
          }
        >
          <option value="">No scene request</option>
          {scenes.map((scene) => (
            <option key={scene.id} value={scene.id}>
              {scene.name}
            </option>
          ))}
        </select>
      </td>
      <td>
        <input
          aria-label="Encounter transition minimum age"
          disabled={!canEdit}
          min="0"
          type="number"
          value={transition.min_state_age_ms}
          onChange={(event) =>
            onChange({
              ...transition,
              min_state_age_ms: Number(event.target.value),
            })
          }
        />
      </td>
      <td>
        <button disabled={!canEdit} onClick={onRemove}>
          Remove transition
        </button>
      </td>
    </tr>
  );
}

function SceneLogicTable({
  canEdit,
  onChange,
  scenes,
}: {
  canEdit: boolean;
  onChange: (scenes: Scene[]) => void;
  scenes: Scene[];
}) {
  return (
    <section className="show-logic-section" aria-label="Scene transition analysis">
      <h4>Scene completion, fallback, arbitration, and resources</h4>
      <div
        className="show-logic-table-scroll"
        role="region"
        aria-label="Scene transition details"
        tabIndex={0}
      >
        <table>
          <thead>
            <tr>
              <th>Scene</th>
              <th>Completion</th>
              <th>Target</th>
              <th>Failure fallback</th>
              <th>Priority</th>
              <th>Interruption</th>
              <th>Safe points</th>
              <th>Resources</th>
            </tr>
          </thead>
          <tbody>
            {scenes.map((scene, index) => {
              const completion =
                typeof scene.completion === "object" && scene.completion
                  ? scene.completion
                  : {};
              const scheduling =
                typeof scene.scheduling === "object" && scene.scheduling
                  ? scene.scheduling
                  : {};
              return (
                <tr key={scene.id}>
                  <td>{scene.name}</td>
                  <td>{String(completion.policy ?? "stop")}</td>
                  <td>{String(completion.target_scene_id ?? "director / none")}</td>
                  <td>
                    <select
                      aria-label={`Failure fallback ${scene.id}`}
                      disabled={!canEdit}
                      value={scene.failure_scene_id ?? ""}
                      onChange={(event) =>
                        onChange(
                          scenes.map((candidate, candidateIndex) =>
                            candidateIndex === index
                              ? {
                                ...candidate,
                                failure_scene_id: event.target.value || null,
                              }
                              : candidate,
                          ),
                        )
                      }
                    >
                      <option value="">Missing fallback</option>
                      {scenes
                        .filter((candidate) => candidate.id !== scene.id)
                        .map((candidate) => (
                          <option key={candidate.id} value={candidate.id}>
                            {candidate.name}
                          </option>
                        ))}
                    </select>
                  </td>
                  <td>{String(scene.priority ?? 50)}</td>
                  <td>{String(scene.interruption ?? "replace_by_priority")}</td>
                  <td>
                    {Array.isArray(scheduling.safe_points_ms)
                      ? scheduling.safe_points_ms.join(", ") || "none"
                      : "none"}
                  </td>
                  <td>{scene.resources?.join(", ") || "none"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function AutonomousAnalysis({
  analysis,
  diagnostics,
  program,
}: {
  analysis: SimulationRun;
  diagnostics: Diagnostic[];
  program: ShowProgramDefinition;
}) {
  const history = Array.isArray(analysis.actual.scene_history)
    ? analysis.actual.scene_history.filter(
        (value): value is string => typeof value === "string",
      )
    : [];
  const counts = new Map<string, number>();
  let longestRepeat = 0;
  let currentRepeat = 0;
  let previous = "";
  for (const sceneId of history) {
    counts.set(sceneId, (counts.get(sceneId) ?? 0) + 1);
    currentRepeat = sceneId === previous ? currentRepeat + 1 : 1;
    longestRepeat = Math.max(longestRepeat, currentRepeat);
    previous = sceneId;
  }
  const programScenes = new Set(
    program.pools.flatMap((pool) => pool.scenes.map((entry) => entry.scene_id)),
  );
  const starved = [...programScenes].filter((sceneId) => !counts.has(sceneId));
  const unavailable = program.pools.flatMap((pool) =>
    pool.scenes
      .filter((entry) => !entry.available)
      .map((entry) => entry.scene_id),
  );
  const status =
    typeof analysis.actual.status === "object" && analysis.actual.status
      ? (analysis.actual.status as Record<string, unknown>)
      : {};
  const director =
    typeof status.director === "object" && status.director
      ? (status.director as Record<string, unknown>)
      : {};
  const eligibility =
    typeof director.eligibility === "object" && director.eligibility
      ? (director.eligibility as Record<string, unknown>)
      : {};
  const cooldowns = Object.entries(eligibility).flatMap(([sceneId, value]) => {
    if (typeof value !== "object" || value === null) {
      return [];
    }
    const remaining = (value as Record<string, unknown>).cooldown_remaining_ms;
    return typeof remaining === "number" && remaining > 0
      ? [`${sceneId}=${remaining} ms`]
      : [];
  });
  const resourceConflicts = diagnostics.filter(
    (diagnostic) => diagnostic.code === "resource_conflict",
  );
  return (
    <output className="show-logic-analysis" aria-label="Autonomous analysis result">
      <strong>{analysis.passed ? "Deterministic run complete" : "Expected outcome mismatch"}</strong>
      <span>Scene selections: {history.length}</span>
      <span>Longest immediate repetition: {longestRepeat}</span>
      <span>Starved program scenes: {starved.join(", ") || "none"}</span>
      <span>Authored unavailable scenes: {unavailable.join(", ") || "none"}</span>
      <span>Active cooldowns: {cooldowns.join(", ") || "none"}</span>
      <span>Unavailable-resource conflicts: {resourceConflicts.length}</span>
      <span>
        Empty eligible-pool events: {String(director.no_eligible_scene_count ?? 0)}
      </span>
      <span>
        Dead end: {status.active_scene === null ? "yes" : "no"}
      </span>
      <span>
        Scene counts:{" "}
        {[...counts.entries()]
          .map(([sceneId, count]) => `${sceneId}=${count}`)
          .join(", ") || "none"}
      </span>
      <small>
        Hardware adapters opened: no · hardware commands sent: no · saved run
        revision {analysis.source_revision}
      </small>
    </output>
  );
}

function initialProgram(draft: ProjectDraftRecord): ShowProgramDefinition {
  const first = draft.draft.scenes[0]?.id ?? "";
  return structuredClone(
    draft.draft.program ?? {
      schema_version: 1,
      initial_scene_id: first,
      default_pool_id: "ambient",
      recent_history_size: 1,
      random_seed: 2026,
      pools: [
        {
          id: "ambient",
          category: "ambient",
          scenes: first
            ? [
                {
                  scene_id: first,
                  weight: 1,
                  cooldown_ms: 0,
                  available: true,
                  conditions: {},
                },
              ]
            : [],
        },
      ],
    },
  );
}

function initialAudience(draft: ProjectDraftRecord): AudiencePresenceDefinition {
  return structuredClone(
    draft.draft.audience ?? {
      schema_version: 1,
      labels: ["person"],
      cameras: ["doorbell"],
      zones: ["Porch"],
      observation_freshness_ms: 5000,
      arrival_confirmation_ms: 1000,
      absence_grace_ms: 5000,
      long_idle_ms: 300000,
    },
  );
}

function initialOpportunities(
  draft: ProjectDraftRecord,
): OpportunityProgramDefinition {
  return structuredClone(
    draft.draft.opportunities ?? {
      schema_version: 1,
      opportunities: [
        defaultOpportunity("visitor", draft.draft.scenes[0]?.id ?? ""),
      ],
    },
  );
}

function initialEncounter(draft: ProjectDraftRecord): EncounterDefinition {
  const ambient = draft.draft.scenes[0]?.id ?? "";
  return structuredClone(
    draft.draft.encounter ?? {
      schema_version: 1,
      initial_state: "idle",
      cooldown_state: "cooldown",
      ambient_scene_id: ambient,
      encounter_timeout_ms: 30000,
      cooldown_ms: 10000,
      debounce_ms: 500,
      event_ttl_ms: 5000,
      transitions: [
        {
          event_type: "audience.arrived",
          from_states: ["idle"],
          to_state: "engaged",
          scene_id: ambient || null,
          min_state_age_ms: 0,
          attributes: {},
        },
        {
          event_type: "audience.departed",
          from_states: ["engaged"],
          to_state: "cooldown",
          scene_id: null,
          min_state_age_ms: 0,
          attributes: {},
        },
      ],
    },
  );
}

function defaultOpportunity(
  id: string,
  sceneId: string,
): OpportunityDefinition {
  return {
    id,
    scene_id: sceneId,
    trigger_event: "audience.arrived",
    trigger_attributes: {},
    required_context: { audience_state: "present" },
    ttl_ms: 30000,
    event_ttl_ms: 3000,
    qualification_window_ms: 30000,
    min_presence_ms: 0,
    min_absence_before_arrival_ms: 0,
    acknowledgement_cooldown_ms: 60000,
    priority: 80,
  };
}

function applyTemplate(
  kind: "ambient" | "visitor" | "manual" | "scheduled",
  scenes: Scene[],
  setScenes: (scenes: Scene[]) => void,
  setProgram: (program: ShowProgramDefinition) => void,
  setAudience: (audience: AudiencePresenceDefinition) => void,
  setOpportunities: (opportunities: OpportunityProgramDefinition) => void,
  setEncounter: (encounter: EncounterDefinition) => void,
) {
  const initial = scenes[0]?.id ?? "";
  const interaction = scenes[1]?.id ?? initial;
  const poolId = kind === "scheduled" ? "scheduled-main" : "ambient";
  setProgram({
    schema_version: 1,
    initial_scene_id: initial,
    default_pool_id: poolId,
    recent_history_size: kind === "manual" ? 0 : 1,
    random_seed: 2026,
    pools: [
      {
        id: poolId,
        category: kind,
        scenes: scenes.map((scene, index): ProgramSceneDefinition => ({
          scene_id: scene.id,
          weight: index === 0 ? 3 : 1,
          cooldown_ms: kind === "visitor" && index > 0 ? 60000 : 0,
          available: true,
          conditions:
            kind === "visitor" && index > 0
              ? { audience_state: "present" }
              : {},
        })),
      },
    ],
  });
  setAudience({
    schema_version: 1,
    labels: ["person"],
    cameras: ["doorbell"],
    zones: ["Porch"],
    observation_freshness_ms: 5000,
    arrival_confirmation_ms: 1000,
    absence_grace_ms: 5000,
    long_idle_ms: 300000,
  });
  setOpportunities({
    schema_version: 1,
    opportunities: [defaultOpportunity("visitor-arrival", interaction)],
  });
  setEncounter({
    schema_version: 1,
    initial_state: "idle",
    cooldown_state: "cooldown",
    ambient_scene_id: initial,
    encounter_timeout_ms: 30000,
    cooldown_ms: 10000,
    debounce_ms: 500,
    event_ttl_ms: 5000,
    transitions: [
      {
        event_type: "audience.arrived",
        from_states: ["idle"],
        to_state: "engaged",
        scene_id: kind === "visitor" ? interaction : null,
        min_state_age_ms: 0,
        attributes: {},
      },
      {
        event_type: "audience.departed",
        from_states: ["engaged"],
        to_state: "cooldown",
        scene_id: null,
        min_state_age_ms: 0,
        attributes: {},
      },
    ],
  });
  setScenes(
    scenes.map((scene, index): Scene => ({
      ...scene,
      priority: kind === "manual" ? 90 : index === 0 ? 20 : 70,
      interruption: kind === "manual" ? "ignore" : "replace_by_priority",
      completion:
        kind === "manual"
          ? { policy: "hold" }
          : { policy: "select_next" },
      failure_scene_id:
        scenes.length < 2
          ? null
          : index === 0
            ? interaction
            : initial,
    })),
  );
}

function showLogicDiagnostics(
  scenes: Scene[],
  program: ShowProgramDefinition,
  audience: AudiencePresenceDefinition,
  opportunities: OpportunityProgramDefinition,
  encounter: EncounterDefinition,
  installation: unknown,
): Diagnostic[] {
  const diagnostics: Diagnostic[] = [];
  const sceneIds = new Set(scenes.map((scene) => scene.id));
  const add = (
    code: string,
    message: string,
    severity: Diagnostic["severity"] = "error",
  ) => diagnostics.push({ code, message, severity });
  if (!sceneIds.has(program.initial_scene_id)) {
    add("invalid_initial_scene", "Program initial scene does not exist.");
  }
  if (!program.pools.length) {
    add("program_pools_empty", "Program requires at least one scene pool.");
  }
  if (!program.pools.some((pool) => pool.id === program.default_pool_id)) {
    add("invalid_default_pool", "Program default pool does not exist.");
  }
  for (const pool of program.pools) {
    if (!pool.scenes.length) {
      add("empty_pool", `Pool ${pool.id} has no scenes.`);
    }
    if (!pool.scenes.some((entry) => entry.available)) {
      add("empty_eligible_pool", `Pool ${pool.id} has no authored available scene.`);
    }
    for (const entry of pool.scenes) {
      if (!sceneIds.has(entry.scene_id)) {
        add("invalid_pool_scene", `Pool ${pool.id} references ${entry.scene_id}.`);
      }
      if (
        entry.conditions.audience_state &&
        !["absent", "candidate", "present"].includes(
          String(entry.conditions.audience_state),
        )
      ) {
        add(
          "invalid_audience_condition",
          `${entry.scene_id} uses an unknown semantic audience state.`,
        );
      }
    }
  }
  if (!audience.labels.length || (!audience.cameras.length && !audience.zones.length)) {
    add(
      "audience_scope_empty",
      "Audience presence needs a label plus at least one camera or zone.",
    );
  }
  if (!opportunities.opportunities.length) {
    add(
      "opportunities_empty",
      "Autonomous logic requires at least one engagement opportunity.",
    );
  }
  for (const opportunity of opportunities.opportunities) {
    if (!sceneIds.has(opportunity.scene_id)) {
      add(
        "invalid_opportunity_scene",
        `Opportunity ${opportunity.id} references ${opportunity.scene_id}.`,
      );
    }
  }
  if (!encounter.transitions.length) {
    add(
      "encounter_transitions_empty",
      "Encounter logic requires at least one transition.",
    );
  }
  const states = new Set(encounterStates(encounter));
  if (!states.has(encounter.initial_state) || !states.has(encounter.cooldown_state)) {
    add("invalid_encounter_state", "Encounter initial or cooldown state is invalid.");
  }
  const reachableStates = new Set([encounter.initial_state]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const transition of encounter.transitions) {
      if (
        transition.from_states.some((state) => reachableStates.has(state)) &&
        !reachableStates.has(transition.to_state)
      ) {
        reachableStates.add(transition.to_state);
        changed = true;
      }
    }
  }
  for (const state of states) {
    if (state !== encounter.cooldown_state && !reachableStates.has(state)) {
      add("unreachable_encounter_state", `Encounter state ${state} is unreachable.`);
    }
  }
  const reachableScenes = new Set<string>([program.initial_scene_id]);
  program.pools.forEach((pool) =>
    pool.scenes.forEach((entry) => reachableScenes.add(entry.scene_id)),
  );
  opportunities.opportunities.forEach((item) => reachableScenes.add(item.scene_id));
  encounter.transitions.forEach((transition) => {
    if (transition.scene_id) {
      reachableScenes.add(transition.scene_id);
    }
  });
  for (const scene of scenes) {
    const completion =
      typeof scene.completion === "object" && scene.completion
        ? scene.completion
        : {};
    if (completion.target_scene_id) {
      reachableScenes.add(String(completion.target_scene_id));
    }
    if (!scene.failure_scene_id) {
      add(
        "missing_failure_fallback",
        `Scene ${scene.name} has no media failure fallback.`,
        "warning",
      );
    } else if (!sceneIds.has(scene.failure_scene_id)) {
      add(
        "invalid_failure_fallback",
        `Scene ${scene.name} references missing fallback ${scene.failure_scene_id}.`,
      );
    } else if (scene.failure_scene_id === scene.id) {
      add(
        "self_failure_fallback",
        `Scene ${scene.name} cannot use itself as its media failure fallback.`,
      );
    }
    if (!reachableScenes.has(scene.id)) {
      add("unreachable_scene", `Scene ${scene.name} is unreachable.`, "warning");
    }
  }
  const resourceDefinitions =
    typeof installation === "object" &&
    installation !== null &&
    "resources" in installation &&
    Array.isArray((installation as { resources: unknown }).resources)
      ? (installation as { resources: { resource?: unknown }[] }).resources
      : [];
  const definedResources = new Set(
    resourceDefinitions.flatMap((resource) =>
      typeof resource.resource === "string" ? [resource.resource] : [],
    ),
  );
  for (const scene of scenes) {
    const missing = (scene.resources ?? []).filter(
      (resource) => !definedResources.has(resource),
    );
    if (missing.length) {
      add(
        "resource_conflict",
        `Scene ${scene.name} claims unavailable resources: ${missing.join(", ")}.`,
      );
    }
  }
  const completionEdges = new Map<string, string>();
  for (const scene of scenes) {
    const completion =
      typeof scene.completion === "object" && scene.completion
        ? scene.completion
        : {};
    if (
      completion.policy === "transition_to" &&
      typeof completion.target_scene_id === "string"
    ) {
      completionEdges.set(scene.id, completion.target_scene_id);
    }
  }
  for (const start of completionEdges.keys()) {
    const seen = new Set<string>();
    let current: string | undefined = start;
    while (current && completionEdges.has(current)) {
      if (seen.has(current)) {
        add(
          "cyclic_completion",
          `Completion transitions form a cycle through ${current}.`,
          "warning",
        );
        break;
      }
      seen.add(current);
      current = completionEdges.get(current);
    }
  }
  add(
    "scheduling_model_unavailable",
    "Quiet hours and wall-clock scheduling await an authoritative runtime model.",
    "info",
  );
  return diagnostics;
}

function encounterStates(encounter: EncounterDefinition): string[] {
  return [
    ...new Set([
      encounter.initial_state,
      encounter.cooldown_state,
      ...encounter.transitions.flatMap((transition) => [
        ...transition.from_states,
        transition.to_state,
      ]),
    ]),
  ].filter(Boolean);
}

function semanticValue(
  key: string,
  value: string,
): string | number | boolean {
  if (key === "audience") {
    return value === "true";
  }
  if (
    key === "audience_active_objects" ||
    key === "audience_present_ms" ||
    key === "audience_previous_absence_ms"
  ) {
    return Number(value);
  }
  return value;
}

function commaList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function acceptanceSimulationSteps(
  audience: AudiencePresenceDefinition,
  program: ShowProgramDefinition,
  opportunities: OpportunityProgramDefinition,
  scenes: Scene[],
): SimulationStep[] {
  const camera = audience.cameras[0] ?? "camera";
  const zone = audience.zones[0] ?? "zone";
  const interactionScene =
    opportunities.opportunities[0]?.scene_id ??
    scenes[1]?.id ??
    program.initial_scene_id;
  return [
    {
      kind: "observe",
      event_type: "frigate.zone.entered",
      attributes: {
        source: "synthetic",
        object_id: "visitor-1",
        label: audience.labels[0] ?? "person",
        camera,
        zone,
      },
    },
    {
      kind: "advance",
      delta_ms: audience.arrival_confirmation_ms + 100,
    },
    { kind: "request_scene", scene_id: interactionScene },
    { kind: "advance", delta_ms: 1000 },
    { kind: "disconnect" },
    { kind: "reconnect" },
    {
      kind: "observe",
      event_type: "frigate.object.end",
      attributes: {
        source: "synthetic",
        object_id: "visitor-1",
        label: audience.labels[0] ?? "person",
        camera,
        zone,
      },
    },
    {
      kind: "advance",
      delta_ms: audience.absence_grace_ms + 10000,
    },
    { kind: "operator_start", scene_id: program.initial_scene_id },
  ];
}
