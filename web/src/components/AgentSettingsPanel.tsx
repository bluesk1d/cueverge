import { useCallback, useEffect, useState } from "react";

import {
  AgentEffectiveSettings,
  AgentSettings,
  DeviceAgentSettings,
  applyDeviceAgentSettings,
  getDeviceAgentSettings,
  updateDeviceAgentSettings,
} from "../api";

const DEFAULT = "";
const settingLabels: Record<keyof AgentEffectiveSettings, string> = {
  keep_awake: "Keep awake",
  player_mode: "Player mode",
  fullscreen_screen: "Projector display",
  audio_device: "Audio output",
  audio_delay_ms: "Audio delay (ms)",
  telemetry_hz: "Position updates per second",
};

const syncLabels: Record<DeviceAgentSettings["sync"], string> = {
  applied: "Applied on the host",
  pending: "Sending to the host",
  offline: "Host offline",
  unsupported: "This agent can't receive settings",
  failed: "The host didn't apply the settings",
};

function describe(name: keyof AgentEffectiveSettings, value: unknown, settings: DeviceAgentSettings | null): string {
  if (value === null || value === undefined) {
    return name === "audio_device" ? "mpv default" : name === "fullscreen_screen" ? "mpv default" : "not set";
  }
  if (name === "keep_awake") {
    return { off: "Off", system: "Block sleep", display: "Block sleep and screen blanking" }[value as string] ?? String(value);
  }
  if (name === "player_mode") return value === "always" ? "Always open" : "On demand";
  if (name === "fullscreen_screen") {
    const display = settings?.displays.find((item) => item.index === value);
    return display ? displayLabel(display) : `Display ${value}`;
  }
  if (name === "audio_device") {
    return settings?.audio_devices.find((item) => item.name === value)?.description || String(value);
  }
  return String(value);
}

function displayLabel(display: DeviceAgentSettings["displays"][number]): string {
  const size = display.width && display.height ? ` · ${display.width}×${display.height}` : "";
  return `${display.index} · ${display.name}${size}${display.primary ? " · primary" : ""}`;
}

/** Edit the controller-managed settings of a media host; host defaults come from its env file. */
export function AgentSettingsPanel({ deviceId, canEdit }: { deviceId: string; canEdit: boolean }) {
  const [settings, setSettings] = useState<DeviceAgentSettings | null>(null);
  const [draft, setDraft] = useState<AgentSettings>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await getDeviceAgentSettings(deviceId);
      setSettings(next);
      setDraft(next.overrides);
    } catch {
      setError("Agent settings could not be loaded.");
    }
  }, [deviceId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!settings) {
    return error ? <p role="alert">{error}</p> : <p className="muted">Loading agent settings…</p>;
  }

  const defaults = settings.host_defaults;
  const editable = canEdit;
  const dirty = JSON.stringify(normalize(draft)) !== JSON.stringify(normalize(settings.overrides));

  function update<K extends keyof AgentSettings>(name: K, value: AgentSettings[K]) {
    setSaved(false);
    setDraft((current) => ({ ...current, [name]: value }));
  }

  function defaultOption(name: keyof AgentEffectiveSettings) {
    const value = defaults ? describe(name, defaults[name], settings) : "unknown while offline";
    return <option value={DEFAULT}>Host default ({value})</option>;
  }

  async function save() {
    if (!settings) return;
    setBusy(true);
    setError(null);
    try {
      const next = await updateDeviceAgentSettings(deviceId, normalize(draft), settings.revision);
      setSettings(next);
      setDraft(next.overrides);
      setSaved(true);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Agent settings could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  async function applyNow() {
    setBusy(true);
    setError(null);
    try {
      const next = await applyDeviceAgentSettings(deviceId);
      setSettings(next);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Agent settings could not be applied.");
    } finally {
      setBusy(false);
    }
  }

  const displays = [...settings.displays];
  if (draft.fullscreen_screen != null && !displays.some((item) => item.index === draft.fullscreen_screen)) {
    displays.push({ index: draft.fullscreen_screen, name: `Display ${draft.fullscreen_screen} (not detected)` });
  }
  const audioDevices = [...settings.audio_devices];
  if (draft.audio_device && !audioDevices.some((item) => item.name === draft.audio_device)) {
    audioDevices.push({ name: draft.audio_device, description: `${draft.audio_device} (not detected)` });
  }

  return (
    <section className="agent-settings" aria-labelledby={`agent-settings-${deviceId}`}>
      <h4 id={`agent-settings-${deviceId}`}>Agent settings</h4>
      <p role="status">
        {syncLabels[settings.sync]}
        {settings.detail ? `: ${settings.detail}` : ""}
      </p>
      {settings.pending_player_restart.length > 0 && (
        <p>
          Waiting for a player restart:{" "}
          {settings.pending_player_restart.map((name) => settingLabels[name]).join(", ")}
        </p>
      )}
      <div className="agent-settings__grid">
        <label>
          {settingLabels.keep_awake}
          <select
            disabled={!editable || busy}
            value={draft.keep_awake ?? DEFAULT}
            onChange={(event) => update("keep_awake", (event.target.value || null) as AgentSettings["keep_awake"])}
          >
            {defaultOption("keep_awake")}
            <option value="off">Off</option>
            <option value="system">Block sleep</option>
            <option value="display">Block sleep and screen blanking</option>
          </select>
        </label>
        <label>
          {settingLabels.player_mode}
          <select
            disabled={!editable || busy}
            value={draft.player_mode ?? DEFAULT}
            onChange={(event) => update("player_mode", (event.target.value || null) as AgentSettings["player_mode"])}
          >
            {defaultOption("player_mode")}
            <option value="always">Always open</option>
            <option value="on_demand">On demand</option>
          </select>
        </label>
        <label>
          {settingLabels.fullscreen_screen}
          <select
            disabled={!editable || busy}
            value={draft.fullscreen_screen == null ? DEFAULT : String(draft.fullscreen_screen)}
            onChange={(event) => update("fullscreen_screen", event.target.value === DEFAULT ? null : Number(event.target.value))}
          >
            {defaultOption("fullscreen_screen")}
            {displays.map((display) => (
              <option key={display.index} value={String(display.index)}>{displayLabel(display)}</option>
            ))}
          </select>
        </label>
        <label>
          {settingLabels.audio_device}
          <select
            disabled={!editable || busy}
            value={draft.audio_device ?? DEFAULT}
            onChange={(event) => update("audio_device", event.target.value || null)}
          >
            {defaultOption("audio_device")}
            {audioDevices.map((device) => (
              <option key={device.name} value={device.name}>{device.description || device.name}</option>
            ))}
          </select>
        </label>
        <label>
          {settingLabels.audio_delay_ms}
          <input
            type="number"
            min={-5000}
            max={5000}
            step={1}
            disabled={!editable || busy}
            placeholder={`Host default (${defaults ? defaults.audio_delay_ms : "unknown"})`}
            value={draft.audio_delay_ms ?? ""}
            onChange={(event) => update("audio_delay_ms", event.target.value === "" ? null : Number(event.target.value))}
          />
        </label>
        <label>
          {settingLabels.telemetry_hz}
          <input
            type="number"
            min={1}
            max={60}
            step={1}
            disabled={!editable || busy}
            placeholder={`Host default (${defaults ? defaults.telemetry_hz : "unknown"})`}
            value={draft.telemetry_hz ?? ""}
            onChange={(event) => update("telemetry_hz", event.target.value === "" ? null : Number(event.target.value))}
          />
        </label>
      </div>
      <p className="muted">
        Display indices are reported by the host; confirm the picture lands on the projector. Display
        changes restart the player only in maintenance mode with nothing playing.
      </p>
      {editable && (
        <div className="agent-settings__actions">
          <button type="button" className="primary-button" disabled={!dirty || busy} onClick={() => void save()}>
            {busy ? "Saving…" : "Save agent settings"}
          </button>
          {settings.pending_player_restart.length > 0 && (
            <button type="button" disabled={busy} onClick={() => void applyNow()}>Apply now</button>
          )}
        </div>
      )}
      {saved && !error && <p role="status">Saved.</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

function normalize(settings: AgentSettings): AgentSettings {
  const result: AgentSettings = {};
  for (const [name, value] of Object.entries(settings)) {
    if (value !== null && value !== undefined && !(typeof value === "number" && Number.isNaN(value))) {
      (result as Record<string, unknown>)[name] = value;
    }
  }
  return result;
}
