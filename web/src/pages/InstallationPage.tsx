import { useCallback, useEffect, useRef, useState } from "react";
import {
  agentSoftwareLabel,
  agentUpdateLabel,
  connectionStatus,
  keepAwakeLabel,
  keepAwakeWarning,
} from "../status";
import { InputSourcePanel } from "./InputSourcePanel";
import { InstallationObjects } from "./InstallationObjects";
import type { CSSProperties } from "react";
import { OutputTraversalPreview } from "../components/OutputTraversalPreview";
import { GeometryCanvas } from "../components/GeometryCanvas";
import { InstallationPhotoCard } from "../components/InstallationPhotoCard";
import { AgentSettingsPanel } from "../components/AgentSettingsPanel";
import { DetailsDialog } from "../components/DetailsDialog";
import { StageMapPanel } from "../components/StageMapPanel";
import { mapperDiagnostics } from "../components/mapper-diagnostics";
import { geometryForBackground, photoPixelPoints } from "../components/pixel-traversal";
import {
  Cable,
  Clock3,
  Lightbulb,
  MonitorPlay,
  Network,
  Server,
} from "lucide-react";

import {
  AgentUpdates,
  AssetRecord,
  assetArtifactUrl,
  deleteInstallationGeometry,
  createPairingToken,
  ArtifactCacheCapability,
  DeviceCapabilityDocument,
  DiscoveredEndpoint,
  getDiscoveredEndpoints,
  getInstallationPhotos,
  getInstallationGeometry,
  getImageAssetLibrary,
  getProjectorMapping,
  projectorSimulationOverlayUrl,
  getLightingSurface,
  getProjectorOverlaps,
  getGeometrySuggestions,
  getStageCoverage,
  getStageMap,
  installationPhotoUrl,
  InstallationPhoto,
  InstallationGeometry,
  InstallationGeometryKind,
  ProjectorMapping,
  LightingSurface,
  ProjectorOverlap,
  GeometrySuggestion,
  StageCoverage,
  StageMap,
  MaintenancePreview,
  InstallationStatus,
  ObjectLayoutSelection,
  MediaPlaybackCapability,
  PairingToken,
  removeResourceAssignment,
  rejectDiscoveredEndpoint,
  reviewDeviceCapabilities,
  CapabilityReview,
  ResourceAssignment,
  Session,
  setDeviceConnectionPolicy,
  controlDevicePlayer,
  getAgentUpdates,
  startAgentUpdates,
  UPDATABLE_AGENT_STATES,
  setDeviceEnrollmentState,
  setResourceAssignment,
  testDeviceConnection,
  saveInstallationGeometry,
  saveProjectorMapping,
  saveLightingSurface,
  saveProjectorOverlap,
  startMaintenancePreview,
  startTimingCalibration,
  adjustTimingCalibration,
  finishTimingCalibration,
  TimingCalibrationSession,
  AcceptedCalibration,
  reviewAcceptedCalibrations,
  reviewGeometrySuggestion,
  uploadInstallationPhoto,
  probeEndpoint,
  Topology,
  TopologyNode,
  TimingCalibrationCapability,
  TimingAdjustment,
} from "../api";
import {
  EmptyState,
  ErrorState,
  Panel,
  Skeleton,
  StatusText,
  StatusTone,
  WorkspaceHeader,
} from "../components/ui";

export function InstallationPage({
  installation,
  topology,
  assignments = [],
  installationError,
  topologyError,
  loading,
  session,
  onRefresh,
  onDirtyChange,
  initialSection = "photos",
  initialProjectId = "",
}: {
  installation: InstallationStatus | null;
  topology: Topology | null;
  assignments?: ResourceAssignment[];
  installationError: boolean;
  topologyError: boolean;
  loading: boolean;
  session?: Session;
  onRefresh?: () => Promise<void> | void;
  onDirtyChange?: (dirty: boolean) => void;
  initialSection?: "photos" | "objects" | "devices" | "inputs";
  initialProjectId?: string;
}) {
  const [section, setSection] = useState(initialSection);
  const [inputDirty, setInputDirty] = useState(false);
  const [layoutDirty, setLayoutDirty] = useState(false);
  const [objectDirty, setObjectDirty] = useState(false);
  const [objectProjectId, setObjectProjectId] = useState(initialProjectId);
  const [layoutSelection, setLayoutSelection] = useState<ObjectLayoutSelection | null>(null);
  useEffect(() => {
    onDirtyChange?.(inputDirty || layoutDirty || objectDirty);
  }, [inputDirty, layoutDirty, objectDirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);
  const controller = topology?.nodes.find((node) => node.kind === "controller");
  const agents =
    topology?.nodes.filter(
      (node) => node.kind === "direct_media_agent" || node.kind === "downstream_controller",
    ) ?? [];
  const directAgents = agents.filter((node) => node.kind === "direct_media_agent");
  const downstreamControllers = agents.filter((node) => node.kind === "downstream_controller");
  const outputs = topology?.nodes.filter((node) => node.kind === "controller_output") ?? [];

  return (
    <>
      <WorkspaceHeader
        eyebrow="Installation"
        title="Your installation"
        description="Connect camera events and manage the devices, photos, and layout of your display."
        meta={
          topology && (
            <div className="freshness">
              <Clock3 aria-hidden="true" size={15} />
              <span>
                Topology observed{" "}
                <time dateTime={topology.observed_at}>
                  {new Date(topology.observed_at).toLocaleTimeString()}
                </time>
              </span>
            </div>
          )
        }
      />
      <div className="setup-tabs" role="group" aria-label="Installation workspace">
        {(["photos", "objects", "devices", "inputs"] as const).map((value) => <button key={value} aria-pressed={section === value} onClick={() => {
          if (value === section) return;
          if ((inputDirty || layoutDirty || objectDirty) && !window.confirm("Discard unsaved installation changes and change section?")) return;
          setInputDirty(false); setLayoutDirty(false); setObjectDirty(false); setSection(value);
        }}>{value === "photos" ? "Photos & Layout" : value === "objects" ? "Objects" : value === "devices" ? "Devices" : "Inputs & connections"}</button>)}
      </div>
      {section === "objects" && <InstallationObjects session={session} projectId={objectProjectId}
        onProjectChange={setObjectProjectId} onDirtyChange={setObjectDirty}
        onLayout={(selection) => { setLayoutSelection(selection); setSection("photos"); }} />}
      {section === "inputs" && <InputSourcePanel session={session} onDirtyChange={setInputDirty} />}
      {section === "devices" && <>
      <details className="setup-disclosure"><summary>Connection overview</summary>
      <section className="topology-summary" aria-label="Topology summary">
        <Summary
          label="Controller"
          value={controller ? "Observed" : topologyError ? "Unavailable" : "Loading"}
          detail="Local runtime process"
          tone={controller ? "success" : topologyError ? "danger" : "neutral"}
        />
        <Summary
          label="Direct media agents"
          value={topology ? String(directAgents.length) : "—"}
          detail={`${directAgents.filter((node) => node.connection === "connected").length} connected`}
          tone={
            directAgents.some((node) => node.connection === "disconnected") ? "warning" : "info"
          }
        />
        <Summary
          label="Controller outputs"
          value={topology ? String(outputs.length) : "—"}
          detail={`${outputs.filter((node) => node.connection === "connected").length} connected`}
          tone={outputs.some((node) => node.connection === "disconnected") ? "warning" : "info"}
        />
        <Summary
          label="Downstream controllers"
          value={topology ? String(downstreamControllers.length) : "—"}
          detail={`${downstreamControllers.filter((node) => node.connection === "connected").length} connected`}
          tone={
            downstreamControllers.some((node) => node.connection === "disconnected")
              ? "warning"
              : "neutral"
          }
        />
      </section>
      </details>

      <div className="topology-layout topology-layout--compact">
        <Panel
          title="Devices"
          description="Connections reported by your controller. Open details only when you need them."
          className="topology-layout__map"
        >
          {topologyError ? (
            <ErrorState description="The controller topology could not be observed." />
          ) : loading && !topology ? (
            <Skeleton lines={6} />
          ) : controller ? (
            <div className="topology-tree">
              <NodeCard node={controller} />
              <div className="topology-tree__children">
                {agents.map((node) => (
                  <NodeCard
                    key={node.id}
                    node={node}
                    canOperate={session?.role === "administrator" || session?.role === "operator"}
                    canAdminister={session?.role === "administrator"}
                    onRefresh={onRefresh}
                  />
                ))}
                {outputs.map((node) => (
                  <NodeCard key={node.id} node={node} />
                ))}
              </div>
            </div>
          ) : (
            <EmptyState
              title="No topology available"
              description="The controller returned no topology nodes."
            />
          )}
        </Panel>

        <details className="setup-disclosure"><summary>Configured outputs and logical resources</summary>
        <Panel
          title="Installation Package"
          description="Logical resources in the active runtime package"
          className="topology-layout__resources"
        >
          {installationError ? (
            <ErrorState description="The active package resources could not be loaded." />
          ) : loading && !installation ? (
            <Skeleton lines={4} />
          ) : installation?.resources.length ? (
            <ul className="resource-list">
              {installation.resources.map((resource) => (
                <li key={resource.resource}>
                  <div>
                    <strong>{resource.resource}</strong>
                    <small>{humanize(resource.kind)}</small>
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
        </details>
      </div>
      {(session?.role === "administrator" || session?.role === "operator") && topology && (
        <DeviceManagement agents={agents} installation={installation} assignments={assignments}
          enrollmentSupported={topology.enrollment_supported}
          canAdminister={session.role === "administrator"} onRefresh={onRefresh} />
      )}
      </>}
      {section === "photos" && installation && (
        <InstallationPhotos
          selection={layoutSelection}
          installationId={installation.installation_id}
          projectorResources={installation.resources
            .filter((resource) => resource.kind === "media_agent")
            .map((resource) => resource.resource)}
          pixelResources={installation.resources.filter(
            (resource) => resource.kind === "pixel_output",
          )}
          canEdit={
            session !== undefined &&
            ["designer", "publisher", "administrator"].includes(session.role)
          }
          canPreview={
            session !== undefined &&
            ["operator", "administrator"].includes(session.role)
          }
          onDirtyChange={setLayoutDirty}
        />
      )}
    </>
  );
}

function InstallationPhotos({
  selection,
  installationId,
  projectorResources,
  pixelResources,
  canEdit,
  canPreview,
  onDirtyChange,
}: {
  selection: ObjectLayoutSelection | null;
  installationId: string;
  projectorResources: string[];
  pixelResources: InstallationStatus["resources"];
  canEdit: boolean;
  canPreview: boolean;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const [photos, setPhotos] = useState<InstallationPhoto[]>([]);
  const [geometry, setGeometry] = useState<InstallationGeometry[]>([]);
  const [imageAssets, setImageAssets] = useState<AssetRecord[]>([]);
  const [overlaps, setOverlaps] = useState<ProjectorOverlap[]>([]);
  const [suggestions, setSuggestions] = useState<GeometrySuggestion[]>([]);
  const [stageCoverage, setStageCoverage] = useState<StageCoverage | null>(null);
  const [stageMap, setStageMap] = useState<StageMap | null>(null);
  const [stageMapFailed, setStageMapFailed] = useState(false);
  const [addingPhoto, setAddingPhoto] = useState(false);
  const [name, setName] = useState("");
  const [layoutPhotoId, setLayoutPhotoId] = useState<string | null>(null);
  const [layoutOpen, setLayoutOpen] = useState(false);
  const [layoutVisited, setLayoutVisited] = useState(false);
  const [layoutRequest, setLayoutRequest] = useState(0);
  const [layoutChanged, setLayoutChanged] = useState(false);
  const layout = useRef<HTMLDetailsElement>(null);
  const updateLayoutDirty = useCallback((value: boolean) => {
    setLayoutChanged(value); onDirtyChange(value);
  }, [onDirtyChange]);
  useEffect(() => {
    if (layoutOpen && layoutPhotoId !== null) layout.current?.scrollIntoView({ block: "start" });
  }, [layoutOpen, layoutRequest, layoutPhotoId]);
  const [busy, setBusy] = useState(false);
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const photoFileInput = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [photoLoadFailed, setPhotoLoadFailed] = useState(false);
  const [photosLoaded, setPhotosLoaded] = useState(false);
  const openedSelection = useRef<ObjectLayoutSelection | null>(null);
  useEffect(() => {
    if (!selection || !photosLoaded || openedSelection.current === selection) return;
    openedSelection.current = selection;
    const shape = geometry.find((item) => selection.geometryIds.includes(item.id));
    setLayoutPhotoId(shape?.photo_id ?? "");
    setLayoutVisited(true); setLayoutOpen(true);
    setLayoutRequest((value) => value + 1);
  }, [selection, geometry, photosLoaded]);

  async function load() {
      const [loadedPhotos, loadedGeometry, loadedImages, loadedOverlaps, loadedSuggestions, loadedCoverage, loadedStageMap] = await Promise.allSettled([
        getInstallationPhotos(installationId),
        getInstallationGeometry(installationId),
        getImageAssetLibrary(),
        getProjectorOverlaps(installationId),
        getGeometrySuggestions(installationId),
        getStageCoverage(installationId),
        getStageMap(installationId),
      ]);
      const failures: string[] = [];
      if (loadedPhotos.status === "fulfilled") { setPhotos(loadedPhotos.value); setPhotosLoaded(true); setPhotoLoadFailed(false); }
      else { setPhotoLoadFailed(true); failures.push("Photos could not be refreshed."); }
      if (loadedGeometry.status === "fulfilled") setGeometry(loadedGeometry.value);
      else failures.push("Layout objects could not be refreshed.");
      if (loadedImages.status === "fulfilled") setImageAssets(loadedImages.value);
      else failures.push("Image library details are unavailable.");
      if (loadedOverlaps.status === "fulfilled") setOverlaps(loadedOverlaps.value);
      else failures.push("Projector overlap details are unavailable.");
      if (loadedSuggestions.status === "fulfilled") setSuggestions(loadedSuggestions.value);
      else failures.push("Layout suggestions are unavailable.");
      if (loadedCoverage.status === "fulfilled") setStageCoverage(loadedCoverage.value);
      else failures.push("Stage coverage details are unavailable.");
      if (loadedStageMap.status === "fulfilled" && loadedStageMap.value && "counts" in loadedStageMap.value) { setStageMap(loadedStageMap.value); setStageMapFailed(false); }
      else setStageMapFailed(true);
      setError(failures.join(" ") || null);
  }

  useEffect(() => {
    void load();
  }, [installationId]);

  async function upload(file: File) {
    setBusy(true);
    setError(null);
    try {
      await uploadInstallationPhoto(
        installationId,
        file,
        name || file.name.replace(/\.[^.]+$/, ""),
      );
      setName("");
      setPhotoFile(null);
      if (photoFileInput.current) photoFileInput.current.value = "";
      setAddingPhoto(false);
      await load();
    } catch {
      setError("Installation photo upload failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="installation-design-stack">
      <Panel
        title="Installation Photos"
        description="Pick the photo you want to arrange lights and decorations on. Photos are optional backgrounds."
      >
        <div className="setup-actions installation-photos__actions">
          {canEdit && <button type="button" className="primary-button" onClick={() => setAddingPhoto(true)}>Add photo</button>}
          <button type="button" onClick={() => {
            if (layoutChanged && !window.confirm("Discard unsaved layout changes and open the installation canvas?")) return;
            setLayoutPhotoId(""); setLayoutVisited(true); setLayoutOpen(true);
            setLayoutRequest((value) => value + 1);
          }}>Open layout without a photo</button>
        </div>
        {layoutChanged && <p role="status">Save or cancel layout edits before changing an existing photo.</p>}
        {canEdit && addingPhoto && (
          <DetailsDialog title="Add photo" closeDisabled={busy} onClose={() => {
            setAddingPhoto(false); setPhotoFile(null); setName("");
          }}>
            <form className="photo-upload" onSubmit={(event) => { event.preventDefault(); if (photoFile) void upload(photoFile); }}>
              <label className="file-button">
                Choose photo
                <input
                  ref={photoFileInput}
                  aria-label="Photo file"
                  accept="image/jpeg,image/png,image/webp"
                  disabled={busy}
                  type="file"
                  onChange={(event) => {
                    const file = event.target.files?.[0] ?? null;
                    setPhotoFile(file);
                    if (file && !name.trim()) setName(file.name.replace(/\.[^.]+$/, ""));
                  }}
                />
              </label>
              {photoFile && <small>{photoFile.name}</small>}
              <label>Photo description
              <input
                aria-label="Photo description"
                disabled={busy}
                required maxLength={200}
                placeholder="For example: House front from the sidewalk"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
              </label>
              <small>A photo taken from where visitors stand works best as the stage view for shared effects.</small>
              <button type="submit" className="primary-button" disabled={busy || !photoFile || !name.trim()}>{busy ? "Adding…" : "Add installation photo"}</button>
            </form>
            {error && <p role="alert">{error}</p>}
          </DetailsDialog>
        )}
        {error && !addingPhoto && <><p role="alert">{error}</p><button onClick={() => void load()}>Retry installation data</button></>}
        {photos.length ? (
          <div className="installation-photos installation-photos--strip">
            {photos.map((photo) => (
              <InstallationPhotoCard
                key={photo.id}
                photo={photo}
                canEdit={canEdit && !layoutChanged}
                onRefresh={load}
                mappedCount={geometry.filter((item) => item.photo_id === photo.id).length}
                isStageView={stageMap?.reference?.photo_id === photo.id}
                selected={layoutOpen && layoutPhotoId === photo.id}
                onLayout={() => {
                  if (layoutChanged && photo.id !== layoutPhotoId && !window.confirm("Discard unsaved layout changes and open another photo?")) return;
                  setLayoutPhotoId(photo.id); setLayoutVisited(true); setLayoutOpen(true); setLayoutRequest((value) => value + 1);
                }}
              />
            ))}
          </div>
        ) : photoLoadFailed ? <ErrorState title="Photo library unavailable" description="Retry loading photos before adding or arranging them." /> : !photosLoaded ? <p>Loading photos…</p> : (
          <EmptyState
            title="No installation photos"
            description="Photos are optional reference backgrounds. Open the layout canvas to arrange your installation without one."
          />
        )}
      </Panel>
      {photosLoaded && photos.length > 0 && (
        <StageMapPanel
          installationId={installationId}
          photos={photos}
          stageMap={stageMap}
          loadError={stageMapFailed}
          canEdit={canEdit && !layoutChanged}
          onChanged={load}
        />
      )}
      {photosLoaded && (
        <details ref={layout} className="installation-layout" open={layoutOpen}
          onToggle={(event) => {
            setLayoutOpen(event.currentTarget.open);
            if (event.currentTarget.open) setLayoutVisited(true);
          }}>
        <summary>Layout</summary>
        {layoutVisited && <>
        <Panel
          title="Visual Mapper"
          description="Select a decoration or choose Add. Installation geometry and wiring are independent of optional photo backgrounds."
        >
          <GeometryMapper
            selection={selection}
            installationId={installationId}
            projectorResources={projectorResources}
            pixelResources={pixelResources}
            photos={photos}
            initialPhotoId={layoutPhotoId}
            openRequest={layoutRequest}
            onDirtyChange={updateLayoutDirty}
            geometry={geometry}
            imageAssets={imageAssets}
            overlaps={overlaps}
            suggestions={suggestions}
            stageCoverage={stageCoverage}
            stageMap={stageMap}
            canEdit={canEdit}
            canPreview={canPreview}
            onRefresh={load}
          />
        </Panel>
        </>}
        </details>
      )}
    </div>
  );
}

const GEOMETRY_KINDS: InstallationGeometryKind[] = [
  "output",
  "pixel_segment",
  "fixture",
  "path",
  "projector_surface",
  "mask",
  "audience_zone",
  "safety_zone",
];

type SimulationPreviewMode =
  | "logical_composition"
  | "parent_context"
  | "native_projector"
  | "selected_output"
  | "full_installation";

function GeometryMapper({
  selection,
  installationId,
  projectorResources,
  pixelResources,
  photos,
  geometry,
  imageAssets,
  overlaps,
  suggestions,
  stageCoverage,
  stageMap,
  canEdit,
  canPreview,
  onRefresh,
  initialPhotoId,
  openRequest,
  onDirtyChange,
}: {
  selection: ObjectLayoutSelection | null;
  installationId: string;
  projectorResources: string[];
  pixelResources: InstallationStatus["resources"];
  photos: InstallationPhoto[];
  geometry: InstallationGeometry[];
  imageAssets: AssetRecord[];
  overlaps: ProjectorOverlap[];
  suggestions: GeometrySuggestion[];
  stageCoverage: StageCoverage | null;
  stageMap: StageMap | null;
  canEdit: boolean;
  canPreview: boolean;
  onRefresh: () => Promise<void>;
  initialPhotoId: string | null;
  openRequest: number;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const [photoId, setPhotoId] = useState(initialPhotoId ?? photos[0]?.id ?? "");
  const [adding, setAdding] = useState(false);
  const [changed, setChanged] = useState(false);
  const [savingGeometry, setSavingGeometry] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  useEffect(() => {
    onDirtyChange(changed);
    const guard = (event: BeforeUnloadEvent) => { if (changed) event.preventDefault(); };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [changed, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  useEffect(() => {
    if (initialPhotoId !== null && initialPhotoId !== undefined && initialPhotoId !== photoId) { reset(); setPhotoId(initialPhotoId); }
  }, [initialPhotoId, openRequest]);
  useEffect(() => {
    if (photoId && !photos.some((value) => value.id === photoId)) { reset(); setPhotoId(""); }
  }, [photos, photoId]);
  const [kind, setKind] = useState<InstallationGeometryKind>("projector_surface");
  const [name, setName] = useState("");
  const [points, setPoints] = useState<{ x: number; y: number }[]>([]);
  const [editing, setEditing] = useState<InstallationGeometry | undefined>();
  const [linkedSemanticId, setLinkedSemanticId] = useState<string | null>(null);
  const [color, setColor] = useState("#6ee7ff");
  const [labelVisible, setLabelVisible] = useState(true);
  const [locked, setLocked] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [zIndex, setZIndex] = useState(0);
  const [direction, setDirection] = useState<"forward" | "reverse">("forward");
  const [startIndex, setStartIndex] = useState("");
  const [pixelCount, setPixelCount] = useState("");
  const [creationMethod, setCreationMethod] = useState<"trace" | "generate">("trace");
  const [allocationResources, setAllocationResources] = useState<Record<string, string>>({});
  const [allocationCheckError, setAllocationCheckError] = useState(false);
  const [allocationLoading, setAllocationLoading] = useState(false);
  const [stagePoints, setStagePoints] = useState("");
  const [traversalPreview, setTraversalPreview] = useState(false);
  const [simulationOverlay, setSimulationOverlay] = useState(false);
  const [coverageOverlay, setCoverageOverlay] = useState(false);
  const [simulationPreviewMode, setSimulationPreviewMode] =
    useState<SimulationPreviewMode>("full_installation");
  const [simulationTarget, setSimulationTarget] = useState("");
  const [sideBySideSimulation, setSideBySideSimulation] = useState(false);
  const [simulationMappings, setSimulationMappings] = useState<
    Record<string, ProjectorMapping | null>
  >({});
  const [simulationMappingsLoading, setSimulationMappingsLoading] = useState(false);
  const [secondaryPhotoId, setSecondaryPhotoId] = useState(
    photos[1]?.id ?? photos[0]?.id ?? "",
  );
  const [pixelGenerator, setPixelGenerator] = useState<
    "row" | "grid" | "ring" | "outline" | "individual" | "import"
  >("row");
  const [generatorRows, setGeneratorRows] = useState(4);
  const [generatorColumns, setGeneratorColumns] = useState(10);
  const [importedPoints, setImportedPoints] = useState("");
  const [timingSession, setTimingSession] =
    useState<TimingCalibrationSession | null>(null);
  const [timingView, setTimingView] = useState<"baseline" | "proposed">("proposed");
  const [acceptedCalibrations, setAcceptedCalibrations] = useState<
    AcceptedCalibration[]
  >([]);
  const [calibrationProvenance, setCalibrationProvenance] = useState({
    test_asset_id: "",
    test_asset_version: "",
    observed_variability_ms: "",
    notes: "",
    hardware: "",
    routing: "",
    display_mode: "",
    media_settings: "",
    environment: "",
  });
  const [overlapSecondId, setOverlapSecondId] = useState("");
  const [overlapPreviewMode, setOverlapPreviewMode] =
    useState<"native" | "mapped">("mapped");
  const [overlapValues, setOverlapValues] = useState({
    stage_points: [
      [0, 0],
      [1, 0],
      [1, 1],
      [0, 1],
    ] as [number, number][],
    ramp_width: 0.2,
    overlap_gamma: 1,
    first_brightness: 1,
    second_brightness: 1,
    first_white: [1, 1, 1] as [number, number, number],
    second_white: [1, 1, 1] as [number, number, number],
    first_color_matrix: [1, 0, 0, 0, 1, 0, 0, 0, 1],
    second_color_matrix: [1, 0, 0, 0, 1, 0, 0, 0, 1],
    first_black_level: 0,
    second_black_level: 0,
  });
  const selectedOverlap = overlaps.find(
    (item) =>
      item.first_geometry_id === editing?.id &&
      item.second_geometry_id === overlapSecondId,
  );
  const [snap, setSnap] = useState(true);
  const [mapping, setMapping] = useState<ProjectorMapping | undefined>();
  const [lightingSurface, setLightingSurface] = useState<LightingSurface | undefined>();
  const [alignmentPreview, setAlignmentPreview] = useState<MaintenancePreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [mappingValues, setMappingValues] = useState({
    resource: projectorResources[0] ?? "",
    native_width: 1920,
    native_height: 1080,
    stage_placement: "surface" as "surface" | "manual",
    stage_x: 0,
    stage_y: 0,
    stage_width: 4,
    stage_height: 2.25,
    crop_x: 0,
    crop_y: 0,
    crop_width: 1,
    crop_height: 1,
    content_asset_id: null as string | null,
    surface_model: "planar" as "planar" | "subdivided",
    fit_mode: "preserve" as "preserve" | "stretch",
    overscan: false,
    brightness_limit: 1,
    media_start_delay_ms: 0,
    media_release_delay_ms: 0,
    orientation_degrees: 0 as 0 | 90 | 180 | 270,
    mirror_x: false,
    mirror_y: false,
    mask_semantic_ids: [] as string[],
    calibration_source: "unverified_estimate" as
      | "measured_grid"
      | "easy_corner_pin"
      | "unverified_estimate",
    calibration_revision: 1,
    mesh_vertices: [] as [number, number, number, number][],
    mesh_triangles: [] as [number, number, number][],
  });
  const [lightingValues, setLightingValues] = useState({
    resource: pixelResources[0]?.resource ?? "",
    local_width: 1,
    local_height: 1,
    local_points: [[0, 0], [1, 1]] as [number, number][],
    stage_matrix: [1, 0, 0, 1, 0, 0] as [
      number,
      number,
      number,
      number,
      number,
      number,
    ],
    fixture_id: "",
    group_id: "",
    path_id: "",
    strand_id: "",
    calibration_id: "",
    pixel_footprint_width: 0.01,
    pixel_footprint_height: 0.01,
    brightness_limit: 1,
    timing_offset_ms: 0,
    release_offset_ms: 0,
    pixel_overrides: [] as {
      output_index: number;
      local_x: number;
      local_y: number;
    }[],
  });
  const [pixelCorrectionIndex, setPixelCorrectionIndex] = useState(0);
  const [pixelCorrectionX, setPixelCorrectionX] = useState(0);
  const [pixelCorrectionY, setPixelCorrectionY] = useState(0);
  const photo = photos.find((candidate) => candidate.id === photoId);
  const secondaryPhoto =
    photos.find(
      (candidate) =>
        candidate.id === secondaryPhotoId && candidate.id !== photo?.id,
    ) ??
    photos.find((candidate) => candidate.id !== photo?.id);
  const highlightedSemantics = new Set(geometry.filter((item) =>
    selection?.geometryIds.includes(item.id)).map((item) => `${item.kind}:${item.semantic_id}`));
  const highlighted = (item: InstallationGeometry) => highlightedSemantics.has(`${item.kind}:${item.semantic_id}`);
  const visible = geometryForBackground(geometry, photo?.id ?? null)
    .filter((item) => !item.hidden || highlighted(item))
    .sort((left, right) => left.z_index - right.z_index);
  const semanticTargets = [...new Set(visible.map((item) => item.semantic_id))];
  const simulationGeometry = filterSimulationGeometry(
    visible,
    simulationPreviewMode,
    simulationTarget,
    projectorResources,
  );
  const secondarySimulationGeometry = secondaryPhoto
    ? filterSimulationGeometry(
        geometryForBackground(geometry, secondaryPhoto.id)
          .filter((item) => !item.hidden)
          .sort((left, right) => left.z_index - right.z_index),
        simulationPreviewMode,
        simulationTarget,
        projectorResources,
      )
    : [];
  const primarySimulationStatus = projectorSimulationStatus(
    simulationGeometry,
    photo,
    simulationMappings,
    simulationMappingsLoading,
  );
  const secondarySimulationStatus = projectorSimulationStatus(
    secondarySimulationGeometry,
    secondaryPhoto,
    simulationMappings,
    simulationMappingsLoading,
  );
  const visibleSuggestions = suggestions.filter(
    (suggestion) =>
      suggestion.photo_id === photo?.id && suggestion.state === "pending",
  );
  const closed = ["output", "projector_surface", "mask", "audience_zone", "safety_zone"].includes(
    kind,
  );
  const diagnostics = mapperDiagnostics(geometry, allocationResources, photo?.id);
  const firstPixel = Number(startIndex);
  const numberOfPixels = Number(pixelCount);
  const selectedPixelOutput = pixelResources.find(
    (resource) => resource.resource === lightingValues.resource,
  );
  const validPixelRange =
    startIndex.trim() !== "" &&
    pixelCount.trim() !== "" &&
    Number.isSafeInteger(firstPixel) &&
    firstPixel >= 0 &&
    Number.isSafeInteger(numberOfPixels) &&
    numberOfPixels > 0 &&
    Number.isSafeInteger(firstPixel + numberOfPixels);
  const exceedsOutput =
    validPixelRange &&
    selectedPixelOutput?.pixel_count != null &&
    firstPixel + numberOfPixels > selectedPixelOutput.pixel_count;
  const overlappingSegments = validPixelRange
    ? geometry.filter(
        (item, index, all) =>
          item.kind === "pixel_segment" &&
          item.semantic_id !== (editing?.semantic_id ?? linkedSemanticId) &&
          item.start_index !== null &&
          item.pixel_count !== null &&
          allocationResources[item.id] === lightingValues.resource &&
          all.findIndex(
            (candidate) => candidate.semantic_id === item.semantic_id,
          ) === index &&
          firstPixel < item.start_index + item.pixel_count &&
          firstPixel + numberOfPixels > item.start_index,
      )
    : [];
  const canGenerate =
    pixelGenerator === "individual" ||
    (pixelGenerator === "import"
      ? generateNormalizedPixelLayout("import", 0, 0, importedPoints).length > 0
      : pixelGenerator === "grid"
        ? Number.isInteger(generatorRows) && generatorRows >= 2 && generatorRows <= 100 &&
          Number.isInteger(generatorColumns) && generatorColumns >= 2 && generatorColumns <= 500
        : Number.isSafeInteger(numberOfPixels) && numberOfPixels >= 2 &&
          (pixelGenerator !== "ring" || numberOfPixels <= 500));

  useEffect(() => {
    let cancelled = false;
    setAllocationLoading(true);
    setAllocationCheckError(false);
    void Promise.all(
      geometry
        .filter((item) => item.kind === "pixel_segment")
        .map(async (item) => {
          try {
            return [item.id, (await getLightingSurface(item.id)).resource] as const;
          } catch {
            if (!cancelled) {
              setAllocationCheckError(true);
            }
            return [item.id, ""] as const;
          }
        }),
    ).then((entries) => {
      if (!cancelled) {
        setAllocationResources(Object.fromEntries(entries));
        setAllocationLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [geometry]);

  useEffect(() => {
    if (!simulationOverlay) {
      setSimulationMappings({});
      setSimulationMappingsLoading(false);
      return;
    }
    const projectorIds = [
      ...new Set(
        geometry
          .filter((item) => item.kind === "projector_surface" && !item.hidden)
          .map((item) => item.id),
      ),
    ];
    let cancelled = false;
    setSimulationMappingsLoading(true);
    void Promise.all(
      projectorIds.map(async (geometryId) => {
        try {
          return [geometryId, await getProjectorMapping(geometryId)] as const;
        } catch {
          return [geometryId, null] as const;
        }
      }),
    ).then((entries) => {
      if (!cancelled) {
        setSimulationMappings(Object.fromEntries(entries));
        setSimulationMappingsLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [geometry, simulationOverlay]);

  async function beginEdit(item: InstallationGeometry) {
    if (editing?.id === item.id && !adding) return;
    if (changed && !window.confirm("Discard unsaved layout changes and select this object?")) return;
    setAdding(false); setChanged(false); setSaveError(null);
    setTraversalPreview(false);
    setCreationMethod("trace");
    setEditing(item);
    if (item.photo_id !== null) setPhotoId(item.photo_id);
    setKind(item.kind);
    setName(item.name);
    setPoints(item.points);
    setLinkedSemanticId(item.semantic_id);
    setColor(item.color);
    setLabelVisible(item.label_visible);
    setLocked(item.locked);
    setHidden(item.hidden);
    setZIndex(item.z_index);
    setDirection(item.direction);
    setStartIndex(item.start_index?.toString() ?? "");
    setPixelCount(item.pixel_count?.toString() ?? "");
    setStagePoints(
      item.stage_points?.map(([x, y]) => `${x},${y}`).join("\n") ?? "",
    );
    setMapping(undefined);
    if (item.kind === "projector_surface") {
      try {
        const loaded = await getProjectorMapping(item.id);
        setMapping(loaded);
        setMappingValues({
          resource: loaded.resource,
          native_width: loaded.native_width,
          native_height: loaded.native_height,
          stage_placement: loaded.stage_placement ?? "surface",
          stage_x: loaded.stage_x,
          stage_y: loaded.stage_y,
          stage_width: loaded.stage_width,
          stage_height: loaded.stage_height,
          crop_x: loaded.crop_x,
          crop_y: loaded.crop_y,
          crop_width: loaded.crop_width,
          crop_height: loaded.crop_height,
          content_asset_id: loaded.content_asset_id,
          surface_model: loaded.surface_model,
          fit_mode: loaded.fit_mode,
          overscan: loaded.overscan,
          brightness_limit: loaded.brightness_limit,
          media_start_delay_ms: loaded.media_start_delay_ms,
          media_release_delay_ms: loaded.media_release_delay_ms,
          orientation_degrees: loaded.orientation_degrees,
          mirror_x: loaded.mirror_x,
          mirror_y: loaded.mirror_y,
          mask_semantic_ids: loaded.mask_semantic_ids,
          calibration_source:
            loaded.calibration_source ?? "unverified_estimate",
          calibration_revision: loaded.calibration_revision ?? 1,
          mesh_vertices: loaded.mesh_vertices ?? [],
          mesh_triangles: loaded.mesh_triangles ?? [],
        });
      } catch {
        setMapping(undefined);
      }
    }
    if (item.kind === "pixel_segment") {
      try {
        const loaded = await getLightingSurface(item.id);
        setLightingSurface(loaded);
        setLightingValues({
          resource: loaded.resource,
          local_width: loaded.local_width,
          local_height: loaded.local_height,
          local_points: loaded.local_points,
          stage_matrix: loaded.stage_matrix,
          fixture_id: loaded.fixture_id ?? "",
          group_id: loaded.group_id ?? "",
          path_id: loaded.path_id ?? "",
          strand_id: loaded.strand_id ?? "",
          calibration_id: loaded.calibration_id ?? "",
          pixel_footprint_width: loaded.pixel_footprint_width,
          pixel_footprint_height: loaded.pixel_footprint_height,
          brightness_limit: loaded.brightness_limit,
          timing_offset_ms: loaded.timing_offset_ms,
          release_offset_ms: loaded.release_offset_ms,
          pixel_overrides: loaded.pixel_overrides,
        });
        const firstPixel = loaded.pixels[0];
        if (firstPixel) {
          setPixelCorrectionIndex(firstPixel.output_index);
          setPixelCorrectionX(firstPixel.local_x);
          setPixelCorrectionY(firstPixel.local_y);
        }
      } catch {
        setLightingSurface(undefined);
      }
    }
    if (item.kind === "projector_surface" || item.kind === "pixel_segment") {
      setAcceptedCalibrations(
        await reviewAcceptedCalibrations(
          item.kind === "projector_surface"
            ? "projector_mapping"
            : "lighting_surface",
          item.id,
          calibrationContext(),
        ),
      );
    }
  }

  function reset() {
    setAdding(false); setChanged(false); setSaveError(null);
    setEditing(undefined);
    setName("");
    setPoints([]);
    setMapping(undefined);
    setLightingSurface(undefined);
    setLinkedSemanticId(null);
    setTraversalPreview(false);
  }

  async function loadSuggestion(suggestion: GeometrySuggestion) {
    await reviewGeometrySuggestion(suggestion.id, "review");
    setKind(suggestion.kind);
    setName(suggestion.name);
    setPoints(suggestion.points);
    setEditing(undefined);
    setLinkedSemanticId(null);
    setAdding(true); setChanged(true);
    await onRefresh();
  }

  async function rejectSuggestion(suggestion: GeometrySuggestion) {
    await reviewGeometrySuggestion(suggestion.id, "reject");
    await onRefresh();
  }

  async function save() {
    if (!name.trim()) {
      return;
    }
    setSavingGeometry(true); setSaveError(null);
    let geometrySaved = false;
    try {
    const saved = await saveInstallationGeometry(
      installationId,
      {
        photo_id: editing ? editing.photo_id : photo?.id ?? null,
        kind,
        name,
        points,
        closed,
        semantic_id: editing ? editing.semantic_id : linkedSemanticId,
        color,
        label_visible: labelVisible,
        locked,
        hidden,
        z_index: zIndex,
        direction,
        start_index: kind === "pixel_segment" ? Number(startIndex) : null,
        pixel_count: kind === "pixel_segment" ? Number(pixelCount) : null,
        stage_points: stagePoints.trim()
          ? stagePoints
              .split("\n")
              .map((line) => line.split(",").map(Number))
              .filter(
                (point): point is [number, number] =>
                  point.length === 2 && point.every(Number.isFinite),
              )
          : null,
      },
      editing,
    );
    geometrySaved = true;
    setEditing(saved);
    if (
      kind === "projector_surface" &&
      (mappingValues.surface_model === "planar" ? points.length === 4 : points.length >= 5)
    ) {
      // Saving surface geometry re-derives surface-placed mappings, advancing their revision.
      const mappingRevision = mapping
        ? (await getProjectorMapping(saved.id).catch(() => mapping)).revision
        : undefined;
      const savedMapping = await saveProjectorMapping(
        saved.id,
        mappingValues,
        mappingRevision,
      );
      setMapping(savedMapping);
      setMappingValues((current) => ({
        ...current,
        stage_x: savedMapping.stage_x,
        stage_y: savedMapping.stage_y,
        stage_width: savedMapping.stage_width,
        stage_height: savedMapping.stage_height,
        mesh_vertices: savedMapping.mesh_vertices ?? current.mesh_vertices,
      }));
    }
    if (kind === "pixel_segment") {
      const localPoints =
        lightingSurface?.local_points ??
        points.map(
          (point) =>
            [
              point.x * lightingValues.local_width,
              point.y * lightingValues.local_height,
            ] as [number, number],
        );
      await saveLightingSurface(
        saved.id,
        {
          ...lightingValues,
          local_points: localPoints,
          fixture_id: lightingValues.fixture_id || null,
          group_id: lightingValues.group_id || null,
          path_id: lightingValues.path_id || null,
          strand_id: lightingValues.strand_id || null,
          calibration_id: lightingValues.calibration_id || null,
        },
        lightingSurface?.revision,
      );
    }
    reset();
    await onRefresh();
    } catch (failure) {
      const detail = failure instanceof Error ? failure.message : "The request failed.";
      setSaveError(geometrySaved
        ? `The layout object was saved, but its mapping could not be completed. Your settings are retained; retry updates the saved object. ${detail}`
        : `Layout changes could not be saved. ${detail}`);
    } finally {
      setSavingGeometry(false);
    }
  }

  function generatePixelLayout() {
    if (!canGenerate) {
      return;
    }
    const generated = generateNormalizedPixelLayout(
      pixelGenerator,
      generatorRows,
      pixelGenerator === "grid" ? generatorColumns : numberOfPixels,
      importedPoints,
    );
    setKind("pixel_segment");
    setPoints(generated);
    const count =
      pixelGenerator === "grid"
        ? generatorRows * generatorColumns
        : pixelGenerator === "import"
          ? generated.length
        : pixelGenerator === "individual"
          ? 1
          : numberOfPixels;
    setPixelCount(String(count));
    setLightingSurface(undefined);
    setLightingValues((current) => ({
      ...current,
      local_points: generated.map(
        (point) =>
          [
            point.x * current.local_width,
            point.y * current.local_height,
          ] as [number, number],
      ),
    }));
  }

  function calibrationContext(): Record<string, unknown> {
    return {
      hardware: calibrationProvenance.hardware,
      routing: calibrationProvenance.routing,
      display_mode: calibrationProvenance.display_mode,
      media_settings: calibrationProvenance.media_settings,
      environment: calibrationProvenance.environment,
    };
  }

  async function beginTiming(adjustmentKind: "onset" | "release") {
    if (!editing) {
      return;
    }
    setTimingSession(
      await startTimingCalibration(
        editing.kind === "projector_surface"
          ? "projector_mapping"
          : "lighting_surface",
        editing.id,
        adjustmentKind,
        {
          test_asset_id: calibrationProvenance.test_asset_id || null,
          test_asset_version: calibrationProvenance.test_asset_version || null,
          observed_variability_ms: calibrationProvenance.observed_variability_ms
            ? Number(calibrationProvenance.observed_variability_ms)
            : null,
          notes: calibrationProvenance.notes || null,
          context: calibrationContext(),
        },
      ),
    );
    setTimingView("proposed");
  }

  async function nudgeTiming(delta: number) {
    if (!timingSession) {
      return;
    }
    const proposed = Math.max(
      -5000,
      Math.min(5000, timingSession.proposed_ms + delta),
    );
    setTimingSession(await adjustTimingCalibration(timingSession, proposed));
    setTimingView("proposed");
  }

  async function finishTiming(action: "accept" | "cancel") {
    if (!timingSession) {
      return;
    }
    setTimingSession(await finishTimingCalibration(timingSession, action));
    if (action === "accept") {
      if (editing?.kind === "projector_surface") {
        setMapping(await getProjectorMapping(editing.id));
      } else if (editing?.kind === "pixel_segment") {
        setLightingSurface(await getLightingSurface(editing.id));
      }
      if (editing) {
        setAcceptedCalibrations(
          await reviewAcceptedCalibrations(
            editing.kind === "projector_surface"
              ? "projector_mapping"
              : "lighting_surface",
            editing.id,
            calibrationContext(),
          ),
        );
      }
    }
  }

  async function saveOverlap() {
    if (!editing || editing.kind !== "projector_surface" || !overlapSecondId) {
      return;
    }
    const existing = overlaps.find(
      (item) =>
        item.first_geometry_id === editing.id &&
        item.second_geometry_id === overlapSecondId,
    );
    await saveProjectorOverlap(
      installationId,
      {
        id: existing?.id,
        first_geometry_id: editing.id,
        second_geometry_id: overlapSecondId,
        ...overlapValues,
      },
      existing?.revision,
    );
    await onRefresh();
  }

  function selectOverlap(secondId: string) {
    setOverlapSecondId(secondId);
    const existing = overlaps.find(
      (item) =>
        item.first_geometry_id === editing?.id &&
        item.second_geometry_id === secondId,
    );
    if (existing) {
      setOverlapValues({
        stage_points: existing.stage_points,
        ramp_width: existing.ramp_width,
        overlap_gamma: existing.overlap_gamma,
        first_brightness: existing.first_brightness,
        second_brightness: existing.second_brightness,
        first_white: existing.first_white,
        second_white: existing.second_white,
        first_color_matrix: existing.first_color_matrix,
        second_color_matrix: existing.second_color_matrix,
        first_black_level: existing.first_black_level,
        second_black_level: existing.second_black_level,
      });
    }
  }

  const stageReference = stageMap?.reference ?? null;
  const coverageAvailable = Boolean(
    photo &&
      stageReference?.applied_at &&
      stageReference.photo_id === photo.id &&
      stageCoverage?.regions.some((region) => region.resources.length > 0),
  );
  const showCoverage = coverageOverlay && coverageAvailable && stageReference !== null;
  const stageToCanvas = ([x, y]: [number, number]) =>
    `${x / (stageReference?.units_per_canvas ?? 1)},${y / (stageReference?.units_per_canvas ?? 1)}`;

  return (
    <section className="geometry-mapper">
      <div className="geometry-mapper__tools">
        {canEdit && <details className="layout-add" open={visible.length === 0}>
          <summary className="primary-button" role="button">{photo ? "Add to photo" : "Add to layout"}</summary>
          <div className="setup-actions">
            {GEOMETRY_KINDS.map((value) => <button type="button" key={value} onClick={(event) => {
              if (changed && !window.confirm("Discard unsaved layout changes and add a new object?")) return;
              reset(); setKind(value); setAdding(true);
              event.currentTarget.closest("details")?.removeAttribute("open");
            }}>{value === "pixel_segment" ? "LED segment" : value === "projector_surface" ? "Projector area" : value.replaceAll("_", " ")}</button>)}
          </div>
        </details>}
        <label>
          Photo view
          <select value={photo?.id ?? ""} onChange={(event) => {
            if (changed && !window.confirm("Discard unsaved layout changes and change photo?")) return;
            reset(); setPhotoId(event.target.value);
          }}>
          <option value="">Installation canvas (no photo)</option>
          {photos.map((candidate) => (
            <option key={candidate.id} value={candidate.id}>
              {candidate.description ?? candidate.name}
            </option>
          ))}
          </select>
        </label>
        {coverageAvailable && (
          <button
            type="button"
            aria-pressed={coverageOverlay}
            onClick={() => setCoverageOverlay((current) => !current)}
          >
            {coverageOverlay ? "Hide projector coverage" : "Show projector coverage"}
          </button>
        )}
        <button
          type="button"
          aria-pressed={simulationOverlay}
          onClick={() => setSimulationOverlay((current) => !current)}
        >
          {simulationOverlay ? "Hide simulation overlay" : "Show simulation overlay"}
        </button>
        {simulationOverlay && (
          <>
            <label>
            Simulation preview mode
            <select
              aria-label="Simulation preview mode"
              value={simulationPreviewMode}
              onChange={(event) => {
                setSimulationPreviewMode(
                  event.target.value as SimulationPreviewMode,
                );
                setSimulationTarget("");
              }}
            >
              <option value="logical_composition">Current logical composition</option>
              <option value="parent_context">Parent composition context</option>
              <option value="native_projector">Selected native projector canvas</option>
              <option value="selected_output">Selected device / output</option>
              <option value="full_installation">Full mapped installation</option>
            </select>
            </label>
            {["logical_composition", "parent_context"].includes(
              simulationPreviewMode,
            ) && (
              <label>
              Semantic context
              <select
                aria-label="Simulation semantic context"
                value={simulationTarget}
                onChange={(event) => setSimulationTarget(event.target.value)}
              >
                <option value="">All semantic targets</option>
                {semanticTargets.map((semanticId) => (
                  <option key={semanticId} value={semanticId}>
                    {semanticId}
                  </option>
                ))}
              </select>
              </label>
            )}
            {["native_projector", "selected_output"].includes(
              simulationPreviewMode,
            ) && (
              <label>
              Output context
              <select
                aria-label="Simulation output context"
                value={simulationTarget}
                onChange={(event) => setSimulationTarget(event.target.value)}
              >
                <option value="">All configured outputs</option>
                {projectorResources.map((resource) => (
                  <option key={resource} value={resource}>
                    {resource}
                  </option>
                ))}
                <option value="lighting">Mapped lighting outputs</option>
              </select>
              </label>
            )}
            <small aria-label="Simulation context status">
              {simulationPreviewMode.replaceAll("_", " ")} · scene time 0 ms
            </small>
            <button
              aria-pressed={sideBySideSimulation}
              onClick={() => setSideBySideSimulation((current) => !current)}
            >
              {sideBySideSimulation
                ? "Hide side-by-side viewpoint"
                : "Show side-by-side viewpoint"}
            </button>
            {sideBySideSimulation && (
              <label>
              Secondary viewpoint
              <select
                aria-label="Secondary simulation viewpoint"
                value={secondaryPhoto?.id ?? ""}
                onChange={(event) => setSecondaryPhotoId(event.target.value)}
              >
                {photos
                  .filter((candidate) => candidate.id !== photo?.id)
                  .map((candidate) => (
                    <option key={candidate.id} value={candidate.id}>
                      {candidate.viewpoint} · {candidate.name}
                    </option>
                  ))}
              </select>
              </label>
            )}
          </>
        )}
      </div>
      <div className={`geometry-mapper__workspace${canEdit ? "" : " geometry-mapper__workspace--readonly"}${!adding && !editing ? " geometry-mapper__workspace--browse" : ""}`}>
      {(adding || editing) && (
      <aside className="geometry-mapper__inspector" aria-label="Geometry properties">
        <fieldset className="geometry-inspector-fields" disabled={!canEdit || savingGeometry} onChangeCapture={() => setChanged(true)}>
        <div className="geometry-mapper__fields">
          <h3>{kind === "pixel_segment" ? "Segment properties" : "Geometry properties"}</h3>
          <label>
            Geometry type
            <select
              value={kind}
              disabled={Boolean(editing)}
              onChange={(event) => {
                reset();
                setAdding(true);
                setKind(event.target.value as InstallationGeometryKind);
                setCreationMethod("trace");
              }}
            >
              {GEOMETRY_KINDS.map((value) => (
                <option key={value} value={value}>
                  {value.replaceAll("_", " ")}
                </option>
              ))}
            </select>
          </label>
          <label>
            Geometry name
            <input
              placeholder={kind === "pixel_segment" ? "e.g. garage-eave" : "Name this geometry"}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          {kind === "pixel_segment" && (
          <>
            <fieldset className="geometry-mapper__method">
              <legend>Creation method</legend>
              <div className="geometry-mapper__actions">
                <button
                  aria-pressed={creationMethod === "trace"}
                  onClick={() => setCreationMethod("trace")}
                >
                  {photo ? "Trace on photo" : "Trace on canvas"}
                </button>
                <button
                  aria-pressed={creationMethod === "generate"}
                  onClick={() => setCreationMethod("generate")}
                >
                  Generate layout
                </button>
              </div>
            </fieldset>
            <label>
              Output resource
              <select
                value={lightingValues.resource}
                onChange={(event) =>
                  setLightingValues((current) => ({ ...current, resource: event.target.value }))
                }
              >
                <option value="">Select an output</option>
                {pixelResources.map((resource) => (
                  <option key={resource.resource} value={resource.resource}>
                    {resource.resource}{resource.pixel_count === null ? "" : ` · ${resource.pixel_count} pixels`}
                  </option>
                ))}
                {lightingValues.resource && !selectedPixelOutput && (
                  <option value={lightingValues.resource}>{lightingValues.resource} (not configured)</option>
                )}
              </select>
            </label>
            <div className="geometry-mapper__pair">
              <label>
                First pixel index
                <input min="0" step="1" type="number" value={startIndex} onChange={(event) => setStartIndex(event.target.value)} aria-describedby="mapper-pixel-index-help" />
              </label>
              <label>
                Pixel count
                <input
                  min="1"
                  step="1"
                  type="number"
                  value={pixelCount}
                  readOnly={creationMethod === "generate" && ["grid", "individual", "import"].includes(pixelGenerator)}
                  onChange={(event) => setPixelCount(event.target.value)}
                />
              </label>
            </div>
            <small id="mapper-pixel-index-help">Zero-based: the first LED is 0. Enter a count, not an end index.</small>
            {validPixelRange && (
              <p className="geometry-mapper__allocation" role="status">
                Pixels {firstPixel}–{firstPixel + numberOfPixels - 1} · {numberOfPixels} LEDs
              </p>
            )}
            {startIndex !== "" && pixelCount !== "" && !validPixelRange && (
              <small role="alert">Use a whole-number first index of 0 or more and a positive whole-number pixel count.</small>
            )}
            {exceedsOutput && (
              <small role="alert">This range exceeds {lightingValues.resource}: valid indices are 0–{selectedPixelOutput!.pixel_count! - 1}.</small>
            )}
            {overlappingSegments.map((item) => (
              <small className="asset-diagnostic--warning" key={item.id}>
                Pixel allocation overlaps {item.name} on {lightingValues.resource}.
              </small>
            ))}
            {allocationCheckError && (
              <small className="asset-diagnostic--warning">
                Some existing LED allocations could not be checked. Review output ranges before saving.
              </small>
            )}
            <details className="geometry-mapper__group"><summary>Optional strand label</summary><label>
              Strand ID
              <input value={lightingValues.strand_id} placeholder="e.g. lower" onChange={(event) => setLightingValues((current) => ({ ...current, strand_id: event.target.value }))} />
            </label></details>
          </>
          )}
          {(kind === "pixel_segment" || kind === "path") && (
            <>
              <label>
                Wiring direction
                <select value={direction} onChange={(event) => setDirection(event.target.value as "forward" | "reverse")}>
                  <option value="forward">First point → last point</option>
                  <option value="reverse">Last point → first point</option>
                </select>
              </label>
              <button
                aria-pressed={traversalPreview}
                disabled={kind === "pixel_segment" && (
                  allocationLoading || allocationCheckError ||
                  !selectedPixelOutput?.pixel_count
                )}
                onClick={() => setTraversalPreview((current) => !current)}
              >
                {traversalPreview ? "Stop traversal preview" : "Preview traversal"}
              </button>
              <small>Canvas only. No hardware commands are sent.</small>
              {kind === "pixel_segment" && (
                <small>
                  Previews every saved segment on the selected output in pixel-index order,
                  not just this segment. Save edits before previewing.
                  {allocationLoading && " Loading output assignments..."}
                  {!selectedPixelOutput?.pixel_count &&
                    " Select a configured output with a known pixel count to preview."}
                </small>
              )}
            </>
          )}
          {kind === "pixel_segment" && creationMethod === "generate" && (
            <section className="geometry-mapper__fields geometry-mapper__generator" aria-label="Layout generator">
            <h4>Generate pixel layout</h4>
            <label>
            Layout shape
            <select
              aria-label="Pixel layout generator"
              value={pixelGenerator}
              onChange={(event) => {
                const next = event.target.value as typeof pixelGenerator;
                setPixelGenerator(next);
                if (next === "grid") setPixelCount(String(generatorRows * generatorColumns));
                if (next === "individual") setPixelCount("1");
                if (next === "import") setPixelCount("");
              }}
            >
              <option value="row">Pixel row</option>
              <option value="grid">Serpentine grid</option>
              <option value="ring">Pixel ring</option>
              <option value="outline">Rectangle outline</option>
              <option value="individual">Individual pixel</option>
              <option value="import">Import coordinates</option>
            </select>
            </label>
            {pixelGenerator === "grid" && (
              <div className="geometry-mapper__pair">
              <label>
              Rows
              <input
                aria-label="Pixel rows"
                min="2"
                max="100"
                type="number"
                value={generatorRows}
                onChange={(event) => {
                  setGeneratorRows(Number(event.target.value));
                  setPixelCount(String(Number(event.target.value) * generatorColumns));
                }}
              />
              </label>
              <label>
              Pixels per row
              <input
                min="2"
                max="500"
                type="number"
                value={generatorColumns}
                onChange={(event) => {
                  setGeneratorColumns(Number(event.target.value));
                  setPixelCount(String(generatorRows * Number(event.target.value)));
                }}
              />
              </label>
              </div>
            )}
            {pixelGenerator === "import" && (
              <label>
              Normalized coordinates
              <textarea
                aria-label="Imported normalized coordinates"
                placeholder={"0.1,0.1\n0.9,0.9"}
                rows={2}
                value={importedPoints}
                onChange={(event) => {
                  setImportedPoints(event.target.value);
                  setPixelCount(String(generateNormalizedPixelLayout("import", 0, 0, event.target.value).length));
                }}
              />
              <small>One x,y pair per line; each coordinate must be between 0 and 1.</small>
              </label>
            )}
            <small>
              {pixelGenerator === "grid" ? "Pixel count is rows × pixels per row." : pixelGenerator === "import" ? "Pixel count is calculated from valid coordinate pairs." : pixelGenerator === "individual" ? "Creates one pixel." : "Uses the Pixel count above; rings support 2–500 pixels."}
              {" "}Generating replaces the current drawing.
            </small>
            <button disabled={!canGenerate} onClick={generatePixelLayout}>Generate pixel layout</button>
            </section>
          )}
          <details className="geometry-mapper__group">
            <summary>Appearance</summary>
            <div className="geometry-mapper__fields">
            <label>
            Geometry color
            <input
              type="color"
              value={color}
              onChange={(event) => setColor(event.target.value)}
            />
            </label>
            <label><input type="checkbox" checked={labelVisible} onChange={(event) => setLabelVisible(event.target.checked)} />Labels</label>
            <label><input type="checkbox" checked={locked} onChange={(event) => setLocked(event.target.checked)} />Locked</label>
            <label><input type="checkbox" checked={hidden} onChange={(event) => setHidden(event.target.checked)} />Hidden</label>
            <label>Layer order<input type="number" value={zIndex} onChange={(event) => setZIndex(Number(event.target.value))} /></label>
            </div>
          </details>
          <details className="geometry-mapper__group">
            <summary>Grouping &amp; associations</summary>
            <div className="geometry-mapper__fields">
            {!editing && (
              <label>
              Link geometry across viewpoints
              <select
                value={linkedSemanticId ?? ""}
                onChange={(event) => setLinkedSemanticId(event.target.value || null)}
              >
                <option value="">Create new semantic object</option>
                {geometry
                  .filter((item) => item.kind === kind && item.photo_id !== photo?.id)
                  .filter(
                    (item, index, all) =>
                      all.findIndex(
                        (candidate) => candidate.semantic_id === item.semantic_id,
                      ) === index,
                  )
                  .map((item) => (
                    <option key={item.semantic_id} value={item.semantic_id}>
                      Link to {item.name}
                    </option>
                  ))}
              </select>
              </label>
            )}
            {kind === "pixel_segment" && (
              <>
                {([["fixture_id", "Fixture ID"], ["group_id", "Group ID"], ["path_id", "Ordered path ID"]] as const).map(([field, label]) => (
                  <label key={field}>{label}<input value={lightingValues[field]} onChange={(event) => setLightingValues((current) => ({ ...current, [field]: event.target.value }))} /></label>
                ))}
              </>
            )}
            {editing && <small>Semantic object: {editing.semantic_id}</small>}
            </div>
          </details>
          <details className="geometry-mapper__group">
            <summary>Shared stage coordinates</summary>
            <label>
              Shared stage points
              <textarea
                placeholder={"Stage x,y per line\n0,0\n1,0"}
                rows={2}
                value={stagePoints}
                onChange={(event) => setStagePoints(event.target.value)}
              />
            </label>
          </details>
        </div>
      {canEdit && kind === "projector_surface" && (
        <>
          <label>
            Projector resource
            <select
              value={mappingValues.resource}
              onChange={(event) =>
                setMappingValues((current) => ({ ...current, resource: event.target.value }))
              }
            >
              {projectorResources.map((resource) => (
                <option key={resource} value={resource}>{resource}</option>
              ))}
            </select>
          </label>
        <details className="geometry-mapper__group">
        <summary>Projector mapping</summary>
        <div className="projector-mapping-controls">
          <label>
            Calibration content
            <select
              value={mappingValues.content_asset_id ?? ""}
              onChange={(event) =>
                setMappingValues((current) => ({
                  ...current,
                  content_asset_id: event.target.value || null,
                }))
              }
            >
              <option value="">Generated calibration grid</option>
              {imageAssets.map((asset) => (
                <option key={asset.id} value={asset.id}>{asset.name}</option>
              ))}
            </select>
          </label>
          <label>
            Surface model
            <select
              value={mappingValues.surface_model}
              onChange={(event) =>
                setMappingValues((current) => ({
                  ...current,
                  surface_model: event.target.value as "planar" | "subdivided",
                }))
              }
            >
              <option value="planar">Planar four-corner</option>
              <option value="subdivided">Subdivided / non-planar</option>
            </select>
          </label>
          <label>
            Calibration source
            <select
              aria-label="Projection calibration source"
              value={mappingValues.calibration_source}
              onChange={(event) =>
                setMappingValues((current) => ({
                  ...current,
                  calibration_source: event.target.value as
                    | "measured_grid"
                    | "easy_corner_pin"
                    | "unverified_estimate",
                }))
              }
            >
              <option value="measured_grid">Measured live grid</option>
              <option value="easy_corner_pin">Easy-mode corner pin</option>
              <option value="unverified_estimate">Unverified estimate</option>
            </select>
          </label>
          <label>
            Calibration revision
            <input
              aria-label="Projection calibration revision"
              min="1"
              type="number"
              value={mappingValues.calibration_revision}
              onChange={(event) =>
                setMappingValues((current) => ({
                  ...current,
                  calibration_revision: Number(event.target.value),
                }))
              }
            />
          </label>
          {mappingValues.surface_model === "subdivided" && (
            <>
              <label className="asset-governance__wide">
                Mesh vertices · UV u,v then stage/object x,y
                <textarea
                  aria-label="Projection mesh vertices"
                  rows={5}
                  value={mappingValues.mesh_vertices
                    .map((vertex) => vertex.join(","))
                    .join("\n")}
                  onChange={(event) =>
                    setMappingValues((current) => ({
                      ...current,
                      mesh_vertices: event.target.value
                        .split("\n")
                        .map((line) => line.split(",").map(Number))
                        .filter(
                          (vertex): vertex is [number, number, number, number] =>
                            vertex.length === 4 && vertex.every(Number.isFinite),
                        ),
                    }))
                  }
                />
              </label>
              <label className="asset-governance__wide">
                Mesh triangle vertex indices
                <textarea
                  aria-label="Projection mesh triangles"
                  rows={4}
                  value={mappingValues.mesh_triangles
                    .map((triangle) => triangle.join(","))
                    .join("\n")}
                  onChange={(event) =>
                    setMappingValues((current) => ({
                      ...current,
                      mesh_triangles: event.target.value
                        .split("\n")
                        .map((line) => line.split(",").map(Number))
                        .filter(
                          (triangle): triangle is [number, number, number] =>
                            triangle.length === 3 &&
                            triangle.every(Number.isInteger),
                        ),
                    }))
                  }
                />
              </label>
            </>
          )}
          <label>
            Content fit
            <select
              value={mappingValues.fit_mode}
              onChange={(event) =>
                setMappingValues((current) => ({
                  ...current,
                  fit_mode: event.target.value as "preserve" | "stretch",
                }))
              }
            >
              <option value="preserve">Preserve aspect ratio</option>
              <option value="stretch">Stretch to surface</option>
            </select>
          </label>
          <label>
            Native orientation
            <select
              value={mappingValues.orientation_degrees}
              onChange={(event) =>
                setMappingValues((current) => ({
                  ...current,
                  orientation_degrees: Number(event.target.value) as
                    | 0
                    | 90
                    | 180
                    | 270,
                }))
              }
            >
              {[0, 90, 180, 270].map((degrees) => (
                <option key={degrees} value={degrees}>{degrees}°</option>
              ))}
            </select>
          </label>
          <label>
            <input
              checked={mappingValues.mirror_x}
              type="checkbox"
              onChange={(event) =>
                setMappingValues((current) => ({
                  ...current,
                  mirror_x: event.target.checked,
                }))
              }
            />
            Mirror horizontally
          </label>
          <label>
            <input
              checked={mappingValues.mirror_y}
              type="checkbox"
              onChange={(event) =>
                setMappingValues((current) => ({
                  ...current,
                  mirror_y: event.target.checked,
                }))
              }
            />
            Mirror vertically
          </label>
          <label>
            Linked masks
            <select
              multiple
              value={mappingValues.mask_semantic_ids}
              onChange={(event) =>
                setMappingValues((current) => ({
                  ...current,
                  mask_semantic_ids: Array.from(
                    event.target.selectedOptions,
                    (option) => option.value,
                  ),
                }))
              }
            >
              {geometry
                .filter(
                  (item, index, all) =>
                    item.kind === "mask" &&
                    all.findIndex(
                      (candidate) =>
                        candidate.semantic_id === item.semantic_id,
                    ) === index,
                )
                .map((item) => (
                  <option key={item.semantic_id} value={item.semantic_id}>
                    {item.name}
                  </option>
                ))}
            </select>
          </label>
          <label>
            <input
              checked={mappingValues.overscan}
              type="checkbox"
              onChange={(event) =>
                setMappingValues((current) => ({
                  ...current,
                  overscan: event.target.checked,
                }))
              }
            />
            Allow overscan
          </label>
          <label>
            Stage placement
            <select
              value={mappingValues.stage_placement}
              onChange={(event) =>
                setMappingValues((current) => ({
                  ...current,
                  stage_placement: event.target.value as "surface" | "manual",
                }))
              }
            >
              <option value="surface">From drawn surface</option>
              <option value="manual">Manual stage rectangle</option>
            </select>
          </label>
          <small className="asset-governance__wide" role="status">
            {mappingValues.stage_placement === "surface"
              ? mapping?.stage_placement === "surface" && mapping.stage_placement_message
                ? mapping.stage_placement_message
                : "Stage position and size follow this drawn surface when saved."
              : mapping?.stage_placement === "manual" && mapping.stage_placement_message
                ? mapping.stage_placement_message
                : "Enter the stage rectangle; redrawing the surface will not move it."}
          </small>
          {(
            [
              ["native_width", "Native width"],
              ["native_height", "Native height"],
              ["stage_x", "Stage X"],
              ["stage_y", "Stage Y"],
              ["stage_width", "Stage width"],
              ["stage_height", "Stage height"],
              ["crop_x", "Crop X"],
              ["crop_y", "Crop Y"],
              ["crop_width", "Crop width"],
              ["crop_height", "Crop height"],
              ["brightness_limit", "Brightness limit (0–1)"],
              ["media_start_delay_ms", "Media start delay (ms)"],
            ] as const
          ).map(([field, label]) => (
            <label key={field}>
              {label}
              <input
                type="number"
                step="any"
                value={
                  mappingValues.stage_placement === "surface" &&
                  field.startsWith("stage_")
                    ? Number(mappingValues[field].toFixed(4))
                    : mappingValues[field]
                }
                readOnly={
                  mappingValues.stage_placement === "surface" &&
                  field.startsWith("stage_")
                }
                title={
                  mappingValues.stage_placement === "surface" &&
                  field.startsWith("stage_")
                    ? "Derived from the drawn surface. Choose Manual stage rectangle to edit."
                    : undefined
                }
                onChange={(event) =>
                  setMappingValues((current) => ({
                    ...current,
                    [field]: Number(event.target.value),
                  }))
                }
              />
            </label>
          ))}
          <label className="asset-governance__wide">
            Surface-local control points
            <textarea
              rows={3}
              value={lightingValues.local_points
                .map(([x, y]) => `${x},${y}`)
                .join("\n")}
              onChange={(event) =>
                setLightingValues((current) => ({
                  ...current,
                  local_points: event.target.value
                    .split("\n")
                    .map((line) => line.split(",").map(Number))
                    .filter(
                      (point): point is [number, number] =>
                        point.length === 2 && point.every(Number.isFinite),
                    ),
                }))
              }
            />
          </label>
          <small>
            Calibration: {mappingValues.calibration_source.replaceAll("_", " ")} ·
            revision {mappingValues.calibration_revision}
            {mappingValues.surface_model === "subdivided"
              ? ` · indexed mesh ${mappingValues.mesh_vertices.length} vertices / ${mappingValues.mesh_triangles.length} triangles`
              : ""}
          </small>
          <small>
            {mapping
              ? mapping.diagnostic ??
                `Homography: ${mapping.homography?.map((value) => value.toFixed(5)).join(", ")}`
              : mappingValues.surface_model === "planar"
                ? "Draw exactly four corners to create the projective transform."
                : "Add five or more control points. A single homography will be disabled."}
          </small>
          {mapping?.photo_views.map((view) => (
            <small key={view.geometry_id}>
              {view.photo_id ? `Photo view ${view.photo_id}` : "Installation canvas"}:{" "}
              {view.homography
                ? `homography ${view.homography.map((value) => value.toFixed(4)).join(", ")}`
                : "no single homography"}
            </small>
          ))}
          {mappingValues.content_asset_id ? (
            <img
              className="projector-content-preview"
              alt="Selected projector mapping content"
              src={assetArtifactUrl(mappingValues.content_asset_id, "review")}
            />
          ) : (
            <div className="projector-grid-preview" aria-label="Generated calibration grid" />
          )}
          <ProjectorWarpPreviews
            calibrationRevision={mappingValues.calibration_revision}
            calibrationSource={mappingValues.calibration_source}
            meshTriangles={mappingValues.mesh_triangles}
            meshVertices={mappingValues.mesh_vertices}
            nativeHeight={mappingValues.native_height}
            nativeWidth={mappingValues.native_width}
            stageExtent={[
              mappingValues.stage_x,
              mappingValues.stage_y,
              mappingValues.stage_width,
              mappingValues.stage_height,
            ]}
            surfaceModel={mappingValues.surface_model}
          />
          {canPreview && (
            <button
              disabled={!mappingValues.resource}
              onClick={async () => {
                setPreviewError(null);
                try {
                  setAlignmentPreview(
                    await startMaintenancePreview(mappingValues.resource, 15),
                  );
                } catch {
                  setPreviewError(
                    "Preview requires maintenance mode, idle playback, and a configured projector.",
                  );
                }
              }}
            >
              Preview alignment grid for 15 seconds
            </button>
          )}
          {mapping?.fit_diagnostics.map((diagnostic) => (
            <small className="asset-diagnostic--warning" key={diagnostic}>
              {diagnostic}
            </small>
          ))}
        </div>
        </details>
        </>
      )}
      {canEdit &&
        editing?.kind === "projector_surface" &&
        geometry.some(
          (item) =>
            item.kind === "projector_surface" && item.id !== editing.id,
        ) && (
          <details className="geometry-mapper__group"><summary>Multi-projector overlap calibration</summary>
          <div className="projector-mapping-controls overlap-calibration">
            <strong>Projector overlap calibration</strong>
            <label>
              Paired projector surface
              <select
                value={overlapSecondId}
                onChange={(event) => selectOverlap(event.target.value)}
              >
                <option value="">Select surface</option>
                {geometry
                  .filter(
                    (item) =>
                      item.kind === "projector_surface" &&
                      item.id !== editing.id,
                  )
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
              </select>
            </label>
            {(
              [
                ["ramp_width", "Blend ramp width"],
                ["overlap_gamma", "Overlap gamma"],
                ["first_brightness", "First brightness"],
                ["second_brightness", "Second brightness"],
                ["first_black_level", "First black level"],
                ["second_black_level", "Second black level"],
              ] as const
            ).map(([field, label]) => (
              <label key={field}>
                {label}
                <input
                  min="0"
                  max={field === "overlap_gamma" ? "5" : "1"}
                  step="0.01"
                  type="number"
                  value={overlapValues[field]}
                  onChange={(event) =>
                    setOverlapValues((current) => ({
                      ...current,
                      [field]: Number(event.target.value),
                    }))
                  }
                />
              </label>
            ))}
            {(
              [
                ["stage_points", "Stage overlap polygon"],
                ["first_white", "First white RGB"],
                ["second_white", "Second white RGB"],
                ["first_color_matrix", "First 3×3 color matrix"],
                ["second_color_matrix", "Second 3×3 color matrix"],
              ] as const
            ).map(([field, label]) => (
              <label className="asset-governance__wide" key={field}>
                {label}
                <textarea
                  rows={2}
                  value={
                    field === "stage_points"
                      ? overlapValues[field]
                          .map(([x, y]) => `${x},${y}`)
                          .join("\n")
                      : overlapValues[field].join(",")
                  }
                  onChange={(event) => {
                    const values = event.target.value
                      .split(field === "stage_points" ? "\n" : ",")
                      .map((part) =>
                        field === "stage_points"
                          ? part.split(",").map(Number)
                          : Number(part),
                      );
                    setOverlapValues((current) => ({
                      ...current,
                      [field]: values,
                    }) as typeof current);
                  }}
                />
              </label>
            ))}
            <button disabled={!overlapSecondId} onClick={() => void saveOverlap()}>
              Save overlap calibration
            </button>
            <button onClick={() => setOverlapPreviewMode("native")}>
              Native preview
            </button>
            <button onClick={() => setOverlapPreviewMode("mapped")}>
              Mapped preview
            </button>
            {selectedOverlap?.diagnostics.map((diagnostic) => (
              <small className="asset-diagnostic--warning" key={diagnostic}>
                {diagnostic}
              </small>
            ))}
            <div
              className={`overlap-calibration-preview overlap-calibration-preview--${overlapPreviewMode}`}
              style={{
                "--overlap-ramp": `${overlapValues.ramp_width * 100}%`,
                "--first-brightness": overlapValues.first_brightness,
                "--second-brightness": overlapValues.second_brightness,
                "--overlap-gamma": overlapValues.overlap_gamma,
              } as CSSProperties}
              aria-label="Non-destructive overlap calibration preview"
            />
          </div>
          </details>
        )}
      {canEdit && kind === "pixel_segment" && (
          <>
            {lightingSurface && (
              <small>
                {lightingSurface.pixels.length} pixels mapped from output index{" "}
                {lightingSurface.pixels[0]?.output_index} to{" "}
                {lightingSurface.pixels.at(-1)?.output_index}.
              </small>
            )}
            <details className="geometry-mapper__group">
            <summary>Advanced calibration</summary>
            <div className="projector-mapping-controls">
              {(
                [
                  ["calibration_id", "Calibration ID"],
                ] as const
              ).map(([field, label]) => (
                <label key={field}>
                  {label}
                  <input
                    value={lightingValues[field]}
                    onChange={(event) =>
                      setLightingValues((current) => ({
                        ...current,
                        [field]: event.target.value,
                      }))
                    }
                  />
                </label>
              ))}
              {(
                [
                  ["local_width", "Local width"],
                  ["local_height", "Local height"],
                  ["pixel_footprint_width", "Pixel footprint width"],
                  ["pixel_footprint_height", "Pixel footprint height"],
                  ["brightness_limit", "Brightness limit (0–1)"],
                  ["timing_offset_ms", "Lighting timing offset (ms)"],
                ] as const
              ).map(([field, label]) => (
                <label key={field}>
                  {label}
                  <input
                    min="0.0001"
                    step="any"
                    type="number"
                    value={lightingValues[field]}
                    onChange={(event) =>
                      setLightingValues((current) => ({
                        ...current,
                        [field]: Number(event.target.value),
                      }))
                    }
                  />
                </label>
              ))}
              {(["a", "b", "c", "d", "tx", "ty"] as const).map((label, index) => (
                <label key={label}>
                  Stage {label}
                  <input
                    step="any"
                    type="number"
                    value={lightingValues.stage_matrix[index]}
                    onChange={(event) =>
                      setLightingValues((current) => {
                        const matrix = [...current.stage_matrix] as typeof current.stage_matrix;
                        matrix[index] = Number(event.target.value);
                        return { ...current, stage_matrix: matrix };
                      })
                    }
                  />
                </label>
              ))}
              {lightingSurface && (
                <>
                  <div className="pixel-correction-controls">
                    <label>
                      Pixel correction
                      <select
                        aria-label="Pixel correction output"
                        value={pixelCorrectionIndex}
                        onChange={(event) => {
                          const outputIndex = Number(event.target.value);
                          const pixel = lightingSurface.pixels.find(
                            (candidate) =>
                              candidate.output_index === outputIndex,
                          );
                          setPixelCorrectionIndex(outputIndex);
                          if (pixel) {
                            setPixelCorrectionX(pixel.local_x);
                            setPixelCorrectionY(pixel.local_y);
                          }
                        }}
                      >
                        {lightingSurface.pixels.map((pixel) => (
                          <option
                            key={pixel.output_index}
                            value={pixel.output_index}
                          >
                            Diagnostic output {pixel.output_index}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Corrected local X
                      <input
                        aria-label="Corrected pixel local X"
                        max={lightingValues.local_width}
                        min={0}
                        step="0.001"
                        type="number"
                        value={pixelCorrectionX}
                        onChange={(event) =>
                          setPixelCorrectionX(Number(event.target.value))
                        }
                      />
                    </label>
                    <label>
                      Corrected local Y
                      <input
                        aria-label="Corrected pixel local Y"
                        max={lightingValues.local_height}
                        min={0}
                        step="0.001"
                        type="number"
                        value={pixelCorrectionY}
                        onChange={(event) =>
                          setPixelCorrectionY(Number(event.target.value))
                        }
                      />
                    </label>
                    <button
                      onClick={() => {
                        const correction = {
                          output_index: pixelCorrectionIndex,
                          local_x: pixelCorrectionX,
                          local_y: pixelCorrectionY,
                        };
                        setLightingValues((current) => ({
                          ...current,
                          pixel_overrides: [
                            ...current.pixel_overrides.filter(
                              (override) =>
                                override.output_index !== pixelCorrectionIndex,
                            ),
                            correction,
                          ].sort(
                            (left, right) =>
                              left.output_index - right.output_index,
                          ),
                        }));
                        setLightingSurface((current) =>
                          current
                            ? {
                                ...current,
                                pixel_overrides: [
                                  ...current.pixel_overrides.filter(
                                    (override) =>
                                      override.output_index !==
                                      pixelCorrectionIndex,
                                  ),
                                  correction,
                                ],
                                pixels: current.pixels.map((pixel) =>
                                  pixel.output_index === pixelCorrectionIndex
                                    ? {
                                        ...pixel,
                                        local_x: pixelCorrectionX,
                                        local_y: pixelCorrectionY,
                                        stage_x:
                                          current.stage_matrix[0] *
                                            pixelCorrectionX +
                                          current.stage_matrix[2] *
                                            pixelCorrectionY +
                                          current.stage_matrix[4],
                                        stage_y:
                                          current.stage_matrix[1] *
                                            pixelCorrectionX +
                                          current.stage_matrix[3] *
                                            pixelCorrectionY +
                                          current.stage_matrix[5],
                                      }
                                    : pixel,
                                ),
                              }
                            : current,
                        );
                      }}
                    >
                      Apply pixel correction
                    </button>
                  </div>
                </>
              )}
            </div>
            </details>
            {canPreview && (
              <details className="geometry-mapper__group">
                <summary>Maintenance preview</summary>
                <div className="geometry-mapper__fields">
                <p>Records a 15-second index-chase simulation for {lightingValues.resource || "the selected output"}. Requires maintenance mode and idle playback. No physical LEDs are driven.</p>
                <button
                  disabled={!lightingValues.resource}
                  onClick={async () => {
                    setPreviewError(null);
                    try {
                      setAlignmentPreview(
                        await startMaintenancePreview(lightingValues.resource, 15),
                      );
                    } catch {
                      setPreviewError(
                        "Index chase requires maintenance mode, idle playback, and a configured pixel output.",
                      );
                    }
                  }}
                >
                  Record index-chase simulation
                </button>
                </div>
              </details>
            )}
          </>
          )}
      {canPreview &&
        editing &&
        (editing.kind === "projector_surface" ||
          editing.kind === "pixel_segment") && (
          <details className="geometry-mapper__group">
          <summary>Timing calibration</summary>
          <div
            className="timing-calibration-panel"
            tabIndex={timingSession?.state === "active" ? 0 : -1}
            onKeyDown={(event) => {
              if (timingSession?.state !== "active") {
                return;
              }
              if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                event.preventDefault();
                const direction = event.key === "ArrowLeft" ? -1 : 1;
                void nudgeTiming(direction * (event.shiftKey ? 100 : 10));
              }
            }}
          >
            <strong>Temporary timing calibration</strong>
            {(
              [
                ["test_asset_id", "Test asset ID"],
                ["test_asset_version", "Test asset version"],
                ["observed_variability_ms", "Observed variability (ms)"],
                ["hardware", "Hardware identity/context"],
                ["routing", "Routing"],
                ["display_mode", "Display mode"],
                ["media_settings", "Media settings"],
                ["environment", "Environment"],
              ] as const
            ).map(([field, label]) => (
              <label key={field}>
                {label}
                <input
                  value={calibrationProvenance[field]}
                  onChange={(event) =>
                    setCalibrationProvenance((current) => ({
                      ...current,
                      [field]: event.target.value,
                    }))
                  }
                />
              </label>
            ))}
            <label className="asset-governance__wide">
              Calibration notes
              <textarea
                rows={2}
                value={calibrationProvenance.notes}
                onChange={(event) =>
                  setCalibrationProvenance((current) => ({
                    ...current,
                    notes: event.target.value,
                  }))
                }
              />
            </label>
            {!timingSession || timingSession.state !== "active" ? (
              <>
                <button onClick={() => void beginTiming("onset")}>
                  Calibrate onset
                </button>
                <button onClick={() => void beginTiming("release")}>
                  Calibrate release
                </button>
              </>
            ) : (
              <>
                <span>
                  {timingSession.adjustment_kind} · baseline{" "}
                  {timingSession.baseline_ms} ms · proposed{" "}
                  {timingSession.proposed_ms} ms
                </span>
                {[-100, -10, 10, 100].map((delta) => (
                  <button key={delta} onClick={() => void nudgeTiming(delta)}>
                    {delta > 0 ? "+" : ""}
                    {delta} ms
                  </button>
                ))}
                <button
                  onClick={() =>
                    void nudgeTiming(
                      timingSession.baseline_ms - timingSession.proposed_ms,
                    )
                  }
                >
                  Reset
                </button>
                <button
                  onClick={() =>
                    setTimingView((current) =>
                      current === "baseline" ? "proposed" : "baseline",
                    )
                  }
                >
                  A/B: {timingView}
                </button>
                <button onClick={() => void finishTiming("accept")}>Accept</button>
                <button onClick={() => void finishTiming("cancel")}>Cancel</button>
                <small>
                  Negative triggers earlier; positive triggers later. This
                  session is simulation-only and expires at{" "}
                  {new Date(timingSession.expires_at).toLocaleTimeString()}.
                </small>
              </>
            )}
            {timingSession && timingSession.state !== "active" && (
              <small role="status">
                Session {timingSession.state}; persisted values change only
                after Accept.
              </small>
            )}
            {acceptedCalibrations.map((calibration) => (
              <div className="accepted-calibration" key={calibration.id}>
                <strong>
                  {calibration.adjustment_kind} {calibration.accepted_ms} ms
                </strong>
                <small>
                  {calibration.resource} · {calibration.software_version ?? "firmware unavailable"} ·{" "}
                  {new Date(calibration.accepted_at).toLocaleString()} · {calibration.actor}
                </small>
                <small>
                  Test {calibration.test_asset_id ?? "not recorded"}{" "}
                  {calibration.test_asset_version ?? ""}
                  {calibration.observed_variability_ms === null
                    ? ""
                    : ` · variability ${calibration.observed_variability_ms} ms`}
                </small>
                {calibration.notes && <small>{calibration.notes}</small>}
                {calibration.stale_reasons.map((reason) => (
                  <small className="asset-diagnostic--warning" key={reason}>
                    {reason}
                  </small>
                ))}
              </div>
            ))}
          </div>
          </details>
        )}
        {alignmentPreview && (
          <div className="geometry-mapper__fields" role="status">
            <small>
              Simulation preview recorded for {alignmentPreview.resource} until{" "}
              {new Date(alignmentPreview.expires_at).toLocaleTimeString()}; no hardware command was sent.
            </small>
            <button onClick={() => setAlignmentPreview(null)}>Dismiss preview record</button>
          </div>
        )}
        {previewError && <small role="alert">{previewError}</small>}
        <div className="geometry-mapper__actions geometry-mapper__save">
          <button
            className="primary-button"
            disabled={
              !name.trim() ||
              !points.length ||
              (kind === "projector_surface" &&
                (!mappingValues.resource ||
                  (mappingValues.surface_model === "planar"
                    ? points.length !== 4
                    : points.length < 5 ||
                      mappingValues.mesh_vertices.length < 4 ||
                      mappingValues.mesh_triangles.length < 2))) ||
              (kind === "pixel_segment" &&
                (!lightingValues.resource || !validPixelRange || exceedsOutput))
            }
            onClick={() => void save()}
          >
            {savingGeometry ? "Saving…" : editing ? "Update geometry" : kind === "pixel_segment" ? "Save segment" : "Save geometry"}
          </button>
          <button onClick={() => { if (!changed || window.confirm("Discard unsaved layout changes?")) reset(); }}>Cancel</button>
        </div>
        {saveError && <ErrorState title="Layout not saved" description={saveError} />}
        </fieldset>
      </aside>
      )}
      <div className="geometry-mapper__drawing">
        {canEdit && (adding || editing) && (
          <>
            <p className="geometry-mapper__instruction">
              {kind === "pixel_segment"
                ? creationMethod === "trace"
                  ? "Click the first pixel, then trace the strand in wiring order. Two points are enough for a straight section."
                  : "Set the layout shape and pixel allocation, then generate. Drag points to position the layout on the photo."
                : kind === "projector_surface"
                  ? "Click the surface corners in order: top left, top right, bottom right, bottom left."
                  : "Click the photo to place geometry points. Drag a point to adjust it."}
            </p>
            <div className="geometry-mapper__canvas-tools" aria-label="Drawing controls">
              <button disabled={!points.length} onClick={() => setPoints((current) => current.slice(0, -1))}>Undo point</button>
              <button disabled={!points.length} onClick={() => {
                setPoints([]);
                setLightingSurface(undefined);
              }}>Clear points</button>
              <label><input type="checkbox" checked={snap} onChange={(event) => setSnap(event.target.checked)} />Snap to 1% grid</label>
            </div>
          </>
        )}
      {traversalPreview && kind === "pixel_segment" &&
        !allocationLoading && !allocationCheckError && selectedPixelOutput?.pixel_count != null &&
        selectedPixelOutput.pixel_count > 0 && (
          <OutputTraversalPreview
            key={`${photo?.id ?? "canvas"}:${lightingValues.resource}:${selectedPixelOutput.pixel_count}`}
            geometry={geometry}
            resources={allocationResources}
            resource={lightingValues.resource}
            pixelCount={selectedPixelOutput.pixel_count}
            photo={photo}
            installationId={installationId}
            onClose={() => setTraversalPreview(false)}
            canTest={canPreview}
          />
      )}
      {(
        <div
          className={
            simulationOverlay && sideBySideSimulation
              ? "simulation-view-grid simulation-view-grid--split"
              : "simulation-view-grid"
          }
        >
        {simulationOverlay && (
          <div className="simulation-diagnostics" role="status">
            <strong className="simulation-approximation">
              {primarySimulationStatus.approximate
                ? "Approximate photo simulation"
                : "Measured photo simulation"}{" "}
              · no hardware adapters or enrolled clients are active
            </strong>
            {primarySimulationStatus.diagnostics.map((diagnostic) => (
              <small key={`primary:${diagnostic}`}>{diagnostic}</small>
            ))}
            {sideBySideSimulation &&
              secondarySimulationStatus.diagnostics.map((diagnostic) => (
                <small key={`secondary:${diagnostic}`}>
                  {secondaryPhoto?.name}: {diagnostic}
                </small>
              ))}
          </div>
        )}
        {showCoverage && (
          <ul className="stage-coverage-legend" aria-label="Projector coverage key">
            <li><span className="stage-coverage-swatch stage-region--covered" />One projector</li>
            <li><span className="stage-coverage-swatch stage-region--overlap" />Projectors overlap (blend the seam)</li>
            <li><span className="stage-coverage-swatch stage-region--occluded" />Blocked by a mask</li>
            <li><span className="stage-coverage-swatch stage-region--uncovered" />Between projectors, no coverage</li>
          </ul>
        )}
        <GeometryCanvas
          key={photo?.id ?? "installation-canvas"}
          label={photo ? `Geometry editor for ${photo.name}` : "Installation layout canvas"}
          className={`geometry-canvas${traversalPreview ? " geometry-canvas--traversal" : ""}${traversalPreview && kind === "path" ? " geometry-canvas--path-traversal" : ""}${simulationOverlay ? " geometry-canvas--simulation" : ""}`}
          canEdit={canEdit && !editing?.locked}
          drawing={adding}
          canDraw={adding || Boolean(editing)}
          onSelectGeometry={(id) => {
            const selected = geometry.find((item) => item.id === id);
            if (selected) void beginEdit(selected);
          }}
          onPlacePoint={(point) => {
            setChanged(true);
            const maximum =
              kind === "fixture"
                ? 1
                : kind === "projector_surface" && mappingValues.surface_model === "planar"
                  ? 4
                  : Infinity;
            if (points.length >= maximum) {
              return;
            }
            const precision = snap ? 100 : 10000;
            setPoints((current) => [
              ...current,
              {
                x: Math.max(0, Math.min(1, Math.round(point.x * precision) / precision)),
                y: Math.max(0, Math.min(1, Math.round(point.y * precision) / precision)),
              },
            ]);
          }}
          onMovePoint={(index, point) => {
            setChanged(true);
            const next = {
              x: Math.max(0, Math.min(1, point.x)),
              y: Math.max(0, Math.min(1, point.y)),
            };
            if (snap) {
              next.x = Math.round(next.x * 100) / 100;
              next.y = Math.round(next.y * 100) / 100;
            }
            setPoints((current) =>
              current.map((point, pointIndex) => (pointIndex === index ? next : point)),
            );
          }}
        >
          {(zoom) => <>
          {photo && <image
            width="1000"
            height="600"
            href={installationPhotoUrl(photo.id, photo.revision)}
            preserveAspectRatio="xMidYMid meet"
          />}
          {showCoverage && stageCoverage && (
            <g className="stage-coverage-overlay" aria-label="Projector coverage">
              {stageCoverage.regions.map((region, index) => (
                <polygon
                  key={`${region.kind}:${index}`}
                  className={`stage-region stage-region--${region.kind}`}
                  points={region.points.map(stageToCanvas).join(" ")}
                >
                  <title>{coverageLabel(region.kind, region.resources)}</title>
                </polygon>
              ))}
            </g>
          )}
          {simulationOverlay && photo && (
            <ProjectorSimulationOverlays
              photo={photo}
              geometry={simulationGeometry}
              mappings={simulationMappings}
            />
          )}
          {(simulationOverlay ? simulationGeometry : visible).map((item) => (
            <g key={item.id} data-layout-object={item.id} data-selected={editing?.id === item.id ? "true" : undefined}
              data-object-highlight={highlighted(item) ? "true" : undefined}>
            <GeometryShape
              geometry={item}
              simulation={simulationOverlay}
            />
            {item.kind === "fixture" && item.points[0] ? <circle className="layout-hit-target"
              cx={item.points[0].x * 1000} cy={item.points[0].y * 600} r={18 / zoom} fill="transparent" />
              : item.closed ? <polygon className="layout-hit-target"
                points={item.points.map((point) => `${point.x * 1000},${point.y * 600}`).join(" ")}
                fill="transparent" stroke="transparent" strokeWidth={14 / zoom} />
              : <polyline className="layout-hit-target"
                points={item.points.map((point) => `${point.x * 1000},${point.y * 600}`).join(" ")}
                fill="none" stroke="transparent" strokeWidth={14 / zoom} />}
            </g>
          ))}
          {simulationOverlay &&
            simulationGeometry
              .filter((item) => item.kind === "pixel_segment")
              .flatMap((item) =>
                photoPixelPoints(item).map((pixel) => (
                  <circle
                    className="geometry-simulation-pixel"
                    cx={pixel.x * 1000}
                    cy={pixel.y * 600}
                    key={`${item.id}:${pixel.outputIndex}`}
                    r="7"
                    style={{
                      fill: `hsl(${(pixel.outputIndex * 47) % 360} 90% 60%)`,
                    }}
                  >
                    <title>
                      Simulated logical pixel on {item.name}
                    </title>
                  </circle>
                )),
              )}
          {visibleSuggestions.map((suggestion) =>
            suggestion.closed ? (
              <polygon
                key={suggestion.id}
                className="geometry-suggestion-shape"
                points={suggestion.points
                  .map((point) => `${point.x * 1000},${point.y * 600}`)
                  .join(" ")}
              />
            ) : (
              <polyline
                key={suggestion.id}
                className="geometry-suggestion-shape"
                points={suggestion.points
                  .map((point) => `${point.x * 1000},${point.y * 600}`)
                  .join(" ")}
              />
            ),
          )}
          {editing?.kind === "pixel_segment" &&
            lightingSurface?.geometry_id === editing.id &&
            photoPixelPoints(editing).map((pixel) => (
              <g key={pixel.outputIndex}>
                <circle
                  className="geometry-pixel-footprint"
                  cx={pixel.x * 1000}
                  cy={pixel.y * 600}
                  r="5"
                />
                <text
                  className="geometry-pixel-label"
                  x={pixel.x * 1000 + 7}
                  y={pixel.y * 600 - 7}
                >
                  {pixel.outputIndex}
                </text>
              </g>
            ))}
          {points.length > 0 && (
            <>
            {closed && points.length >= 3 ? (
              <polygon
                className="geometry-shape geometry-shape--draft"
                points={points.map((point) => `${point.x * 1000},${point.y * 600}`).join(" ")}
              />
            ) : (
              <polyline
              className="geometry-shape geometry-shape--draft"
              points={points.map((point) => `${point.x * 1000},${point.y * 600}`).join(" ")}
            />
            )}
            {points.map((point, index) => (
              <g key={`${index}:${point.x}:${point.y}`}>
                <circle
                  className="geometry-corner-handle"
                  cx={point.x * 1000}
                  cy={point.y * 600}
                  r={10 / zoom}
                  data-geometry-point={index}
                />
                {kind === "projector_surface" && (
                  <text
                    className="geometry-corner-label"
                    x={point.x * 1000 + 14}
                    y={point.y * 600 - 14}
                  >
                    {["TL", "TR", "BR", "BL"][index]}
                  </text>
                )}
              </g>
            ))}
            </>
          )}
          </>}
        </GeometryCanvas>
        {simulationOverlay && sideBySideSimulation && secondaryPhoto && (
          <SimulationPhotoCanvas
            geometry={secondarySimulationGeometry}
            mappings={simulationMappings}
            photo={secondaryPhoto}
          />
        )}
        </div>
      )}
      {points.length >= 2 && (
        <details><summary>Point measurements</summary>
        <small>
          Last segment:{" "}
          {Math.hypot(
            points.at(-1)!.x - points.at(-2)!.x,
            points.at(-1)!.y - points.at(-2)!.y,
          ).toFixed(4)}{" "}
          normalized units
        </small>
        </details>
      )}
      {allocationCheckError && <p role="alert">Some output assignments could not be checked. Review LED mappings before relying on wiring diagnostics.</p>}
      {diagnostics.conflicts.map((diagnostic) => (
        <small className="asset-diagnostic--warning" key={diagnostic}>{diagnostic}</small>
      ))}
      {diagnostics.observations.length > 0 && <details className="setup-disclosure"><summary>Photo-space and coverage diagnostics</summary>
        <p>Photos project a three-dimensional display into two dimensions. Overlap or separation on a photo does not prove a wiring fault.</p>
        {diagnostics.observations.map((diagnostic) => <p key={diagnostic}>{diagnostic}</p>)}
      </details>}
      </div>
      </div>
      {!adding && !editing && <p className="muted">Select a decoration on the canvas or in the list. Use {photo ? "Add to photo" : "Add to layout"} to draw a new one. A photo is optional.</p>}
      {visibleSuggestions.length > 0 && (
        <ul className="geometry-list geometry-suggestion-list">
          {visibleSuggestions.map((suggestion) => (
            <li key={suggestion.id}>
              <span>
                <strong>{suggestion.name}</strong>
                <small>
                  Suggested {suggestion.kind.replaceAll("_", " ")} · confidence{" "}
                  {(suggestion.confidence * 100).toFixed(0)}% · {suggestion.evidence}
                </small>
              </span>
              {canEdit && (
                <>
                  <button onClick={() => void loadSuggestion(suggestion)}>
                    Load for manual edit
                  </button>
                  <button
                    className="danger-button"
                    onClick={() => void rejectSuggestion(suggestion)}
                  >
                    Reject
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {selection && (highlightedSemantics.size
        ? <p role="status">Showing layout geometry for {selection.name}. Highlighted member segments do not require a separate fixture marker.</p>
        : <p role="alert">Layout geometry for {selection.name} is no longer available. Return to Objects and refresh its placement links.</p>)}
      <ul className="geometry-list">
        {visible.map((item) => (
          <li key={item.id} data-object-highlight={highlighted(item) ? "true" : undefined}>
            <span>
              <strong>{item.name}</strong>
              <small>
                {item.kind.replaceAll("_", " ")} · {item.points.length} points
                {item.start_index !== null && item.pixel_count !== null
                  ? ` · pixels ${item.start_index}–${item.start_index + item.pixel_count - 1} ${item.direction}`
                  : ` · ${item.direction}`}
              </small>
            </span>
            <button aria-pressed={editing?.id === item.id} onClick={() => void beginEdit(item)}>Select</button>
            {canEdit && (
              <>
                <button disabled={item.locked} onClick={() => void beginEdit(item)}>Edit</button>
                <button
                  className="danger-button"
                  onClick={async () => {
                    await deleteInstallationGeometry(item);
                    await onRefresh();
                  }}
                >
                  Remove placement
                </button>
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function ProjectorWarpPreviews({
  surfaceModel,
  nativeWidth,
  nativeHeight,
  stageExtent,
  meshVertices,
  meshTriangles,
  calibrationSource,
  calibrationRevision,
}: {
  surfaceModel: "planar" | "subdivided";
  nativeWidth: number;
  nativeHeight: number;
  stageExtent: [number, number, number, number];
  meshVertices: [number, number, number, number][];
  meshTriangles: [number, number, number][];
  calibrationSource:
    | "measured_grid"
    | "easy_corner_pin"
    | "unverified_estimate";
  calibrationRevision: number;
}) {
  const vertices =
    surfaceModel === "subdivided" && meshVertices.length
      ? meshVertices
      : [
          [0, 0, stageExtent[0], stageExtent[1]],
          [1, 0, stageExtent[0] + stageExtent[2], stageExtent[1]],
          [
            1,
            1,
            stageExtent[0] + stageExtent[2],
            stageExtent[1] + stageExtent[3],
          ],
          [0, 1, stageExtent[0], stageExtent[1] + stageExtent[3]],
        ] as [number, number, number, number][];
  const triangles =
    surfaceModel === "subdivided" && meshTriangles.length
      ? meshTriangles
      : ([
          [0, 1, 2],
          [0, 2, 3],
        ] as [number, number, number][]);
  const minimumX = Math.min(...vertices.map((vertex) => vertex[2]));
  const minimumY = Math.min(...vertices.map((vertex) => vertex[3]));
  const maximumX = Math.max(...vertices.map((vertex) => vertex[2]));
  const maximumY = Math.max(...vertices.map((vertex) => vertex[3]));
  const stageWidth = maximumX - minimumX || 1;
  const stageHeight = maximumY - minimumY || 1;
  const provenance = calibrationSource.replaceAll("_", " ");
  return (
    <div className="projector-warp-previews">
      <figure>
        <figcaption>
          Native canvas warp · {nativeWidth}×{nativeHeight}
        </figcaption>
        <svg
          aria-label="Projector native calibrated warp preview"
          viewBox={`0 0 ${nativeWidth} ${nativeHeight}`}
        >
          {triangles.map((triangle, index) => (
            <polygon
              key={index}
              points={triangle
                .map((vertexIndex) => {
                  const vertex = vertices[vertexIndex];
                  return `${vertex[0] * nativeWidth},${vertex[1] * nativeHeight}`;
                })
                .join(" ")}
            />
          ))}
        </svg>
      </figure>
      <figure>
        <figcaption>
          Installation warp · {provenance} · revision {calibrationRevision}
        </figcaption>
        <svg
          aria-label="Installation calibrated warp preview"
          viewBox="0 0 800 400"
        >
          {triangles.map((triangle, index) => (
            <polygon
              key={index}
              points={triangle
                .map((vertexIndex) => {
                  const vertex = vertices[vertexIndex];
                  return `${((vertex[2] - minimumX) / stageWidth) * 800},${
                    ((vertex[3] - minimumY) / stageHeight) * 400
                  }`;
                })
                .join(" ")}
            />
          ))}
        </svg>
      </figure>
    </div>
  );
}


function coverageLabel(kind: StageCoverage["regions"][number]["kind"], resources: string[]): string {
  if (kind === "overlap") return `Overlap: ${resources.join(" and ")}`;
  if (kind === "occluded") return `Blocked for ${resources.join(", ") || "all projectors"}`;
  if (kind === "uncovered") return "No projector covers this area";
  return `Covered by ${resources.join(", ")}`;
}

function GeometryShape({
  geometry,
  simulation,
}: {
  geometry: InstallationGeometry;
  simulation?: boolean;
}) {
  const coordinates = geometry.points
    .map((point) => `${point.x * 1000},${point.y * 600}`)
    .join(" ");
  if (geometry.kind === "fixture") {
    const point = geometry.points[0];
    if (!point) {
      return null;
    }

    return (
      <g>
        <circle
          className={`geometry-shape geometry-shape--${geometry.kind}${simulation ? " geometry-shape--simulation" : ""}`}
          style={{ fill: geometry.color, stroke: geometry.color }}
          cx={point.x * 1000}
          cy={point.y * 600}
          r="9"
        />
        {geometry.label_visible && (
          <text
            className="geometry-corner-label"
            x={point.x * 1000 + 14}
            y={point.y * 600 - 14}
          >
            {geometry.name}
          </text>
        )}
      </g>
    );
  }
  const shape = geometry.closed ? (
    <polygon
      className={`geometry-shape geometry-shape--${geometry.kind}${simulation ? " geometry-shape--simulation" : ""}`}
      points={coordinates}
      style={{ stroke: geometry.color }}
    />
  ) : (
    <polyline
      className={`geometry-shape geometry-shape--${geometry.kind}${simulation ? " geometry-shape--simulation" : ""}`}
      points={coordinates}
      style={{ stroke: geometry.color }}
    />
  );
  if (!geometry.label_visible || !geometry.points.length) {
    return shape;
  }
  const center = geometry.points.reduce(
    (total, point) => ({ x: total.x + point.x, y: total.y + point.y }),
    { x: 0, y: 0 },
  );
  return (
    <g>
      {shape}
      <text
        className="geometry-corner-label"
        x={(center.x / geometry.points.length) * 1000}
        y={(center.y / geometry.points.length) * 600}
      >
        {geometry.name}
      </text>
    </g>
  );
}

function ProjectorSimulationOverlays({
  photo,
  geometry,
  mappings,
}: {
  photo: InstallationPhoto;
  geometry: InstallationGeometry[];
  mappings: Record<string, ProjectorMapping | null>;
}) {
  return (
    <>
      {geometry
        .filter((item) => item.kind === "projector_surface")
        .map((item) => {
          const mapping = mappings[item.id];
          const photoView = mapping?.photo_views.find(
            (view) => view.photo_id === photo.id,
          );
          const hasPlanarCalibration = photoView?.homography !== null;
          const hasMeshCalibration =
            mapping?.mesh_triangles.length &&
            mapping.mesh_vertices.length === photoView?.points.length;
          if (
            !mapping?.content_asset_id ||
            !photoView ||
            (!hasPlanarCalibration && !hasMeshCalibration)
          ) {
            return null;
          }
          return (
            <image
              className="projector-simulation-overlay"
              height="600"
              href={projectorSimulationOverlayUrl(item.id, photo.id)}
              key={item.id}
              preserveAspectRatio="none"
              width="1000"
            >
              <title>Review proxy on {item.name}</title>
            </image>
          );
        })}
    </>
  );
}

function projectorSimulationStatus(
  geometry: InstallationGeometry[],
  photo: InstallationPhoto | undefined,
  mappings: Record<string, ProjectorMapping | null>,
  loading: boolean,
): { approximate: boolean; diagnostics: string[] } {
  const projectors = geometry.filter((item) => item.kind === "projector_surface");
  if (loading) {
    return {
      approximate: true,
      diagnostics: projectors.length ? ["Loading projector calibration and review proxies."] : [],
    };
  }
  const diagnostics: string[] = [];
  let approximate = false;
  for (const projector of projectors) {
    const mapping = mappings[projector.id];
    if (!mapping) {
      diagnostics.push(`${projector.name}: projector mapping is missing.`);
      approximate = true;
      continue;
    }
    if (!mapping.content_asset_id) {
      diagnostics.push(`${projector.name}: no managed review proxy is selected.`);
      approximate = true;
    }
    const photoView = mapping.photo_views.find((view) => view.photo_id === photo?.id);
    if (!photoView) {
      diagnostics.push(`${projector.name}: calibration is missing for this photo viewpoint.`);
      approximate = true;
    } else if (
      photoView.homography === null &&
      (!mapping.mesh_triangles.length ||
        mapping.mesh_vertices.length !== photoView.points.length)
    ) {
      diagnostics.push(`${projector.name}: photo warp calibration is incomplete.`);
      approximate = true;
    }
    if (mapping.calibration_source === "unverified_estimate") {
      diagnostics.push(`${projector.name}: projection calibration is an unverified estimate.`);
      approximate = true;
    } else if (mapping.calibration_source === "easy_corner_pin") {
      diagnostics.push(`${projector.name}: projection uses an unmeasured corner pin.`);
      approximate = true;
    }
    if (Date.parse(projector.updated_at) > Date.parse(mapping.updated_at)) {
      diagnostics.push(`${projector.name}: projection calibration is stale.`);
      approximate = true;
    }
    if (mapping.diagnostic) {
      diagnostics.push(`${projector.name}: ${mapping.diagnostic}`);
      approximate = true;
    }
    diagnostics.push(
      ...mapping.fit_diagnostics.map(
        (diagnostic) => `${projector.name}: ${diagnostic}`,
      ),
    );
  }
  return { approximate, diagnostics };
}

function SimulationPhotoCanvas({
  photo,
  geometry,
  mappings,
}: {
  photo: InstallationPhoto;
  geometry: InstallationGeometry[];
  mappings: Record<string, ProjectorMapping | null>;
}) {
  return (
    <svg
      aria-label={`Synchronized simulation for ${photo.name}`}
      className="geometry-canvas geometry-canvas--simulation geometry-canvas--secondary"
      role="img"
      viewBox="0 0 1000 600"
    >
      <image
        width="1000"
        height="600"
        href={installationPhotoUrl(photo.id, photo.revision)}
        preserveAspectRatio="xMidYMid meet"
      />
      <ProjectorSimulationOverlays
        photo={photo}
        geometry={geometry}
        mappings={mappings}
      />
      {geometry.map((item) => (
        <GeometryShape geometry={item} key={item.id} simulation />
      ))}
      {geometry
        .filter((item) => item.kind === "pixel_segment")
        .flatMap((item) =>
          photoPixelPoints(item).map((pixel) => (
            <circle
              className="geometry-simulation-pixel"
              cx={pixel.x * 1000}
              cy={pixel.y * 600}
              key={`${item.id}:${pixel.outputIndex}`}
              r="7"
              style={{
                fill: `hsl(${(pixel.outputIndex * 47) % 360} 90% 60%)`,
              }}
            >
              <title>
                Simulated logical pixel on {item.name} · scene time 0 ms
              </title>
            </circle>
          )),
        )}
    </svg>
  );
}

function filterSimulationGeometry(
  geometry: InstallationGeometry[],
  mode: SimulationPreviewMode,
  target: string,
  projectorResources: string[],
): InstallationGeometry[] {
  if (mode === "full_installation") {
    return geometry;
  }
  if (mode === "logical_composition") {
    return target
      ? geometry.filter((item) => item.semantic_id === target)
      : geometry.filter((item) =>
          ["pixel_segment", "fixture", "path"].includes(item.kind),
        );
  }
  if (mode === "parent_context") {
    return target
      ? geometry.filter(
          (item) =>
            item.semantic_id === target ||
            ["projector_surface", "mask", "audience_zone", "safety_zone"].includes(
              item.kind,
            ),
        )
      : geometry;
  }
  if (mode === "native_projector") {
    const projectors = geometry.filter(
      (item) => item.kind === "projector_surface",
    );
    if (!target) {
      return projectors;
    }
    const resourceIndex = projectorResources.indexOf(target);
    return resourceIndex >= 0 && projectors[resourceIndex]
      ? [projectors[resourceIndex]]
      : [];
  }
  if (target === "lighting") {
    return geometry.filter((item) =>
      ["pixel_segment", "fixture", "path"].includes(item.kind),
    );
  }
  if (!target) {
    return geometry;
  }
  const projectors = geometry.filter(
    (item) => item.kind === "projector_surface",
  );
  const resourceIndex = projectorResources.indexOf(target);
  return resourceIndex >= 0 && projectors[resourceIndex]
    ? [
        projectors[resourceIndex],
        ...geometry.filter((item) => item.kind === "mask"),
      ]
    : [];
}

function generateNormalizedPixelLayout(
  mode: "row" | "grid" | "ring" | "outline" | "individual" | "import",
  rows: number,
  columns: number,
  imported: string,
): { x: number; y: number }[] {
  const safeRows = Math.max(2, Math.min(100, Math.floor(rows)));
  const safeColumns = Math.max(2, Math.min(500, Math.floor(columns)));
  if (mode === "individual") {
    return [{ x: 0.5, y: 0.5 }];
  }
  if (mode === "import") {
    return imported
      .split("\n")
      .map((line) => line.split(",").map(Number))
      .filter(
        (point): point is [number, number] =>
          point.length === 2 &&
          point.every(Number.isFinite) &&
          point.every((value) => value >= 0 && value <= 1),
      )
      .map(([x, y]) => ({ x, y }));
  }
  if (mode === "row") {
    return [
      { x: 0.1, y: 0.5 },
      { x: 0.9, y: 0.5 },
    ];
  }
  if (mode === "ring") {
    return Array.from({ length: safeColumns + 1 }, (_, index) => {
      const angle = (index / safeColumns) * Math.PI * 2 - Math.PI / 2;
      return { x: 0.5 + Math.cos(angle) * 0.4, y: 0.5 + Math.sin(angle) * 0.4 };
    });
  }
  if (mode === "outline") {
    return [
      { x: 0.1, y: 0.1 },
      { x: 0.9, y: 0.1 },
      { x: 0.9, y: 0.9 },
      { x: 0.1, y: 0.9 },
      { x: 0.1, y: 0.1 },
    ];
  }
  return Array.from({ length: safeRows }, (_, row) => {
    const y = 0.1 + (row / (safeRows - 1)) * 0.8;
    return row % 2 === 0
      ? [{ x: 0.1, y }, { x: 0.9, y }]
      : [{ x: 0.9, y }, { x: 0.1, y }];
  }).flat();
}

function DeviceManagement({
  agents,
  installation,
  assignments,
  enrollmentSupported,
  canAdminister,
  onRefresh,
}: {
  agents: TopologyNode[];
  installation: InstallationStatus | null;
  assignments: ResourceAssignment[];
  enrollmentSupported: boolean;
  canAdminister: boolean;
  onRefresh?: () => Promise<void> | void;
}) {
  const [deviceKind, setDeviceKind] =
    useState<PairingToken["device_kind"]>("direct_media_agent");
  const [pairingToken, setPairingToken] = useState<PairingToken | null>(null);
  const pairingSection = useRef<HTMLDetailsElement>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  const [copied, setCopied] = useState<string | null>(null);
  const [capabilityJson, setCapabilityJson] = useState(() =>
    JSON.stringify(capabilityTemplate("direct_media_agent"), null, 2),
  );
  const [capabilityReview, setCapabilityReview] = useState<CapabilityReview | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [discoveryAddress, setDiscoveryAddress] = useState("");
  const [discoveredEndpoints, setDiscoveredEndpoints] = useState<DiscoveredEndpoint[]>([]);
  const [probing, setProbing] = useState(false);

  useEffect(() => {
    if (!pairingToken) {
      return;
    }
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [pairingToken]);

  useEffect(() => {
    if (!canAdminister) {
      return;
    }
    getDiscoveredEndpoints()
      .then(setDiscoveredEndpoints)
      .catch(() => setError("Discovered endpoints could not be loaded."));
  }, [canAdminister]);

  const remainingSeconds = pairingToken
    ? Math.max(0, Math.ceil((new Date(pairingToken.expires_at).getTime() - now) / 1_000))
    : 0;
  const enrollmentCommand = pairingToken
    ? `cueverge-device-enroll --enrollment-url ${window.location.origin}/api/device/v2/enroll --display-name DEVICE_NAME --capabilities ./device-capabilities.json`
    : "";
  const transportSetting = pairingToken
    ? `CUEVERGE_DEVICE_TRANSPORT_URL=${pairingToken.device_transport_url}`
    : "";

  async function copy(label: string, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(label);
    } catch {
      setError("Clipboard access failed. Select and copy the value manually.");
    }
  }

  async function createToken(endpoint?: DiscoveredEndpoint) {
    setCreating(true);
    setError(null);
    try {
      const kind = endpoint?.device_kind ?? deviceKind;
      setDeviceKind(kind);
      setPairingToken(await createPairingToken(kind, endpoint?.id));
      setNow(Date.now());
      setCopied(null);
      if (endpoint) {
        setDiscoveredEndpoints(await getDiscoveredEndpoints());
      }
    } catch {
      setError("Pairing token creation failed.");
    } finally {
      setCreating(false);
    }
  }

  async function reviewCapabilities() {
    setReviewing(true);
    setError(null);
    try {
      const document = JSON.parse(capabilityJson) as unknown;
      setCapabilityReview(await reviewDeviceCapabilities(document));
    } catch {
      setCapabilityReview(null);
      setError("Capability review failed. Check the JSON document and try again.");
    } finally {
      setReviewing(false);
    }
  }

  async function probe() {
    setProbing(true);
    setError(null);
    try {
      await probeEndpoint(discoveryAddress);
      setDiscoveryAddress("");
      setDiscoveredEndpoints(await getDiscoveredEndpoints());
    } catch {
      setError(
        "Discovery failed. Use a trusted HTTPS origin that resolves only to local/private addresses.",
      );
    } finally {
      setProbing(false);
    }
  }

  async function reject(endpointId: string) {
    try {
      await rejectDiscoveredEndpoint(endpointId);
      setDiscoveredEndpoints(await getDiscoveredEndpoints());
    } catch {
      setError("The discovered endpoint could not be rejected.");
    }
  }

  return (
    <Panel
      title="Connect and manage devices"
      description="Enrollment establishes trust. Assign a display role afterward; connection alone is not readiness."
      className="device-management"
    >
      {canAdminister && <button className="primary-button" onClick={() => {
        if (pairingSection.current) {
          pairingSection.current.open = true;
          pairingSection.current.scrollIntoView({ block: "start" });
        }
      }}>Add a device</button>}
      <div className="device-management__grid">
        {canAdminister && (
          <details className="device-management__wide"><summary>Find another controller (advanced)</summary>
          <section
            className="device-management__wide"
            aria-labelledby="endpoint-discovery-title"
          >
            <h3 id="endpoint-discovery-title">Explicit controller discovery</h3>
            <div className="endpoint-discovery-form">
              <label>
                HTTPS controller origin
                <input
                  placeholder="https://controller.local"
                  value={discoveryAddress}
                  onChange={(event) => setDiscoveryAddress(event.target.value)}
                />
              </label>
              <button
                disabled={probing || !discoveryAddress.trim()}
                onClick={() => void probe()}
              >
                {probing ? "Probing…" : "Probe endpoint"}
              </button>
            </div>
            {discoveredEndpoints.length ? (
              <div className="discovered-endpoints">
                {discoveredEndpoints.map((endpoint) => (
                  <article key={endpoint.id}>
                    <div>
                      <strong>{endpoint.base_url}</strong>
                      <small>
                        {humanize(endpoint.device_kind)} · software{" "}
                        {endpoint.software_version} · protocol v
                        {endpoint.protocol_minimum}-v{endpoint.protocol_maximum}
                      </small>
                      <small>{endpoint.capability_kinds.join(", ")}</small>
                    </div>
                    <StatusText
                      tone={
                        endpoint.state === "rejected" || endpoint.state === "pairing"
                          ? "neutral"
                          : endpoint.state === "enrolled"
                            ? "success"
                          : endpoint.compatible
                            ? "success"
                            : "danger"
                      }
                    >
                      {endpoint.state === "rejected"
                        ? "Rejected"
                        : endpoint.state === "pairing"
                          ? "Pairing token issued"
                          : endpoint.state === "enrolled"
                            ? "Enrolled"
                        : endpoint.compatible
                          ? "Compatible · untrusted"
                          : "Incompatible · untrusted"}
                    </StatusText>
                    <div className="discovered-endpoint-actions">
                      {endpoint.state === "discovered" && endpoint.compatible && (
                        <button onClick={() => void createToken(endpoint)}>
                          Prepare enrollment
                        </button>
                      )}
                      {endpoint.state !== "enrolled" && (
                        <button
                          disabled={endpoint.state === "rejected"}
                          onClick={() => void reject(endpoint.id)}
                        >
                          Reject
                        </button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <p className="muted">
                No endpoints discovered. Discovery never grants trust or command authority.
              </p>
            )}
          </section>
          </details>
        )}
        {canAdminister && <details ref={pairingSection} className="device-management__wide"><summary>Device enrollment</summary><section aria-labelledby="pair-device-title">
          <h3 id="pair-device-title">Pair a device</h3>
          {enrollmentSupported ? (
            <>
              <label>
                1. What are you connecting?
                <select
                  value={deviceKind}
                  onChange={(event) => {
                    const kind = event.target.value as PairingToken["device_kind"];
                    setDeviceKind(kind);
                    setCapabilityJson(JSON.stringify(capabilityTemplate(kind), null, 2));
                    setCapabilityReview(null);
                    setPairingToken(null);
                  }}
                >
                  <option value="direct_media_agent">Media player</option>
                  <option value="downstream_controller">Another Cueverge controller</option>
                </select>
              </label>
              <p>2. Check the device's declared capabilities. A compatible declaration is not proof that the device is connected or ready.</p>
              <label>Capability file from the device<input type="file" accept="application/json,.json" onChange={async (event) => {
                const file = event.target.files?.[0];
                if (!file) return;
                if (file.size > 65536) { setError("The capability file exceeds 64 KiB. Use advanced declaration editing if needed."); return; }
                try { setCapabilityJson(await file.text()); setCapabilityReview(null); setError(null); }
                catch { setError("The capability file could not be read."); }
              }} /></label>
              <details><summary>Advanced declaration editing</summary>
              <p>The starting template is an example, not a device observation. Declare only capabilities verified on the device.</p>
              <label>
                Capability document
                <textarea
                  aria-label="Capability document"
                  rows={12}
                  spellCheck={false}
                  value={capabilityJson}
                  onChange={(event) => {
                    setCapabilityJson(event.target.value);
                    setCapabilityReview(null);
                  }}
                />
              </label>
              </details>
              <button disabled={reviewing} onClick={() => void reviewCapabilities()}>
                {reviewing ? "Reviewing…" : "Review capabilities"}
              </button>
              {capabilityReview && (
                <div
                  className={`capability-review capability-review--${
                    capabilityReview.compatible ? "compatible" : "incompatible"
                  }`}
                  role="status"
                >
                  <StatusText tone={capabilityReview.compatible ? "success" : "danger"}>
                    {capabilityReview.compatible
                      ? `Compatible with protocol v${capabilityReview.controller_protocol_version}`
                      : "Compatibility issues found"}
                  </StatusText>
                  {capabilityReview.issues.length > 0 && (
                    <ul>
                      {capabilityReview.issues.map((issue) => (
                        <li key={issue.code}>{issue.message}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
              <button
                className="primary-button"
                disabled={creating || !capabilityReview?.compatible}
                onClick={() => void createToken()}
              >
                {creating ? "Creating…" : "Create one-time token"}
              </button>
              {pairingToken && (
                <div className="pairing-token" role="status">
                  <strong>Copy this token now</strong>
                  <code>{pairingToken.pairing_token}</code>
                  <button
                    disabled={remainingSeconds === 0}
                    onClick={() =>
                      void copy("token", pairingToken.pairing_token)
                    }
                  >
                    {copied === "token" ? "Token copied" : "Copy token"}
                  </button>
                  <small>
                    {remainingSeconds > 0
                      ? `Expires in ${remainingSeconds} seconds and can be used once.`
                      : "Expired. Create a new token before enrolling."}
                  </small>
                  <div className="enrollment-guide">
                    <strong>1. Review the device capability file</strong>
                    <span>
                      Declare only codecs, outputs, storage, and controls verified on the
                      device.
                    </span>
                    <strong>2. Run enrollment on the device</strong>
                    <code>{enrollmentCommand}</code>
                    <button onClick={() => void copy("command", enrollmentCommand)}>
                      {copied === "command" ? "Command copied" : "Copy command"}
                    </button>
                    <strong>3. Configure the authenticated transport</strong>
                    <code>{transportSetting}</code>
                    <button onClick={() => void copy("transport", transportSetting)}>
                      {copied === "transport" ? "Setting copied" : "Copy setting"}
                    </button>
                    <small>
                      The device generates and retains its private key. Paste the one-time
                      token only at the hidden prompt.
                    </small>
                  </div>
                </div>
              )}
            </>
          ) : (
            <p className="muted">Device enrollment is disabled on this controller. The host administrator must enable verified TLS enrollment and the authenticated device listener before pairing.</p>
          )}
        </section></details>}
        {canAdminister && <AgentSoftwareUpdates onRefresh={onRefresh} />}
        {canAdminister && <details><summary>Connection expectations</summary><section aria-labelledby="connection-policy-title">
          <h3 id="connection-policy-title">Connection expectations</h3>
          <div className="policy-list">
            {agents.map((agent) => (
              <ConnectionPolicy
                key={agent.id}
                node={agent}
                onRefresh={onRefresh}
                onError={() => setError("Connection policy update failed.")}
              />
            ))}
          </div>
        </section></details>}
        {canAdminister && <details className="device-management__wide"><summary>Advanced device controls</summary><section className="device-management__wide" aria-labelledby="device-lifecycle-title">
          <h3 id="device-lifecycle-title">Device lifecycle</h3>
          <div className="policy-list">
            {agents.map((agent) => (
              <DeviceLifecycle
                key={agent.id}
                node={agent}
                onRefresh={onRefresh}
                onError={() => setError("Device lifecycle update failed.")}
              />
            ))}
          </div>
        </section></details>}
        {canAdminister && <details className="device-management__wide"><summary>Assign display roles</summary><section className="device-management__wide" aria-labelledby="resource-assignment-title">
          <h3 id="resource-assignment-title">Resource assignments</h3>
          <div className="policy-list">
            {installation?.resources
              .filter((resource) => resource.kind !== "pixel_output")
              .map((resource) => (
                <ResourceAssignmentControl
                  key={resource.resource}
                  resource={resource.resource}
                  capabilityKind={
                    resource.kind === "media_agent"
                      ? "media_playback"
                      : "downstream_control"
                  }
                  agents={agents}
                  assignment={assignments.find(
                    (assignment) => assignment.resource === resource.resource,
                  )}
                  onRefresh={onRefresh}
                  onError={() =>
                    setError(
                      "Assignment update failed. Devices must be disconnected and playback idle.",
                    )
                  }
                />
              ))}
          </div>
        </section></details>}
        <details className="device-management__wide"><summary>Connection checks</summary><section className="device-management__wide" aria-labelledby="connection-test-title">
          <h3 id="connection-test-title">Connection tests</h3>
          <div className="policy-list">
            {agents.map((agent) => (
              <ConnectionTest key={agent.id} node={agent} />
            ))}
          </div>
        </section>
        </details>
      </div>
      {error && <p role="alert">{error}</p>}
    </Panel>
  );
}

function capabilityTemplate(
  deviceKind: PairingToken["device_kind"],
): Record<string, unknown> {
  return {
    schema_version: 1,
    protocol: { minimum: 2, maximum: 2 },
    device_kind: deviceKind,
    software_version: "0.1.0",
    capabilities:
      deviceKind === "direct_media_agent"
        ? [
            {
              kind: "media_playback",
              codecs: ["h264"],
              containers: ["mp4"],
              max_outputs: 1,
              supports_pause: true,
              supports_seek: true,
            },
            {
              kind: "timing_calibration",
              actuator_kinds: ["media", "audio"],
              onset: {
                minimum_ms: -5000,
                maximum_ms: 5000,
                resolution_ms: 5,
                adjustable_while_active: true,
              },
              release: null,
              safe_test_procedure: "maintenance_reference",
              supports_ab_comparison: true,
            },
          ]
        : [
            {
              kind: "downstream_control",
              operations: ["query_status"],
            },
            {
              kind: "timing_calibration",
              actuator_kinds: ["network_light", "smart_switch", "relay"],
              onset: {
                minimum_ms: -5000,
                maximum_ms: 5000,
                resolution_ms: 10,
                adjustable_while_active: false,
              },
              release: {
                minimum_ms: -5000,
                maximum_ms: 5000,
                resolution_ms: 10,
                adjustable_while_active: false,
              },
              safe_test_procedure: "armed_trials",
              supports_ab_comparison: true,
            },
          ],
  };
}

function ConnectionTest({ node }: { node: TopologyNode }) {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<Awaited<
    ReturnType<typeof testDeviceConnection>
  > | null>(null);
  const [error, setError] = useState(false);

  async function run() {
    setTesting(true);
    setError(false);
    try {
      setResult(await testDeviceConnection(node.id.replace("device:", "")));
    } catch {
      setError(true);
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="connection-test-row">
      <div className="connection-test-row__header">
        <div>
          <strong>{node.label}</strong>
          <small>{connectionStatus(node).label}</small>
        </div>
        <button disabled={testing} onClick={() => void run()}>
          {testing ? "Testing…" : "Test connection"}
        </button>
      </div>
      {result && (
        <div className="connection-test-result" role="status">
          <StatusText tone={result.result === "passed" ? "success" : "danger"}>
            {result.result === "passed" ? "All checks passed" : "Action required"}
          </StatusText>
          <ul>
            {result.checks.map((check) => (
              <li key={check.name}>
                <strong>{humanize(check.name)}</strong>
                <span>{check.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {error && <p role="alert">Connection test could not be completed.</p>}
    </div>
  );
}

function AgentSoftwareUpdates({ onRefresh }: { onRefresh?: () => Promise<void> | void }) {
  const [updates, setUpdates] = useState<AgentUpdates | null>(null);
  const [confirming, setConfirming] = useState<string[] | "all" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const updating = updates?.devices.some((device) => device.state === "updating") ?? false;

  const load = useCallback(async () => {
    try {
      setUpdates(await getAgentUpdates());
    } catch {
      setError("Agent update status could not be loaded.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!updating) return;
    const timer = window.setInterval(() => {
      void load();
      void onRefresh?.();
    }, 3_000);
    return () => window.clearInterval(timer);
  }, [updating, load, onRefresh]);

  const updatable = updates?.devices.filter((device) => UPDATABLE_AGENT_STATES.includes(device.state)) ?? [];

  async function start(target: string[] | "all") {
    setBusy(true);
    setError(null);
    try {
      setUpdates(await startAgentUpdates(target === "all" ? undefined : target));
      setConfirming(null);
      await onRefresh?.();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Agent updates could not start.");
    } finally {
      setBusy(false);
    }
  }

  const confirmCount = confirming === "all" ? updatable.length : confirming?.length ?? 0;
  return (
    <details className="device-management__wide"><summary>Agent software updates{updatable.length ? ` (${updatable.length} available)` : ""}</summary>
      <section className="device-management__wide" aria-labelledby="agent-updates-title">
        <h3 id="agent-updates-title">Agent software updates</h3>
        <p className="muted">
          Media hosts install the agent version bundled with this controller. Switch to
          maintenance mode and stop the show first: each agent closes its player, restarts,
          and rolls back automatically if the new version can't reconnect.
        </p>
        {updates?.bundles.length ? (
          <p>
            Offered: {updates.bundles.map((bundle) => `${bundle.platform} ${bundle.version} · ${bundle.build}`).join("; ")}
          </p>
        ) : updates ? (
          <p className="muted">This controller has no agent bundles. Set CUEVERGE_AGENT_BUNDLE_ROOT or use the controller image.</p>
        ) : null}
        {updates?.bundle_faults.map((fault) => <p role="alert" key={fault}>{fault}</p>)}
        <div className="policy-list">
          {updates?.devices.map((device) => (
            <div className="policy-row" key={device.device_id}>
              <div>
                <strong>{device.display_name}</strong>
                <small>{agentSoftwareLabel(device)} · {agentUpdateLabel(device)}</small>
              </div>
              {UPDATABLE_AGENT_STATES.includes(device.state) && (
                <button
                  type="button"
                  aria-label={`Update agent on ${device.display_name}`}
                  disabled={busy || confirming !== null}
                  onClick={() => setConfirming([device.device_id])}
                >
                  Update agent
                </button>
              )}
            </div>
          ))}
        </div>
        {updatable.length > 1 && (
          <button type="button" disabled={busy || confirming !== null} onClick={() => setConfirming("all")}>
            Update all agents ({updatable.length})
          </button>
        )}
        {confirming && (
          <div role="group" aria-label="Confirm agent update">
            <p>
              Update {confirmCount} {confirmCount === 1 ? "agent" : "agents"}? Their players stop and
              the agents restart. Projectors stay dark until the show starts again.
            </p>
            <button type="button" className="primary-button" disabled={busy} onClick={() => void start(confirming)}>
              {busy ? "Starting…" : "Install and restart"}
            </button>
            <button type="button" disabled={busy} onClick={() => setConfirming(null)}>Cancel</button>
          </div>
        )}
        {error && <p role="alert">{error}</p>}
      </section>
    </details>
  );
}

function DeviceLifecycle({
  node,
  onRefresh,
  onError,
}: {
  node: TopologyNode;
  onRefresh?: () => Promise<void> | void;
  onError: () => void;
}) {
  const [saving, setSaving] = useState(false);

  async function update(
    state: "enrolled" | "suspended" | "revoked",
    confirmation: string,
  ) {
    if (!window.confirm(confirmation)) {
      return;
    }
    setSaving(true);
    try {
      await setDeviceEnrollmentState(node.id.replace("device:", ""), state);
      await onRefresh?.();
    } catch {
      onError();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="policy-row device-lifecycle-row">
      <div>
        <strong>{node.label}</strong>
        <small>
          {humanize(node.enrollment)} · {connectionStatus(node).label}
        </small>
      </div>
      <div className="device-actions">
        {node.enrollment === "enrolled" && (
          <button
            disabled={saving}
            onClick={() =>
              void update(
                "suspended",
                `Suspend ${node.label}? Its authenticated session will be rejected.`,
              )
            }
          >
            Suspend
          </button>
        )}
        {node.enrollment === "suspended" && (
          <button
            disabled={saving}
            onClick={() =>
              void update("enrolled", `Resume ${node.label} using its active certificate?`)
            }
          >
            Resume
          </button>
        )}
        {node.enrollment !== "revoked" && (
          <button
            className="danger-button"
            disabled={saving}
            onClick={() =>
              void update(
                "revoked",
                `Permanently revoke ${node.label}? This cannot be undone and requires new enrollment.`,
              )
            }
          >
            Revoke
          </button>
        )}
        {node.enrollment === "revoked" && (
          <span className="muted">No actions available</span>
        )}
      </div>
    </div>
  );
}

function ResourceAssignmentControl({
  resource,
  capabilityKind,
  agents,
  assignment,
  onRefresh,
  onError,
}: {
  resource: string;
  capabilityKind: "media_playback" | "downstream_control";
  agents: TopologyNode[];
  assignment?: ResourceAssignment;
  onRefresh?: () => Promise<void> | void;
  onError: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const currentNode = assignment
    ? agents.find((agent) => agent.id === `device:${assignment.device_id}`)
    : undefined;
  const locked = currentNode?.connection === "connected";
  const candidates = agents.filter(
    (agent) =>
      agent.capabilities.includes(capabilityKind) &&
      (agent.connection === "disconnected" || agent.id === currentNode?.id),
  );

  async function updateAssignment(deviceId: string) {
    setSaving(true);
    try {
      if (deviceId) {
        await setResourceAssignment(
          resource,
          deviceId,
          capabilityKind,
          assignment?.revision ?? null,
        );
      } else if (assignment) {
        await removeResourceAssignment(resource, assignment.revision);
      }
      await onRefresh?.();
    } catch {
      onError();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="policy-row">
      <div>
        <strong>{resource}</strong>
        <small>
          {locked
            ? "Disconnect the current device before changing this assignment"
            : "Changes apply on the device's next authenticated connection"}
        </small>
      </div>
      <label>
        <span className="sr-only">Assigned device for {resource}</span>
        <select
          aria-label={`Assigned device for ${resource}`}
          disabled={saving || locked}
          value={assignment?.device_id ?? ""}
          onChange={(event) => void updateAssignment(event.target.value)}
        >
          <option value="">Unassigned</option>
          {candidates.map((agent) => (
            <option key={agent.id} value={agent.id.replace("device:", "")}>
              {agent.label}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

function ConnectionPolicy({
  node,
  onRefresh,
  onError,
}: {
  node: TopologyNode;
  onRefresh?: () => Promise<void> | void;
  onError: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const policy = node.connection_policy === "on_demand" ? "on_demand" : "continuous";

  async function updatePolicy(nextPolicy: "continuous" | "on_demand") {
    setSaving(true);
    try {
      await setDeviceConnectionPolicy(node.id.replace("device:", ""), nextPolicy);
      await onRefresh?.();
    } catch {
      onError();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="policy-row">
      <div>
        <strong>{node.label}</strong>
        <small>{node.assigned_resources.join(", ") || "Unassigned"}</small>
      </div>
      <label>
        <span className="sr-only">Connection policy for {node.label}</span>
        <select
          aria-label={`Connection policy for ${node.label}`}
          disabled={saving}
          value={policy}
          onChange={(event) =>
            void updatePolicy(event.target.value as "continuous" | "on_demand")
          }
        >
          <option value="continuous">Continuous</option>
          <option value="on_demand">On demand</option>
        </select>
      </label>
    </div>
  );
}

function NodeCard({
  node,
  canOperate = false,
  canAdminister = false,
  onRefresh,
}: {
  node: TopologyNode;
  canOperate?: boolean;
  canAdminister?: boolean;
  onRefresh?: () => Promise<void> | void;
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [playerBusy, setPlayerBusy] = useState(false);
  const [playerError, setPlayerError] = useState<string | null>(null);
  const canControlPlayer =
    canOperate &&
    node.kind === "direct_media_agent" &&
    node.connection === "connected" &&
    node.player_control === true;
  const playerRunning = node.player_state === "ready" || node.player_state === "starting";

  async function changePlayer(action: "start" | "stop") {
    setPlayerBusy(true);
    setPlayerError(null);
    try {
      await controlDevicePlayer(node.id.replace("device:", ""), action);
      await onRefresh?.();
    } catch (error) {
      setPlayerError(error instanceof Error ? error.message : `Could not ${action} the player.`);
    } finally {
      setPlayerBusy(false);
    }
  }
  const connection = connectionStatus(node);
  const sleepWarning = keepAwakeWarning(node);
  const media = mediaCapability(node.capability_document);
  const cache = cacheCapability(node.capability_document);
  const timing = timingCapability(node.capability_document);
  const Icon =
    node.kind === "controller"
      ? Server
      : node.kind === "direct_media_agent"
        ? MonitorPlay
        : node.kind === "downstream_controller"
          ? Network
          : Lightbulb;

  return (
    <article className={`topology-node topology-node--${node.kind}`}>
      <header>
        <span className="topology-node__icon" aria-hidden="true">
          <Icon size={19} />
        </span>
        <div>
          <span>{humanize(node.kind)}</span>
          <h3>{node.label}</h3>
        </div>
        <StatusText tone={connection.tone}>{connection.label}</StatusText>
      </header>
      <p className="topology-node__summary">
        {node.kind === "controller" ? "Your Cueverge controller" :
          node.assigned_resources.length ? `Used for ${node.assigned_resources.join(", ")}` :
            node.kind === "controller_output" && node.reported_pixel_count != null ? `${node.reported_pixel_count} LEDs` :
              "No display role assigned yet"}
      </p>
      {node.compatibility === "conflict" && <p role="alert">{node.compatibility_detail ?? "This device is incompatible with the controller."}</p>}
      {(node.failed_transfers ?? 0) > 0 && <p role="alert">{node.failed_transfers} asset transfers failed. Open details for troubleshooting.</p>}
      {node.artifact_faults?.map((fault) => <p role="alert" key={fault}>{fault}</p>)}
      {(node.enrollment === "suspended" || node.enrollment === "revoked") && <small>{enrollmentLabel(node)}</small>}
      {node.diagnostic && <p role="status">{node.diagnostic}</p>}
      {sleepWarning && <p role="alert">{sleepWarning}</p>}
      {node.agent_update && ["available", "updating", "failed", "rolled_back"].includes(node.agent_update.state) && (
        <p role="status">{agentUpdateLabel(node.agent_update)}</p>
      )}
      {canControlPlayer && (
        <button
          type="button"
          aria-label={`${playerRunning ? "Stop" : "Start"} player on ${node.label}`}
          disabled={playerBusy}
          onClick={() => void changePlayer(playerRunning ? "stop" : "start")}
        >
          {playerBusy ? "Working…" : playerRunning ? "Stop player" : "Start player"}
        </button>
      )}
      {playerError && <p role="alert">{playerError}</p>}
      <button type="button" aria-label={`Details for ${node.label}`} onClick={() => setDetailsOpen(true)}>Device details</button>
      {detailsOpen && <DetailsDialog title={`Device details: ${node.label}`} onClose={() => setDetailsOpen(false)}>
      {(node.kind === "direct_media_agent" || node.kind === "downstream_controller") && (
        <dl className="topology-node__details">
          <Detail
            label="Protocol v2 assignment"
            value={
              node.assigned_resources.length
                ? node.assigned_resources.join(", ")
                : "No logical resource assigned"
            }
          />
          <Detail label="Enrollment" value={enrollmentLabel(node)} />
          <Detail label="Compatibility" value={humanize(node.compatibility)} />
          <Detail
            label="Compatibility detail"
            value={node.compatibility_detail ?? "No compatibility diagnostic"}
          />
          <Detail
            label="Protocol"
            value={
              node.capability_document
                ? `v${node.capability_document.protocol.minimum}–${node.capability_document.protocol.maximum}`
                : node.protocol_version === null
                  ? "Not observed"
                  : `v${node.protocol_version}`
            }
          />
          <Detail
            label="Capabilities"
            value={node.capabilities.length ? node.capabilities.join(", ") : "Not observed"}
          />
          <Detail
            label="Last observation"
            value={
              node.last_seen_age_ms === null
                ? "Unavailable"
                : formatObservationAge(node.last_seen_age_ms)
            }
          />
          <Detail label="Software version" value={node.software_version ?? "Not reported"} />
          {node.kind === "direct_media_agent" && (
            <>
              <Detail label="Keep awake" value={keepAwakeLabel(node)} />
              <Detail label="Agent software" value={agentSoftwareLabel(node.agent_update)} />
              <Detail label="Agent updates" value={agentUpdateLabel(node.agent_update)} />
            </>
          )}
          <Detail
            label="Media formats"
            value={
              media
                ? `${media.containers.join(", ")} · ${media.codecs.join(", ")}`
                : "Not declared"
            }
          />
          <Detail
            label="Output capacity"
            value={media ? `${media.max_outputs} simultaneous output(s)` : "Not declared"}
          />
          <Detail
            label="Playback controls"
            value={
              media
                ? [
                    media.supports_pause ? "Pause" : null,
                    media.supports_seek ? "Seek" : null,
                  ]
                    .filter(Boolean)
                    .join(", ") || "Play/stop only"
                : "Not declared"
            }
          />
          <Detail
            label="Timing calibration"
            value={
              timing
                ? `${timing.actuator_kinds.join(", ")} · ${humanize(
                    timing.safe_test_procedure,
                  )}`
                : "Not declared"
            }
          />
          {timing?.onset && (
            <Detail
              label="Onset adjustment"
              value={formatTimingAdjustment(timing.onset)}
            />
          )}
          {timing?.release && (
            <Detail
              label="Release adjustment"
              value={formatTimingAdjustment(timing.release)}
            />
          )}
          <Detail
            label="Storage"
            value={
              node.storage_free_bytes !== null &&
              node.storage_free_bytes !== undefined &&
              node.storage_total_bytes
                ? `${formatBytes(node.storage_free_bytes)} free of ${formatBytes(
                    node.storage_total_bytes,
                  )}`
                : "Not declared"
            }
          />
          <Detail
            label="Artifacts"
            value={
              node.artifact_count === null || node.artifact_count === undefined
                ? "Not reported"
                : `${node.artifact_count} digest(s) · ${formatBytes(
                    node.artifact_bytes ?? 0,
                  )} · ${node.pinned_artifact_count ?? 0} pinned`
            }
          />
          <Detail
            label="Cache behavior"
            value={
              node.supports_pinning !== null &&
              node.supports_pinning !== undefined
                ? [
                    node.supports_pinning ? "Pinning" : "No pinning",
                    node.supports_resumable_transfer
                      ? "Resumable transfer"
                      : "No resumable transfer",
                  ].join(", ")
                : cache
                ? [
                    cache.supports_pinning ? "Pinning" : null,
                    cache.supports_resumable_transfer ? "Resumable transfer" : null,
                  ]
                    .filter(Boolean)
                    .join(", ") || "Inventory only"
                : node.artifact_count !== null && node.artifact_count !== undefined
                  ? "Inventory reporting only"
                  : "Not declared"
            }
          />
          <Detail
            label="Transfers"
            value={
              node.active_transfers === null || node.active_transfers === undefined
                ? "Not reported"
                : `${node.active_transfers} active · ${node.failed_transfers ?? 0} failed`
            }
          />
          <Detail
            label="Certificate"
            value={certificateExpiry(node.certificate_expires_at)}
          />
          {node.artifact_faults?.map((fault) => (
            <div key={fault} className="diagnostic-error">
              <dt>Storage fault</dt>
              <dd>{fault}</dd>
            </div>
          ))}
          <Detail
            label="Latency / clock drift"
            value={
              node.latency_ms === null && node.clock_drift_ms === null
                ? "Not reported"
                : `${node.latency_ms ?? "—"} ms / ${node.clock_drift_ms ?? "—"} ms`
            }
          />
        </dl>
      )}
      {node.kind === "controller" && (
        <p className="topology-node__note">
          <Network aria-hidden="true" size={15} />
          Root of the currently observable topology
        </p>
      )}
      {node.kind === "controller_output" && (
        <>
          <dl className="topology-node__details">
            <Detail label="Firmware" value={node.software_version ?? "Not reported"} />
            <Detail
              label="Pixels"
              value={
                node.reported_pixel_count === null ||
                node.reported_pixel_count === undefined
                  ? "Not reported"
                  : node.reported_pixel_count.toLocaleString()
              }
            />
            <Detail
              label="Wi-Fi signal"
              value={
                node.signal_percent === null || node.signal_percent === undefined
                  ? "Not reported"
                  : `${node.signal_percent}%`
              }
            />
            <Detail
              label="Output state"
              value={
                node.output_on === null || node.output_on === undefined
                  ? "Not reported"
                  : node.output_on
                    ? "On"
                    : "Off"
              }
            />
            <Detail
              label="HTTP latency"
              value={node.latency_ms === null ? "Not reported" : `${node.latency_ms} ms`}
            />
          </dl>
          <p className="topology-node__note">
            <Cable aria-hidden="true" size={15} />
            {node.diagnostic ?? "Controller-owned WLED output observed without changing state"}
          </p>
        </>
      )}
      {node.kind === "direct_media_agent" && (
        <AgentSettingsPanel deviceId={node.id.replace("device:", "")} canEdit={canAdminister} />
      )}
      </DetailsDialog>}
    </article>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function Summary({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  tone: StatusTone;
}) {
  return (
    <article className="topology-summary__item">
      <span>{label}</span>
      <strong>{value}</strong>
      <StatusText tone={tone}>{detail}</StatusText>
    </article>
  );
}

function mediaCapability(
  document: DeviceCapabilityDocument | null | undefined,
): MediaPlaybackCapability | null {
  const capability = document?.capabilities.find(
    (candidate) => candidate.kind === "media_playback",
  );
  return capability && "codecs" in capability
    ? (capability as MediaPlaybackCapability)
    : null;
}

function cacheCapability(
  document: DeviceCapabilityDocument | null | undefined,
): ArtifactCacheCapability | null {
  const capability = document?.capabilities.find(
    (candidate) => candidate.kind === "artifact_cache",
  );
  return capability && "total_bytes" in capability
    ? (capability as ArtifactCacheCapability)
    : null;
}

function timingCapability(
  document: DeviceCapabilityDocument | null | undefined,
): TimingCalibrationCapability | null {
  const capability = document?.capabilities.find(
    (candidate) => candidate.kind === "timing_calibration",
  );
  return capability && "actuator_kinds" in capability
    ? (capability as TimingCalibrationCapability)
    : null;
}

function formatTimingAdjustment(
  adjustment: TimingAdjustment,
): string {
  return `${adjustment.minimum_ms} to ${adjustment.maximum_ms} ms · ${adjustment.resolution_ms} ms steps · ${
    adjustment.adjustable_while_active ? "live adjustable" : "armed trials"
  }`;
}

function formatBytes(value: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = value;
  let unit = 0;
  while (amount >= 1_000 && unit < units.length - 1) {
    amount /= 1_000;
    unit += 1;
  }
  return `${amount.toFixed(amount >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function certificateExpiry(value: string | null | undefined): string {
  if (!value) {
    return "Not reported";
  }
  const expiry = new Date(value);
  const remainingDays = Math.ceil((expiry.getTime() - Date.now()) / 86_400_000);
  if (remainingDays < 0) {
    return `Expired ${expiry.toLocaleDateString()}`;
  }
  return `Expires ${expiry.toLocaleDateString()} · ${remainingDays} day(s)`;
}

function formatObservationAge(ageMs: number): string {
  if (ageMs < 1_000) {
    return "Just now";
  }
  if (ageMs < 60_000) {
    return `${Math.floor(ageMs / 1_000)} seconds ago`;
  }
  if (ageMs < 3_600_000) {
    return `${Math.floor(ageMs / 60_000)} minutes ago`;
  }
  return `${Math.floor(ageMs / 3_600_000)} hours ago`;
}

function enrollmentLabel(node: TopologyNode): string {
  return humanize(node.enrollment);
}

function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
