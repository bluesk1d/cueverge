import {
  FormEvent,
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useState,
} from "react";

import {
  ApiError,
  ActivityEvent,
  applyScenePreset,
  AssetRecord,
  assetArtifactUrl,
  archiveProject,
  createInstallation,
  createProjectFromActive,
  createOutputArm,
  createScenePreset,
  deleteInstallation,
  duplicateProject,
  exportProject,
  getActivity,
  getAssets,
  getInstallation,
  getInstallations,
  getLocalUsers,
  getProjectDraft,
  getProjects,
  getResourceAssignments,
  getRuntimeStatus,
  getSession,
  getTopology,
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
      {workspace === "shows" && <ShowsPage session={session} />}
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

export function ShowsPage({
  session,
  initialProjects,
}: {
  session: Session;
  initialProjects?: ProjectSummary[];
}) {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(
    initialProjects ?? null,
  );
  const [error, setError] = useState(false);
  const [name, setName] = useState("");
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
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
    const document = JSON.parse(await file.text()) as ProjectExportDocument;
    await run(() => importProject(document));
  }

  const filteredProjects =
    projects?.filter((project) =>
      project.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
    ) ?? null;
  const activeProjects = filteredProjects?.filter((project) => !project.archived_at) ?? [];
  const archivedProjects = filteredProjects?.filter((project) => project.archived_at) ?? [];

  return (
    <>
      <WorkspaceHeader
        eyebrow="Authoring"
        title="Shows"
        description="Revisioned drafts remain isolated from the active show package."
      />
      {canAuthor && (
        <Panel
          title="Project actions"
          description="Create from Home or import a portable project document"
          className="project-actions-panel"
        >
          <div className="project-actions">
            <label>
              Project name
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
                  await createProjectFromActive(name);
                  setName("");
                })
              }
            >
              Create from Home
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
        </Panel>
      )}
      <Panel title="Draft projects">
        <label className="project-search">
          Search projects
          <input value={search} onChange={(event) => setSearch(event.target.value)} />
        </label>
        {error ? (
          <ErrorState />
        ) : projects === null ? (
          <Skeleton lines={3} />
        ) : filteredProjects?.length === 0 ? (
          <EmptyState
            title="No draft projects"
            description={
              projects.length
                ? "No projects match the current search."
                : "Create a revisioned project from Home or import one."
            }
          />
        ) : (
          <>
            <ProjectGroup
              title="Active"
              projects={activeProjects}
              session={session}
              busy={busy}
              run={run}
            />
            {archivedProjects.length > 0 && (
              <ProjectGroup
                title="Archived"
                projects={archivedProjects}
                session={session}
                busy={busy}
                run={run}
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
  session,
  busy,
  run,
}: {
  title: string;
  projects: ProjectSummary[];
  session: Session;
  busy: boolean;
  run: (action: () => Promise<void>) => Promise<void>;
}) {
  return (
    <section className="project-group" aria-label={`${title} projects`}>
      <h3>{title}</h3>
      <div className="project-grid">
        {projects.map((project) => (
          <ProjectCard
            key={project.id}
            project={project}
            session={session}
            busy={busy}
            run={run}
          />
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
}: {
  project: ProjectSummary;
  session: Session;
  busy: boolean;
  run: (action: () => Promise<void>) => Promise<void>;
}) {
  const [name, setName] = useState(project.name);
  const [assets, setAssets] = useState<AssetRecord[] | null>(null);
  const [draft, setDraft] = useState<ProjectDraftRecord | null>(null);
  const [assetSearch, setAssetSearch] = useState("");
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [assetError, setAssetError] = useState<string | null>(null);
  const [timelineLoaded, setTimelineLoaded] = useState(false);
  const [projectRole, setProjectRole] = useState<Role>(
    session.role === "administrator" ? "administrator" : "viewer",
  );
  const canAuthor = ["designer", "publisher", "administrator"].includes(projectRole);
  const canPublish = ["publisher", "administrator"].includes(projectRole);
  const updatePermissions = useCallback((role: Role) => {
    setProjectRole(role);
  }, []);

  const loadAssets = useCallback(async () => {
    try {
      const [loadedAssets, loadedDraft] = await Promise.all([
        getAssets(project.id),
        getProjectDraft(project.id),
      ]);
      setAssets(loadedAssets);
      setDraft(loadedDraft);
      setAssetError(null);
    } catch {
      setAssetError("Assets could not be loaded.");
    }
  }, [project.id]);

  useEffect(() => {
    void loadAssets();
  }, [loadAssets]);

  useEffect(() => {
    if (!assets?.some((asset) => asset.status === "uploading")) {
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

  return (
    <article className="project-card">
      <span>
        Draft r{project.head_revision} · Metadata r{project.metadata_revision}
      </span>
      {canAuthor && !project.archived_at ? (
        <input
          aria-label={`Name for ${project.name}`}
          disabled={busy}
          maxLength={200}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
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
        <input
          aria-label={`Search assets in ${project.name}`}
          placeholder="Search assets, folders, tags, owners…"
          type="search"
          value={assetSearch}
          onChange={(event) => setAssetSearch(event.target.value)}
        />
      </div>
      <div className="project-assets">
        <strong>Assets</strong>
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
            items={visibleAssets}
            keyFor={(asset) => asset.id}
            renderItem={(asset) => (
              <div className="virtual-asset-row">
                <AssetPreview asset={asset} />
                <div>
                  <strong>{asset.name}</strong>
                  <small>
                    {asset.original_filename ?? "Upload pending"} ·{" "}
                    {asset.size_bytes === null ? "—" : formatAssetBytes(asset.size_bytes)}
                  </small>
                  <small>
                    {asset.favorite ? "Favorite · " : ""}
                    {asset.folder ?? "Unfiled"}
                    {asset.tags.length ? ` · ${asset.tags.join(", ")}` : ""}
                  </small>
                  {asset.metadata && <small>{formatAssetMetadata(asset.metadata)}</small>}
                  <small>Malware scan: {asset.scan_status ?? "pending"}</small>
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
                      {asset.references
                        .map((reference) =>
                          reference.output_id
                            ? `${reference.scene_name} (${reference.output_id})`
                            : reference.scene_name,
                        )
                        .join(", ")}
                    </small>
                  )}
                  {asset.error && <small role="alert">{asset.error}</small>}
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
                </div>
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
              </div>
            )}
          />
        ) : assets?.length ? (
          <small>No assets match this search.</small>
        ) : (
          <small>No assets imported.</small>
        )}
      </div>
      {draft && (
        <>
          <Suspense fallback={<p>Loading collaboration workspace…</p>}>
            <CollaborationWorkspace
              projectId={project.id}
              revision={draft.revision}
              session={session}
              onPermissions={updatePermissions}
            />
          </Suspense>
          <Suspense fallback={<p>Loading MCP authoring workspace…</p>}>
            <McpWorkspace projectId={project.id} canDesign={canAuthor} />
          </Suspense>
          <ScenePresetOrganizer
            canEdit={canAuthor && !project.archived_at}
            draft={draft}
            projectId={project.id}
            onUpdated={setDraft}
          />
          <Suspense fallback={<p>Loading autonomous show designer…</p>}>
            <ShowLogicDesigner
              canEdit={canAuthor && !project.archived_at}
              draft={draft}
              projectId={project.id}
              onSave={async (document) => {
                setDraft(
                  await updateProjectDraft(project.id, draft.revision, document),
                );
              }}
            />
          </Suspense>
          {timelineLoaded ? (
            <Suspense fallback={<p>Loading timeline editor…</p>}>
              <TimelineEditor
                assets={assets ?? []}
                canEdit={canAuthor && !project.archived_at}
                draft={draft}
                initiallyOpen
                projectId={project.id}
                onSave={async (document) => {
                  setDraft(
                    await updateProjectDraft(project.id, draft.revision, document),
                  );
                }}
              />
            </Suspense>
          ) : (
            <details
              className="timeline-editor"
              onToggle={(event) => {
                if (event.currentTarget.open) {
                  setTimelineLoaded(true);
                }
              }}
            >
              <summary>Timeline editor</summary>
            </details>
          )}
          <Suspense fallback={<p>Loading publication workspace…</p>}>
            <PublicationWorkspace
              canPublish={canPublish && !project.archived_at}
              draft={draft}
              projectId={project.id}
            />
          </Suspense>
        </>
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

function AssetPreview({ asset }: { asset: AssetRecord }) {
  const hasReview = hasAssetArtifact(asset, "review");
  const hasThumbnail = hasAssetArtifact(asset, "thumbnail");
  const hasWaveform = hasAssetArtifact(asset, "waveform");
  if (asset.media_type?.startsWith("video/") && hasReview) {
    return (
      <div className="asset-preview asset-preview--video">
        <video
          controls
          poster={hasThumbnail ? assetArtifactUrl(asset.id, "thumbnail") : undefined}
          preload="metadata"
          src={assetArtifactUrl(asset.id, "review")}
        >
          Review proxy playback is unsupported by this browser.
        </video>
        {hasWaveform && (
          <img
            alt={`Waveform for ${asset.name}`}
            src={assetArtifactUrl(asset.id, "waveform")}
          />
        )}
      </div>
    );
  }
  if (asset.media_type?.startsWith("audio/") && hasReview) {
    return (
      <div className="asset-preview asset-preview--audio">
        {hasWaveform && (
          <img
            alt={`Waveform for ${asset.name}`}
            src={assetArtifactUrl(asset.id, "waveform")}
          />
        )}
        <audio controls preload="metadata" src={assetArtifactUrl(asset.id, "review")}>
          Review proxy playback is unsupported by this browser.
        </audio>
      </div>
    );
  }
  if (asset.media_type?.startsWith("image/") && hasReview) {
    return (
      <img
        alt={`Review proxy for ${asset.name}`}
        className="asset-preview"
        loading="lazy"
        src={assetArtifactUrl(asset.id, "review")}
      />
    );
  }
  return <div className="asset-preview asset-preview--pending" aria-hidden="true" />;
}

function hasAssetArtifact(asset: AssetRecord, kind: string): boolean {
  const artifacts = asset.metadata?.artifacts;
  return (
    typeof artifacts === "object" &&
    artifacts !== null &&
    !Array.isArray(artifacts) &&
    kind in artifacts
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
  const [armConfirmation, setArmConfirmation] = useState("");
  const [outputArm, setOutputArm] = useState<OutputArm | null>(null);
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
      {canManage && <LocalUsersPanel />}
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
        {error && <p role="alert">{error}</p>}
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
        <small>
          {installation.favorite ? "Favorite · " : ""}
          {installation.folder ?? "Unfiled"}
          {installation.tags.length ? ` · ${installation.tags.join(", ")}` : ""}
        </small>
        <small>
          {installation.used_by_projects.length
            ? `Used by ${installation.used_by_projects.join(", ")}`
            : "Not used by a project"}
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
