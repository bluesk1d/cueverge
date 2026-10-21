import { useEffect, useId, useState } from "react";

import {
  getOutputTestOverview, operate, startPhysicalOutputTest, stopPhysicalOutputTest,
} from "../api";
import type { OutputTestOverview, PhysicalOutputTest } from "../api";

export function PhysicalOutputTestControls({
  canOperate,
  resource,
  installationId,
  initialSpeed = 30,
  onTestUpdate,
}: {
  canOperate: boolean;
  resource?: string;
  installationId?: string;
  initialSpeed?: number;
  onTestUpdate?: (test: PhysicalOutputTest | null) => void;
}) {
  const labelId = useId();
  const [overview, setOverview] = useState<OutputTestOverview | null>(null);
  const [selected, setSelected] = useState(resource ?? "");
  const [confirmation, setConfirmation] = useState("");
  const [duration, setDuration] = useState(30);
  const [speed, setSpeed] = useState(initialSpeed);
  const [useMapping, setUseMapping] = useState(true);
  const [busy, setBusy] = useState(false);
  const [statusError, setStatusError] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const outputs = overview?.outputs.filter((output) => !resource || output.resource === resource) ?? [];
  const selectedResource = selected || outputs[0]?.resource || "";
  const test = overview?.test;
  const active = test?.state === "active";

  useEffect(() => {
    onTestUpdate?.(overview?.test ?? null);
  }, [overview?.test, onTestUpdate]);

  useEffect(() => {
    let mounted = true;
    let loading = false;
    async function refresh() {
      if (loading) return;
      loading = true;
      try {
        const state = await getOutputTestOverview();
        if (mounted) { setOverview(state); setStatusError(false); }
      } catch {
        if (mounted) setStatusError(true);
      } finally {
        loading = false;
      }
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => { mounted = false; window.clearInterval(timer); };
  }, []);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setActionError(null);
    try {
      await action();
      setOverview(await getOutputTestOverview());
      setStatusError(false);
    } catch (error) {
      setActionError(
        `${error instanceof Error ? error.message : "Controller request failed"}. ` +
        "Check idle maintenance mode, output configuration, and saved segment allocations.",
      );
    } finally {
      setBusy(false);
    }
  }

  function updateTest(updated: PhysicalOutputTest) {
    setOverview((current) => current ? { ...current, test: updated } : current);
  }

  return (
    <section className="physical-output-test" aria-labelledby={labelId}>
      <h4 id={labelId}>Physical LED output test</h4>
      <p>
        Sends real frames through the configured output driver. Uses existing output
        brightness settings without an additional test cap. Other outputs are not tested.
        The test sends black and releases output on stop or timeout.
      </p>
      {statusError && <p role="alert">Unable to read physical output test status. Refresh before starting.</p>}
      {actionError && <p role="alert">{actionError}</p>}
      {overview && outputs.length === 0 && (
        <p>No matching physical DDP output is configured with the installation&apos;s pixel count.</p>
      )}
      {overview?.mode !== "maintenance" && (
        <button
          disabled={!canOperate || busy || overview === null || statusError || overview.playback_active}
          onClick={() => void run(() => operate("maintenance"))}
        >
          Enter maintenance mode
        </button>
      )}
      {overview?.playback_active && <p>Stop show playback before running an output test.</p>}
      <div className="physical-output-test__controls">
        <label>
          LED output to test
          <select
            disabled={active || busy || !!resource}
            value={selectedResource}
            onChange={(event) => { setSelected(event.target.value); setConfirmation(""); }}
          >
            {!outputs.length && <option value="">No available output</option>}
            {outputs.map((output) => (
              <option key={output.resource} value={output.resource}>
                {output.resource} · {output.pixel_count} pixels
              </option>
            ))}
          </select>
        </label>
        <label>
          Physical test duration
          <select value={duration} disabled={active || busy} onChange={(event) => setDuration(Number(event.target.value))}>
            {[5, 15, 30].map((value) => <option key={value} value={value}>{value} seconds</option>)}
          </select>
        </label>
        <label>
          Physical chase speed
          <select value={speed} disabled={active || busy} onChange={(event) => setSpeed(Number(event.target.value))}>
            {[5, 15, 30, 60, 120].map((value) => <option key={value} value={value}>{value} pixels/second</option>)}
          </select>
        </label>
        <label>
          <input type="checkbox" checked={useMapping} disabled={active || busy} onChange={(event) => setUseMapping(event.target.checked)} />
          Use saved segment colors
        </label>
        <label>
          Confirm physical output name
          <input value={confirmation} disabled={active || busy} onChange={(event) => setConfirmation(event.target.value)} />
        </label>
        <button
          className="primary-button"
          disabled={!canOperate || busy || active || statusError || !selectedResource ||
            !outputs.some((output) => output.resource === selectedResource) ||
            confirmation !== selectedResource || overview?.mode !== "maintenance" ||
            overview.playback_active}
          onClick={() => void run(async () => updateTest(await startPhysicalOutputTest({
            resource: selectedResource, confirmation, duration_seconds: duration,
            speed, use_mapping: useMapping, installation_id: installationId,
          })))}
        >
          Start physical chase
        </button>
      </div>
      {test && (
        <div className="physical-output-test__status" role="status">
          <strong>{test.resource}: {test.state} · pixel {test.pixel_index} / {test.pixel_count - 1}</strong>
          <span>
            {test.frames_sent} frames sent · expires {new Date(test.expires_at).toLocaleTimeString()}
          </span>
          {test.segments.length > 0 && (
            <span>
              {test.segments.find((segment) =>
                segment.start <= test.pixel_index && test.pixel_index < segment.end,
              )?.name ?? "Unmapped indices: white pulse"}
            </span>
          )}
          {test.error && <p role="alert">{test.error}</p>}
          {active && (
            <button disabled={!canOperate || busy} onClick={() => void run(async () =>
              updateTest(await stopPhysicalOutputTest(test.id)),
            )}>
              Stop physical chase
            </button>
          )}
        </div>
      )}
      <small>
        Closing this view does not stop a running test; the controller enforces its timeout.
        Unmapped indices use white. UDP transmission is not confirmation of visible LED output.
      </small>
    </section>
  );
}
