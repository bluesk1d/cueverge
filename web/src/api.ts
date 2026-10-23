export type Role = "viewer" | "designer" | "operator" | "publisher" | "administrator";

export interface Session {
  username: string;
  role: Role;
}

export interface LocalUser {
  username: string;
  role: Role;
  disabled: boolean;
  created_at: string;
}

export interface ProjectMembership {
  project_id: string;
  username: string;
  role: Role;
  can_live_output: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProjectComment {
  id: string;
  project_id: string;
  revision: number;
  object_id: string | null;
  body: string;
  state: "open" | "resolved";
  created_by: string;
  resolved_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectChangeRequest {
  id: string;
  project_id: string;
  revision: number;
  object_id: string | null;
  title: string;
  body: string;
  state: "open" | "accepted" | "closed";
  created_by: string;
  updated_by: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectPresence {
  project_id: string;
  username: string;
  location: string;
  last_seen_at: string;
}

export interface ReviewLink {
  id: string;
  project_id: string;
  revision: number;
  expires_at: string;
  created_by: string;
  created_at: string;
  token: string;
}

export type McpScope =
  | "read"
  | "draft_edit"
  | "direct_edit"
  | "simulation"
  | "publish_prepare"
  | "publish"
  | "activate"
  | "live_operation"
  | "input_config"
  | "installation_edit";

export interface InputSourceSettings {
  enabled: boolean;
  host: string;
  port: number;
  tls: boolean;
  topic: string;
  username: string | null;
  accepted_labels: string[];
}

export interface InputSourceSnapshot {
  validation_context_digest: string;
  installation_id: string | null;
  observed_at_ms: number;
  management: "gui" | "environment";
  configuration_revision: number;
  settings: InputSourceSettings;
  credential_present: boolean;
  state: "unavailable" | "disabled" | "connecting" | "subscribed" | "stopped";
  error: string | null;
  freshness: "no_events" | "fresh" | "stale";
  freshness_ms: number;
  messages_received: number;
  invalid_messages: number;
  filtered_messages: number;
  events_delivered: number;
  controller_accepted: number;
  controller_rejected: number;
  audience_matches: number;
  connection_attempts: number;
  last_received_at_ms: number | null;
  recent_events: {
    received_at_ms: number;
    occurred_at_ms?: number;
    event_type?: string;
    camera?: string;
    zone?: string;
    label?: string;
    reason?: string | null;
    accepted?: boolean;
    audience_relevant?: boolean | null;
  }[];
  loaded_rules: {
    audience: AudiencePresenceDefinition | null;
    encounter: boolean;
    opportunities: boolean;
    show_mode: string | null;
    audience_state: Record<string, unknown> | null;
  };
  recovery: string;
}

export interface InputSourceValidation {
  validation_context_digest: string;
  simulation: true;
  hardware_commands_sent: false;
  label_filtered: boolean;
  audience_configured: boolean;
  events: { event_type: string; camera: string; zone: string | null; stale: boolean; audience_relevant: boolean | null }[];
  note: string;
}

export function getInputSource(signal?: AbortSignal): Promise<InputSourceSnapshot> {
  return request("/api/gui/v1/input-source", { signal, cache: "no-store" });
}

export function configureInputSource(settings: InputSourceSettings & {
  expected_revision: number; password?: string; clear_password: boolean;
}): Promise<InputSourceSnapshot> {
  return request("/api/gui/v1/input-source", {
    method: "PUT", headers: mutationHeaders(), body: JSON.stringify(settings),
  }, true);
}

export function validateInputSourcePayload(payload: Record<string, unknown>): Promise<InputSourceValidation> {
  return request("/api/gui/v1/input-source/validate", {
    method: "POST", headers: mutationHeaders(), body: JSON.stringify(payload),
  }, true);
}

export interface McpToken {
  id: string;
  client_id: string;
  username: string;
  project_id: string;
  scopes: McpScope[];
  expires_at: string;
  revoked_at: string | null;
  created_at: string;
  token: string | null;
}

export interface McpProposal {
  id: string;
  project_id: string;
  base_revision: number;
  title: string;
  kind: string;
  changed_paths: string[];
  state: "proposed" | "validated" | "approved" | "merged" | "rejected" | "failed";
  client_id: string;
  username: string;
  validation: { code?: unknown; severity?: unknown; message?: unknown }[];
  simulation: Record<string, unknown> | null;
  approved_by: string | null;
  disposition_reason: string | null;
  created_at: string;
  updated_at: string;
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
  folder: string | null;
  tags: string[];
  favorite: boolean;
  used_by_projects: string[];
  created_at: string;
  updated_at: string;
}

export interface InstallationPhoto {
  id: string;
  installation_id: string;
  name: string;
  description?: string;
  viewpoint: string;
  original_filename: string;
  media_type: string;
  digest_sha256: string;
  size_bytes: number;
  orientation_degrees: 0 | 90 | 180 | 270;
  crop_x: number | null;
  crop_y: number | null;
  crop_width: number | null;
  crop_height: number | null;
  scale_hint: number | null;
  revision: number;
  created_at: string;
  updated_at: string;
}

export type InstallationGeometryKind =
  | "output"
  | "pixel_segment"
  | "fixture"
  | "path"
  | "projector_surface"
  | "mask"
  | "audience_zone"
  | "safety_zone";

export interface InstallationGeometry {
  id: string;
  installation_id: string;
  photo_id: string;
  kind: InstallationGeometryKind;
  name: string;
  semantic_id: string;
  landmark_ids: string[];
  color: string;
  label_visible: boolean;
  locked: boolean;
  hidden: boolean;
  z_index: number;
  direction: "forward" | "reverse";
  start_index: number | null;
  pixel_count: number | null;
  stage_points: [number, number][] | null;
  points: { x: number; y: number }[];
  closed: boolean;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface GeometrySuggestion {
  id: string;
  installation_id: string;
  photo_id: string;
  kind: InstallationGeometryKind;
  name: string;
  points: { x: number; y: number }[];
  closed: boolean;
  confidence: number;
  evidence: string;
  state: "pending" | "reviewed" | "rejected";
  created_by: string;
  reviewed_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectorMapping {
  geometry_id: string;
  resource: string;
  native_width: number;
  native_height: number;
  stage_x: number;
  stage_y: number;
  stage_width: number;
  stage_height: number;
  crop_x: number;
  crop_y: number;
  crop_width: number;
  crop_height: number;
  content_asset_id: string | null;
  surface_model: "planar" | "subdivided";
  fit_mode: "preserve" | "stretch";
  overscan: boolean;
  brightness_limit: number;
  media_start_delay_ms: number;
  media_release_delay_ms: number;
  orientation_degrees: 0 | 90 | 180 | 270;
  mirror_x: boolean;
  mirror_y: boolean;
  mask_semantic_ids: string[];
  calibration_source:
    | "measured_grid"
    | "easy_corner_pin"
    | "unverified_estimate";
  calibration_revision: number;
  mesh_vertices: [number, number, number, number][];
  mesh_triangles: [number, number, number][];
  homography: number[] | null;
  diagnostic: string | null;
  fit_diagnostics: string[];
  photo_views: {
    geometry_id: string;
    photo_id: string;
    points: { x: number; y: number }[];
    homography: number[] | null;
  }[];
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface LightingSurface {
  geometry_id: string;
  resource: string;
  local_width: number;
  local_height: number;
  local_points: [number, number][];
  stage_matrix: [number, number, number, number, number, number];
  fixture_id: string | null;
  group_id: string | null;
  path_id: string | null;
  strand_id: string | null;
  calibration_id: string | null;
  pixel_footprint_width: number;
  pixel_footprint_height: number;
  brightness_limit: number;
  timing_offset_ms: number;
  release_offset_ms: number;
  pixel_overrides: {
    output_index: number;
    local_x: number;
    local_y: number;
  }[];
  pixels: {
    output_index: number;
    local_x: number;
    local_y: number;
    footprint_width: number;
    footprint_height: number;
    stage_x: number;
    stage_y: number;
  }[];
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface ProjectorOverlap {
  id: string;
  installation_id: string;
  first_geometry_id: string;
  second_geometry_id: string;
  stage_points: [number, number][];
  ramp_width: number;
  overlap_gamma: number;
  first_brightness: number;
  second_brightness: number;
  first_white: [number, number, number];
  second_white: [number, number, number];
  first_color_matrix: number[];
  second_color_matrix: number[];
  first_black_level: number;
  second_black_level: number;
  diagnostics: string[];
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface StageCoverage {
  installation_id: string;
  minimum_x: number;
  minimum_y: number;
  maximum_x: number;
  maximum_y: number;
  regions: {
    kind: "covered" | "overlap" | "occluded" | "uncovered";
    points: [number, number][];
    resources: string[];
    source_ids: string[];
  }[];
}

export interface InstallationMapperExport {
  schema_version: 1;
  installation_id: string;
  resources: InstallationResource[];
  projectors: Record<string, unknown>[];
  lighting_surfaces: {
    semantic_id: string;
    resource: string;
    local_width: number;
    local_height: number;
    local_points: [number, number][];
    stage_matrix: [number, number, number, number, number, number];
    calibration_id: string | null;
    mapping_revision: number;
    pixels?: {
      output_index: number;
      local_x: number;
      local_y: number;
      footprint_width: number;
      footprint_height: number;
      stage_x: number;
      stage_y: number;
    }[];
  }[];
  fixtures: {
    semantic_id: string;
    name: string;
    stage_points: [number, number][];
  }[];
  paths: {
    semantic_id: string;
    name: string;
    stage_points: [number, number][];
  }[];
  zones: {
    semantic_id: string;
    name: string;
    kind: "audience_zone" | "safety_zone";
    stage_points: [number, number][];
  }[];
}

export interface ProjectSummary {
  id: string;
  name: string;
  head_revision: number;
  metadata_revision: number;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectExportDocument {
  schema_version: 1 | 2;
  name: string;
  draft: unknown;
  assets?: unknown[];
}

export interface ProgramSceneDefinition {
  scene_id: string;
  weight: number;
  cooldown_ms: number;
  available: boolean;
  conditions: Record<string, string | number | boolean>;
}

export interface ScenePoolDefinition {
  id: string;
  category: string;
  scenes: ProgramSceneDefinition[];
}

export interface ShowProgramDefinition {
  schema_version: 1;
  initial_scene_id: string;
  default_pool_id: string;
  recent_history_size: number;
  random_seed: number;
  pools: ScenePoolDefinition[];
}

export interface AudiencePresenceDefinition {
  schema_version: 1;
  labels: string[];
  cameras: string[];
  zones: string[];
  observation_freshness_ms: number;
  arrival_confirmation_ms: number;
  absence_grace_ms: number;
  long_idle_ms: number;
}

export interface OpportunityDefinition {
  id: string;
  scene_id: string;
  trigger_event: string;
  trigger_attributes: Record<string, string | number | boolean>;
  required_context: Record<string, string | number | boolean>;
  ttl_ms: number;
  event_ttl_ms: number;
  qualification_window_ms: number;
  min_presence_ms: number;
  min_absence_before_arrival_ms: number;
  acknowledgement_cooldown_ms: number;
  priority: number;
}

export interface OpportunityProgramDefinition {
  schema_version: 1;
  opportunities: OpportunityDefinition[];
}

export interface EncounterTransitionDefinition {
  event_type: string;
  from_states: string[];
  to_state: string;
  scene_id: string | null;
  min_state_age_ms: number;
  attributes: Record<string, string | number | boolean>;
}

export interface EncounterDefinition {
  schema_version: 1;
  initial_state: string;
  cooldown_state: string;
  ambient_scene_id: string;
  encounter_timeout_ms: number;
  cooldown_ms: number;
  debounce_ms: number;
  event_ttl_ms: number;
  transitions: EncounterTransitionDefinition[];
}

export interface ProjectDraftRecord {
  project: ProjectSummary;
  revision: number;
  draft: {
    schema_version?: number;
    installation?: unknown;
    program?: ShowProgramDefinition | null;
    audience?: AudiencePresenceDefinition | null;
    opportunities?: OpportunityProgramDefinition | null;
    encounter?: EncounterDefinition | null;
    editor_metadata: Record<string, unknown>;
    scenes: {
      schema_version: 1 | 2;
      id: string;
      name: string;
      duration_ms?: number;
      resources?: string[];
      priority?: number;
      interruption?: "replace_by_priority" | "queue" | "ignore";
      failure_scene_id?: string | null;
      completion?: {
        policy?: "loop" | "select_next" | "transition_to" | "hold" | "stop";
        target_scene_id?: string | null;
      };
      scheduling?: {
        safe_points_ms?: number[];
        protected_intervals?: { start_ms: number; end_ms: number }[];
        [key: string]: unknown;
      };
      media:
        | { asset: string | null }
        | { id: string; asset: string | null }[];
      lighting?: unknown;
      [key: string]: unknown;
    }[];
    [key: string]: unknown;
  };
}

export interface RevisionSummary {
  revision: number;
  parent_revision: number | null;
  actor: string;
  transaction_kind: string;
  created_at: string;
}

export interface SceneLightingPreview {
  project_id: string;
  source_revision: number;
  scene_id: string;
  timestamp_ms: number;
  duration_ms: number;
  unsaved: boolean;
  evaluation: "production_lighting_renderer";
  hardware_commands_sent: false;
  outputs: {
    output_id: string;
    resource: string;
    pixel_count: number;
    pixel_offset: number;
    output_position_ms: number;
    colors: [number, number, number][];
    next_pixel_offset: number | null;
    limitations: string[];
  }[];
}

export type LightingFrameView = Pick<SceneLightingPreview, "timestamp_ms" | "unsaved" | "outputs">;
export interface OutputMonitor {
  observed_at: string;
  source: "successful_controller_sink_frames";
  loaded_catalog: RuntimeStatus["loaded_catalog"];
  sample_interval_ms: number; maximum_resources: number; maximum_samples: number;
  truncated_resources: number; hardware_commands_sent: false;
  outputs: (SceneLightingPreview["outputs"][number] & {
    source: "scene" | "safe_state" | "blackout" | "physical_test" | "unobserved";
    scene_instance_id: string | null; age_ms: number | null; stale: boolean; held: boolean; error: string | null;
  })[];
}
export function getOutputMonitor(resource?: string, pixelOffset = 0, signal?: AbortSignal): Promise<OutputMonitor> {
  const query = new URLSearchParams({ pixel_offset: String(pixelOffset), pixel_limit: "1000" });
  if (resource) query.set("resource", resource);
  return request(`/api/gui/v1/runtime/output-monitor?${query}`, { signal, cache: "no-store" });
}

export async function previewSceneLighting(
  projectId: string,
  sourceRevision: number,
  sceneId: string,
  timestampMs: number,
  resource: string,
  proposedDraft?: ProjectDraftRecord["draft"],
  signal?: AbortSignal,
): Promise<SceneLightingPreview> {
  return request<SceneLightingPreview>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/scene-lighting-preview`,
    {
      method: "POST",
      headers: mutationHeaders(),
      signal,
      body: JSON.stringify({
        source_revision: sourceRevision, scene_id: sceneId, timestamp_ms: timestampMs,
        resource, proposed_draft: proposedDraft, pixel_limit: 4096,
      }),
    },
    true,
  );
}

export interface RevisionRelease {
  project_id: string;
  revision: number;
  name: string | null;
  summary: string | null;
  approval_state: "draft" | "in_review" | "approved" | "rejected";
  approved_by: string | null;
  approved_at: string | null;
}

export interface RevisionDiff {
  project_id: string;
  base_revision: number;
  candidate_revision: number;
  changed_paths: string[];
}

export interface PublicationValidation {
  project_id: string;
  revision: number;
  passed: boolean;
  diagnostics: {
    code: string;
    severity: "error" | "warning" | "info";
    message: string;
  }[];
}

export interface PublishedPackage {
  id: string;
  project_id: string;
  revision: number;
  digest_sha256: string;
  state: "published" | "staged" | "active" | "inactive" | "failed";
  manifest: Record<string, unknown>;
  created_by: string;
  created_at: string;
  staged_at: string | null;
  activated_at: string | null;
  deactivated_at: string | null;
}

export interface AssetRecord {
  id: string;
  project_id: string;
  name: string;
  status: "uploading" | "ready" | "failed" | "quarantined";
  current_revision: number | null;
  metadata_revision: number;
  owner: string | null;
  license: string | null;
  provenance: string | null;
  review_status: "unreviewed" | "approved" | "rejected";
  expires_on: string | null;
  folder: string | null;
  tags: string[];
  favorite: boolean;
  error: string | null;
  digest_sha256: string | null;
  size_bytes: number | null;
  media_type: string | null;
  original_filename: string | null;
  scan_status: string | null;
  metadata: Record<string, unknown> | null;
  original_file_status: "pending" | "available" | "missing" | "size_mismatch";
  artifact_faults: string[];
  references: {
    scene_id: string;
    scene_name: string;
    output_id: string | null;
  }[];
  unused: boolean;
  created_at: string;
  updated_at: string;
}

export interface AssetUploadRecord {
  id: string;
  asset_id: string;
  total_bytes: number;
  received_bytes: number;
  state: "active" | "completed" | "failed";
  error: string | null;
  chunk_max_bytes: number;
  asset: AssetRecord;
}

export interface RuntimeStatus {
  observed_at: string;
  source: "controller_runtime";
  installation_id?: string | null;
  apply_job?: { id: string; project_id: string; state: string } | null;
  input_health?: Pick<InputSourceSnapshot, "state" | "freshness" | "error" | "observed_at_ms" | "events_delivered" | "audience_matches" | "last_received_at_ms"> & { visitor_rules_loaded: boolean };
  loaded_catalog?: {
    state: "unreported" | "loaded" | "recovery_required";
    project_id: string | null;
    project_name?: string | null;
    revision: number | null;
    digest_sha256: string | null;
    loaded_at: string | null;
    generation: string | null;
    error?: string | null;
  };
  status: {
    controller_epoch: string;
    active_scene: string | null;
    scene_instance_id: string | null;
    playback_session_id: string | null;
    estimated_position_ms: number | null;
    clock_authority?: "controller" | "media" | null;
    playback_state: string | null;
    degraded: boolean;
    degraded_reason: string | null;
    connected_agents: string[];
    media_outputs: MediaOutputStatus[];
    lighting_outputs: LightingOutputStatus[];
    output_test?: {
      id: string;
      resource: string;
      state: PhysicalOutputTest["state"];
      error: string | null;
    } | null;
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
      scene_options?: { id: string; name: string; ready: boolean }[];
      starting_scene_id?: string | null;
      output_resources?: { resource: string; pixel_count: number | null }[];
      mode: string;
      show_enabled: boolean;
    };
    health: Record<string, string>;
  };
}

export interface ApplyJob {
  id: string; project_id: string; source_revision: number;
  state: "preparing" | "publishing" | "staging" | "handover" | "completed" | "failed" | "cancelled";
  package_id: string | null; previous_package_id: string | null;
  error: string | null; cancel_requested: boolean; interrupted: boolean;
  loaded: boolean; running: boolean; warnings?: string[];
  loaded_catalog?: NonNullable<RuntimeStatus["loaded_catalog"]>;
  starting_scene_id?: string | null;
  scene_options?: { id: string; name: string }[];
}
export interface ApplyInput {
  source_revision: number; expected_head_revision: number; confirm_interruption: boolean;
  approve_release: boolean; starting_scene_id?: string; restore_package_id?: string;
  preserve_controller_looks: boolean;
}
export function applySavedShow(projectId: string, input: ApplyInput): Promise<ApplyJob> {
  return request(`/api/gui/v1/projects/${encodeURIComponent(projectId)}/apply`, {
    method: "POST", headers: mutationHeaders(), body: JSON.stringify(input),
  }, true);
}
export function getApplyJobs(projectId: string, signal?: AbortSignal): Promise<ApplyJob[]> {
  return request(`/api/gui/v1/projects/${encodeURIComponent(projectId)}/apply-jobs`, { signal, cache: "no-store" });
}
export function getApplyJob(projectId: string, jobId: string, signal?: AbortSignal): Promise<ApplyJob> {
  return request(`/api/gui/v1/projects/${encodeURIComponent(projectId)}/apply-jobs/${encodeURIComponent(jobId)}`, { signal, cache: "no-store" });
}
export function cancelApply(projectId: string, jobId: string): Promise<ApplyJob> {
  return request(`/api/gui/v1/projects/${encodeURIComponent(projectId)}/apply-jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST", headers: mutationHeaders(),
  }, true);
}

export interface RenderJob {
  id: string;
  project_id: string;
  source_revision: number;
  composition_id: string;
  cache_key: string;
  state: "queued" | "running" | "completed" | "failed" | "canceled";
  progress: number;
  reserved_bytes: number;
  error: string | null;
  artifact_id: string | null;
  cancel_requested: boolean;
  created_by: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
}

export type SimulationStep =
  | { kind: "visitor"; camera: string; zone?: string; label?: string; object_id?: string; observation: "arrive" | "stay" | "leave"; age_ms?: number }
  | { kind: "advance"; delta_ms: number }
  | {
      kind: "observe";
      event_type: string;
      attributes: Record<string, string | number | boolean>;
    }
  | { kind: "operator_start" | "request_scene"; scene_id: string }
  | { kind: "disconnect" | "reconnect"; agent_id?: string | null }
  | { kind: "output_failure"; resource: string; failed: boolean };

interface InteractionCardBase {
  key: string; name: string; when: string; scene_id: string | null;
  scene_name: string | null; timing: string; afterward: string; editable: boolean; note: string;
}
export type InteractionCard =
  | (InteractionCardBase & { kind: "response"; id: string; definition: OpportunityDefinition })
  | (InteractionCardBase & { kind: "transition"; index: number; definition: EncounterTransitionDefinition });

export interface InteractionOverview {
  source_revision: number; cards: InteractionCard[]; visitors: AudiencePresenceDefinition | null;
  warnings: string[];
  normal_playback: { starting_scene_id: string | null; default_pool_id: string | null; pools: ScenePoolDefinition[] };
}

export interface GuidedResponse {
  id?: string; name: string; trigger_event: string; scene_id: string; ttl_ms: number;
  min_presence_ms: number; acknowledgement_cooldown_ms: number; priority: number;
  fallback_scene_id?: string; prepare_completion: boolean;
  qualification_context?: "preserve" | "present" | "absent" | "any";
  min_absence_before_arrival_ms?: number; qualification_window_ms?: number; event_ttl_ms?: number;
}
export interface InteractionEdit {
  source_revision: number; operation: "response" | "visitors" | "transition";
  response?: GuidedResponse; visitors?: AudiencePresenceDefinition;
  transition_index?: number; scene_id?: string | null;
}
export interface InteractionCheckpoint {
  index: number; at_ms: number; action: string;
  observations: { event_type?: string; accepted?: boolean; audience_relevant?: boolean | null; reason?: string | null; decision?: string; note?: string }[];
  scene_history: string[];
  status: {
    active_scene: string | null; estimated_position_ms: number | null;
    completion_policy: string | null;
    playback_state: string | null; degraded_reason: string | null;
    audience: { state: string; active_objects: number; relevant_observations: number } | null;
    encounter: { state: string; cooldown_until_ms: number | null; rejected_events: number; accepted_events: number } | null;
    opportunities: {
      pending: { id: string; scene_id: string; expires_at_ms: number; remaining_ms: number; priority: number }[];
      qualifying_count: number; qualifying: { id: string; scene_id: string; blocked_by: string[] }[];
      created_count: number; consumed_count: number; expired_count: number;
      qualification_expired_count: number;
    } | null;
    director: { eligible_scene_ids: string[]; active_pool_id: string; eligibility: Record<string, { available: boolean; cooldown_remaining_ms: number; conditions_match: boolean }> } | null;
  };
}
export interface InteractionPreview {
  project_id: string; source_revision: number; draft_digest: string; unsaved: boolean;
  trace: InteractionCheckpoint[]; selected_index: number;
  frame: SceneLightingPreview | null; frame_unavailable_reason: string | null;
  scene_timeline: { scene_id: string | null; at_ms: number }[];
  hardware_adapters_opened: false; hardware_commands_sent: false; limitations: string[];
}
export interface InteractionPreviewInput {
  source_revision: number; proposed_edit?: InteractionEdit;
  steps: SimulationStep[]; frame_index?: number; starting_scene_id?: string;
  resource?: string; pixel_limit?: number;
}
export function getInteractions(projectId: string, signal?: AbortSignal): Promise<InteractionOverview> {
  return request(`/api/gui/v1/projects/${encodeURIComponent(projectId)}/interactions`, { signal });
}
export function updateInteraction(projectId: string, edit: InteractionEdit): Promise<ProjectDraftRecord> {
  return request(`/api/gui/v1/projects/${encodeURIComponent(projectId)}/interactions`, {
    method: "PUT", headers: mutationHeaders(), body: JSON.stringify(edit),
  }, true);
}
export function previewInteractions(projectId: string, input: InteractionPreviewInput, signal?: AbortSignal): Promise<InteractionPreview> {
  return request(`/api/gui/v1/projects/${encodeURIComponent(projectId)}/interaction-preview`, {
    method: "POST", headers: mutationHeaders(), body: JSON.stringify(input), signal,
  }, true);
}

export interface SimulationScenario {
  id: string;
  project_id: string;
  name: string;
  source_revision: number;
  steps: SimulationStep[];
  expected: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SimulationRun {
  scenario_id: string;
  source_revision: number;
  passed: boolean;
  mismatches: unknown[];
  actual: Record<string, unknown>;
  hardware_adapters_opened: false;
  hardware_commands_sent: false;
}

export interface SyncRehearsal {
  id: string;
  resources: string[];
  start_error_ms: number;
  drift_ms_per_minute: number;
  measurement_duration_seconds: number;
  context: Record<string, unknown>;
  notes: string | null;
  measured_at: string;
  actor: string;
}

export interface SyncAdmission {
  allowed: boolean;
  code: string;
  message: string;
  rehearsal: SyncRehearsal | null;
}

export interface ArtifactDeployment {
  activation_identity: string;
  artifact_id: string;
  resources: string[];
  staged_device_ids: string[];
  activated_device_ids: string[];
}

export interface LightingPreview {
  canvas_png_base64?: string;
  composition_id: string;
  timestamp_ms: number;
  surface_semantic_id: string;
  surface_resource: string;
  sampling: "nearest" | "bilinear" | "area";
  normalized_channel_load: number;
  clipped_pixels: number;
  diagnostics: {
    mapping_revision: number;
    calibration_id: string | null;
    mapping_stale: boolean;
    unmapped_pixels: number;
    duplicate_output_indices: number[];
    device_reported_scale: number | null;
  };
  output_boundary: [number, number][];
  performance_budget: {
    profile: "interactive_30fps" | "area_preview_10fps";
    target_fps: number;
    maximum_canvas_pixels: number;
    maximum_output_pixels: number;
    maximum_author_layers: number;
    qualified: boolean;
    exceeded_dimensions: string[];
  };
  samples: {
    output_index: number;
    requested_color: [number, number, number];
    color: [number, number, number];
  }[];
  hardware_command_sent: false;
}

export interface PreviewEvaluation {
  project_id: string;
  source_revision: number;
  space_id: string;
  space_kind: "scene" | "projector_native" | "lighting_surface" | "stage";
  timestamp_ms: number;
  duration_ms: number;
  frame_rate: { numerator: number; denominator: number };
  frame_index: number;
  frame_timestamp_ms: number;
  timing_error_ms: number;
  resources: string[];
  degraded_resources: {
    source_id: string;
    reason: string;
    detail?: string | null;
  }[];
  evaluation: "backend_deterministic";
  hardware_command_sent: false;
}

export interface StageLightingPreview {
  composition_id: string;
  timestamp_ms: number;
  source: "shared_stage_compositor";
  surfaces: {
    surface_semantic_id: string;
    surface_resource: string;
    mapped_brightness_limit: number;
    device_reported_scale: number | null;
    samples: {
      output_index: number;
      requested_color: number[];
      color: number[];
    }[];
  }[];
  hardware_command_sent: false;
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

export interface TimingAdjustment {
  minimum_ms: number;
  maximum_ms: number;
  resolution_ms: number;
  adjustable_while_active: boolean;
}

export interface TimingCalibrationCapability {
  kind: "timing_calibration";
  actuator_kinds: (
    | "media"
    | "audio"
    | "pixels"
    | "network_light"
    | "smart_switch"
    | "relay"
    | "physical_effect"
  )[];
  onset: TimingAdjustment | null;
  release: TimingAdjustment | null;
  safe_test_procedure:
    | "maintenance_reference"
    | "armed_trials"
    | "simulation_only";
  supports_ab_comparison: boolean;
}

export interface DeviceCapabilityDocument {
  schema_version: 1;
  protocol: ProtocolRange;
  device_kind: "direct_media_agent" | "downstream_controller";
  software_version: string;
  capabilities: (
    | MediaPlaybackCapability
    | ArtifactCacheCapability
    | TimingCalibrationCapability
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

export interface OutputArm {
  id: string;
  resource: string;
  resource_kind: string;
  device_identity: string;
  stop_action: string;
  state: "active" | "stopped" | "expired";
  expires_at: string;
  hardware_command_sent: boolean;
}

export interface PhysicalOutputTest {
  id: string;
  arm_id: string;
  resource: string;
  pixel_count: number;
  speed: number;
  state: "active" | "stopped" | "completed" | "failed";
  pixel_index: number;
  frames_sent: number;
  started_at: string;
  expires_at: string;
  error: string | null;
  stop_reason: string | null;
  simulation_only: false;
  segments: { name: string; start: number; end: number; color: [number, number, number] }[];
}

export interface OutputTestOverview {
  mode: string;
  playback_active: boolean;
  outputs: InstallationResource[];
  test: PhysicalOutputTest | null;
}

export function getOutputTestOverview(): Promise<OutputTestOverview> {
  return request<OutputTestOverview>("/api/gui/v1/maintenance/output-tests");
}

export function startPhysicalOutputTest(body: {
  resource: string;
  confirmation: string;
  duration_seconds: number;
  speed: number;
  use_mapping: boolean;
  installation_id?: string;
}): Promise<PhysicalOutputTest> {
  return request<PhysicalOutputTest>("/api/gui/v1/maintenance/output-tests", {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify(body),
  }, true);
}

export function stopPhysicalOutputTest(id: string): Promise<PhysicalOutputTest> {
  return request<PhysicalOutputTest>(
    `/api/gui/v1/maintenance/output-tests/${encodeURIComponent(id)}/stop`,
    { method: "POST", headers: mutationHeaders() }, true,
  );
}

export interface TimingCalibrationSession {
  id: string;
  target_kind: "projector_mapping" | "lighting_surface";
  geometry_id: string;
  adjustment_kind: "onset" | "release";
  baseline_ms: number;
  proposed_ms: number;
  state: "active" | "accepted" | "cancelled" | "expired";
  revision: number;
  simulation_only: true;
  expires_at: string;
}

export interface AcceptedCalibration {
  id: string;
  target_kind: TimingCalibrationSession["target_kind"];
  geometry_id: string;
  adjustment_kind: TimingCalibrationSession["adjustment_kind"];
  accepted_ms: number;
  resource: string;
  device_id: string | null;
  software_version: string | null;
  test_asset_id: string | null;
  test_asset_version: string | null;
  observed_variability_ms: number | null;
  notes: string | null;
  context: Record<string, unknown>;
  actor: string;
  accepted_at: string;
  stale_reasons: string[];
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

export async function getInstallation(signal?: AbortSignal): Promise<InstallationStatus> {
  return request<InstallationStatus>("/api/gui/v1/installation", { signal, cache: "no-store" });
}

export async function getInstallations(signal?: AbortSignal): Promise<InstallationSummary[]> {
  return request<InstallationSummary[]>("/api/gui/v1/installations", { signal, cache: "no-store" });
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
  folder: string | null,
  tags: string[],
  favorite: boolean,
): Promise<InstallationSummary> {
  return request<InstallationSummary>(
    `/api/gui/v1/installations/${encodeURIComponent(installationId)}`,
    {
      method: "PUT",
      headers: { ...mutationHeaders(), "If-Match": `"${revision}"` },
      body: JSON.stringify({ name, folder, tags, favorite }),
    },
  );
}

export async function organizeScene(
  projectId: string,
  revision: number,
  sceneId: string,
  organization: { folder: string | null; tags: string[]; favorite: boolean },
): Promise<ProjectDraftRecord> {
  return request<ProjectDraftRecord>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/scenes/${encodeURIComponent(sceneId)}/organization`,
    {
      method: "PUT",
      headers: { ...mutationHeaders(), "If-Match": `"${revision}"` },
      body: JSON.stringify(organization),
    },
  );
}

export async function createScenePreset(
  projectId: string,
  revision: number,
  values: {
    scene_id: string;
    name: string;
    folder: string | null;
    tags: string[];
    favorite: boolean;
  },
): Promise<ProjectDraftRecord> {
  return request<ProjectDraftRecord>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/presets`,
    {
      method: "POST",
      headers: { ...mutationHeaders(), "If-Match": `"${revision}"` },
      body: JSON.stringify(values),
    },
  );
}

export async function applyScenePreset(
  projectId: string,
  revision: number,
  presetId: string,
  sceneId: string,
): Promise<ProjectDraftRecord> {
  return request<ProjectDraftRecord>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/presets/${encodeURIComponent(presetId)}/apply`,
    {
      method: "POST",
      headers: { ...mutationHeaders(), "If-Match": `"${revision}"` },
      body: JSON.stringify({ scene_id: sceneId }),
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

export async function getInstallationPhotos(
  installationId: string,
): Promise<InstallationPhoto[]> {
  return request<InstallationPhoto[]>(
    `/api/gui/v1/installations/${encodeURIComponent(installationId)}/photos`,
  );
}

export function installationPhotoUrl(photoId: string): string {
  return `/api/gui/v1/installation-photos/${encodeURIComponent(photoId)}/content`;
}

export async function uploadInstallationPhoto(
  installationId: string,
  file: File,
  name: string,
  viewpoint?: string,
): Promise<InstallationPhoto> {
  return request<InstallationPhoto>(
    `/api/gui/v1/installations/${encodeURIComponent(installationId)}/photos`,
    {
      method: "POST",
      headers: {
        ...mutationHeaders(),
        "Content-Type": file.type,
        "X-Photo-Description": encodeURIComponent(name),
        "X-Photo-Metadata-Encoding": "uri",
        ...(viewpoint === undefined ? {} : { "X-Photo-Viewpoint": encodeURIComponent(viewpoint) }),
        "X-Original-Filename": encodeURIComponent(file.name),
      },
      body: file,
    },
  );
}

export function describeInstallationPhoto(photo: InstallationPhoto, description: string): Promise<InstallationPhoto> {
  return request(`/api/gui/v1/installation-photos/${encodeURIComponent(photo.id)}/description`, {
    method: "PATCH", headers: { ...mutationHeaders(), "If-Match": `"${photo.revision}"` },
    body: JSON.stringify({ description }),
  }, true);
}

export async function updateInstallationPhoto(
  photo: InstallationPhoto,
  values: Pick<
    InstallationPhoto,
    | "name"
    | "viewpoint"
    | "orientation_degrees"
    | "crop_x"
    | "crop_y"
    | "crop_width"
    | "crop_height"
    | "scale_hint"
  >,
): Promise<InstallationPhoto> {
  return request<InstallationPhoto>(
    `/api/gui/v1/installation-photos/${encodeURIComponent(photo.id)}`,
    {
      method: "PUT",
      headers: { ...mutationHeaders(), "If-Match": `"${photo.revision}"` },
      body: JSON.stringify(values),
    },
  );
}

export async function deleteInstallationPhoto(photo: InstallationPhoto): Promise<void> {
  await request(`/api/gui/v1/installation-photos/${encodeURIComponent(photo.id)}`, {
    method: "DELETE",
    headers: { ...mutationHeaders(), "If-Match": `"${photo.revision}"` },
  });
}

export async function getInstallationGeometry(
  installationId: string,
  photoId?: string,
): Promise<InstallationGeometry[]> {
  const query = photoId ? `?photo_id=${encodeURIComponent(photoId)}` : "";
  return request<InstallationGeometry[]>(
    `/api/gui/v1/installations/${encodeURIComponent(installationId)}/geometry${query}`,
  );
}

export async function saveInstallationGeometry(
  installationId: string,
  geometry: Omit<
    InstallationGeometry,
    | "id"
    | "installation_id"
    | "landmark_ids"
    | "semantic_id"
    | "revision"
    | "created_at"
    | "updated_at"
  > & { semantic_id: string | null },
  existing?: InstallationGeometry,
): Promise<InstallationGeometry> {
  const path = existing
    ? `/api/gui/v1/installation-geometry/${encodeURIComponent(existing.id)}`
    : `/api/gui/v1/installations/${encodeURIComponent(installationId)}/geometry`;
  return request<InstallationGeometry>(path, {
    method: existing ? "PUT" : "POST",
    headers: existing
      ? { ...mutationHeaders(), "If-Match": `"${existing.revision}"` }
      : mutationHeaders(),
    body: JSON.stringify(geometry),
  });
}

export async function deleteInstallationGeometry(
  geometry: InstallationGeometry,
): Promise<void> {
  await request(
    `/api/gui/v1/installation-geometry/${encodeURIComponent(geometry.id)}`,
    {
      method: "DELETE",
      headers: { ...mutationHeaders(), "If-Match": `"${geometry.revision}"` },
    },
  );
}

export async function getGeometrySuggestions(
  installationId: string,
): Promise<GeometrySuggestion[]> {
  return request<GeometrySuggestion[]>(
    `/api/gui/v1/installations/${encodeURIComponent(installationId)}/geometry-suggestions`,
  );
}

export async function reviewGeometrySuggestion(
  suggestionId: string,
  action: "review" | "reject",
): Promise<GeometrySuggestion> {
  return request<GeometrySuggestion>(
    `/api/gui/v1/geometry-suggestions/${encodeURIComponent(suggestionId)}/${action}`,
    { method: "POST", headers: mutationHeaders() },
  );
}

export async function getProjectorMapping(
  geometryId: string,
): Promise<ProjectorMapping> {
  return request<ProjectorMapping>(
    `/api/gui/v1/installation-geometry/${encodeURIComponent(geometryId)}/projector-mapping`,
  );
}

export function projectorSimulationOverlayUrl(
  geometryId: string,
  photoId: string,
): string {
  return (
    `/api/gui/v1/installation-geometry/${encodeURIComponent(geometryId)}` +
    `/projector-simulation-overlay?photo_id=${encodeURIComponent(photoId)}`
  );
}

export async function getRenderJobs(projectId: string): Promise<RenderJob[]> {
  return request<RenderJob[]>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/render-jobs`,
  );
}

export async function getSimulationScenarios(
  projectId: string,
): Promise<SimulationScenario[]> {
  return request<SimulationScenario[]>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/simulation-scenarios`,
  );
}

export async function saveSimulationScenario(
  projectId: string,
  values: Pick<SimulationScenario, "name" | "steps" | "expected">,
): Promise<SimulationScenario> {
  return request<SimulationScenario>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/simulation-scenarios`,
    {
      method: "POST",
      headers: mutationHeaders(),
      body: JSON.stringify(values),
    },
  );
}

export async function runSimulationScenario(
  projectId: string,
  scenarioId: string,
): Promise<SimulationRun> {
  return request<SimulationRun>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/simulation-scenarios/` +
      `${encodeURIComponent(scenarioId)}/run`,
    { method: "POST", headers: mutationHeaders() },
  );
}

export async function createRenderJob(
  projectId: string,
  compositionId: string,
): Promise<RenderJob> {
  return request<RenderJob>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/render-jobs`,
    {
      method: "POST",
      headers: mutationHeaders(),
      body: JSON.stringify({ composition_id: compositionId }),
    },
  );
}

export async function cancelRenderJob(jobId: string): Promise<RenderJob> {
  return request<RenderJob>(
    `/api/gui/v1/render-jobs/${encodeURIComponent(jobId)}/cancel`,
    {
      method: "POST",
      headers: mutationHeaders(),
    },
  );
}

export function compositionPreviewFrameUrl(
  projectId: string,
  compositionId: string,
  timestampMs: number,
  resource?: string,
): string {
  const base =
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/compositions/` +
    `${encodeURIComponent(compositionId)}/preview-frame?timestamp_ms=${timestampMs}`;
  return resource
    ? `${base}&resource=${encodeURIComponent(resource)}`
    : base;
}

export function renderArtifactPreviewFrameUrl(
  artifactId: string,
  resource: string,
  timestampMs: number,
): string {
  return (
    `/api/gui/v1/render-artifacts/${encodeURIComponent(artifactId)}` +
    `/preview-frame?resource=${encodeURIComponent(resource)}` +
    `&timestamp_ms=${timestampMs}`
  );
}

export async function recordSyncRehearsal(values: {
  resources: string[];
  start_error_ms: number;
  drift_ms_per_minute: number;
  measurement_duration_seconds: number;
  notes: string | null;
  measured_at: string;
}): Promise<SyncRehearsal> {
  return request<SyncRehearsal>("/api/gui/v1/projector-sync-rehearsals", {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify(values),
  });
}

export async function evaluateSyncAdmission(
  resources: string[],
): Promise<SyncAdmission> {
  return request<SyncAdmission>(
    "/api/gui/v1/projector-sync-rehearsals/evaluate",
    {
      method: "POST",
      headers: mutationHeaders(),
      body: JSON.stringify({ resources }),
    },
  );
}

export async function deployRenderArtifact(
  artifactId: string,
): Promise<ArtifactDeployment> {
  return request<ArtifactDeployment>(
    `/api/gui/v1/render-artifacts/${encodeURIComponent(artifactId)}/deploy`,
    {
      method: "POST",
      headers: mutationHeaders(),
    },
  );
}

export async function getLightingPreview(
  projectId: string,
  compositionId: string,
  timestampMs: number,
): Promise<LightingPreview> {
  return request<LightingPreview>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/compositions/` +
      `${encodeURIComponent(compositionId)}/lighting-preview?timestamp_ms=${timestampMs}&include_canvas=true`,
  );
}

export async function getPreviewEvaluation(
  projectId: string,
  spaceId: string,
  timestampMs: number,
): Promise<PreviewEvaluation> {
  return request<PreviewEvaluation>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/preview-evaluation` +
      `?space_id=${encodeURIComponent(spaceId)}&timestamp_ms=${timestampMs}`,
  );
}

export async function getStageLightingPreview(
  projectId: string,
  compositionId: string,
  timestampMs: number,
): Promise<StageLightingPreview> {
  return request<StageLightingPreview>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/compositions/` +
      `${encodeURIComponent(compositionId)}/stage-lighting-preview` +
      `?timestamp_ms=${timestampMs}`,
  );
}

export async function saveProjectorMapping(
  geometryId: string,
  values: Omit<
    ProjectorMapping,
    | "geometry_id"
    | "homography"
    | "diagnostic"
    | "fit_diagnostics"
    | "photo_views"
    | "revision"
    | "created_at"
    | "updated_at"
  >,
  revision?: number,
): Promise<ProjectorMapping> {
  return request<ProjectorMapping>(
    `/api/gui/v1/installation-geometry/${encodeURIComponent(geometryId)}/projector-mapping`,
    {
      method: "PUT",
      headers:
        revision === undefined
          ? mutationHeaders()
          : { ...mutationHeaders(), "If-Match": `"${revision}"` },
      body: JSON.stringify(values),
    },
  );
}

export async function getLightingSurface(
  geometryId: string,
): Promise<LightingSurface> {
  return request<LightingSurface>(
    `/api/gui/v1/installation-geometry/${encodeURIComponent(geometryId)}/lighting-surface`,
  );
}

export async function saveLightingSurface(
  geometryId: string,
  values: Omit<
    LightingSurface,
    "geometry_id" | "pixels" | "revision" | "created_at" | "updated_at"
  >,
  revision?: number,
): Promise<LightingSurface> {
  return request<LightingSurface>(
    `/api/gui/v1/installation-geometry/${encodeURIComponent(geometryId)}/lighting-surface`,
    {
      method: "PUT",
      headers:
        revision === undefined
          ? mutationHeaders()
          : { ...mutationHeaders(), "If-Match": `"${revision}"` },
      body: JSON.stringify(values),
    },
  );
}

export async function getProjectorOverlaps(
  installationId: string,
): Promise<ProjectorOverlap[]> {
  return request<ProjectorOverlap[]>(
    `/api/gui/v1/installations/${encodeURIComponent(installationId)}/projector-overlaps`,
  );
}

export async function saveProjectorOverlap(
  installationId: string,
  values: Omit<
    ProjectorOverlap,
    | "id"
    | "installation_id"
    | "diagnostics"
    | "revision"
    | "created_at"
    | "updated_at"
  > & { id?: string },
  revision?: number,
): Promise<ProjectorOverlap> {
  return request<ProjectorOverlap>(
    `/api/gui/v1/installations/${encodeURIComponent(installationId)}/projector-overlaps`,
    {
      method: "PUT",
      headers:
        revision === undefined
          ? mutationHeaders()
          : { ...mutationHeaders(), "If-Match": `"${revision}"` },
      body: JSON.stringify(values),
    },
  );
}

export async function getStageCoverage(
  installationId: string,
): Promise<StageCoverage> {
  return request<StageCoverage>(
    `/api/gui/v1/installations/${encodeURIComponent(installationId)}/stage-coverage`,
  );
}

export async function getInstallationMapperExport(
  installationId: string,
): Promise<InstallationMapperExport> {
  return request<InstallationMapperExport>(
    `/api/gui/v1/installations/${encodeURIComponent(installationId)}/mapper-export`,
  );
}

export async function getProjects(): Promise<ProjectSummary[]> {
  return request<ProjectSummary[]>("/api/gui/v1/projects");
}

export async function getProjectDraft(projectId: string): Promise<ProjectDraftRecord> {
  return request<ProjectDraftRecord>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/draft`,
  );
}

export async function listProjectRevisions(
  projectId: string,
): Promise<RevisionSummary[]> {
  return request<RevisionSummary[]>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/revisions`,
  );
}

export async function getRevisionRelease(
  projectId: string,
  revision: number,
): Promise<RevisionRelease> {
  return request<RevisionRelease>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/revisions/${revision}/release`,
  );
}

export async function updateRevisionRelease(
  projectId: string,
  revision: number,
  release: Pick<RevisionRelease, "name" | "summary" | "approval_state">,
): Promise<RevisionRelease> {
  return request<RevisionRelease>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/revisions/${revision}/release`,
    { method: "PUT", body: JSON.stringify(release) },
  );
}

export async function compareProjectRevisions(
  projectId: string,
  baseRevision: number,
  candidateRevision: number,
): Promise<RevisionDiff> {
  const query = new URLSearchParams({
    base_revision: String(baseRevision),
    candidate_revision: String(candidateRevision),
  });
  return request<RevisionDiff>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/revision-diff?${query}`,
  );
}

export async function validatePublication(
  projectId: string,
  revision: number,
): Promise<PublicationValidation> {
  return request<PublicationValidation>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/revisions/${revision}/publication-validation`,
  );
}

export async function listPublishedPackages(
  projectId: string,
): Promise<PublishedPackage[]> {
  return request<PublishedPackage[]>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/published-packages`,
  );
}

export async function publishRevision(
  projectId: string,
  revision: number,
): Promise<PublishedPackage> {
  return request<PublishedPackage>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/revisions/${revision}/publish`,
    { method: "POST" },
  );
}

export async function transitionPublishedPackage(
  packageId: string,
  action: "stage" | "activate" | "deactivate" | "rollback",
  simulationMode = false,
): Promise<PublishedPackage> {
  return request<PublishedPackage>(
    `/api/gui/v1/published-packages/${encodeURIComponent(packageId)}/${action}`,
    {
      method: "POST",
      body: JSON.stringify({ simulation_mode: simulationMode }),
    },
  );
}

export async function updateProjectDraft(
  projectId: string,
  revision: number,
  draft: ProjectDraftRecord["draft"],
): Promise<ProjectDraftRecord> {
  return request<ProjectDraftRecord>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/draft`,
    {
      method: "PUT",
      headers: { ...mutationHeaders(), "If-Match": `"${revision}"` },
      body: JSON.stringify(draft),
    },
  );
}

export async function createProjectFromActive(name: string): Promise<ProjectDraftRecord> {
  return request<ProjectDraftRecord>("/api/gui/v1/projects/from-active", {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify({ name }),
  });
}

export async function renameProject(
  projectId: string,
  metadataRevision: number,
  name: string,
): Promise<void> {
  await request(`/api/gui/v1/projects/${encodeURIComponent(projectId)}`, {
    method: "PUT",
    headers: { ...mutationHeaders(), "If-Match": `"${metadataRevision}"` },
    body: JSON.stringify({ name }),
  });
}

export async function duplicateProject(projectId: string, name: string): Promise<void> {
  await request(`/api/gui/v1/projects/${encodeURIComponent(projectId)}/duplicate`, {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify({ name }),
  });
}

export async function archiveProject(
  projectId: string,
  metadataRevision: number,
): Promise<void> {
  await request(`/api/gui/v1/projects/${encodeURIComponent(projectId)}/archive`, {
    method: "POST",
    headers: { ...mutationHeaders(), "If-Match": `"${metadataRevision}"` },
  });
}

export async function exportProject(projectId: string): Promise<ProjectExportDocument> {
  return request<ProjectExportDocument>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/export`,
  );
}

export async function importProject(document: ProjectExportDocument): Promise<void> {
  await request("/api/gui/v1/projects/import", {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify(document),
  });
}

export async function getAssets(projectId: string): Promise<AssetRecord[]> {
  return request<AssetRecord[]>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/assets`,
  );
}

export async function getImageAssetLibrary(): Promise<AssetRecord[]> {
  return request<AssetRecord[]>("/api/gui/v1/assets/image-library");
}

export function assetArtifactUrl(
  assetId: string,
  kind: "review" | "thumbnail" | "waveform",
): string {
  return `/api/gui/v1/assets/${encodeURIComponent(assetId)}/artifacts/${kind}`;
}

export async function updateAssetMetadata(
  asset: AssetRecord,
  values: Pick<
    AssetRecord,
    | "owner"
    | "license"
    | "provenance"
    | "review_status"
    | "expires_on"
    | "folder"
    | "tags"
    | "favorite"
  >,
): Promise<AssetRecord> {
  return request<AssetRecord>(
    `/api/gui/v1/assets/${encodeURIComponent(asset.id)}/metadata`,
    {
      method: "PUT",
      headers: { ...mutationHeaders(), "If-Match": `"${asset.metadata_revision}"` },
      body: JSON.stringify(values),
    },
  );
}

export async function placeAsset(
  projectId: string,
  projectRevision: number,
  assetId: string,
  sceneId: string,
  outputId: string | null,
): Promise<ProjectDraftRecord> {
  return request<ProjectDraftRecord>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(assetId)}/place`,
    {
      method: "POST",
      headers: { ...mutationHeaders(), "If-Match": `"${projectRevision}"` },
      body: JSON.stringify({ scene_id: sceneId, output_id: outputId }),
    },
  );
}

export async function uploadAsset(
  projectId: string,
  file: File,
  onProgress: (receivedBytes: number) => void,
): Promise<AssetUploadRecord> {
  let upload = await request<AssetUploadRecord>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/assets/uploads`,
    {
      method: "POST",
      headers: mutationHeaders(),
      body: JSON.stringify({
        name: file.name.replace(/\.[^.]+$/, "") || file.name,
        original_filename: file.name,
        media_type: file.type || "application/octet-stream",
        total_bytes: file.size,
        expected_sha256: null,
      }),
    },
  );
  while (upload.received_bytes < file.size) {
    const chunk = file.slice(
      upload.received_bytes,
      Math.min(file.size, upload.received_bytes + upload.chunk_max_bytes),
    );
    upload = await request<AssetUploadRecord>(
      `/api/gui/v1/assets/uploads/${encodeURIComponent(upload.id)}`,
      {
        method: "PUT",
        headers: {
          "Content-Type": "application/octet-stream",
          "Upload-Offset": String(upload.received_bytes),
          "X-CSRF-Token": readCookie("cueverge_csrf") ?? "",
        },
        body: chunk,
      },
    );
    onProgress(upload.received_bytes);
  }
  return upload;
}

export async function getRuntimeStatus(signal?: AbortSignal): Promise<RuntimeStatus> {
  return request<RuntimeStatus>("/api/gui/v1/runtime/status", { signal, cache: "no-store" });
}

export async function getTopology(signal?: AbortSignal): Promise<Topology> {
  return request<Topology>("/api/gui/v1/topology", { signal, cache: "no-store" });
}

export async function getResourceAssignments(signal?: AbortSignal): Promise<ResourceAssignment[]> {
  return request<ResourceAssignment[]>("/api/gui/v1/resource-assignments", { signal });
}

export async function getActivity(signal?: AbortSignal): Promise<ActivityEvent[]> {
  return request<ActivityEvent[]>("/api/gui/v1/activity", { signal, cache: "no-store" });
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
  sceneId?: string,
): Promise<void> {
  await request("/api/gui/v1/operate/actions", {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify({
      action,
      ...(positionMs === undefined ? {} : { position_ms: positionMs }),
      ...(sceneId === undefined ? {} : { scene_id: sceneId }),
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

export async function createOutputArm(
  resource: string,
  confirmation: string,
  durationSeconds: number,
): Promise<OutputArm> {
  return request<OutputArm>("/api/gui/v1/maintenance/output-arms", {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify({
      resource,
      confirmation,
      duration_seconds: durationSeconds,
    }),
  });
}

export async function stopOutputArm(armId: string): Promise<OutputArm> {
  return request<OutputArm>(
    `/api/gui/v1/maintenance/output-arms/${encodeURIComponent(armId)}/stop`,
    { method: "POST", headers: mutationHeaders() },
  );
}

export async function startTimingCalibration(
  targetKind: TimingCalibrationSession["target_kind"],
  geometryId: string,
  adjustmentKind: TimingCalibrationSession["adjustment_kind"],
  provenance: {
    test_asset_id: string | null;
    test_asset_version: string | null;
    observed_variability_ms: number | null;
    notes: string | null;
    context: Record<string, unknown>;
  },
): Promise<TimingCalibrationSession> {
  return request<TimingCalibrationSession>("/api/gui/v1/timing-calibrations", {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify({
      target_kind: targetKind,
      geometry_id: geometryId,
      adjustment_kind: adjustmentKind,
      ...provenance,
    }),
  });
}

export async function reviewAcceptedCalibrations(
  targetKind: TimingCalibrationSession["target_kind"],
  geometryId: string,
  context: Record<string, unknown>,
): Promise<AcceptedCalibration[]> {
  return request<AcceptedCalibration[]>("/api/gui/v1/accepted-calibrations/review", {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify({
      target_kind: targetKind,
      geometry_id: geometryId,
      context,
    }),
  });
}

export async function adjustTimingCalibration(
  session: TimingCalibrationSession,
  proposedMs: number,
): Promise<TimingCalibrationSession> {
  return request<TimingCalibrationSession>(
    `/api/gui/v1/timing-calibrations/${encodeURIComponent(session.id)}`,
    {
      method: "PUT",
      headers: { ...mutationHeaders(), "If-Match": `"${session.revision}"` },
      body: JSON.stringify({ proposed_ms: proposedMs }),
    },
  );
}

export async function finishTimingCalibration(
  session: TimingCalibrationSession,
  action: "accept" | "cancel",
): Promise<TimingCalibrationSession> {
  return request<TimingCalibrationSession>(
    `/api/gui/v1/timing-calibrations/${encodeURIComponent(session.id)}/${action}`,
    {
      method: "POST",
      headers: { ...mutationHeaders(), "If-Match": `"${session.revision}"` },
    },
  );
}

export async function getLocalUsers(): Promise<LocalUser[]> {
  return request<LocalUser[]>("/api/gui/v1/users");
}

export async function putLocalUser(
  username: string,
  password: string,
  role: Role,
  disabled = false,
): Promise<LocalUser> {
  return request<LocalUser>(`/api/gui/v1/users/${encodeURIComponent(username)}`, {
    method: "PUT",
    headers: mutationHeaders(),
    body: JSON.stringify({ username, password, role, disabled }),
  });
}

export async function getProjectMembers(
  projectId: string,
): Promise<ProjectMembership[]> {
  return request<ProjectMembership[]>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/members`,
  );
}

export async function putProjectMember(
  projectId: string,
  username: string,
  role: Role,
  canLiveOutput: boolean,
): Promise<ProjectMembership> {
  return request<ProjectMembership>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(username)}`,
    {
      method: "PUT",
      headers: mutationHeaders(),
      body: JSON.stringify({ role, can_live_output: canLiveOutput }),
    },
  );
}

export async function getProjectComments(
  projectId: string,
): Promise<ProjectComment[]> {
  return request<ProjectComment[]>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/comments`,
  );
}

export async function createProjectComment(
  projectId: string,
  revision: number,
  body: string,
  objectId?: string,
): Promise<ProjectComment> {
  return request<ProjectComment>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/comments`,
    {
      method: "POST",
      headers: mutationHeaders(),
      body: JSON.stringify({
        revision,
        object_id: objectId || null,
        body,
      }),
    },
  );
}

export async function updateProjectComment(
  projectId: string,
  commentId: string,
  state: ProjectComment["state"],
): Promise<ProjectComment> {
  return request<ProjectComment>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/comments/${encodeURIComponent(commentId)}`,
    {
      method: "PUT",
      headers: mutationHeaders(),
      body: JSON.stringify({ state }),
    },
  );
}

export async function getProjectChangeRequests(
  projectId: string,
): Promise<ProjectChangeRequest[]> {
  return request<ProjectChangeRequest[]>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/change-requests`,
  );
}

export async function createProjectChangeRequest(
  projectId: string,
  revision: number,
  title: string,
  body: string,
  objectId?: string,
): Promise<ProjectChangeRequest> {
  return request<ProjectChangeRequest>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/change-requests`,
    {
      method: "POST",
      headers: mutationHeaders(),
      body: JSON.stringify({
        revision,
        object_id: objectId || null,
        title,
        body,
      }),
    },
  );
}

export async function updateProjectChangeRequest(
  projectId: string,
  requestId: string,
  state: ProjectChangeRequest["state"],
): Promise<ProjectChangeRequest> {
  return request<ProjectChangeRequest>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/change-requests/${encodeURIComponent(requestId)}`,
    {
      method: "PUT",
      headers: mutationHeaders(),
      body: JSON.stringify({ state }),
    },
  );
}

export async function createReviewLink(
  projectId: string,
  revision: number,
  expiresAt: string,
): Promise<ReviewLink> {
  return request<ReviewLink>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/review-links`,
    {
      method: "POST",
      headers: mutationHeaders(),
      body: JSON.stringify({ revision, expires_at: expiresAt }),
    },
  );
}

export async function heartbeatProjectPresence(
  projectId: string,
  location: string,
): Promise<void> {
  return request<void>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/presence`,
    {
      method: "POST",
      headers: mutationHeaders(),
      body: JSON.stringify({ location }),
    },
  );
}

export async function getProjectPresence(
  projectId: string,
): Promise<ProjectPresence[]> {
  return request<ProjectPresence[]>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/presence`,
  );
}

export async function getMcpTokens(projectId: string): Promise<McpToken[]> {
  return request<McpToken[]>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/mcp-tokens`,
  );
}

export async function createMcpToken(
  projectId: string,
  clientId: string,
  scopes: McpScope[],
  expiresAt: string,
): Promise<McpToken> {
  return request<McpToken>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/mcp-tokens`,
    {
      method: "POST",
      headers: mutationHeaders(),
      body: JSON.stringify({
        client_id: clientId,
        scopes,
        expires_at: expiresAt,
      }),
    },
  );
}

export async function revokeMcpToken(
  projectId: string,
  tokenId: string,
): Promise<void> {
  await request<void>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/mcp-tokens/${encodeURIComponent(tokenId)}`,
    { method: "DELETE", headers: mutationHeaders() },
  );
}

export async function getMcpProposals(
  projectId: string,
): Promise<McpProposal[]> {
  return request<McpProposal[]>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/mcp-proposals`,
  );
}

async function transitionMcpProposal(
  projectId: string,
  proposalId: string,
  action: "validate" | "approve" | "reject" | "merge",
  reason?: string,
): Promise<McpProposal> {
  return request<McpProposal>(
    `/api/gui/v1/projects/${encodeURIComponent(projectId)}/mcp-proposals/${encodeURIComponent(proposalId)}/${action}`,
    {
      method: "POST",
      headers: mutationHeaders(),
      body:
        action === "approve" || action === "reject"
          ? JSON.stringify({ reason })
          : undefined,
    },
  );
}

export const validateMcpProposal = (projectId: string, proposalId: string) =>
  transitionMcpProposal(projectId, proposalId, "validate");

export const approveMcpProposal = (
  projectId: string,
  proposalId: string,
  reason: string,
) => transitionMcpProposal(projectId, proposalId, "approve", reason);

export const rejectMcpProposal = (
  projectId: string,
  proposalId: string,
  reason: string,
) => transitionMcpProposal(projectId, proposalId, "reject", reason);

export const mergeMcpProposal = (projectId: string, proposalId: string) =>
  transitionMcpProposal(projectId, proposalId, "merge");

async function request<T>(
  path: string, init?: RequestInit, includeErrorDetail = false,
): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let message = `Cueverge API request failed (${response.status})`;
    if (includeErrorDetail && response.headers.get("content-type")?.includes("application/json")) {
      const payload: unknown = await response.json();
      if (payload && typeof payload === "object" && "detail" in payload &&
          payload.detail && typeof payload.detail === "object") {
        if ("message" in payload.detail && typeof payload.detail.message === "string") {
          message += `: ${payload.detail.message}`;
        } else if ("code" in payload.detail && typeof payload.detail.code === "string") {
          message += `: ${payload.detail.code}`;
        }
      }
    }
    throw new ApiError(response.status, message);
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

export async function reportBrowserError(
  message: string,
  source: string,
  occurredAt = new Date().toISOString(),
): Promise<void> {
  await request<void>("/api/gui/v1/browser-errors", {
    method: "POST",
    headers: mutationHeaders(),
    keepalive: true,
    body: JSON.stringify({
      message: message.slice(0, 2000),
      source: source.slice(0, 500),
      occurred_at: occurredAt,
    }),
  });
}
