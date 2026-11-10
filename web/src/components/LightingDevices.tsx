import { useState } from "react";
import {
  createLightingDevice, detectWledController, removeLightingDevice, updateLightingDevice,
  type LightingDevice, type LightingDeviceConfig, type WledDetection,
} from "../api";

export type LightingKind = "lifx" | "wled";

const RESOURCE_PATTERN = "[A-Za-z0-9][A-Za-z0-9._\\-]{0,99}";
const IPV4_PATTERN = "\\d{1,3}(\\.\\d{1,3}){3}";

function message(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

/** Registration/edit form for controller-driven lights. Fields change with the device type. */
export function LightingDeviceForm({ installationId, kind, revision, device, onSaved, onCancel }: {
  installationId: string;
  kind: LightingKind;
  revision: number;
  device?: LightingDevice;
  onSaved: (resource: string, created: boolean) => Promise<void> | void;
  onCancel?: () => void;
}) {
  const editing = device !== undefined;
  const lifx = device?.kind === "lifx" ? device : undefined;
  const wled = device?.kind === "wled" ? device : undefined;
  const [resource, setResource] = useState(device?.resource ?? "");
  const [host, setHost] = useState(device?.host ?? "");
  const [target, setTarget] = useState(lifx?.target ?? "");
  const [pixels, setPixels] = useState(wled ? String(wled.expected_pixel_count) : "");
  const [brightness, setBrightness] = useState(
    String(Math.round((device?.brightness_limit ?? (kind === "lifx" ? 0.25 : 1)) * 100)),
  );
  const [port, setPort] = useState(String(device?.port ?? (kind === "lifx" ? 56700 : 4048)));
  const [kelvin, setKelvin] = useState(String(lifx?.kelvin ?? 3500));
  const [maxHz, setMaxHz] = useState(String(lifx?.max_hz ?? 18));
  const [transition, setTransition] = useState(String(lifx?.duration_ms ?? 0));
  const [destination, setDestination] = useState(String(wled?.destination_id ?? 1));
  const [payload, setPayload] = useState(String(wled?.max_payload_bytes ?? 1440));
  const [detection, setDetection] = useState<WledDetection | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = kind === "lifx" ? "LIFX bulb" : "WLED controller";

  function config(): LightingDeviceConfig {
    const shared = { resource, host: host.trim(), port: Number(port), brightness_limit: Number(brightness) / 100 };
    return kind === "lifx"
      ? { kind, ...shared, target: target.trim().toLowerCase(), kelvin: Number(kelvin), max_hz: Number(maxHz), duration_ms: Number(transition) }
      : { kind, ...shared, expected_pixel_count: Number(pixels), destination_id: Number(destination), max_payload_bytes: Number(payload) };
  }

  async function detect() {
    setBusy(true); setError(null); setDetection(null);
    try {
      const found = await detectWledController(installationId, host.trim());
      setDetection(found);
      if (found.connected && found.reported_pixel_count && !editing) setPixels(String(found.reported_pixel_count));
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }

  return <form aria-label={editing ? `Edit ${device.resource}` : `Add ${label}`} className="lighting-device-form" onSubmit={(event) => {
    event.preventDefault(); setBusy(true); setError(null);
    const value = config();
    void (editing ? updateLightingDevice(installationId, value, revision) : createLightingDevice(installationId, value, revision))
      .then(() => onSaved(value.resource, !editing))
      .catch((e: unknown) => setError(message(e)))
      .finally(() => setBusy(false));
  }}>
    <p className="muted">{kind === "lifx"
      ? "A LIFX bulb is a stationary point light: one color sample placed in your layout, driven over the LAN protocol."
      : "A WLED controller drives an LED strip as one pixel output over DDP (UDP 4048). Enable DDP/E1.31 receive in WLED."}
      {" "}Stop playback, output tests and calibration first. Saving sends no lighting.</p>
    <label>Output resource name<input required disabled={editing} pattern={RESOURCE_PATTERN} value={resource}
      onChange={(e) => setResource(e.target.value)} placeholder={kind === "lifx" ? "light.left" : "roofline"} /></label>
    <label>IPv4 address<input required pattern={IPV4_PATTERN} value={host} inputMode="decimal"
      onChange={(e) => { setHost(e.target.value); setDetection(null); }} placeholder={kind === "lifx" ? "192.168.40.71" : "192.168.40.80"} /></label>
    {kind === "lifx" && <label>LIFX target ID<input required pattern="[0-9a-fA-F]{12}" value={target}
      onChange={(e) => setTarget(e.target.value)} placeholder="d073d58b1a6d" />
      <small>The bulb's 12-digit MAC address (LIFX app → bulb settings), without colons.</small></label>}
    {kind === "wled" && <>
      <button type="button" disabled={busy || !new RegExp(`^${IPV4_PATTERN}$`).test(host.trim())} onClick={() => void detect()}>
        {busy && !detection ? "Detecting…" : "Detect WLED"}</button>
      {detection && <p role="status">{detection.connected
        ? `WLED ${detection.software_version ?? ""} reports ${detection.reported_pixel_count ?? "?"} LEDs` +
          (detection.signal_percent != null ? ` · Wi-Fi ${detection.signal_percent}%` : "")
        : detection.diagnostic ?? "Not reachable"}</p>}
      <label>LED count<input required type="number" min={1} max={10000} disabled={editing} value={pixels}
        onChange={(e) => setPixels(e.target.value)} />
        {editing && <small>To change the LED count, remove the device and add it again.</small>}</label>
    </>}
    <label>Brightness cap (%)<input required type="number" min={1} max={100} value={brightness}
      onChange={(e) => setBrightness(e.target.value)} /></label>
    <details><summary>Advanced {label} settings</summary>
      <label>{kind === "lifx" ? "UDP port" : "DDP port"}<input type="number" min={1} max={65535} value={port} onChange={(e) => setPort(e.target.value)} /></label>
      {kind === "lifx" ? <>
        <label>White temperature (K)<input type="number" min={1500} max={9000} value={kelvin} onChange={(e) => setKelvin(e.target.value)} /></label>
        <label>Maximum updates per second<input type="number" min={1} max={20} step="any" value={maxHz} onChange={(e) => setMaxHz(e.target.value)} /></label>
        <label>Transition (ms)<input type="number" min={0} max={1000} value={transition} onChange={(e) => setTransition(e.target.value)} /></label>
      </> : <>
        <label>DDP destination ID<input type="number" min={1} max={255} value={destination} onChange={(e) => setDestination(e.target.value)} /></label>
        <label>Maximum packet payload (bytes)<input type="number" min={3} max={65535} value={payload} onChange={(e) => setPayload(e.target.value)} /></label>
      </>}
    </details>
    <div className="lighting-device-form__actions">
      <button className="primary-button" disabled={busy}>{busy ? "Saving…" : editing ? "Save changes" : `Add ${label}`}</button>
      {onCancel && <button type="button" onClick={onCancel}>Cancel</button>}
    </div>
    {error && <p role="alert">{error}</p>}
  </form>;
}

/** Registered LIFX/WLED devices with edit and remove. */
export function LightingDeviceList({ installationId, devices, revision, onChanged }: {
  installationId: string;
  devices: LightingDevice[];
  revision: number;
  onChanged: () => Promise<void> | void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!devices.length) {
    return <p className="muted">No LIFX bulbs or WLED controllers yet. Use Add a device.</p>;
  }
  return <div className="policy-list">
    {devices.map((device) => <article key={device.resource} className="lighting-device">
      <div>
        <strong>{device.resource}</strong>
        <small>{device.kind === "lifx" ? `LIFX bulb · ${device.target}` : `WLED / DDP controller · ${device.pixel_count} LEDs`}
          {" "}· {device.host} · cap {Math.round((device.brightness_limit ?? 1) * 100)}%
          {device.in_use ? " · used by the loaded show" : ""}</small>
        {device.managed_by === "deployment_config" && <small>From the controller's output config file
          (CUEVERGE_OUTPUT_CONFIG). Change or remove it there; it can't be edited here.</small>}
      </div>
      {device.managed_by === "deployment_config" ? null : editing === device.resource ? <LightingDeviceForm installationId={installationId} kind={device.kind}
        revision={revision} device={device} onCancel={() => setEditing(null)}
        onSaved={async () => { setEditing(null); await onChanged(); }} /> : <div className="discovered-endpoint-actions">
        <button type="button" onClick={() => { setError(null); setEditing(device.resource); }}>Edit {device.resource}</button>
        <button type="button" disabled={device.in_use}
          title={device.in_use ? "Apply a show that doesn't use this output first" : undefined}
          onClick={() => {
            if (!window.confirm(`Remove ${device.resource}? Shows and layout points that use it keep the reference but it won't light until registered again.`)) return;
            setError(null);
            void removeLightingDevice(installationId, device.resource, revision)
              .then(() => onChanged()).catch((e: unknown) => setError(message(e)));
          }}>Remove {device.resource}</button>
      </div>}
    </article>)}
    {error && <p role="alert">{error}</p>}
  </div>;
}
