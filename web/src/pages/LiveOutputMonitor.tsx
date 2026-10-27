import { useState } from "react";
import { RuntimeStatus } from "../api";
import { ScenePreview } from "./SceneWorkspace";
import { ErrorState, Panel } from "../components/ui";
import { useOutputMonitor } from "../components/useOutputMonitor";

const noNames = () => {};
const noFailure = () => {};

export function LiveOutputMonitor({ installationId, runtime }: {
  installationId?: string; runtime: RuntimeStatus | null;
}) {
  const [enabled, setEnabled] = useState(false);
  const [resource, setResource] = useState("");
  const [offset, setOffset] = useState(0);
  const { snapshot, error, retry, clear } = useOutputMonitor({ enabled, resource, offset });
  const generationMatches = snapshot?.loaded_catalog?.generation === runtime?.loaded_catalog?.generation;
  const outputs = snapshot?.outputs ?? [];
  return <Panel className="live-output-monitor" title="Intended output monitor"
    description="Last sent LED colors and separate device reports—not proof of physical output.">
    <button onClick={() => setEnabled((value) => !value)}>{enabled ? "Pause output monitor" : "Open output monitor"}</button>
    {enabled && <>
      <p role="status">Read only · up to 2 updates/second · pauses in hidden tabs.</p>
      <details><summary>What does this monitor show?</summary>
        <p>These are successfully sent controller frames, not a camera feed or electrical measurement. Held colors can remain on LEDs after Stop when no safe-state look is configured. Unmapped LEDs and unsupported physical feedback are not invented.</p>
      </details>
      <label>LED output<select value={resource} onChange={(event) => { setResource(event.target.value); setOffset(0); clear(); }}>
        <option value="">All loaded LED outputs</option>
        {runtime?.status.lighting_outputs.map((value) => <option key={value.resource} value={value.resource}>{value.resource}</option>)}
        {runtime?.status.operations.output_resources?.filter((value) => !runtime.status.lighting_outputs.some((item) => item.resource === value.resource)).map((value) => <option key={value.resource} value={value.resource}>{value.resource}</option>)}
      </select></label>
      {error && <><ErrorState title="Output monitor unavailable" description={`${error} Last displayed samples are not current.`} /><button onClick={retry}>Retry output monitor</button></>}
      {snapshot && <small>Observation received {new Date(snapshot.observed_at).toLocaleTimeString()} · {snapshot.outputs.reduce((total, value) => total + value.colors.length, 0)} / {snapshot.maximum_samples} maximum sampled LEDs. {snapshot.truncated_resources > 0 && `${snapshot.truncated_resources} additional outputs exceed this view's resource budget.`}</small>}
      {snapshot && !generationMatches && <p role="alert">Loaded identity changed or the status snapshot is older than these samples. Refresh controller status; do not interpret this as the current saved show.</p>}
      {outputs.map((value) => <article key={value.resource}>
        <h3>{value.resource}</h3>
        <p>{value.source === "unobserved" ? "No retained successful frame is available for this output." : `${value.source.replaceAll("_", " ")} · ${value.age_ms ?? "unknown"} ms old${value.held ? " · held / stopped" : ""}${value.stale ? " · stale" : ""}`}</p>
        {value.error && <p role="alert">Latest sink send failed: {value.error}. Displayed colors are the last successful send.</p>}
        {value.colors.length > 0 && <ScenePreview
          projectId={snapshot?.loaded_catalog?.project_id ?? ""} revision={snapshot?.loaded_catalog?.revision ?? 1}
          dirty={false} sceneId="" resource={value.resource} position={value.output_position_ms ?? 0}
          valid external intent installationIdOverride={installationId}
          frameOverride={{ timestamp_ms: value.output_position_ms ?? 0, unsaved: false, outputs: [value] }}
          onFailure={noFailure} onTargetNames={noNames}
        />}
        {value.next_pixel_offset !== null && <button onClick={() => { setResource(value.resource); setOffset(value.next_pixel_offset!); }}>Next sampled LED range</button>}
      </article>)}
      {offset > 0 && <button onClick={() => setOffset(0)}>First sampled LED range</button>}
      <details><summary>Device-reported media playback</summary>
        <p>Media thumbnails/decoded video are not captured by this monitor. These are agent reports, not observed projector images.</p>
        {runtime?.status.media_outputs.map((value) => <pre key={value.output_id}>{JSON.stringify(value, null, 2)}</pre>)}
        {!runtime?.status.media_outputs.length && <p>No active media outputs reported.</p>}
      </details>
    </>}
  </Panel>;
}
