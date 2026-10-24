import { useEffect, useState } from "react";

import {
  getInstallationMapperExport,
  getSimulationScenarios,
  ProjectDraftRecord,
  runSimulationScenario,
  saveSimulationScenario,
  SimulationRun,
  SimulationScenario,
  SimulationStep,
} from "../api";

/** Repeatable no-hardware controller scenarios with synthetic events and injected faults. */
export function SimulationScenarioPanel({
  draft,
  projectId,
  canEdit,
  sceneId,
}: {
  draft: ProjectDraftRecord;
  projectId: string;
  canEdit: boolean;
  sceneId?: string;
}) {
  const [simulationScenarios, setSimulationScenarios] = useState<SimulationScenario[]>([]);
  const [simulationScenarioName, setSimulationScenarioName] = useState("Rehearsal scenario");
  const [simulationSteps, setSimulationSteps] = useState<SimulationStep[]>([]);
  const [simulationExpected, setSimulationExpected] = useState("{}");
  const [simulationAdvanceMs, setSimulationAdvanceMs] = useState(1000);
  const [simulationEventType, setSimulationEventType] = useState("synthetic.observation");
  const [simulationEventAttributes, setSimulationEventAttributes] = useState("{}");
  const [simulationOutputResource, setSimulationOutputResource] = useState("");
  const [simulationSceneId, setSimulationSceneId] = useState(
    sceneId || draft.draft.scenes[0]?.id || "",
  );
  const [simulationRun, setSimulationRun] = useState<SimulationRun | null>(null);
  const [simulationError, setSimulationError] = useState<string | null>(null);
  const [pixelOutputs, setPixelOutputs] = useState<string[]>([]);
  const installationId =
    typeof draft.draft.editor_metadata.installation_id === "string"
      ? draft.draft.editor_metadata.installation_id
      : null;

  useEffect(() => {
    let active = true;
    getSimulationScenarios(projectId)
      .then((scenarios) => {
        if (active) {
          setSimulationScenarios(scenarios);
          setSimulationError(null);
        }
      })
      .catch((error) => {
        if (active) {
          setSimulationError(
            error instanceof Error ? error.message : "Simulation scenarios are unavailable.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, [projectId]);

  useEffect(() => {
    if (!installationId) return;
    let active = true;
    getInstallationMapperExport(installationId)
      .then((exported) => {
        if (!active) return;
        const outputs = exported.resources
          .filter((resource) => resource.kind === "pixel_output")
          .map((resource) => resource.resource);
        setPixelOutputs(outputs);
        setSimulationOutputResource((current) =>
          current && outputs.includes(current) ? current : outputs[0] ?? "",
        );
      })
      .catch(() => {
        if (active) setPixelOutputs([]);
      });
    return () => {
      active = false;
    };
  }, [installationId]);

  return (
    <details
      className="timeline-simulation-scenarios"
      aria-label="Repeatable simulation scenarios"
    >
      <summary>Fault-injection test scenarios (advanced)</summary>
      <small>
        Runs the compiled controller with fake media clients, in-memory lighting
        sinks, synthetic observations, and accelerated time. Hardware adapters
        and enrolled clients are never opened.
      </small>
      <label>
        Scenario name
        <input
          aria-label="Simulation scenario name"
          value={simulationScenarioName}
          onChange={(event) => setSimulationScenarioName(event.target.value)}
        />
      </label>
      <label>
        Accelerated advance milliseconds
        <input
          aria-label="Simulation advance milliseconds"
          min="0"
          type="number"
          value={simulationAdvanceMs}
          onChange={(event) => setSimulationAdvanceMs(Number(event.target.value))}
        />
      </label>
      <button
        onClick={() =>
          setSimulationSteps((current) => [
            ...current,
            { kind: "advance", delta_ms: simulationAdvanceMs },
          ])
        }
      >
        Add accelerated advance
      </button>
      <label>
        Synthetic event type
        <input
          aria-label="Synthetic event type"
          value={simulationEventType}
          onChange={(event) => setSimulationEventType(event.target.value)}
        />
      </label>
      <label>
        Synthetic event attributes
        <textarea
          aria-label="Synthetic event attributes"
          value={simulationEventAttributes}
          onChange={(event) =>
            setSimulationEventAttributes(event.target.value)
          }
        />
      </label>
      <button
        onClick={() => {
          try {
            const parsed = JSON.parse(simulationEventAttributes);
            if (
              typeof parsed !== "object" ||
              parsed === null ||
              Array.isArray(parsed)
            ) {
              throw new Error("Synthetic event attributes must be an object.");
            }
            setSimulationSteps((current) => [
              ...current,
              {
                kind: "observe",
                event_type: simulationEventType,
                attributes: parsed as Record<
                  string,
                  string | number | boolean
                >,
              },
            ]);
            setSimulationError(null);
          } catch (error) {
            setSimulationError(
              error instanceof Error
                ? error.message
                : "Synthetic event attributes are invalid.",
            );
          }
        }}
      >
        Add synthetic observation
      </button>
      <label>
        Operator scene override
        <select
          aria-label="Simulation operator scene"
          value={simulationSceneId}
          onChange={(event) => setSimulationSceneId(event.target.value)}
        >
          {draft.draft.scenes.map((candidate) => (
            <option key={candidate.id} value={candidate.id}>
              {candidate.name}
            </option>
          ))}
        </select>
      </label>
      <button
        disabled={!simulationSceneId}
        onClick={() =>
          setSimulationSteps((current) => [
            ...current,
            { kind: "operator_start", scene_id: simulationSceneId },
          ])
        }
      >
        Add operator override
      </button>
      <button
        onClick={() =>
          setSimulationSteps((current) => [
            ...current,
            { kind: "disconnect" },
          ])
        }
      >
        Add client disconnect
      </button>
      <button
        onClick={() =>
          setSimulationSteps((current) => [
            ...current,
            { kind: "reconnect" },
          ])
        }
      >
        Add client reconnect
      </button>
      <label>
        Simulated lighting output
        <select
          aria-label="Simulated lighting output"
          value={simulationOutputResource}
          onChange={(event) =>
            setSimulationOutputResource(event.target.value)
          }
        >
          <option value="">Select output</option>
          {pixelOutputs.map((resource) => (
            <option key={resource} value={resource}>
              {resource}
            </option>
          ))}
        </select>
      </label>
      <button
        disabled={!simulationOutputResource}
        onClick={() =>
          setSimulationSteps((current) => [
            ...current,
            {
              kind: "output_failure",
              resource: simulationOutputResource,
              failed: true,
            },
          ])
        }
      >
        Add output failure
      </button>
      <button
        disabled={!simulationOutputResource}
        onClick={() =>
          setSimulationSteps((current) => [
            ...current,
            {
              kind: "output_failure",
              resource: simulationOutputResource,
              failed: false,
            },
          ])
        }
      >
        Add output recovery
      </button>
      <ol aria-label="Simulation scenario steps">
        {simulationSteps.map((step, index) => (
          <li key={`${index}:${step.kind}`}>
            <code>{JSON.stringify(step)}</code>
            <button
              aria-label={`Remove simulation step ${index + 1}`}
              onClick={() =>
                setSimulationSteps((current) =>
                  current.filter((_, candidate) => candidate !== index),
                )
              }
            >
              Remove
            </button>
          </li>
        ))}
      </ol>
      <label>
        Expected deterministic outcome
        <textarea
          aria-label="Simulation expected outcome"
          value={simulationExpected}
          onChange={(event) => setSimulationExpected(event.target.value)}
        />
      </label>
      <button
        disabled={
          !canEdit ||
          !simulationScenarioName.trim() ||
          simulationSteps.length === 0
        }
        onClick={async () => {
          try {
            const expected = JSON.parse(simulationExpected);
            if (
              typeof expected !== "object" ||
              expected === null ||
              Array.isArray(expected)
            ) {
              throw new Error("Expected outcome must be a JSON object.");
            }
            const saved = await saveSimulationScenario(projectId, {
              name: simulationScenarioName,
              steps: simulationSteps,
              expected: expected as Record<string, unknown>,
            });
            setSimulationScenarios((current) => [
              saved,
              ...current.filter((candidate) => candidate.id !== saved.id),
            ]);
            setSimulationError(null);
          } catch (error) {
            setSimulationError(
              error instanceof Error
                ? error.message
                : "Simulation scenario could not be saved.",
            );
          }
        }}
      >
        Save simulation scenario
      </button>
      <div className="timeline-simulation-saved">
        {simulationScenarios.map((scenario) => (
          <div key={scenario.id}>
            <span>
              <strong>{scenario.name}</strong> · revision{" "}
              {scenario.source_revision} · {scenario.steps.length} steps
            </span>
            <small>
              {scenario.source_revision === draft.revision
                ? "Matches current draft"
                : `Reproducible against stored revision ${scenario.source_revision}; current draft is ${draft.revision}`}
            </small>
            <button
              disabled={!canEdit}
              onClick={async () => {
                try {
                  setSimulationRun(
                    await runSimulationScenario(projectId, scenario.id),
                  );
                  setSimulationError(null);
                } catch (error) {
                  setSimulationError(
                    error instanceof Error
                      ? error.message
                      : "Simulation scenario failed to run.",
                  );
                }
              }}
            >
              Run saved scenario
            </button>
          </div>
        ))}
      </div>
      {simulationRun && (
        <output aria-label="Simulation scenario result">
          <strong>
            {simulationRun.passed ? "Expected outcome matched" : "Outcome mismatch"}
          </strong>
          <span>
            Hardware adapters opened: no · hardware commands sent: no
          </span>
          <code>{JSON.stringify(simulationRun.actual)}</code>
          {simulationRun.mismatches.length > 0 && (
            <code>{JSON.stringify(simulationRun.mismatches)}</code>
          )}
        </output>
      )}
      {simulationError && <small role="alert">{simulationError}</small>}
    </details>
  );
}
