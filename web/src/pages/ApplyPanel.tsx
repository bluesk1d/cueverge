import { useEffect, useState } from "react";
import {
  ApplyJob, applySavedShow, cancelApply, compareProjectRevisions, getApplyJob, getApplyJobs,
  ProjectDraftRecord, RuntimeStatus,
} from "../api";
import { DetailsDialog } from "../components/DetailsDialog";
import { ErrorState } from "../components/ui";

const terminal = new Set<ApplyJob["state"]>(["completed", "failed", "cancelled"]);
const phases: Record<ApplyJob["state"], string> = {
  preparing: "Checking the saved show", publishing: "Preparing the approved package",
  staging: "Verifying device transfers", handover: "Stopping safely and loading the show",
  completed: "Apply completed", failed: "Apply needs attention", cancelled: "Apply cancelled",
};

export function ApplyPanel({ draft, canApply, runtime, runtimeFresh = false }: {
  draft: ProjectDraftRecord; canApply: boolean; runtime?: RuntimeStatus | null; runtimeFresh?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [job, setJob] = useState<ApplyJob | null>(null);
  const [history, setHistory] = useState<ApplyJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [restore, setRestore] = useState<ApplyJob | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    getApplyJobs(draft.project.id, controller.signal).then((values) => {
      if (!controller.signal.aborted) {
        setHistory(values); setJob(values[0] ?? null);
      }
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Apply history is unavailable.");
    });
    return () => controller.abort();
  }, [draft.project.id, retry]);
  useEffect(() => {
    if (!job || terminal.has(job.state)) return;
    let current = true;
    const controller = new AbortController();
    let timer: number | undefined;
    async function poll() {
      const timeout = window.setTimeout(() => controller.abort(), 8000);
      try {
        const value = await getApplyJob(draft.project.id, job!.id, controller.signal);
        if (current) {
          setJob(value);
          if (!terminal.has(value.state)) timer = window.setTimeout(poll, 750);
          else {
            const values = await getApplyJobs(draft.project.id, controller.signal);
            if (current) setHistory(values);
          }
        }
      } catch (failure) {
        if (current) setError(failure instanceof Error ? failure.message : "Apply progress could not be refreshed. Inspect controller status before retrying.");
      } finally { window.clearTimeout(timeout); }
    }
    timer = window.setTimeout(poll, 500);
    return () => { current = false; controller.abort(); window.clearTimeout(timer); };
  }, [draft.project.id, job?.id, retry]);
  const loaded = runtime?.loaded_catalog;
  const matching = runtimeFresh && loaded?.state === "loaded" && loaded.project_id === draft.project.id && loaded.revision === draft.revision;
  const busy = Boolean(job && !terminal.has(job.state));
  return <section className="apply-panel" aria-label="Saved and loaded show">
    <p>{matching ? "This saved show version is loaded in the controller." : loaded?.state === "recovery_required" ? "The controller needs catalog recovery." : "Save keeps your work. Apply loads a verified show into this controller."}</p>
    {!runtimeFresh && <small>Loaded status is not current; an Apply job still requires fresh server-side checks.</small>}
    <div className="show-actions">
      <button className="primary-button" disabled={!canApply || busy} onClick={() => { setRestore(null); setOpen(true); }}>Apply saved changes</button>
      {job && !terminal.has(job.state) && ["preparing", "publishing", "staging"].includes(job.state) && <button disabled={!canApply || job.cancel_requested} onClick={async () => {
        try { setJob(await cancelApply(draft.project.id, job.id)); }
        catch (failure) { setError(failure instanceof Error ? failure.message : "Cancellation failed."); }
      }}>{job.cancel_requested ? "Cancellation requested" : "Cancel preparation"}</button>}
    </div>
    {!canApply && <small>Applying requires publisher/administrator permission, a live-output grant, and no pending authoring edits.</small>}
    {job && <p role="status">{phases[job.state]}{job.state === "completed" ? job.running ? " · restarted from the beginning" : " · playback remains stopped" : ""}</p>}
    {job?.error && <ErrorState title="Apply did not complete" description={job.error} />}
    {job?.warnings?.map((value) => <p role="status" key={value}>{value}</p>)}
    {error && <><ErrorState title="Apply information unavailable" description={`${error} Refresh progress before retrying a load.`} /><button onClick={() => { setError(null); setRetry((value) => value + 1); }}>Refresh Apply status</button></>}
    {loaded?.error && <p role="alert">{loaded.error}</p>}
    <details><summary>Loaded identity and retained versions</summary>
      <p>{loaded?.state === "loaded" ? `Observed show ${loaded.project_id}, version ${loaded.revision}; generation ${loaded.generation}.` : "Legacy/external catalog identity is unreported until managed Apply completes."}</p>
      <p>Paused or stopped shows stay stopped after Apply/recovery. A playing show restarts the chosen scene at zero. Saved edits, artifact activation and physical output are different facts.</p>
      <ul>{history.filter((value) => value.state === "completed" && value.package_id).map((value) => <li key={value.id}>
        Saved version {value.source_revision}
        <button disabled={!canApply || busy} onClick={() => { setRestore(value); setOpen(true); }}>Restore retained version {value.source_revision}</button>
      </li>)}</ul>
    </details>
    {open && <ApplyReview draft={draft} restore={restore} priorRevision={loaded?.project_id === draft.project.id ? loaded.revision : null} onClose={() => setOpen(false)} onStarted={(value) => {
      setJob(value); setError(null); setOpen(false);
    }} />}
  </section>;
}

function ApplyReview({ draft, restore, priorRevision, onClose, onStarted }: {
  draft: ProjectDraftRecord; restore: ApplyJob | null; onClose: () => void; onStarted: (job: ApplyJob) => void;
  priorRevision?: number | null;
}) {
  const [reviewed] = useState(draft.revision);
  const [approved, setApproved] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [preserveLooks, setPreserveLooks] = useState(true);
  const sceneOptions = restore?.scene_options ?? draft.draft.scenes;
  const [startingScene, setStartingScene] = useState(restore?.starting_scene_id ?? (restore ? sceneOptions[0]?.id : draft.draft.program?.initial_scene_id) ?? sceneOptions[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [changes, setChanges] = useState<string[] | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const targetRevision = restore?.source_revision ?? reviewed;
  useEffect(() => {
    if (priorRevision == null || priorRevision === targetRevision) return;
    let current = true;
    compareProjectRevisions(draft.project.id, priorRevision, targetRevision).then((value) => {
      const labels: Record<string, string> = {
        scenes: "Scene names, timing, media or LED animation", program: "Scene sequence",
        audience: "Visitor detection rules", opportunities: "Visitor responses",
        encounter: "Advanced visitor state sequence", installation: "Installation resource definitions",
        editor_metadata: "Layout, composition or other editor data",
      };
      if (current) setChanges([...new Set(value.changed_paths.map((path) => labels[path.split("/")[1]] ?? "Other saved show data"))]);
    }).catch((failure: unknown) => {
      if (current) setSummaryError(failure instanceof Error ? failure.message : "Change comparison is unavailable.");
    });
    return () => { current = false; };
  }, [draft.project.id, priorRevision, targetRevision]);
  async function apply() {
    setBusy(true); setError(null);
    try {
      onStarted(await applySavedShow(draft.project.id, {
        source_revision: restore?.source_revision ?? reviewed, expected_head_revision: reviewed,
        confirm_interruption: confirmed, approve_release: approved,
        starting_scene_id: startingScene || undefined,
        restore_package_id: restore?.package_id ?? undefined,
        preserve_controller_looks: preserveLooks,
      }));
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Apply could not be started."); }
    finally { setBusy(false); }
  }
  return <DetailsDialog title={restore ? "Restore retained show" : "Apply saved changes"} onClose={() => { if (!busy) onClose(); }}>
    <p><strong>{draft.project.name}</strong> → this controller's bound installation.</p>
    <p>{restore ? `Load retained saved version ${restore.source_revision}. Current authoring work is not overwritten.` : `Prepare and load the exact saved version shown here. Unsaved edits are not included.`}</p>
    <p>{restore ? "Retained scenes" : `${draft.draft.scenes.length} saved scenes`} and this version's visitor rules will be loaded.</p>
    {priorRevision == null ? <p>This replaces another or externally loaded catalog; there is no matching managed version to compare.</p>
      : priorRevision === targetRevision ? <p>Saved content matches the observed loaded version. Apply still rechecks its dependencies and controller lighting.</p>
        : changes ? <p>Compared with the observed loaded version: {changes.length ? changes.join("; ") : "no saved content differences"}.</p>
          : <p role="status">Comparing saved show changes…</p>}
    {summaryError && <ErrorState title="Change comparison unavailable" description={`${summaryError} Close and retry before applying.`} />}
    <details><summary>Saved version being reviewed</summary><p>Saved version {targetRevision}; expected working head {reviewed}. A changed head is rejected, never silently substituted.</p></details>
    <p>Device transfers are verified before interruption. If playing, the chosen/starting scene restarts from zero; there is no exact-position hot reload. Paused/stopped remains stopped. Camera responses may run once the loaded show is started in automatic mode.</p>
    <form onSubmit={(event) => { event.preventDefault(); void apply(); }}>
      <fieldset disabled={busy}>
        <label>Scene to restart if playing<select value={startingScene} onChange={(event) => setStartingScene(event.target.value)}>
          {sceneOptions.map((scene) => <option key={scene.id} value={scene.id}>{scene.name}</option>)}
        </select></label>
        {!restore && <label><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} />I approve this exact saved version for this installation.</label>}
        <label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />I authorize the controller load and safe playback interruption described above.</label>
        <details><summary>Controller lighting setup</summary>
          <label><input type="checkbox" checked={preserveLooks} onChange={(event) => setPreserveLooks(event.target.checked)} />Keep current controller baseline and safe-state lighting in the immutable package.</label>
          <small>Clearing this deliberately removes those operator-defined looks. An incompatible preserved setup blocks Apply rather than silently dropping it.</small>
        </details>
        {reviewed !== draft.revision && <p role="alert">This saved view changed. Close and review the latest version.</p>}
        <button className="primary-button" disabled={!confirmed || (!restore && !approved) || reviewed !== draft.revision || Boolean(summaryError) || (priorRevision != null && priorRevision !== targetRevision && changes === null)}>{busy ? "Starting preparation…" : restore ? "Restore now" : "Apply now"}</button>
      </fieldset>
    </form>
    {error && <ErrorState title="Apply not started" description={error} />}
  </DetailsDialog>;
}
