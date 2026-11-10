import { useState } from "react";
import { createPointLightOutput } from "../api";

export function PointLightOutputs({ installationId, onRefresh }: {
  installationId: string; onRefresh?: () => Promise<void> | void;
}) {
  const [resource, setResource] = useState("");
  const [host, setHost] = useState("");
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  return <section aria-label="LIFX point light outputs">
    <h3>Add stationary point light output</h3>
    <p>Register a LIFX bulb for layout placement, timing calibration and show lighting. Stop playback and output tests first.</p>
    <form onSubmit={(event) => {
      event.preventDefault(); setBusy(true); setMessage("");
      void createPointLightOutput(installationId, { resource, host, target }).then(async () => {
        setMessage(`${resource} is ready. Add it to existing shows under Installation → Objects, or create a new show.`);
        await onRefresh?.();
      }).catch((error: unknown) => setMessage(error instanceof Error ? error.message : String(error)))
        .finally(() => setBusy(false));
    }}>
      <label>Output resource<input required value={resource} onChange={(e) => setResource(e.target.value)} placeholder="light.left" /></label>
      <label>IPv4 address<input required value={host} onChange={(e) => setHost(e.target.value)} placeholder="192.168.40.71" /></label>
      <label>LIFX target ID<input required pattern="[0-9a-fA-F]{12}" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="d073d58b1a6d" /></label>
      <button disabled={busy}>{busy ? "Adding…" : "Add LIFX output"}</button>
    </form>
    <p role="status">{message}</p>
  </section>;
}
