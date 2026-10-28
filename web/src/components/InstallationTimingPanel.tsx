import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import {
  adjustTimingCalibration,
  finishTimingCalibration,
  getDeviceAgentSettings,
  getInstallationTiming,
  getRuntimeStatus,
  operate,
  resyncTimingCalibration,
  setOutputTiming,
  startTimingCalibration,
  updateDeviceAgentSettings,
} from "../api";
import type {
  CalibrationOutput,
  DeviceAgentSettings,
  InstallationTiming,
  OutputTiming,
  ResourceAssignment,
} from "../api";
import { NumericInput } from "./NumericInput";

const NUDGES = [-100, -10, 10, 100] as const;
const KIND_LABEL: Record<OutputTiming["kind"], string> = {
  media: "Projector start delay",
  lighting: "LED timing offset",
};
const STATE_LABEL: Record<CalibrationOutput["state"], string> = {
  preparing: "Loading clip",
  waiting: "Starting",
  playing: "Playing",
  flashing: "Flashing",
  error: "Error",
};

function errorText(failure: unknown, fallback: string): string {
  return failure instanceof Error ? failure.message : fallback;
}

function clamp(output: { kind: OutputTiming["kind"] }, value: number): number {
  const [minimum, maximum] = output.kind === "media" ? [0, 5000] : [-5000, 5000];
  return Math.max(minimum, Math.min(maximum, Math.round(value)));
}

/** Installation-level latency compensation and the live flash-and-click calibration wizard. */
export function InstallationTimingPanel({
  installationId,
  assignments,
  canOperate,
  canConfigureDevices,
}: {
  installationId: string;
  assignments: ResourceAssignment[];
  canOperate: boolean;
  canConfigureDevices: boolean;
}) {
  const [timing, setTiming] = useState<InstallationTiming | null>(null);
  const [mode, setMode] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, number>>({});
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [clicks, setClicks] = useState<Record<string, boolean>>({});
  const [audio, setAudio] = useState<Record<string, DeviceAgentSettings>>({});
  const [result, setResult] = useState<string | null>(null);
  const loadRef = useRef<() => Promise<void>>(async () => undefined);

  const load = useCallback(async () => {
    try {
      const [loaded, runtime] = await Promise.all([getInstallationTiming(installationId), getRuntimeStatus()]);
      setTiming(loaded);
      const status = runtime.status as { operations?: { mode?: string }; active_scene?: string | null };
      setMode(status.operations?.mode ?? null);
      setPlaying(Boolean(status.active_scene));
      setLoadError(null);
    } catch (failure) {
      setLoadError(errorText(failure, "Installation timing could not be loaded."));
    }
  }, [installationId]);
  loadRef.current = load;

  const run = timing?.calibration ?? null;
  const running = run !== null && (run.state === "starting" || run.state === "active");

  useEffect(() => {
    void loadRef.current();
    const timer = window.setInterval(() => void loadRef.current(), running ? 1000 : 4000);
    return () => window.clearInterval(timer);
  }, [installationId, running]);

  useEffect(() => {
    if (!timing) return;
    setSelected((current) => {
      const next = { ...current };
      for (const output of timing.outputs) if (!(output.resource in next)) next[output.resource] = true;
      return next;
    });
    setClicks((current) => {
      const next = { ...current };
      for (const output of timing.outputs) if (output.kind === "media" && !(output.resource in next)) next[output.resource] = true;
      return next;
    });
  }, [timing]);

  const devices = useMemo(() => {
    const byResource: Record<string, string> = {};
    for (const item of assignments) if (item.capability_kind === "media_playback") byResource[item.resource] = item.device_id;
    return byResource;
  }, [assignments]);

  const calibratedProjectors = useMemo(
    () => (running ? run!.outputs.filter((item) => item.kind === "media").map((item) => item.resource) : []),
    [running, run],
  );

  useEffect(() => {
    if (!canConfigureDevices || calibratedProjectors.length === 0) return;
    let active = true;
    const deviceIds = [...new Set(calibratedProjectors.map((resource) => devices[resource]).filter(Boolean))];
    void Promise.all(deviceIds.map((deviceId) => getDeviceAgentSettings(deviceId).catch(() => null))).then((values) => {
      if (!active) return;
      const next: Record<string, DeviceAgentSettings> = {};
      for (const value of values) if (value) next[value.device_id] = value;
      setAudio(next);
    });
    return () => {
      active = false;
    };
  }, [canConfigureDevices, calibratedProjectors, devices]);

  async function action(label: string, work: () => Promise<void>) {
    setBusy(label);
    setError(null);
    try {
      await work();
      await load();
    } catch (failure) {
      setError(errorText(failure, `${label} failed.`));
    } finally {
      setBusy(null);
    }
  }

  async function saveOutput(output: OutputTiming) {
    const value = clamp(output, drafts[output.resource] ?? output.delay_ms);
    await action(`Saving ${output.resource}`, async () => {
      await setOutputTiming(installationId, output, value);
      setDrafts(({ [output.resource]: _removed, ...rest }) => rest);
    });
  }

  async function adjust(output: CalibrationOutput, value: number) {
    const next = clamp(output, value);
    setTiming((current) =>
      current?.calibration
        ? {
            ...current,
            calibration: {
              ...current.calibration,
              outputs: current.calibration.outputs.map((item) =>
                item.resource === output.resource ? { ...item, delay_ms: next } : item,
              ),
            },
          }
        : current,
    );
    try {
      await adjustTimingCalibration(installationId, output.resource, next);
    } catch (failure) {
      setError(errorText(failure, "The delay could not be changed."));
      await load();
    }
  }

  async function adjustAudio(deviceId: string, delta: number) {
    const current = audio[deviceId];
    if (!current) return;
    const value = Math.max(-5000, Math.min(5000, (current.effective?.audio_delay_ms ?? 0) + delta));
    try {
      const saved = await updateDeviceAgentSettings(
        deviceId,
        { ...current.overrides, audio_delay_ms: value },
        current.revision,
      );
      setAudio((all) => ({ ...all, [deviceId]: saved }));
    } catch (failure) {
      setError(errorText(failure, "The client's audio delay could not be changed."));
    }
  }

  function onRowKey(event: KeyboardEvent<HTMLDivElement>, output: CalibrationOutput) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = (event.shiftKey ? 100 : 10) * (event.key === "ArrowLeft" ? -1 : 1);
    void adjust(output, output.delay_ms + step);
  }

  const idleMaintenance = mode === "maintenance" && !playing;
  const outputs = timing?.outputs ?? [];
  const chosenProjectors = outputs.filter((item) => item.kind === "media" && selected[item.resource]);
  const chosenLeds = outputs.filter((item) => item.kind === "lighting" && selected[item.resource]);

  return (
    <section className="installation-timing" aria-label="Installation timing">
      <header>
        <h2>Output timing</h2>
        <p>
          Hardware latency compensation for this installation. Every show uses these values right away,
          with no Apply needed; delays set on a scene&apos;s outputs are added on top. A projector start
          delay makes that projector start later. An LED timing offset makes the LEDs later (positive)
          or earlier (negative).
        </p>
      </header>
      {loadError && <p role="alert" className="installation-timing__error">{loadError}</p>}
      {error && <p role="alert" className="installation-timing__error">{error}</p>}
      {result && <p role="status">{result}</p>}

      <table className="installation-timing__saved" aria-label="Saved output timing">
        <thead>
          <tr>
            <th scope="col">Output</th>
            <th scope="col">Setting</th>
            <th scope="col">Delay (ms)</th>
            <th scope="col">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {outputs.map((output) => {
            const draft = drafts[output.resource];
            return (
              <tr key={output.resource}>
                <th scope="row">{output.resource}</th>
                <td>{KIND_LABEL[output.kind]}</td>
                <td>
                  <NumericInput
                    aria-label={`${output.resource} saved delay`}
                    min={output.minimum_ms}
                    max={output.maximum_ms}
                    step={1}
                    disabled={!canOperate || running}
                    value={draft ?? output.delay_ms}
                    onChange={(event) =>
                      setDrafts((current) => ({ ...current, [output.resource]: Number(event.target.value) }))
                    }
                  />
                  <small>
                    {output.minimum_ms} to {output.maximum_ms}
                  </small>
                </td>
                <td>
                  {canOperate && draft !== undefined && draft !== output.delay_ms && (
                    <button type="button" disabled={busy !== null || running} onClick={() => void saveOutput(output)}>
                      Save
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
          {timing && outputs.length === 0 && (
            <tr>
              <td colSpan={4}>The loaded installation has no projector or LED outputs.</td>
            </tr>
          )}
        </tbody>
      </table>

      <section className="installation-timing__wizard" aria-label="Timing calibration wizard">
        <h3>Calibrate with flash and click</h3>
        {!running ? (
          <>
            <p>
              Each chosen projector loops a white flash with a click every two seconds, and each chosen LED
              output flashes on the same beat. Adjust each output until the flashes line up, then accept.
            </p>
            {!idleMaintenance && (
              <div className="installation-timing__requirement" role="status">
                <span>Calibration needs maintenance mode with nothing playing.</span>
                {canOperate && (
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void action("Switching to maintenance", () => operate("maintenance"))}
                  >
                    Switch to maintenance (stops the show)
                  </button>
                )}
              </div>
            )}
            <fieldset disabled={!canOperate || busy !== null}>
              <legend>Outputs to calibrate</legend>
              {outputs.map((output) => (
                <div key={output.resource} className="installation-timing__choice">
                  <label>
                    <input
                      type="checkbox"
                      checked={selected[output.resource] ?? false}
                      onChange={(event) =>
                        setSelected((current) => ({ ...current, [output.resource]: event.target.checked }))
                      }
                    />
                    {output.resource} · {output.kind === "media" ? "projector" : "LEDs"}
                  </label>
                  {output.kind === "media" && (
                    <label>
                      <input
                        type="checkbox"
                        checked={clicks[output.resource] ?? true}
                        disabled={!selected[output.resource]}
                        onChange={(event) =>
                          setClicks((current) => ({ ...current, [output.resource]: event.target.checked }))
                        }
                      />
                      Play the click
                    </label>
                  )}
                </div>
              ))}
            </fieldset>
            {run && run.state === "failed" && (
              <p role="alert">The last calibration stopped: {run.error ?? "unknown error"}</p>
            )}
            {canOperate && (
              <button
                type="button"
                className="primary-button"
                disabled={!idleMaintenance || busy !== null || chosenProjectors.length + chosenLeds.length === 0}
                onClick={() => {
                  setResult(null);
                  void action("Starting calibration", async () => {
                    await startTimingCalibration(
                      installationId,
                      chosenProjectors.map((item) => ({ resource: item.resource, click: clicks[item.resource] ?? true })),
                      chosenLeds.map((item) => item.resource),
                    );
                  });
                }}
              >
                {busy === "Starting calibration" ? "Starting… (sending the clip to clients)" : "Start calibration"}
              </button>
            )}
          </>
        ) : (
          <>
            <p>
              Watch where each flash lands and listen for the click. Raise a delay to make that output later,
              lower it to make it earlier. LED changes show on the next flash; projector changes restart the
              loop. Focus a row and use ← → (Shift for 100 ms).
            </p>
            {run!.error && <p role="alert">{run!.error}</p>}
            <div className="installation-timing__live" aria-label="Live calibration delays">
              {run!.outputs.map((output) => {
                const deviceId = output.kind === "media" ? devices[output.resource] : undefined;
                const settings = deviceId ? audio[deviceId] : undefined;
                return (
                  <div
                    key={output.resource}
                    className="installation-timing__row"
                    role="group"
                    aria-label={`${output.resource} live delay`}
                    tabIndex={0}
                    onKeyDown={(event) => onRowKey(event, output)}
                  >
                    <strong>{output.resource}</strong>
                    <span>
                      {KIND_LABEL[output.kind]} · {STATE_LABEL[output.state]}
                      {output.drift_ms !== null && output.kind === "media" ? ` · drift ${output.drift_ms} ms` : ""}
                    </span>
                    {output.error && <small role="alert">{output.error}</small>}
                    <div className="installation-timing__controls">
                      {NUDGES.map((delta) => (
                        <button
                          key={delta}
                          type="button"
                          aria-label={`${delta > 0 ? "Later" : "Earlier"} by ${Math.abs(delta)} ms for ${output.resource}`}
                          onClick={() => void adjust(output, output.delay_ms + delta)}
                        >
                          {delta > 0 ? `+${delta}` : delta}
                        </button>
                      ))}
                      <NumericInput
                        aria-label={`${output.resource} live delay (ms)`}
                        step={1}
                        value={output.delay_ms}
                        onBlur={(event) => {
                          const value = Number(event.currentTarget.value);
                          if (Number.isFinite(value) && value !== output.delay_ms) void adjust(output, value);
                        }}
                        onChange={() => undefined}
                      />
                      <span>saved {output.saved_ms} ms</span>
                      {output.delay_ms !== output.saved_ms && (
                        <button type="button" onClick={() => void adjust(output, output.saved_ms)}>
                          Reset to saved
                        </button>
                      )}
                    </div>
                    {settings && deviceId && (
                      <div className="installation-timing__controls" aria-label={`${output.resource} client audio delay`}>
                        <span>Client audio delay {settings.effective?.audio_delay_ms ?? 0} ms</span>
                        {[-10, 10].map((delta) => (
                          <button
                            key={delta}
                            type="button"
                            aria-label={`Audio ${delta > 0 ? "later" : "earlier"} by 10 ms on ${settings.display_name}`}
                            onClick={() => void adjustAudio(deviceId, delta)}
                          >
                            {delta > 0 ? "Audio +10" : "Audio −10"}
                          </button>
                        ))}
                        <small>Saved to the client immediately.</small>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="installation-timing__actions">
              {calibratedProjectors.length > 0 && (
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void action("Resyncing", async () => void (await resyncTimingCalibration(installationId)))}
                >
                  Resync projectors
                </button>
              )}
              <button
                type="button"
                className="primary-button"
                disabled={busy !== null || !canOperate}
                onClick={() =>
                  void action("Accepting", async () => {
                    const finished = await finishTimingCalibration(installationId, "accept");
                    setResult(
                      finished.saved.length
                        ? `Saved ${finished.saved.map((item) => `${item.resource} ${item.delay_ms} ms`).join(", ")}.`
                        : "Accepted; no delays changed.",
                    );
                  })
                }
              >
                Accept and save
              </button>
              <button
                type="button"
                disabled={busy !== null || !canOperate}
                onClick={() =>
                  void action("Cancelling", async () => {
                    await finishTimingCalibration(installationId, "cancel");
                    setResult("Calibration cancelled; saved delays are unchanged.");
                  })
                }
              >
                Cancel
              </button>
            </div>
          </>
        )}
      </section>
    </section>
  );
}
