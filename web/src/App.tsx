import {
  FormEvent,
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { PhysicalOutputTestControls } from "./components/PhysicalOutputTestControls";
import { AssetPreview } from "./components/AssetPreview";
import { DetailsDialog } from "./components/DetailsDialog";
import { ApplyPanel } from "./pages/ApplyPanel";
import { IntegrationsPanel } from "./pages/IntegrationsPanel";
import { LiveOutputMonitor } from "./pages/LiveOutputMonitor";

import {
  ApiError,
  ActivityEvent,
  applyScenePreset,
  AssetRecord,
  archiveProject,
  createInstallation,
  createProjectFromActive,
  createOutputArm,
  createScenePreset,
  deleteInstallation,
  deleteScene,
  duplicateProject,
  exportProject,
  getActivity,
  getAssets,
  getTrashedAssets,
  getInstallation,
  getInstallations,
  getLocalUsers,
  getProjectDraft,
  getProjectMembers,
  getProjects,
  getResourceAssignments,
  getSession,
  InstallationStatus,
  InstallationSummary,
  importProject,
  LocalUser,
  login,
  logout,
  MaintenancePreview,
  operate,
  organizeScene,
  placeAsset,
  purgeAsset,
  replaceAssetFile,
  replaceAssetFromAsset,
  revertAssetRevision,
  OperateAction,
  OutputArm,
  ProjectSummary,
  ProjectDraftRecord,
  ProjectExportDocument,
  renameProject,
  ResourceAssignment,
  Role,
  RuntimeStatus,
  Session,
  startMaintenancePreview,
  stopOutputArm,
  Topology,
  updateInstallation,
  restoreAsset,
  trashAsset,
  updateAssetMetadata,
  updateProjectDraft,
  putLocalUser,
  uploadAsset,
} from "./api";
import { AppShell, Workspace } from "./components/AppShell";
import { VirtualizedList } from "./components/VirtualizedList";
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
import { useLiveStatus } from "./useLiveStatus";
import { SnapshotFreshness, STATUS_TIMEOUT_MS } from "./status";

const ShowPlan = lazy(() => import("./pages/ShowPlan").then((module) => ({ default: module.ShowPlan })));
const VisitorInteractions = lazy(() => import("./pages/VisitorInteractions").then((module) => ({ default: module.VisitorInteractions })));
const SimulationScenarioPanel = lazy(() => import("./components/SimulationScenarioPanel").then((module) => ({ default: module.SimulationScenarioPanel })));
const ShowPreviewWorkspace = lazy(() => import("./pages/ShowPreviewWorkspace").then((module) => ({ default: module.ShowPreviewWorkspace })));
const ShowLogicDesigner = lazy(() =>
  import("./pages/ShowLogicDesigner").then((module) => ({
    default: module.ShowLogicDesigner,
  })),
);
const PublicationWorkspace = lazy(() =>
  import("./pages/PublicationWorkspace").then((module) => ({
    default: module.PublicationWorkspace,
  })),
);
const CollaborationWorkspace = lazy(() =>
  import("./pages/CollaborationWorkspace").then((module) => ({
    default: module.CollaborationWorkspace,
  })),
);
const McpWorkspace = lazy(() =>
  import("./pages/McpWorkspace").then((module) => ({
    default: module.McpWorkspace,
  })),
);
const TimelineEditor = lazy(() =>
  import("./pages/TimelineEditor").then((module) => ({
    default: module.TimelineEditor,
  })),
);
const SceneWorkspace = lazy(() =>
  import("./pages/SceneWorkspace").then((module) => ({ default: module.SceneWorkspace })),
);

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
  const [installationEntry, setInstallationEntry] = useState<"photos" | "objects" | "devices" | "inputs">("photos");
  const [installationProjectId, setInstallationProjectId] = useState("");
  const [authoringDirty, setAuthoringDirty] = useState(false);
  const navigate = (next: Workspace, setupSection: "photos" | "objects" | "devices" | "inputs" = "photos") => {
    if (next === workspace) return;
    if (!authoringDirty ||
      window.confirm("Discard unsaved changes and leave this page?")) {
        if (next === "installation") setInstallationEntry(setupSection);
        setWorkspace(next);
      }
  };
  const [installation, setInstallation] = useState<InstallationStatus | null>(null);
  const [installations, setInstallations] = useState<InstallationSummary[]>([]);
  const {
    runtime, topology, runtimeError, topologyError,
    runtimeFreshness, topologyFreshness, refresh: refreshLiveStatus,
    sessionExpired,
  } = useLiveStatus();
  const [assignments, setAssignments] = useState<ResourceAssignment[]>([]);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [installationError, setInstallationError] = useState(false);
  const [activityError, setActivityError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [configurationError, setConfigurationError] = useState<string | null>(null);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const configurationRequest = useRef<AbortController | null>(null);
  const configurationTimeout = useRef<number | null>(null);

  const loadStatus = useCallback(async (refresh = false) => {
    configurationRequest.current?.abort();
    if (configurationTimeout.current !== null) window.clearTimeout(configurationTimeout.current);
    const request = new AbortController();
    configurationRequest.current = request;
    const timeout = window.setTimeout(() => request.abort(), STATUS_TIMEOUT_MS);
    configurationTimeout.current = timeout;
    if (refresh) {
      setRefreshing(true);
      setRefreshVersion((version) => version + 1);
    }
    const liveRequest = refreshLiveStatus(refresh);
    const [
      installationResult,
      installationsResult,
      assignmentResult,
      activityResult,
    ] = await Promise.allSettled([
      getInstallation(request.signal),
      getInstallations(request.signal),
      getResourceAssignments(request.signal),
      getActivity(request.signal),
    ]);
    window.clearTimeout(timeout);
    if (configurationRequest.current !== request) return;
    configurationRequest.current = null;
    configurationTimeout.current = null;
    const unavailable = [];
    if (installationResult.status === "rejected") unavailable.push("installation setup");
    if (installationsResult.status === "rejected") unavailable.push("installation list");
    if (assignmentResult.status === "rejected") unavailable.push("device assignments");
    setConfigurationError(unavailable.length
      ? `Could not refresh ${unavailable.join(" and ")}. Previously loaded setup may be out of date.`
      : null);
    if (installationResult.status === "fulfilled") {
      setInstallation(installationResult.value);
      setInstallationError(false);
    } else {
      setInstallationError(true);
    }
    if (installationsResult.status === "fulfilled") {
      setInstallations(installationsResult.value);
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
    await liveRequest;
    setRefreshing(false);
  }, [refreshLiveStatus]);

  useEffect(() => {
    if (sessionExpired) {
      setRefreshing(false);
      return;
    }
    void loadStatus();
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "hidden") void loadStatus();
    }, 30000);
    return () => {
      window.clearInterval(timer);
      configurationRequest.current?.abort();
      configurationRequest.current = null;
      if (configurationTimeout.current !== null) window.clearTimeout(configurationTimeout.current);
      configurationTimeout.current = null;
    };
  }, [loadStatus, sessionExpired]);

  return (
    <AppShell
      session={session}
      workspace={workspace}
      installation={installation}
      installationUnavailable={installationError}
      runtime={runtime}
      runtimeFreshness={runtimeFreshness}
      topologyFreshness={topologyFreshness}
      refreshing={refreshing}
      configurationError={configurationError}
      sessionExpired={sessionExpired}
      onReauthenticate={() => {
        if (window.confirm("Return to sign-in? Unsaved changes in this tab will be discarded.")) onSignedOut();
      }}
      onNavigate={navigate}
      onRefresh={() => void loadStatus(true)}
      onSignOut={() => {
        if (authoringDirty && !window.confirm("Discard unsaved changes and sign out?")) return;
        void logout().finally(onSignedOut);
      }}
    >
      {workspace === "overview" && (
        <OverviewPage
          onManageDevices={() => navigate("installation", "devices")}
          installation={installation}
          runtime={runtime}
          activity={activity}
          installationError={installationError}
          runtimeError={runtimeError}
          runtimeFreshness={runtimeFreshness}
          topology={topology}
          topologyFreshness={topologyFreshness}
          activityError={activityError}
          refreshKey={refreshVersion}
          loading={loading}
          onNavigate={navigate}
        />
      )}
      {workspace === "installation" && (
        <InstallationPage
          initialSection={installationEntry}
          initialProjectId={installationProjectId}
          session={session}
          installation={installation}
          topology={topology}
          assignments={assignments}
          installationError={installationError}
          topologyError={topologyError}
          loading={loading}
          onRefresh={() => loadStatus(true)}
          onDirtyChange={setAuthoringDirty}
        />
      )}
      {workspace === "shows" && <ShowsPage session={session} runtime={runtime} runtimeFresh={runtimeFreshness === "fresh"} onDirtyChange={setAuthoringDirty} onInputs={() => navigate("installation", "inputs")}
        onObjects={(projectId) => {
          if (authoringDirty && !window.confirm("Discard unsaved changes and open installation objects?")) return;
          setInstallationProjectId(projectId); setInstallationEntry("objects"); setWorkspace("installation");
        }} />}
      {workspace === "operate" && (
        <OperatePage
          session={session}
          installation={installation}
          runtime={runtime}
          topology={topology}
          runtimeError={runtimeError}
          runtimeFreshness={runtimeFreshness}
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

export function ShowsPage({
  session,
  initialProjects,
  onDirtyChange,
  onInputs,
  onObjects,
  runtime,
  runtimeFresh,
}: {
  session: Session;
  initialProjects?: ProjectSummary[];
  onDirtyChange?: (dirty: boolean) => void;
  onInputs?: () => void;
  onObjects?: (projectId: string) => void;
  runtime?: RuntimeStatus | null;
  runtimeFresh?: boolean;
}) {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(
    initialProjects ?? null,
  );
  const [error, setError] = useState(false);
  const [name, setName] = useState("");
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [openProjectId, setOpenProjectId] = useState<string | null>(null);
  const [openedNewProject, setOpenedNewProject] = useState(false);
  const canAuthor = ["designer", "publisher", "administrator"].includes(session.role);

  const loadProjects = useCallback(async () => {
    try {
      setProjects(await getProjects());
      setError(false);
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    if (initialProjects === undefined) {
      void loadProjects();
    }
  }, [initialProjects, loadProjects]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(false);
    try {
      await action();
      await loadProjects();
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  }

  async function importFile(file: File) {
    await run(async () => {
      const document = JSON.parse(await file.text()) as ProjectExportDocument;
      await importProject(document);
    });
  }

  const filteredProjects =
    projects?.filter((project) =>
      project.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
    ) ?? null;
  const activeProjects = filteredProjects?.filter((project) => !project.archived_at) ?? [];
  const archivedProjects = filteredProjects?.filter((project) => project.archived_at) ?? [];

  const openProject = projects?.find((project) => project.id === openProjectId);
  if (openProject) {
    return <>{error && <ErrorState title="Show action failed" description="The action or its refresh could not be completed. Check saved state before retrying." />}<ProjectCard
      key={openProject.id} project={openProject} session={session} busy={busy} run={run}
      onClose={() => { setOpenProjectId(null); setOpenedNewProject(false); void loadProjects(); }}
      initialView={openedNewProject ? "scenes" : "plan"}
      onDirtyChange={onDirtyChange}
      onInputs={onInputs}
      onObjects={onObjects}
      runtime={runtime}
      runtimeFresh={runtimeFresh}
    /></>;
  }
  return (
    <>
      <WorkspaceHeader
        eyebrow="Authoring"
        title="Shows"
        description="Choose a show to create, edit, and safely preview its scenes."
      />
      {canAuthor && (
        <details className="new-show-flow">
          <summary>New show</summary>
          <p>Start with scenes from the current installation. Creating a show does not make it live.</p>
          <div className="project-actions">
            <label>
              Show name
              <input
                maxLength={200}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <button
              className="primary-button"
              disabled={busy || !name.trim()}
              onClick={() =>
                void run(async () => {
                  const created = await createProjectFromActive(name);
                  setName("");
                  setOpenedNewProject(true);
                  setOpenProjectId(created.project.id);
                })
              }
            >
              Create show
            </button>
            <label className="file-button">
              Import project
              <input
                accept="application/json,.json"
                disabled={busy}
                type="file"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) {
                    void importFile(file);
                  }
                }}
              />
            </label>
          </div>
        </details>
      )}
      <Panel title="Your shows">
        <label className="project-search">
          Find a show
          <input value={search} onChange={(event) => setSearch(event.target.value)} />
        </label>
        {error ? (
          <ErrorState />
        ) : projects === null ? (
          <Skeleton lines={3} />
        ) : filteredProjects?.length === 0 ? (
          <EmptyState
            title="No shows found"
            description={
              projects.length
                ? "No projects match the current search."
                : "Create a show or import an existing one."
            }
          />
        ) : (
          <>
            <ProjectGroup
              title="Saved"
              projects={activeProjects}
              busy={busy}
              onOpen={setOpenProjectId}
            />
            {archivedProjects.length > 0 && (
              <ProjectGroup
                title="Archived"
                projects={archivedProjects}
                busy={busy}
                onOpen={setOpenProjectId}
              />
            )}
          </>
        )}
      </Panel>
    </>
  );
}

function ProjectGroup({
  title,
  projects,
  busy,
  onOpen,
}: {
  title: string;
  projects: ProjectSummary[];
  busy: boolean;
  onOpen: (id: string) => void;
}) {
  return (
    <section className="project-group" aria-label={`${title} projects`}>
      <h3>{title}</h3>
      <div className="project-grid">
        {projects.map((project) => (
          <article className="show-library-card" key={project.id}>
            <h4>{project.name}</h4>
            <p>Edited {new Date(project.updated_at).toLocaleString()}</p>
            <small>{project.archived_at ? "Archived · read only" : "Saved show · not a live status"}</small>
            <button disabled={busy} onClick={() => onOpen(project.id)} aria-label={`Open show ${project.name}`}>Open show</button>
          </article>
        ))}
      </div>
    </section>
  );
}

function ProjectCard({
  project,
  session,
  busy,
  run,
  onClose,
  initialView = "plan",
  onDirtyChange,
  onInputs,
  onObjects,
  runtime,
  runtimeFresh,
}: {
  project: ProjectSummary;
  initialView?: "plan" | "scenes";
  session: Session;
  busy: boolean;
  run: (action: () => Promise<void>) => Promise<void>;
  onClose: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  onInputs?: () => void;
  onObjects?: (projectId: string) => void;
  runtime?: RuntimeStatus | null;
  runtimeFresh?: boolean;
}) {
  const [name, setName] = useState(project.name);
  const [assets, setAssets] = useState<AssetRecord[] | null>(null);
  const [trashedAssets, setTrashedAssets] = useState<AssetRecord[]>([]);
  const [draft, setDraft] = useState<ProjectDraftRecord | null>(null);
  const [assetSearch, setAssetSearch] = useState("");
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [assetError, setAssetError] = useState<string | null>(null);
  const [timelineWorkspace, setTimelineWorkspace] = useState(false);
  const [sceneId, setSceneId] = useState("");
  const [sceneDirty, setSceneDirty] = useState(false);
  const [interactionDirty, setInteractionDirty] = useState(false);
  const [showView, setShowView] = useState<"plan" | "scenes" | "interactions" | "preview">(initialView);
  const [previewStartsWithVisitors, setPreviewStartsWithVisitors] = useState(false);
  const interactionDirtyRef = useRef(false);
  const sceneDirtyRef = useRef(false);
  const updateSceneDirty = useCallback((value: boolean) => {
    sceneDirtyRef.current = value;
    setSceneDirty(value);
    onDirtyChange?.(value || interactionDirtyRef.current);
  }, [onDirtyChange]);
  const updateInteractionDirty = useCallback((value: boolean) => {
    interactionDirtyRef.current = value;
    setInteractionDirty(value);
    onDirtyChange?.(value || sceneDirtyRef.current);
  }, [onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);
  const [sharingOpen, setSharingOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [logicOpenRequest, setLogicOpenRequest] = useState(0);
  const [timelineReload, setTimelineReload] = useState(0);
  const [projectRole, setProjectRole] = useState<Role>(
    session.role === "administrator" ? "administrator" : "viewer",
  );
  const canEditScene = ["designer", "publisher", "administrator"].includes(projectRole);
  const canAuthor = canEditScene && !sceneDirty && !interactionDirty;
  const canPublish = ["publisher", "administrator"].includes(projectRole) && !sceneDirty && !interactionDirty;
  const [canLiveOutput, setCanLiveOutput] = useState(session.role === "administrator");
  const [permissionError, setPermissionError] = useState<string | null>(null);
  const updatePermissions = useCallback((role: Role, liveOutput: boolean) => {
    setProjectRole(role);
    setCanLiveOutput(liveOutput);
    setPermissionError(null);
  }, []);

  const loadAssets = useCallback(async () => {
      const [assetResult, draftResult, trashResult] = await Promise.allSettled([
        getAssets(project.id),
        getProjectDraft(project.id),
        getTrashedAssets(project.id),
      ]);
      const issues: string[] = [];
      if (assetResult.status === "fulfilled") setAssets(assetResult.value);
      else issues.push("Assets could not be refreshed.");
      if (trashResult.status === "fulfilled") setTrashedAssets(trashResult.value);
      else issues.push("Asset trash could not be refreshed.");
      if (draftResult.status === "fulfilled") {
        if (!sceneDirtyRef.current && !interactionDirtyRef.current) {
          const loadedDraft = draftResult.value;
          setDraft(loadedDraft);
          setSceneId((current) => loadedDraft.draft.scenes.some((scene) => scene.id === current)
            ? current : loadedDraft.draft.scenes[0]?.id ?? "");
        }
      } else issues.push("Saved scene data could not be refreshed.");
      setAssetError(issues.join(" ") || null);
  }, [project.id]);

  useEffect(() => {
    void loadAssets();
  }, [loadAssets]);

  useEffect(() => {
    if (session.role === "administrator") return;
    let current = true;
    getProjectMembers(project.id).then((members) => {
      if (!current) return;
      const membership = members.find((member) => member.username === session.username);
      updatePermissions(membership?.role ?? "viewer", membership?.can_live_output === true);
    }).catch(() => { if (current) setPermissionError("Project permissions could not be checked. Editing remains unavailable."); });
    return () => { current = false; };
  }, [project.id, session.role, session.username, updatePermissions]);

  useEffect(() => {
    if (!assets?.some((asset) => asset.status === "uploading" || asset.replacement_status === "processing")) {
      return;
    }
    const timer = window.setTimeout(() => void loadAssets(), 2000);
    return () => window.clearTimeout(timer);
  }, [assets, loadAssets]);

  async function upload(file: File) {
    setUploadProgress(0);
    setAssetError(null);
    try {
      const result = await uploadAsset(project.id, file, (receivedBytes) => {
        setUploadProgress(Math.round((receivedBytes / file.size) * 100));
      });
      if (result.state === "failed") {
        setAssetError(result.error ?? "Upload verification failed.");
      }
      await loadAssets();
    } catch {
      setAssetError("Asset upload failed.");
    } finally {
      setUploadProgress(null);
    }
  }

  async function download() {
    const document = await exportProject(project.id);
    const blob = new Blob([JSON.stringify(document, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = window.document.createElement("a");
    anchor.href = url;
    anchor.download = `${project.name.replace(/[^A-Za-z0-9._-]+/g, "-")}.cueverge.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  const visibleAssets =
    assets?.filter((asset) => {
      const search = assetSearch.trim().toLocaleLowerCase();
      if (!search) {
        return true;
      }
      return [
        asset.name,
        asset.original_filename,
        asset.folder,
        asset.owner,
        asset.license,
        ...asset.tags,
      ].some((value) => value?.toLocaleLowerCase().includes(search));
    }) ?? null;

  if (draft && timelineWorkspace) {
    return (
      <article className="project-card project-card--timeline" aria-label={`Timeline editor for ${project.name}`}>
        <nav className="timeline-workspace-nav" aria-label="Timeline editor navigation">
          <button className="show-back" onClick={() => setTimelineWorkspace(false)}>
            Back to scene
          </button>
          <span>{project.name}</span>
        </nav>
        <Suspense fallback={<p>Loading timeline editor…</p>}>
          <TimelineEditor
            key={`timeline-${timelineReload}`}
            assets={assets ?? []}
            canEdit={canAuthor && !project.archived_at}
            draft={draft}
            projectId={project.id}
            selectedSceneId={sceneId}
            onSceneChange={setSceneId}
            onSave={async (document) => {
              setDraft(await updateProjectDraft(project.id, draft.revision, document));
            }}
            onOutputsChanged={(updated) => {
              setDraft(updated);
              setTimelineReload((value) => value + 1);
            }}
          />
        </Suspense>
      </article>
    );
  }

  return (
    <article className="project-card" aria-label={`Show ${project.name}`}>
      <button className="show-back" onClick={() => {
        if ((!sceneDirty && !interactionDirty) || window.confirm("Discard unsaved edits and return to the show library?")) onClose();
      }}>Back to shows</button>
      <h1>{project.name}</h1>
      {onObjects && <button onClick={() => onObjects(project.id)}>Manage installation objects</button>}
      {assetError && <p role="alert">{assetError}</p>}
      {permissionError && <p role="alert">{permissionError}</p>}
      <div className="show-workspace-tabs" role="group" aria-label="Show workspace">
        {(["plan", "scenes", "interactions", "preview"] as const).map((view) => <button key={view} aria-pressed={showView === view} onClick={() => {
          if (showView === view) return;
          if ((sceneDirty || interactionDirty) && !window.confirm("Discard unsaved edits and change workspace?")) return;
          updateSceneDirty(false); updateInteractionDirty(false);
          if (view === "preview") setPreviewStartsWithVisitors(false);
          setShowView(view);
        }}>{view === "plan" ? "Show plan" : view === "scenes" ? "Scenes" : view === "interactions" ? "Visitor responses" : "Preview"}</button>)}
      </div>
      {draft && showView === "plan" && <Suspense fallback={<p>Loading show plan…</p>}>
        <ShowPlan draft={draft} canEdit={canEditScene && !project.archived_at} onUpdated={setDraft}
          onOpenScene={(id) => { setSceneId(id); setShowView("scenes"); }}
          onVisitorResponses={() => setShowView("interactions")} />
      </Suspense>}
      {draft && showView === "interactions" && <Suspense fallback={<p>Loading visitor responses…</p>}>
        <VisitorInteractions draft={draft} canEdit={canEditScene && !project.archived_at}
          canPreview={["designer", "operator", "publisher", "administrator"].includes(projectRole)}
          onDirtyChange={updateInteractionDirty} onUpdated={setDraft} onInputs={onInputs}
          onPreview={() => { setPreviewStartsWithVisitors(true); setShowView("preview"); }}
          onAdvanced={() => {
            setAdvancedOpen(true);
            setLogicOpenRequest((value) => value + 1);
          }} />
      </Suspense>}
      {draft && showView === "preview" && <Suspense fallback={<p>Loading preview workspace…</p>}>
        <ShowPreviewWorkspace draft={draft} assets={assets ?? []} startWithVisitors={previewStartsWithVisitors} canTestVisitors={["designer", "operator", "publisher", "administrator"].includes(projectRole)} />
        <SimulationScenarioPanel draft={draft} projectId={project.id} sceneId={sceneId}
          canEdit={canAuthor && !project.archived_at} />
      </Suspense>}
      {draft && showView === "scenes" && <nav className="show-breadcrumb" aria-label="Breadcrumb">
        <button onClick={() => {
          if (sceneDirty && !window.confirm("Discard unsaved scene edits and return to the show plan?")) return;
          updateSceneDirty(false);
          setShowView("plan");
        }}>Show plan</button>
        <span aria-hidden="true">›</span>
        <span aria-current="page">{draft.draft.scenes.find((scene) => scene.id === sceneId)?.name ?? "Scene"}</span>
      </nav>}
      {draft && showView === "scenes" && <Suspense fallback={<p>Loading scene workspace…</p>}>
        <SceneWorkspace
          draft={draft} sceneId={sceneId} canEdit={canEditScene && !project.archived_at}
          canAdd={!interactionDirty}
          onCreated={(updated, id) => { setSceneId(id); setDraft(updated); }}
          assets={assets ?? []} onSelectScene={setSceneId} onDirtyChange={updateSceneDirty}
          onOpenTimeline={() => setTimelineWorkspace(true)}
          onSave={async (document) => setDraft(await updateProjectDraft(project.id, draft.revision, document))}
          onDelete={async (id) => {
            const updated = await deleteScene(project.id, draft.revision, id);
            setSceneId(updated.draft.scenes[0].id);
            setDraft(updated);
          }}
        />
      </Suspense>}
      <details className="show-options"><summary>Show options</summary>
      <small>Saved revision {draft?.revision ?? project.head_revision} · metadata {project.metadata_revision}</small>
      {canAuthor && !project.archived_at ? (
        <label>Show name
        <input
          aria-label={`Name for ${project.name}`}
          disabled={busy}
          maxLength={200}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        </label>
      ) : (
        <h2>{project.name}</h2>
      )}
      <p>Updated {new Date(project.updated_at).toLocaleString()}</p>
      <div className="project-card__actions">
        <button onClick={() => void download()}>Export</button>
        {canAuthor && !project.archived_at && (
          <>
            <button
              disabled={busy || !name.trim() || name === project.name}
              onClick={() =>
                void run(() =>
                  renameProject(project.id, project.metadata_revision, name),
                )
              }
            >
              Rename
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void run(() => duplicateProject(project.id, `${project.name} Copy`))
              }
            >
              Duplicate
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void run(() => archiveProject(project.id, project.metadata_revision))
              }
            >
              Archive
            </button>
          </>
        )}
      </div>
      <details className="share-options" onToggle={(event) => setSharingOpen(event.currentTarget.open)}>
        <summary>Share &amp; review</summary>
        {sharingOpen && draft && <Suspense fallback={<p>Loading sharing…</p>}>
          <CollaborationWorkspace projectId={project.id} revision={draft.revision} session={session} onPermissions={updatePermissions} />
        </Suspense>}
      </details>
      </details>
      <details className="project-assets">
        <summary>Assets</summary>
        <div className="asset-toolbar">
          <input
            aria-label={`Search assets in ${project.name}`}
            placeholder="Find media"
            type="search"
            value={assetSearch}
            onChange={(event) => setAssetSearch(event.target.value)}
          />
          {canAuthor && !project.archived_at && (
            <label className="file-button file-button--compact">
              Add visual or audio asset
              <input
                accept="video/mp4,video/webm,video/quicktime,audio/mpeg,audio/wav,audio/flac,audio/ogg,image/png,image/jpeg,image/webp"
                disabled={uploadProgress !== null}
                type="file"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) {
                    void upload(file);
                  }
                }}
              />
            </label>
          )}
        </div>
        {uploadProgress !== null && (
          <div className="upload-progress" role="status">
            <span style={{ width: `${uploadProgress}%` }} />
            <small>{uploadProgress}% uploaded</small>
          </div>
        )}
        {assetError && <p role="alert">{assetError}</p>}
        {visibleAssets === null ? (
          <small>Loading assets…</small>
        ) : visibleAssets.length ? (
          <VirtualizedList
            ariaLabel={`Assets in ${project.name}`}
            estimateSize={420}
            measureItems
            items={visibleAssets}
            keyFor={(asset) => asset.id}
            renderItem={(asset) => (
              <div className="virtual-asset-row">
                <AssetPreview asset={asset} />
                <div className="asset-card__details">
                  <header className="asset-card__header">
                    <strong>{asset.name}</strong>
                    <StatusText
                      tone={
                        asset.original_file_status === "missing" ||
                        asset.original_file_status === "size_mismatch" ||
                        asset.artifact_faults.length
                          ? "danger"
                          : asset.status === "ready"
                          ? "success"
                          : asset.status === "failed" || asset.status === "quarantined"
                            ? "danger"
                            : "info"
                      }
                    >
                      {asset.original_file_status === "missing"
                        ? "missing"
                        : asset.original_file_status === "size_mismatch"
                          ? "corrupt"
                          : asset.artifact_faults.length
                            ? "artifact fault"
                            : asset.status === "uploading"
                              ? "processing"
                              : asset.status}
                    </StatusText>
                  </header>
                  <p className="asset-card__file">
                    {asset.original_filename ?? "Upload pending"} ·{" "}
                    {asset.size_bytes === null ? "—" : formatAssetBytes(asset.size_bytes)}
                  </p>
                  {asset.metadata && <p className="asset-card__metadata">{formatAssetMetadata(asset.metadata)}</p>}
                  <p className="asset-card__organization">
                    {asset.favorite ? "Favorite · " : ""}
                    {asset.folder ?? "Unfiled"}
                    {asset.tags.length ? ` · ${asset.tags.join(", ")}` : ""}
                  </p>
                  <div className="asset-card__diagnostics">
                    <small>Original file: {asset.original_file_status.replace("_", " ")}</small>
                    {asset.artifact_faults.map((fault) => (
                      <small className="asset-diagnostic asset-diagnostic--danger" key={fault}>
                        Artifact {fault}
                      </small>
                    ))}
                    {asset.unused ? (
                      <small className="asset-diagnostic asset-diagnostic--warning">
                        Unused in the current draft
                      </small>
                    ) : (
                      <small>
                        Used by{" "}
                        {(asset.draft_usage?.length
                          ? asset.draft_usage
                          : asset.references.map((reference) =>
                              reference.output_id
                                ? `${reference.scene_name} (${reference.output_id})`
                                : reference.scene_name,
                            )
                        ).join(", ")}
                      </small>
                    )}
                    {asset.error && <small role="alert">{asset.error}</small>}
                  </div>
                  <AssetGovernance
                    asset={asset}
                    canEdit={canAuthor && !project.archived_at}
                    onUpdated={(updated) =>
                      setAssets((current) =>
                        current?.map((candidate) =>
                          candidate.id === updated.id ? updated : candidate,
                        ) ?? null,
                      )
                    }
                  />
                  {canAuthor && !project.archived_at && draft && (
                    <AssetPlacement
                      asset={asset}
                      draft={draft}
                      projectId={project.id}
                      onPlaced={async (updated) => {
                        setDraft(updated);
                        await loadAssets();
                      }}
                    />
                  )}
                  <AssetReplacement
                    asset={asset}
                    assets={assets ?? []}
                    canEdit={canAuthor && !project.archived_at}
                    onChanged={loadAssets}
                  />
                  {canAuthor && !project.archived_at && (
                    <AssetRemoval asset={asset} onChanged={loadAssets} />
                  )}
                </div>
              </div>
            )}
          />
        ) : assets?.length ? (
          <small>No assets match this search.</small>
        ) : (
          <small>No assets imported.</small>
        )}
        <AssetTrash
          assets={trashedAssets}
          canRestore={canAuthor && !project.archived_at}
          canPurge={projectRole === "administrator"}
          onChanged={loadAssets}
        />
      </details>
      {draft && <ApplyPanel draft={draft} canApply={canPublish && canLiveOutput && !project.archived_at} runtime={runtime} runtimeFresh={runtimeFresh} />}
      {draft && (
        <details className="show-advanced" open={advancedOpen}
          onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}>
          <summary>Advanced authoring and history</summary>
          {sceneDirty && <p role="status">Save or cancel scene edits before editing other show data.</p>}
          <Suspense fallback={<p>Loading MCP authoring workspace…</p>}>
            <McpWorkspace
              projectId={project.id}
              canDesign={canAuthor && !sceneDirty}
              canPublish={canPublish}
              canActivate={canPublish && canLiveOutput}
              canOperate={canLiveOutput && ["operator", "administrator"].includes(projectRole)}
              canConfigureInputs={session.role === "administrator"}
              canConfigureInstallation={canAuthor && ["designer", "publisher", "administrator"].includes(session.role)}
            />
          </Suspense>
          <ScenePresetOrganizer
            canEdit={canAuthor && !project.archived_at && !sceneDirty}
            draft={draft}
            projectId={project.id}
            onUpdated={setDraft}
          />
          <Suspense fallback={<p>Loading autonomous show designer…</p>}>
            <ShowLogicDesigner
              openRequest={logicOpenRequest}
              onInputs={onInputs}
              canEdit={canAuthor && !project.archived_at && !sceneDirty}
              draft={draft}
              projectId={project.id}
              onSave={async (document) => {
                setDraft(
                  await updateProjectDraft(project.id, draft.revision, document),
                );
              }}
            />
          </Suspense>
          <div className="show-advanced-timeline">
            <strong>Timeline editor</strong>
            <p>Layered media, lighting cues, compositions and whole-display overlays open in a full-screen editor.</p>
            <button disabled={sceneDirty} onClick={() => setTimelineWorkspace(true)}>
              Open timeline editor
            </button>
          </div>
          <Suspense fallback={<p>Loading publication workspace…</p>}>
            <PublicationWorkspace
              canPublish={canPublish && !project.archived_at}
              draft={draft}
              projectId={project.id}
            />
          </Suspense>
        </details>
      )}
    </article>
  );
}

function ScenePresetOrganizer({
  projectId,
  draft,
  canEdit,
  onUpdated,
}: {
  projectId: string;
  draft: ProjectDraftRecord;
  canEdit: boolean;
  onUpdated: (draft: ProjectDraftRecord) => void;
}) {
  const [search, setSearch] = useState("");
  const [selectedScene, setSelectedScene] = useState(draft.draft.scenes[0]?.id ?? "");
  const [presetName, setPresetName] = useState("");
  const [presetFolder, setPresetFolder] = useState("");
  const [presetTags, setPresetTags] = useState("");
  const [presetFavorite, setPresetFavorite] = useState(false);
  const [busy, setBusy] = useState(false);
  const metadata = draft.draft.editor_metadata;
  const organizations = metadataRecord(metadata.scene_organization);
  const presets = Object.values(metadataRecord(metadata.scene_presets)).map(metadataRecord);
  const presetUsage = metadataRecord(metadata.scene_preset_usage);
  const normalizedSearch = search.toLocaleLowerCase();
  const visibleScenes = draft.draft.scenes.filter((scene) => {
    const organization = metadataRecord(organizations[scene.id]);
    return [scene.name, scene.id, organization.folder, ...metadataStrings(organization.tags)]
      .some((value) => String(value ?? "").toLocaleLowerCase().includes(normalizedSearch));
  });
  const visiblePresets = presets.filter((preset) =>
    [preset.name, preset.folder, ...metadataStrings(preset.tags)].some((value) =>
      String(value ?? "").toLocaleLowerCase().includes(normalizedSearch),
    ),
  );

  async function createPreset() {
    if (!selectedScene || !presetName.trim()) {
      return;
    }
    setBusy(true);
    try {
      onUpdated(
        await createScenePreset(projectId, draft.revision, {
          scene_id: selectedScene,
          name: presetName,
          folder: presetFolder || null,
          tags: presetTags
            .split(",")
            .map((tag) => tag.trim())
            .filter(Boolean),
          favorite: presetFavorite,
        }),
      );
      setPresetName("");
      setPresetFolder("");
      setPresetTags("");
      setPresetFavorite(false);
    } finally {
      setBusy(false);
    }
  }

  async function applyPreset(presetId: string) {
    if (!selectedScene) {
      return;
    }
    setBusy(true);
    try {
      onUpdated(
        await applyScenePreset(
          projectId,
          draft.revision,
          presetId,
          selectedScene,
        ),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="scene-library">
      <summary>Scenes & reusable presets</summary>
      <input
        aria-label="Search scenes and presets"
        placeholder="Search scenes, folders, tags, presets…"
        type="search"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      <div className="scene-library__grid">
        <section>
          <strong>Scenes</strong>
          {visibleScenes.map((scene) => {
            const referencedBy = draft.draft.scenes
              .filter(
                (candidate) =>
                  candidate.failure_scene_id === scene.id ||
                  candidate.completion?.target_scene_id === scene.id,
              )
              .map((candidate) => candidate.name);
            return (
              <SceneOrganizationRow
                key={scene.id}
                canEdit={canEdit}
                draft={draft}
                organization={metadataRecord(organizations[scene.id])}
                projectId={projectId}
                referencedBy={referencedBy}
                scene={scene}
                onUpdated={onUpdated}
              />
            );
          })}
        </section>
        <section>
          <strong>Reusable presets</strong>
          {canEdit && (
            <div className="preset-create">
              <select
                aria-label="Source or target scene"
                disabled={busy}
                value={selectedScene}
                onChange={(event) => setSelectedScene(event.target.value)}
              >
                {draft.draft.scenes.map((scene) => (
                  <option key={scene.id} value={scene.id}>
                    {scene.name}
                  </option>
                ))}
              </select>
              <input
                aria-label="Preset name"
                disabled={busy}
                placeholder="Preset name"
                value={presetName}
                onChange={(event) => setPresetName(event.target.value)}
              />
              <input
                aria-label="Preset folder"
                disabled={busy}
                placeholder="Folder"
                value={presetFolder}
                onChange={(event) => setPresetFolder(event.target.value)}
              />
              <input
                aria-label="Preset tags"
                disabled={busy}
                placeholder="tag, tag"
                value={presetTags}
                onChange={(event) => setPresetTags(event.target.value)}
              />
              <label>
                <input
                  checked={presetFavorite}
                  disabled={busy}
                  type="checkbox"
                  onChange={(event) => setPresetFavorite(event.target.checked)}
                />
                Favorite
              </label>
              <button
                disabled={busy || !presetName.trim() || !selectedScene}
                onClick={() => void createPreset()}
              >
                Save scene as preset
              </button>
            </div>
          )}
          {visiblePresets.length ? (
            <ul>
              {visiblePresets.map((preset) => {
                const presetId = String(preset.id);
                const usedBy = Object.entries(presetUsage)
                  .filter(([, value]) => value === presetId)
                  .map(([sceneId]) => sceneId);
                return (
                  <li key={presetId}>
                    <strong>{String(preset.name)}</strong>
                    <small>
                      {preset.favorite ? "Favorite · " : ""}
                      {String(preset.folder ?? "Unfiled")}
                      {metadataStrings(preset.tags).length
                        ? ` · ${metadataStrings(preset.tags).join(", ")}`
                        : ""}
                    </small>
                    <small>{usedBy.length ? `Used by ${usedBy.join(", ")}` : "Unused"}</small>
                    {canEdit && (
                      <button disabled={busy} onClick={() => void applyPreset(presetId)}>
                        Apply to selected scene
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          ) : (
            <small>No reusable presets.</small>
          )}
        </section>
      </div>
    </details>
  );
}

function SceneOrganizationRow({
  projectId,
  draft,
  scene,
  organization,
  referencedBy,
  canEdit,
  onUpdated,
}: {
  projectId: string;
  draft: ProjectDraftRecord;
  scene: ProjectDraftRecord["draft"]["scenes"][number];
  organization: Record<string, unknown>;
  referencedBy: string[];
  canEdit: boolean;
  onUpdated: (draft: ProjectDraftRecord) => void;
}) {
  const [folder, setFolder] = useState(String(organization.folder ?? ""));
  const [tags, setTags] = useState(metadataStrings(organization.tags).join(", "));
  const [favorite, setFavorite] = useState(organization.favorite === true);
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      onUpdated(
        await organizeScene(projectId, draft.revision, scene.id, {
          folder: folder || null,
          tags: tags
            .split(",")
            .map((tag) => tag.trim())
            .filter(Boolean),
          favorite,
        }),
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <article>
      <strong>{scene.name}</strong>
      <small>{referencedBy.length ? `Referenced by ${referencedBy.join(", ")}` : "No incoming references"}</small>
      {canEdit && (
        <>
          <input
            aria-label={`Folder for scene ${scene.name}`}
            disabled={saving}
            placeholder="Folder"
            value={folder}
            onChange={(event) => setFolder(event.target.value)}
          />
          <input
            aria-label={`Tags for scene ${scene.name}`}
            disabled={saving}
            placeholder="tag, tag"
            value={tags}
            onChange={(event) => setTags(event.target.value)}
          />
          <label>
            <input
              checked={favorite}
              disabled={saving}
              type="checkbox"
              onChange={(event) => setFavorite(event.target.checked)}
            />
            Favorite
          </label>
          <button disabled={saving} onClick={() => void save()}>
            Save scene organization
          </button>
        </>
      )}
    </article>
  );
}

function metadataRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function metadataStrings(value: unknown): string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string")
    ? value
    : [];
}

function AssetGovernance({
  asset,
  canEdit,
  onUpdated,
}: {
  asset: AssetRecord;
  canEdit: boolean;
  onUpdated: (asset: AssetRecord) => void;
}) {
  const [owner, setOwner] = useState(asset.owner ?? "");
  const [license, setLicense] = useState(asset.license ?? "");
  const [provenance, setProvenance] = useState(asset.provenance ?? "");
  const [reviewStatus, setReviewStatus] = useState(asset.review_status);
  const [expiresOn, setExpiresOn] = useState(asset.expires_on ?? "");
  const [folder, setFolder] = useState(asset.folder ?? "");
  const [tags, setTags] = useState(asset.tags.join(", "));
  const [favorite, setFavorite] = useState(asset.favorite);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const expired =
    asset.expires_on !== null &&
    new Date(`${asset.expires_on}T23:59:59`).getTime() < Date.now();

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      onUpdated(
        await updateAssetMetadata(asset, {
          owner: owner || null,
          license: license || null,
          provenance: provenance || null,
          review_status: reviewStatus,
          expires_on: expiresOn || null,
          folder: folder || null,
          tags: tags
            .split(",")
            .map((tag) => tag.trim())
            .filter(Boolean),
          favorite,
        }),
      );
    } catch {
      setError("Asset governance metadata could not be saved. Reload and retry.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <details className="asset-governance">
      <summary>
        Organization & governance · {asset.review_status}
        {expired ? " · expired" : ""}
      </summary>
      <form onSubmit={(event) => void save(event)}>
        <label>
          Folder
          <input
            disabled={!canEdit || saving}
            maxLength={200}
            placeholder="Visuals/Windows"
            value={folder}
            onChange={(event) => setFolder(event.target.value)}
          />
        </label>
        <label>
          Tags
          <input
            disabled={!canEdit || saving}
            maxLength={1000}
            placeholder="window, halloween"
            value={tags}
            onChange={(event) => setTags(event.target.value)}
          />
        </label>
        <label className="asset-governance__favorite">
          <input
            checked={favorite}
            disabled={!canEdit || saving}
            type="checkbox"
            onChange={(event) => setFavorite(event.target.checked)}
          />
          Favorite
        </label>
        <label>
          Owner
          <input
            disabled={!canEdit || saving}
            maxLength={200}
            value={owner}
            onChange={(event) => setOwner(event.target.value)}
          />
        </label>
        <label>
          License
          <input
            disabled={!canEdit || saving}
            maxLength={500}
            value={license}
            onChange={(event) => setLicense(event.target.value)}
          />
        </label>
        <label>
          Review status
          <select
            disabled={!canEdit || saving}
            value={reviewStatus}
            onChange={(event) =>
              setReviewStatus(
                event.target.value as AssetRecord["review_status"],
              )
            }
          >
            <option value="unreviewed">Unreviewed</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
          </select>
        </label>
        <label>
          Expires
          <input
            disabled={!canEdit || saving}
            type="date"
            value={expiresOn}
            onChange={(event) => setExpiresOn(event.target.value)}
          />
        </label>
        <label className="asset-governance__wide">
          Provenance notes
          <textarea
            disabled={!canEdit || saving}
            maxLength={4000}
            rows={3}
            value={provenance}
            onChange={(event) => setProvenance(event.target.value)}
          />
        </label>
        {error && <small role="alert">{error}</small>}
        {canEdit && (
          <button disabled={saving} type="submit">
            {saving ? "Saving…" : "Save governance"}
          </button>
        )}
      </form>
    </details>
  );
}

function mediaKind(mediaType: string | null): string | null {
  return mediaType ? mediaType.split("/")[0] : null;
}

function AssetReplacement({
  asset,
  assets,
  canEdit,
  onChanged,
}: {
  asset: AssetRecord;
  assets: AssetRecord[];
  canEdit: boolean;
  onChanged: () => Promise<void>;
}) {
  const [progress, setProgress] = useState<number | null>(null);
  const [working, setWorking] = useState(false);
  const [sourceId, setSourceId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const kind = mediaKind(asset.media_type);
  const revisions = asset.revisions ?? [];
  const replacing = asset.replacement_status ?? null;
  const busy = working || asset.status === "uploading" || replacing !== null;
  const sources = assets.filter(
    (candidate) =>
      candidate.id !== asset.id &&
      candidate.status === "ready" &&
      candidate.original_file_status === "available" &&
      !candidate.trashed_at &&
      (kind === null || mediaKind(candidate.media_type) === kind),
  );
  const uses = asset.draft_usage?.length
    ? asset.draft_usage
    : asset.references.map((reference) => reference.scene_name);
  const followers = uses.length
    ? `${uses.length} ${uses.length === 1 ? "use" : "uses"} in this draft (${uses.join(", ")}) will switch to the new file`
    : "Nothing in this draft uses it yet";

  async function perform(action: () => Promise<unknown>, failure: string) {
    setWorking(true);
    setError(null);
    try {
      await action();
      await onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : failure);
    } finally {
      setWorking(false);
      setProgress(null);
    }
  }

  async function uploadReplacement(file: File) {
    if (kind && mediaKind(file.type) !== kind) {
      setError(`Choose ${kind === "image" ? "an" : "a"} ${kind} file to replace this ${kind}.`);
      return;
    }
    setProgress(0);
    await perform(async () => {
      const result = await replaceAssetFile(asset, file, (receivedBytes) =>
        setProgress(Math.round((receivedBytes / file.size) * 100)),
      );
      if (result.state === "failed") throw new Error(result.error ?? "Upload verification failed.");
    }, "The replacement file could not be uploaded.");
  }

  return (
    <details className="asset-replacement" open={replacing !== null || undefined}>
      <summary>
        Replace file · revision {asset.current_revision ?? "—"}
        {replacing ? ` · replacement ${replacing}` : ""}
        {!replacing && asset.replacement_error ? " · last replacement not used" : ""}
      </summary>
      <small>
        Keeps this asset&apos;s name, scene placements, timing and effects. {followers}.
        Published releases keep the file they were published with.
      </small>
      {replacing === "processing" && (
        <small role="status">
          Processing the new file. The current file stays in use until it is ready.
        </small>
      )}
      {replacing === "uploading" && !working && (
        <small role="status">
          A replacement upload did not finish. Choose the file again to restart it.
        </small>
      )}
      {asset.replacement_error && (
        <small className="asset-diagnostic asset-diagnostic--warning">
          Last replacement was not used: {asset.replacement_error}
        </small>
      )}
      {canEdit && (
        <div className="asset-replacement__actions">
          <label>
            Upload edited file
            <input
              aria-label={`Replace file for ${asset.name}`}
              type="file"
              accept={kind ? `${kind}/*` : undefined}
              disabled={busy && replacing !== "uploading"}
              onChange={(event) => {
                const file = event.currentTarget.files?.[0];
                event.currentTarget.value = "";
                if (file) void uploadReplacement(file);
              }}
            />
          </label>
          {progress !== null && <progress aria-label="Replacement upload progress" max={100} value={progress} />}
          {sources.length > 0 && (
            <div className="asset-replacement__source">
              <label>
                Or use an uploaded asset
                <select
                  aria-label={`Replacement source for ${asset.name}`}
                  value={sourceId}
                  disabled={busy}
                  onChange={(event) => setSourceId(event.target.value)}
                >
                  <option value="">Choose an asset…</option>
                  {sources.map((candidate) => (
                    <option key={candidate.id} value={candidate.id}>
                      {candidate.name} ({candidate.original_filename ?? candidate.media_type})
                    </option>
                  ))}
                </select>
              </label>
              <button
                disabled={busy || !sourceId}
                onClick={() =>
                  void perform(async () => {
                    await replaceAssetFromAsset(asset, sourceId);
                    setSourceId("");
                  }, "The asset could not be replaced.")
                }
              >
                Use this file
              </button>
            </div>
          )}
        </div>
      )}
      {revisions.length > 0 && (
        <ol className="asset-replacement__history" aria-label={`File history for ${asset.name}`}>
          {revisions.map((item) => {
            const current = item.revision === asset.current_revision;
            return (
              <li key={item.revision}>
                <small>
                  Revision {item.revision} · {item.original_filename} ·{" "}
                  {formatAssetBytes(item.size_bytes)} · {new Date(item.created_at).toLocaleString()}
                  {current ? " · current" : item.status !== "ready" ? ` · ${item.status}` : ""}
                </small>
                {canEdit && !current && item.status === "ready" && (
                  <button
                    aria-label={`Restore revision ${item.revision} of ${asset.name}`}
                    disabled={busy}
                    onClick={() =>
                      void perform(
                        () => revertAssetRevision(asset, item.revision),
                        "The earlier file could not be restored.",
                      )
                    }
                  >
                    Restore
                  </button>
                )}
              </li>
            );
          })}
        </ol>
      )}
      {error && <small role="alert">{error}</small>}
    </details>
  );
}

function AssetRemoval({
  asset,
  onChanged,
}: {
  asset: AssetRecord;
  onChanged: () => Promise<void>;
}) {
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const blockers = asset.removal_blockers ?? [];

  async function moveToTrash() {
    if (!window.confirm(`Move "${asset.name}" to the trash? You can restore it for 30 days.`)) {
      return;
    }
    setWorking(true);
    setError(null);
    try {
      await trashAsset(asset);
      await onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The asset could not be moved to the trash.");
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="asset-removal">
      <button
        aria-label={`Move ${asset.name} to trash`}
        disabled={working || blockers.length > 0}
        aria-describedby={blockers.length ? `asset-removal-blockers-${asset.id}` : undefined}
        onClick={() => void moveToTrash()}
      >
        {working ? "Moving to trash…" : "Move to trash"}
      </button>
      {blockers.length > 0 && (
        <div id={`asset-removal-blockers-${asset.id}`} className="asset-removal__blockers">
          <small>Remove these uses before moving it to the trash:</small>
          <ul>
            {blockers.map((blocker) => (
              <li key={blocker}>
                <small>{blocker}</small>
              </li>
            ))}
          </ul>
        </div>
      )}
      {error && <small role="alert">{error}</small>}
    </div>
  );
}

function AssetTrash({
  assets,
  canRestore,
  canPurge,
  onChanged,
}: {
  assets: AssetRecord[];
  canRestore: boolean;
  canPurge: boolean;
  onChanged: () => Promise<void>;
}) {
  return (
    <details className="asset-trash">
      <summary>Trash ({assets.length})</summary>
      <small>
        Trashed assets keep their files and can be restored. Each is deleted permanently
        after 30 days unless a published release still needs it. Files shared with other
        assets or shows are kept until nothing uses them.
      </small>
      {assets.length ? (
        <ul aria-label="Trashed assets">
          {assets.map((asset) => (
            <AssetTrashItem
              key={asset.id}
              asset={asset}
              canRestore={canRestore}
              canPurge={canPurge}
              onChanged={onChanged}
            />
          ))}
        </ul>
      ) : (
        <small>The trash is empty.</small>
      )}
    </details>
  );
}

function AssetTrashItem({
  asset,
  canRestore,
  canPurge,
  onChanged,
}: {
  asset: AssetRecord;
  canRestore: boolean;
  canPurge: boolean;
  onChanged: () => Promise<void>;
}) {
  const [working, setWorking] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const purgeBlockers = asset.purge_blockers ?? [];

  async function perform(action: () => Promise<unknown>, failure: string) {
    setWorking(true);
    setError(null);
    try {
      await action();
      await onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : failure);
    } finally {
      setWorking(false);
    }
  }

  return (
    <li className="asset-trash__item">
      <strong>{asset.name}</strong>
      <small>
        {asset.original_filename ?? "Upload pending"} ·{" "}
        {asset.size_bytes === null ? "—" : formatAssetBytes(asset.size_bytes)}
      </small>
      <small>
        Moved to trash{asset.trashed_by ? ` by ${asset.trashed_by}` : ""}
        {asset.trashed_at ? ` on ${new Date(asset.trashed_at).toLocaleString()}` : ""}
        {asset.purge_after
          ? ` · deleted permanently after ${new Date(asset.purge_after).toLocaleDateString()}`
          : ""}
      </small>
      {purgeBlockers.map((blocker) => (
        <small className="asset-diagnostic asset-diagnostic--warning" key={blocker}>
          {blocker}
        </small>
      ))}
      {canRestore && (
        <button
          aria-label={`Restore ${asset.name}`}
          disabled={working}
          onClick={() => void perform(() => restoreAsset(asset), "The asset could not be restored.")}
        >
          Restore
        </button>
      )}
      {canPurge && (
        <div className="asset-trash__purge">
          <label>
            Type <strong>{asset.name}</strong> to delete permanently
            <input
              aria-label={`Confirm permanent deletion of ${asset.name}`}
              disabled={working || purgeBlockers.length > 0}
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
            />
          </label>
          <button
            aria-label={`Delete ${asset.name} permanently`}
            className="danger-button"
            disabled={working || purgeBlockers.length > 0 || confirmation !== asset.name}
            onClick={() =>
              void perform(
                () => purgeAsset(asset, confirmation),
                "The asset could not be deleted permanently.",
              )
            }
          >
            Delete permanently
          </button>
        </div>
      )}
      {error && <small role="alert">{error}</small>}
    </li>
  );
}

function AssetPlacement({
  asset,
  draft,
  projectId,
  onPlaced,
}: {
  asset: AssetRecord;
  draft: ProjectDraftRecord;
  projectId: string;
  onPlaced: (draft: ProjectDraftRecord) => Promise<void>;
}) {
  const options: {
    sceneId: string;
    outputId: string | null;
    label: string;
  }[] = [];
  for (const scene of draft.draft.scenes) {
    if (Array.isArray(scene.media)) {
      options.push(
        ...scene.media.map((output) => ({
          sceneId: scene.id,
          outputId: output.id,
          label: `${scene.name} · ${output.id}`,
        })),
      );
    } else {
      options.push({
        sceneId: scene.id,
        outputId: null,
        label: scene.name,
      });
    }
  }
  const [selected, setSelected] = useState("0");
  const [placing, setPlacing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const target = options[Number(selected)];
    if (!target) {
      setError("Select a scene media output.");
      return;
    }
    setPlacing(true);
    setError(null);
    try {
      await onPlaced(
        await placeAsset(
          projectId,
          draft.revision,
          asset.id,
          target.sceneId,
          target.outputId,
        ),
      );
    } catch {
      setError("Asset placement failed. Reload the project and retry.");
    } finally {
      setPlacing(false);
    }
  }

  if (!options.length) {
    return <p className="asset-placement__empty">
      No media outputs in this show. Add a media output to a scene before placing this asset.
    </p>;
  }

  return (
    <form className="asset-placement" onSubmit={(event) => void submit(event)}>
      <label>
        Place in scene output
        <select
          disabled={
            placing ||
            asset.status !== "ready" ||
            asset.original_file_status !== "available"
          }
          value={selected}
          onChange={(event) => setSelected(event.target.value)}
        >
          {options.map((option, index) => (
            <option key={`${option.sceneId}:${option.outputId ?? "media"}`} value={index}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <button
        disabled={
          placing ||
          asset.status !== "ready" ||
          asset.original_file_status !== "available" ||
          !options.length
        }
        type="submit"
      >
        {placing ? "Placing…" : "Place asset"}
      </button>
      {error && <small role="alert">{error}</small>}
    </form>
  );
}

function formatAssetMetadata(metadata: Record<string, unknown>): string {
  const values: string[] = [];
  if (typeof metadata.duration_ms === "number") {
    values.push(`${(metadata.duration_ms / 1000).toFixed(2)}s`);
  }
  if (typeof metadata.width === "number" && typeof metadata.height === "number") {
    values.push(`${metadata.width}×${metadata.height}`);
  }
  if (typeof metadata.frame_rate === "number") {
    values.push(`${metadata.frame_rate} fps`);
  }
  for (const key of ["video_codecs", "audio_codecs"] as const) {
    const codecs = metadata[key];
    if (Array.isArray(codecs) && codecs.every((codec) => typeof codec === "string")) {
      values.push(codecs.join(", "));
    }
  }
  if (typeof metadata.channels === "number") {
    values.push(`${metadata.channels} ch`);
  }
  if (typeof metadata.sample_rate === "number") {
    values.push(`${metadata.sample_rate} Hz`);
  }
  if (typeof metadata.loudness_lufs === "number") {
    values.push(`${metadata.loudness_lufs} LUFS`);
  }
  return values.length ? values.join(" · ") : "Metadata unavailable";
}

function formatAssetBytes(value: number): string {
  if (value >= 1_000_000_000) {
    return `${(value / 1_000_000_000).toFixed(1)} GB`;
  }
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(1)} MB`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(1)} KB`;
  }
  return `${value} B`;
}

export function OperatePage({
  session,
  installation,
  runtime,
  topology,
  runtimeError,
  runtimeFreshness,
  loading,
  onRefresh,
}: {
  session: Session;
  installation: InstallationStatus | null;
  runtime: RuntimeStatus | null;
  topology: Topology | null;
  runtimeError: boolean;
  runtimeFreshness: SnapshotFreshness;
  loading: boolean;
  onRefresh: () => Promise<void>;
}) {
  const [positionMs, setPositionMs] = useState(0);
  const [pending, setPending] = useState<OperateAction | null>(null);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [previewResource, setPreviewResource] = useState("");
  const [previewDuration, setPreviewDuration] = useState(15);
  const [preview, setPreview] = useState<MaintenancePreview | null>(null);
  const [armConfirmation, setArmConfirmation] = useState("");
  const [outputArm, setOutputArm] = useState<OutputArm | null>(null);
  const [previewNow, setPreviewNow] = useState(Date.now());
  const [chosenScene, setChosenScene] = useState("");
  const sceneOptions = runtime?.status.operations.scene_options;
  const startingScene = chosenScene || runtime?.status.operations.starting_scene_id || sceneOptions?.[0]?.id;
  const activeSceneName = sceneOptions?.find((value) => value.id === runtime?.status.active_scene)?.name ?? runtime?.status.active_scene;
  const canOperate = session.role === "operator" || session.role === "administrator";
  const playbackUnavailable = runtimeFreshness !== "fresh" || Boolean(runtime?.apply_job);
  const unavailableRequiredDevice =
    sceneOptions ? !sceneOptions.find((value) => value.id === startingScene)?.ready : topology?.nodes.some(
      (node) =>
        node.kind === "direct_media_agent" &&
        node.configured &&
        node.connection !== "connected",
    ) ?? true;
  const active = runtime?.status.active_scene != null;
  const physicalTestActive = runtime?.status.output_test?.state === "active";
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
    if (runtimeFreshness !== "fresh" && action !== "stop" && action !== "blackout") {
      setOperationError("Current status is unavailable. Refresh before changing playback or mode.");
      return;
    }
    if (confirmMessage && !window.confirm(confirmMessage)) {
      return;
    }

    setPending(action);
    setOperationError(null);
    try {
      await operate(action, action === "seek" ? positionMs : undefined, action === "start" ? startingScene : undefined);
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

  async function armPreview() {
    if (!previewResource) {
      return;
    }
    setOperationError(null);
    try {
      setOutputArm(
        await createOutputArm(
          previewResource,
          armConfirmation,
          previewDuration,
        ),
      );
    } catch {
      setOperationError(
        "Arming requires idle maintenance mode and exact resource confirmation.",
      );
    }
  }

  async function stopArm() {
    if (!outputArm) {
      return;
    }
    setOutputArm(await stopOutputArm(outputArm.id));
  }

  return (
    <>
      <WorkspaceHeader
        eyebrow="Operations"
        title="Operate"
        description="Start your loaded show, stop safely, and check what the controller is sending."
      />
      <Panel title="Controller status">
        {runtime?.apply_job && <p role="status">Applying saved changes: {runtime.apply_job.state}. Stop and Blackout remain available.</p>}
        <p><strong>{runtimeFreshness !== "fresh" ? "Controller status is not current" : runtime?.status.active_scene ? `${activeSceneName} · ${runtime.status.playback_state}` : "Playback stopped"}</strong></p>
        <p>{runtime?.loaded_catalog?.state === "loaded"
          ? `Loaded: ${runtime.loaded_catalog.project_name ?? "a verified saved show"}. Saving edits does not update this running copy.`
          : runtime?.loaded_catalog?.state === "recovery_required"
            ? "Recover a verified show from Shows before starting."
            : "Legacy/external catalog: saved-show identity is unreported. Apply from Shows to establish a verified identity."}</p>
        {runtimeFreshness === "fresh" && runtime?.status.degraded && <p role="alert">{degradedGuidance(runtime.status.degraded_reason)} Check device connections and the contextual diagnostics before continuing.</p>}
        {runtimeFreshness === "fresh" && runtime?.input_health?.visitor_rules_loaded && <p role={runtime.input_health.error ? "alert" : "status"}>
          {runtime.input_health.state === "disabled" ? "Camera input is off; visitor responses cannot receive camera events."
            : runtime.input_health.error ? `Camera input needs attention: ${runtime.input_health.error}`
              : runtime.input_health.freshness === "fresh" ? "Recent camera input received; visitor matching and scene playback are separate checks."
                : "No recent camera observations. Check Installation > Inputs & connections if visitors are not being detected."}
        </p>}
        <details><summary>Controller details</summary>
        {runtimeError || runtimeFreshness !== "fresh" ? (
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
              <dt>Next scene</dt>
              <dd>{runtime.status.next_scene
                ? `${sceneOptions?.find((value) => value.id === runtime.status.next_scene?.scene_id)?.name ?? runtime.status.next_scene.scene_id} · preloaded on ${runtime.status.next_scene.preloaded_outputs.join(", ")}${runtime.status.next_scene.automatic_cut ? " · cuts at the end of the clock media" : ""}`
                : "Not preloaded"}</dd>
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
              <dt>Clock authority</dt>
              <dd>
                {runtime.status.clock_authority === "controller"
                  ? "Controller"
                  : runtime.status.active_scene ? "Media playback" : "None"}
              </dd>
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
        </details>
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
            {sceneOptions && <label>Starting scene<select value={startingScene ?? ""} onChange={(event) => setChosenScene(event.target.value)} disabled={active || pending !== null}>
              {sceneOptions.map((value) => <option key={value.id} value={value.id}>{value.name}{value.ready ? "" : " — required device unavailable"}</option>)}
            </select></label>}
            <div className="operate-controls__buttons">
              <button
                className="primary-button"
                disabled={!canOperate || playbackUnavailable || unavailableRequiredDevice || active || physicalTestActive || runtime?.loaded_catalog?.state === "recovery_required" || pending !== null}
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
                disabled={!canOperate || playbackUnavailable || !active || runtime?.status.playback_state === "paused" || pending !== null}
                onClick={() => void runAction("pause")}
              >
                Pause
              </button>
              <button
                disabled={!canOperate || playbackUnavailable || !active || runtime?.status.playback_state !== "paused" || pending !== null}
                onClick={() => void runAction("resume")}
              >
                Resume
              </button>
              <button
                disabled={!canOperate || pending !== null}
                onClick={() => void runAction("stop", "Stop the active show and return outputs safe?")}
              >
                Stop
              </button>
              <button className="danger-button" disabled={!canOperate || pending !== null}
                onClick={() => void runAction("blackout", "Stop playback and send black LED frames? This does not verify physical output or other equipment safety.")}>Blackout</button>
            </div>
            {unavailableRequiredDevice && (
              <p className="control-note">
                Start is unavailable until this starting scene's required media devices are connected. Unused and optional media devices do not block an LED-only show.
              </p>
            )}
            {physicalTestActive && <p role="status">A physical wiring test owns the output. Stop it before starting playback.</p>}
          </section>
          <details className="operate-advanced"><summary>More playback and recovery controls</summary>
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
                disabled={!canOperate || playbackUnavailable || !active || pending !== null}
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
                disabled={!canOperate || playbackUnavailable || pending !== null}
                onClick={() => void runAction("automatic")}
              >
                Automatic
              </button>
              <button
                disabled={!canOperate || playbackUnavailable || pending !== null}
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
                disabled={!canOperate || playbackUnavailable || unavailableRequiredDevice || physicalTestActive || pending !== null}
                onClick={() =>
                  void runAction("reset", "Reset to the configured ambient scene?")
                }
              >
                Reset ambient
              </button>
            </div>
          </section>
          </details>
        </div>
        {pending && <p role="status">Sending {pending}…</p>}
        {operationError && <p role="alert">{operationError}</p>}
      </Panel>
      <LiveOutputMonitor installationId={installation?.installation_id ?? undefined} runtime={runtime} />
      <details className="operate-advanced"><summary>Setup tests and maintenance</summary>
      <Panel
        title="Physical LED output tests"
        description="Run a bounded chase on configured LED output devices without changing the show."
      >
        <PhysicalOutputTestControls
          canOperate={canOperate}
          installationId={installation?.installation_id}
        />
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
          <label>
            Type resource name to arm
            <input
              value={armConfirmation}
              onChange={(event) => setArmConfirmation(event.target.value)}
            />
          </label>
          <button
            disabled={
              !canOperate ||
              !maintenanceMode ||
              active ||
              !previewResource ||
              armConfirmation !== previewResource ||
              outputArm?.state === "active"
            }
            onClick={() => void armPreview()}
          >
            Arm selected output
          </button>
          <button
            className="primary-button"
            disabled={
              !canOperate ||
              !maintenanceMode ||
              active ||
              !previewResource ||
              outputArm?.state !== "active" ||
              preview !== null
            }
            onClick={() => void runPreview()}
          >
            Start simulated pattern
          </button>
        </div>
        {outputArm && (
          <div className="notice notice--warning" role="status">
            <strong>
              Armed: {outputArm.resource} · {outputArm.device_identity}
            </strong>
            <p>
              Expires {new Date(outputArm.expires_at).toLocaleTimeString()} ·
              Stop action: {outputArm.stop_action}. Blackout is not assumed safe.
            </p>
            {outputArm.state === "active" && (
              <button onClick={() => void stopArm()}>
                Stop/disarm selected output
              </button>
            )}
          </div>
        )}
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
      </details>
      {runtime && <details><summary>Command and playback diagnostics</summary><OperationalDiagnostics runtime={runtime} /></details>}
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
      {status.event_loop_stalls && (
        <Panel
          title="Event-loop stalls"
          description={`Pauses longer than ${status.event_loop_stalls.threshold_ms} ms in the loop that renders LEDs and serves this page; each one freezes LED output`}
        >
          <dl className="diagnostic-counters">
            <div>
              <dt>Stalls since start</dt>
              <dd className={status.event_loop_stalls.stall_count > 0 ? "diagnostic-counter--warning" : ""}>
                {status.event_loop_stalls.stall_count}
              </dd>
            </div>
            <div>
              <dt>Longest</dt>
              <dd>{status.event_loop_stalls.longest_ms} ms</dd>
            </div>
          </dl>
          {status.event_loop_stalls.recent.length ? (
            <ol className="command-failure-list" aria-label="Recent event-loop stalls">
              {status.event_loop_stalls.recent.slice(0, 8).map((stall) => (
                <li key={`${stall.observed_at}-${stall.duration_ms}`}>
                  <div>
                    <strong>{stall.duration_ms} ms</strong>{" "}
                    <small>{new Date(stall.observed_at).toLocaleTimeString()}</small>
                  </div>
                  <small>Blocked in {stall.blocked_in.split("/cueverge/").pop()}</small>
                  {stall.requests.length > 0 && <small>Requests in flight: {stall.requests.join(", ")}</small>}
                  {stall.other_threads.length > 0 && (
                    <small>Busy threads: {stall.other_threads.map((item) => item.split("/cueverge/").pop()).join("; ")}</small>
                  )}
                  <details>
                    <summary>Stack</summary>
                    <pre>{stall.loop_stack.join("\n")}</pre>
                  </details>
                </li>
              ))}
            </ol>
          ) : (
            <EmptyState title="No stalls recorded" description="LED rendering has not been delayed." />
          )}
        </Panel>
      )}
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
  const [search, setSearch] = useState("");
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
      {canManage && <details className="setup-disclosure"><summary>People &amp; project access</summary><LocalUsersPanel /></details>}
      <Panel
        title="Your installation"
        description="Name your display. Additional installation records are planning data, not extra connected controllers."
      >
        <div className="installation-records">
          {installations.filter((value) => value.runtime_active).map((value) =>
            <InstallationRecord key={value.id} installation={value} canManage={canManage} onRefresh={onRefresh} onError={setError} />)}
        </div>
        <details className="setup-disclosure"><summary>Additional installation records (advanced)</summary>
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
        <label>
          Search installations
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <div className="installation-records">
          {installations
            .filter((installation) => !installation.runtime_active)
            .filter((installation) =>
              [
                installation.name,
                installation.folder,
                ...installation.tags,
                ...installation.used_by_projects,
              ].some((value) =>
                value?.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
              ),
            )
            .map((installation) => (
            <InstallationRecord
              key={installation.id}
              installation={installation}
              canManage={canManage}
              onRefresh={onRefresh}
              onError={setError}
            />
            ))}
        </div>
        </details>
        {error && <p role="alert">{error}</p>}
      </Panel>
      <Panel
        title="Integrations"
        description="Connect home automation and assistants to this installation."
      >
        <IntegrationsPanel canManage={canManage} />
      </Panel>
    </>
  );
}

function LocalUsersPanel() {
  const [users, setUsers] = useState<LocalUser[]>([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setUsers(await getLocalUsers());
      setError(false);
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    setBusy(true);
    try {
      await putLocalUser(username.trim(), password, role);
      setUsername("");
      setPassword("");
      await load();
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      title="Local users"
      description="Create controller-managed identities, then grant project-specific roles from each show."
    >
      <div className="local-user-create">
        <label>
          Username
          <input
            autoComplete="off"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <label>
          Password
          <input
            autoComplete="new-password"
            minLength={12}
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        <label>
          Global role
          <select value={role} onChange={(event) => setRole(event.target.value as Role)}>
            <option value="viewer">Viewer</option>
            <option value="designer">Designer</option>
            <option value="operator">Operator</option>
            <option value="publisher">Publisher</option>
            <option value="administrator">Administrator</option>
          </select>
        </label>
        <button
          disabled={busy || !username.trim() || password.length < 12}
          onClick={() => void save()}
        >
          Save local user
        </button>
      </div>
      {error && <p role="alert">Local users could not be updated.</p>}
      <ul className="resource-list">
        {users.map((user) => (
          <li key={user.username}>
            <strong>{user.username}</strong>
            <span>
              {user.role}
              {user.disabled ? " · disabled" : ""}
            </span>
          </li>
        ))}
      </ul>
    </Panel>
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
  const [folder, setFolder] = useState(installation.folder ?? "");
  const [tags, setTags] = useState(installation.tags.join(", "));
  const [favorite, setFavorite] = useState(installation.favorite);
  const [saving, setSaving] = useState(false);
  const [recordOpen, setRecordOpen] = useState(false);
  const [recordError, setRecordError] = useState<string | null>(null);
  const recordDirty = name !== installation.name || folder !== (installation.folder ?? "") ||
    tags !== installation.tags.join(", ") || favorite !== installation.favorite;
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (recordOpen && recordDirty) event.preventDefault(); };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [recordOpen, recordDirty]);
  function closeRecord() {
    if (!saving && (!recordDirty || window.confirm("Discard unsaved installation settings?"))) setRecordOpen(false);
  }

  async function save() {
    setSaving(true);
    try {
      await updateInstallation(
        installation.id,
        installation.revision,
        name,
        folder || null,
        tags
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
        favorite,
      );
      await onRefresh();
      setRecordOpen(false);
    } catch {
      setRecordError("Installation update failed. Reopen saved settings before retrying if another user changed them.");
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
          {installation.runtime_active ? "This controller's installation" : "Planning record · no controller attached"}
        </small>
        <small>
          {installation.used_by_projects.length
            ? `Used by ${installation.used_by_projects.join(", ")}`
            : "Not used by a project"}
        </small>
      </div>
      {canManage && (
        <>
        <button type="button" onClick={() => {
          setName(installation.name); setFolder(installation.folder ?? "");
          setTags(installation.tags.join(", ")); setFavorite(installation.favorite);
          setRecordError(null); setRecordOpen(true);
        }}>Installation settings</button>
        {recordOpen && <DetailsDialog title="Installation settings" onClose={closeRecord}>
        <form className="installation-record__actions" onSubmit={(event) => { event.preventDefault(); void save(); }}>
          <label>
            Display name
            <input
              aria-label={`Name for ${installation.name}`}
              disabled={saving}
              maxLength={200}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <details><summary>Organization (optional)</summary>
          <input
            aria-label={`Folder for ${installation.name}`}
            disabled={saving}
            placeholder="Home/Outdoor"
            value={folder}
            onChange={(event) => setFolder(event.target.value)}
          />
          <input
            aria-label={`Tags for ${installation.name}`}
            disabled={saving}
            placeholder="primary, halloween"
            value={tags}
            onChange={(event) => setTags(event.target.value)}
          />
          <label>
            <input
              checked={favorite}
              disabled={saving}
              type="checkbox"
              onChange={(event) => setFavorite(event.target.checked)}
            />
            Favorite
          </label>
          </details>
          <button
            type="submit"
            disabled={saving || !name.trim() || !recordDirty}
          >
            Save
          </button>
          <button
            type="button"
            className="danger-button"
            disabled={saving || installation.runtime_active}
            onClick={() => void remove()}
          >
            Delete
          </button>
        </form>
        {recordError && <p role="alert">{recordError}</p>}
        </DetailsDialog>}
        </>
      )}
    </article>
  );
}
