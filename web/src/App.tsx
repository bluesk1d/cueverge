import { FormEvent, useCallback, useEffect, useState } from "react";

import {
  ApiError,
  ActivityEvent,
  createInstallation,
  deleteInstallation,
  getActivity,
  getInstallation,
  getInstallations,
  getProjects,
  getResourceAssignments,
  getRuntimeStatus,
  getSession,
  getTopology,
  InstallationStatus,
  InstallationSummary,
  login,
  logout,
  MaintenancePreview,
  operate,
  OperateAction,
  ProjectSummary,
  ResourceAssignment,
  RuntimeStatus,
  Session,
  startMaintenancePreview,
  Topology,
  updateInstallation,
} from "./api";
import { AppShell, Workspace } from "./components/AppShell";
import {
  EmptyState,
  ErrorState,
  Panel,
  Skeleton,
  StatusText,
  WorkspaceHeader,
} from "./components/ui";
import { OverviewPage } from "./pages/OverviewPage";
import { InstallationPage } from "./pages/InstallationPage";

export function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [checkingSession, setCheckingSession] = useState(true);

  useEffect(() => {
    getSession()
      .then(setSession)
      .catch((error: unknown) => {
        if (!(error instanceof ApiError && error.status === 401)) {
          console.error(error);
        }
      })
      .finally(() => setCheckingSession(false));
  }, []);

  if (checkingSession) {
    return (
      <main className="centered">
        <div className="boot-status">
          <span className="brand__mark" aria-hidden="true" />
          <strong>Loading Cueverge</strong>
        </div>
      </main>
    );
  }
  if (session === null) {
    return <Login onAuthenticated={setSession} />;
  }

  return <AuthenticatedApp session={session} onSignedOut={() => setSession(null)} />;
}

function AuthenticatedApp({
  session,
  onSignedOut,
}: {
  session: Session;
  onSignedOut: () => void;
}) {
  const [workspace, setWorkspace] = useState<Workspace>("overview");
  const [installation, setInstallation] = useState<InstallationStatus | null>(null);
  const [installations, setInstallations] = useState<InstallationSummary[]>([]);
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [topology, setTopology] = useState<Topology | null>(null);
  const [assignments, setAssignments] = useState<ResourceAssignment[]>([]);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [installationError, setInstallationError] = useState(false);
  const [runtimeError, setRuntimeError] = useState(false);
  const [topologyError, setTopologyError] = useState(false);
  const [activityError, setActivityError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const loadStatus = useCallback(async (refresh = false) => {
    if (refresh) {
      setRefreshing(true);
    }
    const [
      installationResult,
      installationsResult,
      runtimeResult,
      topologyResult,
      assignmentResult,
      activityResult,
    ] = await Promise.allSettled([
      getInstallation(),
      getInstallations(),
      getRuntimeStatus(),
      getTopology(),
      getResourceAssignments(),
      getActivity(),
    ]);
    if (installationResult.status === "fulfilled") {
      setInstallation(installationResult.value);
      setInstallationError(false);
    } else {
      setInstallationError(true);
    }
    if (installationsResult.status === "fulfilled") {
      setInstallations(installationsResult.value);
    }
    if (runtimeResult.status === "fulfilled") {
      setRuntime(runtimeResult.value);
      setRuntimeError(false);
    } else {
      setRuntimeError(true);
    }
    if (topologyResult.status === "fulfilled") {
      setTopology(topologyResult.value);
      setTopologyError(false);
    } else {
      setTopologyError(true);
    }
    if (assignmentResult.status === "fulfilled") {
      setAssignments(assignmentResult.value);
    }
    if (activityResult.status === "fulfilled") {
      setActivity(activityResult.value);
      setActivityError(false);
    } else {
      setActivityError(true);
    }
    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  return (
    <AppShell
      session={session}
      workspace={workspace}
      installation={installation}
      installations={installations}
      runtime={runtime}
      runtimeError={runtimeError}
      refreshing={refreshing}
      onNavigate={setWorkspace}
      onRefresh={() => void loadStatus(true)}
      onSignOut={() => {
        void logout().finally(onSignedOut);
      }}
    >
      {workspace === "overview" && (
        <OverviewPage
          installation={installation}
          runtime={runtime}
          activity={activity}
          installationError={installationError}
          runtimeError={runtimeError}
          activityError={activityError}
          loading={loading}
        />
      )}
      {workspace === "installation" && (
        <InstallationPage
          session={session}
          installation={installation}
          topology={topology}
          assignments={assignments}
          installationError={installationError}
          topologyError={topologyError}
          loading={loading}
          onRefresh={() => loadStatus(true)}
        />
      )}
      {workspace === "shows" && <ShowsPage />}
      {workspace === "operate" && (
        <OperatePage
          session={session}
          installation={installation}
          runtime={runtime}
          topology={topology}
          runtimeError={runtimeError}
          loading={loading}
          onRefresh={() => loadStatus(true)}
        />
      )}
      {workspace === "settings" && (
        <SettingsPage
          session={session}
          installations={installations}
          onRefresh={() => loadStatus(true)}
        />
      )}
    </AppShell>
  );
}

function Login({ onAuthenticated }: { onAuthenticated: (session: Session) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      onAuthenticated(await login(username, password));
    } catch {
      setError("Sign-in failed. Check your credentials and try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-layout">
      <section className="login-card" aria-labelledby="login-title">
        <span className="login-card__brand">
          <span className="brand__mark" aria-hidden="true" />
          Cueverge
        </span>
        <p className="eyebrow">Secure show workspace</p>
        <h1 id="login-title">Sign in to Cueverge</h1>
        <p className="muted">Draft editing and runtime status require an authorized session.</p>
        <form onSubmit={(event) => void submit(event)}>
          <label>
            Username
            <input
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
            />
          </label>
          <label>
            Password
            <input
              autoComplete="current-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {error && <p role="alert">{error}</p>}
          <button className="primary-button" disabled={submitting}>
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </section>
    </main>
  );
}

function ShowsPage() {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    getProjects().then(setProjects).catch(() => setError(true));
  }, []);

  return (
    <>
      <WorkspaceHeader
        eyebrow="Authoring"
        title="Shows"
        description="Revisioned drafts remain isolated from the active show package."
      />
      <Panel title="Draft projects">
        {error ? (
          <ErrorState />
        ) : projects === null ? (
          <Skeleton lines={3} />
        ) : projects.length === 0 ? (
          <EmptyState
            title="No draft projects"
            description="The authoring API is ready for the first revisioned project."
          />
        ) : (
          <div className="project-grid">
            {projects.map((project) => (
              <article className="project-card" key={project.id}>
                <span>Revision {project.head_revision}</span>
                <h2>{project.name}</h2>
                <p>Updated {new Date(project.updated_at).toLocaleString()}</p>
              </article>
            ))}
          </div>
        )}
      </Panel>
    </>
  );
}

export function OperatePage({
  session,
  installation,
  runtime,
  topology,
  runtimeError,
  loading,
  onRefresh,
}: {
  session: Session;
  installation: InstallationStatus | null;
  runtime: RuntimeStatus | null;
  topology: Topology | null;
  runtimeError: boolean;
  loading: boolean;
  onRefresh: () => Promise<void>;
}) {
  const [positionMs, setPositionMs] = useState(0);
  const [pending, setPending] = useState<OperateAction | null>(null);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [previewResource, setPreviewResource] = useState("");
  const [previewDuration, setPreviewDuration] = useState(15);
  const [preview, setPreview] = useState<MaintenancePreview | null>(null);
  const [previewNow, setPreviewNow] = useState(Date.now());
  const canOperate = session.role === "operator" || session.role === "administrator";
  const unavailableRequiredDevice =
    topology?.nodes.some(
      (node) =>
        node.kind === "direct_media_agent" &&
        node.configured &&
        node.connection !== "connected",
    ) ?? true;
  const active = runtime?.status.active_scene != null;
  const maintenanceMode = runtime?.status.operations.mode === "maintenance";
  const previewRemaining = preview
    ? Math.max(0, Math.ceil((new Date(preview.expires_at).getTime() - previewNow) / 1_000))
    : 0;

  useEffect(() => {
    if (!preview) {
      return;
    }
    const timer = window.setInterval(() => {
      const current = Date.now();
      setPreviewNow(current);
      if (current >= new Date(preview.expires_at).getTime()) {
        setPreview(null);
      }
    }, 1_000);
    return () => window.clearInterval(timer);
  }, [preview]);

  async function runAction(action: OperateAction, confirmMessage?: string) {
    if (confirmMessage && !window.confirm(confirmMessage)) {
      return;
    }

    setPending(action);
    setOperationError(null);
    try {
      await operate(action, action === "seek" ? positionMs : undefined);
      await onRefresh();
    } catch {
      setOperationError("The controller rejected the operation. Refresh status and try again.");
    } finally {
      setPending(null);
    }
  }

  async function runPreview() {
    if (!previewResource) {
      return;
    }
    setOperationError(null);
    try {
      setPreview(await startMaintenancePreview(previewResource, previewDuration));
      setPreviewNow(Date.now());
    } catch {
      setOperationError(
        "Preview requires maintenance mode, idle playback, and a configured resource.",
      );
    }
  }

  return (
    <>
      <WorkspaceHeader
        eyebrow="Operations"
        title="Operate"
        description="Authenticated controller actions with explicit state and impact checks."
      />
      <Panel title="Controller status">
        {runtimeError ? (
          <ErrorState />
        ) : loading && !runtime ? (
          <Skeleton />
        ) : runtime ? (
          <dl className="detail-list detail-list--wide">
            <div>
              <dt>Mode</dt>
              <dd>{runtime.status.operations.mode}</dd>
            </div>
            <div>
              <dt>Active scene</dt>
              <dd>{runtime.status.active_scene ?? "None"}</dd>
            </div>
            <div>
              <dt>Playback</dt>
              <dd>{runtime.status.playback_state ?? "Stopped"}</dd>
            </div>
            <div>
              <dt>Authoritative time</dt>
              <dd>{formatDuration(runtime.status.estimated_position_ms)}</dd>
            </div>
            <div>
              <dt>Connected agents</dt>
              <dd>{runtime.status.connected_agents.length}</dd>
            </div>
            <div>
              <dt>Queued scenes</dt>
              <dd>
                {runtime.status.queued_scenes.length
                  ? runtime.status.queued_scenes.join(", ")
                  : "None"}
              </dd>
            </div>
            <div>
              <dt>Health</dt>
              <dd>
                {runtime.status.degraded
                  ? degradedGuidance(runtime.status.degraded_reason)
                  : "Not degraded"}
              </dd>
            </div>
          </dl>
        ) : (
          <EmptyState title="No status" description="No controller response is available." />
        )}
      </Panel>
      <Panel
        title="Show controls"
        description={
          canOperate
            ? "Commands affect the active installation."
            : "Your role can observe status but cannot operate the show."
        }
        className="operate-controls"
      >
        <div className="operate-controls__groups">
          <section>
            <h3>Playback</h3>
            <div className="operate-controls__buttons">
              <button
                className="primary-button"
                disabled={!canOperate || unavailableRequiredDevice || active || pending !== null}
                onClick={() =>
                  void runAction(
                    "start",
                    "Start the configured show on all required connected outputs?",
                  )
                }
              >
                Start show
              </button>
              <button
                disabled={!canOperate || !active || pending !== null}
                onClick={() => void runAction("pause")}
              >
                Pause
              </button>
              <button
                disabled={!canOperate || !active || pending !== null}
                onClick={() => void runAction("resume")}
              >
                Resume
              </button>
              <button
                disabled={!canOperate || !active || pending !== null}
                onClick={() => void runAction("stop", "Stop the active show and return outputs safe?")}
              >
                Stop
              </button>
            </div>
            {unavailableRequiredDevice && (
              <p className="control-note">
                Start is unavailable until all assigned media clients are connected.
              </p>
            )}
          </section>
          <section>
            <h3>Position</h3>
            <div className="seek-control">
              <label>
                Position (ms)
                <input
                  min="0"
                  type="number"
                  value={positionMs}
                  onChange={(event) => setPositionMs(Math.max(0, Number(event.target.value)))}
                />
              </label>
              <button
                disabled={!canOperate || !active || pending !== null}
                onClick={() => void runAction("seek")}
              >
                Seek
              </button>
            </div>
          </section>
          <section>
            <h3>Mode and recovery</h3>
            <div className="operate-controls__buttons">
              <button
                disabled={!canOperate || pending !== null}
                onClick={() => void runAction("automatic")}
              >
                Automatic
              </button>
              <button
                disabled={!canOperate || pending !== null}
                onClick={() =>
                  void runAction(
                    "maintenance",
                    "Enter maintenance mode and stop active playback?",
                  )
                }
              >
                Maintenance
              </button>
              <button
                disabled={!canOperate || unavailableRequiredDevice || pending !== null}
                onClick={() =>
                  void runAction("reset", "Reset to the configured ambient scene?")
                }
              >
                Reset ambient
              </button>
              <button
                className="danger-button"
                disabled={!canOperate || pending !== null}
                onClick={() =>
                  void runAction(
                    "blackout",
                    "Blackout stops playback and emits the configured safe lighting state. Continue?",
                  )
                }
              >
                Blackout
              </button>
            </div>
          </section>
        </div>
        {pending && <p role="status">Sending {pending}…</p>}
        {operationError && <p role="alert">{operationError}</p>}
      </Panel>
      <Panel
        title="Maintenance simulation"
        description="Browser-only bounded patterns. No command is sent to projectors or lighting."
        className="maintenance-preview-panel"
      >
        <div className="maintenance-preview-controls">
          <label>
            Resource
            <select
              value={previewResource}
              onChange={(event) => setPreviewResource(event.target.value)}
            >
              <option value="">Select a resource</option>
              {installation?.resources.map((resource) => (
                <option key={resource.resource} value={resource.resource}>
                  {resource.resource}
                </option>
              ))}
            </select>
          </label>
          <label>
            Duration
            <select
              value={previewDuration}
              onChange={(event) => setPreviewDuration(Number(event.target.value))}
            >
              <option value={5}>5 seconds</option>
              <option value={15}>15 seconds</option>
              <option value={30}>30 seconds</option>
            </select>
          </label>
          <button
            className="primary-button"
            disabled={
              !canOperate ||
              !maintenanceMode ||
              active ||
              !previewResource ||
              preview !== null
            }
            onClick={() => void runPreview()}
          >
            Start simulated pattern
          </button>
        </div>
        {!maintenanceMode && (
          <p className="control-note">Enter maintenance mode to arm simulated patterns.</p>
        )}
        {preview && (
          <div className="maintenance-preview" role="status">
            <header>
              <div>
                <strong>{preview.resource}</strong>
                <small>Simulation only · {previewRemaining} seconds remaining</small>
              </div>
              <StatusText tone="info">
                {preview.pattern === "alignment_grid" ? "Alignment grid" : "Pixel index chase"}
              </StatusText>
            </header>
            {preview.pattern === "alignment_grid" ? (
              <div className="alignment-grid" aria-label="Simulated projector alignment grid">
                {Array.from({ length: 24 }, (_, index) => (
                  <span key={index}>{index + 1}</span>
                ))}
              </div>
            ) : (
              <div className="pixel-chase" aria-label="Simulated pixel index chase">
                {Array.from({ length: 24 }, (_, index) => (
                  <span key={index}>{index + 1}</span>
                ))}
              </div>
            )}
          </div>
        )}
      </Panel>
      {runtime && <OperationalDiagnostics runtime={runtime} />}
    </>
  );
}

function OperationalDiagnostics({ runtime }: { runtime: RuntimeStatus }) {
  const status = runtime.status;
  const counters = [
    ["Pending commands", status.pending_commands],
    ["Command errors", status.command_errors],
    ["Command timeouts", status.command_timeouts],
    ["Abandoned commands", status.abandoned_commands],
    ["Heartbeat timeouts", status.heartbeat_timeouts],
    ["Rejected messages", status.rejected_protocol_messages],
    ["Rejected telemetry", status.rejected_telemetry],
    ["Scene completions", status.scene_completions],
    ["Completion errors", status.scene_completion_errors],
    ["Scene failovers", status.scene_failovers],
    ["Lighting errors", status.lighting_output_error_count],
    ["Skipped render deadlines", status.skipped_render_deadlines],
  ] as const;

  return (
    <div className="operations-diagnostics">
      <Panel title="Media outputs" description="Per-output playback and synchronization">
        {status.media_outputs.length ? (
          <div className="output-diagnostic-list">
            {status.media_outputs.map((output) => (
              <article key={output.output_id}>
                <header>
                  <div>
                    <strong>{output.output_id}</strong>
                    <small>{output.agent_id}</small>
                  </div>
                  <StatusText tone={output.connected ? "success" : "warning"}>
                    {output.connected ? "Connected" : "Disconnected"}
                  </StatusText>
                </header>
                <dl>
                  <div>
                    <dt>Role</dt>
                    <dd>{output.clock_source ? "Clock source" : "Follower"}</dd>
                  </div>
                  <div>
                    <dt>Position</dt>
                    <dd>{formatDuration(output.last_position_ms)}</dd>
                  </div>
                  <div>
                    <dt>Drift</dt>
                    <dd>{output.drift_ms === null ? "Unavailable" : `${output.drift_ms} ms`}</dd>
                  </div>
                  <div>
                    <dt>Pending</dt>
                    <dd>{output.pending_commands}</dd>
                  </div>
                </dl>
                {output.prepare_failed && (
                  <p className="diagnostic-error">Prepare failed for this output.</p>
                )}
              </article>
            ))}
          </div>
        ) : (
          <EmptyState
            title="No active media outputs"
            description="Per-output telemetry appears after a scene starts."
          />
        )}
      </Panel>
      <Panel title="Lighting outputs" description="Active routed lighting state">
        {status.lighting_outputs.length ? (
          <div className="output-diagnostic-list">
            {status.lighting_outputs.map((output) => (
              <article key={output.output_id}>
                <header>
                  <div>
                    <strong>{output.output_id}</strong>
                    <small>{output.resource}</small>
                  </div>
                  <StatusText tone={output.error ? "danger" : "success"}>
                    {output.error ? "Error" : "Rendering"}
                  </StatusText>
                </header>
                <dl>
                  <div>
                    <dt>Pixels</dt>
                    <dd>{output.pixel_count.toLocaleString()}</dd>
                  </div>
                  <div>
                    <dt>Timing offset</dt>
                    <dd>{output.timing_offset_ms} ms</dd>
                  </div>
                </dl>
                {output.error && <p className="diagnostic-error">{output.error}</p>}
              </article>
            ))}
          </div>
        ) : (
          <EmptyState
            title="No active lighting outputs"
            description="Lighting routing appears after a scene starts."
          />
        )}
      </Panel>
      <Panel title="Runtime counters" description="Command and render diagnostics">
        <dl className="diagnostic-counters">
          {counters.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd className={value > 0 ? "diagnostic-counter--warning" : ""}>{value}</dd>
            </div>
          ))}
          <div>
            <dt>Render rate</dt>
            <dd>{status.render_target_fps} FPS</dd>
          </div>
          <div>
            <dt>Mean interval</dt>
            <dd>
              {status.mean_render_interval_ms === null
                ? "Unavailable"
                : `${status.mean_render_interval_ms.toFixed(1)} ms`}
            </dd>
          </div>
          <div>
            <dt>Rendered frames</dt>
            <dd>{status.rendered_frames.toLocaleString()}</dd>
          </div>
          <div>
            <dt>Stale frames</dt>
            <dd>{status.stale_frames}</dd>
          </div>
        </dl>
      </Panel>
      <Panel
        title="Recent command failures"
        description="Bounded device command errors, timeouts, and disconnect abandonment"
      >
        {status.recent_command_failures.length ? (
          <ol className="command-failure-list">
            {status.recent_command_failures.slice(0, 8).map((failure, index) => (
              <li key={`${failure.agent_id}-${failure.command}-${failure.age_ms}-${index}`}>
                <div>
                  <strong>
                    {failure.agent_id} · {failure.command}
                  </strong>
                  <small>
                    {failure.error_code} · {formatAge(failure.age_ms)}
                  </small>
                </div>
                <p>{failure.detail ?? commandFailureGuidance(failure.error_code)}</p>
                <span>{commandFailureGuidance(failure.error_code)}</span>
              </li>
            ))}
          </ol>
        ) : (
          <EmptyState
            title="No recent command failures"
            description="Device command errors, timeouts, and abandoned requests appear here."
          />
        )}
      </Panel>
      <Panel title="Automation state" description="Director, audience, and opportunity facts">
        <dl className="detail-list">
          <div>
            <dt>Director pool</dt>
            <dd>{recordString(status.director, "active_pool_id") ?? "Not configured"}</dd>
          </div>
          <div>
            <dt>Director category</dt>
            <dd>{recordString(status.director, "active_category") ?? "Not configured"}</dd>
          </div>
          <div>
            <dt>Eligible scenes</dt>
            <dd>{recordStrings(status.director, "eligible_scene_ids")}</dd>
          </div>
          <div>
            <dt>Audience</dt>
            <dd>{recordString(status.audience, "state") ?? "Not configured"}</dd>
          </div>
          <div>
            <dt>Encounter</dt>
            <dd>{recordString(status.encounter, "state") ?? "Not configured"}</dd>
          </div>
          <div>
            <dt>Opportunities</dt>
            <dd>{status.opportunities === null ? "Not configured" : "Configured"}</dd>
          </div>
        </dl>
      </Panel>
    </div>
  );
}

function formatDuration(positionMs: number | null): string {
  if (positionMs === null) {
    return "Unavailable";
  }
  const totalSeconds = Math.floor(positionMs / 1_000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}.${Math.floor(
    (positionMs % 1_000) / 100,
  )}`;
}

function degradedGuidance(reason: string | null): string {
  if (reason === null) {
    return "Degraded for an unspecified reason";
  }
  if (reason.includes("disconnected")) {
    return `${reason} — reconnect the affected device or stop the show`;
  }
  if (reason.includes("command_timeout")) {
    return `${reason} — inspect device connectivity before retrying`;
  }
  if (reason.includes("reconciliation")) {
    return `${reason} — stop stale playback before restarting`;
  }
  return reason;
}

function commandFailureGuidance(errorCode: string): string {
  if (errorCode === "connection_lost") {
    return "Reconnect and test the device before retrying the show.";
  }
  if (errorCode === "command_timeout") {
    return "Check heartbeat and latency, then retry only after the device is responsive.";
  }
  if (errorCode === "player_error") {
    return "Inspect the client player log and verify the referenced artifact.";
  }
  return "Inspect device status and logs before retrying.";
}

function formatAge(ageMs: number): string {
  if (ageMs < 1_000) {
    return "just now";
  }
  if (ageMs < 60_000) {
    return `${Math.floor(ageMs / 1_000)}s ago`;
  }
  return `${Math.floor(ageMs / 60_000)}m ago`;
}

function recordString(
  record: Record<string, unknown> | null,
  key: string,
): string | null {
  const value = record?.[key];
  return typeof value === "string" ? value : null;
}

function recordStrings(record: Record<string, unknown> | null, key: string): string {
  const value = record?.[key];
  return Array.isArray(value) && value.every((item) => typeof item === "string")
    ? value.join(", ") || "None"
    : "Not configured";
}

export function SettingsPage({
  session,
  installations,
  onRefresh,
}: {
  session: Session;
  installations: InstallationSummary[];
  onRefresh: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const canManage = session.role === "administrator";

  async function create() {
    setCreating(true);
    setError(null);
    try {
      await createInstallation(name);
      setName("");
      await onRefresh();
    } catch {
      setError("Installation creation failed. Names must be unique.");
    } finally {
      setCreating(false);
    }
  }

  return (
    <>
      <WorkspaceHeader
        eyebrow="Application"
        title="Settings"
        description="Durable installation records and application configuration."
      />
      <Panel
        title="Installations"
        description="Home is runtime-backed; new installations remain unprovisioned until a package is attached."
      >
        {canManage && (
          <div className="installation-create">
            <label>
              New installation name
              <input
                maxLength={200}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <button
              className="primary-button"
              disabled={creating || !name.trim()}
              onClick={() => void create()}
            >
              {creating ? "Creating…" : "Create installation"}
            </button>
          </div>
        )}
        <div className="installation-records">
          {installations.map((installation) => (
            <InstallationRecord
              key={installation.id}
              installation={installation}
              canManage={canManage}
              onRefresh={onRefresh}
              onError={setError}
            />
          ))}
        </div>
        {error && <p role="alert">{error}</p>}
      </Panel>
    </>
  );
}

function InstallationRecord({
  installation,
  canManage,
  onRefresh,
  onError,
}: {
  installation: InstallationSummary;
  canManage: boolean;
  onRefresh: () => Promise<void>;
  onError: (message: string) => void;
}) {
  const [name, setName] = useState(installation.name);
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      await updateInstallation(installation.id, installation.revision, name);
      await onRefresh();
    } catch {
      onError("Installation update failed. Refresh and try again.");
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!window.confirm(`Delete ${installation.name}? This cannot be undone.`)) {
      return;
    }
    setSaving(true);
    try {
      await deleteInstallation(installation.id, installation.revision);
      await onRefresh();
    } catch {
      onError("Installation deletion failed. Runtime-backed installations cannot be deleted.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <article className="installation-record">
      <div>
        <strong>{installation.name}</strong>
        <small>
          Revision {installation.revision} ·{" "}
          {installation.runtime_active ? "Runtime backed" : "Not provisioned"}
        </small>
      </div>
      {canManage && (
        <div className="installation-record__actions">
          <label>
            <span className="sr-only">Name for {installation.name}</span>
            <input
              aria-label={`Name for ${installation.name}`}
              disabled={saving}
              maxLength={200}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <button
            disabled={saving || !name.trim() || name === installation.name}
            onClick={() => void save()}
          >
            Save
          </button>
          <button
            className="danger-button"
            disabled={saving || installation.runtime_active}
            onClick={() => void remove()}
          >
            Delete
          </button>
        </div>
      )}
    </article>
  );
}
