import { FormEvent, useCallback, useEffect, useState } from "react";

import {
  ApiError,
  getInstallation,
  getProjects,
  getResourceAssignments,
  getRuntimeStatus,
  getSession,
  getTopology,
  InstallationStatus,
  login,
  logout,
  operate,
  OperateAction,
  ProjectSummary,
  ResourceAssignment,
  RuntimeStatus,
  Session,
  Topology,
} from "./api";
import { AppShell, Workspace } from "./components/AppShell";
import { EmptyState, ErrorState, Panel, Skeleton, WorkspaceHeader } from "./components/ui";
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
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [topology, setTopology] = useState<Topology | null>(null);
  const [assignments, setAssignments] = useState<ResourceAssignment[]>([]);
  const [installationError, setInstallationError] = useState(false);
  const [runtimeError, setRuntimeError] = useState(false);
  const [topologyError, setTopologyError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const loadStatus = useCallback(async (refresh = false) => {
    if (refresh) {
      setRefreshing(true);
    }
    const [installationResult, runtimeResult, topologyResult, assignmentResult] =
      await Promise.allSettled([
      getInstallation(),
      getRuntimeStatus(),
      getTopology(),
      getResourceAssignments(),
    ]);
    if (installationResult.status === "fulfilled") {
      setInstallation(installationResult.value);
      setInstallationError(false);
    } else {
      setInstallationError(true);
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
          installationError={installationError}
          runtimeError={runtimeError}
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
          runtime={runtime}
          topology={topology}
          runtimeError={runtimeError}
          loading={loading}
          onRefresh={() => loadStatus(true)}
        />
      )}
      {workspace === "settings" && <SettingsPage />}
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

function OperatePage({
  session,
  runtime,
  topology,
  runtimeError,
  loading,
  onRefresh,
}: {
  session: Session;
  runtime: RuntimeStatus | null;
  topology: Topology | null;
  runtimeError: boolean;
  loading: boolean;
  onRefresh: () => Promise<void>;
}) {
  const [positionMs, setPositionMs] = useState(0);
  const [pending, setPending] = useState<OperateAction | null>(null);
  const [operationError, setOperationError] = useState<string | null>(null);
  const canOperate = session.role === "operator" || session.role === "administrator";
  const unavailableRequiredDevice =
    topology?.nodes.some(
      (node) =>
        node.kind === "direct_media_agent" &&
        node.configured &&
        node.connection !== "connected",
    ) ?? true;
  const active = runtime?.status.active_scene != null;

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
              <dt>Connected agents</dt>
              <dd>{runtime.status.connected_agents.length}</dd>
            </div>
            <div>
              <dt>Health</dt>
              <dd>{runtime.status.degraded ? runtime.status.degraded_reason : "Not degraded"}</dd>
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
    </>
  );
}

function SettingsPage() {
  return (
    <>
      <WorkspaceHeader
        eyebrow="Application"
        title="Settings"
        description="Controller preferences and permissions will appear here in a later slice."
      />
      <Panel title="Settings unavailable">
        <EmptyState
          title="No editable settings yet"
          description="GUI-00 does not expose controller or hardware configuration mutations."
        />
      </Panel>
    </>
  );
}
