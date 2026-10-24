import { useEffect, useState } from "react";
import {
  ApplyJob, applySavedShow, cancelApply, compareProjectRevisions, getApplyJob, getApplyJobs,
  ProjectDraftRecord, RuntimeStatus,
} from "../api";
import { DetailsDialog } from "../components/DetailsDialog";
import { ErrorState } from "../components/ui";

const terminal = new Set<ApplyJob["state"]>(["completed", "failed", "cancelled"]);
const phases: Record<ApplyJob["state"], string> = {
  preparing: "Checking the saved show", publishing: "Packaging the show",
  staging: "Sending files to devices", handover: "Pausing briefly and loading the show",
  completed: "Applied", failed: "Applying needs attention", cancelled: "Applying cancelled",
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
    <p>{matching ? "The display is running your latest saved version." : loaded?.state === "recovery_required" ? "The display needs to be restored to a working version." : "Saving keeps your work. Applying puts your saved show on the display."}</p>
    {!runtimeFresh && <small>Couldn't confirm what the display is running right now. Applying will check again first.</small>}
    <div className="show-actions">
      <button className="primary-button" disabled={!canApply || busy} onClick={() => { setRestore(null); setOpen(true); }}>Apply saved changes</button>
      {job && !terminal.has(job.state) && ["preparing", "publishing", "staging"].includes(job.state) && <button disabled={!canApply || job.cancel_requested} onClick={async () => {
        try { setJob(await cancelApply(draft.project.id, job.id)); }
        catch (failure) { setError(failure instanceof Error ? failure.message : "Cancellation failed."); }
      }}>{job.cancel_requested ? "Cancellation requested" : "Cancel preparation"}</button>}
    </div>
    {!canApply && <small>Applying needs publisher or administrator access, permission to change the live display, and no unsaved edits.</small>}
    {job && <p role="status">{phases[job.state]}{job.state === "completed" ? job.running ? " · restarted from the beginning" : " · playback remains stopped" : ""}</p>}
    {job?.error && <ErrorState title="Applying didn't finish" description={job.error} />}
    {job?.warnings?.map((value) => <p role="status" key={value}>{value}</p>)}
    {error && <><ErrorState title="Couldn't get applying status" description={`${error} Refresh before trying again.`} /><button onClick={() => { setError(null); setRetry((value) => value + 1); }}>Refresh status</button></>}
    {loaded?.error && <p role="alert">{loaded.error}</p>}
    <details><summary>Earlier versions</summary>
      <p>{loaded?.state === "loaded" ? `The display is running version ${loaded.revision} of this show.` : "The display's current show wasn't loaded from here, so its version is unknown."}</p>
      <p>Restoring an earlier version works like applying: a playing show restarts the chosen scene from the beginning, and a paused or stopped show stays stopped.</p>
      <ul>{history.filter((value) => value.state === "completed" && value.package_id).map((value) => <li key={value.id}>
        Version {value.source_revision}
        <button disabled={!canApply || busy} onClick={() => { setRestore(value); setOpen(true); }}>Restore version {value.source_revision}</button>
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
        scenes: "Scenes: names, timing, media or LED effects", program: "Scene order",
        audience: "How visitors are detected", opportunities: "Visitor responses",
        encounter: "Visitor response sequence", installation: "Devices and outputs used by the show",
        editor_metadata: "Layout, compositions or other editing details",
      };
      if (current) setChanges([...new Set(value.changed_paths.map((path) => labels[path.split("/")[1]] ?? "Other show settings"))]);
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
  return <DetailsDialog title={restore ? "Restore an earlier version" : "Apply saved changes"} onClose={() => { if (!busy) onClose(); }}>
    <div className="apply-review">
      <section className="apply-review__summary" aria-label="What will be loaded">
        <p className="apply-review__lead">
          {restore ? <>Load version {restore.source_revision} of <strong>{draft.project.name}</strong> onto your display.</>
            : <>Load <strong>{draft.project.name}</strong> onto your display.</>}
        </p>
        <p>{restore
          ? "Your current saved work isn't changed; you can apply it again later."
          : `Uses your last saved version (version ${targetRevision}), with ${draft.draft.scenes.length} ${draft.draft.scenes.length === 1 ? "scene" : "scenes"} and its visitor responses. Unsaved edits aren't included.`}</p>
      </section>

      <section aria-label="What's changing">
        <h3>What's changing</h3>
        {priorRevision == null ? <p>The display is running a show that wasn't loaded from here, so there's nothing to compare. The whole show will be replaced.</p>
          : priorRevision === targetRevision ? <p>This version is already on the display. Applying again re-checks your devices and lighting.</p>
            : changes ? changes.length
              ? <ul>{changes.map((change) => <li key={change}>{change}</li>)}</ul>
              : <p>No differences in saved content since the version on the display.</p>
              : summaryError ? null : <p role="status">Comparing with the version on the display…</p>}
        {summaryError && <ErrorState title="Couldn't compare versions" description={`${summaryError} Close this and try again before applying.`} />}
      </section>

      <section aria-label="What happens to playback">
        <h3>What happens to playback</h3>
        <ul>
          <li>New files are sent to your devices and checked first. Nothing stops until they're ready.</li>
          <li>If the show is playing, it stops briefly and starts again from the beginning of the scene you pick below.</li>
          <li>If the show is paused or stopped, it stays that way.</li>
          <li>Visitor responses start working once the show is running in automatic mode.</li>
        </ul>
      </section>

      <form onSubmit={(event) => { event.preventDefault(); void apply(); }}>
        <fieldset disabled={busy}>
          <label>If the show is playing, restart with
            <select value={startingScene} onChange={(event) => setStartingScene(event.target.value)}>
              {sceneOptions.map((scene) => <option key={scene.id} value={scene.id}>{scene.name}</option>)}
            </select>
          </label>

          <div className="apply-review__confirm" role="group" aria-label="Confirm">
            {!restore && <label className="apply-review__check">
              <input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} />
              <span>This version is ready to go live on the display.</span>
            </label>}
            <label className="apply-review__check">
              <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
              <span>It's OK to briefly stop playback to load it.</span>
            </label>
          </div>

          <details className="apply-review__advanced">
            <summary>Advanced options</summary>
            <label className="apply-review__check">
              <input type="checkbox" checked={preserveLooks} onChange={(event) => setPreserveLooks(event.target.checked)} />
              <span>Keep the controller's standby and safety lighting looks</span>
            </label>
            <small>Turn this off to remove those looks. If they don't work with this show, applying stops and tells you instead of dropping them.</small>
            <small>Applying uses exactly version {targetRevision}. If someone saves a newer version first, applying stops so you can review it.</small>
          </details>

          {reviewed !== draft.revision && <p role="alert">This show was saved again since you opened this. Close and review the latest version.</p>}
          <div className="apply-review__actions">
            <button type="button" onClick={onClose} disabled={busy}>Cancel</button>
            <button className="primary-button" disabled={!confirmed || (!restore && !approved) || reviewed !== draft.revision || Boolean(summaryError) || (priorRevision != null && priorRevision !== targetRevision && changes === null)}>{busy ? "Starting…" : restore ? "Restore now" : "Apply now"}</button>
          </div>
        </fieldset>
      </form>
      {error && <ErrorState title="Couldn't start applying" description={error} />}
    </div>
  </DetailsDialog>;
}
