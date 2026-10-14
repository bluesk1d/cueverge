export type Role = "viewer" | "designer" | "operator" | "publisher" | "administrator";

export interface Session {
  username: string;
  role: Role;
}

export interface InstallationResource {
  resource: string;
  kind: "media_agent" | "pixel_output" | "downstream_controller";
  target: string | null;
  pixel_count: number | null;
}

export interface InstallationStatus {
  installation_id: string;
  name: string;
  revision: number;
  runtime_active: boolean;
  package_schema: "runtime-v1-v2";
  resources: InstallationResource[];
}

export interface InstallationSummary {
  id: string;
  name: string;
  revision: number;
  runtime_active: boolean;
  created_at: string;
  updated_at: string;
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
    controller_epoch: string;
    active_scene: string | null;
    scene_instance_id: string | null;
    playback_session_id: string | null;
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
    abandoned_commands: number;
    recent_command_failures: {
      agent_id: string;
      command: string;
      error_code: string;
      detail: string | null;
      age_ms: number;
    }[];
    heartbeat_timeouts: number;
    reconciliations: number;
    scene_completions: number;
    scene_completion_errors: number;
    scene_failovers: number;
    rejected_protocol_messages: number;
    rendered_frames: number;
    lighting_output_error_count: number;
    rejected_telemetry: number;
    render_target_fps: number;
    render_loop_running: boolean;
    render_ticks: number;
    render_ticks_without_timecode: number;
    stale_frames: number;
    skipped_render_deadlines: number;
    mean_render_interval_ms: number | null;
    encounter: Record<string, unknown> | null;
    director: Record<string, unknown> | null;
    audience: Record<string, unknown> | null;
    opportunities: Record<string, unknown> | null;
    encounter_loop_running: boolean;
    queued_scenes: string[];
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

export interface ProtocolRange {
  minimum: number;
  maximum: number;
}

export interface MediaPlaybackCapability {
  kind: "media_playback";
  codecs: string[];
  containers: string[];
  max_outputs: number;
  supports_pause: boolean;
  supports_seek: boolean;
}

export interface ArtifactCacheCapability {
  kind: "artifact_cache";
  digest: "sha256";
  total_bytes: number;
  free_bytes: number;
  supports_pinning: boolean;
  supports_resumable_transfer: boolean;
}

export interface GenericDeviceCapability {
  kind: string;
  [key: string]: unknown;
}

export interface DeviceCapabilityDocument {
  schema_version: 1;
  protocol: ProtocolRange;
  device_kind: "direct_media_agent" | "downstream_controller";
  software_version: string;
  capabilities: (
    | MediaPlaybackCapability
    | ArtifactCacheCapability
    | GenericDeviceCapability
  )[];
}

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
  compatibility_detail?: string | null;
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
  capability_document?: DeviceCapabilityDocument | null;
  certificate_expires_at?: string | null;
  storage_total_bytes?: number | null;
  storage_free_bytes?: number | null;
  artifact_count?: number | null;
  artifact_bytes?: number | null;
  pinned_artifact_count?: number | null;
  supports_pinning?: boolean | null;
  supports_resumable_transfer?: boolean | null;
  active_transfers?: number | null;
  failed_transfers?: number | null;
  artifact_faults?: string[];
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
  device_transport_url: string;
  discovered_endpoint_id: string | null;
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

export interface MaintenancePreview {
  resource: string;
  resource_kind: "media_agent" | "pixel_output";
  pattern: "alignment_grid" | "pixel_index_chase";
  simulation_only: true;
  started_at: string;
  expires_at: string;
}

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

export interface ActivityEvent {
  id: number;
  occurred_at: string;
  category: "device" | "operation" | "configuration" | "project" | "security";
  action: string;
  actor: string;
  target: string;
  result: string;
}

export interface DeviceConnectionTest {
  device_id: string;
  observed_at: string;
  result: "passed" | "failed";
  checks: {
    name: "enrollment" | "heartbeat" | "timing" | "assignment";
    passed: boolean;
    detail: string;
  }[];
  latency_ms: number | null;
  clock_offset_ms: number | null;
}

export interface CapabilityReview {
  compatible: boolean;
  controller_protocol_version: number;
  capability_kinds: string[];
  issues: {
    code: string;
    message: string;
  }[];
}

export interface DiscoveredEndpoint {
  id: string;
  base_url: string;
  device_kind: "direct_media_agent" | "downstream_controller";
  software_version: string;
  protocol_minimum: number;
  protocol_maximum: number;
  capability_kinds: string[];
  compatible: boolean;
  state: "discovered" | "pairing" | "enrolled" | "rejected";
  enrolled_device_id: string | null;
  observed_at: string;
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

export async function getInstallations(): Promise<InstallationSummary[]> {
  return request<InstallationSummary[]>("/api/gui/v1/installations");
}

export async function createInstallation(name: string): Promise<InstallationSummary> {
  return request<InstallationSummary>("/api/gui/v1/installations", {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify({ name }),
  });
}

export async function updateInstallation(
  installationId: string,
  revision: number,
  name: string,
): Promise<InstallationSummary> {
  return request<InstallationSummary>(
    `/api/gui/v1/installations/${encodeURIComponent(installationId)}`,
    {
      method: "PUT",
      headers: { ...mutationHeaders(), "If-Match": `"${revision}"` },
      body: JSON.stringify({ name }),
    },
  );
}

export async function deleteInstallation(
  installationId: string,
  revision: number,
): Promise<void> {
  await request(`/api/gui/v1/installations/${encodeURIComponent(installationId)}`, {
    method: "DELETE",
    headers: { ...mutationHeaders(), "If-Match": `"${revision}"` },
  });
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

export async function getActivity(): Promise<ActivityEvent[]> {
  return request<ActivityEvent[]>("/api/gui/v1/activity");
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
  discoveredEndpointId?: string,
): Promise<PairingToken> {
  return request<PairingToken>("/api/gui/v1/devices/pairing-tokens", {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify({
      device_kind: deviceKind,
      discovered_endpoint_id: discoveredEndpointId ?? null,
    }),
  });
}

export async function reviewDeviceCapabilities(
  document: unknown,
): Promise<CapabilityReview> {
  return request<CapabilityReview>("/api/gui/v1/devices/capabilities/review", {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify(document),
  });
}

export async function getDiscoveredEndpoints(): Promise<DiscoveredEndpoint[]> {
  return request<DiscoveredEndpoint[]>("/api/gui/v1/discovery/endpoints");
}

export async function probeEndpoint(address: string): Promise<DiscoveredEndpoint> {
  return request<DiscoveredEndpoint>("/api/gui/v1/discovery/probe", {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify({ address }),
  });
}

export async function rejectDiscoveredEndpoint(
  endpointId: string,
): Promise<DiscoveredEndpoint> {
  return request<DiscoveredEndpoint>(
    `/api/gui/v1/discovery/endpoints/${encodeURIComponent(endpointId)}/reject`,
    {
      method: "POST",
      headers: mutationHeaders(),
    },
  );
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

export async function testDeviceConnection(deviceId: string): Promise<DeviceConnectionTest> {
  return request<DeviceConnectionTest>(
    `/api/gui/v1/devices/${encodeURIComponent(deviceId)}/connection-test`,
    {
      method: "POST",
      headers: mutationHeaders(),
    },
  );
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

export async function startMaintenancePreview(
  resource: string,
  durationSeconds: number,
): Promise<MaintenancePreview> {
  return request<MaintenancePreview>("/api/gui/v1/maintenance/test-pattern-preview", {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify({
      resource,
      duration_seconds: durationSeconds,
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
