import { useEffect, useRef, useState } from "react";

import {
  compareProjectRevisions,
  getRevisionRelease,
  listProjectRevisions,
  listPublishedPackages,
  ProjectDraftRecord,
  PublishedPackage,
  publishRevision,
  RevisionDiff,
  RevisionRelease,
  RevisionSummary,
  runSimulationScenario,
  saveSimulationScenario,
  SimulationRun,
  transitionPublishedPackage,
  updateRevisionRelease,
  validatePublication,
  PublicationValidation,
} from "../api";

export function PublicationWorkspace({
  canPublish,
  draft,
  projectId,
}: {
  canPublish: boolean;
  draft: ProjectDraftRecord;
  projectId: string;
}) {
  const [open, setOpen] = useState(false);
  const [revisions, setRevisions] = useState<RevisionSummary[]>([]);
  const [release, setRelease] = useState<RevisionRelease | null>(null);
  const [validation, setValidation] = useState<PublicationValidation | null>(
    null,
  );
  const [packages, setPackages] = useState<PublishedPackage[]>([]);
  const [diff, setDiff] = useState<RevisionDiff | null>(null);
  const [baseRevision, setBaseRevision] = useState(
    Math.max(1, draft.revision - 1),
  );
  const [name, setName] = useState("");
  const [summary, setSummary] = useState("");
  const [approval, setApproval] =
    useState<RevisionRelease["approval_state"]>("draft");
  const [selectedScenes, setSelectedScenes] = useState<string[]>(
    draft.draft.scenes.map((scene) => scene.id),
  );
  const [countdown, setCountdown] = useState<number | null>(null);
  const [rehearsal, setRehearsal] = useState<SimulationRun | null>(null);
  const [rehearsalMessage, setRehearsalMessage] = useState(
    "No rehearsal has run.",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rehearsalGeneration = useRef(0);

  async function load() {
    setError(null);
    try {
      const [revisionRows, currentRelease, packageRows, validationResult] =
        await Promise.all([
          listProjectRevisions(projectId),
          getRevisionRelease(projectId, draft.revision),
          listPublishedPackages(projectId),
          validatePublication(projectId, draft.revision),
        ]);
      setRevisions(revisionRows);
      setRelease(currentRelease);
      setName(currentRelease.name ?? "");
      setSummary(currentRelease.summary ?? "");
      setApproval(currentRelease.approval_state);
      setPackages(packageRows);
      setValidation(validationResult);
    } catch (loadError) {
      setError(message(loadError, "Publication workspace could not be loaded."));
    }
  }

  useEffect(() => {
    if (open) {
      void load();
    }
  }, [draft.revision, open]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (actionError) {
      setError(message(actionError, "Publication action failed."));
    } finally {
      setBusy(false);
    }
  }

  async function saveRelease() {
    const updated = await updateRevisionRelease(projectId, draft.revision, {
      name: name || null,
      summary: summary || null,
      approval_state: approval,
    });
    setRelease(updated);
    setValidation(await validatePublication(projectId, draft.revision));
  }

  async function compare() {
    setDiff(
      await compareProjectRevisions(projectId, baseRevision, draft.revision),
    );
  }

  async function publish() {
    await publishRevision(projectId, draft.revision);
    setPackages(await listPublishedPackages(projectId));
  }

  async function transition(
    packageId: string,
    action: "stage" | "activate" | "deactivate" | "rollback",
  ) {
    await transitionPublishedPackage(packageId, action);
    setPackages(await listPublishedPackages(projectId));
  }

  async function startRehearsal() {
    const generation = ++rehearsalGeneration.current;
    setRehearsal(null);
    setRehearsalMessage("Preflight complete. Countdown started.");
    for (const value of [3, 2, 1]) {
      if (generation !== rehearsalGeneration.current) {
        return;
      }
      setCountdown(value);
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
    setCountdown(null);
    if (generation !== rehearsalGeneration.current) {
      return;
    }
    const scenario = await saveSimulationScenario(projectId, {
      name: `Publication rehearsal r${draft.revision}`,
      steps: [
        { kind: "advance", delta_ms: 60_000 },
        ...selectedScenes.map(
          (sceneId) =>
            ({ kind: "request_scene", scene_id: sceneId }) as const,
        ),
        { kind: "disconnect" },
        { kind: "reconnect" },
        { kind: "advance", delta_ms: 10_000 },
      ],
      expected: {},
    });
    const result = await runSimulationScenario(projectId, scenario.id);
    setRehearsal(result);
    setRehearsalMessage(
      result.passed
        ? "Rehearsal completed without hardware commands."
        : "Rehearsal completed with expected-outcome mismatches.",
    );
  }

  function stopRehearsal() {
    rehearsalGeneration.current += 1;
    setCountdown(null);
    setRehearsalMessage("Rehearsal stopped; no live output was changed.");
  }

  return (
    <details
      className="publication-workspace"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>Rehearsal, publication, and rollback</summary>
      <p>
        Candidate draft r{draft.revision}. Publication creates an immutable,
        content-addressed package; staging and live activation remain separate.
      </p>

      <section aria-label="Revision release metadata">
        <h4>Named revision and approval</h4>
        <label>
          Revision name
          <input
            disabled={!canPublish}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label>
          Change summary
          <textarea
            disabled={!canPublish}
            value={summary}
            onChange={(event) => setSummary(event.target.value)}
          />
        </label>
        <label>
          Approval state
          <select
            disabled={!canPublish}
            value={approval}
            onChange={(event) =>
              setApproval(
                event.target.value as RevisionRelease["approval_state"],
              )
            }
          >
            <option value="draft">Draft</option>
            <option value="in_review">In review</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
          </select>
        </label>
        <button
          disabled={!canPublish || busy}
          onClick={() => void run(saveRelease)}
        >
          Save release metadata
        </button>
        {release?.approved_by && (
          <small>
            Approved by {release.approved_by} at {release.approved_at}
          </small>
        )}
      </section>

      <section aria-label="Package revision comparison">
        <h4>Candidate versus prior revision</h4>
        <select
          aria-label="Comparison base revision"
          value={baseRevision}
          onChange={(event) => setBaseRevision(Number(event.target.value))}
        >
          {revisions.map((revision) => (
            <option key={revision.revision} value={revision.revision}>
              r{revision.revision} · {revision.transaction_kind} ·{" "}
              {revision.actor}
            </option>
          ))}
        </select>
        <button disabled={busy} onClick={() => void run(compare)}>
          Compare revisions
        </button>
        {diff && (
          <ul>
            {diff.changed_paths.map((path) => (
              <li key={path}>
                <code>{path}</code>
              </li>
            ))}
            {!diff.changed_paths.length && <li>No package changes.</li>}
          </ul>
        )}
      </section>

      <section aria-label="Publication preflight">
        <h4>Publication validation</h4>
        <button
          disabled={busy}
          onClick={() =>
            void run(async () =>
              setValidation(
                await validatePublication(projectId, draft.revision),
              ),
            )
          }
        >
          Run preflight
        </button>
        <strong>{validation?.passed ? "Preflight passed" : "Preflight blocked"}</strong>
        <ul>
          {validation?.diagnostics.map((diagnostic) => (
            <li key={`${diagnostic.code}:${diagnostic.message}`}>
              {diagnostic.severity} · <code>{diagnostic.code}</code> ·{" "}
              {diagnostic.message}
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="Hardware-free publication rehearsal">
        <h4>Rehearsal mode</h4>
        <p>
          Preflight: simulation-only targets, deterministic clock, stop and
          blackout controls, client interruption, and post-run diagnostics.
        </p>
        {draft.draft.scenes.map((scene) => (
          <label key={scene.id}>
            <input
              checked={selectedScenes.includes(scene.id)}
              type="checkbox"
              onChange={(event) =>
                setSelectedScenes((current) =>
                  event.target.checked
                    ? [...current, scene.id]
                    : current.filter((id) => id !== scene.id),
                )
              }
            />
            {scene.name}
          </label>
        ))}
        <button
          disabled={busy || !selectedScenes.length}
          onClick={() => void run(startRehearsal)}
        >
          Start rehearsal
        </button>
        <button onClick={stopRehearsal}>Stop rehearsal</button>
        <button
          onClick={() =>
            setRehearsalMessage(
              "Simulation blackout applied; no hardware command was sent.",
            )
          }
        >
          Blackout rehearsal
        </button>
        {countdown !== null && <strong>Starting in {countdown}</strong>}
        <output>
          {rehearsalMessage}
          {rehearsal && ` Revision ${rehearsal.source_revision}.`}
        </output>
      </section>

      <section aria-label="Published package deployment">
        <h4>Publish, stage, activate, and roll back</h4>
        <button
          disabled={
            !canPublish ||
            busy ||
            approval !== "approved" ||
            validation?.passed !== true
          }
          onClick={() => void run(publish)}
        >
          Publish immutable package
        </button>
        {packages.map((item) => (
          <article key={item.id}>
            <strong>
              r{item.revision} · {item.state}
            </strong>
            <code>{item.digest_sha256}</code>
            <small>
              Created by {item.created_by} at {item.created_at}
            </small>
            <button
              disabled={!canPublish || busy || item.state !== "published"}
              onClick={() => void run(() => transition(item.id, "stage"))}
            >
              Stage
            </button>
            <button
              disabled={!canPublish || busy || item.state !== "staged"}
              onClick={() => void run(() => transition(item.id, "activate"))}
            >
              Activate
            </button>
            <button
              disabled={!canPublish || busy || item.state !== "active"}
              onClick={() => void run(() => transition(item.id, "deactivate"))}
            >
              Deactivate
            </button>
            <button
              disabled={
                !canPublish ||
                busy ||
                !["inactive", "staged"].includes(item.state)
              }
              onClick={() => void run(() => transition(item.id, "rollback"))}
            >
              Roll back
            </button>
          </article>
        ))}
      </section>
      {error && <p role="alert">{error}</p>}
    </details>
  );
}

function message(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}
