import { useEffect, useState } from "react";
import {
  ApiError, configureInputSource, getInputSource, InputSourceSettings,
  InputSourceSnapshot, InputSourceValidation, Session, validateInputSourcePayload,
} from "../api";
import { ErrorState, Panel } from "../components/ui";

const reasons: Record<string, string> = {
  invalid_payload: "Not a valid Frigate event. Check the publishing topic and Frigate configuration.",
  payload_too_large: "Event exceeds the 256 KiB limit.",
  label_filtered: "Object label is not enabled for this connection.",
  audience_label_filtered: "Object label does not match the loaded show's visitor rules.",
  camera_not_matched: "Camera does not match the loaded show's visitor rules.",
  waiting_for_matching_zone: "Waiting for entry into a matching visitor zone.",
  observation_not_matched: "Observation did not qualify; it may be repeated or no longer tracked.",
  stale_observation: "Observation is too old. Check the loaded show's freshness limit and synchronize the Frigate/controller clocks.",
  event_expired: "Event expired before a loaded response could use it.",
  event_in_future: "Event timestamp is ahead of the controller clock.",
  duplicate: "Repeated event was ignored.",
  debounced: "Events arrived too close together for the loaded repetition limit.",
  no_transition: "No response in the current show state uses this event.",
  show_not_automatic: "The loaded show is not in automatic mode.",
  controller_unavailable: "The loaded show cannot receive this event. Check that visitor rules are loaded.",
  irrelevant: "No loaded rule accepted this event.",
};

export function InputSourcePanel({ session, onDirtyChange }: {
  session?: Session; onDirtyChange?: (dirty: boolean) => void;
}) {
  const [snapshot, setSnapshot] = useState<InputSourceSnapshot | null>(null);
  const [form, setForm] = useState<InputSourceSettings | null>(null);
  const [baseline, setBaseline] = useState<{ settings: InputSourceSettings; revision: number } | null>(null);
  const [password, setPassword] = useState("");
  const [clearPassword, setClearPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [camera, setCamera] = useState("");
  const [zone, setZone] = useState("");
  const [validation, setValidation] = useState<InputSourceValidation | null>(null);
  const dirty = Boolean(form && baseline && (JSON.stringify(form) !== JSON.stringify(baseline.settings) || password || clearPassword));
  const canEdit = session?.role === "administrator" && snapshot?.management === "gui";

  useEffect(() => {
    onDirtyChange?.(dirty);
    const beforeUnload = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault(); };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  useEffect(() => {
    if (busy) return;
    let current = true;
    let timer: number | undefined;
    let controller: AbortController | null = null;
    let timeout: number | undefined;
    let expired = false;
    const load = async () => {
      if (!current || expired) return;
      controller = new AbortController();
      timeout = window.setTimeout(() => controller?.abort(), 8000);
      try {
        const value = await getInputSource(controller.signal);
        if (!current) return;
        setSnapshot(value);
        setHealthError(null);
        setForm((previous) => previous ?? value.settings);
        setBaseline((previous) => previous ?? { settings: value.settings, revision: value.configuration_revision });
      } catch (failure) {
        if (!current) return;
        expired = failure instanceof ApiError && failure.status === 401;
        setHealthError(expired ? "Sign in again to refresh input health. Your edits are retained in this tab." : "Input health could not be refreshed. Displayed values are last known, not live.");
      } finally {
        window.clearTimeout(timeout);
        controller = null;
        if (current && !expired) timer = window.setTimeout(() => { if (document.visibilityState !== "hidden") void load(); }, 2000);
      }
    };
    const resume = () => {
      if (document.visibilityState !== "hidden" && !controller) void load();
    };
    void load();
    const visibility = () => {
      if (document.visibilityState !== "hidden") {
        window.clearTimeout(timer);
        resume();
      }
    };
    document.addEventListener("visibilitychange", visibility);
    return () => {
      current = false; controller?.abort();
      window.clearTimeout(timer); window.clearTimeout(timeout);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [refresh, busy]);

  async function save() {
    if (!form || !baseline) return;
    if (form.enabled && !window.confirm("Connect camera events now? Real visitor events can trigger responses in the loaded automatic show.")) return;
    setBusy(true); setError(null); setValidation(null);
    try {
      const value = await configureInputSource({
        ...form, expected_revision: baseline.revision, clear_password: clearPassword,
        ...(password ? { password } : {}),
      });
      setSnapshot(value); setForm(value.settings);
      setBaseline({ settings: value.settings, revision: value.configuration_revision });
      setPassword(""); setClearPassword(false);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Input settings could not be saved.");
    } finally { setBusy(false); }
  }

  async function checkExample() {
    setChecking(true); setError(null); setValidation(null);
    try {
      const timestamp = Date.now() / 1000;
      setValidation(await validateInputSourcePayload({
        type: "new", after: {
          id: "isolated-setup-example", label: "person", camera,
          start_time: timestamp, frame_time: timestamp, entered_zones: zone ? [zone] : [],
        },
      }));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "The isolated event check failed.");
    } finally { setChecking(false); }
  }

  return <Panel title="Inputs & connections" description="Connect Frigate camera detections to visitor responses. No video streaming is needed.">
    <section className="input-source" aria-label="Camera event connection">
      {healthError && <p role="alert">{healthError}</p>}
      {error && <ErrorState title="Input action failed" description={error} />}
      {!snapshot || !form ? <p>{healthError ? "Input setup is unavailable." : "Loading input setup…"}</p> : <>
        <p><strong>{healthError ? "Health unavailable" : ({
          disabled: "Camera events off", connecting: "Connecting to broker…", subscribed: "Subscribed to camera events",
          stopped: "Listener stopped", unavailable: "Input setup needs attention",
        }[snapshot.state])}</strong> · {snapshot.freshness === "no_events" ? "No normalized events received" : snapshot.freshness === "stale" ? "Last observation is stale" : "Recent observation received"}</p>
        {snapshot.error && <p role="alert">{snapshot.error}</p>}
        <p>Connection does not prove that visitor events are arriving or that a scene will play.</p>
        {snapshot.management === "environment" && <p>Operator-managed connection: these settings are read-only. Change the controller's CUEVERGE_FRIGATE_* environment settings and restart it.</p>}
        {session?.role !== "administrator" && <p>An installation administrator can change connection settings.</p>}
        <details>
          <summary>{form.host ? "Connection settings" : "Set up Frigate camera events"}</summary>
          <form onSubmit={(event) => { event.preventDefault(); void save(); }}>
            <fieldset disabled={!canEdit || busy || checking}>
              <legend>Frigate MQTT connection</legend>
              <small>Use the same MQTT broker as Frigate, not the camera's address.</small>
              <label><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} />Enable camera events</label>
              <div className="input-source__address">
                <label>Broker hostname<input required={form.enabled} value={form.host} autoComplete="off" placeholder="e.g. mqtt.local" onChange={(event) => setForm({ ...form, host: event.target.value })} /></label>
                <label>Broker port<input type="number" required min={1} max={65535} value={Number.isNaN(form.port) ? "" : form.port} onChange={(event) => setForm({ ...form, port: event.target.valueAsNumber })} /></label>
              </div>
              <label><input type="checkbox" checked={form.tls} onChange={(event) => setForm({ ...form, tls: event.target.checked })} />Use TLS with trusted broker certificate</label>
              {!form.tls && <small>Without TLS, credentials and events travel unencrypted. Use only on a trusted local network.</small>}
              <details><summary>Broker login (optional)</summary>
              <label>Broker username (optional)<input autoComplete="off" value={form.username ?? ""} onChange={(event) => setForm({ ...form, username: event.target.value || null })} /></label>
              <label>Broker password<input type="password" autoComplete="new-password" value={password} disabled={clearPassword} onChange={(event) => setPassword(event.target.value)} /></label>
              <small>{snapshot.credential_present ? "Password stored on this controller. Leave blank to retain it." : "No stored password. Leave blank for a broker without password authentication."}</small>
              {snapshot.credential_present && <label><input type="checkbox" checked={clearPassword} onChange={(event) => { setClearPassword(event.target.checked); setPassword(""); }} />Remove stored password</label>}
              </details>
              <details><summary>Advanced connection settings</summary>
                <label>Frigate event topic<input required value={form.topic} onChange={(event) => setForm({ ...form, topic: event.target.value })} /></label>
                <label>Accepted object labels<input required value={form.accepted_labels.join(", ")} onChange={(event) => setForm({ ...form, accepted_labels: event.target.value.split(",").map((label) => label.trim()) })} /></label>
              </details>
              <p>Saving reconnects only the camera listener, not the show. Enabling it allows real events to reach the loaded automatic show.</p>
              <button className="primary-button" disabled={!dirty && !snapshot.error}>{busy ? "Saving…" : !dirty && snapshot.error && form.enabled ? "Retry connection" : form.enabled ? "Save and connect" : "Save connection"}</button>
            </fieldset>
          </form>
          <button disabled={busy} onClick={() => {
            if (dirty && !window.confirm("Discard unsaved connection settings?")) return;
            setForm(snapshot.settings); setBaseline({ settings: snapshot.settings, revision: snapshot.configuration_revision });
            setPassword(""); setClearPassword(false); setError(null); setValidation(null);
          }}>Reload saved settings</button>
        </details>
        <details>
          <summary>Check event reception</summary>
          <p>Received: {snapshot.messages_received} · Delivered: {snapshot.events_delivered} · Accepted by controller: {snapshot.controller_accepted} · Matched visitor rules: {snapshot.audience_matches}</p>
          <p>Invalid: {snapshot.invalid_messages} · Filtered labels: {snapshot.filtered_messages} · Rejected by controller: {snapshot.controller_rejected}</p>
          <p>{snapshot.last_received_at_ms ? `Last message: ${new Date(snapshot.last_received_at_ms).toLocaleTimeString()}` : "No broker messages received in this listener session."}</p>
          {!snapshot.loaded_rules.audience && <p>No visitor qualification rules are loaded. Edit the show and apply its rules before expecting qualified visitors.</p>}
          {snapshot.loaded_rules.audience && <p>Loaded visitor rules: cameras {snapshot.loaded_rules.audience.cameras.join(", ") || "any"}; zones {snapshot.loaded_rules.audience.zones.join(", ") || "any"}. Freshness limit: {snapshot.freshness_ms / 1000}s.</p>}
          {snapshot.loaded_rules.audience && <p>Visitor qualification: {snapshot.loaded_rules.audience_state?.state === "present" ? "Visitors present" : snapshot.loaded_rules.audience_state?.state === "candidate" ? "Confirming arrival" : snapshot.loaded_rules.audience_state?.state === "absent" ? "No qualified visitors" : "No state reported"}.</p>}
          <p>{snapshot.recovery}</p>
          {snapshot.recent_events.length > 0 && <ul>{snapshot.recent_events.slice().reverse().map((event, index) => <li key={index}>
            {event.camera || "Unknown camera"}{event.zone ? ` / ${event.zone}` : ""}: {event.event_type || "Message"} · {event.reason ? reasons[event.reason] ?? event.reason : event.audience_relevant ? "Matched visitor rules" : event.accepted ? "Accepted by controller" : "Normalized"}
          </li>)}</ul>}
        </details>
        <details>
          <summary>Try an isolated example</summary>
          <p>This checks saved connection labels and the loaded show's camera/zone rules. It does not send a live event or verify the MQTT connection.</p>
          <label>Example camera<input disabled={checking || busy} value={camera} onChange={(event) => { setCamera(event.target.value); setValidation(null); }} /></label>
          <label>Example zone (optional)<input disabled={checking || busy} value={zone} onChange={(event) => { setZone(event.target.value); setValidation(null); }} /></label>
          <button disabled={busy || checking || !camera || dirty} onClick={() => void checkExample()}>{checking ? "Checking…" : "Check example safely"}</button>
          {dirty && <small>Save or reload connection settings before checking an example.</small>}
          {validation && (dirty || validation.validation_context_digest !== snapshot.validation_context_digest) && <p role="status">Connection settings or loaded visitor rules changed. Check the example again after saving or reloading settings.</p>}
          {validation && !dirty && validation.validation_context_digest === snapshot.validation_context_digest && <div role="status">
            <p>{validation.label_filtered ? "The example's person label is filtered out." : !validation.audience_configured ? "Normalized, but no visitor rules are loaded." : validation.events.some((event) => event.audience_relevant) ? "Example matches loaded visitor rules." : "Example does not match loaded visitor rules."}</p>
            <small>{validation.note}</small>
          </div>}
        </details>
      </>}
      <button disabled={busy} onClick={() => setRefresh((value) => value + 1)}>Refresh input health</button>
    </section>
  </Panel>;
}
