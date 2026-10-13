import { FormEvent, useCallback, useEffect, useState } from "react";

import {
  ApiError,
  getInstallation,
  getProjects,
  getRuntimeStatus,
  getSession,
  InstallationStatus,
  login,
  logout,
  ProjectSummary,
  RuntimeStatus,
  Session,
} from "./api";
import { AppShell, Workspace } from "./components/AppShell";
import { EmptyState, ErrorState, Panel, Skeleton, WorkspaceHeader } from "./components/ui";
import { OverviewPage } from "./pages/OverviewPage";

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
  const [installationError, setInstallationError] = useState(false);
  const [runtimeError, setRuntimeError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const loadStatus = useCallback(async (refresh = false) => {
    if (refresh) {
      setRefreshing(true);
    }
    const [installationResult, runtimeResult] = await Promise.allSettled([
      getInstallation(),
      getRuntimeStatus(),
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
          installation={installation}
          installationError={installationError}
          loading={loading}
        />
      )}
      {workspace === "shows" && <ShowsPage />}
      {workspace === "operate" && (
        <OperatePage runtime={runtime} runtimeError={runtimeError} loading={loading} />
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

function InstallationPage({
  installation,
  installationError,
  loading,
}: {
  installation: InstallationStatus | null;
  installationError: boolean;
  loading: boolean;
}) {
  return (
    <>
      <WorkspaceHeader
        eyebrow="Installation"
        title="Installation"
        description="Configured logical resources in the active runtime package."
      />
      <Panel title="Resources" description="Runtime bindings exposed by the current package">
        {installationError ? (
          <ErrorState />
        ) : loading && !installation ? (
          <Skeleton lines={4} />
        ) : installation?.resources.length ? (
          <ul className="resource-list">
            {installation.resources.map((resource) => (
              <li key={resource.resource}>
                <div>
                  <strong>{resource.resource}</strong>
                  <small>{resource.kind.replaceAll("_", " ")}</small>
                </div>
                <span>
                  {resource.target ?? `${resource.pixel_count ?? "Unknown"} configured pixels`}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            title="No configured resources"
            description="The active installation package contains no logical resources."
          />
        )}
      </Panel>
    </>
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
  runtime,
  runtimeError,
  loading,
}: {
  runtime: RuntimeStatus | null;
  runtimeError: boolean;
  loading: boolean;
}) {
  return (
    <>
      <WorkspaceHeader
        eyebrow="Read-only"
        title="Operate"
        description="Observed controller state. GUI-00 exposes no live control actions."
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
