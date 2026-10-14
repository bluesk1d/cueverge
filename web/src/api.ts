export type Role = "viewer" | "designer" | "operator" | "publisher" | "administrator";

export interface Session {
  username: string;
  role: Role;
}

export interface InstallationResource {
  resource: string;
  kind: "media_agent" | "pixel_output";
  target: string | null;
  pixel_count: number | null;
}

export interface InstallationStatus {
  installation_id: "active";
  name: string;
  package_schema: "runtime-v1-v2";
  resources: InstallationResource[];
}

export interface ProjectSummary {
  id: string;
  name: string;
  head_revision: number;
  created_at: string;
  updated_at: string;
}

export interface RuntimeStatus {
  observed_at: string;
  source: "controller_runtime";
  status: {
    active_scene: string | null;
    estimated_position_ms: number | null;
    playback_state: string | null;
    degraded: boolean;
    degraded_reason: string | null;
    connected_agents: string[];
    media_outputs: MediaOutputStatus[];
    lighting_outputs: LightingOutputStatus[];
    pending_commands: number;
    command_errors: number;
    command_timeouts: number;
    render_loop_running: boolean;
    operations: {
      mode: string;
      show_enabled: boolean;
    };
    health: Record<string, string>;
  };
}

export type TopologyNodeKind =
  | "controller"
  | "direct_media_agent"
  | "downstream_controller"
  | "controller_output";

export interface TopologyNode {
  id: string;
  label: string;
  kind: TopologyNodeKind;
  parent_id: string | null;
  configured: boolean;
  connection: "connected" | "disconnected" | "not_independently_detectable";
  enrollment:
    | "local_controller"
    | "enrolled"
    | "suspended"
    | "revoked"
    | "not_applicable";
  compatibility: "compatible" | "conflict" | "unknown" | "not_applicable";
  protocol_version: number | null;
  capabilities: string[];
  assigned_resources: string[];
  last_seen_age_ms: number | null;
  software_version: string | null;
  latency_ms: number | null;
  clock_drift_ms: number | null;
  connection_policy?: "continuous" | "on_demand" | "not_applicable";
  reported_pixel_count?: number | null;
  signal_percent?: number | null;
  output_on?: boolean | null;
  diagnostic?: string | null;
}

export interface Topology {
  observed_at: string;
  source: "controller_runtime";
  enrollment_supported: boolean;
  nodes: TopologyNode[];
}

export interface PairingToken {
  id: string;
  pairing_token: string;
  device_kind: "direct_media_agent" | "downstream_controller";
  expires_at: string;
}

export type OperateAction =
  | "start"
  | "pause"
  | "resume"
  | "seek"
  | "stop"
  | "reset"
  | "automatic"
  | "maintenance"
  | "blackout";

export interface ResourceAssignment {
  resource: string;
  device_id: string;
  capability_kind: string;
  device_output: string | null;
  revision: number;
  actor: string;
  created_at: string;
  updated_at: string;
}

export interface MediaOutputStatus {
  output_id: string;
  agent_id: string;
  playback_session_id: string;
  required: boolean;
  start_delay_ms: number;
  clock_source: boolean;
  connected: boolean;
  prepare_failed: boolean;
  last_position_ms: number | null;
  drift_ms: number | null;
  pending_commands: number;
}

export interface LightingOutputStatus {
  output_id: string;
  resource: string;
  required: boolean;
  pixel_count: number;
  timing_offset_ms: number;
  error: string | null;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function getSession(): Promise<Session> {
  return request<Session>("/api/gui/v1/auth/session");
}

export async function login(username: string, password: string): Promise<Session> {
  return request<Session>("/api/gui/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
}

export async function logout(): Promise<void> {
  await request<void>("/api/gui/v1/auth/logout", {
    method: "POST",
    headers: { "X-CSRF-Token": readCookie("cueverge_csrf") ?? "" },
  });
}

export async function getInstallation(): Promise<InstallationStatus> {
  return request<InstallationStatus>("/api/gui/v1/installation");
}

export async function getProjects(): Promise<ProjectSummary[]> {
  return request<ProjectSummary[]>("/api/gui/v1/projects");
}

export async function getRuntimeStatus(): Promise<RuntimeStatus> {
  return request<RuntimeStatus>("/api/gui/v1/runtime/status");
}

export async function getTopology(): Promise<Topology> {
  return request<Topology>("/api/gui/v1/topology");
}

export async function getResourceAssignments(): Promise<ResourceAssignment[]> {
  return request<ResourceAssignment[]>("/api/gui/v1/resource-assignments");
}

export async function setResourceAssignment(
  resource: string,
  deviceId: string,
  capabilityKind: "media_playback" | "pixel_output" | "downstream_control",
  expectedRevision: number | null,
): Promise<ResourceAssignment> {
  return request<ResourceAssignment>(
    `/api/gui/v1/resource-assignments/${encodeURIComponent(resource)}`,
    {
      method: "PUT",
      headers: mutationHeaders(),
      body: JSON.stringify({
        device_id: deviceId,
        capability_kind: capabilityKind,
        expected_revision: expectedRevision,
      }),
    },
  );
}

export async function removeResourceAssignment(
  resource: string,
  revision: number,
): Promise<void> {
  await request(`/api/gui/v1/resource-assignments/${encodeURIComponent(resource)}`, {
    method: "DELETE",
    headers: { ...mutationHeaders(), "If-Match": `"${revision}"` },
  });
}

export async function createPairingToken(
  deviceKind: PairingToken["device_kind"],
): Promise<PairingToken> {
  return request<PairingToken>("/api/gui/v1/devices/pairing-tokens", {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify({ device_kind: deviceKind }),
  });
}

export async function setDeviceConnectionPolicy(
  deviceId: string,
  policy: "continuous" | "on_demand",
): Promise<void> {
  await request(`/api/gui/v1/devices/${encodeURIComponent(deviceId)}/connection-policy`, {
    method: "PUT",
    headers: mutationHeaders(),
    body: JSON.stringify({ policy }),
  });
}

export async function setDeviceEnrollmentState(
  deviceId: string,
  state: "enrolled" | "suspended" | "revoked",
): Promise<void> {
  await request(`/api/gui/v1/devices/${encodeURIComponent(deviceId)}/enrollment-state`, {
    method: "PUT",
    headers: mutationHeaders(),
    body: JSON.stringify({ state }),
  });
}

export async function operate(
  action: OperateAction,
  positionMs?: number,
): Promise<void> {
  await request("/api/gui/v1/operate/actions", {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify({
      action,
      ...(positionMs === undefined ? {} : { position_ms: positionMs }),
    }),
  });
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    throw new ApiError(response.status, `Cueverge API request failed (${response.status})`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  const cookie = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith(prefix));
  return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : null;
}

function mutationHeaders(): Record<string, string> {
  return {
    "Content-Type": "application/json",
    "X-CSRF-Token": readCookie("cueverge_csrf") ?? "",
  };
}
