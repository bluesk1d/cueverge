import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, InputHTMLAttributes, PointerEvent as ReactPointerEvent } from "react";

import {
  assetArtifactUrl,
  AssetRecord,
  cancelRenderJob,
  compositionPreviewFrameUrl,
  createRenderJob,
  deployRenderArtifact,
  getInstallationGeometry,
  getInstallationMapperExport,
  getInstallationPhotos,
  getLightingPreview,
  getPreviewEvaluation,
  getRenderJobs,
  getStageLightingPreview,
  getStageCoverage,
  InstallationMapperExport,
  InstallationGeometry,
  InstallationPhoto,
  installationPhotoUrl,
  LightingPreview,
  PreviewEvaluation,
  projectorSimulationOverlayUrl,
  ProjectDraftRecord,
  RenderJob,
  renderArtifactPreviewFrameUrl,
  evaluateSyncAdmission,
  recordSyncRehearsal,
  SyncAdmission,
  StageCoverage,
  StageLightingPreview,
} from "../api";
import { VirtualizedList } from "../components/VirtualizedList";
import { SurfaceMediaOverlay } from "../components/SurfaceMediaOverlay";
import { DetailsDialog } from "../components/DetailsDialog";
import { SceneHouseView } from "../components/SceneHouseView";
import { SceneOutputActions } from "../components/SceneOutputActions";
import {
  formatTimecode,
  lightingSurfaceLabel,
  mediaOutputSourceMs,
  sceneMediaOutputs,
  stackLayerRows,
  trimKeyframes,
} from "./timelineUtils";
import type { SceneMediaEndBehavior, SceneMediaOutputRef } from "./timelineUtils";

const TIMELINE_SOURCE_MIME = "application/x-cueverge-timeline-source";


const TIMELINE_LAYER_ROW_HEIGHT = 22;


function clampNumber(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

export type TimelineToolTab = "layers" | "cues" | "tracks" | "compositions" | "render" | "ranges";

const TIMELINE_TOOL_TABS: [TimelineToolTab, string][] = [
  ["layers", "Media layers"],
  ["cues", "Markers & cues"],
  ["tracks", "Tracks"],
  ["compositions", "Compositions"],
  ["render", "Render & bake"],
  ["ranges", "Loop & ranges"],
];


type TrackKind =
  | "media_output"
  | "audio_reference"
  | "lighting_output"
  | "fixture"
  | "group"
  | "path";

type TimelineTrack = {
  id: string;
  kind: TrackKind;
  name: string;
  resource_binding: string;
  built_in?: boolean;
};

type SyncMarker = {
  id: string;
  name: string;
  time_ms: number;
};

type SyncCueKind = "visual" | "audio" | "lighting" | "switch" | "physical_effect";

type LightingPrimitive =
  | "solid"
  | "blackout"
  | "envelope"
  | "pulse"
  | "chase"
  | "path_sweep"
  | "flicker"
  | "sparkle"
  | "ripple"
  | "color_transition";

type LightingCueProperties = {
  primitive: LightingPrimitive;
  duration_ms: number;
  target: { kind: "segment" | "fixture" | "path"; id: string } | null;
  color: string;
  end_color: string | null;
  brightness: number;
  end_brightness: number | null;
  fade_in_ms: number;
  fade_out_ms: number;
  period_ms: number;
  width_pixels: number;
  density: number;
  seed: number;
  reverse: boolean;
  blend_mode: "replace" | "add" | "max" | "multiply";
  precedence: number;
};

type SyncCue = {
  id: string;
  name: string;
  kind: SyncCueKind;
  track_id: string;
  time_ms: number | null;
  marker_id: string | null;
  marker_offset_ms: number;
  lighting?: LightingCueProperties;
};

type Composition = {
  id: string;
  name: string;
  duration_ms: number;
  space_kind: "projector_native" | "lighting_surface" | "stage";
  target_id: string;
  width: number;
  height: number;
  local_time_offset_ms: number;
  children: string[];
  stage_lighting_surfaces: string[];
  stage_projector_resources?: string[];
  /** Stage only: composition units per source pixel at scale 1 (media size independent of render size). */
  source_pixel_units?: number;
  /** Stage LED bakes: what undrawn LEDs show (unset: ambient when the background is black). */
  led_background?: "ambient" | "background";
  safe_points_ms: number[];
  protected_intervals: { start_ms: number; end_ms: number }[];
  render_settings: {
    render_pipeline_revision: 1;
    render_width: number;
    render_height: number;
    frame_rate_numerator: number;
    frame_rate_denominator: number;
    working_color_space: "linear_srgb";
    working_alpha_mode: "premultiplied";
    scaling_filter: "lanczos" | "bicubic";
    output_background_color: string;
    media_end_behavior: "stop" | "hold_last_frame" | "loop";
    output_profile: {
      preset_id: "low_power_h264" | "legacy_h264";
      container: "mp4";
      video_codec: "h264";
      video_profile: "main" | "baseline";
      video_level: "4.0" | "3.1";
      pixel_format: "yuv420p";
      max_video_bitrate_kbps: number;
      audio_codec: "aac";
      audio_sample_rate: 48000;
      audio_channel_layout: "stereo";
      loudness_target_lufs: number;
      keyframe_interval_frames: number;
      b_frames: number;
      hardware_decoder_compatibility: string;
    };
  };
  lighting_settings: {
    canvas_width: number;
    canvas_height: number;
    sampling: "nearest" | "bilinear" | "area";
    gamma: number;
    brightness: number;
  };
};

type CompositionInstance = {
  id: string;
  composition_id: string;
  start_offset_ms: number;
  time_scale: number;
  parameter_overrides: Record<string, unknown>;
  placement: { x: number; y: number };
};

type TransformKeyframe = {
  id: string;
  time_ms: number;
  position: [number, number];
  scale: [number, number];
  rotation_deg: number;
  anchor: [number, number];
  opacity: number;
  crop: [number, number, number, number];
  interpolation: {
    kind: "hold" | "linear" | "ease_in" | "ease_out" | "bezier";
    temporal_bezier: [number, number, number, number] | null;
    spatial_in: [number, number] | null;
    spatial_out: [number, number] | null;
  };
};

type TransformLayer = {
  layer_schema_version: 1;
  id: string;
  name: string;
  kind: "image" | "video" | "audio" | "composition";
  source_id: string;
  track_id: string;
  start_ms: number;
  duration_ms: number;
  in_point_ms: number;
  out_point_ms: number;
  playback?: "hold" | "loop" | "once";
  z_index: number;
  fade_in_ms: number;
  fade_out_ms: number;
  audio: {
    gain_db: number;
    mute: boolean;
    solo: boolean;
    mix_order: number;
  } | null;
  alpha_interpretation: "opaque" | "straight" | "premultiplied" | "black_transparent" | null;
  source_color_space: "srgb" | "bt709" | "linear_srgb" | null;
  scaling_behavior: "fit" | "fill" | "stretch" | "native" | null;
  keyframes: TransformKeyframe[];
  corner_pin?: [number, number][] | null;
  mask?: LayerMask | null;
};

type LayerMask = {
  points: [number, number][];
  feather: number;
  invert: boolean;
};

type EditorialAudioSource = {
  id: string;
  name: string;
  asset: AssetRecord;
  start_ms: number;
  duration_ms: number;
  in_point_ms: number;
  gain_db: number;
  mute: boolean;
  solo: boolean;
  fade_in_ms: number;
  fade_out_ms: number;
};

type SceneOperationalPolicy = {
  duration_ms: number;
  priority: number;
  completion_policy: "loop" | "select_next" | "transition_to" | "hold" | "stop";
  completion_target: string;
  request_policy:
    | "priority"
    | "immediate"
    | "at_safe_point"
    | "after_completion"
    | "ignore";
  request_ttl_ms: number;
  resources: string;
  mode: "media_synchronized" | "controller_timed";
  clock_source: string;
  media_required: Record<string, boolean>;
  lighting_required: Record<string, boolean>;
  media_offsets: Record<string, number>;
  media_assets: Record<string, string | null>;
  media_end_behaviors: Record<string, SceneMediaEndBehavior>;
  media_repeat_counts: Record<string, number | null>;
  lighting_offsets: Record<string, number>;
};

type CuePreset = {
  id: string;
  name: string;
  lighting: LightingCueProperties;
};

type LightingAuthorLayer = {
  id: string;
  name: string;
  mode: "spatial" | "path" | "fixture";
  start_ms: number;
  duration_ms: number;
  persistent: boolean;
  precedence: number;
  blend_mode: "replace" | "add" | "max" | "multiply";
  opacity: number;
  spatial?: {
    primitive:
      | "solid"
      | "linear_gradient"
      | "radial_gradient"
      | "shape"
      | "procedural_noise";
    color: string;
    end_color: string;
    seed: number;
    position: [number, number];
    size: [number, number];
    radius: number;
  } | {
    primitive: "ripple";
    coordinate_space: "surface_local" | "stage";
    origin: [number, number];
    speed: number;
    ring_width: number;
    color: string;
    repeat_ms: number | null;
  };
  path?: {
    path_id: string;
    primitive: "chase" | "pulse" | "sweep";
    position: number;
    width: number;
    direction: "forward" | "reverse";
    fill: number;
    color: string;
  };
  fixture?: {
    target_kind: "fixture" | "group";
    target_id: string;
    primitive: "solid" | "pulse";
    color: string;
    brightness: number;
  };
};

type TimelineDiagnostic = {
  code: string;
  message: string;
  object_id: string;
};

type TimelineSnapshot = {
  compositions: Record<string, Composition>;
  sceneCompositions: Record<string, string[]>;
  compositionInstances: Record<string, CompositionInstance[]>;
  syncMarkers: Record<string, SyncMarker[]>;
  syncCues: Record<string, SyncCue[]>;
  transformLayers: Record<string, TransformLayer[]>;
  cuePresets: CuePreset[];
  safePoints: number[];
  regions: { start_ms: number; end_ms: number }[];
  customTracks: TimelineTrack[];
  scenePolicy: SceneOperationalPolicy;
  lightingAuthorLayers: Record<string, LightingAuthorLayer[]>;
};

export function TimelineEditor({
  draft,
  assets,
  canEdit,
  projectId,
  onSave,
  selectedSceneId,
  onSceneChange,
  onOutputsChanged,
  initialCompositionId,
  initialToolTab,
}: {
  draft: ProjectDraftRecord;
  assets: AssetRecord[];
  canEdit: boolean;
  projectId: string;
  onSave: (draft: ProjectDraftRecord["draft"]) => Promise<void>;
  selectedSceneId?: string;
  onSceneChange?: (id: string) => void;
  onOutputsChanged?: (updated: ProjectDraftRecord) => void;
  initialCompositionId?: string;
  initialToolTab?: TimelineToolTab;
}) {
  const [toolTab, setToolTab] = useState<TimelineToolTab>(initialToolTab ?? "layers");
  const [sceneSettingsOpen, setSceneSettingsOpen] = useState(false);
  const [compositionSettingsOpen, setCompositionSettingsOpen] = useState(false);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [sceneId, setSceneId] = useState(draft.draft.scenes[0]?.id ?? "");
  const scene =
    draft.draft.scenes.find((candidate) => candidate.id === (selectedSceneId ?? sceneId)) ??
    draft.draft.scenes[0];
  const [scenePolicy, setScenePolicy] = useState<SceneOperationalPolicy>(() =>
    readSceneOperationalPolicy(scene),
  );
  const [compositions, setCompositions] = useState<Record<string, Composition>>(
    () => compositionMap(draft.draft.editor_metadata.compositions),
  );
  const [sceneCompositions, setSceneCompositions] = useState<Record<string, string[]>>(
    () => stringArrayMap(draft.draft.editor_metadata.scene_compositions),
  );
  const [compositionInstances, setCompositionInstances] = useState<
    Record<string, CompositionInstance[]>
  >(() => compositionInstanceMap(draft.draft.editor_metadata.composition_instances));
  // A deep link (such as the scene's LED bake chip) opens one composition once.
  const initialCompositionRef = useRef(
    initialCompositionId && compositions[initialCompositionId] ? initialCompositionId : null,
  );
  const [activeCompositionId, setActiveCompositionId] = useState(
    () =>
      initialCompositionRef.current ??
      defaultCompositionForScene(scene, compositions, sceneCompositions, compositionInstances),
  );
  useEffect(() => {
    if (initialCompositionRef.current) {
      setActiveCompositionId(initialCompositionRef.current);
      initialCompositionRef.current = null;
      return;
    }
    setActiveCompositionId(
      defaultCompositionForScene(scene, compositions, sceneCompositions, compositionInstances),
    );
    // Only a scene switch chooses the default tab; later composition edits keep the user's tab.
  }, [scene?.id]);
  const [newCompositionName, setNewCompositionName] = useState("");
  const [newCompositionDuration, setNewCompositionDuration] = useState(5000);
  const [newCompositionSpace, setNewCompositionSpace] =
    useState<Composition["space_kind"]>("projector_native");
  const [newCompositionTarget, setNewCompositionTarget] = useState("");
  const [newCompositionWidth, setNewCompositionWidth] = useState(1920);
  const [newCompositionHeight, setNewCompositionHeight] = useState(1080);
  const [newCompositionOffset, setNewCompositionOffset] = useState(0);
  const [childCompositionId, setChildCompositionId] = useState("");
  const [instanceStartOffset, setInstanceStartOffset] = useState(0);
  const [instanceTimeScale, setInstanceTimeScale] = useState(1);
  const [instanceX, setInstanceX] = useState(0);
  const [instanceY, setInstanceY] = useState(0);
  const [instanceOverrides, setInstanceOverrides] = useState("{}");
  const [replacementByComposition, setReplacementByComposition] = useState<
    Record<string, string>
  >({});
  const [stageCoverage, setStageCoverage] = useState<StageCoverage | null>(null);
  const [mapperExport, setMapperExport] =
    useState<InstallationMapperExport | null>(null);
  const composition = activeCompositionId
    ? compositions[activeCompositionId]
    : undefined;
  const editingSpaceId = composition?.id ?? scene?.id ?? "";
  const duration = composition?.duration_ms ?? (scene ? scenePolicy.duration_ms : 1000);
  const localOffset = composition?.local_time_offset_ms ?? 0;
  const [zoom, setZoom] = useState(80);
  const [snapMs, setSnapMs] = useState(100);
  const [playhead, setPlayhead] = useState(0);
  const [isPreviewPlaying, setIsPreviewPlaying] = useState(false);
  const [previewPlaybackRate, setPreviewPlaybackRate] = useState(1);
  const [previewEvaluation, setPreviewEvaluation] =
    useState<PreviewEvaluation | null>(null);
  const [previewEvaluationError, setPreviewEvaluationError] =
    useState<string | null>(null);
  const playbackFrameRef = useRef<number | null>(null);
  const [safePoints, setSafePoints] = useState<number[]>([]);
  const [regions, setRegions] = useState<{ start_ms: number; end_ms: number }[]>([]);
  const [regionStart, setRegionStart] = useState(0);
  const [regionEnd, setRegionEnd] = useState(Math.min(1000, duration));
  const timelineViews = metadataRecords(draft.draft.editor_metadata.timeline_views);
  const sceneView = metadataRecord(timelineViews[editingSpaceId]);
  const [loopStart, setLoopStart] = useState(0);
  const [loopEnd, setLoopEnd] = useState(duration);
  const timelineTracks = metadataRecord(draft.draft.editor_metadata.timeline_tracks);
  const [syncMarkers, setSyncMarkers] = useState<Record<string, SyncMarker[]>>(
    () => syncMarkerMap(draft.draft.editor_metadata.timeline_markers),
  );
  const [syncCues, setSyncCues] = useState<Record<string, SyncCue[]>>(
    () => syncCueMap(draft.draft.editor_metadata.timeline_cues),
  );
  const [newMarkerName, setNewMarkerName] = useState("");
  const [newCueName, setNewCueName] = useState("");
  const [newCueKind, setNewCueKind] = useState<SyncCueKind>("visual");
  const [newCueTrackId, setNewCueTrackId] = useState("");
  const [newCueMarkerId, setNewCueMarkerId] = useState("");
  const [newCueOffset, setNewCueOffset] = useState(0);
  const [newCueDuration, setNewCueDuration] = useState(500);
  const [transformLayers, setTransformLayers] = useState<
    Record<string, TransformLayer[]>
  >(() =>
    transformLayerMap(draft.draft.editor_metadata.timeline_transform_layers),
  );
  const [selectedTransformLayerId, setSelectedTransformLayerId] = useState("");
  const [inspectorTarget, setInspectorTarget] = useState<"layer" | "track">("layer");
  const canvasRef = useRef<HTMLDivElement>(null);
  const [markerDrag, setMarkerDrag] = useState<{
    from: number;
    to: number;
    startX: number;
    moved: boolean;
  } | null>(null);
  const suppressMarkerClickRef = useRef(false);
  const gestureRef = useRef({ active: false, recorded: false });
  const stashedTracksRef = useRef<Record<string, TimelineTrack[]>>({});
  const previousSpaceRef = useRef<string | null>(null);
  const previousLoadedSceneRef = useRef<string | null>(null);
  // Values the space-load effect is applying; the render that shows them is a baseline, not an edit.
  const pendingSpaceLoadRef = useRef<{ values: string; scenePolicy: string | null; passes: number } | null>(null);
  const timelineDirtyRef = useRef(false);
  const [viewerMode, setViewerMode] = useState<"house" | "layout">("house");
  const [sceneViewerMode, setSceneViewerMode] = useState<"house" | "monitor">("house");
  const playheadDragRef = useRef(false);
  const [layerDrag, setLayerDrag] = useState<{
    id: string;
    mode: "move" | "start" | "end";
    startX: number;
    moved: boolean;
    start: number;
    duration: number;
  } | null>(null);
  const [keyframeDrag, setKeyframeDrag] = useState<{
    layerId: string;
    keyframeId: string;
    startX: number;
    moved: boolean;
    time: number;
  } | null>(null);
  const suppressLayerClickRef = useRef(false);
  const [binSearch, setBinSearch] = useState("");
  const [binMessage, setBinMessage] = useState<string | null>(null);
  const [pendingPlacement, setPendingPlacement] = useState<{
    sourceId: string;
    trackId: string;
    startMs: number;
    naturalMs: number;
    name: string;
  } | null>(null);
  const [dropTargetId, setDropTargetId] = useState<string | null>(null);
  const [presetDialogOpen, setPresetDialogOpen] = useState(false);
  const [preset, setPreset] = useState<CompositionPreset>("stage");
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const sceneNames = useMemo(
    () => Object.fromEntries(draft.draft.scenes.map((candidate) => [candidate.id, candidate.name])),
    [draft.draft.scenes],
  );
  const [presetTarget, setPresetTarget] = useState("");
  const [presetName, setPresetName] = useState("");
  const [presetDuration, setPresetDuration] = useState(0);
  const [presetAddToScene, setPresetAddToScene] = useState(true);
  const [selectedTransformKeyframeId, setSelectedTransformKeyframeId] =
    useState("");
  const [newTransformName, setNewTransformName] = useState("");
  const [newTransformKind, setNewTransformKind] =
    useState<TransformLayer["kind"]>("image");
  const [newTransformSource, setNewTransformSource] = useState("");
  const [newTransformTrack, setNewTransformTrack] = useState("");
  const [newTransformDuration, setNewTransformDuration] = useState(1000);
  const [spatialSnapMode, setSpatialSnapMode] = useState<
    "none" | "landmarks" | "surfaces" | "all"
  >("all");
  const [cuePresets, setCuePresets] = useState<CuePreset[]>(() =>
    cuePresetArray(draft.draft.editor_metadata.timeline_cue_presets),
  );
  const [newPresetName, setNewPresetName] = useState("");
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [compoundSelection, setCompoundSelection] = useState<string[]>([]);
  const [compoundShiftMs, setCompoundShiftMs] = useState(0);
  const historyRef = useRef<TimelineSnapshot[]>([]);
  const historyIndexRef = useRef(-1);
  const restoringHistoryRef = useRef(false);
  const savedSnapshotRef = useRef<string | null>(null);
  const [timelineDirty, setTimelineDirty] = useState(false);
  const [historyVersion, setHistoryVersion] = useState(0);
  const [renderJobs, setRenderJobs] = useState<RenderJob[]>([]);
  const hasPendingRenderJobs = renderJobs.some(
    (job) => job.state === "queued" || job.state === "running",
  );
  const [renderJobError, setRenderJobError] = useState<string | null>(null);
  const [selectedArtifactId, setSelectedArtifactId] = useState("");
  const [renderPreviewMode, setRenderPreviewMode] = useState<
    "source" | "baked" | "side_by_side" | "difference"
  >("source");
  const [syncAdmission, setSyncAdmission] = useState<SyncAdmission | null>(null);
  const [rehearsalStartError, setRehearsalStartError] = useState(0);
  const [rehearsalDrift, setRehearsalDrift] = useState(0);
  const [rehearsalDuration, setRehearsalDuration] = useState(600);
  const [rehearsalNotes, setRehearsalNotes] = useState("");
  const [deployedArtifactId, setDeployedArtifactId] = useState<string | null>(
    null,
  );
  const [lightingAuthorLayers, setLightingAuthorLayers] = useState<
    Record<string, LightingAuthorLayer[]>
  >(() =>
    lightingAuthorLayerMap(
      draft.draft.editor_metadata.lighting_author_layers,
    ),
  );
  const [selectedLightingLayerId, setSelectedLightingLayerId] = useState("");
  const [newLightingLayerName, setNewLightingLayerName] = useState("");
  const [newLightingLayerMode, setNewLightingLayerMode] =
    useState<LightingAuthorLayer["mode"]>("spatial");
  const spaceLightingLayers = lightingAuthorLayers[editingSpaceId] ?? [];
  const selectedLightingLayer = spaceLightingLayers.find(
    (layer) => layer.id === selectedLightingLayerId,
  );
  const [customTracks, setCustomTracks] = useState<TimelineTrack[]>([]);
  const [selectedTrackId, setSelectedTrackId] = useState("");
  const [newTrackKind, setNewTrackKind] =
    useState<Exclude<TrackKind, "media_output" | "lighting_output">>(
      "audio_reference",
    );
  const [newTrackName, setNewTrackName] = useState("");
  const [newTrackBinding, setNewTrackBinding] = useState("");
  const width = Math.max(640, (duration / 1000) * zoom);
  const localPlayhead = Math.max(
    0,
    Math.min(duration, playhead - localOffset),
  );
  const breadcrumbs = compositionBreadcrumbs(
    compositions,
    sceneCompositions[scene?.id ?? ""] ?? [],
    activeCompositionId,
  );
  const installationId =
    typeof draft.draft.editor_metadata.installation_id === "string"
      ? draft.draft.editor_metadata.installation_id
      : null;
  const mappedProjectorResources = (mapperExport?.projectors ?? []).flatMap(
    (value) => {
      const projector = metadataRecord(value);
      return typeof projector.resource === "string"
        ? [projector.resource]
        : [];
    },
  );
  const stageProjectorResources =
    composition?.space_kind === "stage" && composition.stage_projector_resources
      ? mappedProjectorResources.filter((resource) =>
          composition.stage_projector_resources?.includes(resource),
        )
      : mappedProjectorResources;
  const lightsOnlyStage =
    composition?.space_kind === "stage" && composition.stage_projector_resources?.length === 0;
  const completedRenderJobs = renderJobs.filter(
    (job) =>
      job.composition_id === composition?.id &&
      job.state === "completed" &&
      job.artifact_id,
  );
  const selectedArtifactJob = completedRenderJobs.find(
    (job) => job.artifact_id === selectedArtifactId,
  );
  const frameDurationMs = composition
    ? (1000 * composition.render_settings.frame_rate_denominator) /
      composition.render_settings.frame_rate_numerator
    : 1000 / 30;
  const evaluationTimestampMs = Math.min(
    duration,
    Math.max(
      0,
      Math.floor(localPlayhead / frameDurationMs) * frameDurationMs,
    ),
  );

  useEffect(() => {
    if (!installationId) {
      return;
    }
    Promise.all([
      getStageCoverage(installationId),
      getInstallationMapperExport(installationId),
    ])
      .then(([coverage, exported]) => {
        setStageCoverage(coverage);
        setMapperExport(exported);
      })
      .catch(() => {
        setStageCoverage(null);
        setMapperExport(null);
      });
  }, [installationId]);

  useEffect(() => {
    let active = true;
    getPreviewEvaluation(
      projectId,
      editingSpaceId,
      Math.round(evaluationTimestampMs),
    )
      .then((evaluation) => {
        if (active) {
          setPreviewEvaluation(evaluation);
          setPreviewEvaluationError(null);
        }
      })
      .catch((error) => {
        if (active) {
          setPreviewEvaluation(null);
          setPreviewEvaluationError(
            error instanceof Error
              ? error.message
              : "Backend preview evaluation is unavailable.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, [editingSpaceId, evaluationTimestampMs, projectId]);

  useEffect(() => {
    if (
      selectedArtifactId &&
      completedRenderJobs.some((job) => job.artifact_id === selectedArtifactId)
    ) {
      return;
    }
    setSelectedArtifactId(completedRenderJobs[0]?.artifact_id ?? "");
    if (!completedRenderJobs.length) {
      setRenderPreviewMode("source");
    }
  }, [completedRenderJobs, selectedArtifactId]);

  useEffect(() => {
    let active = true;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const jobs = await getRenderJobs(projectId);
        if (!active) {
          return;
        }
        setRenderJobs(jobs);
        setRenderJobError(null);
        if (jobs.some((job) => job.state === "queued" || job.state === "running")) {
          timeout = setTimeout(() => void load(), 1000);
        }
      } catch (error) {
        if (active) {
          setRenderJobError(
            error instanceof Error ? error.message : "Render jobs are unavailable.",
          );
        }
      }
    };
    void load();
    return () => {
      active = false;
      if (timeout) {
        clearTimeout(timeout);
      }
    };
  }, [projectId, hasPendingRenderJobs]);

  useEffect(() => {
    if (lightsOnlyStage) {
      setSyncAdmission(
        (composition?.stage_lighting_surfaces.length ?? 0) > 0
          ? {
              allowed: true,
              code: "lights_only_stage",
              message: "No projector output is baked. Rehearse LED timing before live use.",
              rehearsal: null,
            }
          : {
              allowed: false,
              code: "lights_only_stage_without_leds",
              message: "Choose at least one LED output for a lights-only overlay.",
              rehearsal: null,
            },
      );
      return;
    }
    if (
      composition?.space_kind === "stage" &&
      stageProjectorResources.length === 1 &&
      composition.stage_lighting_surfaces.length > 0
    ) {
      setSyncAdmission({
        allowed: true,
        code: "single_projector_stage",
        message: "No cross-projector synchronization is needed. Rehearse LED/projector latency before live use.",
        rehearsal: null,
      });
      return;
    }
    if (composition?.space_kind !== "stage" || stageProjectorResources.length < 2) {
      setSyncAdmission(null);
      return;
    }
    let active = true;
    evaluateSyncAdmission(stageProjectorResources)
      .then((admission) => {
        if (active) {
          setSyncAdmission(admission);
        }
      })
      .catch((error) => {
        if (active) {
          setSyncAdmission({
            allowed: false,
            code: "sync_admission_unavailable",
            message:
              error instanceof Error
                ? error.message
                : "Synchronization admission is unavailable.",
            rehearsal: null,
          });
        }
      });
    return () => {
      active = false;
    };
  }, [
    composition?.id, composition?.space_kind, lightsOnlyStage,
    composition?.stage_lighting_surfaces.length, stageProjectorResources.join("|"),
  ]);

  useEffect(() => {
    const loadedSafePoints = [
      ...(composition?.safe_points_ms ??
        scene?.scheduling?.safe_points_ms ??
        []),
    ];
    const loadedRegions = [
      ...(composition?.protected_intervals ??
        scene?.scheduling?.protected_intervals ??
        []),
    ];
    setSafePoints(loadedSafePoints);
    setRegions(loadedRegions);
    setRegionStart(0);
    setRegionEnd(Math.min(1000, scene?.duration_ms ?? 1000));
    setLoopStart(numberValue(sceneView.loop_start_ms, 0));
    setLoopEnd(numberValue(sceneView.loop_end_ms, scene?.duration_ms ?? 1000));
    setZoom(numberValue(sceneView.zoom_px_per_second, 80));
    setSnapMs(numberValue(sceneView.quantization_ms, 100));
    if (previousSpaceRef.current !== null && previousSpaceRef.current !== editingSpaceId) {
      stashedTracksRef.current[previousSpaceRef.current] = customTracks;
    }
    previousSpaceRef.current = editingSpaceId;
    const loadedTracks =
      stashedTracksRef.current[editingSpaceId] ?? trackArray(timelineTracks[editingSpaceId]);
    setCustomTracks(loadedTracks);
    const sceneChanged = previousLoadedSceneRef.current !== (scene?.id ?? null);
    previousLoadedSceneRef.current = scene?.id ?? null;
    pendingSpaceLoadRef.current = {
      values: JSON.stringify([loadedSafePoints, loadedRegions, loadedTracks]),
      scenePolicy: sceneChanged ? JSON.stringify(readSceneOperationalPolicy(scene)) : null,
      passes: 0,
    };
    setSelectedTrackId("");
    setSelectedTransformLayerId("");
    setSelectedTransformKeyframeId("");
    setCompoundSelection([]);
    setSelectedLightingLayerId("");
    setIsPreviewPlaying(false);
  }, [
    composition?.id,
    editingSpaceId,
    scene?.id,
    scene?.duration_ms,
    sceneView.loop_end_ms,
    sceneView.loop_start_ms,
    sceneView.zoom_px_per_second,
    sceneView.quantization_ms,
  ]);

  useEffect(() => {
    const firstLayer = transformLayers[editingSpaceId]?.[0];
    setSelectedTransformLayerId(firstLayer?.id ?? "");
    setSelectedTransformKeyframeId(firstLayer?.keyframes[0]?.id ?? "");
  }, [editingSpaceId]);

  useEffect(() => {
    if (!isPreviewPlaying) {
      if (playbackFrameRef.current !== null) {
        cancelAnimationFrame(playbackFrameRef.current);
        playbackFrameRef.current = null;
      }
      return;
    }
    let previous = performance.now();
    const tick = (now: number) => {
      const elapsed = now - previous;
      previous = now;
      setPlayhead((current) => {
        const local = Math.max(0, current - localOffset);
        const effectiveLoopStart = Math.max(0, Math.min(loopStart, duration));
        const effectiveLoopEnd = Math.max(
          effectiveLoopStart + 1,
          Math.min(loopEnd, duration),
        );
        const next = local + elapsed * previewPlaybackRate;
        if (next < effectiveLoopEnd) {
          return localOffset + Math.round(next);
        }
        const loopDuration = effectiveLoopEnd - effectiveLoopStart;
        return (
          localOffset +
          Math.round(
            effectiveLoopStart + ((next - effectiveLoopEnd) % loopDuration),
          )
        );
      });
      playbackFrameRef.current = requestAnimationFrame(tick);
    };
    playbackFrameRef.current = requestAnimationFrame(tick);
    return () => {
      if (playbackFrameRef.current !== null) {
        cancelAnimationFrame(playbackFrameRef.current);
        playbackFrameRef.current = null;
      }
    };
  }, [
    duration,
    isPreviewPlaying,
    localOffset,
    loopEnd,
    loopStart,
    previewPlaybackRate,
  ]);

  useEffect(() => {
    setScenePolicy(readSceneOperationalPolicy(scene));
  }, [scene?.id]);

  function timelineSnapshot(compositionsValue = compositions): TimelineSnapshot {
    return {
      compositions: compositionsValue,
      sceneCompositions,
      compositionInstances,
      syncMarkers,
      syncCues,
      transformLayers,
      cuePresets,
      safePoints,
      regions,
      customTracks,
      scenePolicy,
      lightingAuthorLayers,
    };
  }

  useEffect(() => {
    const snapshot = timelineSnapshot();
    const serialized = JSON.stringify(snapshot);
    const pendingLoad = pendingSpaceLoadRef.current;
    if (pendingLoad) {
      pendingLoad.passes += 1;
      const loaded =
        JSON.stringify([snapshot.safePoints, snapshot.regions, snapshot.customTracks]) ===
          pendingLoad.values &&
        (pendingLoad.scenePolicy === null ||
          JSON.stringify(snapshot.scenePolicy) === pendingLoad.scenePolicy);
      if (loaded) {
        // Opening the editor or switching scene/composition only loads saved values.
        pendingSpaceLoadRef.current = null;
        restoringHistoryRef.current = false;
        if (savedSnapshotRef.current === null || !timelineDirtyRef.current) {
          savedSnapshotRef.current = serialized;
        }
        if (historyIndexRef.current < 0) {
          historyRef.current = [snapshot];
          historyIndexRef.current = 0;
        } else {
          historyRef.current[historyIndexRef.current] = snapshot;
        }
        timelineDirtyRef.current = serialized !== savedSnapshotRef.current;
        setTimelineDirty(timelineDirtyRef.current);
        setHistoryVersion((version) => version + 1);
        return;
      }
      if (pendingLoad.passes < 3) return;
      pendingSpaceLoadRef.current = null;
    }
    if (savedSnapshotRef.current === null) savedSnapshotRef.current = serialized;
    timelineDirtyRef.current = serialized !== savedSnapshotRef.current;
    setTimelineDirty(timelineDirtyRef.current);
    if (restoringHistoryRef.current) {
      restoringHistoryRef.current = false;
      return;
    }
    const current = historyRef.current[historyIndexRef.current];
    if (current && JSON.stringify(current) === serialized) {
      return;
    }
    if (gestureRef.current.active && gestureRef.current.recorded) {
      historyRef.current = [
        ...historyRef.current.slice(0, historyIndexRef.current),
        snapshot,
      ];
      historyIndexRef.current = historyRef.current.length - 1;
      return;
    }
    if (gestureRef.current.active) gestureRef.current.recorded = true;
    historyRef.current = [
      ...historyRef.current.slice(0, historyIndexRef.current + 1),
      snapshot,
    ].slice(-100);
    historyIndexRef.current = historyRef.current.length - 1;
    setHistoryVersion((version) => version + 1);
  }, [
    compositions,
    sceneCompositions,
    compositionInstances,
    syncMarkers,
    syncCues,
    transformLayers,
    cuePresets,
    safePoints,
    regions,
    customTracks,
    scenePolicy,
    lightingAuthorLayers,
  ]);

  const tracks = useMemo<TimelineTrack[]>(() => {
    if (!scene) {
      return [];
    }
    const media = !composition && Array.isArray(scene.media)
      ? scene.media.map((output) => ({
          id: `media:${output.id}`,
          kind: "media_output" as const,
          name: output.id,
          resource_binding: String(metadataRecord(output).resource ?? ""),
          built_in: true,
        }))
      : !composition
        ? [
          {
            id: `media:${scene.id}`,
            kind: "media_output" as const,
            name: "Media",
            resource_binding: String(metadataRecord(scene.media).resource ?? ""),
            built_in: true,
          },
          ]
        : [];
    const lighting = !composition && Array.isArray(scene.lighting)
      ? scene.lighting.map((output) => {
          const record = metadataRecord(output);
          return {
            id: `lighting:${String(record.id ?? record.resource ?? "output")}`,
            kind: "lighting_output" as const,
            name: String(record.id ?? "Lighting"),
            resource_binding: String(record.resource ?? ""),
            built_in: true,
          };
        })
      : !composition && scene.lighting
        ? [
            {
              id: `lighting:${scene.id}`,
              kind: "lighting_output" as const,
              name: "Lighting",
              resource_binding: String(
                metadataRecord(scene.lighting).resource ?? "",
              ),
              built_in: true,
            },
          ]
        : [];
    return [...media, ...lighting, ...customTracks];
  }, [composition, customTracks, scene]);
  const placedCompositions = useMemo(
    () =>
      scene
        ? scenePlacedCompositions(scene.id, compositions, sceneCompositions, compositionInstances)
        : [],
    [compositionInstances, compositions, scene, sceneCompositions],
  );
  const sceneTabCompositions = useMemo(
    () =>
      sceneScopedCompositions(
        [
          ...placedCompositions.map((item) => item.compositionId),
          ...(scene ? (transformLayers[scene.id] ?? []) : [])
            .filter((layer) => layer.kind === "composition")
            .map((layer) => layer.source_id),
        ],
        compositions,
        activeCompositionId,
      ),
    [activeCompositionId, compositions, placedCompositions, scene, transformLayers],
  );
  const selectedTrack = tracks.find((track) => track.id === selectedTrackId);
  const selectedOutputRef = !composition && selectedTrack?.built_in &&
    (selectedTrack.kind === "media_output" || selectedTrack.kind === "lighting_output")
    ? {
        kind: selectedTrack.kind === "media_output" ? "media" as const : "lighting" as const,
        outputId: selectedTrack.kind === "media_output"
          ? Array.isArray(scene?.media) ? selectedTrack.id.slice("media:".length) : "media"
          : Array.isArray(scene?.lighting) ? selectedTrack.id.slice("lighting:".length) : "lighting",
      }
    : undefined;
  const previewScene = useMemo(
    () => scene ? applySceneOperationalPolicy(scene, scenePolicy) : undefined,
    [scene, scenePolicy],
  );
  const sceneOutputs = useMemo(() => sceneMediaOutputs(previewScene?.media), [previewScene?.media]);
  const selectedMediaOutput = !composition && selectedTrack?.built_in && selectedTrack.kind === "media_output"
    ? sceneOutputs.find((output) => `media:${output.id}` === selectedTrack.id) ??
      (sceneOutputs.length === 1 ? sceneOutputs[0] : undefined)
    : undefined;
  const parsedInstanceOverrides = parseObject(instanceOverrides);
  const markers = syncMarkers[editingSpaceId] ?? [];
  const cues = syncCues[editingSpaceId] ?? [];
  const renderedMarkers = useMemo(
    () =>
      markers.length <= 500
        ? markers
        : [...markers]
            .sort(
              (left, right) =>
                Math.abs(left.time_ms - (playhead - localOffset)) -
                Math.abs(right.time_ms - (playhead - localOffset)),
            )
            .slice(0, 500)
            .sort((left, right) => left.time_ms - right.time_ms),
    [localOffset, markers, playhead],
  );
  const selectableMarkers = useMemo(() => {
    const selected = markers.find((marker) => marker.id === newCueMarkerId);
    const nearest = renderedMarkers.slice(0, 500);
    return selected && !nearest.some((marker) => marker.id === selected.id)
      ? [selected, ...nearest]
      : nearest;
  }, [markers, newCueMarkerId, renderedMarkers]);
  const spaceTransformLayers = transformLayers[editingSpaceId] ?? [];
  const editorialAudioSources = useMemo(
    () =>
      collectEditorialAudioSources(
        previewScene,
        composition,
        tracks,
        spaceTransformLayers,
        assets,
        duration,
      ),
    [assets, composition, duration, previewScene, spaceTransformLayers, tracks],
  );
  const selectedTransformLayer = spaceTransformLayers.find(
    (layer) => layer.id === selectedTransformLayerId,
  );
  const selectedTransformKeyframe = selectedTransformLayer?.keyframes.find(
    (keyframe) => keyframe.id === selectedTransformKeyframeId,
  );
  const timelineDiagnostics = useMemo(
    () =>
      validateTimelineState({
        scene,
        policy: scenePolicy,
        duration,
        safePoints,
        regions,
        markers,
        cues,
        tracks,
        layers: spaceTransformLayers,
        composition,
      }),
    [
      cues,
      duration,
      markers,
      regions,
      safePoints,
      scene,
      scenePolicy,
      spaceTransformLayers,
      tracks,
      composition,
    ],
  );
  const evaluatedTimelineState = useMemo(
    () =>
      evaluateTimelineState(
        localPlayhead,
        cues,
        markers,
        spaceTransformLayers,
      ),
    [cues, localPlayhead, markers, spaceTransformLayers],
  );
  const resolvedSchedule = useMemo(
    () =>
      resolveSyncSchedule(
        draft.draft.scenes.map((candidate) => ({
          id: candidate.id,
          duration_ms: candidate.id === scene?.id
            ? scenePolicy.duration_ms
            : candidate.duration_ms ?? 1000,
        })),
        compositions,
        sceneCompositions,
        compositionInstances,
        syncMarkers,
        syncCues,
      ),
    [
      compositionInstances,
      compositions,
      draft.draft.scenes,
      sceneCompositions,
      scene?.id,
      scenePolicy.duration_ms,
      syncCues,
      syncMarkers,
    ],
  );

  function snapped(value: number): number {
    return Math.max(
      0,
      Math.min(
        scene ? scenePolicy.duration_ms : duration,
        Math.round(value / snapMs) * snapMs,
      ),
    );
  }

  function snappedLocal(value: number): number {
    return Math.max(0, Math.min(duration, Math.round(value / snapMs) * snapMs));
  }

  function movePlayhead(delta: number) {
    setPlayhead((current) => snapped(current + delta));
  }

  function updateLightingCue(
    cueId: string,
    update: (lighting: LightingCueProperties) => LightingCueProperties,
  ) {
    setSyncCues((current) => ({
      ...current,
      [editingSpaceId]: cues.map((cue) =>
        cue.id === cueId && cue.lighting
          ? { ...cue, lighting: update(cue.lighting) }
          : cue,
      ),
    }));
  }

  function restoreHistory(index: number) {
    const snapshot = historyRef.current[index];
    if (!snapshot) {
      return;
    }
    restoringHistoryRef.current = true;
    historyIndexRef.current = index;
    setCompositions(snapshot.compositions);
    setSceneCompositions(snapshot.sceneCompositions);
    setCompositionInstances(snapshot.compositionInstances);
    setSyncMarkers(snapshot.syncMarkers);
    setSyncCues(snapshot.syncCues);
    setTransformLayers(snapshot.transformLayers);
    setCuePresets(snapshot.cuePresets);
    setSafePoints(snapshot.safePoints);
    setRegions(snapshot.regions);
    setCustomTracks(snapshot.customTracks);
    setScenePolicy(snapshot.scenePolicy);
    setLightingAuthorLayers(snapshot.lightingAuthorLayers);
    setHistoryVersion((version) => version + 1);
  }

  function updateTransformLayer(
    layerId: string,
    update: (layer: TransformLayer) => TransformLayer,
  ) {
    setTransformLayers((current) => ({
      ...current,
      [editingSpaceId]: spaceTransformLayers.map((layer) =>
        layer.id === layerId ? update(layer) : layer,
      ),
    }));
  }

  function updateTransformKeyframe(
    layerId: string,
    keyframeId: string,
    update: (keyframe: TransformKeyframe) => TransformKeyframe,
  ) {
    updateTransformLayer(layerId, (layer) => ({
      ...layer,
      keyframes: layer.keyframes
        .map((keyframe) =>
          keyframe.id === keyframeId ? update(keyframe) : keyframe,
        )
        .sort((left, right) => left.time_ms - right.time_ms),
    }));
  }

  async function save() {
    if (!scene) {
      return;
    }
    const scenes = draft.draft.scenes.map((candidate) =>
      !composition && candidate.id === scene.id
        ? applySceneOperationalPolicy(
            {
              ...candidate,
              scheduling: {
                ...(candidate.scheduling ?? {}),
                safe_points_ms: [...new Set(safePoints)].sort(
                  (left, right) => left - right,
                ),
                protected_intervals: [...regions].sort(
                  (left, right) => left.start_ms - right.start_ms,
                ),
              },
            },
            scenePolicy,
          )
        : candidate,
    );
    const updatedCompositions = composition
      ? {
          ...compositions,
          [composition.id]: {
            ...composition,
            safe_points_ms: [...new Set(safePoints)].sort(
              (left, right) => left - right,
            ),
            protected_intervals: [...regions].sort(
              (left, right) => left.start_ms - right.start_ms,
            ),
          },
        }
      : compositions;
    setCompositions(updatedCompositions);
    const validEditingSpaces = new Set([
      ...draft.draft.scenes.map((candidate) => candidate.id),
      ...Object.keys(updatedCompositions),
    ]);
    await onSave({
      ...draft.draft,
      scenes,
      editor_metadata: {
        ...draft.draft.editor_metadata,
        timeline_views: {
          ...filterMetadataKeys(timelineViews, validEditingSpaces),
          [editingSpaceId]: {
            loop_start_ms: loopStart,
            loop_end_ms: loopEnd,
            quantization_ms: snapMs,
            zoom_px_per_second: zoom,
          },
        },
        timeline_tracks: {
          ...filterMetadataKeys(timelineTracks, validEditingSpaces),
          ...filterMetadataKeys(stashedTracksRef.current, validEditingSpaces),
          [editingSpaceId]: customTracks,
        },
        compositions: updatedCompositions,
        scene_compositions: sceneCompositions,
        composition_instances: compositionInstances,
        timeline_markers: filterMetadataKeys(syncMarkers, validEditingSpaces),
        timeline_cues: filterMetadataKeys(syncCues, validEditingSpaces),
        timeline_sync_schedule: resolvedSchedule,
        timeline_transform_layers: filterMetadataKeys(
          transformLayers,
          validEditingSpaces,
        ),
        timeline_cue_presets: cuePresets,
        lighting_author_layers: filterMetadataKeys(
          lightingAuthorLayers,
          validEditingSpaces,
        ),
      },
    });
    savedSnapshotRef.current = JSON.stringify(timelineSnapshot(updatedCompositions));
    timelineDirtyRef.current = false;
    setTimelineDirty(false);
  }

  function removeComposition(
    compositionId: string,
    replacementId?: string,
  ) {
    const next = Object.fromEntries(
      Object.entries(compositions)
        .filter(([id]) => id !== compositionId)
        .map(([id, item]) => [
          id,
          {
            ...item,
            children: [
              ...new Set(
                item.children
                  .map((child) =>
                    child === compositionId ? replacementId : child,
                  )
                  .filter((child): child is string => Boolean(child)),
              ),
            ],
          },
        ]),
    );
    if (compositionGraphHasCycle(next)) {
      return;
    }
    setCompositions(next);
    setSceneCompositions((current) =>
      Object.fromEntries(
        Object.entries(current).map(([sceneKey, ids]) => [
          sceneKey,
          [
            ...new Set(
              ids
                .map((id) => (id === compositionId ? replacementId : id))
                .filter((id): id is string => Boolean(id)),
            ),
          ],
        ]),
      ),
    );
    setCompositionInstances((current) =>
      Object.fromEntries(
        Object.entries(current).map(([sceneKey, instances]) => [
          sceneKey,
          instances.flatMap((instance) =>
            instance.composition_id === compositionId
              ? replacementId
                ? [{ ...instance, composition_id: replacementId }]
                : []
              : [instance],
          ),
        ]),
      ),
    );
    setTransformLayers((current) =>
      Object.fromEntries(
        Object.entries(current)
          .filter(([spaceId]) => spaceId !== compositionId)
          .map(([spaceId, layers]) => [
            spaceId,
            layers.flatMap((layer) =>
              layer.kind === "composition" && layer.source_id === compositionId
                ? replacementId
                  ? [{ ...layer, source_id: replacementId }]
                  : []
                : [layer],
            ),
          ]),
      ),
    );
    const withoutSpace = <T,>(current: Record<string, T>) => {
      const updated = { ...current };
      delete updated[compositionId];
      return updated;
    };
    setSyncMarkers(withoutSpace);
    setSyncCues(withoutSpace);
    setLightingAuthorLayers(withoutSpace);
    delete stashedTracksRef.current[compositionId];
    if (activeCompositionId === compositionId) {
      setActiveCompositionId(replacementId ?? "");
    }
    setReplacementByComposition((current) => {
      const updated = { ...current };
      delete updated[compositionId];
      return updated;
    });
  }

  function removeCompositionFromScene(compositionId: string) {
    if (!scene) return;
    setSceneCompositions((current) => ({
      ...current,
      [scene.id]: (current[scene.id] ?? []).filter((id) => id !== compositionId),
    }));
    setCompositionInstances((current) => ({
      ...current,
      [scene.id]: (current[scene.id] ?? []).filter(
        (instance) => instance.composition_id !== compositionId,
      ),
    }));
    setTransformLayers((current) => ({
      ...current,
      [scene.id]: (current[scene.id] ?? []).filter(
        (layer) => !(layer.kind === "composition" && layer.source_id === compositionId),
      ),
    }));
  }

  function startPlayheadDrag(event: ReactPointerEvent<HTMLElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    playheadDragRef.current = true;
    setIsPreviewPlaying(false);
    setPlayhead(localOffset + timeAtClientX(event.clientX));
  }

  function movePlayheadDrag(event: ReactPointerEvent<HTMLElement>) {
    if (playheadDragRef.current) setPlayhead(localOffset + timeAtClientX(event.clientX));
  }

  function endPlayheadDrag() {
    playheadDragRef.current = false;
  }

  function timeAtClientX(clientX: number): number {
    const bounds = canvasRef.current?.getBoundingClientRect();
    if (!bounds || bounds.width <= 0) return localPlayhead;
    const ratio = Math.min(1, Math.max(0, (clientX - bounds.left) / bounds.width));
    return snappedLocal(ratio * duration);
  }

  function setLoopEdge(edge: "start" | "end", value: number) {
    const snappedValue = snappedLocal(value);
    if (edge === "start") {
      setLoopStart(Math.min(Math.max(0, snappedValue), loopEnd - snapMs));
    } else {
      setLoopEnd(Math.max(Math.min(duration, snappedValue), loopStart + snapMs));
    }
  }

  function createComposition(spec: {
    name: string;
    durationMs: number;
    space: Composition["space_kind"];
    target: string;
    width: number;
    height: number;
    offsetMs: number;
    stageOutputs?: Pick<Composition, "stage_projector_resources" | "stage_lighting_surfaces">;
  }): string {
    const id = crypto.randomUUID();
    setCompositions((current) => ({
      ...current,
      [id]: {
        id,
        name: spec.name,
        duration_ms: spec.durationMs,
        space_kind: spec.space,
        target_id: spec.target,
        width: spec.width,
        height: spec.height,
        local_time_offset_ms: spec.offsetMs,
        children: [],
        safe_points_ms: [],
        protected_intervals: [],
        stage_lighting_surfaces: [],
        ...spec.stageOutputs,
        ...(() => {
          if (spec.space !== "stage") {
            return {
              render_settings: defaultRenderSettings(
                spec.space === "projector_native" ? [spec.width, spec.height] : [1920, 1080],
              ),
            };
          }
          const [renderWidth] = suggestedStageRenderResolution(
            mapperExport,
            spec.stageOutputs?.stage_projector_resources?.length === 0,
          );
          return {
            source_pixel_units: spec.width / renderWidth,
            render_settings: defaultRenderSettings([
              renderWidth,
              stageRenderHeight(spec.width, spec.height, renderWidth),
            ]),
          };
        })(),
        lighting_settings: defaultLightingSettings(),
      },
    }));
    stashedTracksRef.current[id] = [defaultCompositionTrack(spec.space, spec.target)];
    setActiveCompositionId(id);
    return id;
  }

  function defaultCompositionTrack(space: Composition["space_kind"], target: string): TimelineTrack {
    const binding =
      space === "lighting_surface"
        ? mapperExport?.lighting_surfaces.find((surface) => surface.semantic_id === target)?.resource ?? target
        : target;
    return { id: crypto.randomUUID(), kind: "group", name: "Media", resource_binding: binding };
  }

  function addMediaLayer(spec: {
    name: string;
    kind: TransformLayer["kind"];
    sourceId: string;
    trackId: string;
    startMs: number;
    durationMs: number;
  }) {
    const asset = assets.find((item) => item.id === spec.sourceId);
    const keyframe = defaultTransformKeyframe(0);
    if (composition?.space_kind === "lighting_surface" && asset?.metadata) {
      const sourceWidth = numberValue(asset.metadata.width, 0);
      const sourceHeight = numberValue(asset.metadata.height, 0);
      if (sourceWidth > 0 && sourceHeight > 0) {
        const scale = Math.min(composition.width / sourceWidth, composition.height / sourceHeight);
        keyframe.position = [composition.width / 2, composition.height / 2];
        keyframe.scale = [scale, scale];
      }
    }
    if (composition && composition.space_kind !== "lighting_surface" && spec.kind !== "audio") {
      const sourceComposition = compositions[spec.sourceId];
      const [width, height] = sourceComposition
        ? compositionRasterSize(sourceComposition)
        : [numberValue(asset?.metadata?.width, 0), numberValue(asset?.metadata?.height, 0)];
      const upp = layerUnitsPerSourcePixel(composition);
      keyframe.position = [composition.width / 2, composition.height / 2];
      if (width > 0 && height > 0) {
        const scale = roundTo(
          Math.min(composition.width / (width * upp), composition.height / (height * upp)),
          4,
        );
        keyframe.scale = [scale, scale];
      }
    }
    const layer: TransformLayer = {
      layer_schema_version: 1,
      id: crypto.randomUUID(),
      name: spec.name,
      kind: spec.kind,
      source_id: spec.sourceId,
      track_id: spec.trackId,
      start_ms: spec.startMs,
      duration_ms: spec.durationMs,
      in_point_ms: 0,
      out_point_ms: spec.durationMs,
      playback: composition?.space_kind === "lighting_surface" && spec.kind === "video"
        ? "loop" : "hold",
      z_index: spaceTransformLayers.length,
      fade_in_ms: 0,
      fade_out_ms: 0,
      audio:
        spec.kind === "audio"
          ? {
              gain_db: 0,
              mute: false,
              solo: false,
              mix_order: spaceTransformLayers.filter(
                (candidate) => candidate.kind === "audio",
              ).length,
            }
          : null,
      alpha_interpretation: spec.kind === "audio" ? null : "straight",
      source_color_space: spec.kind === "audio" ? null : "srgb",
      scaling_behavior: spec.kind === "audio" ? null : "fit",
      keyframes: spec.kind === "audio" ? [] : [keyframe],
    };
    setTransformLayers((current) => ({
      ...current,
      [editingSpaceId]: [...spaceTransformLayers, layer],
    }));
    setSelectedTransformLayerId(layer.id);
    setInspectorTarget("layer");
    setSelectedTransformKeyframeId(layer.keyframes[0]?.id ?? "");
  }

  function defaultDropTrackId(): string | undefined {
    if (tracks.some((track) => track.id === selectedTrackId)) return selectedTrackId;
    return (tracks.find((track) => track.kind === "media_output") ?? tracks[0])?.id;
  }

  function placeSource(sourceId: string, trackId: string | undefined, startMs: number) {
    if (!canEdit) return;
    setPendingPlacement(null);
    let track = tracks.find((candidate) => candidate.id === trackId);
    if (!track && composition && tracks.length === 0) {
      track = defaultCompositionTrack(composition.space_kind, composition.target_id);
      const created = track;
      setCustomTracks((current) => [...current, created]);
    }
    if (!track) {
      setBinMessage("Add a track on the Tracks tab before placing media.");
      return;
    }
    const asset = assets.find((candidate) => candidate.id === sourceId);
    const child = compositions[sourceId];
    if (!asset && !child) return;
    if (
      child &&
      activeCompositionId &&
      (child.id === activeCompositionId ||
        wouldCreateCycle(compositions, activeCompositionId, child.id))
    ) {
      setBinMessage(`${child.name} cannot be nested inside itself.`);
      return;
    }
    const type = asset?.media_type ?? "";
    const kind: TransformLayer["kind"] = child
      ? "composition"
      : type.startsWith("audio/")
        ? "audio"
        : type.startsWith("video/") || type === "image/gif"
          ? "video"
          : "image";
    const start = clampNumber(snappedLocal(startMs), 0, Math.max(0, duration - 1));
    const knownLength = child ? child.duration_ms : numberValue(asset?.metadata?.duration_ms, 0);
    const natural = knownLength || 5000;
    const name = child ? child.name : asset!.name;
    if (!composition && knownLength > 0 && start + natural > duration) {
      setPendingPlacement({ sourceId, trackId: track.id, startMs: start, naturalMs: Math.round(natural), name });
      setBinMessage(
        `${name} (${formatTimecode(natural)}) runs past the scene end at ${formatTimecode(duration)}.`,
      );
      return;
    }
    const length = Math.round(Math.min(natural, duration - start));
    if (length <= 0) {
      setBinMessage("There is no room left on the timeline at that point.");
      return;
    }
    addMediaLayer({ name, kind, sourceId, trackId: track.id, startMs: start, durationMs: length });
    setBinMessage(
      length < knownLength
        ? `Added ${name} to ${track.name} at ${formatTimecode(start)}, trimmed to fit the composition.`
        : `Added ${name} to ${track.name} at ${formatTimecode(start)}.`,
    );
  }

  function placementKind(sourceId: string): TransformLayer["kind"] {
    if (compositions[sourceId]) return "composition";
    const type = assets.find((candidate) => candidate.id === sourceId)?.media_type ?? "";
    return type.startsWith("audio/")
      ? "audio"
      : type.startsWith("video/") || type === "image/gif"
        ? "video"
        : "image";
  }

  function setCompositionDuration(compositionId: string, next: number) {
    if (!Number.isInteger(next) || next <= 0) return;
    setCompositions((current) =>
      current[compositionId]
        ? { ...current, [compositionId]: { ...current[compositionId], duration_ms: next } }
        : current,
    );
    if (compositionId === activeCompositionId) {
      setLoopEnd((end) => (end >= duration ? next : Math.min(end, next)));
      setLoopStart((loopStartMs) => Math.min(loopStartMs, Math.max(0, next - 1)));
    }
  }

  function setSceneDuration(next: number) {
    setScenePolicy((current) => ({ ...current, duration_ms: next }));
    if (!Number.isInteger(next) || next <= 0) return;
    setLoopEnd((end) => (end >= duration ? next : Math.min(end, next)));
    setLoopStart((loopStartMs) => Math.min(loopStartMs, Math.max(0, next - 1)));
  }

  function resolvePendingPlacement(mode: "extend" | "trim") {
    const pending = pendingPlacement;
    if (!pending || !canEdit) return;
    setPendingPlacement(null);
    const track = tracks.find((candidate) => candidate.id === pending.trackId);
    const length = mode === "extend" ? pending.naturalMs : duration - pending.startMs;
    if (!track || length <= 0) {
      setBinMessage("There is no room left on the timeline at that point.");
      return;
    }
    if (mode === "extend") setSceneDuration(pending.startMs + pending.naturalMs);
    addMediaLayer({
      name: pending.name,
      kind: placementKind(pending.sourceId),
      sourceId: pending.sourceId,
      trackId: track.id,
      startMs: pending.startMs,
      durationMs: length,
    });
    setBinMessage(
      mode === "extend"
        ? `Added ${pending.name} at ${formatTimecode(pending.startMs)} and extended the scene to ${formatTimecode(pending.startMs + pending.naturalMs)}.`
        : `Added ${pending.name} at ${formatTimecode(pending.startMs)}, trimmed to end with the scene.`,
    );
  }

  function msPerPixel(): number {
    const width = canvasRef.current?.getBoundingClientRect().width ?? 0;
    return width > 0 ? duration / width : 0;
  }

  function beginGesture() {
    gestureRef.current = { active: true, recorded: false };
  }

  function endGesture() {
    gestureRef.current = { active: false, recorded: false };
  }

  function removeTransformLayer(layerId: string) {
    setTransformLayers((current) => ({
      ...current,
      [editingSpaceId]: (current[editingSpaceId] ?? []).filter((layer) => layer.id !== layerId),
    }));
    setCompoundSelection((current) => current.filter((id) => id !== layerId));
    if (selectedTransformLayerId === layerId) {
      setSelectedTransformLayerId("");
      setSelectedTransformKeyframeId("");
    }
  }

  function dragLayerTo(clientX: number) {
    if (!layerDrag) return;
    const layer = spaceTransformLayers.find((candidate) => candidate.id === layerDrag.id);
    if (!layer) return;
    const moved = layerDrag.moved || Math.abs(clientX - layerDrag.startX) > 3;
    if (!moved) return;
    const delta = (clientX - layerDrag.startX) * msPerPixel();
    const minimumLength = Math.max(1, snapMs);
    let start = layer.start_ms;
    let length = layer.duration_ms;
    if (layerDrag.mode === "move") {
      start = clampNumber(snappedLocal(layer.start_ms + delta), 0, duration - layer.duration_ms);
    } else if (layerDrag.mode === "start") {
      const end = layer.start_ms + layer.duration_ms;
      start = clampNumber(
        snappedLocal(layer.start_ms + delta),
        Math.max(0, layer.start_ms - layer.in_point_ms),
        end - minimumLength,
      );
      length = end - start;
    } else {
      length = clampNumber(
        snappedLocal(layer.duration_ms + delta),
        minimumLength,
        duration - layer.start_ms,
      );
    }
    setLayerDrag({ ...layerDrag, moved, start, duration: length });
  }

  function commitLayerDrag() {
    const drag = layerDrag;
    setLayerDrag(null);
    if (!drag?.moved) return;
    suppressLayerClickRef.current = true;
    window.setTimeout(() => {
      suppressLayerClickRef.current = false;
    }, 0);
    updateTransformLayer(drag.id, (layer) => {
      if (drag.mode === "move") return { ...layer, start_ms: drag.start };
      if (drag.mode === "end") {
        return {
          ...layer,
          duration_ms: drag.duration,
          out_point_ms: layer.in_point_ms + drag.duration,
          keyframes: trimKeyframes(layer.keyframes, 0, drag.duration),
        };
      }
      const shift = drag.start - layer.start_ms;
      const inPoint = layer.in_point_ms + shift;
      return {
        ...layer,
        start_ms: drag.start,
        duration_ms: drag.duration,
        in_point_ms: inPoint,
        out_point_ms: inPoint + drag.duration,
        keyframes: trimKeyframes(
          layer.keyframes.map((keyframe) => ({ ...keyframe, time_ms: keyframe.time_ms - shift })),
          0,
          drag.duration,
        ),
      };
    });
  }

  function dragKeyframeTo(clientX: number) {
    if (!keyframeDrag) return;
    const layer = spaceTransformLayers.find((candidate) => candidate.id === keyframeDrag.layerId);
    const keyframe = layer?.keyframes.find((candidate) => candidate.id === keyframeDrag.keyframeId);
    if (!layer || !keyframe) return;
    const moved = keyframeDrag.moved || Math.abs(clientX - keyframeDrag.startX) > 3;
    if (!moved) return;
    const time = clampNumber(
      snappedLocal(keyframe.time_ms + (clientX - keyframeDrag.startX) * msPerPixel()),
      0,
      layer.duration_ms,
    );
    setKeyframeDrag({ ...keyframeDrag, moved, time });
  }

  function commitKeyframeDrag() {
    const drag = keyframeDrag;
    setKeyframeDrag(null);
    if (!drag?.moved) return;
    suppressLayerClickRef.current = true;
    window.setTimeout(() => {
      suppressLayerClickRef.current = false;
    }, 0);
    const layer = spaceTransformLayers.find((candidate) => candidate.id === drag.layerId);
    if (
      layer?.keyframes.some(
        (keyframe) => keyframe.id !== drag.keyframeId && keyframe.time_ms === drag.time,
      )
    ) {
      return;
    }
    updateTransformKeyframe(drag.layerId, drag.keyframeId, (keyframe) => ({
      ...keyframe,
      time_ms: drag.time,
    }));
  }

  function openPresetDialog() {
    const firstProjector = (mapperExport?.projectors ?? [])
      .map((value) => metadataRecord(value))
      .find((projector) => typeof projector.resource === "string");
    setPreset("stage");
    setPresetTarget(String(firstProjector?.resource ?? ""));
    setPresetName("");
    setPresetDuration(scene?.duration_ms ?? 5000);
    setPresetAddToScene(true);
    setPresetDialogOpen(true);
  }

  function presetSpec() {
    if (preset === "stage" || preset === "stage_lights") {
      const width = mapperExport?.stage ? roundStage(mapperExport.stage.width) : 10;
      const height = mapperExport?.stage ? roundStage(mapperExport.stage.height) : 6;
      return {
        target: "shared-stage",
        width,
        height,
        label: preset === "stage" ? "Whole display overlay" : "Whole house lights",
      };
    }
    if (preset === "projector_native") {
      const projector = (mapperExport?.projectors ?? [])
        .map((value) => metadataRecord(value))
        .find((candidate) => candidate.resource === presetTarget);
      return {
        target: presetTarget,
        width: numberValue(projector?.native_width, 1920),
        height: numberValue(projector?.native_height, 1080),
        label: presetTarget || "Projector",
      };
    }
    const surface = mapperExport?.lighting_surfaces.find(
      (candidate) => candidate.semantic_id === presetTarget,
    );
    return {
      target: presetTarget,
      width: surface?.local_width ?? 1,
      height: surface?.local_height ?? 1,
      label: surface?.name || surface?.resource || presetTarget || "LED surface",
    };
  }

  function createFromPreset() {
    const spec = presetSpec();
    const name = presetName.trim() || spec.label;
    const id = createComposition({
      name,
      durationMs: presetDuration,
      space: preset === "stage_lights" ? "stage" : preset,
      target: spec.target,
      width: spec.width,
      height: spec.height,
      offsetMs: 0,
      stageOutputs:
        preset === "stage_lights"
          ? {
              stage_projector_resources: [],
              stage_lighting_surfaces: (mapperExport?.lighting_surfaces ?? []).map(
                (surface) => surface.semantic_id,
              ),
            }
          : undefined,
    });
    if (presetAddToScene && scene) {
      setSceneCompositions((current) => ({
        ...current,
        [scene.id]: [...new Set([...(current[scene.id] ?? []), id])],
      }));
      setCompositionInstances((current) => ({
        ...current,
        [scene.id]: [
          ...(current[scene.id] ?? []),
          {
            id: crypto.randomUUID(),
            composition_id: id,
            start_offset_ms: 0,
            time_scale: 1,
            parameter_overrides: {},
            placement: { x: 0, y: 0 },
          },
        ],
      }));
    }
    setToolTab("layers");
    setPresetDialogOpen(false);
  }

  function addSafePointAtPlayhead() {
    setSafePoints((current) =>
      [...new Set([...current, snappedLocal(localPlayhead)])].sort(
        (left, right) => left - right,
      ),
    );
  }

  const shortcutHandlers = useRef({
    save: () => {},
    undo: () => {},
    redo: () => {},
    togglePlay: () => {},
    addMarker: () => {},
    deleteSelection: () => {},
  });
  shortcutHandlers.current = {
    save: () => {
      if (canEdit && timelineDirtyRef.current && timelineDiagnostics.length === 0) void save();
    },
    undo: () => {
      if (canEdit && historyIndexRef.current > 0) restoreHistory(historyIndexRef.current - 1);
    },
    redo: () => {
      if (canEdit && historyIndexRef.current < historyRef.current.length - 1) {
        restoreHistory(historyIndexRef.current + 1);
      }
    },
    togglePlay: () => setIsPreviewPlaying((current) => !current),
    addMarker: () => {
      if (canEdit) addSafePointAtPlayhead();
    },
    deleteSelection: () => {
      if (canEdit && inspectorTarget === "layer" && selectedTransformLayer) {
        removeTransformLayer(selectedTransformLayer.id);
      }
    },
  };
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing =
        target !== null &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
      const modifier = event.ctrlKey || event.metaKey;
      const key = event.key.toLowerCase();
      if (modifier && key === "s") {
        event.preventDefault();
        shortcutHandlers.current.save();
      } else if (typing || event.altKey) {
        return;
      } else if (modifier && key === "z") {
        event.preventDefault();
        if (event.shiftKey) shortcutHandlers.current.redo();
        else shortcutHandlers.current.undo();
      } else if (!modifier && event.key === " " && target?.tagName !== "BUTTON") {
        event.preventDefault();
        shortcutHandlers.current.togglePlay();
      } else if (!modifier && key === "m") {
        shortcutHandlers.current.addMarker();
      } else if (!modifier && (event.key === "Delete" || event.key === "Backspace")) {
        event.preventDefault();
        shortcutHandlers.current.deleteSelection();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  if (!scene) {
    return null;
  }

  const content = (
    <>
      <header className="timeline-header">
        <div className="timeline-header__context">
          <label>
            Scene
            <select
              value={scene.id}
              onChange={(event) => {
                setSceneId(event.target.value);
                onSceneChange?.(event.target.value);
                setActiveCompositionId("");
              }}
            >
              {draft.draft.scenes.map((candidate) => (
                <option key={candidate.id} value={candidate.id}>
                  {candidate.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="timeline-header__actions">
          <button
            type="button"
            className={`timeline-status-chip${
              timelineDiagnostics.length ? " timeline-status-chip--invalid" : ""
            }`}
            aria-label={
              timelineDiagnostics.length
                ? `${timelineDiagnostics.length} timeline issue${timelineDiagnostics.length === 1 ? "" : "s"}; show diagnostics`
                : "Timeline valid; show diagnostics"
            }
            onClick={() => setDiagnosticsOpen(true)}
          >
            {timelineDiagnostics.length
              ? `${timelineDiagnostics.length} issue${timelineDiagnostics.length === 1 ? "" : "s"}`
              : "Valid"}
          </button>
          {!composition && (
            <button type="button" onClick={() => setSceneSettingsOpen(true)}>
              Scene settings
            </button>
          )}
          {composition && (
            <button type="button" onClick={() => setCompositionSettingsOpen(true)}>
              Composition settings
            </button>
          )}
          {canEdit && (
            <>
              <button
                aria-label="Undo timeline edit"
                title="Undo (Ctrl+Z)"
                disabled={historyVersion < 0 || historyIndexRef.current <= 0}
                onClick={() => restoreHistory(historyIndexRef.current - 1)}
              >
                Undo
              </button>
              <button
                aria-label="Redo timeline edit"
                title="Redo (Ctrl+Shift+Z)"
                disabled={
                  historyIndexRef.current < 0 ||
                  historyIndexRef.current >= historyRef.current.length - 1
                }
                onClick={() => restoreHistory(historyIndexRef.current + 1)}
              >
                Redo
              </button>
              <button
                className="timeline-primary-action"
                title={timelineDirty ? "Save (Ctrl+S)" : "No timeline changes to save"}
                disabled={!timelineDirty || timelineDiagnostics.length > 0}
                onClick={() => void save()}
              >
                Save timeline
              </button>
            </>
          )}
        </div>
      </header>
      <div className="timeline-transport" role="toolbar" aria-label="Playback and view">
        <div className="timeline-transport__group">
          <button
            aria-label="Stop editorial preview"
            title="Stop and return to loop start"
            onClick={() => {
              setIsPreviewPlaying(false);
              setPlayhead(localOffset + loopStart);
            }}
          >
            ■
          </button>
          <button
            aria-label="Previous preview frame"
            title="Previous frame"
            onClick={() => {
              setIsPreviewPlaying(false);
              setPlayhead(
                localOffset + Math.max(0, localPlayhead - frameDurationMs),
              );
            }}
          >
            ◀|
          </button>
          <button
            className="timeline-transport__play"
            aria-label={isPreviewPlaying ? "Pause editorial preview" : "Play editorial preview"}
            title="Play / pause (Space)"
            onClick={() => setIsPreviewPlaying((current) => !current)}
          >
            {isPreviewPlaying ? "Pause" : "Play"}
          </button>
          <button
            aria-label="Next preview frame"
            title="Next frame"
            onClick={() => {
              setIsPreviewPlaying(false);
              setPlayhead(
                localOffset +
                  Math.min(duration, localPlayhead + frameDurationMs),
              );
            }}
          >
            |▶
          </button>
        </div>
        <output className="timeline-timecode" aria-label="Playhead time">
          <strong>{formatTimecode(localPlayhead)}</strong>
          <small>
            / {formatTimecode(duration)}
            {composition ? ` · scene ${formatTimecode(playhead)}` : ""}
          </small>
        </output>
        <label>
          Go to (ms)
          <input
            aria-label="Scrub position"
            max={localOffset + duration}
            min={localOffset}
            step={snapMs}
            type="number"
            value={playhead}
            onChange={(event) => setPlayhead(snapped(Number(event.target.value)))}
          />
        </label>
        <label>
          Speed
          <select
            aria-label="Preview speed"
            value={previewPlaybackRate}
            onChange={(event) =>
              setPreviewPlaybackRate(Number(event.target.value))
            }
          >
            {[0.25, 0.5, 1, 2, 4].map((rate) => (
              <option key={rate} value={rate}>
                {rate}×
              </option>
            ))}
          </select>
        </label>
        <label>
          Zoom
          <input
            min="20"
            max="240"
            step="10"
            type="range"
            value={zoom}
            onChange={(event) => setZoom(Number(event.target.value))}
          />
        </label>
        <label>
          Snap
          <select value={snapMs} onChange={(event) => setSnapMs(Number(event.target.value))}>
            {[1, 10, 100, 250, 500, 1000].map((value) => (
              <option key={value} value={value}>
                {value} ms
              </option>
            ))}
          </select>
        </label>
        {canEdit && (
          <button
            title="Add a marker at the playhead (M)"
            onClick={addSafePointAtPlayhead}
          >
            Add marker
          </button>
        )}
      </div>
      <div className="timeline-composition-tabs" role="tablist" aria-label="Open compositions">
        <button
          type="button"
          role="tab"
          aria-selected={!activeCompositionId}
          onClick={() => setActiveCompositionId("")}
        >
          Whole scene
        </button>
        {sceneTabCompositions.map((candidate) => (
          <button
            key={candidate.id}
            type="button"
            role="tab"
            aria-label={candidate.name}
            aria-selected={activeCompositionId === candidate.id}
            data-composition-id={candidate.id}
            title={`${candidate.name} · ${humanizeSpace(candidate.space_kind)}`}
            onClick={() => setActiveCompositionId(candidate.id)}
          >
            {candidate.name}
            <small>{humanizeSpace(candidate.space_kind)}</small>
          </button>
        ))}
        {canEdit && (
          <button
            type="button"
            className="timeline-composition-tabs__new"
            onClick={openPresetDialog}
          >
            + New composition
          </button>
        )}
        {canEdit && composition && (
          <button
            type="button"
            className="timeline-composition-tabs__delete danger-button"
            onClick={() => setPendingDeleteId(composition.id)}
          >
            Delete composition…
          </button>
        )}
      </div>
      <div className="timeline-stage-row">
        <section className="timeline-bin" aria-label="Media bin">
          <h3 className="timeline-inspector__title">Media</h3>
          <input
            aria-label="Search media"
            placeholder="Search"
            type="search"
            value={binSearch}
            onChange={(event) => setBinSearch(event.target.value)}
          />
          <ul className="timeline-bin__list">
            {[
              ...assets
                .filter((asset) => asset.status === "ready")
                .map((asset) => ({
                  id: asset.id,
                  name: asset.name,
                  kind: assetKindLabel(asset),
                  thumbnail: hasAssetArtifact(asset, "thumbnail")
                    ? assetArtifactUrl(asset.id, "thumbnail", asset.current_revision)
                    : null,
                })),
              ...Object.values(compositions)
                .filter((candidate) => candidate.id !== activeCompositionId)
                .map((candidate) => ({
                  id: candidate.id,
                  name: candidate.name,
                  kind: "composition",
                  thumbnail: null,
                })),
            ]
              .filter((item) =>
                item.name.toLocaleLowerCase().includes(binSearch.trim().toLocaleLowerCase()),
              )
              .map((item) => (
                <li
                  key={item.id}
                  className="timeline-bin__item"
                  draggable={canEdit}
                  title={canEdit ? "Drag onto a track or the viewer" : undefined}
                  onDragStart={(event) => {
                    event.dataTransfer.setData(TIMELINE_SOURCE_MIME, item.id);
                    event.dataTransfer.setData("text/plain", item.name);
                    event.dataTransfer.effectAllowed = "copy";
                  }}
                >
                  {item.thumbnail ? (
                    <img alt="" src={item.thumbnail} />
                  ) : (
                    <span className="timeline-bin__icon" aria-hidden="true">
                      {item.kind.slice(0, 3)}
                    </span>
                  )}
                  <span className="timeline-bin__text" title={item.name}>
                    <strong>{item.name}</strong>
                    <small>{item.kind}</small>
                  </span>
                  {canEdit && (
                    <button
                      type="button"
                      aria-label={`Add ${item.name} at playhead`}
                      title="Add at the playhead on the selected track"
                      onClick={() => placeSource(item.id, defaultDropTrackId(), localPlayhead)}
                    >
                      +
                    </button>
                  )}
                </li>
              ))}
          </ul>
          {!assets.some((asset) => asset.status === "ready") && (
            <small>No ready media yet. Upload assets on the show page.</small>
          )}
          {binMessage && <small role="status">{binMessage}</small>}
          {pendingPlacement && canEdit && (
            <div className="timeline-bin__overflow" role="group" aria-label="Clip runs past scene end">
              <button type="button" onClick={() => resolvePendingPlacement("extend")}>
                Extend scene to {formatTimecode(pendingPlacement.startMs + pendingPlacement.naturalMs)}
              </button>
              <button
                type="button"
                disabled={pendingPlacement.startMs >= duration}
                onClick={() => resolvePendingPlacement("trim")}
              >
                Trim clip to fit
              </button>
              <button type="button" onClick={() => { setPendingPlacement(null); setBinMessage(null); }}>
                Cancel
              </button>
            </div>
          )}
        </section>
        <section
          className={`timeline-viewer${dropTargetId === "viewer" ? " timeline-drop-target" : ""}`}
          aria-label="Viewer"
          onDragOver={(event) => {
            if (!canEdit || !event.dataTransfer.types.includes(TIMELINE_SOURCE_MIME)) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = "copy";
            setDropTargetId("viewer");
          }}
          onDragLeave={() => setDropTargetId(null)}
          onDrop={(event) => {
            const sourceId = event.dataTransfer.getData(TIMELINE_SOURCE_MIME);
            setDropTargetId(null);
            if (!sourceId) return;
            event.preventDefault();
            placeSource(sourceId, defaultDropTrackId(), localPlayhead);
          }}
        >
        <nav className="composition-breadcrumbs" aria-label="Composition breadcrumbs">
          <button onClick={() => setActiveCompositionId("")}>Scene · {scene.name}</button>
          {breadcrumbs.map((compositionId) => (
            <span key={compositionId}>
              <span aria-hidden="true">›</span>
              <button onClick={() => setActiveCompositionId(compositionId)}>
                {compositions[compositionId]?.name ?? compositionId}
              </button>
            </span>
          ))}
        </nav>
        {composition?.space_kind === "stage" &&
          viewerMode === "house" &&
          stageCoverage &&
          mapperExport &&
          installationId && (
          <StageCompositionPreview
            composition={composition}
            compositions={compositions}
            coverage={stageCoverage}
            installationId={installationId}
            mapperExport={mapperExport}
            projectId={projectId}
            selectedKeyframe={selectedTransformKeyframe}
            selectedLayer={selectedTransformLayer}
            canEdit={canEdit}
            snapMode={spatialSnapMode}
            onSnapModeChange={setSpatialSnapMode}
            timestampMs={Math.min(localPlayhead, composition.duration_ms - 1)}
            onMoveKeyframe={(position) => {
              if (selectedTransformLayer && selectedTransformKeyframe) {
                updateTransformKeyframe(
                  selectedTransformLayer.id,
                  selectedTransformKeyframe.id,
                  (keyframe) => ({ ...keyframe, position }),
                );
              }
            }}
            onCornerPinChange={(points) => {
              if (selectedTransformLayer && canEdit) {
                updateTransformLayer(selectedTransformLayer.id, (layer) => ({
                  ...layer,
                  corner_pin: points,
                }));
              }
            }}
          />
        )}
        {composition?.space_kind === "lighting_surface" && viewerMode === "house" && mapperExport && (
          <LightingSurfaceCompositionPreview
            composition={composition}
            installationId={installationId}
            mapperExport={mapperExport}
            projectId={projectId}
            timestampMs={Math.min(localPlayhead, composition.duration_ms - 1)}
            onMoveMedia={canEdit && selectedTransformLayer && selectedTransformKeyframe
              ? (position) => updateTransformKeyframe(
                  selectedTransformLayer.id, selectedTransformKeyframe.id,
                  (keyframe) => ({ ...keyframe, position }),
                )
              : undefined}
          />
        )}
          {composition?.space_kind === "stage" && (
            <StageOutputSelector
              composition={composition}
              mapperExport={mapperExport}
              canEdit={canEdit}
              onChange={(update) =>
                setCompositions((current) => ({
                  ...current,
                  [composition.id]: update(current[composition.id]),
                }))
              }
            />
          )}
          {!composition && installationId && (
            <div className="timeline-viewer__modes" role="group" aria-label="Scene viewer mode">
              <button
                type="button"
                aria-pressed={sceneViewerMode === "house"}
                onClick={() => setSceneViewerMode("house")}
              >
                House view
              </button>
              <button
                type="button"
                aria-pressed={sceneViewerMode === "monitor"}
                onClick={() => setSceneViewerMode("monitor")}
              >
                Program monitor
              </button>
            </div>
          )}
          {!composition && installationId && sceneViewerMode === "house" && (
            <SceneHouseView
              assets={assets}
              installationId={installationId}
              isPlaying={isPreviewPlaying}
              outputs={sceneOutputs}
              playheadMs={localPlayhead}
            />
          )}
          {!composition && (!installationId || sceneViewerMode === "monitor") && (
            <SceneProgramMonitor
              assets={assets}
              isPlaying={isPreviewPlaying}
              layers={spaceTransformLayers}
              outputs={sceneOutputs}
              playheadMs={localPlayhead}
            />
          )}
          {composition && composition.space_kind !== "projector_native" && (
            <div className="timeline-viewer__modes" role="group" aria-label="Viewer mode">
              <button
                type="button"
                aria-pressed={viewerMode === "house"}
                onClick={() => setViewerMode("house")}
              >
                House view
              </button>
              <button
                type="button"
                aria-pressed={viewerMode === "layout"}
                onClick={() => setViewerMode("layout")}
              >
                Layout
              </button>
            </div>
          )}
          {composition &&
            (composition.space_kind === "projector_native" || viewerMode === "layout") && (
              <CompositionLayoutCanvas
                assets={assets}
                canEdit={canEdit}
                composition={composition}
                compositions={compositions}
                installationId={installationId}
                mapperExport={mapperExport}
                layers={spaceTransformLayers}
                playheadMs={localPlayhead}
                isPlaying={isPreviewPlaying}
                selectedKeyframeId={selectedTransformKeyframeId}
                selectedLayerId={inspectorTarget === "layer" ? selectedTransformLayerId : ""}
                onGestureStart={beginGesture}
                onGestureEnd={endGesture}
                onSelectLayer={(layerId) => {
                  if (layerId !== selectedTransformLayerId) {
                    const layer = spaceTransformLayers.find((candidate) => candidate.id === layerId);
                    setSelectedTransformLayerId(layerId);
                    setSelectedTransformKeyframeId(
                      layer ? keyframeAtTime(layer, localPlayhead - layer.start_ms)?.id ?? "" : "",
                    );
                  }
                  setInspectorTarget("layer");
                }}
                onChangeKeyframe={(layerId, keyframeId, patch) =>
                  updateTransformKeyframe(layerId, keyframeId, (keyframe) => ({ ...keyframe, ...patch }))
                }
              />
            )}
          {composition?.space_kind === "projector_native" && (
            <p className="timeline-viewer__hint">
              Rendered projector frames are on the Render &amp; bake tab.
            </p>
          )}
        </section>
        <aside className="timeline-inspector" aria-label="Inspector">
          <h3 className="timeline-inspector__title">Inspector</h3>
          {inspectorTarget === "layer" && selectedTransformLayer ? (
            <>
            <div className="timeline-inspector__actions">
              <strong>{selectedTransformLayer.name}</strong>
              {canEdit && (
                <button
                  type="button"
                  className="danger-button"
                  aria-label={`Delete media layer ${selectedTransformLayer.name}`}
                  title="Delete layer (Delete key)"
                  onClick={() => removeTransformLayer(selectedTransformLayer.id)}
                >
                  Delete layer
                </button>
              )}
            </div>
            {selectedTransformLayer && (
              <div className="timeline-transform-properties">
                {timelineDiagnostics
                  .filter(
                    (diagnostic) =>
                      diagnostic.object_id === selectedTransformLayer.id ||
                      diagnostic.object_id === selectedTransformKeyframeId,
                  )
                  .map((diagnostic) => (
                    <small
                      className="timeline-property-error timeline-transform-error"
                      key={`${diagnostic.code}:${diagnostic.object_id}`}
                    >
                      {diagnostic.message}
                    </small>
                  ))}
                <label>
                  Layer name
                  <input
                    aria-label="Media layer name"
                    disabled={!canEdit}
                    value={selectedTransformLayer.name}
                    onChange={(event) =>
                      updateTransformLayer(selectedTransformLayer.id, (layer) => ({
                        ...layer,
                        name: event.target.value,
                      }))
                    }
                  />
                </label>
                <label>
                  Layer start
                  <TimeNumberInput
                    aria-label="Media layer start"
                    disabled={!canEdit}
                    max={Math.max(0, duration - selectedTransformLayer.duration_ms)}
                    min={0}
                    step={snapMs}
                    value={selectedTransformLayer.start_ms}
                    onEditStart={beginGesture}
                    onEditEnd={endGesture}
                    onCommit={(start) =>
                      updateTransformLayer(selectedTransformLayer.id, (layer) => ({
                        ...layer,
                        start_ms: start,
                      }))
                    }
                  />
                </label>
                {selectedTransformLayer.kind !== "audio" && (
                  <label>
                    Media playback
                    <select
                      aria-label="Media playback"
                      disabled={!canEdit}
                      value={selectedTransformLayer.playback ?? "hold"}
                      onChange={(event) => updateTransformLayer(selectedTransformLayer.id, (layer) => ({
                        ...layer, playback: event.target.value as TransformLayer["playback"],
                      }))}
                    >
                      <option value="hold">Hold last frame</option>
                      <option value="loop">Loop source</option>
                      <option value="once">Play once, then transparent</option>
                    </select>
                  </label>
                )}
                {selectedTransformLayer.kind === "video" &&
                  selectedTransformLayer.playback === "loop" &&
                  (() => {
                    const sourceMs = numberValue(
                      assets.find((asset) => asset.id === selectedTransformLayer.source_id)?.metadata
                        ?.duration_ms,
                      0,
                    );
                    if (sourceMs <= 0) return null;
                    const exact =
                      (selectedTransformLayer.duration_ms + selectedTransformLayer.in_point_ms) /
                      sourceMs;
                    return (
                      <label>
                        Plays
                        <input
                          aria-label="Layer plays"
                          disabled={!canEdit}
                          min="1"
                          max="10000"
                          step="1"
                          type="number"
                          value={Number.isInteger(exact) ? exact : ""}
                          placeholder={exact.toFixed(2)}
                          onChange={(event) => {
                            const plays = Math.floor(Number(event.target.value));
                            if (!Number.isFinite(plays) || plays < 1) return;
                            const nextDuration = plays * sourceMs - selectedTransformLayer.in_point_ms;
                            if (nextDuration <= 0) return;
                            const end = selectedTransformLayer.start_ms + nextDuration;
                            if (end > duration) {
                              if (composition) setCompositionDuration(composition.id, end);
                              else setSceneDuration(end);
                            }
                            updateTransformLayer(selectedTransformLayer.id, (layer) => ({
                              ...layer,
                              duration_ms: nextDuration,
                              out_point_ms: layer.in_point_ms + nextDuration,
                              keyframes: layer.keyframes.map((keyframe) => ({
                                ...keyframe,
                                time_ms: Math.min(keyframe.time_ms, nextDuration),
                              })),
                            }));
                          }}
                        />
                        <small>
                          Sets the layer to exactly this many back-to-back plays of the{" "}
                          {formatTimecode(sourceMs)} source
                          {Number.isInteger(exact) ? "" : "; the current length is not a whole number of plays"}
                          . Extends the {composition ? "composition" : "scene"} if needed.
                        </small>
                      </label>
                    );
                  })()}
                {selectedTransformLayer.kind !== "audio" &&
                  composition?.space_kind === "stage" && (
                    <StageLayerShape
                      layer={selectedTransformLayer}
                      composition={composition}
                      mapperExport={mapperExport}
                      canEdit={canEdit}
                      onChange={(update) => updateTransformLayer(selectedTransformLayer.id, update)}
                    />
                  )}
                <label>
                  Layer duration
                  <input
                    aria-label="Media layer duration"
                    disabled={!canEdit}
                    max={duration - selectedTransformLayer.start_ms}
                    min="1"
                    step={snapMs}
                    type="number"
                    value={selectedTransformLayer.duration_ms}
                    onChange={(event) =>
                      updateTransformLayer(selectedTransformLayer.id, (layer) => {
                        const nextDuration = Math.max(
                          1,
                          Math.min(
                            duration - layer.start_ms,
                            Number(event.target.value),
                          ),
                        );
                        return {
                          ...layer,
                          duration_ms: nextDuration,
                          out_point_ms: layer.in_point_ms + nextDuration,
                          keyframes: layer.keyframes.map((keyframe) => ({
                            ...keyframe,
                            time_ms: Math.min(keyframe.time_ms, nextDuration),
                          })),
                        };
                      })
                    }
                  />
                </label>
                <label>
                  Source in
                  <input
                    aria-label="Layer source in"
                    disabled={!canEdit}
                    min="0"
                    type="number"
                    value={selectedTransformLayer.in_point_ms}
                    onChange={(event) =>
                      updateTransformLayer(selectedTransformLayer.id, (layer) => {
                        const inPoint = Math.max(0, Number(event.target.value));
                        return {
                          ...layer,
                          in_point_ms: inPoint,
                          out_point_ms: inPoint + layer.duration_ms,
                        };
                      })
                    }
                  />
                </label>
                <label>
                  Source out
                  <input
                    aria-label="Layer source out"
                    disabled={!canEdit}
                    min={selectedTransformLayer.in_point_ms + 1}
                    type="number"
                    value={selectedTransformLayer.out_point_ms}
                    onChange={(event) =>
                      updateTransformLayer(selectedTransformLayer.id, (layer) => {
                        const nextDuration = Math.max(
                          1,
                          Number(event.target.value) - layer.in_point_ms,
                        );
                        const boundedDuration = Math.min(
                          nextDuration,
                          duration - layer.start_ms,
                        );
                        return {
                          ...layer,
                          out_point_ms: layer.in_point_ms + boundedDuration,
                          duration_ms: boundedDuration,
                        };
                      })
                    }
                  />
                </label>
                <label>
                  Visual order
                  <input
                    aria-label="Layer visual order"
                    disabled={!canEdit}
                    type="number"
                    value={selectedTransformLayer.z_index}
                    onChange={(event) =>
                      updateTransformLayer(selectedTransformLayer.id, (layer) => ({
                        ...layer,
                        z_index: Number(event.target.value),
                      }))
                    }
                  />
                </label>
                <label>
                  Fade in
                  <input
                    aria-label="Layer fade in"
                    disabled={!canEdit}
                    min="0"
                    type="number"
                    value={selectedTransformLayer.fade_in_ms}
                    onChange={(event) =>
                      updateTransformLayer(selectedTransformLayer.id, (layer) => ({
                        ...layer,
                        fade_in_ms: Number(event.target.value),
                      }))
                    }
                  />
                </label>
                <label>
                  Fade out
                  <input
                    aria-label="Layer fade out"
                    disabled={!canEdit}
                    min="0"
                    type="number"
                    value={selectedTransformLayer.fade_out_ms}
                    onChange={(event) =>
                      updateTransformLayer(selectedTransformLayer.id, (layer) => ({
                        ...layer,
                        fade_out_ms: Number(event.target.value),
                      }))
                    }
                  />
                </label>
                {selectedTransformLayer.audio && (
                  <>
                    <label>
                      Audio gain
                      <input
                        aria-label="Audio gain"
                        disabled={!canEdit}
                        max="24"
                        min="-96"
                        step="0.1"
                        type="number"
                        value={selectedTransformLayer.audio.gain_db}
                        onChange={(event) =>
                          updateTransformLayer(selectedTransformLayer.id, (layer) => ({
                            ...layer,
                            audio: layer.audio
                              ? {
                                  ...layer.audio,
                                  gain_db: Number(event.target.value),
                                }
                              : null,
                          }))
                        }
                      />
                    </label>
                    <label>
                      Audio mix order
                      <input
                        aria-label="Audio mix order"
                        disabled={!canEdit}
                        min="0"
                        type="number"
                        value={selectedTransformLayer.audio.mix_order}
                        onChange={(event) =>
                          updateTransformLayer(selectedTransformLayer.id, (layer) => ({
                            ...layer,
                            audio: layer.audio
                              ? {
                                  ...layer.audio,
                                  mix_order: Number(event.target.value),
                                }
                              : null,
                          }))
                        }
                      />
                    </label>
                    {(["mute", "solo"] as const).map((field) => (
                      <label key={field}>
                        <input
                          aria-label={`Audio ${field}`}
                          checked={selectedTransformLayer.audio![field]}
                          disabled={!canEdit}
                          type="checkbox"
                          onChange={(event) =>
                            updateTransformLayer(
                              selectedTransformLayer.id,
                              (layer) => ({
                                ...layer,
                                audio: layer.audio
                                  ? {
                                      ...layer.audio,
                                      [field]: event.target.checked,
                                    }
                                  : null,
                              }),
                            )
                          }
                        />
                        {field}
                      </label>
                    ))}
                  </>
                )}
                {selectedTransformLayer.kind !== "audio" && (
                  <>
                    <label>
                      Source alpha
                      <select
                        aria-label="Source alpha interpretation"
                        disabled={!canEdit}
                        title={
                          {
                            opaque: "Opaque",
                            straight: "Straight alpha",
                            premultiplied: "Premultiplied alpha",
                            black_transparent: "Black is transparent (screen)",
                          }[selectedTransformLayer.alpha_interpretation ?? "straight"]
                        }
                        value={selectedTransformLayer.alpha_interpretation ?? "straight"}
                        onChange={(event) =>
                          updateTransformLayer(selectedTransformLayer.id, (layer) => ({
                            ...layer,
                            alpha_interpretation: event.target
                              .value as NonNullable<TransformLayer["alpha_interpretation"]>,
                          }))
                        }
                      >
                        <option value="opaque">Opaque</option>
                        <option value="straight">Straight alpha</option>
                        <option value="premultiplied">Premultiplied alpha</option>
                        <option value="black_transparent">Black is transparent (screen)</option>
                      </select>
                    </label>
                    <label>
                      Source color space
                      <select
                        aria-label="Source color space"
                        disabled={!canEdit}
                        value={selectedTransformLayer.source_color_space ?? "srgb"}
                        onChange={(event) =>
                          updateTransformLayer(selectedTransformLayer.id, (layer) => ({
                            ...layer,
                            source_color_space: event.target
                              .value as NonNullable<TransformLayer["source_color_space"]>,
                          }))
                        }
                      >
                        <option value="srgb">sRGB</option>
                        <option value="bt709">BT.709</option>
                        <option value="linear_srgb">Linear sRGB</option>
                      </select>
                    </label>
                    <label>
                      Scaling behavior
                      <select
                        aria-label="Source scaling behavior"
                        disabled={!canEdit}
                        value={selectedTransformLayer.scaling_behavior ?? "fit"}
                        onChange={(event) =>
                          updateTransformLayer(selectedTransformLayer.id, (layer) => ({
                            ...layer,
                            scaling_behavior: event.target
                              .value as NonNullable<TransformLayer["scaling_behavior"]>,
                          }))
                        }
                      >
                        <option value="fit">Fit</option>
                        <option value="fill">Fill</option>
                        <option value="stretch">Stretch</option>
                        <option value="native">Native size</option>
                      </select>
                    </label>
                    {selectedTransformLayer.alpha_interpretation === "black_transparent" && (
                      <small className="timeline-transform-note">
                        Black is transparent: brightness becomes coverage, so black areas let the
                        lighting or layers underneath show through, like a screen blend. Re-bake
                        LED overlays after changing this.
                      </small>
                    )}
                  </>
                )}
                <label>
                  Keyframe
                  <select
                    aria-label="Transform keyframe"
                    value={selectedTransformKeyframeId}
                    onChange={(event) =>
                      setSelectedTransformKeyframeId(event.target.value)
                    }
                  >
                    {selectedTransformLayer.keyframes.map((keyframe) => (
                      <option key={keyframe.id} value={keyframe.id}>
                        {keyframe.time_ms} ms
                      </option>
                    ))}
                  </select>
                </label>
                {canEdit && selectedTransformLayer.kind !== "audio" && (
                  <button
                    disabled={selectedTransformLayer.keyframes.some(
                      (keyframe) =>
                        keyframe.time_ms ===
                        Math.max(
                          0,
                          Math.min(
                            selectedTransformLayer.duration_ms,
                            localPlayhead - selectedTransformLayer.start_ms,
                          ),
                        ),
                    )}
                    onClick={() => {
                      const keyframe = defaultTransformKeyframe(
                        Math.max(
                          0,
                          Math.min(
                            selectedTransformLayer.duration_ms,
                            localPlayhead - selectedTransformLayer.start_ms,
                          ),
                        ),
                      );
                      updateTransformLayer(selectedTransformLayer.id, (layer) => ({
                        ...layer,
                        keyframes: [...layer.keyframes, keyframe].sort(
                          (left, right) => left.time_ms - right.time_ms,
                        ),
                      }));
                      setSelectedTransformKeyframeId(keyframe.id);
                    }}
                  >
                    Add keyframe at playhead
                  </button>
                )}
                {selectedTransformKeyframe && (
                  <div className="timeline-keyframe-properties">
                    <label>
                      Keyframe time
                      <input
                        aria-label="Transform keyframe time"
                        disabled={!canEdit}
                        max={selectedTransformLayer.duration_ms}
                        min="0"
                        step={snapMs}
                        type="number"
                        value={selectedTransformKeyframe.time_ms}
                        onChange={(event) =>
                          updateTransformKeyframe(
                            selectedTransformLayer.id,
                            selectedTransformKeyframe.id,
                            (keyframe) => ({
                              ...keyframe,
                              time_ms: Math.max(
                                0,
                                Math.min(
                                  selectedTransformLayer.duration_ms,
                                  Number(event.target.value),
                                ),
                              ),
                            }),
                          )
                        }
                      />
                    </label>
                    <label>
                      Snap time to marker
                      <select
                        aria-label="Snap transform keyframe to marker"
                        disabled={!canEdit}
                        value=""
                        onChange={(event) => {
                          const marker = markers.find(
                            (candidate) => candidate.id === event.target.value,
                          );
                          if (marker) {
                            updateTransformKeyframe(
                              selectedTransformLayer.id,
                              selectedTransformKeyframe.id,
                              (keyframe) => ({
                                ...keyframe,
                                time_ms: Math.max(
                                  0,
                                  Math.min(
                                    selectedTransformLayer.duration_ms,
                                    marker.time_ms - selectedTransformLayer.start_ms,
                                  ),
                                ),
                              }),
                            );
                          }
                        }}
                      >
                        <option value="">Named marker…</option>
                        {markers
                          .filter(
                            (marker) =>
                              marker.time_ms >= selectedTransformLayer.start_ms &&
                              marker.time_ms <=
                                selectedTransformLayer.start_ms +
                                  selectedTransformLayer.duration_ms,
                          )
                          .map((marker) => (
                            <option key={marker.id} value={marker.id}>
                              {marker.name} · {marker.time_ms} ms
                            </option>
                          ))}
                      </select>
                    </label>
                    <TransformFields
                      disabled={!canEdit}
                      keyframe={selectedTransformKeyframe}
                      positionUnit={
                        composition?.space_kind === "projector_native"
                          ? "px"
                          : composition?.space_kind === "stage"
                            ? mapperExport?.stage?.unit ?? "u"
                            : composition
                              ? "u"
                              : "px"
                      }
                      sourceSize={layerSourceSize(selectedTransformLayer, assets, composition, compositions)}
                      unitsPerSourcePixel={layerUnitsPerSourcePixel(composition)}
                      onGestureStart={beginGesture}
                      onGestureEnd={endGesture}
                      onChange={(patch) =>
                        updateTransformKeyframe(
                          selectedTransformLayer.id,
                          selectedTransformKeyframe.id,
                          (keyframe) => ({ ...keyframe, ...patch }),
                        )
                      }
                    />
                    <label>
                      Interpolation
                      <select
                        aria-label="Transform interpolation"
                        disabled={!canEdit}
                        value={selectedTransformKeyframe.interpolation.kind}
                        onChange={(event) =>
                          updateTransformKeyframe(
                            selectedTransformLayer.id,
                            selectedTransformKeyframe.id,
                            (keyframe) => ({
                              ...keyframe,
                              interpolation: interpolationFor(
                                event.target.value as TransformKeyframe["interpolation"]["kind"],
                              ),
                            }),
                          )
                        }
                      >
                        <option value="hold">Hold</option>
                        <option value="linear">Linear</option>
                        <option value="ease_in">Ease in</option>
                        <option value="ease_out">Ease out</option>
                        <option value="bezier">Temporal + spatial Bezier</option>
                      </select>
                    </label>
                    {selectedTransformKeyframe.interpolation.kind === "bezier" && (
                      <>
                        <NumberVectorEditor
                          disabled={!canEdit}
                          label="Temporal Bezier"
                          value={
                            selectedTransformKeyframe.interpolation
                              .temporal_bezier ?? [0.25, 0.1, 0.25, 1]
                          }
                          onChange={(value) =>
                            updateTransformKeyframe(
                              selectedTransformLayer.id,
                              selectedTransformKeyframe.id,
                              (keyframe) => ({
                                ...keyframe,
                                interpolation: {
                                  ...keyframe.interpolation,
                                  temporal_bezier: value as [
                                    number,
                                    number,
                                    number,
                                    number,
                                  ],
                                },
                              }),
                            )
                          }
                        />
                        {(["spatial_in", "spatial_out"] as const).map((field) => (
                          <NumberVectorEditor
                            disabled={!canEdit}
                            key={field}
                            label={field === "spatial_in" ? "Spatial in" : "Spatial out"}
                            value={
                              selectedTransformKeyframe.interpolation[field] ?? [0, 0]
                            }
                            onChange={(value) =>
                              updateTransformKeyframe(
                                selectedTransformLayer.id,
                                selectedTransformKeyframe.id,
                                (keyframe) => ({
                                  ...keyframe,
                                  interpolation: {
                                    ...keyframe.interpolation,
                                    [field]: value as [number, number],
                                  },
                                }),
                              )
                            }
                          />
                        ))}
                      </>
                    )}
                  </div>
                )}
              </div>
            )}
            </>
          ) : (
            <div className="timeline-track-properties">
              {selectedTrack ? (
                <>
                  <small>Stable ID: {selectedTrack.id}</small>
                  <small>Kind: {humanizeTrackKind(selectedTrack.kind)}</small>
                  {selectedOutputRef && onOutputsChanged && (
                    <SceneOutputActions
                      draft={draft}
                      sceneId={scene?.id ?? ""}
                      outputId={selectedOutputRef.outputId}
                      kind={selectedOutputRef.kind}
                      disabledReason={
                        !canEdit
                          ? "You can inspect this output but not change it."
                          : timelineDirty
                            ? "Save timeline or undo your edits before renaming or removing outputs."
                            : null
                      }
                      onChanged={onOutputsChanged}
                    />
                  )}
                  {selectedMediaOutput && (
                    <>
                      <strong>Media output · {selectedMediaOutput.id}</strong>
                      <small>Resource: {selectedMediaOutput.resource}</small>
                      <label>
                        Media asset
                        <select
                          disabled={!canEdit}
                          value={selectedMediaOutput.asset ?? ""}
                          onChange={(event) => setScenePolicy((current) => ({
                            ...current,
                            media_assets: {
                              ...current.media_assets,
                              [selectedMediaOutput.id]: event.target.value || null,
                            },
                          }))}
                        >
                          <option value="">No managed asset</option>
                          {selectedMediaOutput.asset && !assets.some((asset) => asset.id === selectedMediaOutput.asset) && (
                            <option value={selectedMediaOutput.asset}>{selectedMediaOutput.asset} (unavailable)</option>
                          )}
                          {assets.filter((asset) => asset.status === "ready" || asset.id === selectedMediaOutput.asset).map((asset) => (
                            <option key={asset.id} value={asset.id}>{asset.name}</option>
                          ))}
                        </select>
                      </label>
                      {scene.schema_version === 2 && (
                        <>
                          <label>
                            Media start delay (ms)
                            <input
                              type="number"
                              min="0"
                              max="5000"
                              step="1"
                              disabled={!canEdit}
                              value={selectedMediaOutput.startDelayMs}
                              onChange={(event) => setScenePolicy((current) => ({
                                ...current,
                                media_offsets: {
                                  ...current.media_offsets,
                                  [selectedMediaOutput.id]: Number(event.target.value),
                                },
                              }))}
                            />
                          </label>
                          <label>
                            Media end behavior
                            <select
                              disabled={!canEdit}
                              value={selectedMediaOutput.endBehavior}
                              onChange={(event) => setScenePolicy((current) => ({
                                ...current,
                                media_end_behaviors: {
                                  ...current.media_end_behaviors,
                                  [selectedMediaOutput.id]: event.target.value as SceneMediaEndBehavior,
                                },
                              }))}
                            >
                              <option value="hold_last_frame">Hold last frame</option>
                              <option value="loop">Loop</option>
                              <option value="stop">Stop</option>
                            </select>
                          </label>
                          <MediaOutputRepeatControls
                            asset={assets.find((asset) => asset.id === selectedMediaOutput.asset)}
                            canEdit={canEdit}
                            output={selectedMediaOutput}
                            sceneDurationMs={scenePolicy.duration_ms}
                            onRepeatCount={(count) => setScenePolicy((current) => ({
                              ...current,
                              media_repeat_counts: {
                                ...current.media_repeat_counts,
                                [selectedMediaOutput.id]: count,
                              },
                            }))}
                            onMatchSceneLength={setSceneDuration}
                          />
                          <label>
                            <input
                              type="checkbox"
                              aria-label="Required media output"
                              disabled={!canEdit}
                              checked={scenePolicy.media_required[selectedMediaOutput.id] ?? true}
                              onChange={(event) => setScenePolicy((current) => ({
                                ...current,
                                media_required: {
                                  ...current.media_required,
                                  [selectedMediaOutput.id]: event.target.checked,
                                },
                              }))}
                            />
                            Required
                          </label>
                          {timelineDiagnostics.filter((diagnostic) => diagnostic.object_id === selectedMediaOutput.id).map((diagnostic) => (
                            <small className="timeline-property-error" key={diagnostic.code}>{diagnostic.message}</small>
                          ))}
                        </>
                      )}
                    </>
                  )}
                  <label>
                    Name
                    <input
                      disabled={selectedTrack.built_in || !canEdit}
                      value={selectedTrack.name}
                      onChange={(event) =>
                        setCustomTracks((current) =>
                          current.map((track) =>
                            track.id === selectedTrack.id
                              ? { ...track, name: event.target.value }
                              : track,
                          ),
                        )
                      }
                    />
                  </label>
                  <label>
                    Resource binding
                    <input
                      disabled={selectedTrack.built_in || !canEdit}
                      value={selectedTrack.resource_binding}
                      onChange={(event) =>
                        setCustomTracks((current) =>
                          current.map((track) =>
                            track.id === selectedTrack.id
                              ? { ...track, resource_binding: event.target.value }
                              : track,
                          ),
                        )
                      }
                    />
                  </label>
                </>
              ) : (
                <small>Select a media layer or track on the timeline to see its properties here.</small>
              )}
            </div>
          )}
        </aside>
      </div>
      <div
        className="timeline-scroll"
        aria-label="Timeline canvas"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
            event.preventDefault();
            movePlayhead((event.key === "ArrowLeft" ? -1 : 1) * snapMs);
          }
        }}
      >
        <div
          ref={canvasRef}
          className="timeline-canvas"
          style={{ width }}
          onClick={(event) => {
            const bounds = event.currentTarget.getBoundingClientRect();
            setPlayhead(
              localOffset +
                snappedLocal(
                  ((event.clientX - bounds.left) / bounds.width) * duration,
                ),
            );
          }}
        >
          <div
            className="timeline-ruler"
            title="Drag to scrub"
            onPointerDown={(event) => {
              event.currentTarget.setPointerCapture(event.pointerId);
              setIsPreviewPlaying(false);
              setPlayhead(localOffset + timeAtClientX(event.clientX));
            }}
            onPointerMove={(event) => {
              if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                setPlayhead(localOffset + timeAtClientX(event.clientX));
              }
            }}
            onClick={(event) => event.stopPropagation()}
          >
            {Array.from({ length: Math.ceil(duration / 1000) + 1 }, (_, second) => (
              <span key={second} style={{ left: `${(second * 1000 * 100) / duration}%` }}>
                {second}s
              </span>
            ))}
          </div>
          <div
            className="timeline-loop"
            style={{
              left: `${(loopStart * 100) / duration}%`,
              width: `${((loopEnd - loopStart) * 100) / duration}%`,
            }}
          />
          {canEdit &&
            ([
              ["start", loopStart, "Loop start handle"],
              ["end", loopEnd, "Loop end handle"],
            ] as const).map(([edge, value, label]) => (
              <span
                key={edge}
                role="slider"
                tabIndex={0}
                aria-label={label}
                aria-valuemin={edge === "start" ? 0 : loopStart + snapMs}
                aria-valuemax={edge === "start" ? loopEnd - snapMs : duration}
                aria-valuenow={value}
                aria-valuetext={formatTimecode(value)}
                className={`timeline-loop-handle timeline-loop-handle--${edge}`}
                style={{ left: `${(value * 100) / duration}%` }}
                title={`${label}: drag or use arrow keys`}
                onClick={(event) => event.stopPropagation()}
                onPointerDown={(event) => {
                  event.stopPropagation();
                  event.currentTarget.setPointerCapture(event.pointerId);
                }}
                onPointerMove={(event) => {
                  if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                    setLoopEdge(edge, timeAtClientX(event.clientX));
                  }
                }}
                onKeyDown={(event) => {
                  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                    event.preventDefault();
                    event.stopPropagation();
                    setLoopEdge(edge, value + (event.key === "ArrowLeft" ? -snapMs : snapMs));
                  }
                }}
              />
            ))}
          {!composition && placedCompositions.length > 0 && (
            <div
              className="timeline-track timeline-track--compositions"
              aria-label="Track Compositions in this scene"
            >
              <span className="timeline-track__label">Compositions in this scene</span>
              {placedCompositions.map((placed) => {
                const placedComposition = compositions[placed.compositionId];
                const start = Math.min(Math.max(0, placed.startMs), duration);
                const end = Math.min(duration, placed.startMs + placed.durationMs);
                return (
                  <button
                    type="button"
                    key={placed.key}
                    aria-label={`Open composition ${placedComposition.name}`}
                    className="timeline-track-reference timeline-track-reference--clip timeline-composition-clip"
                    style={{
                      left: `${(start * 100) / duration}%`,
                      width: `${(Math.max(0, end - start) * 100) / duration}%`,
                    }}
                    title={`${placedComposition.name} · ${humanizeSpace(placedComposition.space_kind)} · ${formatTimecode(placed.startMs)}–${formatTimecode(placed.startMs + placed.durationMs)} · open to edit`}
                    onClick={(event) => {
                      event.stopPropagation();
                      setActiveCompositionId(placed.compositionId);
                    }}
                  >
                    <span className="timeline-track-reference__name">
                      {placedComposition.name}
                    </span>
                    <small>{humanizeSpace(placedComposition.space_kind)}</small>
                  </button>
                );
              })}
            </div>
          )}
          {tracks.map((track) => {
            const asset = timelineTrackAsset(previewScene ?? scene, track, assets);
            const showThumbnail =
              track.kind === "media_output" &&
              asset &&
              hasAssetArtifact(asset, "thumbnail");
            const showWaveform =
              (track.kind === "media_output" ||
                track.kind === "audio_reference") &&
              asset &&
              hasAssetArtifact(asset, "waveform");
            const trackOutput =
              track.kind === "media_output"
                ? sceneOutputs.find((output) => `media:${output.id}` === track.id) ??
                  (sceneOutputs.length === 1 ? sceneOutputs[0] : undefined)
                : undefined;
            const referenceStartMs = Math.min(trackOutput?.startDelayMs ?? 0, duration);
            const assetDuration = numberValue(asset?.metadata?.duration_ms, 0);
            const plays = trackOutput?.repeatCount ?? 1;
            const playedDuration = assetDuration * plays;
            const extendsPastClip = trackOutput?.endBehavior === "loop" || trackOutput?.endBehavior === "hold_last_frame";
            const referenceEndMs =
              playedDuration > 0 && !extendsPastClip
                ? Math.min(duration, referenceStartMs + playedDuration)
                : duration;
            const clipEndPercent =
              playedDuration > 0 && extendsPastClip && referenceStartMs + playedDuration < duration
                ? (playedDuration * 100) / Math.max(1, referenceEndMs - referenceStartMs)
                : null;
            const repeatMarks =
              plays > 1 && assetDuration > 0 && referenceEndMs > referenceStartMs
                ? Array.from({ length: plays - 1 }, (_, index) => ((index + 1) * assetDuration * 100) /
                    (referenceEndMs - referenceStartMs)).filter((percent) => percent < 100)
                : [];
            const layerRow = stackLayerRows(
              spaceTransformLayers.filter((layer) => layer.track_id === track.id),
            );
            const rowCount = Math.max(1, ...[...layerRow.values()].map((row) => row + 1));
            return (
              <div
              style={{ height: `${68 + (rowCount - 1) * TIMELINE_LAYER_ROW_HEIGHT}px` }}
              className={`timeline-track${
                track.id === selectedTrackId ? " timeline-track--selected" : ""
              }${dropTargetId === track.id ? " timeline-drop-target" : ""}`}
              key={track.id}
              aria-label={`Track ${track.name}`}
              onDragOver={(event) => {
                if (!canEdit || !event.dataTransfer.types.includes(TIMELINE_SOURCE_MIME)) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = "copy";
                setDropTargetId(track.id);
              }}
              onDragLeave={() => setDropTargetId(null)}
              onDrop={(event) => {
                const sourceId = event.dataTransfer.getData(TIMELINE_SOURCE_MIME);
                setDropTargetId(null);
                if (!sourceId) return;
                event.preventDefault();
                event.stopPropagation();
                placeSource(sourceId, track.id, timeAtClientX(event.clientX));
              }}
              >
                <button
                  className="timeline-track__label"
                  onClick={(event) => {
                    event.stopPropagation();
                    setSelectedTrackId(track.id);
                    setInspectorTarget("track");
                  }}
                >
                  {humanizeTrackKind(track.kind)} · {track.name}
                </button>
                {(track.kind === "media_output" ||
                  track.kind === "audio_reference") && (
                  <button
                    type="button"
                    aria-label={`${track.name} timing reference`}
                    aria-pressed={inspectorTarget === "track" && selectedTrackId === track.id}
                    className={`timeline-track-reference${asset ? " timeline-track-reference--clip" : ""}`}
                    style={{
                      left: `${(referenceStartMs * 100) / duration}%`,
                      width: `${((referenceEndMs - referenceStartMs) * 100) / duration}%`,
                    }}
                    title={asset ? `${asset.name}${assetDuration > 0 ? ` · ${formatTimecode(assetDuration)}` : ""}` : undefined}
                    onClick={(event) => {
                      event.stopPropagation();
                      setSelectedTrackId(track.id);
                      setInspectorTarget("track");
                    }}
                  >
                    {showWaveform && (
                      <img
                        alt={`Timeline waveform for ${asset.name}`}
                        className="timeline-track-reference__waveform"
                        src={assetArtifactUrl(asset.id, "waveform", asset.current_revision)}
                        style={clipEndPercent === null ? undefined : { width: `${clipEndPercent}%` }}
                      />
                    )}
                    {showThumbnail && (
                      <img
                        alt={`Head frame of ${asset.name}`}
                        className="timeline-track-reference__thumbnail"
                        src={assetArtifactUrl(asset.id, "thumbnail", asset.current_revision)}
                      />
                    )}
                    {asset && track.kind === "media_output" && (
                      <span className="timeline-track-reference__name">
                        {asset.name}{plays > 1 ? ` ×${plays}` : ""}
                      </span>
                    )}
                    {repeatMarks.map((percent) => (
                      <span
                        aria-hidden="true"
                        className="timeline-track-reference__repeat"
                        key={percent}
                        style={{ left: `${percent}%` }}
                      />
                    ))}
                    {clipEndPercent !== null && (
                      <span
                        className="timeline-track-reference__hold"
                        style={{ left: `${clipEndPercent}%` }}
                        title={trackOutput?.endBehavior === "loop" ? "Loops" : "Holds last frame"}
                      >
                        {trackOutput?.endBehavior === "loop" ? "loop" : "hold"}
                      </span>
                    )}
                    {!showThumbnail && !showWaveform && layerRow.size === 0 && (
                      <small>
                        {asset
                          ? "Timing derivative unavailable"
                          : "No asset bound for timing reference"}
                      </small>
                    )}
                  </button>
                )}
                {[...spaceTransformLayers]
                  .filter((layer) => layer.track_id === track.id)
                  .sort(
                    (left, right) =>
                      left.z_index - right.z_index ||
                      left.id.localeCompare(right.id),
                  )
                  .map((layer) => (
                    <button
                      aria-label={`Media layer ${layer.name}`}
                      className={`timeline-transform-layer${
                        inspectorTarget === "layer" && layer.id === selectedTransformLayerId
                          ? " timeline-transform-layer--selected"
                          : ""
                      }${
                        timelineDiagnostics.some(
                          (diagnostic) => diagnostic.object_id === layer.id,
                        )
                          ? " timeline-item--invalid"
                          : ""
                      }`}
                      key={layer.id}
                      style={{
                        left: `${((layerDrag?.id === layer.id ? layerDrag.start : layer.start_ms) * 100) / duration}%`,
                        width: `${((layerDrag?.id === layer.id ? layerDrag.duration : layer.duration_ms) * 100) / duration}%`,
                        top: `${43 + (layerRow.get(layer.id) ?? 0) * TIMELINE_LAYER_ROW_HEIGHT}px`,
                      }}
                      title={canEdit ? `${layer.name}: drag to move, drag either edge to trim, Delete to remove` : layer.name}
                      onPointerDown={(event) => {
                        if (!canEdit || event.button !== 0) return;
                        const bounds = event.currentTarget.getBoundingClientRect();
                        const offset = event.clientX - bounds.left;
                        const edge = Math.min(8, bounds.width / 4);
                        event.currentTarget.setPointerCapture(event.pointerId);
                        setLayerDrag({
                          id: layer.id,
                          mode: offset <= edge ? "start" : offset >= bounds.width - edge ? "end" : "move",
                          startX: event.clientX,
                          moved: false,
                          start: layer.start_ms,
                          duration: layer.duration_ms,
                        });
                      }}
                      onPointerMove={(event) => {
                        if (layerDrag?.id === layer.id) dragLayerTo(event.clientX);
                      }}
                      onPointerUp={() => {
                        if (layerDrag?.id === layer.id) commitLayerDrag();
                      }}
                      onClick={(event) => {
                        event.stopPropagation();
                        if (suppressLayerClickRef.current) return;
                        setSelectedTransformLayerId(layer.id);
                        setInspectorTarget("layer");
                        setSelectedTransformKeyframeId(
                          layer.keyframes[0]?.id ?? "",
                        );
                      }}
                    >
                      <span className="timeline-transform-layer__name" aria-hidden="true">
                        {layer.name}
                      </span>
                      {canEdit && (
                        <>
                          <span className="timeline-transform-layer__trim timeline-transform-layer__trim--start" aria-hidden="true" />
                          <span className="timeline-transform-layer__trim timeline-transform-layer__trim--end" aria-hidden="true" />
                        </>
                      )}
                      {layer.keyframes.map((keyframe) => (
                        <span
                          className={`timeline-transform-keyframe${
                            keyframe.id === selectedTransformKeyframeId &&
                            layer.id === selectedTransformLayerId
                              ? " timeline-transform-keyframe--selected"
                              : ""
                          }`}
                          data-keyframe-id={keyframe.id}
                          key={keyframe.id}
                          style={{
                            left: `${(((keyframeDrag?.keyframeId === keyframe.id ? keyframeDrag.time : keyframe.time_ms)) * 100) / Math.max(1, layerDrag?.id === layer.id ? layerDrag.duration : layer.duration_ms)}%`,
                          }}
                          title={canEdit ? "Keyframe: drag to retime" : "Keyframe"}
                          onPointerDown={(event) => {
                            event.stopPropagation();
                            if (!canEdit || event.button !== 0) return;
                            event.currentTarget.setPointerCapture(event.pointerId);
                            setKeyframeDrag({
                              layerId: layer.id,
                              keyframeId: keyframe.id,
                              startX: event.clientX,
                              moved: false,
                              time: keyframe.time_ms,
                            });
                          }}
                          onPointerMove={(event) => {
                            if (keyframeDrag?.keyframeId === keyframe.id) dragKeyframeTo(event.clientX);
                          }}
                          onPointerUp={(event) => {
                            event.stopPropagation();
                            if (keyframeDrag?.keyframeId === keyframe.id) commitKeyframeDrag();
                          }}
                          onClick={(event) => {
                            event.stopPropagation();
                            if (suppressLayerClickRef.current) return;
                            setSelectedTransformLayerId(layer.id);
                            setSelectedTransformKeyframeId(keyframe.id);
                            setInspectorTarget("layer");
                            setIsPreviewPlaying(false);
                            setPlayhead(localOffset + layer.start_ms + keyframe.time_ms);
                          }}
                        />
                      ))}
                    </button>
                  ))}
              {regions.map((region, index) => (
                <span
                  className="timeline-region"
                  key={`${region.start_ms}:${region.end_ms}:${index}`}
                  style={{
                    left: `${(region.start_ms * 100) / duration}%`,
                    width: `${((region.end_ms - region.start_ms) * 100) / duration}%`,
                  }}
                />
              ))}
              {cues
                .filter((cue) => cue.track_id === track.id)
                .map((cue) => {
                  const time = cueTime(cue, markers);
                  return (
                    <button
                      aria-label={`${cue.name} cue at ${time} milliseconds`}
                      className={`timeline-cue timeline-cue--${cue.kind}${
                        timelineDiagnostics.some(
                          (diagnostic) => diagnostic.object_id === cue.id,
                        )
                          ? " timeline-item--invalid"
                          : ""
                      }`}
                      key={cue.id}
                      style={{
                        left: `${(time * 100) / duration}%`,
                        width: cue.lighting
                          ? `${Math.max(12, (cue.lighting.duration_ms * width) / duration)}px`
                          : undefined,
                        transform: cue.lighting ? "none" : undefined,
                      }}
                      title={`${cue.name} · ${cue.kind}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        setSelectedTrackId(track.id);
                        setInspectorTarget("track");
                      }}
                    />
                  );
                })}
              </div>
            );
          })}
          {renderedMarkers.map((marker) => (
            <button
              aria-label={`${marker.name} sync marker at ${marker.time_ms} milliseconds`}
              className="timeline-sync-marker"
              key={marker.id}
              style={{ left: `${(marker.time_ms * 100) / duration}%` }}
              title={`${marker.name} · shared synchronization marker`}
              onClick={(event) => {
                event.stopPropagation();
                setPlayhead(localOffset + marker.time_ms);
              }}
            />
          ))}
          {safePoints.map((point) => (
            <button
              aria-label={`Marker at ${point} milliseconds`}
              className={`timeline-marker${markerDrag?.from === point ? " timeline-marker--dragging" : ""}`}
              key={point}
              style={{
                left: `${((markerDrag?.from === point ? markerDrag.to : point) * 100) / duration}%`,
              }}
              title={canEdit ? "Drag to move · click to remove" : undefined}
              onPointerDown={(event) => {
                if (!canEdit) return;
                event.stopPropagation();
                event.currentTarget.setPointerCapture(event.pointerId);
                setMarkerDrag({ from: point, to: point, startX: event.clientX, moved: false });
              }}
              onPointerMove={(event) => {
                if (markerDrag?.from !== point) return;
                const moved = markerDrag.moved || Math.abs(event.clientX - markerDrag.startX) > 3;
                setMarkerDrag({
                  ...markerDrag,
                  moved,
                  to: moved ? timeAtClientX(event.clientX) : point,
                });
              }}
              onPointerUp={() => {
                if (markerDrag?.from !== point) return;
                if (markerDrag.moved) {
                  suppressMarkerClickRef.current = true;
                  window.setTimeout(() => {
                    suppressMarkerClickRef.current = false;
                  }, 0);
                  const target = markerDrag.to;
                  setSafePoints((current) =>
                    [...new Set([...current.filter((value) => value !== point), target])].sort(
                      (left, right) => left - right,
                    ),
                  );
                }
                setMarkerDrag(null);
              }}
              onClick={(event) => {
                event.stopPropagation();
                if (suppressMarkerClickRef.current) {
                  suppressMarkerClickRef.current = false;
                  return;
                }
                if (canEdit) {
                  setSafePoints((current) => current.filter((value) => value !== point));
                }
              }}
            />
          ))}
          <span
            className="timeline-playhead"
            style={{ left: `${(localPlayhead * 100) / duration}%` }}
            onPointerDown={startPlayheadDrag}
            onPointerMove={movePlayheadDrag}
            onPointerUp={endPlayheadDrag}
            onPointerCancel={endPlayheadDrag}
            onClick={(event) => event.stopPropagation()}
          />
          <span
            role="slider"
            tabIndex={0}
            aria-label="Playhead"
            aria-valuemin={0}
            aria-valuemax={duration}
            aria-valuenow={Math.round(localPlayhead)}
            aria-valuetext={formatTimecode(localPlayhead)}
            className="timeline-playhead-handle"
            title="Drag to scrub (arrow keys step by the snap interval)"
            style={{ left: `${(localPlayhead * 100) / duration}%` }}
            onPointerDown={startPlayheadDrag}
            onPointerMove={movePlayheadDrag}
            onPointerUp={endPlayheadDrag}
            onPointerCancel={endPlayheadDrag}
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                event.preventDefault();
                event.stopPropagation();
                movePlayhead((event.key === "ArrowLeft" ? -1 : 1) * snapMs);
              } else if (event.key === "Home" || event.key === "End") {
                event.preventDefault();
                setPlayhead(localOffset + (event.key === "Home" ? 0 : duration));
              }
            }}
          />
        </div>
      </div>
      {editorialAudioSources.length > 0 && (
        <EditorialAudioPreview
          isPlaying={isPreviewPlaying}
          playheadMs={localPlayhead}
          sources={editorialAudioSources}
        />
      )}
      <div className="timeline-tool-tabs" role="tablist" aria-label="Timeline tools">
        {TIMELINE_TOOL_TABS.map(([id, label]) => (
          <button
            key={id}
            id={`timeline-tool-tab-${id}`}
            type="button"
            role="tab"
            aria-selected={toolTab === id}
            aria-controls="timeline-tool-panel"
            onClick={() => setToolTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      <div
        className="timeline-tool-panel"
        id="timeline-tool-panel"
        role="tabpanel"
        aria-labelledby={`timeline-tool-tab-${toolTab}`}
      >
        {toolTab === "layers" && (
          <>
          <section className="timeline-transform-editor">
            <strong>Media layers</strong>
            <small className="timeline-coordinate-space">
              Placed in{" "}
              {composition
                ? `${humanizeSpace(composition.space_kind)} · ${composition.target_id} · ${composition.width}×${composition.height}`
                : `the whole scene · ${scene.name}`}
              . Projector calibration stays in the installation and is not
              keyframed here.
            </small>
            {composition?.space_kind === "lighting_surface" && (
              <div className="timeline-render-conformance">
                <strong>Lighting canvas evaluation</strong>
                {(
                  [
                    ["Canvas width", "canvas_width", 1, 2048, 1],
                    ["Canvas height", "canvas_height", 1, 2048, 1],
                    ["Gamma", "gamma", 0.1, 5, 0.1],
                    ["Brightness", "brightness", 0, 1, 0.01],
                  ] as const
                ).map(([label, field, minimum, maximum, step]) => (
                  <label key={field}>
                    {label}
                    <input
                      aria-label={`Lighting ${label.toLocaleLowerCase()}`}
                      disabled={!canEdit}
                      max={maximum}
                      min={minimum}
                      step={step}
                      type="number"
                      value={composition.lighting_settings[field]}
                      onChange={(event) =>
                        setCompositions((current) => ({
                          ...current,
                          [composition.id]: {
                            ...current[composition.id],
                            lighting_settings: {
                              ...current[composition.id].lighting_settings,
                              [field]: Number(event.target.value),
                            },
                          },
                        }))
                      }
                    />
                  </label>
                ))}
                <label>
                  Sampling mode
                  <select
                    aria-label="Lighting sampling mode"
                    disabled={!canEdit}
                    value={composition.lighting_settings.sampling}
                    onChange={(event) =>
                      setCompositions((current) => ({
                        ...current,
                        [composition.id]: {
                          ...current[composition.id],
                          lighting_settings: {
                            ...current[composition.id].lighting_settings,
                            sampling: event.target
                              .value as Composition["lighting_settings"]["sampling"],
                          },
                        },
                      }))
                    }
                  >
                    <option value="nearest">Nearest</option>
                    <option value="bilinear">Bilinear</option>
                    <option value="area">Bounded footprint / area</option>
                  </select>
                </label>
                <small>
                  Logical raster resolution is independent of pixel count and
                  physical output order.
                </small>
              </div>
            )}
            {composition?.space_kind === "lighting_surface" && (
              <section className="timeline-lighting-authoring">
                <strong>Spatial media overlays</strong>
                <small>
                  Add an image, video, or animated GIF below, then position it in surface-local
                  coordinates. Save the revision before previewing or baking. Preview never sends output.
                </small>
                <button
                  disabled={!canEdit || renderJobs.some(
                    (job) => job.composition_id === composition.id &&
                      (job.state === "queued" || job.state === "running"),
                  )}
                  onClick={async () => {
                    try {
                      const job = await createRenderJob(projectId, composition.id);
                      setRenderJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
                      setRenderJobError(null);
                    } catch (error) {
                      setRenderJobError(error instanceof Error ? error.message : "Lighting bake failed.");
                    }
                  }}
                >
                  Bake LED animation
                </button>
                {renderJobs.filter((job) => job.composition_id === composition.id).slice(0, 3).map((job) => (
                  <small key={job.id} role={job.state === "failed" ? "alert" : undefined}>
                    LED bake · revision {job.source_revision} · {job.state}
                    {job.error ? ` · ${job.error}` : ""}
                  </small>
                ))}
                {renderJobError && <small role="alert">{renderJobError}</small>}
                <strong>Lighting author layers</strong>
                {spaceLightingLayers.map((layer) => (
                  <button
                    className={
                      layer.id === selectedLightingLayerId
                        ? "timeline-lighting-layer timeline-lighting-layer--selected"
                        : "timeline-lighting-layer"
                    }
                    key={layer.id}
                    onClick={() => setSelectedLightingLayerId(layer.id)}
                  >
                    {layer.mode} · {layer.name} · precedence {layer.precedence} ·{" "}
                    {layer.blend_mode}
                    {layer.persistent ? " · persistent look" : ""}
                  </button>
                ))}
                {selectedLightingLayer && (
                  <div className="timeline-lighting-layer-properties">
                    <label>
                      Layer name
                      <input
                        aria-label="Lighting layer name"
                        value={selectedLightingLayer.name}
                        onChange={(event) =>
                          setLightingAuthorLayers((current) => ({
                            ...current,
                            [editingSpaceId]: spaceLightingLayers.map((layer) =>
                              layer.id === selectedLightingLayer.id
                                ? { ...layer, name: event.target.value }
                                : layer,
                            ),
                          }))
                        }
                      />
                    </label>
                    {(
                      [
                        ["Start", "start_ms", 0, duration],
                        ["Duration", "duration_ms", 1, duration],
                        ["Precedence", "precedence", 0, 1000],
                        ["Opacity", "opacity", 0, 1],
                      ] as const
                    ).map(([label, field, minimum, maximum]) => (
                      <label key={field}>
                        {label}
                        <input
                          aria-label={`Lighting layer ${label.toLocaleLowerCase()}`}
                          max={maximum}
                          min={minimum}
                          step={field === "opacity" ? 0.01 : 1}
                          type="number"
                          disabled={
                            selectedLightingLayer.persistent &&
                            (field === "start_ms" || field === "duration_ms")
                          }
                          value={selectedLightingLayer[field]}
                          onChange={(event) =>
                            setLightingAuthorLayers((current) => ({
                              ...current,
                              [editingSpaceId]: spaceLightingLayers.map((layer) =>
                                layer.id === selectedLightingLayer.id
                                  ? {
                                      ...layer,
                                      [field]: Number(event.target.value),
                                    }
                                  : layer,
                              ),
                            }))
                          }
                        />
                      </label>
                    ))}
                    <label>
                      <input
                        aria-label="Persistent lighting look"
                        checked={selectedLightingLayer.persistent}
                        type="checkbox"
                        onChange={(event) =>
                          setLightingAuthorLayers((current) => ({
                            ...current,
                            [editingSpaceId]: spaceLightingLayers.map((layer) =>
                              layer.id === selectedLightingLayer.id
                                ? {
                                    ...layer,
                                    persistent: event.target.checked,
                                    start_ms: event.target.checked ? 0 : layer.start_ms,
                                    duration_ms: event.target.checked
                                      ? duration
                                      : layer.duration_ms,
                                  }
                                : layer,
                            ),
                          }))
                        }
                      />
                      Persistent look
                    </label>
                    <label>
                      Blend
                      <select
                        aria-label="Lighting layer blend"
                        value={selectedLightingLayer.blend_mode}
                        onChange={(event) =>
                          setLightingAuthorLayers((current) => ({
                            ...current,
                            [editingSpaceId]: spaceLightingLayers.map((layer) =>
                              layer.id === selectedLightingLayer.id
                                ? {
                                    ...layer,
                                    blend_mode: event.target
                                      .value as LightingAuthorLayer["blend_mode"],
                                  }
                                : layer,
                            ),
                          }))
                        }
                      >
                        <option value="replace">Replace</option>
                        <option value="add">Add</option>
                        <option value="max">Max</option>
                        <option value="multiply">Multiply</option>
                      </select>
                    </label>
                    {selectedLightingLayer.spatial && (
                      <label>
                        Spatial primitive
                        <select
                          aria-label="Spatial lighting primitive"
                          value={selectedLightingLayer.spatial.primitive}
                          onChange={(event) =>
                            setLightingAuthorLayers((current) => ({
                              ...current,
                              [editingSpaceId]: spaceLightingLayers.map((layer) =>
                                layer.id === selectedLightingLayer.id &&
                                layer.spatial
                                  ? {
                                      ...layer,
                                      spatial: {
                                        ...(event.target.value === "ripple"
                                          ? {
                                              primitive: "ripple" as const,
                                              coordinate_space: "surface_local" as const,
                                              origin: [composition.width / 2, composition.height / 2] as [number, number],
                                              speed: 1, ring_width: 0.5,
                                              color: layer.spatial.color, repeat_ms: null,
                                            }
                                          : {
                                              primitive: event.target.value as Exclude<NonNullable<LightingAuthorLayer["spatial"]>["primitive"], "ripple">,
                                              color: layer.spatial.color, end_color: "#000000",
                                              seed: 1, position: [0.5, 0.5] as [number, number],
                                              size: [0.25, 0.25] as [number, number], radius: 0.5,
                                            }),
                                      },
                                    }
                                  : layer,
                              ),
                            }))
                          }
                        >
                          <option value="solid">Solid</option>
                          <option value="linear_gradient">Linear gradient</option>
                          <option value="radial_gradient">Radial gradient</option>
                          <option value="shape">Shape</option>
                          <option value="procedural_noise">Procedural noise</option>
                          <option value="ripple">2D expanding ripple</option>
                        </select>
                      </label>
                    )}
                    {selectedLightingLayer.spatial?.primitive === "ripple" && (
                      <RippleEffectEditor
                        disabled={!canEdit}
                        effect={selectedLightingLayer.spatial}
                        onChange={(spatial) => setLightingAuthorLayers((current) => ({
                          ...current,
                          [editingSpaceId]: spaceLightingLayers.map((layer) =>
                            layer.id === selectedLightingLayer.id ? { ...layer, spatial } : layer,
                          ),
                        }))}
                      />
                    )}
                    {selectedLightingLayer.path && (
                      <label>
                        Semantic path
                        <select
                          aria-label="Lighting semantic path"
                          value={selectedLightingLayer.path.path_id}
                          onChange={(event) =>
                            setLightingAuthorLayers((current) => ({
                              ...current,
                              [editingSpaceId]: spaceLightingLayers.map((layer) =>
                                layer.id === selectedLightingLayer.id && layer.path
                                  ? {
                                      ...layer,
                                      path: {
                                        ...layer.path,
                                        path_id: event.target.value,
                                      },
                                    }
                                  : layer,
                              ),
                            }))
                          }
                        >
                          <option value="">Select path</option>
                          {mapperExport?.paths.map((path) => (
                            <option key={path.semantic_id} value={path.semantic_id}>
                              {path.name}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                    {selectedLightingLayer.fixture && (
                      <label>
                        Semantic fixture/group
                        <input
                          aria-label="Lighting fixture or group target"
                          value={selectedLightingLayer.fixture.target_id}
                          onChange={(event) =>
                            setLightingAuthorLayers((current) => ({
                              ...current,
                              [editingSpaceId]: spaceLightingLayers.map((layer) =>
                                layer.id === selectedLightingLayer.id &&
                                layer.fixture
                                  ? {
                                      ...layer,
                                      fixture: {
                                        ...layer.fixture,
                                        target_id: event.target.value,
                                      },
                                    }
                                  : layer,
                              ),
                            }))
                          }
                        />
                      </label>
                    )}
                  </div>
                )}
                <div className="timeline-lighting-layer-create">
                  <input
                    aria-label="New lighting layer name"
                    placeholder="Lighting layer name"
                    value={newLightingLayerName}
                    onChange={(event) => setNewLightingLayerName(event.target.value)}
                  />
                  <select
                    aria-label="New lighting author mode"
                    value={newLightingLayerMode}
                    onChange={(event) =>
                      setNewLightingLayerMode(
                        event.target.value as LightingAuthorLayer["mode"],
                      )
                    }
                  >
                    <option value="spatial">Spatial canvas</option>
                    <option value="path">Ordered path</option>
                    <option value="fixture">Fixture / group</option>
                  </select>
                  <button
                    disabled={!newLightingLayerName.trim()}
                    onClick={() => {
                      const layer = defaultLightingAuthorLayer(
                        newLightingLayerMode,
                        newLightingLayerName.trim(),
                        duration,
                      );
                      setLightingAuthorLayers((current) => ({
                        ...current,
                        [editingSpaceId]: [...spaceLightingLayers, layer],
                      }));
                      setSelectedLightingLayerId(layer.id);
                      setNewLightingLayerName("");
                    }}
                  >
                    Add lighting layer
                  </button>
                </div>
              </section>
            )}
            <details className="timeline-keyframe-boundary">
              <summary>What can be animated?</summary>
              <small>
                Keyframes: position, independent X/Y scale, rotation, anchor,
                opacity, and crop/reveal bounds.
              </small>
              <small>
                Fixed per cue: lighting effect, target, colors, brightness,
                blend, precedence, seed, and effect parameters. Other property
                animation is not previewed or saved.
              </small>
              <div>
                <button
                  disabled
                  title="Unsupported schema gap: lighting property keyframes require runtime or GUI-05 baking support."
                >
                  Keyframe lighting properties
                </button>
                <button
                  disabled
                  title="Unsupported schema gap: nested composition time remapping is not deterministic in the runtime schema."
                >
                  Time-remap nested composition
                </button>
              </div>
            </details>
            {canEdit && (
              <div className="timeline-transform-create">
                <label>
                  Layer name
                  <input
                    aria-label="New media layer name"
                    placeholder="e.g. Flag ripple"
                    value={newTransformName}
                    onChange={(event) => setNewTransformName(event.target.value)}
                  />
                </label>
                <label>
                  Type
                  <select
                    aria-label="New media layer kind"
                    value={newTransformKind}
                    onChange={(event) => {
                      setNewTransformKind(event.target.value as TransformLayer["kind"]);
                      setNewTransformSource("");
                    }}
                  >
                    <option value="image">Image</option>
                    <option value="video">Video or GIF</option>
                    <option value="audio">Audio</option>
                    <option value="composition">Nested composition</option>
                  </select>
                </label>
                <label>
                  Source
                  <select
                    aria-label="New media layer source"
                    value={newTransformSource}
                    onChange={(event) => {
                      setNewTransformSource(event.target.value);
                      const asset = assets.find((item) => item.id === event.target.value);
                      if (asset?.media_type === "image/gif" || asset?.media_type?.startsWith("video/")) {
                        setNewTransformKind("video");
                      } else if (asset?.media_type?.startsWith("image/")) {
                        setNewTransformKind("image");
                      } else if (asset?.media_type?.startsWith("audio/")) {
                        setNewTransformKind("audio");
                      }
                    }}
                  >
                    <option value="">
                      {newTransformKind === "composition" ? "Choose a composition" : "Choose media"}
                    </option>
                    {newTransformKind === "composition"
                      ? Object.values(compositions)
                          .filter((candidate) => candidate.id !== activeCompositionId)
                          .map((candidate) => (
                            <option key={candidate.id} value={candidate.id}>
                              {candidate.name}
                            </option>
                          ))
                      : assets
                          .filter((asset) => asset.status === "ready")
                          .sort(
                            (left, right) =>
                              Number(assetMatchesLayerKind(right, newTransformKind)) -
                              Number(assetMatchesLayerKind(left, newTransformKind)),
                          )
                          .map((asset) => (
                            <option key={asset.id} value={asset.id}>
                              {asset.name} · {assetKindLabel(asset)}
                            </option>
                          ))}
                  </select>
                </label>
                <label>
                  Track
                  <select
                    aria-label="New media layer track"
                    value={newTransformTrack}
                    onChange={(event) => setNewTransformTrack(event.target.value)}
                  >
                    <option value="">Select track</option>
                    {tracks.map((track) => (
                      <option key={track.id} value={track.id}>
                        {track.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Duration (ms)
                  <input
                    aria-label="New media layer duration"
                    min="1"
                    step={snapMs}
                    type="number"
                    value={newTransformDuration}
                    onChange={(event) =>
                      setNewTransformDuration(Number(event.target.value))
                    }
                  />
                </label>
                <button
                  disabled={
                    !newTransformName.trim() ||
                    !newTransformSource.trim() ||
                    !newTransformTrack ||
                    newTransformDuration <= 0 ||
                    localPlayhead + newTransformDuration > duration
                  }
                  onClick={() => {
                    addMediaLayer({
                      name: newTransformName.trim(),
                      kind: newTransformKind,
                      sourceId: newTransformSource.trim(),
                      trackId: newTransformTrack,
                      startMs: localPlayhead,
                      durationMs: newTransformDuration,
                    });
                    setNewTransformName("");
                  }}
                >
                  Add media layer
                </button>
              </div>
            )}
          </section>
          </>
        )}
        {toolTab === "cues" && (
          <>
          <section className="timeline-sync-controls">
            <strong>Markers</strong>
            <small>
              Moving a marker moves every cue attached to it. Cues not attached to
              a marker keep their own time.
            </small>
            {markers.length > 0 && (
              <VirtualizedList
                ariaLabel="Synchronization markers"
                estimateSize={52}
                items={markers}
                keyFor={(marker) => marker.id}
                renderItem={(marker) => {
                  const linkedCount = cues.filter(
                    (cue) => cue.marker_id === marker.id,
                  ).length;
                  const bounds = markerMoveBounds(marker.id, cues, duration);
                  return (
                <div className="timeline-sync-marker-row" key={marker.id}>
                  <input
                    aria-label={`Sync marker name ${marker.name}`}
                    disabled={!canEdit}
                    value={marker.name}
                    onChange={(event) =>
                      setSyncMarkers((current) => ({
                        ...current,
                        [editingSpaceId]: markers.map((candidate) =>
                          candidate.id === marker.id
                            ? { ...candidate, name: event.target.value }
                            : candidate,
                        ),
                      }))
                    }
                  />
                  <TimeNumberInput
                    aria-label={`Sync marker time ${marker.name}`}
                    disabled={!canEdit}
                    max={bounds.maximum}
                    min={bounds.minimum}
                    step={snapMs}
                    value={marker.time_ms}
                    onEditStart={beginGesture}
                    onEditEnd={endGesture}
                    onCommit={(time) =>
                      setSyncMarkers((current) => ({
                        ...current,
                        [editingSpaceId]: markers.map((candidate) =>
                          candidate.id === marker.id ? { ...candidate, time_ms: time } : candidate,
                        ),
                      }))
                    }
                  />
                  <small>{linkedCount} linked cues</small>
                  {canEdit && (
                    <button
                      disabled={linkedCount > 0}
                      onClick={() =>
                        setSyncMarkers((current) => ({
                          ...current,
                          [editingSpaceId]: markers.filter(
                            (candidate) => candidate.id !== marker.id,
                          ),
                        }))
                      }
                    >
                      Delete marker
                    </button>
                  )}
                </div>
                  );
                }}
              />
            )}
            {canEdit && (
              <div className="timeline-sync-create">
                <label>
                  Marker name
                  <input
                    aria-label="New sync marker name"
                    placeholder="Named synchronization marker"
                    value={newMarkerName}
                    onChange={(event) => setNewMarkerName(event.target.value)}
                  />
                </label>
                <button
                  disabled={
                    !newMarkerName.trim() ||
                    markers.some(
                      (marker) =>
                        marker.name.toLocaleLowerCase() ===
                        newMarkerName.trim().toLocaleLowerCase(),
                    )
                  }
                  onClick={() => {
                    const marker: SyncMarker = {
                      id: crypto.randomUUID(),
                      name: newMarkerName.trim(),
                      time_ms: snappedLocal(localPlayhead),
                    };
                    setSyncMarkers((current) => ({
                      ...current,
                      [editingSpaceId]: [...markers, marker],
                    }));
                    setNewCueMarkerId(marker.id);
                    setNewMarkerName("");
                  }}
                >
                  Add sync marker
                </button>
              </div>
            )}
            {cues.map((cue) => {
              const effectiveTime = cueTime(cue, markers);
              const sceneTimes = resolvedSchedule
                .filter(
                  (entry) =>
                    entry.cue_id === cue.id && entry.scene_id === scene.id,
                )
                .map((entry) => entry.scene_time_ms);
              return (
                <div className="timeline-sync-cue-row" key={cue.id}>
                  <label className="timeline-compound-checkbox">
                    <input
                      aria-label={`Select cue ${cue.name}`}
                      checked={compoundSelection.includes(cue.id)}
                      type="checkbox"
                      onChange={(event) =>
                        setCompoundSelection((current) =>
                          event.target.checked
                            ? [...new Set([...current, cue.id])]
                            : current.filter((id) => id !== cue.id),
                        )
                      }
                    />
                    Select
                  </label>
                  <span>
                    <strong>{cue.name}</strong>
                    <small>
                      {cue.kind} · local {effectiveTime} ms
                      {cue.marker_id
                        ? ` · ${markers.find((marker) => marker.id === cue.marker_id)?.name ?? cue.marker_id} ${cue.marker_offset_ms >= 0 ? "+" : ""}${cue.marker_offset_ms} ms`
                        : " · detached"}
                    </small>
                    <small>
                      Scene timestamps:{" "}
                      {sceneTimes.length
                        ? [...new Set(sceneTimes)].sort((left, right) => left - right).join(", ")
                        : "not instantiated in this scene"}
                      {sceneTimes.length ? " ms" : ""}
                    </small>
                    {timelineDiagnostics
                      .filter((diagnostic) => diagnostic.object_id === cue.id)
                      .map((diagnostic) => (
                        <small className="timeline-property-error" key={diagnostic.code}>
                          {diagnostic.message}
                        </small>
                      ))}
                  </span>
                  {cue.lighting && (
                    <div className="timeline-lighting-properties">
                      <label>
                        Start
                        <TimeNumberInput
                          aria-label={`Lighting cue start ${cue.name}`}
                          disabled={!canEdit}
                          max={Math.max(0, duration - cue.lighting.duration_ms)}
                          min={0}
                          step={snapMs}
                          value={effectiveTime}
                          onEditStart={beginGesture}
                          onEditEnd={endGesture}
                          onCommit={(start) => {
                            setSyncCues((current) => ({
                              ...current,
                              [editingSpaceId]: cues.map((candidate) =>
                                candidate.id === cue.id
                                  ? candidate.marker_id
                                    ? {
                                        ...candidate,
                                        marker_offset_ms:
                                          start -
                                          markerTime(candidate.marker_id, markers),
                                      }
                                    : { ...candidate, time_ms: start }
                                  : candidate,
                              ),
                            }));
                          }}
                        />
                      </label>
                      <label>
                        Duration
                        <input
                          aria-label={`Lighting cue duration ${cue.name}`}
                          disabled={!canEdit}
                          max={duration - effectiveTime}
                          min="1"
                          step={snapMs}
                          type="number"
                          value={cue.lighting.duration_ms}
                          onChange={(event) =>
                            updateLightingCue(cue.id, (lighting) => ({
                              ...lighting,
                              duration_ms: Math.max(
                                1,
                                Math.min(
                                  duration - effectiveTime,
                                  Number(event.target.value),
                                ),
                              ),
                            }))
                          }
                        />
                      </label>
                      <label>
                        Primitive
                        <select
                          aria-label={`Lighting cue primitive ${cue.name}`}
                          disabled={!canEdit}
                          value={cue.lighting.primitive}
                          onChange={(event) =>
                            updateLightingCue(cue.id, (lighting) => ({
                              ...lighting,
                              primitive: event.target.value as LightingPrimitive,
                              end_color:
                                event.target.value === "color_transition"
                                  ? (lighting.end_color ?? "#FFFFFF")
                                  : lighting.end_color,
                            }))
                          }
                        >
                          {lightingPrimitives.map((primitive) => (
                            <option key={primitive} value={primitive}>
                              {primitive.replaceAll("_", " ")}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Target kind
                        <select
                          aria-label={`Lighting cue target kind ${cue.name}`}
                          disabled={!canEdit}
                          value={cue.lighting.target?.kind ?? ""}
                          onChange={(event) =>
                            updateLightingCue(cue.id, (lighting) => ({
                              ...lighting,
                              target: event.target.value
                                ? {
                                    kind: event.target.value as
                                      | "segment"
                                      | "fixture"
                                      | "path",
                                    id: lighting.target?.id ?? "",
                                  }
                                : null,
                            }))
                          }
                        >
                          <option value="">Whole output</option>
                          <option value="segment">Segment</option>
                          <option value="fixture">Fixture</option>
                          <option value="path">Path</option>
                        </select>
                      </label>
                      <label>
                        Target ID
                        <input
                          aria-label={`Lighting cue target ID ${cue.name}`}
                          disabled={!canEdit || !cue.lighting.target}
                          value={cue.lighting.target?.id ?? ""}
                          onChange={(event) =>
                            updateLightingCue(cue.id, (lighting) => ({
                              ...lighting,
                              target: lighting.target
                                ? { ...lighting.target, id: event.target.value }
                                : null,
                            }))
                          }
                        />
                      </label>
                      <label>
                        Color
                        <input
                          aria-label={`Lighting cue color ${cue.name}`}
                          disabled={!canEdit}
                          type="color"
                          value={cue.lighting.color}
                          onChange={(event) =>
                            updateLightingCue(cue.id, (lighting) => ({
                              ...lighting,
                              color: event.target.value.toUpperCase(),
                            }))
                          }
                        />
                      </label>
                      <label>
                        End color
                        <input
                          aria-label={`Lighting cue end color ${cue.name}`}
                          disabled={!canEdit}
                          type="color"
                          value={cue.lighting.end_color ?? cue.lighting.color}
                          onChange={(event) =>
                            updateLightingCue(cue.id, (lighting) => ({
                              ...lighting,
                              end_color: event.target.value.toUpperCase(),
                            }))
                          }
                        />
                      </label>
                      <label>
                        Brightness
                        <input
                          aria-label={`Lighting cue brightness ${cue.name}`}
                          disabled={!canEdit}
                          max="1"
                          min="0"
                          step="0.01"
                          type="number"
                          value={cue.lighting.brightness}
                          onChange={(event) =>
                            updateLightingCue(cue.id, (lighting) => ({
                              ...lighting,
                              brightness: Number(event.target.value),
                            }))
                          }
                        />
                      </label>
                      <label>
                        End brightness
                        <input
                          aria-label={`Lighting cue end brightness ${cue.name}`}
                          disabled={!canEdit}
                          max="1"
                          min="0"
                          step="0.01"
                          type="number"
                          value={cue.lighting.end_brightness ?? cue.lighting.brightness}
                          onChange={(event) =>
                            updateLightingCue(cue.id, (lighting) => ({
                              ...lighting,
                              end_brightness: Number(event.target.value),
                            }))
                          }
                        />
                      </label>
                      <label>
                        Blend
                        <select
                          aria-label={`Lighting cue blend ${cue.name}`}
                          disabled={!canEdit}
                          value={cue.lighting.blend_mode}
                          onChange={(event) =>
                            updateLightingCue(cue.id, (lighting) => ({
                              ...lighting,
                              blend_mode: event.target
                                .value as LightingCueProperties["blend_mode"],
                            }))
                          }
                        >
                          <option value="replace">Replace</option>
                          <option value="add">Add</option>
                          <option value="max">Max</option>
                          <option value="multiply">Multiply</option>
                        </select>
                      </label>
                      {(
                        [
                          ["Precedence", "precedence", 0, 1000, 1],
                          ["Seed", "seed", -2147483648, 2147483647, 1],
                          ["Fade in", "fade_in_ms", 0, cue.lighting.duration_ms, 1],
                          ["Fade out", "fade_out_ms", 0, cue.lighting.duration_ms, 1],
                          ["Period", "period_ms", 1, duration, 1],
                          ["Width pixels", "width_pixels", 1, 1000000, 1],
                          ["Density", "density", 0.01, 1, 0.01],
                        ] as const
                      ).map(([label, field, minimum, maximum, step]) => (
                        <label key={field}>
                          {label}
                          <input
                            aria-label={`Lighting cue ${label.toLocaleLowerCase()} ${cue.name}`}
                            disabled={!canEdit}
                            max={maximum}
                            min={minimum}
                            step={step}
                            type="number"
                            value={cue.lighting![field]}
                            onChange={(event) =>
                              updateLightingCue(cue.id, (lighting) => ({
                                ...lighting,
                                [field]: Number(event.target.value),
                              }))
                            }
                          />
                        </label>
                      ))}
                      <label className="timeline-lighting-checkbox">
                        <input
                          aria-label={`Lighting cue reverse ${cue.name}`}
                          checked={cue.lighting.reverse}
                          disabled={!canEdit}
                          type="checkbox"
                          onChange={(event) =>
                            updateLightingCue(cue.id, (lighting) => ({
                              ...lighting,
                              reverse: event.target.checked,
                            }))
                          }
                        />
                        Reverse
                      </label>
                    </div>
                  )}
                  {canEdit && cue.marker_id && (
                    <button
                      onClick={() =>
                        setSyncCues((current) => ({
                          ...current,
                          [editingSpaceId]: cues.map((candidate) =>
                            candidate.id === cue.id
                              ? {
                                  ...candidate,
                                  marker_id: null,
                                  marker_offset_ms: 0,
                                  time_ms: effectiveTime,
                                }
                              : candidate,
                          ),
                        }))
                      }
                    >
                      Detach cue
                    </button>
                  )}
                  {canEdit && (
                    <button
                      onClick={() =>
                        setSyncCues((current) => ({
                          ...current,
                          [editingSpaceId]: cues.filter(
                            (candidate) => candidate.id !== cue.id,
                          ),
                        }))
                      }
                    >
                      Delete cue
                    </button>
                  )}
                </div>
              );
            })}
            {canEdit && (
              <div className="timeline-sync-create timeline-sync-create--cue">
                <label>
                  Cue name
                  <input
                    aria-label="New synchronized cue name"
                    placeholder="Cue name"
                    value={newCueName}
                    onChange={(event) => setNewCueName(event.target.value)}
                  />
                </label>
                <label>
                  Cue type
                  <select
                    aria-label="New synchronized cue kind"
                    value={newCueKind}
                    onChange={(event) =>
                      setNewCueKind(event.target.value as SyncCueKind)
                    }
                  >
                    <option value="visual">Visual</option>
                    <option value="audio">Audio</option>
                    <option value="lighting">Lighting</option>
                    <option value="switch">Switch</option>
                    <option value="physical_effect">Physical effect</option>
                  </select>
                </label>
                <label>
                  Track
                  <select
                    aria-label="New synchronized cue track"
                    value={newCueTrackId}
                    onChange={(event) => setNewCueTrackId(event.target.value)}
                  >
                    <option value="">Select track</option>
                    {tracks.map((track) => (
                      <option key={track.id} value={track.id}>
                        {track.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Attach to marker
                  <select
                    aria-label="New synchronized cue marker"
                    value={newCueMarkerId}
                    onChange={(event) => setNewCueMarkerId(event.target.value)}
                  >
                    <option value="">Detached at playhead</option>
                    {selectableMarkers.map((marker) => (
                      <option key={marker.id} value={marker.id}>
                        {marker.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Offset from marker (ms)
                  <input
                    aria-label="New synchronized cue marker offset"
                    disabled={!newCueMarkerId}
                    step={snapMs}
                    type="number"
                    value={newCueOffset}
                    onChange={(event) => setNewCueOffset(Number(event.target.value))}
                  />
                </label>
                {newCueKind === "lighting" && (
                  <label>
                    Duration (ms)
                    <input
                      aria-label="New lighting cue duration"
                      min="1"
                      step={snapMs}
                      type="number"
                      value={newCueDuration}
                      onChange={(event) => setNewCueDuration(Number(event.target.value))}
                    />
                  </label>
                )}
                <button
                  disabled={
                    !newCueName.trim() ||
                    !newCueTrackId ||
                    (newCueKind === "lighting" &&
                      (newCueDuration <= 0 ||
                        (newCueMarkerId
                          ? markerTime(newCueMarkerId, markers) + newCueOffset
                          : localPlayhead) +
                          newCueDuration >
                          duration)) ||
                    (newCueMarkerId
                      ? markerTime(newCueMarkerId, markers) + newCueOffset < 0 ||
                        markerTime(newCueMarkerId, markers) + newCueOffset > duration
                      : false)
                  }
                  onClick={() => {
                    const markerId = newCueMarkerId || null;
                    setSyncCues((current) => ({
                      ...current,
                      [editingSpaceId]: [
                        ...cues,
                        {
                          id: crypto.randomUUID(),
                          name: newCueName.trim(),
                          kind: newCueKind,
                          track_id: newCueTrackId,
                          time_ms: markerId ? null : snappedLocal(localPlayhead),
                          marker_id: markerId,
                          marker_offset_ms: markerId ? Math.round(newCueOffset) : 0,
                          lighting:
                            newCueKind === "lighting"
                              ? defaultLightingCue(
                                  newCueDuration,
                                  tracks.find((track) => track.id === newCueTrackId),
                                )
                              : undefined,
                        },
                      ],
                    }));
                    setNewCueName("");
                    setNewCueOffset(0);
                  }}
                >
                  Add synchronized cue
                </button>
              </div>
            )}
            <div className="timeline-preset-controls">
              <strong>Cue presets</strong>
              <label>
                Preset name
                <input
                  aria-label="New cue preset name"
                  disabled={!canEdit}
                  placeholder="Preset name"
                  value={newPresetName}
                  onChange={(event) => setNewPresetName(event.target.value)}
                />
              </label>
              <button
                disabled={
                  !canEdit ||
                  !newPresetName.trim() ||
                  !cues.some(
                    (cue) =>
                      compoundSelection.includes(cue.id) && cue.lighting,
                  )
                }
                onClick={() => {
                  const source = cues.find(
                    (cue) =>
                      compoundSelection.includes(cue.id) && cue.lighting,
                  );
                  if (!source?.lighting) {
                    return;
                  }
                  const preset: CuePreset = {
                    id: crypto.randomUUID(),
                    name: newPresetName.trim(),
                    lighting: cloneLightingCue(source.lighting),
                  };
                  setCuePresets((current) => [...current, preset]);
                  setSelectedPresetId(preset.id);
                  setNewPresetName("");
                }}
              >
                Save selected lighting cue as preset
              </button>
              <label>
                Preset
                <select
                  aria-label="Cue preset"
                  value={selectedPresetId}
                  onChange={(event) => setSelectedPresetId(event.target.value)}
                >
                  <option value="">Select preset</option>
                  {cuePresets.map((preset) => (
                    <option key={preset.id} value={preset.id}>
                      {preset.name}
                    </option>
                  ))}
                </select>
              </label>
              <button
                disabled={
                  !canEdit ||
                  !selectedPresetId ||
                  !cues.some(
                    (cue) =>
                      compoundSelection.includes(cue.id) && cue.lighting,
                  )
                }
                onClick={() => {
                  const preset = cuePresets.find(
                    (candidate) => candidate.id === selectedPresetId,
                  );
                  if (!preset) {
                    return;
                  }
                  setSyncCues((current) => ({
                    ...current,
                    [editingSpaceId]: cues.map((cue) =>
                      compoundSelection.includes(cue.id) && cue.lighting
                        ? {
                            ...cue,
                            lighting: cloneLightingCue(preset.lighting),
                          }
                        : cue,
                    ),
                  }));
                }}
              >
                Apply preset to selected cues
              </button>
              <small>
                Applying a preset copies values into each cue. Runtime cues never
                reference preset IDs.
              </small>
            </div>
            <div className="timeline-compound-controls">
              <strong>Edit selected items</strong>
              {spaceTransformLayers.map((layer) => (
                <label key={layer.id}>
                  <input
                    aria-label={`Select media layer ${layer.name}`}
                    checked={compoundSelection.includes(layer.id)}
                    type="checkbox"
                    onChange={(event) =>
                      setCompoundSelection((current) =>
                        event.target.checked
                          ? [...new Set([...current, layer.id])]
                          : current.filter((id) => id !== layer.id),
                      )
                    }
                  />
                  Transform · {layer.name}
                </label>
              ))}
              <label>
                Shift by (ms)
                <input
                  aria-label="Compound shift milliseconds"
                  step={snapMs}
                  type="number"
                  value={compoundShiftMs}
                  onChange={(event) => setCompoundShiftMs(Number(event.target.value))}
                />
              </label>
              <button
                disabled={
                  !canEdit ||
                  !compoundSelection.length ||
                  !canShiftCompoundSelection(
                    cues,
                    markers,
                    spaceTransformLayers,
                    compoundSelection,
                    compoundShiftMs,
                    duration,
                  )
                }
                onClick={() => {
                  setSyncCues((current) => ({
                    ...current,
                    [editingSpaceId]: cues.map((cue) =>
                      compoundSelection.includes(cue.id)
                        ? cue.marker_id
                          ? {
                              ...cue,
                              marker_offset_ms:
                                cue.marker_offset_ms + compoundShiftMs,
                            }
                          : {
                              ...cue,
                              time_ms: (cue.time_ms ?? 0) + compoundShiftMs,
                            }
                        : cue,
                    ),
                  }));
                  setTransformLayers((current) => ({
                    ...current,
                    [editingSpaceId]: spaceTransformLayers.map((layer) =>
                      compoundSelection.includes(layer.id)
                        ? {
                            ...layer,
                            start_ms: layer.start_ms + compoundShiftMs,
                          }
                        : layer,
                    ),
                  }));
                }}
              >
                Shift selected
              </button>
              <button
                disabled={!canEdit || !compoundSelection.length}
                onClick={() => {
                  const duplicatedCues = cues
                    .filter((cue) => compoundSelection.includes(cue.id))
                    .map((cue) => ({
                      ...cue,
                      id: crypto.randomUUID(),
                      name: `${cue.name} copy`,
                      lighting: cue.lighting
                        ? cloneLightingCue(cue.lighting)
                        : undefined,
                    }));
                  const duplicatedLayers = spaceTransformLayers
                    .filter((layer) => compoundSelection.includes(layer.id))
                    .map((layer) => ({
                      ...layer,
                      id: crypto.randomUUID(),
                      name: `${layer.name} copy`,
                      keyframes: layer.keyframes.map((keyframe) => ({
                        ...keyframe,
                        id: crypto.randomUUID(),
                      })),
                    }));
                  setSyncCues((current) => ({
                    ...current,
                    [editingSpaceId]: [...cues, ...duplicatedCues],
                  }));
                  setTransformLayers((current) => ({
                    ...current,
                    [editingSpaceId]: [
                      ...spaceTransformLayers,
                      ...duplicatedLayers,
                    ],
                  }));
                  setCompoundSelection([]);
                }}
              >
                Duplicate selected
              </button>
              <button
                className="danger-button"
                disabled={!canEdit || !compoundSelection.length}
                onClick={() => {
                  setSyncCues((current) => ({
                    ...current,
                    [editingSpaceId]: cues.filter(
                      (cue) => !compoundSelection.includes(cue.id),
                    ),
                  }));
                  setTransformLayers((current) => ({
                    ...current,
                    [editingSpaceId]: spaceTransformLayers.filter(
                      (layer) => !compoundSelection.includes(layer.id),
                    ),
                  }));
                  setCompoundSelection([]);
                }}
              >
                Delete selected
              </button>
            </div>
          </section>
          </>
        )}
        {toolTab === "tracks" && (
          <>
          <div className="timeline-track-properties">
            {canEdit && (
              <div className="timeline-track-create">
                <label>
                  Track type
                  <select
                    aria-label="New track kind"
                    value={newTrackKind}
                    onChange={(event) => {
                      setNewTrackKind(event.target.value as typeof newTrackKind);
                      setNewTrackBinding("");
                    }}
                  >
                    <option value="audio_reference">Audio reference</option>
                    <option value="fixture">Fixture</option>
                    <option value="group">Group</option>
                    <option value="path">Path</option>
                  </select>
                </label>
                <label>
                  Track name
                  <input
                    aria-label="New track name"
                    placeholder="e.g. Narration"
                    value={newTrackName}
                    onChange={(event) => setNewTrackName(event.target.value)}
                  />
                </label>
                <label>
                  {newTrackKind === "audio_reference" ? "Audio asset" : `Linked ${newTrackKind}`}
                  <input
                    aria-label="New track resource binding"
                    list="timeline-track-bindings"
                    placeholder="Start typing to pick"
                    value={newTrackBinding}
                    onChange={(event) => setNewTrackBinding(event.target.value)}
                  />
                </label>
                <datalist id="timeline-track-bindings">
                  {trackBindingSuggestions(newTrackKind, assets, mapperExport).map(
                    ([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ),
                  )}
                </datalist>
                <button
                  disabled={!newTrackName.trim() || !newTrackBinding.trim()}
                  onClick={() => {
                    const track: TimelineTrack = {
                      id: crypto.randomUUID(),
                      kind: newTrackKind,
                      name: newTrackName,
                      resource_binding: newTrackBinding,
                    };
                    setCustomTracks((current) => [...current, track]);
                    setSelectedTrackId(track.id);
                    setInspectorTarget("track");
                    setNewTrackName("");
                    setNewTrackBinding("");
                  }}
                >
                  Add track
                </button>
              </div>
            )}
          </div>
          </>
        )}
        {toolTab === "compositions" && (
          <>
          <div className="composition-hierarchy">
            <strong>Compositions</strong>
            {Object.values(compositions).length ? (
              <ul>
                {Object.values(compositions).map((candidate) => {
                  const usage = compositionUsage(
                    compositions,
                    sceneCompositions,
                    compositionInstances,
                    transformLayers,
                    sceneNames,
                    candidate.id,
                  );
                  const inThisScene =
                    (sceneCompositions[scene.id] ?? []).includes(candidate.id) ||
                    (compositionInstances[scene.id] ?? []).some(
                      (instance) => instance.composition_id === candidate.id,
                    ) ||
                    (transformLayers[scene.id] ?? []).some(
                      (layer) => layer.kind === "composition" && layer.source_id === candidate.id,
                    );
                  return (
                    <li key={candidate.id}>
                      <span>
                        <strong>{candidate.name}</strong>
                        <small>
                          {candidate.duration_ms} ms · {humanizeSpace(
                            candidate.space_kind,
                          )} {candidate.width}×{candidate.height} · target{" "}
                          {candidate.target_id} · local offset{" "}
                          {candidate.local_time_offset_ms} ms · stable ID {candidate.id}
                        </small>
                        <small>
                          {candidate.children.length
                            ? `Children: ${candidate.children
                                .map((id) => compositions[id]?.name ?? id)
                                .join(", ")}`
                            : "No child compositions"}
                        </small>
                        <small>
                          {usage.length ? `Used by ${usage.join(", ")}` : "Unused"}
                        </small>
                      </span>
                      <button onClick={() => setActiveCompositionId(candidate.id)}>
                        Open timeline
                      </button>
                      {canEdit && inThisScene && (
                        <button onClick={() => removeCompositionFromScene(candidate.id)}>
                          Remove from this scene
                        </button>
                      )}
                      {canEdit && (
                        <button
                          aria-label={`Delete composition ${candidate.name}`}
                          className="danger-button"
                          onClick={() => setPendingDeleteId(candidate.id)}
                        >
                          Delete…
                        </button>
                      )}
                      {canEdit && usage.length > 0 && (
                        <div className="composition-replacement">
                          <select
                            aria-label={`Replacement for ${candidate.name}`}
                            value={replacementByComposition[candidate.id] ?? ""}
                            onChange={(event) =>
                              setReplacementByComposition((current) => ({
                                ...current,
                                [candidate.id]: event.target.value,
                              }))
                            }
                          >
                            <option value="">Select replacement</option>
                            {Object.values(compositions)
                              .filter((item) => item.id !== candidate.id)
                              .map((item) => (
                                <option key={item.id} value={item.id}>
                                  {item.name}
                                </option>
                              ))}
                          </select>
                          <button
                            className="danger-button"
                            disabled={
                              !replacementByComposition[candidate.id] ||
                              !canReplaceComposition(
                                compositions,
                                candidate.id,
                                replacementByComposition[candidate.id],
                              )
                            }
                            onClick={() =>
                              removeComposition(
                                candidate.id,
                                replacementByComposition[candidate.id],
                              )
                            }
                          >
                            Replace and delete
                          </button>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <small>No reusable compositions.</small>
            )}
            {(compositionInstances[scene.id] ?? []).map((instance) => (
              <div className="composition-instance" key={instance.id}>
                <strong>
                  Instance · {compositions[instance.composition_id]?.name ?? instance.composition_id}
                </strong>
                <small>Stable instance ID: {instance.id}</small>
                <small>
                  Start {instance.start_offset_ms} ms · time scale {instance.time_scale}× ·
                  placement ({instance.placement.x}, {instance.placement.y})
                </small>
                <small>
                  Overrides: {JSON.stringify(instance.parameter_overrides)}
                </small>
                {canEdit && (
                  <button
                    aria-label={`Remove instance ${instance.id}`}
                    onClick={() =>
                      setCompositionInstances((current) => ({
                        ...current,
                        [scene.id]: (current[scene.id] ?? []).filter(
                          (candidate) => candidate.id !== instance.id,
                        ),
                      }))
                    }
                  >
                    Remove instance
                  </button>
                )}
              </div>
            ))}
            {canEdit && (
              <div className="composition-create">
                <strong className="composition-create__heading">New composition</strong>
                <label>
                  Name
                  <input
                    aria-label="New composition name"
                    placeholder="Composition name"
                    value={newCompositionName}
                    onChange={(event) => setNewCompositionName(event.target.value)}
                  />
                </label>
                <label>
                  Covers
                  <select
                    aria-label="New composition space"
                    value={newCompositionSpace}
                    onChange={(event) => {
                      const space = event.target.value as Composition["space_kind"];
                      setNewCompositionSpace(space);
                      if (space === "stage") {
                        if (mapperExport?.stage) {
                          setNewCompositionWidth(roundStage(mapperExport.stage.width));
                          setNewCompositionHeight(roundStage(mapperExport.stage.height));
                        }
                        setNewCompositionTarget("shared-stage");
                      } else if (newCompositionTarget === "shared-stage") {
                        setNewCompositionTarget("");
                      }
                    }}
                  >
                    <option value="projector_native">One projector (native pixels)</option>
                    <option value="lighting_surface">One LED surface</option>
                    <option value="stage">Whole display overlay</option>
                  </select>
                </label>
                <label>
                  Output
                  <input
                    aria-label="New composition target"
                    list="timeline-composition-targets"
                    placeholder="Start typing to pick"
                    value={newCompositionTarget}
                    onChange={(event) => setNewCompositionTarget(event.target.value)}
                  />
                </label>
                <datalist id="timeline-composition-targets">
                  {compositionTargetSuggestions(newCompositionSpace, mapperExport).map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </datalist>
                <label>
                  Width
                  <input
                    aria-label="New composition width"
                    min="0.001"
                    step="any"
                    type="number"
                    value={newCompositionWidth}
                    onChange={(event) =>
                      setNewCompositionWidth(Number(event.target.value))
                    }
                  />
                </label>
                <label>
                  Height
                  <input
                    aria-label="New composition height"
                    min="0.001"
                    step="any"
                    type="number"
                    value={newCompositionHeight}
                    onChange={(event) =>
                      setNewCompositionHeight(Number(event.target.value))
                    }
                  />
                </label>
                <label>
                  Duration (ms)
                  <input
                    aria-label="New composition duration"
                    min="1"
                    type="number"
                    value={newCompositionDuration}
                    onChange={(event) =>
                      setNewCompositionDuration(Number(event.target.value))
                    }
                  />
                </label>
                <label>
                  Local time offset (ms)
                  <input
                    aria-label="New composition local offset"
                    min="0"
                    type="number"
                    value={newCompositionOffset}
                    onChange={(event) =>
                      setNewCompositionOffset(Number(event.target.value))
                    }
                  />
                </label>
                <button
                  disabled={
                    !newCompositionName.trim() ||
                    !newCompositionTarget.trim() ||
                    newCompositionDuration <= 0 ||
                    newCompositionWidth <= 0 ||
                    newCompositionHeight <= 0 ||
                    newCompositionOffset < 0
                  }
                  onClick={() => {
                    createComposition({
                      name: newCompositionName,
                      durationMs: newCompositionDuration,
                      space: newCompositionSpace,
                      target: newCompositionTarget,
                      width: newCompositionWidth,
                      height: newCompositionHeight,
                      offsetMs: newCompositionOffset,
                    });
                    setNewCompositionName("");
                    setNewCompositionTarget("");
                  }}
                >
                  Create composition
                </button>
                <strong className="composition-create__heading">Use a composition</strong>
                <label>
                  Composition
                  <select
                    aria-label="Composition to use"
                    value={childCompositionId}
                    onChange={(event) => setChildCompositionId(event.target.value)}
                  >
                    <option value="">Select composition</option>
                    {Object.values(compositions).map((candidate) => (
                      <option key={candidate.id} value={candidate.id}>
                        {candidate.name}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  disabled={
                    !activeCompositionId ||
                    !childCompositionId ||
                    activeCompositionId === childCompositionId ||
                    wouldCreateCycle(
                      compositions,
                      activeCompositionId,
                      childCompositionId,
                    )
                  }
                  onClick={() =>
                    setCompositions((current) => ({
                      ...current,
                      [activeCompositionId]: {
                        ...current[activeCompositionId],
                        children: [
                          ...new Set([
                            ...current[activeCompositionId].children,
                            childCompositionId,
                          ]),
                        ],
                      },
                    }))
                  }
                >
                  Nest in open composition
                </button>
                <label>
                  Starts at (ms)
                  <input
                    aria-label="Instance start offset"
                    min="0"
                    type="number"
                    value={instanceStartOffset}
                    onChange={(event) =>
                      setInstanceStartOffset(Number(event.target.value))
                    }
                  />
                </label>
                <label>
                  Speed
                  <input
                    aria-label="Instance time scale"
                    min="0.01"
                    max="16"
                    step="0.01"
                    type="number"
                    value={instanceTimeScale}
                    onChange={(event) =>
                      setInstanceTimeScale(Number(event.target.value))
                    }
                  />
                </label>
                <label>
                  Stage X
                  <input
                    aria-label="Instance stage X"
                    step="any"
                    type="number"
                    value={instanceX}
                    onChange={(event) => setInstanceX(Number(event.target.value))}
                  />
                </label>
                <label>
                  Stage Y
                  <input
                    aria-label="Instance stage Y"
                    step="any"
                    type="number"
                    value={instanceY}
                    onChange={(event) => setInstanceY(Number(event.target.value))}
                  />
                </label>
                <label>
                  Parameter overrides (JSON)
                  <textarea
                    aria-label="Instance parameter overrides"
                    rows={2}
                    value={instanceOverrides}
                    onChange={(event) => setInstanceOverrides(event.target.value)}
                  />
                </label>
                <button
                  disabled={
                    !childCompositionId ||
                    instanceStartOffset < 0 ||
                    instanceTimeScale <= 0 ||
                    instanceTimeScale > 16 ||
                    parsedInstanceOverrides === null
                  }
                  onClick={() => {
                    setSceneCompositions((current) => ({
                      ...current,
                      [scene.id]: [
                        ...new Set([
                          ...(current[scene.id] ?? []),
                          childCompositionId,
                        ]),
                      ],
                    }));
                    setCompositionInstances((current) => ({
                      ...current,
                      [scene.id]: [
                        ...(current[scene.id] ?? []),
                        {
                          id: crypto.randomUUID(),
                          composition_id: childCompositionId,
                          start_offset_ms: instanceStartOffset,
                          time_scale: instanceTimeScale,
                          parameter_overrides: parsedInstanceOverrides ?? {},
                          placement: { x: instanceX, y: instanceY },
                        },
                      ],
                    }));
                  }}
                >
                  Add to scene
                </button>
              </div>
            )}
          </div>
          </>
        )}
        {toolTab === "render" && (
          <>
            {composition && composition.space_kind !== "lighting_surface" && (
              <div className="timeline-render-conformance">
                <strong>Render conformance · pipeline revision 1</strong>
                <label>
                  Render width
                  <input
                    aria-label="Render width"
                    disabled={!canEdit}
                    min="1"
                    type="number"
                    value={composition.render_settings.render_width}
                    onChange={(event) => {
                      const renderWidth = Number(event.target.value);
                      if (!Number.isFinite(renderWidth) || renderWidth <= 0) return;
                      setCompositions((current) => ({
                        ...current,
                        [composition.id]:
                          current[composition.id].space_kind === "stage"
                            ? withStageSize(current[composition.id], { renderWidth })
                            : {
                                ...current[composition.id],
                                render_settings: {
                                  ...current[composition.id].render_settings,
                                  render_width: renderWidth,
                                },
                              },
                      }));
                    }}
                  />
                </label>
                <label>
                  Render height
                  <input
                    aria-label="Render height"
                    disabled={!canEdit || composition.space_kind === "stage"}
                    title={
                      composition.space_kind === "stage"
                        ? "Follows the composition's shape so media is never stretched"
                        : undefined
                    }
                    min="1"
                    type="number"
                    value={
                      composition.space_kind === "stage"
                        ? stageRenderHeight(
                            composition.width,
                            composition.height,
                            composition.render_settings.render_width,
                          )
                        : composition.render_settings.render_height
                    }
                    onChange={(event) =>
                      setCompositions((current) => ({
                        ...current,
                        [composition.id]: {
                          ...current[composition.id],
                          render_settings: {
                            ...current[composition.id].render_settings,
                            render_height: Number(event.target.value),
                          },
                        },
                      }))
                    }
                  />
                </label>
                <label>
                  Frame-rate numerator
                  <input
                    aria-label="Render frame-rate numerator"
                    disabled={!canEdit}
                    min="1"
                    type="number"
                    value={composition.render_settings.frame_rate_numerator}
                    onChange={(event) =>
                      setCompositions((current) => ({
                        ...current,
                        [composition.id]: {
                          ...current[composition.id],
                          render_settings: {
                            ...current[composition.id].render_settings,
                            frame_rate_numerator: Number(event.target.value),
                          },
                        },
                      }))
                    }
                  />
                </label>
                <label>
                  Frame-rate denominator
                  <input
                    aria-label="Render frame-rate denominator"
                    disabled={!canEdit}
                    min="1"
                    type="number"
                    value={composition.render_settings.frame_rate_denominator}
                    onChange={(event) =>
                      setCompositions((current) => ({
                        ...current,
                        [composition.id]: {
                          ...current[composition.id],
                          render_settings: {
                            ...current[composition.id].render_settings,
                            frame_rate_denominator: Number(event.target.value),
                          },
                        },
                      }))
                    }
                  />
                </label>
                <label>
                  Scaling filter
                  <select
                    aria-label="Render scaling filter"
                    disabled={!canEdit}
                    value={composition.render_settings.scaling_filter}
                    onChange={(event) =>
                      setCompositions((current) => ({
                        ...current,
                        [composition.id]: {
                          ...current[composition.id],
                          render_settings: {
                            ...current[composition.id].render_settings,
                            scaling_filter: event.target
                              .value as Composition["render_settings"]["scaling_filter"],
                          },
                        },
                      }))
                    }
                  >
                    <option value="lanczos">Lanczos</option>
                    <option value="bicubic">Bicubic</option>
                  </select>
                </label>
                <label>
                  Client output preset
                  <select
                    aria-label="Client output preset"
                    disabled={!canEdit}
                    value={composition.render_settings.output_profile.preset_id}
                    onChange={(event) =>
                      setCompositions((current) => ({
                        ...current,
                        [composition.id]: {
                          ...current[composition.id],
                          render_settings: {
                            ...current[composition.id].render_settings,
                            output_profile: outputProfile(
                              event.target.value as
                                | "low_power_h264"
                                | "legacy_h264",
                            ),
                          },
                        },
                      }))
                    }
                  >
                    <option value="low_power_h264">Low-power H.264 Main</option>
                    <option value="legacy_h264">Legacy H.264 Baseline</option>
                  </select>
                </label>
                <label>
                  Final output background
                  <input
                    aria-label="Final output background"
                    disabled={!canEdit}
                    type="color"
                    value={composition.render_settings.output_background_color}
                    onChange={(event) =>
                      setCompositions((current) => ({
                        ...current,
                        [composition.id]: {
                          ...current[composition.id],
                          render_settings: {
                            ...current[composition.id].render_settings,
                            output_background_color:
                              event.target.value.toUpperCase(),
                          },
                        },
                      }))
                    }
                  />
                </label>
                {composition.space_kind === "stage" &&
                  composition.stage_lighting_surfaces.length > 0 && (
                  <label>
                    Where nothing is drawn, LEDs show
                    <select
                      aria-label="Where nothing is drawn, LEDs show"
                      disabled={!canEdit}
                      value={
                        composition.led_background ??
                        (composition.render_settings.output_background_color.toUpperCase() === "#000000"
                          ? "ambient"
                          : "background")
                      }
                      onChange={(event) =>
                        setCompositions((current) => ({
                          ...current,
                          [composition.id]: {
                            ...current[composition.id],
                            led_background: event.target.value as "ambient" | "background",
                          },
                        }))
                      }
                    >
                      <option value="ambient">Ambient lighting (only drawn LEDs change)</option>
                      <option value="background">Final output background (every selected LED)</option>
                    </select>
                    <small>Takes effect at the next LED bake.</small>
                  </label>
                )}
                <label>
                  Media end behavior
                  <select
                    aria-label="Media end behavior"
                    disabled={!canEdit}
                    value={composition.render_settings.media_end_behavior}
                    onChange={(event) =>
                      setCompositions((current) => ({
                        ...current,
                        [composition.id]: {
                          ...current[composition.id],
                          render_settings: {
                            ...current[composition.id].render_settings,
                            media_end_behavior: event.target
                              .value as Composition["render_settings"]["media_end_behavior"],
                          },
                        },
                      }))
                    }
                  >
                    <option value="hold_last_frame">Hold last frame</option>
                    <option value="stop">Stop</option>
                    <option value="loop">Loop after gapless validation</option>
                  </select>
                </label>
                <label>
                  Maximum video bitrate
                  <input
                    aria-label="Maximum video bitrate"
                    disabled={!canEdit}
                    min="500"
                    type="number"
                    value={
                      composition.render_settings.output_profile
                        .max_video_bitrate_kbps
                    }
                    onChange={(event) =>
                      setCompositions((current) => ({
                        ...current,
                        [composition.id]: {
                          ...current[composition.id],
                          render_settings: {
                            ...current[composition.id].render_settings,
                            output_profile: {
                              ...current[composition.id].render_settings
                                .output_profile,
                              max_video_bitrate_kbps: Number(event.target.value),
                            },
                          },
                        },
                      }))
                    }
                  />
                </label>
                <label>
                  Loudness target
                  <input
                    aria-label="Loudness target"
                    disabled={!canEdit}
                    max="-5"
                    min="-40"
                    step="0.1"
                    type="number"
                    value={
                      composition.render_settings.output_profile
                        .loudness_target_lufs
                    }
                    onChange={(event) =>
                      setCompositions((current) => ({
                        ...current,
                        [composition.id]: {
                          ...current[composition.id],
                          render_settings: {
                            ...current[composition.id].render_settings,
                            output_profile: {
                              ...current[composition.id].render_settings
                                .output_profile,
                              loudness_target_lufs: Number(event.target.value),
                            },
                          },
                        },
                      }))
                    }
                  />
                </label>
                <label>
                  Keyframe interval
                  <input
                    aria-label="Keyframe interval"
                    disabled={!canEdit}
                    min="1"
                    type="number"
                    value={
                      composition.render_settings.output_profile
                        .keyframe_interval_frames
                    }
                    onChange={(event) =>
                      setCompositions((current) => ({
                        ...current,
                        [composition.id]: {
                          ...current[composition.id],
                          render_settings: {
                            ...current[composition.id].render_settings,
                            output_profile: {
                              ...current[composition.id].render_settings
                                .output_profile,
                              keyframe_interval_frames: Number(event.target.value),
                            },
                          },
                        },
                      }))
                    }
                  />
                </label>
                <small>
                  Logical {composition.width}×{composition.height} · render{" "}
                  {compositionRasterSize(composition).join("×")} · linear sRGB working
                  color · premultiplied working alpha. Preview and baking consume this
                  same contract.
                </small>
                <small>
                  Target: MP4 · H.264{" "}
                  {composition.render_settings.output_profile.video_profile} level{" "}
                  {composition.render_settings.output_profile.video_level} ·
                  yuv420p · AAC 48 kHz stereo · B-frames{" "}
                  {composition.render_settings.output_profile.b_frames} ·{" "}
                  {
                    composition.render_settings.output_profile
                      .hardware_decoder_compatibility
                  }
                </small>
                {lightsOnlyStage ? (
                  <>
                    <ol className="timeline-render-pipeline" aria-label="Render pipeline order">
                      <li>Decode and conform sources</li>
                      <li>Logical transforms, masks, and alpha composition</li>
                      <li>Sample each mapped LED at its stage position</li>
                      <li>LED brightness limits, then controller LED sequences</li>
                    </ol>
                    <small>
                      Lights only: render size and frame rate apply; video encoder, bitrate,
                      loudness and media end settings are not used because no projector video is
                      made. Render size only sets how finely the video is sampled at each LED and
                      never resizes or stretches media; about {LIGHTS_ONLY_RENDER_WIDTH} px wide is
                      plenty and bakes much faster than projector sizes.
                    </small>
                    {composition.render_settings.render_width > LIGHTS_ONLY_RENDER_WIDTH * 1.5 &&
                      (() => {
                        const [width] = suggestedStageRenderResolution(mapperExport, true);
                        const height = stageRenderHeight(composition.width, composition.height, width);
                        return (
                          <button
                            type="button"
                            disabled={!canEdit}
                            onClick={() =>
                              setCompositions((current) => ({
                                ...current,
                                [composition.id]: withStageSize(current[composition.id], {
                                  renderWidth: width,
                                }),
                              }))
                            }
                          >
                            Use LED bake size ({width} × {height})
                          </button>
                        );
                      })()}
                  </>
                ) : (
                  <ol className="timeline-render-pipeline" aria-label="Render pipeline order">
                    <li>Decode and conform sources</li>
                    <li>Logical transforms, masks, and alpha composition</li>
                    <li>Physical-object mapping and occlusion</li>
                    <li>Projector-native warp, crop, and orientation</li>
                    <li>Overlap and edge blending</li>
                    <li>Projector brightness, black level, and color correction</li>
                    <li>Audio mix, encode, and mux</li>
                  </ol>
                )}
                <div className="timeline-render-jobs">
                  <strong>Controller render jobs</strong>
                  {composition.space_kind === "stage" && !lightsOnlyStage && (
                    <div className="timeline-sync-rehearsal">
                      <strong>Cross-projector rehearsal evidence</strong>
                      <small>
                        {syncAdmission?.message ??
                          "Evaluate or record the current projector group."}
                      </small>
                      <label>
                        Start error ms
                        <input
                          aria-label="Rehearsal start error"
                          min="0"
                          step="0.1"
                          type="number"
                          value={rehearsalStartError}
                          onChange={(event) =>
                            setRehearsalStartError(Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        Drift ms/min
                        <input
                          aria-label="Rehearsal drift"
                          min="0"
                          step="0.1"
                          type="number"
                          value={rehearsalDrift}
                          onChange={(event) =>
                            setRehearsalDrift(Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        Measurement duration seconds
                        <input
                          aria-label="Rehearsal duration"
                          min="1"
                          type="number"
                          value={rehearsalDuration}
                          onChange={(event) =>
                            setRehearsalDuration(Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        Notes
                        <input
                          aria-label="Rehearsal notes"
                          value={rehearsalNotes}
                          onChange={(event) => setRehearsalNotes(event.target.value)}
                        />
                      </label>
                      <button
                        disabled={!canEdit || stageProjectorResources.length < 2}
                        onClick={async () => {
                          try {
                            await recordSyncRehearsal({
                              resources: stageProjectorResources,
                              start_error_ms: rehearsalStartError,
                              drift_ms_per_minute: rehearsalDrift,
                              measurement_duration_seconds: rehearsalDuration,
                              notes: rehearsalNotes || null,
                              measured_at: new Date().toISOString(),
                            });
                            setSyncAdmission(
                              await evaluateSyncAdmission(stageProjectorResources),
                            );
                          } catch (error) {
                            setRenderJobError(
                              error instanceof Error
                                ? error.message
                                : "Rehearsal evidence could not be recorded.",
                            );
                          }
                        }}
                      >
                        Record measured rehearsal
                      </button>
                      <small>
                        Evidence only; this action sends no projector command.
                      </small>
                    </div>
                  )}
                  <button
                    disabled={
                      !canEdit ||
                      (composition.space_kind === "stage" &&
                        syncAdmission?.allowed !== true) ||
                      renderJobs.some(
                        (job) =>
                          job.composition_id === composition.id &&
                          (job.state === "queued" || job.state === "running"),
                      )
                    }
                    title={
                      composition.space_kind === "stage"
                        ? syncAdmission?.message ??
                          "Stage render requires synchronization rehearsal evidence."
                        : "Render the complete image/video/audio composition."
                    }
                    onClick={async () => {
                      try {
                        const job = await createRenderJob(projectId, composition.id);
                        setRenderJobs((current) => [
                          job,
                          ...current.filter((candidate) => candidate.id !== job.id),
                        ]);
                        setRenderJobError(null);
                      } catch (error) {
                        setRenderJobError(
                          error instanceof Error ? error.message : "Render could not start.",
                        );
                      }
                    }}
                  >
                    {lightsOnlyStage
                      ? "Bake LED overlay"
                      : composition.stage_lighting_surfaces.length > 0
                        ? "Bake installation overlay"
                        : "Start controller render"}
                  </button>
                  {renderJobs
                    .filter((job) => job.composition_id === composition.id)
                    .map((job) => (
                      <div className="timeline-render-job" key={job.id}>
                        <span>
                          <strong>{job.state}</strong> ·{" "}
                          {Math.round(job.progress * 100)}% · reservation{" "}
                          {(job.reserved_bytes / (1024 * 1024)).toFixed(1)} MiB
                        </span>
                        <small>Cache {job.cache_key.slice(0, 12)}</small>
                        {job.artifact_id && (
                          <small>Artifact set {job.artifact_id}</small>
                        )}
                        {job.state === "completed" && job.artifact_id && lightsOnlyStage && (
                          <small>LED sequences only · publish the show to send them to the controller.</small>
                        )}
                        {job.state === "completed" && job.artifact_id && !lightsOnlyStage && (
                          <button
                            disabled={deployedArtifactId === job.artifact_id}
                            onClick={async () => {
                              try {
                                const deployment = await deployRenderArtifact(
                                  job.artifact_id!,
                                );
                                setDeployedArtifactId(deployment.artifact_id);
                                setRenderJobError(null);
                              } catch (error) {
                                setRenderJobError(
                                  error instanceof Error
                                    ? error.message
                                    : "Artifact deployment failed.",
                                );
                              }
                            }}
                          >
                            {deployedArtifactId === job.artifact_id
                              ? "Activated on client group"
                              : "Stage and activate client group"}
                          </button>
                        )}
                        {job.error && <small role="alert">{job.error}</small>}
                        {(job.state === "queued" || job.state === "running") && (
                          <button
                            disabled={job.cancel_requested}
                            onClick={async () => {
                              try {
                                const updated = await cancelRenderJob(job.id);
                                setRenderJobs((current) =>
                                  current.map((candidate) =>
                                    candidate.id === updated.id ? updated : candidate,
                                  ),
                                );
                              } catch (error) {
                                setRenderJobError(
                                  error instanceof Error
                                    ? error.message
                                    : "Render cancellation failed.",
                                );
                              }
                            }}
                          >
                            Cancel render
                          </button>
                        )}
                      </div>
                    ))}
                  {renderJobError && <small role="alert">{renderJobError}</small>}
                </div>
                {lightsOnlyStage && (
                  <p className="timeline-viewer__hint">
                    Lights only: there are no projector frames to inspect. Check the sampled LED
                    colors in House view.
                  </p>
                )}
                {!lightsOnlyStage && (
                <div
                  className="timeline-preview-inspection"
                  aria-label="Controller preview inspection"
                >
                  <strong>Controller preview inspection</strong>
                  <label>
                    Checksummed bake
                    <select
                      aria-label="Checksummed bake"
                      value={selectedArtifactId}
                      onChange={(event) => setSelectedArtifactId(event.target.value)}
                    >
                      <option value="">No completed bake</option>
                      {completedRenderJobs.map((job) => (
                        <option key={job.artifact_id!} value={job.artifact_id!}>
                          {job.artifact_id!.slice(0, 12)} · revision {job.source_revision}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Inspection mode
                    <select
                      aria-label="Preview inspection mode"
                      disabled={!selectedArtifactId}
                      value={renderPreviewMode}
                      onChange={(event) =>
                        setRenderPreviewMode(
                          event.target.value as typeof renderPreviewMode,
                        )
                      }
                    >
                      <option value="source">Editable source</option>
                      <option value="baked">Selected bake</option>
                      <option value="side_by_side">Before / after</option>
                      <option value="difference">Frame difference</option>
                    </select>
                  </label>
                  <small>
                    {selectedArtifactJob
                      ? selectedArtifactJob.source_revision === draft.revision
                        ? `Checksummed bake ${selectedArtifactId} matches draft revision ${draft.revision}.`
                        : `Stale bake warning: artifact revision ${selectedArtifactJob.source_revision} differs from draft revision ${draft.revision}.`
                      : "Editable source preview; no checksummed bake is selected."}
                  </small>
                  <small>
                    Source and bake use the same controller composition, alpha, warp,
                    mask, and audio-mix pipeline semantics.
                  </small>
                </div>
                )}
                {composition.space_kind === "projector_native" && (
                  <CompositionPreviewInspection
                    bakedUrl={
                      selectedArtifactId
                        ? renderArtifactPreviewFrameUrl(
                            selectedArtifactId,
                            composition.target_id,
                            Math.min(
                              composition.duration_ms - 1,
                              Math.max(0, localPlayhead),
                            ),
                          )
                        : null
                    }
                    label={`Controller render preview for ${composition.name}`}
                    mode={renderPreviewMode}
                    sourceUrl={compositionPreviewFrameUrl(
                      projectId,
                      composition.id,
                      Math.min(
                        composition.duration_ms - 1,
                        Math.max(0, localPlayhead),
                      ),
                    )}
                  />
                )}
                {composition.space_kind === "stage" &&
                  stageProjectorResources.map((resource) => {
                    return resource ? (
                      <CompositionPreviewInspection
                        bakedUrl={
                          selectedArtifactId
                            ? renderArtifactPreviewFrameUrl(
                                selectedArtifactId,
                                resource,
                                Math.min(
                                  composition.duration_ms - 1,
                                  Math.max(0, localPlayhead),
                                ),
                              )
                            : null
                        }
                        key={resource}
                        label={`Controller render preview for ${composition.name} · ${resource}`}
                        mode={renderPreviewMode}
                        sourceUrl={compositionPreviewFrameUrl(
                          projectId,
                          composition.id,
                          Math.min(
                            composition.duration_ms - 1,
                            Math.max(0, localPlayhead),
                          ),
                          resource,
                        )}
                      />
                    ) : null;
                  })}
              </div>
            )}
            {(!composition || composition.space_kind === "lighting_surface") && (
              <p className="timeline-viewer__hint">
                {composition
                  ? "LED surface compositions bake with Bake LED animation on the Media layers tab."
                  : "Open a projector or whole display overlay composition to render or bake it."}
              </p>
            )}
          </>
        )}
        {toolTab === "ranges" && (
          <>
          {canEdit && (
            <div className="timeline-region-controls">
              <label>
                Protected start
                <TimeNumberInput
                  max={duration}
                  min={0}
                  step={snapMs}
                  value={regionStart}
                  onCommit={setRegionStart}
                />
              </label>
              <label>
                Protected end
                <TimeNumberInput
                  max={duration}
                  min={0}
                  step={snapMs}
                  value={regionEnd}
                  onCommit={setRegionEnd}
                />
              </label>
              <button
                disabled={regionEnd <= regionStart}
                onClick={() =>
                  setRegions((current) => [
                    ...current,
                    { start_ms: regionStart, end_ms: regionEnd },
                  ])
                }
              >
                Add protected region
              </button>
              <label>
                Loop start
                <TimeNumberInput
                  max={duration}
                  min={0}
                  step={snapMs}
                  value={loopStart}
                  onCommit={setLoopStart}
                />
              </label>
              <label>
                Loop end
                <TimeNumberInput
                  max={duration}
                  min={0}
                  step={snapMs}
                  value={loopEnd}
                  onCommit={setLoopEnd}
                />
              </label>
            </div>
          )}
            {!canEdit && <small>Loop and protected ranges are read-only.</small>}
          </>
        )}
      </div>
      {pendingDeleteId && compositions[pendingDeleteId] && (() => {
        const target = compositions[pendingDeleteId];
        const usage = compositionUsage(
          compositions,
          sceneCompositions,
          compositionInstances,
          transformLayers,
          sceneNames,
          target.id,
        );
        return (
          <DetailsDialog
            title={`Delete ${target.name}?`}
            onClose={() => setPendingDeleteId(null)}
          >
            {usage.length ? (
              <>
                <p>It will be removed from:</p>
                <ul aria-label="Composition usage">
                  {usage.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </>
            ) : (
              <p>It is not used by any scene or composition.</p>
            )}
            <p>
              Its media layers, tracks, markers, cues and LED effect layers are deleted with it,
              and its bakes are no longer published. Media assets are kept. Undo restores it;
              nothing changes until you choose Save timeline.
            </p>
            <div className="timeline-dialog-actions">
              <button type="button" onClick={() => setPendingDeleteId(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="danger-button"
                onClick={() => {
                  removeComposition(target.id);
                  setPendingDeleteId(null);
                }}
              >
                Delete composition
              </button>
            </div>
          </DetailsDialog>
        );
      })()}
      {presetDialogOpen && (
        <DetailsDialog title="New composition" onClose={() => setPresetDialogOpen(false)}>
          <div className="composition-presets" role="radiogroup" aria-label="Composition type">
            {([
              ["stage", "Whole display overlay", "One image or video placed across projectors and LEDs."],
              [
                "stage_lights",
                "Whole house lights",
                "One image or video placed on the house photo and sampled by every LED output. No projectors.",
              ],
              ["projector_native", "One projector", "Content sized to a single projector's native pixels."],
              [
                "lighting_surface",
                "One LED surface",
                "Content sampled onto one mapped LED surface, such as a single roofline segment.",
              ],
            ] as const).map(([value, title, description]) => (
              <label
                key={value}
                className={`composition-preset${preset === value ? " composition-preset--selected" : ""}`}
              >
                <input
                  type="radio"
                  name="composition-preset"
                  value={value}
                  checked={preset === value}
                  onChange={() => {
                    setPreset(value);
                    if (value === "projector_native") {
                      const first = (mapperExport?.projectors ?? [])
                        .map((item) => metadataRecord(item))
                        .find((item) => typeof item.resource === "string");
                      setPresetTarget(String(first?.resource ?? ""));
                    } else if (value === "lighting_surface") {
                      setPresetTarget(mapperExport?.lighting_surfaces[0]?.semantic_id ?? "");
                    } else {
                      setPresetTarget("shared-stage");
                    }
                  }}
                />
                <strong>{title}</strong>
                <small>{description}</small>
              </label>
            ))}
          </div>
          {preset === "projector_native" && (
            <label>
              Projector
              <select
                aria-label="Preset projector"
                value={presetTarget}
                onChange={(event) => setPresetTarget(event.target.value)}
              >
                <option value="">Choose a projector</option>
                {mappedProjectorResources.map((resource) => (
                  <option key={resource} value={resource}>
                    {resource}
                  </option>
                ))}
              </select>
            </label>
          )}
          {preset === "lighting_surface" && (
            <label>
              LED surface
              <select
                aria-label="Preset LED surface"
                value={presetTarget}
                onChange={(event) => setPresetTarget(event.target.value)}
              >
                <option value="">Choose an LED surface</option>
                {(mapperExport?.lighting_surfaces ?? []).map((surface) => (
                  <option key={surface.semantic_id} value={surface.semantic_id}>
                    {lightingSurfaceLabel(surface)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            Name
            <input
              aria-label="Preset composition name"
              placeholder={presetSpec().label}
              value={presetName}
              onChange={(event) => setPresetName(event.target.value)}
            />
          </label>
          <label>
            Duration (ms)
            <input
              aria-label="Preset composition duration"
              min="1"
              type="number"
              value={presetDuration}
              onChange={(event) => setPresetDuration(Number(event.target.value))}
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={presetAddToScene}
              onChange={(event) => setPresetAddToScene(event.target.checked)}
            />
            Add to this scene at 0:00
          </label>
          <small>
            Size {presetSpec().width} × {presetSpec().height}
            {preset === "stage" || preset === "stage_lights"
              ? mapperExport?.stage
                ? ` ${mapperExport.stage.unit} (installation stage)`
                : " units · choose a stage view in Stage map for real-world size"
              : preset === "projector_native"
                ? " pixels"
                : " surface units"}
          </small>
          {preset === "stage_lights" && (
            <small role="note">
              {(mapperExport?.lighting_surfaces ?? []).length === 0
                ? "No LED outputs are mapped yet. Map LEDs in Installation first."
                : `Includes all ${(mapperExport?.lighting_surfaces ?? []).length} mapped LED outputs; adjust them under Outputs in this effect.`}
            </small>
          )}
          <button
            type="button"
            className="timeline-primary-action"
            disabled={
              presetDuration <= 0 ||
              !presetSpec().target ||
              (preset === "stage_lights" && (mapperExport?.lighting_surfaces ?? []).length === 0)
            }
            onClick={createFromPreset}
          >
            Create and open
          </button>
        </DetailsDialog>
      )}
      {compositionSettingsOpen && composition && (() => {
        const layersEnd = Math.max(
          0,
          ...(transformLayers[composition.id] ?? []).map((layer) => layer.start_ms + layer.duration_ms),
        );
        return (
          <DetailsDialog
            title={`Composition settings · ${composition.name}`}
            onClose={() => setCompositionSettingsOpen(false)}
          >
            <p className="timeline-dialog-note">Changes are kept with this timeline and saved by Save timeline.</p>
            <section className="timeline-scene-policy">
              <label>
                Composition name
                <input
                  aria-label="Composition name"
                  disabled={!canEdit}
                  value={composition.name}
                  onChange={(event) =>
                    setCompositions((current) => ({
                      ...current,
                      [composition.id]: { ...current[composition.id], name: event.target.value },
                    }))
                  }
                />
              </label>
              <label>
                Composition duration (seconds)
                <input
                  aria-label="Composition duration (seconds)"
                  disabled={!canEdit}
                  min="0.001"
                  step="0.001"
                  type="number"
                  value={composition.duration_ms / 1000}
                  onChange={(event) =>
                    setCompositionDuration(composition.id, Math.round(Number(event.target.value) * 1000))
                  }
                />
                <small>
                  Layers, cues and markers are not trimmed automatically; anything past the new end
                  is reported as an issue. LED bakes cover this whole length, so it must fit in
                  every scene that uses the composition.
                </small>
              </label>
              <div className="timeline-dialog-actions">
                <button
                  type="button"
                  disabled={!canEdit || layersEnd <= 0 || layersEnd === composition.duration_ms}
                  onClick={() => setCompositionDuration(composition.id, layersEnd)}
                >
                  Fit to layers ({formatTimecode(layersEnd)})
                </button>
                <button
                  type="button"
                  disabled={!canEdit || scenePolicy.duration_ms === composition.duration_ms}
                  onClick={() => setCompositionDuration(composition.id, scenePolicy.duration_ms)}
                >
                  Match scene length ({formatTimecode(scenePolicy.duration_ms)})
                </button>
              </div>
            </section>
          </DetailsDialog>
        );
      })()}
      {sceneSettingsOpen && !composition && (
        <DetailsDialog title={`Scene settings · ${scene.name}`} onClose={() => setSceneSettingsOpen(false)}>
          <p className="timeline-dialog-note">Changes are kept with this timeline and saved by Save timeline.</p>
        {!composition && (
          <section className="timeline-scene-policy">
            <strong>Scene scheduling and output policy</strong>
            <label>
              Scene duration (seconds)
              <input
                aria-label="Scene duration (seconds)"
                disabled={!canEdit}
                min="0.001"
                step="0.001"
                type="number"
                value={scenePolicy.duration_ms / 1000}
                onChange={(event) => setSceneDuration(Math.round(Number(event.target.value) * 1000))}
              />
              <small>
                Clips, cues, markers and regions are not trimmed automatically; anything past the
                new end is reported as an issue.
              </small>
            </label>
            <label>
              Scene priority
              <input
                aria-label="Scene priority"
                disabled={!canEdit}
                max="100"
                min="0"
                type="number"
                value={scenePolicy.priority}
                onChange={(event) =>
                  setScenePolicy((current) => ({
                    ...current,
                    priority: Number(event.target.value),
                  }))
                }
              />
            </label>
            <label>
              Completion policy
              <select
                aria-label="Completion policy"
                disabled={!canEdit}
                value={scenePolicy.completion_policy}
                onChange={(event) =>
                  setScenePolicy((current) => ({
                    ...current,
                    completion_policy: event.target
                      .value as SceneOperationalPolicy["completion_policy"],
                  }))
                }
              >
                <option value="loop">Loop</option>
                <option value="select_next">Select next</option>
                <option value="transition_to">Transition to scene</option>
                <option value="hold">Hold</option>
                <option value="stop">Stop</option>
              </select>
            </label>
            {scenePolicy.completion_policy === "transition_to" && (
              <label>
                Completion target
                <select
                  aria-label="Completion target"
                  disabled={!canEdit}
                  value={scenePolicy.completion_target}
                  onChange={(event) =>
                    setScenePolicy((current) => ({
                      ...current,
                      completion_target: event.target.value,
                    }))
                  }
                >
                  <option value="">Select scene</option>
                  {draft.draft.scenes
                    .filter((candidate) => candidate.id !== scene.id)
                    .map((candidate) => (
                      <option key={candidate.id} value={candidate.id}>
                        {candidate.name}
                      </option>
                    ))}
                </select>
              </label>
            )}
            <label>
              Request policy
              <select
                aria-label="Request policy"
                disabled={!canEdit}
                value={scenePolicy.request_policy}
                onChange={(event) =>
                  setScenePolicy((current) => ({
                    ...current,
                    request_policy: event.target
                      .value as SceneOperationalPolicy["request_policy"],
                  }))
                }
              >
                <option value="priority">Priority</option>
                <option value="immediate">Immediate</option>
                <option value="at_safe_point">At safe point</option>
                <option value="after_completion">After completion</option>
                <option value="ignore">Ignore</option>
              </select>
            </label>
            <label>
              Request TTL
              <input
                aria-label="Request TTL"
                disabled={!canEdit}
                min="1"
                type="number"
                value={scenePolicy.request_ttl_ms}
                onChange={(event) =>
                  setScenePolicy((current) => ({
                    ...current,
                    request_ttl_ms: Number(event.target.value),
                  }))
                }
              />
            </label>
            <label className="timeline-scene-policy__claims">
              Resource claims
              <input
                aria-label="Resource claims"
                disabled={!canEdit}
                value={scenePolicy.resources}
                onChange={(event) =>
                  setScenePolicy((current) => ({
                    ...current,
                    resources: event.target.value,
                  }))
                }
              />
            </label>
            {scene.schema_version === 2 && Array.isArray(scene.media) && (
              <>
                <label>
                  Clock authority
                  <select
                    aria-label="Clock authority"
                    disabled={!canEdit}
                    value={scenePolicy.mode}
                    onChange={(event) =>
                      setScenePolicy((current) => ({
                        ...current,
                        mode: event.target.value as SceneOperationalPolicy["mode"],
                        clock_source: event.target.value === "controller_timed"
                          ? "controller"
                          : Array.isArray(scene.media) ? scene.media[0]?.id ?? "" : "",
                      }))
                    }
                  >
                    <option value="media_synchronized">Media playback</option>
                    <option value="controller_timed">Controller</option>
                  </select>
                </label>
                <label>
                  Clock source
                  <select
                    aria-label="Clock source"
                    disabled={!canEdit || scenePolicy.mode === "controller_timed"}
                    value={scenePolicy.clock_source}
                    onChange={(event) =>
                      setScenePolicy((current) => ({
                        ...current,
                        clock_source: event.target.value,
                      }))
                    }
                  >
                    {scenePolicy.mode === "controller_timed" && (
                      <option value="controller">Controller monotonic clock</option>
                    )}
                    {scene.media.map((output) => (
                      <option key={output.id} value={output.id}>
                        {output.id}
                      </option>
                    ))}
                  </select>
                </label>
                {scene.media.map((output) => (
                  <div className="timeline-output-policy" key={output.id}>
                    <strong>Media · {output.id}</strong>
                    <label>
                      <input
                        aria-label={`Required media output ${output.id}`}
                        checked={scenePolicy.media_required[output.id] ?? true}
                        disabled={!canEdit}
                        type="checkbox"
                        onChange={(event) =>
                          setScenePolicy((current) => ({
                            ...current,
                            media_required: {
                              ...current.media_required,
                              [output.id]: event.target.checked,
                            },
                          }))
                        }
                      />
                      Required
                    </label>
                    <label>
                      Start calibration offset
                      <input
                        aria-label={`Media calibration offset ${output.id}`}
                        disabled={!canEdit}
                        max="5000"
                        min="0"
                        type="number"
                        value={scenePolicy.media_offsets[output.id] ?? 0}
                        onChange={(event) =>
                          setScenePolicy((current) => ({
                            ...current,
                            media_offsets: {
                              ...current.media_offsets,
                              [output.id]: Number(event.target.value),
                            },
                          }))
                        }
                      />
                    </label>
                  </div>
                ))}
                {Array.isArray(scene.lighting) &&
                  scene.lighting.map((output) => {
                    const record = metadataRecord(output);
                    const outputId = String(record.id ?? record.resource ?? "");
                    return (
                      <div className="timeline-output-policy" key={outputId}>
                        <strong>Lighting · {outputId}</strong>
                        <label>
                          <input
                            aria-label={`Required lighting output ${outputId}`}
                            checked={
                              scenePolicy.lighting_required[outputId] ?? true
                            }
                            disabled={!canEdit}
                            type="checkbox"
                            onChange={(event) =>
                              setScenePolicy((current) => ({
                                ...current,
                                lighting_required: {
                                  ...current.lighting_required,
                                  [outputId]: event.target.checked,
                                },
                              }))
                            }
                          />
                          Required
                        </label>
                        <label>
                          Timing calibration offset
                          <input
                            aria-label={`Lighting calibration offset ${outputId}`}
                            disabled={!canEdit}
                            max="5000"
                            min="-5000"
                            type="number"
                            value={scenePolicy.lighting_offsets[outputId] ?? 0}
                            onChange={(event) =>
                              setScenePolicy((current) => ({
                                ...current,
                                lighting_offsets: {
                                  ...current.lighting_offsets,
                                  [outputId]: Number(event.target.value),
                                },
                              }))
                            }
                          />
                        </label>
                      </div>
                    );
                  })}
              </>
            )}
          </section>
        )}
        </DetailsDialog>
      )}
      <details
        className="timeline-diagnostics-drawer"
        open={diagnosticsOpen}
        onToggle={(event) => setDiagnosticsOpen(event.currentTarget.open)}
      >
        <summary>Diagnostics</summary>
        <section
          className={`timeline-diagnostics${
            timelineDiagnostics.length ? " timeline-diagnostics--invalid" : ""
          }`}
          aria-label="Timeline validation"
        >
          <strong>
            {timelineDiagnostics.length
              ? `${timelineDiagnostics.length} timeline validation issue${timelineDiagnostics.length === 1 ? "" : "s"}`
              : "Timeline valid"}
          </strong>
          {timelineDiagnostics.length ? (
            <ul>
              {timelineDiagnostics.map((diagnostic) => (
                <li key={`${diagnostic.code}:${diagnostic.object_id}`}>
                  <code>{diagnostic.code}</code> · {diagnostic.message}
                </li>
              ))}
            </ul>
          ) : (
            <small>Scene and editor timing can be saved deterministically.</small>
          )}
        </section>
        <section
          className="timeline-preview-diagnostics"
          aria-label="Backend preview diagnostics"
        >
          <strong>Backend deterministic scene evaluation</strong>
          {previewEvaluation ? (
            <>
              <span>
                {previewEvaluation.frame_rate.numerator}/
                {previewEvaluation.frame_rate.denominator} fps · frame{" "}
                {previewEvaluation.frame_index} · controller time{" "}
                {previewEvaluation.frame_timestamp_ms.toFixed(3)} ms
              </span>
              <span>
                Timing quantization error{" "}
                {previewEvaluation.timing_error_ms.toFixed(3)} ms · source revision{" "}
                {previewEvaluation.source_revision}
              </span>
              <span>
                Resources: {previewEvaluation.resources.join(", ") || "none"}
              </span>
              <span>
                {previewEvaluation.degraded_resources.length
                  ? `Degraded resources: ${previewEvaluation.degraded_resources
                      .map(
                        (resource) =>
                          `${resource.source_id} (${resource.reason})`,
                      )
                      .join(", ")}`
                  : "Degraded resources: none"}
              </span>
              <small>
                Deterministic backend evaluation only; hardware commands sent: no.
              </small>
            </>
          ) : (
            <small role={previewEvaluationError ? "alert" : undefined}>
              {previewEvaluationError ?? "Evaluating controller scene time…"}
            </small>
          )}
        </section>
        <output className="timeline-evaluated-state" aria-label="Timeline evaluated state">
          {JSON.stringify(evaluatedTimelineState)}
        </output>
      </details>
    </>
  );

  return (
    <section className="timeline-editor timeline-editor--workspace" aria-label="Timeline editor">
      {content}
    </section>
  );
}

type KeyframePatch = Partial<
  Pick<TransformKeyframe, "position" | "scale" | "rotation_deg" | "anchor" | "opacity" | "crop">
>;

type GestureHandlers = {
  onGestureStart?: () => void;
  onGestureEnd?: () => void;
};

function roundTo(value: number, precision: number): number {
  const factor = 10 ** precision;
  return Math.round(value * factor) / factor;
}

/** Number field whose label can be dragged horizontally to change the value (Shift ×10, Alt ×0.1). */
function ScrubField({
  label,
  ariaLabel,
  value,
  onChange,
  step = 1,
  min,
  max,
  unit,
  precision = 2,
  disabled = false,
  onGestureStart,
  onGestureEnd,
}: {
  label: string;
  ariaLabel: string;
  value: number;
  onChange: (value: number) => void;
  step?: number;
  min?: number;
  max?: number;
  unit?: string;
  precision?: number;
  disabled?: boolean;
} & GestureHandlers) {
  const dragRef = useRef<{ x: number; start: number; moved: boolean } | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const clamp = (next: number) =>
    Math.min(max ?? Number.POSITIVE_INFINITY, Math.max(min ?? Number.NEGATIVE_INFINITY, next));
  return (
    <label className={`scrub-field${disabled ? " scrub-field--disabled" : ""}`}>
      <span
        className="scrub-field__label"
        title={disabled ? undefined : `Drag to adjust ${label.toLowerCase()} (Shift ×10, Alt fine)`}
        onPointerDown={(event) => {
          if (disabled || event.button !== 0) return;
          event.preventDefault();
          event.currentTarget.setPointerCapture(event.pointerId);
          dragRef.current = { x: event.clientX, start: value, moved: false };
        }}
        onPointerMove={(event) => {
          const drag = dragRef.current;
          if (!drag) return;
          const pixels = event.clientX - drag.x;
          if (!drag.moved && Math.abs(pixels) < 3) return;
          if (!drag.moved) {
            drag.moved = true;
            onGestureStart?.();
          }
          const factor = event.shiftKey ? 10 : event.altKey ? 0.1 : 1;
          onChange(clamp(roundTo(drag.start + Math.round(pixels / 2) * step * factor, precision)));
        }}
        onPointerUp={() => {
          if (dragRef.current?.moved) onGestureEnd?.();
          dragRef.current = null;
        }}
        onPointerCancel={() => {
          if (dragRef.current?.moved) onGestureEnd?.();
          dragRef.current = null;
        }}
      >
        {label}
      </span>
      <span className="scrub-field__control">
        <input
          aria-label={ariaLabel}
          disabled={disabled}
          step="any"
          type="number"
          value={draft ?? String(roundTo(value, precision))}
          onChange={(event) => {
            setDraft(event.target.value);
            const next = Number(event.target.value);
            if (event.target.value.trim() !== "" && Number.isFinite(next)) onChange(clamp(next));
          }}
          onBlur={() => setDraft(null)}
          onKeyDown={(event) => {
            if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
            event.preventDefault();
            const factor = event.shiftKey ? 10 : event.altKey ? 0.1 : 1;
            setDraft(null);
            onChange(clamp(roundTo(value + (event.key === "ArrowUp" ? 1 : -1) * step * factor, precision)));
          }}
        />
        {unit && <span className="scrub-field__unit">{unit}</span>}
      </span>
    </label>
  );
}

const ANCHOR_PRESETS: [number, number, string][] = [
  [0, 0, "top left"],
  [0.5, 0, "top"],
  [1, 0, "top right"],
  [0, 0.5, "left"],
  [0.5, 0.5, "center"],
  [1, 0.5, "right"],
  [0, 1, "bottom left"],
  [0.5, 1, "bottom"],
  [1, 1, "bottom right"],
];

/** Position correction so changing the anchor keeps the layer visually in place. */
function anchorCompensatedPosition(
  keyframe: TransformKeyframe,
  anchor: [number, number],
  sourceSize: [number, number],
  unitsPerSourcePixel: number,
): [number, number] {
  const dx = (anchor[0] - keyframe.anchor[0]) * sourceSize[0] * keyframe.scale[0] * unitsPerSourcePixel;
  const dy = (anchor[1] - keyframe.anchor[1]) * sourceSize[1] * keyframe.scale[1] * unitsPerSourcePixel;
  const angle = (keyframe.rotation_deg * Math.PI) / 180;
  return [
    keyframe.position[0] + dx * Math.cos(angle) - dy * Math.sin(angle),
    keyframe.position[1] + dx * Math.sin(angle) + dy * Math.cos(angle),
  ];
}

function TransformFields({
  keyframe,
  disabled,
  positionUnit,
  sourceSize,
  unitsPerSourcePixel,
  onChange,
  onGestureStart,
  onGestureEnd,
}: {
  keyframe: TransformKeyframe;
  disabled: boolean;
  positionUnit: string;
  sourceSize: [number, number];
  unitsPerSourcePixel: number;
  onChange: (patch: KeyframePatch) => void;
} & GestureHandlers) {
  const [linked, setLinked] = useState(true);
  const gesture = { onGestureStart, onGestureEnd, disabled };
  const [scaleX, scaleY] = keyframe.scale;
  const crop = keyframe.crop;
  const setCrop = (index: number, percent: number) => {
    const next = [...crop] as [number, number, number, number];
    next[index] = percent / 100;
    onChange({ crop: next });
  };
  return (
    <div className="transform-fields" aria-label="Layer transform">
      <div className="transform-fields__row">
        <span className="transform-fields__name">Position</span>
        <ScrubField
          label="X"
          ariaLabel="Position 1"
          value={keyframe.position[0]}
          unit={positionUnit}
          onChange={(value) => onChange({ position: [value, keyframe.position[1]] })}
          {...gesture}
        />
        <ScrubField
          label="Y"
          ariaLabel="Position 2"
          value={keyframe.position[1]}
          unit={positionUnit}
          onChange={(value) => onChange({ position: [keyframe.position[0], value] })}
          {...gesture}
        />
      </div>
      <div className="transform-fields__row">
        <span className="transform-fields__name">Scale</span>
        <ScrubField
          label="W"
          ariaLabel="Scale 1"
          value={scaleX * 100}
          unit="%"
          precision={1}
          onChange={(value) => {
            const next = value / 100;
            onChange({
              scale: !linked ? [next, scaleY] : scaleX !== 0 ? [next, roundTo(scaleY * (next / scaleX), 4)] : [next, next],
            });
          }}
          {...gesture}
        />
        <ScrubField
          label="H"
          ariaLabel="Scale 2"
          value={scaleY * 100}
          unit="%"
          precision={1}
          onChange={(value) => {
            const next = value / 100;
            onChange({
              scale: !linked ? [scaleX, next] : scaleY !== 0 ? [roundTo(scaleX * (next / scaleY), 4), next] : [next, next],
            });
          }}
          {...gesture}
        />
        <button
          type="button"
          className="transform-fields__link"
          aria-label="Lock scale proportions"
          aria-pressed={linked}
          title={linked ? "Proportions locked" : "Proportions unlocked"}
          disabled={disabled}
          onClick={() => setLinked((current) => !current)}
        >
          {linked ? "🔗" : "⛓️‍💥"}
        </button>
      </div>
      <div className="transform-fields__row">
        <span className="transform-fields__name">Rotation</span>
        <ScrubField
          label="°"
          ariaLabel="Rotation"
          value={keyframe.rotation_deg}
          unit="°"
          precision={1}
          onChange={(value) => onChange({ rotation_deg: value })}
          {...gesture}
        />
      </div>
      <div className="transform-fields__row">
        <span className="transform-fields__name">Opacity</span>
        <input
          aria-label="Opacity slider"
          className="transform-fields__slider"
          disabled={disabled}
          max="100"
          min="0"
          step="1"
          type="range"
          value={Math.round(keyframe.opacity * 100)}
          onPointerDown={() => onGestureStart?.()}
          onPointerUp={() => onGestureEnd?.()}
          onChange={(event) => onChange({ opacity: Number(event.target.value) / 100 })}
        />
        <ScrubField
          label="%"
          ariaLabel="Opacity"
          value={keyframe.opacity * 100}
          min={0}
          max={100}
          unit="%"
          precision={0}
          onChange={(value) => onChange({ opacity: value / 100 })}
          {...gesture}
        />
      </div>
      <div className="transform-fields__row transform-fields__row--anchor">
        <span className="transform-fields__name">Anchor</span>
        <div className="anchor-grid" role="group" aria-label="Anchor point">
          {ANCHOR_PRESETS.map(([x, y, name]) => (
            <button
              key={name}
              type="button"
              aria-label={`Anchor ${name}`}
              aria-pressed={Math.abs(keyframe.anchor[0] - x) < 1e-6 && Math.abs(keyframe.anchor[1] - y) < 1e-6}
              disabled={disabled}
              title={`Anchor ${name} (layer stays in place)`}
              onClick={() =>
                onChange({
                  anchor: [x, y],
                  position: anchorCompensatedPosition(keyframe, [x, y], sourceSize, unitsPerSourcePixel),
                })
              }
            />
          ))}
        </div>
        <ScrubField
          label="X"
          ariaLabel="Anchor 1"
          value={keyframe.anchor[0] * 100}
          unit="%"
          precision={1}
          onChange={(value) => onChange({ anchor: [value / 100, keyframe.anchor[1]] })}
          {...gesture}
        />
        <ScrubField
          label="Y"
          ariaLabel="Anchor 2"
          value={keyframe.anchor[1] * 100}
          unit="%"
          precision={1}
          onChange={(value) => onChange({ anchor: [keyframe.anchor[0], value / 100] })}
          {...gesture}
        />
      </div>
      <div className="transform-fields__row">
        <span className="transform-fields__name">Crop</span>
        {(["L", "T", "R", "B"] as const).map((edge, index) => (
          <ScrubField
            key={edge}
            label={edge}
            ariaLabel={`Crop ${index + 1}`}
            value={crop[index] * 100}
            min={0}
            max={99}
            unit="%"
            precision={1}
            onChange={(value) => setCrop(index, value)}
            {...gesture}
          />
        ))}
      </div>
    </div>
  );
}

function layerUnitsPerSourcePixel(composition: Composition | undefined): number {
  if (!composition) return 1;
  if (composition.space_kind === "stage" && (composition.source_pixel_units ?? 0) > 0) {
    return composition.source_pixel_units!;
  }
  const raster =
    composition.space_kind === "lighting_surface"
      ? composition.lighting_settings.canvas_width
      : composition.render_settings.render_width;
  return raster > 0 && composition.width > 0 ? composition.width / raster : 1;
}

function layerSourceSize(
  layer: TransformLayer,
  assets: AssetRecord[],
  composition: Composition | undefined,
  compositions: Record<string, Composition>,
): [number, number] {
  const child = compositions[layer.source_id];
  if (child) return compositionRasterSize(child);
  const asset = assets.find((candidate) => candidate.id === layer.source_id);
  const width = numberValue(asset?.metadata?.width, 0);
  const height = numberValue(asset?.metadata?.height, 0);
  if (width > 0 && height > 0) return [width, height];
  const upp = layerUnitsPerSourcePixel(composition);
  return composition ? [composition.width / upp, composition.height / upp] : [1920, 1080];
}

/**
 * Millisecond field that accepts typed values: what is typed stays visible while focused and
 * each valid number is applied unsnapped (clamped to min/max); arrows step by `step`.
 */
function TimeNumberInput({
  value,
  min,
  max,
  onCommit,
  onEditStart,
  onEditEnd,
  ...rest
}: Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type" | "min" | "max"> & {
  value: number;
  min: number;
  max: number;
  onCommit: (milliseconds: number) => void;
  onEditStart?: () => void;
  onEditEnd?: () => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <input
      {...rest}
      type="number"
      min={min}
      max={max}
      value={draft ?? String(value)}
      onFocus={() => {
        setDraft(String(value));
        onEditStart?.();
      }}
      onBlur={() => {
        setDraft(null);
        onEditEnd?.();
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
      }}
      onChange={(event) => {
        setDraft(event.target.value);
        const typed = event.target.valueAsNumber;
        if (Number.isFinite(typed)) onCommit(Math.round(Math.min(max, Math.max(min, typed))));
      }}
    />
  );
}

function keyframeAtTime(layer: TransformLayer, localMs: number): TransformKeyframe | undefined {
  const sorted = [...layer.keyframes].sort((left, right) => left.time_ms - right.time_ms);
  return [...sorted].reverse().find((keyframe) => keyframe.time_ms <= localMs) ?? sorted[0];
}

/**
 * Source time shown for a layer at a composition-local time, mirroring the renderer:
 * in point plus elapsed time; loop wraps by the source length, once ends after it,
 * hold freezes on the last frame. Outside the layer's span the nearest end is shown.
 */
function layerSourceMs(
  layer: TransformLayer,
  localMs: number,
  sourceDurationMs: number,
): number | null {
  const elapsed = Math.min(Math.max(0, localMs - layer.start_ms), Math.max(0, layer.duration_ms - 1));
  const sourceMs = layer.in_point_ms + elapsed;
  if (sourceDurationMs <= 0) return sourceMs;
  if (layer.playback === "loop") return sourceMs % sourceDurationMs;
  if (layer.playback === "once" && sourceMs >= sourceDurationMs) return null;
  return Math.min(sourceMs, sourceDurationMs - 1);
}

/** Review proxy seeked to the layer's frame at the playhead, so placement can be checked by scrubbing. */
function LayoutVideoFrame({
  label,
  src,
  poster,
  sourceMs,
  isPlaying,
  style,
}: {
  label: string;
  src: string;
  poster?: string;
  sourceMs: number | null;
  isPlaying: boolean;
  style: CSSProperties;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  useEffect(() => {
    const element = videoRef.current;
    if (!element || sourceMs === null) return;
    const target = sourceMs / 1000;
    if (Math.abs(element.currentTime - target) > (isPlaying ? 0.35 : 0.02)) {
      element.currentTime = target;
    }
    if (isPlaying && element.paused) {
      void element.play().catch(() => undefined);
    } else if (!isPlaying && !element.paused) {
      element.pause();
    }
  }, [isPlaying, sourceMs, src]);
  if (sourceMs === null) {
    return <span className="layout-layer__placeholder">{label} · finished (plays once)</span>;
  }
  return (
    <video
      ref={videoRef}
      aria-label={`${label} frame at ${formatTimecode(sourceMs)}`}
      draggable={false}
      muted
      playsInline
      poster={poster}
      preload="auto"
      src={src}
      style={style}
      onLoadedMetadata={(event) => {
        event.currentTarget.currentTime = sourceMs / 1000;
      }}
    />
  );
}

type CompositionPreset = Composition["space_kind"] | "stage_lights";

type LayoutDrag =
  | { kind: "move"; startPointer: [number, number]; keyframe: TransformKeyframe }
  | { kind: "scale"; corner: [number, number]; keyframe: TransformKeyframe }
  | { kind: "rotate"; startAngle: number; keyframe: TransformKeyframe };

interface LayoutReference {
  photo: { href: string; width: number; height: number } | null;
  outlines: { key: string; label: string; points: StagePoint[]; included: boolean }[];
  boundary: StagePoint[];
  leds: { key: string; x: number; y: number; included: boolean; title: string }[];
  summary: string;
}

/** Physical installation elements expressed in the composition's own coordinates. */
function useLayoutReference(
  composition: Composition,
  mapperExport: InstallationMapperExport | null | undefined,
  installationId: string | null | undefined,
): LayoutReference | null {
  const [photos, setPhotos] = useState<InstallationPhoto[]>([]);
  const photoId =
    composition.space_kind === "stage" ? mapperExport?.stage?.reference_photo_id ?? null : null;
  useEffect(() => {
    if (!photoId || !installationId) return;
    let active = true;
    getInstallationPhotos(installationId)
      .then((loaded) => {
        if (active) setPhotos(loaded);
      })
      .catch(() => {
        if (active) setPhotos([]);
      });
    return () => {
      active = false;
    };
  }, [installationId, photoId]);
  return useMemo(() => {
    if (!mapperExport) return null;
    if (composition.space_kind === "lighting_surface") {
      const surface = mapperExport.lighting_surfaces.find(
        (candidate) =>
          candidate.semantic_id === composition.target_id ||
          candidate.resource === composition.target_id,
      );
      if (!surface) return null;
      const scaleX = composition.width / (surface.local_width || composition.width);
      const scaleY = composition.height / (surface.local_height || composition.height);
      const label = lightingSurfaceLabel(surface);
      return {
        photo: null,
        outlines: [],
        boundary: surface.local_points.map(([x, y]) => [x * scaleX, y * scaleY] as StagePoint),
        leds: (surface.pixels ?? []).map((pixel) => ({
          key: `${surface.semantic_id}:${pixel.output_index}`,
          x: pixel.local_x * scaleX,
          y: pixel.local_y * scaleY,
          included: true,
          title: `${label} · LED ${pixel.output_index}`,
        })),
        summary: `${(surface.pixels ?? []).length} LEDs on ${label}`,
      };
    }
    if (composition.space_kind !== "stage") return null;
    const stage = mapperExport.stage ?? null;
    const photo = stage?.reference_photo_id
      ? photos.find((candidate) => candidate.id === stage.reference_photo_id)
      : undefined;
    const selectedProjectors = composition.stage_projector_resources;
    const outlines = mapperExport.projectors.flatMap((value) => {
      const projector = metadataRecord(value);
      if (typeof projector.resource !== "string") return [];
      const points = projectorStageFootprint(projector);
      if (points.length < 3) return [];
      return [{
        key: projector.resource,
        label: projector.resource,
        points,
        included: !selectedProjectors || selectedProjectors.includes(projector.resource),
      }];
    });
    const leds = mapperExport.lighting_surfaces.flatMap((surface) => {
      const included = composition.stage_lighting_surfaces.includes(surface.semantic_id);
      const label = lightingSurfaceLabel(surface);
      return (surface.pixels ?? []).map((pixel) => ({
        key: `${surface.semantic_id}:${pixel.output_index}`,
        x: pixel.stage_x,
        y: pixel.stage_y,
        included,
        title: `${label} · LED ${pixel.output_index}${included ? "" : " (not in this effect)"}`,
      }));
    });
    const includedLeds = leds.filter((led) => led.included).length;
    const includedProjectors = outlines.filter((outline) => outline.included).length;
    return {
      photo:
        photo && stage
          ? {
              href: installationPhotoUrl(photo.id, photo.revision),
              width: stage.width,
              height: stage.height,
            }
          : null,
      outlines,
      boundary: [],
      leds,
      summary: `${includedLeds} LEDs and ${includedProjectors} projector${includedProjectors === 1 ? "" : "s"} in this effect`,
    };
  }, [composition, mapperExport, photos]);
}

/** Composition-space layout with move / scale / rotate handles for the selected layer. */
function CompositionLayoutCanvas({
  composition,
  compositions,
  layers,
  assets,
  playheadMs,
  isPlaying = false,
  selectedLayerId,
  selectedKeyframeId,
  canEdit,
  installationId,
  mapperExport,
  onSelectLayer,
  onChangeKeyframe,
  onGestureStart,
  onGestureEnd,
}: {
  composition: Composition;
  compositions: Record<string, Composition>;
  layers: TransformLayer[];
  assets: AssetRecord[];
  playheadMs: number;
  isPlaying?: boolean;
  selectedLayerId: string;
  selectedKeyframeId: string;
  canEdit: boolean;
  installationId?: string | null;
  mapperExport?: InstallationMapperExport | null;
  onSelectLayer: (layerId: string) => void;
  onChangeKeyframe: (layerId: string, keyframeId: string, patch: KeyframePatch) => void;
} & GestureHandlers) {
  const frameRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<LayoutDrag | null>(null);
  const [showHouse, setShowHouse] = useState(true);
  const reference = useLayoutReference(composition, mapperExport, installationId);
  const houseShown = showHouse && reference !== null;
  const dotRadius = Math.max(composition.width, composition.height) * 0.006;
  const upp = layerUnitsPerSourcePixel(composition);
  const visible = layers
    .filter(
      (layer) =>
        layer.kind !== "audio" &&
        (layer.id === selectedLayerId ||
          (playheadMs >= layer.start_ms && playheadMs < layer.start_ms + layer.duration_ms)),
    )
    .sort((left, right) => left.z_index - right.z_index || left.id.localeCompare(right.id));

  const toLogical = (clientX: number, clientY: number): [number, number] => {
    const bounds = frameRef.current?.getBoundingClientRect();
    if (!bounds || bounds.width <= 0) return [0, 0];
    return [
      ((clientX - bounds.left) / bounds.width) * composition.width,
      ((clientY - bounds.top) / bounds.height) * composition.height,
    ];
  };

  const begin = (event: ReactPointerEvent<HTMLElement>, drag: LayoutDrag) => {
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = drag;
    onGestureStart?.();
  };

  const finish = () => {
    if (dragRef.current) onGestureEnd?.();
    dragRef.current = null;
  };

  const handleMove = (layer: TransformLayer, keyframeId: string, event: ReactPointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const pointer = toLogical(event.clientX, event.clientY);
    const start = drag.keyframe;
    if (drag.kind === "move") {
      onChangeKeyframe(layer.id, keyframeId, {
        position: [
          roundTo(start.position[0] + pointer[0] - drag.startPointer[0], 3),
          roundTo(start.position[1] + pointer[1] - drag.startPointer[1], 3),
        ],
      });
      return;
    }
    const dx = pointer[0] - start.position[0];
    const dy = pointer[1] - start.position[1];
    if (drag.kind === "rotate") {
      const angle = (Math.atan2(dy, dx) * 180) / Math.PI;
      let rotation = start.rotation_deg + angle - drag.startAngle;
      if (event.shiftKey) rotation = Math.round(rotation / 15) * 15;
      onChangeKeyframe(layer.id, keyframeId, { rotation_deg: roundTo(rotation, 1) });
      return;
    }
    const [width, height] = layerSourceSize(layer, assets, composition, compositions);
    const radians = (-start.rotation_deg * Math.PI) / 180;
    const localX = dx * Math.cos(radians) - dy * Math.sin(radians);
    const localY = dx * Math.sin(radians) + dy * Math.cos(radians);
    const baseX = (drag.corner[0] - start.anchor[0]) * width * upp;
    const baseY = (drag.corner[1] - start.anchor[1]) * height * upp;
    let scaleX = Math.abs(baseX) > 1e-9 ? localX / baseX : start.scale[0];
    let scaleY = Math.abs(baseY) > 1e-9 ? localY / baseY : start.scale[1];
    if (!event.shiftKey) {
      const vectorX = baseX * start.scale[0];
      const vectorY = baseY * start.scale[1];
      const length = vectorX * vectorX + vectorY * vectorY;
      const ratio = length > 1e-9 ? (localX * vectorX + localY * vectorY) / length : 1;
      scaleX = start.scale[0] * ratio;
      scaleY = start.scale[1] * ratio;
    }
    const guard = (value: number, fallback: number) =>
      Math.abs(value) < 0.01 ? Math.sign(fallback || 1) * 0.01 : value;
    onChangeKeyframe(layer.id, keyframeId, {
      scale: [roundTo(guard(scaleX, start.scale[0]), 4), roundTo(guard(scaleY, start.scale[1]), 4)],
    });
  };

  return (
    <div className="layout-canvas" aria-label="Composition layout">
      <div className="layout-canvas__stage">
      <div
        ref={frameRef}
        className="layout-canvas__frame"
        style={{ aspectRatio: `${composition.width} / ${composition.height}` }}
      >
        {houseShown && (
          <svg
            aria-label="Installation reference"
            className="layout-canvas__reference"
            role="img"
            viewBox={`0 0 ${composition.width} ${composition.height}`}
            preserveAspectRatio="none"
          >
            {reference.photo && (
              <image
                className="layout-canvas__photo"
                href={reference.photo.href}
                width={reference.photo.width}
                height={reference.photo.height}
                preserveAspectRatio="xMidYMid meet"
              />
            )}
            {reference.outlines.map((outline) => (
              <polygon
                key={outline.key}
                className={`layout-canvas__projector${outline.included ? "" : " layout-canvas__projector--excluded"}`}
                points={outline.points.map((point) => point.join(",")).join(" ")}
              >
                <title>{outline.label}{outline.included ? "" : " (not in this effect)"}</title>
              </polygon>
            ))}
            {reference.boundary.length >= 2 && (
              <polyline
                className="layout-canvas__surface-boundary"
                points={[...reference.boundary, ...(reference.boundary.length >= 3 ? [reference.boundary[0]] : [])]
                  .map((point) => point.join(","))
                  .join(" ")}
              />
            )}
          </svg>
        )}
        {visible.map((layer) => {
          const selected = layer.id === selectedLayerId;
          const keyframe =
            (selected && layer.keyframes.find((candidate) => candidate.id === selectedKeyframeId)) ||
            keyframeAtTime(layer, playheadMs - layer.start_ms);
          if (!keyframe) return null;
          if (layer.corner_pin) {
            return (
              <svg
                key={layer.id}
                className="layout-canvas__pin"
                viewBox={`0 0 ${composition.width} ${composition.height}`}
                preserveAspectRatio="none"
                aria-label={`${layer.name} is corner-pinned`}
              >
                <polygon points={layer.corner_pin.map((point) => point.join(",")).join(" ")} />
              </svg>
            );
          }
          const [width, height] = layerSourceSize(layer, assets, composition, compositions);
          const boxWidth = width * Math.abs(keyframe.scale[0]) * upp;
          const boxHeight = height * Math.abs(keyframe.scale[1]) * upp;
          const asset = assets.find((candidate) => candidate.id === layer.source_id);
          const video =
            asset && layer.kind === "video" && asset.media_type?.startsWith("video/") &&
            hasAssetArtifact(asset, "review")
              ? {
                  src: assetArtifactUrl(asset.id, "review", asset.current_revision),
                  poster: hasAssetArtifact(asset, "thumbnail")
                    ? assetArtifactUrl(asset.id, "thumbnail", asset.current_revision)
                    : undefined,
                  sourceMs: layerSourceMs(
                    layer,
                    playheadMs,
                    numberValue(asset.metadata?.duration_ms, 0),
                  ),
                }
              : null;
          const image =
            asset && asset.media_type?.startsWith("image/") && hasAssetArtifact(asset, "review")
              ? assetArtifactUrl(asset.id, "review", asset.current_revision)
              : asset && hasAssetArtifact(asset, "thumbnail")
                ? assetArtifactUrl(asset.id, "thumbnail", asset.current_revision)
                : null;
          const [left, top, right, bottom] = keyframe.crop;
          const cropStyle = { clipPath: `inset(${top * 100}% ${right * 100}% ${bottom * 100}% ${left * 100}%)` };
          return (
            <div
              key={layer.id}
              aria-label={`Layout box ${layer.name}`}
              className={`layout-layer${selected ? " layout-layer--selected" : ""}`}
              style={{
                left: `${((keyframe.position[0] - keyframe.anchor[0] * boxWidth) / composition.width) * 100}%`,
                top: `${((keyframe.position[1] - keyframe.anchor[1] * boxHeight) / composition.height) * 100}%`,
                width: `${(boxWidth / composition.width) * 100}%`,
                height: `${(boxHeight / composition.height) * 100}%`,
                transformOrigin: `${keyframe.anchor[0] * 100}% ${keyframe.anchor[1] * 100}%`,
                transform: `rotate(${keyframe.rotation_deg}deg) scale(${Math.sign(keyframe.scale[0]) || 1}, ${Math.sign(keyframe.scale[1]) || 1})`,
                opacity: Math.max(0.15, keyframe.opacity) * (houseShown ? 0.8 : 1),
              }}
              onPointerDown={(event) => {
                onSelectLayer(layer.id);
                if (!canEdit || event.button !== 0) return;
                begin(event, { kind: "move", startPointer: toLogical(event.clientX, event.clientY), keyframe });
              }}
              onPointerMove={(event) => handleMove(layer, keyframe.id, event)}
              onPointerUp={finish}
              onPointerCancel={finish}
            >
              {video ? (
                <LayoutVideoFrame
                  label={layer.name}
                  poster={video.poster}
                  sourceMs={video.sourceMs}
                  isPlaying={isPlaying}
                  src={video.src}
                  style={cropStyle}
                />
              ) : image ? (
                <img
                  alt=""
                  draggable={false}
                  src={image}
                  style={cropStyle}
                />
              ) : (
                <span className="layout-layer__placeholder">{layer.name}</span>
              )}
              {selected && canEdit && (
                <>
                  {([
                    [0, 0, "top left"],
                    [1, 0, "top right"],
                    [1, 1, "bottom right"],
                    [0, 1, "bottom left"],
                  ] as const).map(([cornerX, cornerY, name]) => (
                    <span
                      key={name}
                      aria-label={`Scale from ${name} corner`}
                      className="layout-layer__handle"
                      role="presentation"
                      style={{ left: `${cornerX * 100}%`, top: `${cornerY * 100}%` }}
                      onPointerDown={(event) => begin(event, { kind: "scale", corner: [cornerX, cornerY], keyframe })}
                      onPointerMove={(event) => handleMove(layer, keyframe.id, event)}
                      onPointerUp={finish}
                      onPointerCancel={finish}
                    />
                  ))}
                  <span
                    aria-label="Rotate layer"
                    className="layout-layer__rotate"
                    role="presentation"
                    onPointerDown={(event) => {
                      const pointer = toLogical(event.clientX, event.clientY);
                      begin(event, {
                        kind: "rotate",
                        startAngle:
                          (Math.atan2(pointer[1] - keyframe.position[1], pointer[0] - keyframe.position[0]) * 180) /
                          Math.PI,
                        keyframe,
                      });
                    }}
                    onPointerMove={(event) => handleMove(layer, keyframe.id, event)}
                    onPointerUp={finish}
                    onPointerCancel={finish}
                  />
                  <span
                    className="layout-layer__anchor"
                    aria-hidden="true"
                    style={{ left: `${keyframe.anchor[0] * 100}%`, top: `${keyframe.anchor[1] * 100}%` }}
                  />
                </>
              )}
            </div>
          );
        })}
        {houseShown && reference.leds.length > 0 && (
          <svg
            aria-label="Mapped LED positions"
            className="layout-canvas__leds"
            role="img"
            viewBox={`0 0 ${composition.width} ${composition.height}`}
            preserveAspectRatio="none"
          >
            {reference.leds.map((led) => (
              <circle
                key={led.key}
                className={`layout-canvas__led${led.included ? "" : " layout-canvas__led--excluded"}`}
                cx={led.x}
                cy={led.y}
                r={dotRadius}
              >
                <title>{led.title}</title>
              </circle>
            ))}
          </svg>
        )}
        {!visible.length && (
          <span className="layout-canvas__empty">No visual layer at the playhead</span>
        )}
      </div>
      </div>
      {reference && (
        <div className="layout-canvas__toolbar">
          <label>
            <input
              type="checkbox"
              checked={showHouse}
              onChange={(event) => setShowHouse(event.target.checked)}
            />
            Show house and LEDs
          </label>
          {showHouse && <small>{reference.summary}</small>}
        </div>
      )}
      <small>
        {canEdit
          ? "Drag a layer to move it, a corner to resize (Shift: free aspect), or the top knob to rotate (Shift: 15° steps). Edits the selected keyframe."
          : "Layout of layers at the playhead."}
        {houseShown ? " White dots mark mapped LEDs; each LED takes the color beneath it." : ""}
      </small>
    </div>
  );
}

/** Approximate whole-scene monitor: the topmost visual layer (or scene media output) at the playhead. */
function SceneProgramMonitor({
  layers,
  outputs,
  assets,
  playheadMs,
  isPlaying,
}: {
  layers: TransformLayer[];
  outputs: SceneMediaOutputRef[];
  assets: AssetRecord[];
  playheadMs: number;
  isPlaying: boolean;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const isVisualAsset = (asset: AssetRecord | undefined) =>
    Boolean(asset && (asset.media_type?.startsWith("video/") || asset.media_type?.startsWith("image/")));
  const visual = layers.filter((layer) => layer.kind === "image" || layer.kind === "video");
  const active = visual
    .filter((layer) => playheadMs >= layer.start_ms && playheadMs < layer.start_ms + layer.duration_ms)
    .sort(
      (left, right) =>
        right.z_index - left.z_index || right.start_ms - left.start_ms || left.id.localeCompare(right.id),
    );
  const outputSources = outputs.flatMap((output) => {
    const asset = output.asset ? assets.find((candidate) => candidate.id === output.asset) : undefined;
    if (!asset || !isVisualAsset(asset)) return [];
    const sourceMs = mediaOutputSourceMs(
      playheadMs,
      output.startDelayMs,
      numberValue(asset.metadata?.duration_ms, 0),
      output.endBehavior,
      output.repeatCount,
    );
    return [{ output, asset, sourceMs }];
  });
  const activeOutputs = outputSources.filter((item) => item.sourceMs !== null);
  const top = active[0];
  const fallback = top ? undefined : activeOutputs[0];
  const asset = top ? assets.find((candidate) => candidate.id === top.source_id) : fallback?.asset;
  const name = top?.name ?? (fallback ? `${fallback.asset.name} (${fallback.output.resource ?? fallback.output.id})` : "");
  const reviewUrl = asset && hasAssetArtifact(asset, "review") ? assetArtifactUrl(asset.id, "review", asset.current_revision) : null;
  const isVideo = Boolean(asset && (asset.media_type?.startsWith("video/") || asset.media_type === "image/gif"));
  const sourceSeconds = top
    ? (top.in_point_ms + playheadMs - top.start_ms) / 1000
    : (fallback?.sourceMs ?? 0) / 1000;
  const showing = Boolean(top || fallback);
  const underneath = active.length + activeOutputs.length - (showing ? 1 : 0);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !isVideo) return;
    if (Math.abs(video.currentTime - sourceSeconds) > (isPlaying ? 0.35 : 0.04)) {
      video.currentTime = sourceSeconds;
    }
    if (isPlaying && video.paused) {
      void video.play().catch(() => undefined);
    } else if (!isPlaying && !video.paused) {
      video.pause();
    }
  }, [isPlaying, isVideo, sourceSeconds, reviewUrl]);

  if (!visual.length && !outputSources.length) {
    return (
      <div className="timeline-viewer__empty">
        <strong>Whole scene</strong>
        <p>
          Drag media from the Media bin onto a track or here to see it at the
          playhead. Open a composition tab to preview it on the house.
        </p>
      </div>
    );
  }
  return (
    <figure className="timeline-program-monitor" aria-label="Scene program monitor">
      <div className="timeline-program-monitor__frame">
        {!showing ? (
          <span className="timeline-program-monitor__idle">Nothing on screen at {formatTimecode(playheadMs)}</span>
        ) : !reviewUrl ? (
          <span className="timeline-program-monitor__idle">
            {name}: no review proxy available yet
          </span>
        ) : isVideo ? (
          <video
            key={reviewUrl}
            ref={videoRef}
            aria-label={`Program monitor showing ${name}`}
            muted
            playsInline
            poster={asset && hasAssetArtifact(asset, "thumbnail") ? assetArtifactUrl(asset.id, "thumbnail", asset.current_revision) : undefined}
            preload="auto"
            src={reviewUrl}
          />
        ) : (
          <img alt={`Program monitor showing ${name}`} src={reviewUrl} />
        )}
      </div>
      <figcaption>
        {showing
          ? `${name} · source ${formatTimecode(sourceSeconds * 1000)}${
              underneath > 0 ? ` · ${underneath} more source${underneath > 1 ? "s" : ""} underneath` : ""
            }`
          : "No visual media at the playhead"}
        <small>
          Approximate monitor: shows the top source only. Use House view to see
          media on its projector surfaces; composition previews and bakes show
          exact warp and transforms.
        </small>
      </figcaption>
    </figure>
  );
}

function CompositionPreviewInspection({
  bakedUrl,
  label,
  mode,
  sourceUrl,
}: {
  bakedUrl: string | null;
  label: string;
  mode: "source" | "baked" | "side_by_side" | "difference";
  sourceUrl: string;
}) {
  if (!bakedUrl || mode === "source") {
    return (
      <img
        alt={label}
        className="timeline-render-preview"
        src={sourceUrl}
      />
    );
  }
  if (mode === "baked") {
    return (
      <img
        alt={`${label} · selected bake`}
        className="timeline-render-preview"
        src={bakedUrl}
      />
    );
  }
  return (
    <figure
      aria-label={`${label} · ${mode === "difference" ? "frame difference" : "before and after"}`}
      className={`timeline-preview-comparison timeline-preview-comparison--${mode}`}
    >
      <div>
        <img alt={`${label} · editable source`} src={sourceUrl} />
        <small>Editable source</small>
      </div>
      <div>
        <img alt={`${label} · selected bake`} src={bakedUrl} />
        <small>Selected checksummed bake</small>
      </div>
    </figure>
  );
}

function EditorialAudioPreview({
  sources,
  playheadMs,
  isPlaying,
}: {
  sources: EditorialAudioSource[];
  playheadMs: number;
  isPlaying: boolean;
}) {
  const anySolo = sources.some((source) => source.solo);
  return (
    <section className="editorial-audio-preview" aria-label="Browser editorial audio">
      <div>
        <strong>Browser editorial audio</strong>
        <small>
          Proxy playback and meters are for editing only; they do not represent
          venue output or hardware synchronization.
        </small>
      </div>
      {sources.length ? (
        <div className="editorial-audio-preview__channels">
          {sources.map((source) => (
            <EditorialAudioChannel
              anySolo={anySolo}
              isPlaying={isPlaying}
              key={source.id}
              playheadMs={playheadMs}
              source={source}
            />
          ))}
        </div>
      ) : (
        <small>No playable managed audio proxy is bound to this editing space.</small>
      )}
    </section>
  );
}

function EditorialAudioChannel({
  source,
  playheadMs,
  isPlaying,
  anySolo,
}: {
  source: EditorialAudioSource;
  playheadMs: number;
  isPlaying: boolean;
  anySolo: boolean;
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const gainRef = useRef<GainNode | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const meterFrameRef = useRef<number | null>(null);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const active =
    playheadMs >= source.start_ms &&
    playheadMs < source.start_ms + source.duration_ms;
  const muted = source.mute || (anySolo && !source.solo);
  const progress = active
    ? Math.max(
        0,
        Math.min(1, (playheadMs - source.start_ms) / source.duration_ms),
      )
    : playheadMs < source.start_ms
      ? 0
      : 1;

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) {
      return;
    }
    let cancelled = false;
    const stopMeter = () => {
      if (meterFrameRef.current !== null) {
        cancelAnimationFrame(meterFrameRef.current);
        meterFrameRef.current = null;
      }
      setLevel(0);
    };
    const startMeter = (analyser: AnalyserNode) => {
      if (meterFrameRef.current !== null) {
        return;
      }
      const samples = new Uint8Array(analyser.fftSize);
      const measure = () => {
        analyser.getByteTimeDomainData(samples);
        const rms = Math.sqrt(
          samples.reduce((sum, sample) => {
            const normalized = (sample - 128) / 128;
            return sum + normalized * normalized;
          }, 0) / samples.length,
        );
        setLevel(Math.min(1, rms * 4));
        meterFrameRef.current = requestAnimationFrame(measure);
      };
      meterFrameRef.current = requestAnimationFrame(measure);
    };
    const synchronize = async () => {
      if (!active) {
        audio.pause();
        stopMeter();
        return;
      }
      const targetSeconds =
        (source.in_point_ms + playheadMs - source.start_ms) / 1000;
      if (Math.abs(audio.currentTime - targetSeconds) > 0.25) {
        audio.currentTime = targetSeconds;
      }
      if (!isPlaying) {
        if (!audio.paused) {
          audio.pause();
        }
        stopMeter();
        return;
      }
      if (typeof AudioContext === "undefined") {
        audio.pause();
        stopMeter();
        setError("This browser does not provide Web Audio preview support.");
        return;
      }
      if (!contextRef.current) {
        const context = new AudioContext();
        const media = context.createMediaElementSource(audio);
        const gain = context.createGain();
        const analyser = context.createAnalyser();
        analyser.fftSize = 256;
        media.connect(gain);
        gain.connect(analyser);
        analyser.connect(context.destination);
        contextRef.current = context;
        gainRef.current = gain;
        analyserRef.current = analyser;
      }
      const elapsed = playheadMs - source.start_ms;
      const remaining = source.duration_ms - elapsed;
      const fadeIn = source.fade_in_ms > 0 ? Math.min(1, elapsed / source.fade_in_ms) : 1;
      const fadeOut =
        source.fade_out_ms > 0 ? Math.min(1, remaining / source.fade_out_ms) : 1;
      gainRef.current!.gain.value =
        muted ? 0 : 10 ** (source.gain_db / 20) * Math.min(fadeIn, fadeOut);
      try {
        await contextRef.current.resume();
        await audio.play();
        if (!cancelled && analyserRef.current) {
          setError(null);
          startMeter(analyserRef.current);
        }
      } catch (playbackError) {
        if (!cancelled) {
          audio.pause();
          stopMeter();
          setError(
            playbackError instanceof Error
              ? playbackError.message
              : "Browser audio playback failed.",
          );
        }
      }
    };
    void synchronize();
    return () => {
      cancelled = true;
      if (!isPlaying || !active) {
        stopMeter();
      }
    };
  }, [active, anySolo, isPlaying, muted, playheadMs, source]);

  useEffect(
    () => () => {
      if (meterFrameRef.current !== null) {
        cancelAnimationFrame(meterFrameRef.current);
      }
      audioRef.current?.pause();
      void contextRef.current?.close();
    },
    [],
  );

  return (
    <article className="editorial-audio-channel">
      <audio
        aria-label={`Editorial audio proxy for ${source.name}`}
        preload="auto"
        ref={audioRef}
        src={assetArtifactUrl(source.asset.id, "review", source.asset.current_revision)}
      />
      <div>
        <strong>{source.name}</strong>
        <small>
          {muted ? "Muted" : `${source.gain_db.toFixed(1)} dB`}
          {source.solo ? " · Solo" : ""}
        </small>
      </div>
      <div className="editorial-audio-channel__waveform">
        {hasAssetArtifact(source.asset, "waveform") ? (
          <img
            alt={`Editorial waveform for ${source.asset.name}`}
            src={assetArtifactUrl(source.asset.id, "waveform", source.asset.current_revision)}
          />
        ) : (
          <small>Waveform unavailable</small>
        )}
        <span
          aria-label={`${source.name} waveform position`}
          className="editorial-audio-channel__position"
          style={{ left: `${progress * 100}%` }}
        />
      </div>
      <label>
        Level
        <meter
          aria-label={`${source.name} audio level`}
          low={0.2}
          max={1}
          optimum={0.65}
          high={0.85}
          value={muted ? 0 : level}
        />
      </label>
      {error && <small role="alert">{error}</small>}
    </article>
  );
}

function NumberVectorEditor({
  label,
  value,
  disabled,
  min,
  max,
  onChange,
}: {
  label: string;
  value: readonly number[];
  disabled: boolean;
  min?: number;
  max?: number;
  onChange: (value: number[]) => void;
}) {
  return (
    <fieldset className="timeline-vector-editor">
      <legend>{label}</legend>
      {value.map((item, index) => (
        <input
          aria-label={`${label} ${index + 1}`}
          disabled={disabled}
          key={index}
          max={max}
          min={min}
          step="any"
          type="number"
          value={item}
          onChange={(event) =>
            onChange(
              value.map((candidate, candidateIndex) =>
                candidateIndex === index
                  ? Number(event.target.value)
                  : candidate,
              ),
            )
          }
        />
      ))}
    </fieldset>
  );
}

function metadataRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function readSceneOperationalPolicy(
  scene: ProjectDraftRecord["draft"]["scenes"][number] | undefined,
): SceneOperationalPolicy {
  const value = metadataRecord(scene);
  const completion = metadataRecord(value.completion);
  const scheduling = metadataRecord(value.scheduling);
  const media = Array.isArray(scene?.media) ? scene.media : [];
  const lighting = Array.isArray(scene?.lighting)
    ? scene.lighting.map(metadataRecord)
    : [];
  return {
    duration_ms: numberValue(value.duration_ms, 1000),
    priority: numberValue(value.priority, 50),
    completion_policy: [
      "loop",
      "select_next",
      "transition_to",
      "hold",
      "stop",
    ].includes(String(completion.policy))
      ? (completion.policy as SceneOperationalPolicy["completion_policy"])
      : "hold",
    completion_target:
      typeof completion.target_scene_id === "string"
        ? completion.target_scene_id
        : "",
    request_policy: [
      "priority",
      "immediate",
      "at_safe_point",
      "after_completion",
      "ignore",
    ].includes(String(scheduling.request_policy))
      ? (scheduling.request_policy as SceneOperationalPolicy["request_policy"])
      : "priority",
    request_ttl_ms: numberValue(scheduling.request_ttl_ms, 30000),
    resources: scene?.resources?.join(", ") ?? "",
    mode: value.mode === "controller_timed" ? "controller_timed" : "media_synchronized",
    clock_source:
      typeof value.clock_source === "string" ? value.clock_source : "",
    media_required: Object.fromEntries(
      media.map((output) => [
        output.id,
        metadataRecord(output).required !== false,
      ]),
    ),
    lighting_required: Object.fromEntries(
      lighting.map((output) => [
        String(output.id ?? output.resource ?? ""),
        output.required !== false,
      ]),
    ),
    media_offsets: Object.fromEntries(
      media.map((output) => [
        output.id,
        numberValue(metadataRecord(output).start_delay_ms, 0),
      ]),
    ),
    media_assets: {},
    media_end_behaviors: {},
    media_repeat_counts: {},
    lighting_offsets: Object.fromEntries(
      lighting.map((output) => [
        String(output.id ?? output.resource ?? ""),
        numberValue(output.timing_offset_ms, 0),
      ]),
    ),
  };
}

function validateTimelineState({
  scene,
  policy,
  duration,
  safePoints,
  regions,
  markers,
  cues,
  tracks,
  layers,
  composition,
}: {
  scene: ProjectDraftRecord["draft"]["scenes"][number] | undefined;
  policy: SceneOperationalPolicy;
  duration: number;
  safePoints: number[];
  regions: { start_ms: number; end_ms: number }[];
  markers: SyncMarker[];
  cues: SyncCue[];
  tracks: TimelineTrack[];
  layers: TransformLayer[];
  composition: Composition | undefined;
}): TimelineDiagnostic[] {
  const diagnostics: TimelineDiagnostic[] = [];
  const add = (code: string, message: string, objectId: string) =>
    diagnostics.push({ code, message, object_id: objectId });
  if (
    composition &&
    (composition.render_settings.frame_rate_numerator <= 0 ||
      composition.render_settings.frame_rate_denominator <= 0 ||
      composition.render_settings.render_width <= 0 ||
      composition.render_settings.render_height <= 0)
  ) {
    add(
      "render_frame_rate_invalid",
      "Render frame-rate numerator and denominator must be positive.",
      composition.id,
    );
  }
  if (
    composition &&
    (composition.render_settings.output_profile.max_video_bitrate_kbps < 500 ||
      composition.render_settings.output_profile.loudness_target_lufs < -40 ||
      composition.render_settings.output_profile.loudness_target_lufs > -5 ||
      composition.render_settings.output_profile.keyframe_interval_frames <= 0)
  ) {
    add(
      "render_output_profile_invalid",
      "Output bitrate, loudness, or keyframe policy is outside the supported range.",
      composition.id,
    );
  }
  if (policy.priority < 0 || policy.priority > 100) {
    add("scene_priority_out_of_range", "Scene priority must be between 0 and 100.", scene?.id ?? "scene");
  }
  if (policy.request_ttl_ms <= 0) {
    add("request_ttl_invalid", "Request TTL must be greater than zero.", scene?.id ?? "scene");
  }
  if (
    policy.completion_policy === "transition_to" &&
    (!policy.completion_target || policy.completion_target === scene?.id)
  ) {
    add(
      "completion_target_invalid",
      "Transition completion requires a different target scene.",
      scene?.id ?? "scene",
    );
  }
  const claims = new Set(
    policy.resources
      .split(",")
      .map((resource) => resource.trim())
      .filter(Boolean),
  );
  const sceneRecord = metadataRecord(scene);
  const media = Array.isArray(scene?.media) ? scene.media : [];
  const lighting = Array.isArray(scene?.lighting)
    ? scene.lighting.map(metadataRecord)
    : [];
  for (const output of media) {
    const delay = policy.media_offsets[output.id] ?? 0;
    if (!Number.isInteger(delay) || delay < 0 || delay > 5000) {
      add("media_offset_invalid", "Media start delay must be an integer between 0 and 5000 ms.", output.id);
    }
  }
  if (!composition && scene) {
    if (!Number.isInteger(policy.duration_ms) || policy.duration_ms <= 0) {
      add("scene_duration_invalid", "Scene duration must be a positive whole number of milliseconds.", scene.id);
    } else {
      for (const output of lighting) {
        const outputId = String(output.id ?? output.resource ?? "lighting");
        const timeline = Array.isArray(output.timeline) ? output.timeline.map(metadataRecord) : [];
        if (timeline.some((cue) => numberValue(cue.start_ms, 0) + numberValue(cue.duration_ms, 0) > policy.duration_ms)) {
          add("lighting_cue_past_scene_end", `Lighting effects on ${outputId} run past the scene end.`, scene.id);
        }
      }
    }
  }
  for (const output of [...media.map(metadataRecord), ...lighting]) {
    const resource = output.resource;
    if (typeof resource === "string" && !claims.has(resource)) {
      add(
        "resource_claim_missing",
        `Resource claim ${resource} is required by an output.`,
        scene?.id ?? "scene",
      );
    }
  }
  if (scene?.schema_version === 2 && policy.mode === "media_synchronized") {
    const clock = media.find((output) => output.id === policy.clock_source);
    if (!clock) {
      add(
        "clock_source_unknown",
        "Clock source must reference a media output.",
        scene.id,
      );
    } else if (policy.media_required[clock.id] === false) {
      add(
        "clock_source_optional",
        "The clock-source media output must remain required.",
        clock.id,
      );
    }
  } else if (scene?.schema_version !== 2 && typeof sceneRecord.clock_source === "string") {
    add("clock_source_unsupported", "Schema-v1 scenes cannot define a clock source.", scene?.id ?? "scene");
  }
  if (
    safePoints.some((point) => point < 0 || point > duration) ||
    new Set(safePoints).size !== safePoints.length
  ) {
    add(
      "safe_points_invalid",
      "Safe points must be unique and inside the scene duration.",
      scene?.id ?? "scene",
    );
  }
  const orderedRegions = [...regions].sort(
    (left, right) => left.start_ms - right.start_ms,
  );
  if (
    orderedRegions.some(
      (region) =>
        region.start_ms < 0 ||
        region.end_ms <= region.start_ms ||
        region.end_ms > duration,
    ) ||
    orderedRegions.some(
      (region, index) =>
        index > 0 && region.start_ms < orderedRegions[index - 1].end_ms,
    )
  ) {
    add(
      "protected_intervals_invalid",
      "Protected intervals must be ordered, non-overlapping, and inside the duration.",
      scene?.id ?? "scene",
    );
  }
  const markerNames = new Set<string>();
  for (const marker of markers) {
    const normalized = marker.name.trim().toLocaleLowerCase();
    if (!normalized || markerNames.has(normalized)) {
      add(
        "marker_name_invalid",
        "Synchronization marker names must be nonempty and unique.",
        marker.id,
      );
    }
    markerNames.add(normalized);
    if (marker.time_ms < 0 || marker.time_ms > duration) {
      add("marker_time_invalid", "Marker time is outside the editing space.", marker.id);
    }
  }
  const trackIds = new Set(tracks.map((track) => track.id));
  for (const cue of cues) {
    const start = cueTime(cue, markers);
    const cueDuration = cue.lighting?.duration_ms ?? 0;
    if (!trackIds.has(cue.track_id)) {
      add("cue_track_unknown", "Cue track binding does not exist.", cue.id);
    }
    if (start < 0 || start + cueDuration > duration) {
      add("cue_interval_invalid", "Cue interval leaves the editing-space duration.", cue.id);
    }
    if (
      cue.lighting &&
      cue.lighting.fade_in_ms + cue.lighting.fade_out_ms >
        cue.lighting.duration_ms
    ) {
      add("cue_fades_invalid", "Lighting cue fades exceed its duration.", cue.id);
    }
    if (cue.lighting?.target && !cue.lighting.target.id.trim()) {
      add("cue_target_invalid", "Lighting target ID cannot be empty.", cue.id);
    }
    if (
      cue.lighting?.primitive === "color_transition" &&
      !cue.lighting.end_color
    ) {
      add("cue_end_color_missing", "Color transition requires an end color.", cue.id);
    }
  }
  const audioMixOrders = new Set<number>();
  for (const layer of layers) {
    if (!trackIds.has(layer.track_id)) {
      add("transform_track_unknown", "Media layer track does not exist.", layer.id);
    }
    if (
      layer.start_ms < 0 ||
      layer.duration_ms <= 0 ||
      layer.start_ms + layer.duration_ms > duration
    ) {
      add("transform_interval_invalid", "Media layer runs outside the scene or composition.", layer.id);
    }
    if (
      layer.in_point_ms < 0 ||
      layer.out_point_ms - layer.in_point_ms !== layer.duration_ms
    ) {
      add(
        "layer_source_trim_invalid",
        "Layer source in/out points must match its timeline duration.",
        layer.id,
      );
    }
    if (
      layer.fade_in_ms < 0 ||
      layer.fade_out_ms < 0 ||
      layer.fade_in_ms + layer.fade_out_ms > layer.duration_ms
    ) {
      add(
        "layer_fades_invalid",
        "Layer fades must fit inside its duration.",
        layer.id,
      );
    }
    if (layer.kind === "audio") {
      if (
        !layer.audio ||
        layer.audio.gain_db < -96 ||
        layer.audio.gain_db > 24 ||
        layer.audio.mix_order < 0
      ) {
        add("audio_mix_invalid", "Audio gain or mix order is invalid.", layer.id);
      } else if (audioMixOrders.has(layer.audio.mix_order)) {
        add(
          "audio_mix_order_duplicate",
          "Audio layer mix order must be unique.",
          layer.id,
        );
      } else {
        audioMixOrders.add(layer.audio.mix_order);
      }
    } else if (layer.audio) {
      add(
        "visual_layer_audio_invalid",
        "Visual layers cannot define audio mix properties.",
        layer.id,
      );
    }
    const times = layer.keyframes.map((keyframe) => keyframe.time_ms);
    if (new Set(times).size !== times.length) {
      add("keyframe_time_duplicate", "Transform keyframe times must be unique.", layer.id);
    }
    for (const keyframe of layer.keyframes) {
      if (keyframe.time_ms < 0 || keyframe.time_ms > layer.duration_ms) {
        add("keyframe_time_invalid", "Keyframe is outside its media layer.", keyframe.id);
      }
      if (keyframe.scale.some((value) => value === 0)) {
        add("keyframe_scale_zero", "Independent X/Y scale cannot be zero.", keyframe.id);
      }
      if (keyframe.opacity < 0 || keyframe.opacity > 1) {
        add("keyframe_opacity_invalid", "Keyframe opacity must be between 0 and 1.", keyframe.id);
      }
      if (
        keyframe.crop.some((value) => value < 0 || value > 1) ||
        keyframe.crop[0] + keyframe.crop[2] >= 1 ||
        keyframe.crop[1] + keyframe.crop[3] >= 1
      ) {
        add("keyframe_crop_invalid", "Crop bounds must be normalized and retain visible content.", keyframe.id);
      }
    }
  }
  return diagnostics;
}

function applySceneOperationalPolicy(
  scene: ProjectDraftRecord["draft"]["scenes"][number],
  policy: SceneOperationalPolicy,
): ProjectDraftRecord["draft"]["scenes"][number] {
  const completion =
    policy.completion_policy === "transition_to"
      ? {
          policy: policy.completion_policy,
          target_scene_id: policy.completion_target,
        }
      : { policy: policy.completion_policy };
  const updated = {
    ...scene,
    duration_ms: policy.duration_ms,
    priority: policy.priority,
    resources: policy.resources
      .split(",")
      .map((resource) => resource.trim())
      .filter(Boolean),
    completion,
    scheduling: {
      ...(scene.scheduling ?? {}),
      request_policy: policy.request_policy,
      request_ttl_ms: policy.request_ttl_ms,
    },
  };
  if (scene.schema_version !== 2 || !Array.isArray(scene.media)) {
    return Array.isArray(scene.media) ? updated : {
      ...updated,
      media: {
        ...scene.media,
        asset: policy.media_assets.media === undefined ? scene.media.asset : policy.media_assets.media,
      },
    };
  }
  return {
    ...updated,
    mode: policy.mode,
    clock_source: policy.clock_source,
    media: scene.media.map((output) => {
      const record = metadataRecord(output);
      return {
        ...output,
        asset: policy.media_assets[output.id] === undefined ? output.asset : policy.media_assets[output.id],
        ...(policy.media_end_behaviors[output.id] === undefined ? {} : {
          end_behavior: policy.media_end_behaviors[output.id],
        }),
        ...repeatCountUpdate(
          policy.media_repeat_counts[output.id],
          policy.media_end_behaviors[output.id] ?? record.end_behavior,
        ),
        required: policy.media_required[output.id] ?? record.required ?? true,
        start_delay_ms:
          policy.media_offsets[output.id] ??
          numberValue(record.start_delay_ms, 0),
      };
    }),
    lighting: Array.isArray(scene.lighting)
      ? scene.lighting.map((output) => {
          const record = metadataRecord(output);
          const outputId = String(record.id ?? record.resource ?? "");
          return {
            ...record,
            required:
              policy.lighting_required[outputId] ?? record.required ?? true,
            timing_offset_ms:
              policy.lighting_offsets[outputId] ??
              numberValue(record.timing_offset_ms, 0),
          };
        })
      : scene.lighting,
  };
}

function LightingSurfaceCompositionPreview({
  composition,
  installationId,
  mapperExport,
  projectId,
  timestampMs,
  onMoveMedia,
}: {
  composition: Composition;
  installationId: string | null;
  mapperExport: InstallationMapperExport;
  projectId: string;
  timestampMs: number;
  onMoveMedia?: (position: [number, number]) => void;
}) {
  const [preview, setPreview] = useState<LightingPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [photos, setPhotos] = useState<InstallationPhoto[]>([]);
  const [geometry, setGeometry] = useState<InstallationGeometry[]>([]);
  const [photoError, setPhotoError] = useState<string | null>(null);
  useEffect(() => {
    if (!installationId) return;
    let active = true;
    Promise.all([getInstallationPhotos(installationId), getInstallationGeometry(installationId)])
      .then(([photoResult, geometryResult]) => {
        if (active) {
          setPhotos(photoResult);
          setGeometry(geometryResult);
          setPhotoError(null);
        }
      })
      .catch((error: unknown) => {
        if (active) setPhotoError(error instanceof Error ? error.message : "Photo preview failed.");
      });
    return () => { active = false; };
  }, [installationId]);
  useEffect(() => {
    let active = true;
    void getLightingPreview(projectId, composition.id, timestampMs)
      .then((result) => {
        if (active) {
          setPreview(result);
          setPreviewError(null);
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setPreview(null);
          setPreviewError(
            error instanceof Error ? error.message : "Lighting preview failed.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, [composition.id, projectId, timestampMs]);
  const surface = mapperExport.lighting_surfaces.find(
    (candidate) =>
      candidate.semantic_id === composition.target_id ||
      candidate.resource === composition.target_id,
  );
  if (!surface) {
    return (
      <section className="stage-composition-preview">
        <strong>Lighting surface · {composition.name}</strong>
        <small role="alert">
          Target {composition.target_id} is not present in the mapper export.
        </small>
      </section>
    );
  }
  const width = surface.local_width || composition.width;
  const height = surface.local_height || composition.height;
  const samplesByIndex = new Map(
    (preview?.samples ?? []).map((sample) => [sample.output_index, sample.color]),
  );
  return (
    <section className="stage-composition-preview lighting-composition-preview">
      <strong>
        LED surface · {lightingSurfaceLabel(surface)}
      </strong>
      <small>
        Surface coordinates span {width} × {height} units; dots are the mapped LEDs on this
        surface only. Use a lights-only whole house overlay to cover several surfaces.
      </small>
      <svg
        aria-label="Lighting surface local composition canvas"
        role="img"
        viewBox="0 0 800 400"
        onPointerDown={(event) => {
          const matrix = event.currentTarget.getScreenCTM();
          if (!onMoveMedia || !matrix) return;
          const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
          if (point.x >= 0 && point.x <= 800 && point.y >= 0 && point.y <= 400) {
            onMoveMedia([point.x / 800 * width, point.y / 400 * height]);
          }
        }}
      >
        {preview?.canvas_png_base64 && (
          <image
            href={`data:image/png;base64,${preview.canvas_png_base64}`}
            width="800"
            height="400"
            preserveAspectRatio="none"
          />
        )}
        {surface.local_points.length >= 3 && (
          <polygon
            className="lighting-composition-boundary"
            points={surface.local_points
              .map(([x, y]) => `${(x / width) * 800},${(y / height) * 400}`)
              .join(" ")}
          />
        )}
        {(surface.pixels ?? []).map((pixel) => {
          const color = samplesByIndex.get(pixel.output_index);
          return (
            <g key={pixel.output_index}>
              <ellipse
                className="lighting-composition-footprint"
                cx={(pixel.local_x / width) * 800}
                cy={(pixel.local_y / height) * 400}
                rx={Math.max(3, (pixel.footprint_width / width) * 400)}
                ry={Math.max(3, (pixel.footprint_height / height) * 200)}
              />
              <circle
                className="lighting-composition-sample"
                cx={(pixel.local_x / width) * 800}
                cy={(pixel.local_y / height) * 400}
                r="5"
                style={
                  color
                    ? { fill: `rgb(${color[0]} ${color[1]} ${color[2]})` }
                    : undefined
                }
              >
                <title>
                  Logical sample ({pixel.local_x}, {pixel.local_y})
                  {color ? ` · RGB ${color.join(", ")}` : ""}
                </title>
              </circle>
            </g>
          );
        })}
      </svg>
      <small>
        Surface-local authoring · {surface.pixels?.length ?? 0} calibrated
        samples · physical indices remain installation diagnostics only
      </small>
      <small>
        Stage transform: {surface.stage_matrix.join(", ")}
      </small>
      {onMoveMedia && <small>Click the logical canvas to position selected media; numeric controls remain available.</small>}
      {preview && (
        <small aria-label="Lighting preview diagnostics">
          {preview.sampling} sampling · normalized channel load{" "}
          {preview.normalized_channel_load.toFixed(3)} · calibration clipping{" "}
          {preview.clipped_pixels} · mapping revision{" "}
          {preview.diagnostics.mapping_revision} · unmapped samples{" "}
          {preview.diagnostics.unmapped_pixels} · duplicate mappings{" "}
          {preview.diagnostics.duplicate_output_indices.length}
        </small>
      )}
      {preview && (
        <small aria-label="Lighting device limiting status">
          Device-reported limiting:{" "}
          {preview.diagnostics.device_reported_scale === null
            ? "not reported"
            : `${(
                preview.diagnostics.device_reported_scale * 100
              ).toFixed(1)}% delivered`}
          {" · "}
          Geometry calibration:{" "}
          {preview.diagnostics.mapping_stale ? "missing or stale" : "current"}
        </small>
      )}
      {preview && (
        <small aria-label="Lighting performance budget">
          Performance budget: {preview.performance_budget.profile} ·{" "}
          {preview.performance_budget.target_fps} fps target ·{" "}
          {preview.performance_budget.qualified
            ? "qualified for interactive evaluation"
            : `exceeded: ${preview.performance_budget.exceeded_dimensions.join(
                ", ",
              )}`}
        </small>
      )}
      {previewError && <small role="alert">{previewError}</small>}
      {photoError && <small role="alert">{photoError}</small>}
      <IntegratedPhotoPreview
        geometry={geometry}
        mapperExport={{ ...mapperExport, lighting_surfaces: [surface] }}
        photos={photos}
        previewBySurface={new Map(preview ? [[surface.semantic_id, preview]] : [])}
      />
    </section>
  );
}


type StagePoint = [number, number];

function stageOutputPoints(
  composition: Pick<Composition, "stage_projector_resources" | "stage_lighting_surfaces">,
  mapperExport: InstallationMapperExport | null,
): StagePoint[] {
  const points: StagePoint[] = [];
  for (const value of mapperExport?.projectors ?? []) {
    const projector = metadataRecord(value);
    if (
      typeof projector.resource !== "string" ||
      (composition.stage_projector_resources &&
        !composition.stage_projector_resources.includes(projector.resource))
    ) {
      continue;
    }
    points.push(...projectorStageFootprint(projector));
  }
  for (const surface of mapperExport?.lighting_surfaces ?? []) {
    if (!composition.stage_lighting_surfaces.includes(surface.semantic_id)) continue;
    for (const pixel of surface.pixels ?? []) points.push([pixel.stage_x, pixel.stage_y]);
  }
  return points;
}

function projectorStageFootprint(projector: Record<string, unknown>): StagePoint[] {
  const matrix = projector.stage_homography;
  if (Array.isArray(matrix) && matrix.length === 9 && matrix.every((item) => typeof item === "number")) {
    const h = matrix as number[];
    return ([[0, 0], [1, 0], [1, 1], [0, 1]] as StagePoint[]).map(([u, v]) => {
      const w = h[6] * u + h[7] * v + h[8];
      return [(h[0] * u + h[1] * v + h[2]) / w, (h[3] * u + h[4] * v + h[5]) / w];
    });
  }
  const extent = projector.stage_extent;
  if (Array.isArray(extent) && extent.length === 4 && extent.every((item) => typeof item === "number")) {
    const [x, y, width, height] = extent as number[];
    return [[x, y], [x + width, y], [x + width, y + height], [x, y + height]];
  }
  return [];
}

function convexHull(points: StagePoint[]): StagePoint[] {
  const sorted = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (sorted.length < 3) return sorted;
  const cross = (o: StagePoint, a: StagePoint, b: StagePoint) =>
    (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower: StagePoint[] = [];
  for (const point of sorted) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], point) <= 0) lower.pop();
    lower.push(point);
  }
  const upper: StagePoint[] = [];
  for (const point of [...sorted].reverse()) {
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], point) <= 0) upper.pop();
    upper.push(point);
  }
  return [...lower.slice(0, -1), ...upper.slice(0, -1)];
}

function isConvexQuad(points: StagePoint[]): boolean {
  if (points.length !== 4) return false;
  const crosses = points.map((point, index) => {
    const next = points[(index + 1) % 4];
    const after = points[(index + 2) % 4];
    return (next[0] - point[0]) * (after[1] - next[1]) - (next[1] - point[1]) * (after[0] - next[0]);
  });
  return crosses.every((value) => value > 0) || crosses.every((value) => value < 0);
}

function boundsOf(points: StagePoint[]): [number, number, number, number] | null {
  if (!points.length) return null;
  const xs = points.map((point) => point[0]);
  const ys = points.map((point) => point[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

function rectanglePoints([x0, y0, x1, y1]: [number, number, number, number]): StagePoint[] {
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
}

function ellipsePoints([x0, y0, x1, y1]: [number, number, number, number], segments = 48): StagePoint[] {
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, rx = (x1 - x0) / 2, ry = (y1 - y0) / 2;
  return Array.from({ length: segments }, (_, index) => {
    const angle = (index / segments) * Math.PI * 2;
    return [roundStage(cx + rx * Math.cos(angle)), roundStage(cy + ry * Math.sin(angle))] as StagePoint;
  });
}

function StageLayerShape({
  layer,
  composition,
  mapperExport,
  canEdit,
  onChange,
}: {
  layer: TransformLayer;
  composition: Composition;
  mapperExport: InstallationMapperExport | null;
  canEdit: boolean;
  onChange: (update: (layer: TransformLayer) => TransformLayer) => void;
}) {
  const outputs = stageOutputPoints(composition, mapperExport);
  const outputBounds = boundsOf(outputs);
  const compositionBounds: [number, number, number, number] = [0, 0, composition.width, composition.height];
  const clamp = ([x0, y0, x1, y1]: [number, number, number, number]): [number, number, number, number] => [
    Math.max(0, x0), Math.max(0, y0), Math.min(composition.width, x1), Math.min(composition.height, y1),
  ];
  const minimumSize = Math.max(composition.width, composition.height) * 0.05;
  const padded = (bounds: [number, number, number, number]): [number, number, number, number] => {
    const [x0, y0, x1, y1] = bounds;
    const padX = Math.max(0, (minimumSize - (x1 - x0)) / 2);
    const padY = Math.max(0, (minimumSize - (y1 - y0)) / 2);
    return [x0 - padX, y0 - padY, x1 + padX, y1 + padY];
  };
  const fitBounds = outputBounds ? clamp(padded(outputBounds)) : compositionBounds;
  const pinConvex = !layer.corner_pin || isConvexQuad(layer.corner_pin);
  const maskBounds = boundsOf(layer.corner_pin ?? []) ?? fitBounds;
  const maskKind = layer.mask ? "custom" : "none";
  return (
    <fieldset className="stage-layer-shape" aria-label="Shape on stage">
      <legend>Shape on stage</legend>
      <label>
        <input
          type="checkbox"
          aria-label="Stretch to four corners"
          disabled={!canEdit}
          checked={Boolean(layer.corner_pin)}
          onChange={(event) => {
            const enabled = event.target.checked;
            onChange((current) => {
              const { corner_pin: _pin, ...rest } = current;
              return enabled
                ? { ...rest, corner_pin: rectanglePoints(fitBounds).map(([x, y]) => [roundStage(x), roundStage(y)]) }
                : rest;
            });
          }}
        />
        Stretch to four corners
      </label>
      <small>
        {layer.corner_pin
          ? "Drag the corner handles on the stage preview. Position, scale and rotation keyframes are ignored; opacity and crop still animate."
          : "Uses the position, scale and rotation keyframes."}
      </small>
      {!pinConvex && (
        <small role="alert">
          The corners cross or fold over. Drag them back into top-left, top-right,
          bottom-right, bottom-left order before saving.
        </small>
      )}
      <button
        type="button"
        disabled={!canEdit || !outputBounds}
        title={outputBounds ? undefined : "Select projectors or LED outputs for this effect first."}
        onClick={() =>
          onChange((current) => ({
            ...current,
            corner_pin: rectanglePoints(fitBounds).map(([x, y]) => [roundStage(x), roundStage(y)]),
          }))
        }
      >
        Fit to selected outputs
      </button>
      <label>
        Shape mask
        <select
          aria-label="Layer shape mask"
          disabled={!canEdit}
          value={maskKind}
          onChange={(event) => {
            const kind = event.target.value;
            onChange((current) => {
              const { mask: _mask, ...rest } = current;
              const feather = current.mask?.feather ?? 0;
              const invert = current.mask?.invert ?? false;
              if (kind === "ellipse") return { ...rest, mask: { points: ellipsePoints(maskBounds), feather, invert } };
              if (kind === "rectangle") return { ...rest, mask: { points: rectanglePoints(maskBounds), feather, invert } };
              if (kind === "outputs") {
                const hull = convexHull(outputs).map(([x, y]) => [roundStage(x), roundStage(y)] as StagePoint);
                return hull.length >= 3 ? { ...rest, mask: { points: hull, feather, invert } } : rest;
              }
              return rest;
            });
          }}
        >
          <option value="none">No mask</option>
          {maskKind === "custom" && <option value="custom">Current shape ({layer.mask?.points.length} points)</option>}
          <option value="ellipse">Ellipse</option>
          <option value="rectangle">Rectangle</option>
          <option value="outputs" disabled={outputs.length < 3}>Outline of selected outputs</option>
        </select>
      </label>
      {layer.mask && (
        <>
          <label>
            Soft edge ({mapperExport?.stage?.unit ?? "stage units"})
            <input
              aria-label="Layer mask soft edge"
              type="number"
              min="0"
              step="any"
              disabled={!canEdit}
              value={layer.mask.feather}
              onChange={(event) => {
                const feather = Math.max(0, Number(event.target.value) || 0);
                onChange((current) => current.mask ? { ...current, mask: { ...current.mask, feather } } : current);
              }}
            />
          </label>
          <label>
            <input
              type="checkbox"
              aria-label="Invert layer mask"
              disabled={!canEdit}
              checked={layer.mask.invert}
              onChange={(event) => {
                const invert = event.target.checked;
                onChange((current) => current.mask ? { ...current, mask: { ...current.mask, invert } } : current);
              }}
            />
            Show outside the mask instead
          </label>
        </>
      )}
    </fieldset>
  );
}

function StageOutputSelector({
  composition,
  mapperExport,
  canEdit,
  onChange,
}: {
  composition: Composition;
  mapperExport: InstallationMapperExport | null;
  canEdit: boolean;
  onChange: (update: (current: Composition) => Composition) => void;
}) {
  const projectors = (mapperExport?.projectors ?? []).flatMap((value) => {
    const projector = metadataRecord(value);
    return typeof projector.resource === "string" ? [projector.resource] : [];
  });
  const selectedProjectors = composition.stage_projector_resources ?? projectors;
  const lightsOnly = composition.stage_projector_resources?.length === 0;
  const surfaces = mapperExport?.lighting_surfaces ?? [];
  const stage = mapperExport?.stage ?? null;
  const sizeMismatch =
    stage !== null &&
    (Math.abs(composition.width - stage.width) > 1e-3 ||
      Math.abs(composition.height - stage.height) > 1e-3);
  return (
    <fieldset className="stage-output-selector" aria-label="Outputs in this effect">
      <legend>Outputs in this effect</legend>
      <small>
        Every selected projector and LED output shows its own part of this one stage
        animation, in sync. Clear every projector for a lights-only effect. Save the
        timeline before baking.
      </small>
      {stage === null && (
        <small role="note">
          The installation has no stage view yet. Choose one under Installation › Stage map so
          outputs line up with the photo.
        </small>
      )}
      {sizeMismatch && stage && (
        <div className="stage-output-selector__mismatch" role="alert">
          <span>
            This composition is {roundStage(composition.width)} × {roundStage(composition.height)}{" "}
            but the installation stage is {roundStage(stage.width)} × {roundStage(stage.height)}{" "}
            {stage.unit}. LEDs outside the composition cannot be baked.
          </span>
          <button
            type="button"
            disabled={!canEdit}
            onClick={() =>
              onChange((current) => {
                const [renderWidth] = suggestedStageRenderResolution(
                  mapperExport,
                  current.stage_projector_resources?.length === 0,
                );
                return withStageSize(current, {
                  renderWidth,
                  width: roundStage(stage.width),
                  height: roundStage(stage.height),
                });
              })
            }
          >
            Match installation stage
          </button>
        </div>
      )}
      <div className="stage-output-selector__group" role="group" aria-label="Projectors in this effect">
        <strong>Projectors</strong>
        {projectors.length === 0 && <small>No projectors are mapped.</small>}
        {projectors.map((resource) => {
          const checked = selectedProjectors.includes(resource);
          return (
            <label key={resource}>
              <input
                type="checkbox"
                aria-label={`Include projector ${resource}`}
                disabled={
                  !canEdit ||
                  (checked &&
                    selectedProjectors.length === 1 &&
                    composition.stage_lighting_surfaces.length === 0)
                }
                title={
                  checked &&
                  selectedProjectors.length === 1 &&
                  composition.stage_lighting_surfaces.length === 0
                    ? "Select an LED output first to make this a lights-only effect."
                    : undefined
                }
                checked={checked}
                onChange={(event) => {
                  const include = event.target.checked;
                  onChange((current) => {
                    const base = current.stage_projector_resources ?? projectors;
                    const next = include
                      ? projectors.filter((item) => item === resource || base.includes(item))
                      : base.filter((item) => item !== resource);
                    const { stage_projector_resources: _omitted, ...rest } = current;
                    return next.length === projectors.length
                      ? rest
                      : { ...rest, stage_projector_resources: next };
                  });
                }}
              />
              {resource}
            </label>
          );
        })}
      </div>
      <div
        className="stage-output-selector__group"
        role="group"
        aria-label="Installation overlay LED outputs"
      >
        <strong>LED outputs</strong>
        {surfaces.length === 0 && <small>No LED outputs are mapped.</small>}
        {surfaces.length > 1 && (
          <button
            type="button"
            disabled={
              !canEdit || composition.stage_lighting_surfaces.length === surfaces.length
            }
            onClick={() =>
              onChange((current) => ({
                ...current,
                stage_lighting_surfaces: surfaces.map((surface) => surface.semantic_id),
              }))
            }
          >
            Include all LED outputs
          </button>
        )}
        {surfaces.map((surface) => {
          const label = lightingSurfaceLabel(surface);
          const checked = composition.stage_lighting_surfaces.includes(surface.semantic_id);
          const lastLightsOnly =
            checked && lightsOnly && composition.stage_lighting_surfaces.length === 1;
          return (
            <label key={surface.semantic_id}>
              <input
                type="checkbox"
                aria-label={`Include LED surface ${surface.semantic_id}`}
                disabled={!canEdit || lastLightsOnly}
                title={
                  lastLightsOnly
                    ? "A lights-only effect needs at least one LED output."
                    : undefined
                }
                checked={checked}
                onChange={(event) => {
                  const checked = event.target.checked;
                  onChange((current) => ({
                    ...current,
                    stage_lighting_surfaces: checked
                      ? [...current.stage_lighting_surfaces, surface.semantic_id]
                      : current.stage_lighting_surfaces.filter((id) => id !== surface.semantic_id),
                  }));
                }}
              />
              {label}
            </label>
          );
        })}
        {lightsOnly ? (
          <small role="status">
            Lights only: the bake produces LED sequences for the controller and no projector
            video. Audio layers are not used. Publish the show to send the sequences to the
            controller.
          </small>
        ) : composition.stage_lighting_surfaces.length > 0 && (
          <small role="status">
            Bake produces projector videos and LED sequences together. Publish the package to
            include controller LED playback; client-group activation alone does not install LED
            sequences. Rehearse mixed-device latency before live use.
          </small>
        )}
      </div>
    </fieldset>
  );
}

function StageCompositionPreview({
  composition,
  compositions,
  coverage,
  installationId,
  mapperExport,
  projectId,
  selectedLayer,
  selectedKeyframe,
  canEdit,
  snapMode,
  onSnapModeChange,
  onMoveKeyframe,
  onCornerPinChange,
  timestampMs,
}: {
  composition: Composition;
  compositions: Record<string, Composition>;
  coverage: StageCoverage;
  installationId: string;
  mapperExport: InstallationMapperExport;
  projectId: string;
  selectedLayer: TransformLayer | undefined;
  selectedKeyframe: TransformKeyframe | undefined;
  canEdit: boolean;
  snapMode: "none" | "landmarks" | "surfaces" | "all";
  onSnapModeChange: (mode: "none" | "landmarks" | "surfaces" | "all") => void;
  onMoveKeyframe: (position: [number, number]) => void;
  onCornerPinChange: (points: [number, number][]) => void;
  timestampMs: number;
}) {
  const [showRenderedFrame, setShowRenderedFrame] = useState(true);
  const [showCoverage, setShowCoverage] = useState(true);
  const [showLedNumbers, setShowLedNumbers] = useState(false);
  const [draggingCorner, setDraggingCorner] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [lightingPreviews, setLightingPreviews] = useState<
    Record<string, LightingPreview>
  >({});
  const [lightingPreviewError, setLightingPreviewError] = useState<string | null>(
    null,
  );
  const [stageLightingPreview, setStageLightingPreview] =
    useState<StageLightingPreview | null>(null);
  const [photos, setPhotos] = useState<InstallationPhoto[]>([]);
  const [photoGeometry, setPhotoGeometry] = useState<InstallationGeometry[]>([]);
  const lightingCompositions = useMemo(
    () =>
      Object.values(compositions).filter(
        (candidate) => candidate.space_kind === "lighting_surface",
      ),
    [compositions],
  );
  useEffect(() => {
    let active = true;
    Promise.allSettled(
      lightingCompositions.map(async (candidate) => [
        candidate.id,
        await getLightingPreview(
          projectId,
          candidate.id,
          Math.min(timestampMs, candidate.duration_ms - 1),
        ),
      ] as const),
    ).then((results) => {
      if (!active) {
        return;
      }
      const previews: Record<string, LightingPreview> = {};
      const errors: string[] = [];
      results.forEach((result, index) => {
        if (result.status === "fulfilled") {
          previews[result.value[0]] = result.value[1];
        } else {
          errors.push(
            `${lightingCompositions[index].name}: ${
              result.reason instanceof Error
                ? result.reason.message
                : "preview unavailable"
            }`,
          );
        }
      });
      setLightingPreviews(previews);
      setLightingPreviewError(errors.length ? errors.join("; ") : null);
    });
    return () => {
      active = false;
    };
  }, [lightingCompositions, projectId, timestampMs]);
  useEffect(() => {
    let active = true;
    getStageLightingPreview(projectId, composition.id, timestampMs, true)
      .then((preview) => {
        if (active) {
          setStageLightingPreview(preview);
        }
      })
      .catch((error) => {
        if (active) {
          setStageLightingPreview(null);
          setLightingPreviewError(
            error instanceof Error
              ? error.message
              : "Shared stage lighting sampling is unavailable.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, [composition.id, projectId, timestampMs]);
  useEffect(() => {
    let active = true;
    Promise.all([
      getInstallationPhotos(installationId),
      getInstallationGeometry(installationId),
    ])
      .then(([loadedPhotos, geometry]) => {
        if (active) {
          setPhotos(loadedPhotos);
          setPhotoGeometry(geometry);
        }
      })
      .catch((error) => {
        if (active) {
          setLightingPreviewError(
            error instanceof Error
              ? error.message
              : "Installation photo geometry is unavailable.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, [installationId]);

  const previewBySurface = new Map<
    string,
    LightingPreview | StageLightingPreview["surfaces"][number]
  >(
    Object.values(lightingPreviews).flatMap((preview) => [
      [preview.surface_semantic_id, preview] as const,
      [preview.surface_resource, preview] as const,
    ]),
  );
  for (const surface of stageLightingPreview?.surfaces ?? []) {
    previewBySurface.set(surface.surface_semantic_id, surface);
    previewBySurface.set(surface.surface_resource, surface);
  }
  const stage = mapperExport.stage ?? null;
  const minimumX = stage ? 0 : coverage.minimum_x;
  const minimumY = stage ? 0 : coverage.minimum_y;
  const width = stage ? stage.width : coverage.maximum_x - minimumX || composition.width;
  const height = stage ? stage.height : coverage.maximum_y - minimumY || composition.height;
  const screenWidth = 800;
  // Without a stage reference, keep the legacy stretched 2:1 frame.
  const screenHeight = stage
    ? Math.round(Math.min(1200, Math.max(160, (screenWidth * height) / width)))
    : 400;
  const screenPoint = ([x, y]: [number, number]): [number, number] => [
    ((x - minimumX) / width) * screenWidth,
    ((y - minimumY) / height) * screenHeight,
  ];
  const stagePointFromEvent = (clientX: number, clientY: number): [number, number] | null => {
    const svg = svgRef.current;
    const matrix = svg?.getScreenCTM();
    if (!svg || !matrix) return null;
    const local = new DOMPoint(clientX, clientY).matrixTransform(matrix.inverse());
    return [
      roundStage(minimumX + (local.x / screenWidth) * width),
      roundStage(minimumY + (local.y / screenHeight) * height),
    ];
  };
  const referencePhoto = stage?.reference_photo_id
    ? photos.find((candidate) => candidate.id === stage.reference_photo_id)
    : undefined;
  const compositionTopLeft = screenPoint([0, 0]);
  const compositionBottomRight = screenPoint([composition.width, composition.height]);
  const cornerPin = selectedLayer?.corner_pin ?? null;
  const layerMask = selectedLayer?.mask ?? null;
  const point = (value: [number, number]) => screenPoint(value).join(",");
  const keyframes = selectedLayer?.keyframes ?? [];
  const selectedIndex = selectedKeyframe
    ? keyframes.findIndex((keyframe) => keyframe.id === selectedKeyframe.id)
    : -1;
  const motionPath = transformMotionPath(keyframes, screenPoint);
  const warnings = selectedLayer
    ? motionCoverageWarnings(selectedLayer, coverage)
    : [];
  const landmarkCandidates = [
    ...mapperExport.fixtures.flatMap((fixture) =>
      fixture.stage_points.map((position) => ({
        position,
        label: `${fixture.name} landmark`,
      })),
    ),
    ...mapperExport.paths.flatMap((path) =>
      path.stage_points.map((position) => ({
        position,
        label: `${path.name} landmark`,
      })),
    ),
  ];
  const surfaceCandidates = coverage.regions.flatMap((region) =>
    region.points.map((position) => ({
      position,
      label:
        region.kind === "overlap"
          ? `${region.resources.join(" / ")} seam`
          : `${region.resources.join(" / ") || region.kind} surface edge`,
    })),
  );
  const candidates =
    snapMode === "landmarks"
      ? landmarkCandidates
      : snapMode === "surfaces"
        ? surfaceCandidates
        : snapMode === "all"
          ? [...landmarkCandidates, ...surfaceCandidates]
          : [];
  return (
    <section className="stage-composition-preview">
      <strong>
        Stage composition · {composition.name} · {composition.width}×
        {composition.height}
      </strong>
      <label>
        Spatial snapping
        <select
          aria-label="Motion path spatial snapping"
          value={snapMode}
          onChange={(event) =>
            onSnapModeChange(event.target.value as typeof snapMode)
          }
        >
          <option value="none">Off</option>
          <option value="landmarks">Named landmarks</option>
          <option value="surfaces">Surfaces and seams</option>
          <option value="all">Landmarks, surfaces, and seams</option>
        </select>
      </label>
      <div className="stage-composition-preview__toggles">
        <label>
          <input
            type="checkbox"
            checked={showRenderedFrame}
            onChange={(event) => setShowRenderedFrame(event.target.checked)}
          />
          Show rendered frame
        </label>
        <label>
          <input
            type="checkbox"
            checked={showCoverage}
            onChange={(event) => setShowCoverage(event.target.checked)}
          />
          Show projector coverage
        </label>
        <label>
          <input
            type="checkbox"
            checked={showLedNumbers}
            onChange={(event) => setShowLedNumbers(event.target.checked)}
          />
          Show LED numbers
        </label>
        {stage && (
          <small>
            Stage {roundStage(stage.width)} × {roundStage(stage.height)} {stage.unit}
            {referencePhoto ? ` · seen from ${referencePhoto.description ?? referencePhoto.name}` : ""}
          </small>
        )}
      </div>
      <svg
        ref={svgRef}
        aria-label="Stage composition overlay"
        role="img"
        viewBox={`0 0 ${screenWidth} ${screenHeight}`}
        onPointerMove={(event) => {
          if (draggingCorner === null || !cornerPin) return;
          const next = stagePointFromEvent(event.clientX, event.clientY);
          if (!next) return;
          onCornerPinChange(
            cornerPin.map((corner, index) => (index === draggingCorner ? next : corner)),
          );
        }}
        onPointerUp={() => setDraggingCorner(null)}
        onPointerCancel={() => setDraggingCorner(null)}
        onClick={(event) => {
          if (!canEdit || !selectedKeyframe || cornerPin) {
            return;
          }
          const bounds = event.currentTarget.getBoundingClientRect();
          const raw: [number, number] = stage
            ? stagePointFromEvent(event.clientX, event.clientY) ?? [minimumX, minimumY]
            : [
                minimumX + ((event.clientX - bounds.left) / bounds.width) * width,
                minimumY + ((event.clientY - bounds.top) / bounds.height) * height,
              ];
          const nearest = candidates
            .map((candidate) => ({
              ...candidate,
              distance: Math.hypot(
                screenPoint(candidate.position)[0] - screenPoint(raw)[0],
                screenPoint(candidate.position)[1] - screenPoint(raw)[1],
              ),
            }))
            .sort((left, right) => left.distance - right.distance)[0];
          onMoveKeyframe(
            nearest && nearest.distance <= 18 ? nearest.position : raw,
          );
        }}
      >
        {referencePhoto && (
          <image
            className="stage-composition-photo"
            href={installationPhotoUrl(referencePhoto.id, referencePhoto.revision)}
            width={screenWidth}
            height={screenHeight}
            preserveAspectRatio="xMidYMid meet"
          />
        )}
        {showRenderedFrame && stageLightingPreview?.canvas_png_base64 && (
          <image
            aria-label="Rendered stage frame"
            className="stage-composition-frame"
            href={`data:image/png;base64,${stageLightingPreview.canvas_png_base64}`}
            x={compositionTopLeft[0]}
            y={compositionTopLeft[1]}
            width={Math.max(1, compositionBottomRight[0] - compositionTopLeft[0])}
            height={Math.max(1, compositionBottomRight[1] - compositionTopLeft[1])}
            preserveAspectRatio="none"
          />
        )}
        {showCoverage &&
          coverage.regions.map((region, index) => (
            <polygon
              key={`${region.kind}:${index}`}
              className={`stage-region stage-region--${region.kind}`}
              points={region.points.map(point).join(" ")}
            />
          ))}
        <rect
          className="stage-composition-bounds"
          x={compositionTopLeft[0]}
          y={compositionTopLeft[1]}
          width={Math.max(1, compositionBottomRight[0] - compositionTopLeft[0])}
          height={Math.max(1, compositionBottomRight[1] - compositionTopLeft[1])}
        >
          <title>Composition area</title>
        </rect>
        {mapperExport.zones.map((zone) => (
          <polygon
            key={zone.semantic_id}
            className={`stage-composition-zone stage-composition-zone--${zone.kind}`}
            points={zone.stage_points.map(point).join(" ")}
          >
            <title>{zone.name}</title>
          </polygon>
        ))}
        {mapperExport.paths.map((path) => (
          <polyline
            className="stage-composition-lighting-path"
            key={path.semantic_id}
            points={path.stage_points.map(point).join(" ")}
          >
            <title>{path.name} lighting path</title>
          </polyline>
        ))}
        {mapperExport.fixtures.flatMap((fixture) =>
          fixture.stage_points.map((position, index) => (
            <rect
              className="stage-composition-fixture"
              height="10"
              key={`${fixture.semantic_id}:${index}`}
              width="10"
              x={screenPoint(position)[0] - 5}
              y={screenPoint(position)[1] - 5}
            >
              <title>{fixture.name} fixture</title>
            </rect>
          )),
        )}
        {mapperExport.lighting_surfaces.flatMap((surface) => {
          const preview =
            previewBySurface.get(surface.semantic_id) ??
            previewBySurface.get(surface.resource);
          const samples = new Map(
            (preview?.samples ?? []).map((sample) => [
              sample.output_index,
              sample.color,
            ]),
          );
          return (surface.pixels ?? []).map((pixel) => {
            const color = samples.get(pixel.output_index);
            const center = screenPoint([pixel.stage_x, pixel.stage_y]);
            return (
              <g key={`${surface.semantic_id}:${pixel.output_index}`}>
                <ellipse
                  className="stage-composition-pixel-footprint"
                  cx={center[0]}
                  cy={center[1]}
                  rx={Math.max(4, (pixel.footprint_width / width) * (screenWidth / 2))}
                  ry={Math.max(4, (pixel.footprint_height / height) * (screenHeight / 2))}
                />
                <circle
                  className="stage-composition-pixel"
                  cx={center[0]}
                  cy={center[1]}
                  r="4"
                  style={
                    color
                      ? { fill: `rgb(${color[0]} ${color[1]} ${color[2]})` }
                      : undefined
                  }
                >
                  <title>
                    {surface.resource} physical output {pixel.output_index}
                    {color ? ` · RGB ${color.join(", ")}` : ""}
                  </title>
                </circle>
                {showLedNumbers && (
                  <text
                    className="stage-composition-output-order"
                    x={center[0] + 6}
                    y={center[1] - 6}
                  >
                    {pixel.output_index}
                  </text>
                )}
              </g>
            );
          });
        })}
        {layerMask && (
          <polygon
            aria-label="Layer mask outline"
            className={`stage-layer-mask${layerMask.invert ? " stage-layer-mask--inverted" : ""}`}
            points={layerMask.points.map(point).join(" ")}
          />
        )}
        {cornerPin && (
          <g aria-label="Layer corner pin">
            <polygon className="stage-layer-pin" points={cornerPin.map(point).join(" ")} />
            {cornerPin.map((corner, index) => (
              <circle
                key={index}
                aria-label={`${["Top left", "Top right", "Bottom right", "Bottom left"][index]} corner`}
                className="stage-layer-pin-handle"
                cx={screenPoint(corner)[0]}
                cy={screenPoint(corner)[1]}
                r="9"
                onPointerDown={(event) => {
                  if (!canEdit) return;
                  event.stopPropagation();
                  svgRef.current?.setPointerCapture(event.pointerId);
                  setDraggingCorner(index);
                }}
              />
            ))}
          </g>
        )}
        {motionPath && !cornerPin && (
          <path className="stage-motion-path" d={motionPath}>
            <title>{selectedLayer?.name} motion path</title>
          </path>
        )}
        {selectedIndex > 0 && (
          <circle
            aria-label="Previous position ghost"
            className="stage-motion-ghost"
            cx={screenPoint(keyframes[selectedIndex - 1].position)[0]}
            cy={screenPoint(keyframes[selectedIndex - 1].position)[1]}
            r="8"
          />
        )}
        {selectedIndex >= 0 && selectedIndex < keyframes.length - 1 && (
          <circle
            aria-label="Next position ghost"
            className="stage-motion-ghost"
            cx={screenPoint(keyframes[selectedIndex + 1].position)[0]}
            cy={screenPoint(keyframes[selectedIndex + 1].position)[1]}
            r="8"
          />
        )}
        {selectedKeyframe && (
          <>
            {(
              [
                selectedKeyframe.interpolation.spatial_in,
                selectedKeyframe.interpolation.spatial_out,
              ].filter((handle): handle is [number, number] => handle !== null)
            ).map((handle, index) => {
              const origin = screenPoint(selectedKeyframe.position);
              const endpoint = screenPoint([
                selectedKeyframe.position[0] + handle[0],
                selectedKeyframe.position[1] + handle[1],
              ]);
              return (
                <g key={index}>
                  <line
                    className="stage-motion-handle"
                    x1={origin[0]}
                    x2={endpoint[0]}
                    y1={origin[1]}
                    y2={endpoint[1]}
                  />
                  <circle
                    aria-label={index === 0 ? "Spatial in handle" : "Spatial out handle"}
                    className="stage-motion-handle-point"
                    cx={endpoint[0]}
                    cy={endpoint[1]}
                    r="5"
                  />
                </g>
              );
            })}
            <circle
              aria-label="Selected motion position"
              className="stage-motion-selected"
              cx={screenPoint(selectedKeyframe.position)[0]}
              cy={screenPoint(selectedKeyframe.position)[1]}
              r="7"
            />
          </>
        )}
      </svg>
      <small>
        Projector coverage · evaluated lighting canvas samples and footprints ·
        LED numbers when shown · fixtures · paths · audience zones ·
        safety/no-output zones
      </small>
      {stageLightingPreview && (
        <small aria-label="Shared stage lighting sampling">
          Shared stage visual sampled onto calibrated LEDs at{" "}
          {stageLightingPreview.timestamp_ms} ms · mapped and device power limits
          applied · hardware commands sent: no
        </small>
      )}
      {lightingPreviewError && <small role="alert">{lightingPreviewError}</small>}
      {warnings.length > 0 && (
        <ul className="stage-motion-warnings" aria-label="Motion path warnings">
          {warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
      <IntegratedPhotoPreview
        geometry={photoGeometry}
        mapperExport={mapperExport}
        photos={photos}
        previewBySurface={previewBySurface}
      />
    </section>
  );
}

function IntegratedPhotoPreview({
  geometry,
  mapperExport,
  photos,
  previewBySurface,
}: {
  geometry: InstallationGeometry[];
  mapperExport: InstallationMapperExport;
  photos: InstallationPhoto[];
  previewBySurface: Map<
    string,
    LightingPreview | StageLightingPreview["surfaces"][number]
  >;
}) {
  if (!photos.length) {
    return (
      <small>
        No calibrated installation photo is available for the integrated preview.
      </small>
    );
  }
  return (
    <div
      className="timeline-integrated-photo-grid"
      aria-label="Integrated projector and lighting photo previews"
    >
      {photos.map((photo) => {
        const photoGeometry = geometry.filter(
          (candidate) => candidate.photo_id === photo.id && !candidate.hidden,
        );
        return (
          <figure key={photo.id}>
            <strong>{photo.name}</strong>
            <svg
              aria-label={`Integrated simulation on ${photo.name}`}
              role="img"
              viewBox="0 0 1000 600"
            >
              <image
                height="600"
                href={installationPhotoUrl(photo.id, photo.revision)}
                preserveAspectRatio="xMidYMid meet"
                width="1000"
              />
              {mapperExport.lighting_surfaces.map((surface) => {
                const preview = previewBySurface.get(surface.semantic_id);
                const boundary = photoGeometry.find((item) =>
                  item.semantic_id === surface.semantic_id && item.points.length >= 4,
                );
                return preview && "canvas_png_base64" in preview && preview.canvas_png_base64 && boundary
                  ? <SurfaceMediaOverlay key={surface.semantic_id} png={preview.canvas_png_base64} corners={boundary.points} />
                  : null;
              })}
              {photoGeometry
                .filter((item) => item.kind === "projector_surface")
                .map((item) => (
                  <image
                    className="projector-simulation-overlay"
                    height="600"
                    href={projectorSimulationOverlayUrl(item.id, photo.id)}
                    key={item.id}
                    preserveAspectRatio="none"
                    width="1000"
                  >
                    <title>Projector review proxy on {item.name}</title>
                  </image>
                ))}
              {photoGeometry
                .filter((item) => item.kind === "path" && item.points.length >= 2)
                .map((item) => (
                  <polyline
                    className="stage-composition-lighting-path"
                    key={item.id}
                    points={item.points
                      .map((point) => `${point.x * 1000},${point.y * 600}`)
                      .join(" ")}
                  >
                    <title>{item.name} lighting path</title>
                  </polyline>
                ))}
              {photoGeometry
                .filter((item) => item.kind === "fixture")
                .flatMap((item) =>
                  item.points.map((position, index) => (
                    <rect
                      className="stage-composition-fixture"
                      height="12"
                      key={`${item.id}:${index}`}
                      width="12"
                      x={position.x * 1000 - 6}
                      y={position.y * 600 - 6}
                    >
                      <title>{item.name} fixture</title>
                    </rect>
                  )),
                )}
              {mapperExport.lighting_surfaces.flatMap((surface) => {
                const boundary = photoGeometry.find(
                  (item) =>
                    item.semantic_id === surface.semantic_id &&
                    item.points.length >= 3,
                );
                if (!boundary) {
                  return [];
                }
                const preview =
                  previewBySurface.get(surface.semantic_id) ??
                  previewBySurface.get(surface.resource);
                const samples = new Map(
                  (preview?.samples ?? []).map((sample) => [
                    sample.output_index,
                    sample.color,
                  ]),
                );
                return (surface.pixels ?? []).map((pixel) => {
                  const [x, y] = lightingPixelPhotoPoint(
                    pixel.local_x / surface.local_width,
                    pixel.local_y / surface.local_height,
                    boundary.points,
                  );
                  const color = samples.get(pixel.output_index);
                  return (
                    <g key={`${surface.semantic_id}:${pixel.output_index}`}>
                      <ellipse
                        className="stage-composition-pixel-footprint"
                        cx={x * 1000}
                        cy={y * 600}
                        rx={Math.max(
                          4,
                          (pixel.footprint_width / surface.local_width) * 40,
                        )}
                        ry={Math.max(
                          4,
                          (pixel.footprint_height / surface.local_height) * 24,
                        )}
                      />
                      <circle
                        className="stage-composition-pixel"
                        cx={x * 1000}
                        cy={y * 600}
                        r="5"
                        style={
                          color
                            ? {
                                fill: `rgb(${color[0]} ${color[1]} ${color[2]})`,
                              }
                            : undefined
                        }
                      >
                        <title>
                          {surface.resource} physical output {pixel.output_index}
                          {color ? ` · RGB ${color.join(", ")}` : ""}
                        </title>
                      </circle>
                    </g>
                  );
                });
              })}
            </svg>
            <small>
              Approximate visual composite on calibrated photo geometry; sampled
              colors include controller power limiting, but are not measured light
              output. Hardware commands sent: no.
            </small>
          </figure>
        );
      })}
    </div>
  );
}

function lightingPixelPhotoPoint(
  u: number,
  v: number,
  points: { x: number; y: number }[],
): [number, number] {
  if (points.length === 4) {
    const [topLeft, topRight, bottomRight, bottomLeft] = points;
    return [
      (1 - u) * (1 - v) * topLeft.x +
        u * (1 - v) * topRight.x +
        u * v * bottomRight.x +
        (1 - u) * v * bottomLeft.x,
      (1 - u) * (1 - v) * topLeft.y +
        u * (1 - v) * topRight.y +
        u * v * bottomRight.y +
        (1 - u) * v * bottomLeft.y,
    ];
  }
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const minimumX = Math.min(...xs);
  const minimumY = Math.min(...ys);
  return [
    minimumX + u * (Math.max(...xs) - minimumX),
    minimumY + v * (Math.max(...ys) - minimumY),
  ];
}

function transformMotionPath(
  keyframes: TransformKeyframe[],
  screenPoint: (position: [number, number]) => [number, number],
): string {
  if (!keyframes.length) {
    return "";
  }
  const start = screenPoint(keyframes[0].position);
  const segments = [`M ${start[0]} ${start[1]}`];
  for (let index = 1; index < keyframes.length; index += 1) {
    const previous = keyframes[index - 1];
    const current = keyframes[index];
    const target = screenPoint(current.position);
    if (
      previous.interpolation.kind === "bezier" &&
      previous.interpolation.spatial_out &&
      current.interpolation.spatial_in
    ) {
      const first = screenPoint([
        previous.position[0] + previous.interpolation.spatial_out[0],
        previous.position[1] + previous.interpolation.spatial_out[1],
      ]);
      const second = screenPoint([
        current.position[0] + current.interpolation.spatial_in[0],
        current.position[1] + current.interpolation.spatial_in[1],
      ]);
      segments.push(
        `C ${first[0]} ${first[1]} ${second[0]} ${second[1]} ${target[0]} ${target[1]}`,
      );
    } else {
      segments.push(`L ${target[0]} ${target[1]}`);
    }
  }
  return segments.join(" ");
}

function motionCoverageWarnings(
  layer: TransformLayer,
  coverage: StageCoverage,
): string[] {
  const samples: [number, number][] = [];
  for (let index = 0; index < layer.keyframes.length - 1; index += 1) {
    const start = layer.keyframes[index].position;
    const end = layer.keyframes[index + 1].position;
    for (let step = 0; step <= 20; step += 1) {
      samples.push([
        start[0] + ((end[0] - start[0]) * step) / 20,
        start[1] + ((end[1] - start[1]) * step) / 20,
      ]);
    }
  }
  if (layer.keyframes.length === 1) {
    samples.push(layer.keyframes[0].position);
  }
  const kinds = new Set<string>();
  for (const sample of samples) {
    const containing = coverage.regions.filter((region) =>
      pointInPolygon(sample, region.points),
    );
    if (containing.some((region) => region.kind === "occluded")) {
      kinds.add("Motion path enters an occluded region.");
    }
    if (containing.some((region) => region.kind === "uncovered")) {
      kinds.add("Motion path enters an uncovered region.");
    }
    if (
      !containing.some(
        (region) => region.kind === "covered" || region.kind === "overlap",
      )
    ) {
      kinds.add("Motion path enters an insufficiently calibrated region.");
    }
  }
  return [...kinds];
}

function pointInPolygon(
  point: [number, number],
  polygon: [number, number][],
): boolean {
  let inside = false;
  for (
    let index = 0, previous = polygon.length - 1;
    index < polygon.length;
    previous = index, index += 1
  ) {
    const [x, y] = polygon[index];
    const [previousX, previousY] = polygon[previous];
    if (
      y > point[1] !== previousY > point[1] &&
      point[0] <
        ((previousX - x) * (point[1] - y)) / (previousY - y) + x
    ) {
      inside = !inside;
    }
  }
  return inside;
}

function metadataRecords(value: unknown): Record<string, Record<string, unknown>> {
  const record = metadataRecord(value);
  return Object.fromEntries(
    Object.entries(record).map(([key, nested]) => [key, metadataRecord(nested)]),
  );
}

function numberValue(value: unknown, fallback: number): number {
  return typeof value === "number" ? value : fallback;
}

function trackArray(value: unknown): TimelineTrack[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter(
    (item): item is TimelineTrack =>
      typeof item === "object" &&
      item !== null &&
      typeof (item as TimelineTrack).id === "string" &&
      typeof (item as TimelineTrack).kind === "string" &&
      typeof (item as TimelineTrack).name === "string" &&
      typeof (item as TimelineTrack).resource_binding === "string",
  );
}

function lightingAuthorLayerMap(
    value: unknown,
  ): Record<string, LightingAuthorLayer[]> {
    const record = metadataRecord(value);
    return Object.fromEntries(
      Object.entries(record).map(([spaceId, items]) => [
        spaceId,
        Array.isArray(items)
          ? items.flatMap((item) => {
              if (
                typeof item !== "object" ||
                item === null ||
                typeof (item as LightingAuthorLayer).id !== "string" ||
                !(
                  (item as LightingAuthorLayer).mode === "spatial" ||
                  (item as LightingAuthorLayer).mode === "path" ||
                  (item as LightingAuthorLayer).mode === "fixture"
                )
              ) {
                return [];
              }
              return [
                {
                  ...(item as LightingAuthorLayer),
                  persistent:
                    typeof (item as Partial<LightingAuthorLayer>).persistent ===
                    "boolean"
                      ? (item as LightingAuthorLayer).persistent
                      : false,
                },
              ];
            })
          : [],
      ]),
    );
  }

function defaultLightingAuthorLayer(
    mode: LightingAuthorLayer["mode"],
    name: string,
    durationMs: number,
  ): LightingAuthorLayer {
    const common = {
      id: crypto.randomUUID(),
      name,
      mode,
      start_ms: 0,
      duration_ms: durationMs,
      persistent: false,
      precedence: 0,
      blend_mode: "replace" as const,
      opacity: 1,
    };
    if (mode === "path") {
      return {
        ...common,
        path: {
          path_id: "",
          primitive: "chase",
          position: 0,
          width: 0.1,
          direction: "forward",
          fill: 1,
          color: "#FFFFFF",
        },
      };
    }
    if (mode === "fixture") {
      return {
        ...common,
        fixture: {
          target_kind: "fixture",
          target_id: "",
          primitive: "solid",
          color: "#FFFFFF",
          brightness: 1,
        },
      };
    }
    return {
      ...common,
      spatial: {
        primitive: "solid",
        color: "#FFFFFF",
        end_color: "#000000",
        seed: 0,
        position: [0.5, 0.5],
        size: [0.25, 0.25],
        radius: 0.5,
      },
    };
  }
function defaultTransformKeyframe(timeMs: number): TransformKeyframe {
  return {
    id: crypto.randomUUID(),
    time_ms: timeMs,
    position: [0, 0],
    scale: [1, 1],
    rotation_deg: 0,
    anchor: [0.5, 0.5],
    opacity: 1,
    crop: [0, 0, 0, 0],
    interpolation: interpolationFor("linear"),
  };
}

function RippleEffectEditor({
  effect, disabled, onChange,
}: {
  effect: Extract<NonNullable<LightingAuthorLayer["spatial"]>, { primitive: "ripple" }>;
  disabled: boolean;
  onChange: (value: Extract<NonNullable<LightingAuthorLayer["spatial"]>, { primitive: "ripple" }>) => void;
}) {
  return (
    <fieldset disabled={disabled}>
      <legend>2D ripple · installation distance units</legend>
      <label>
        Coordinate space
        <select
          aria-label="Ripple coordinate space"
          value={effect.coordinate_space}
          onChange={(event) => onChange({
            ...effect, coordinate_space: event.target.value as typeof effect.coordinate_space,
          })}
        >
          <option value="surface_local">Surface local</option>
          <option value="stage">Whole display</option>
        </select>
      </label>
      <NumberVectorEditor
        label="Ripple origin" disabled={disabled} value={effect.origin}
        onChange={(value) => onChange({ ...effect, origin: [value[0], value[1]] })}
      />
      {(["speed", "ring_width"] as const).map((field) => (
        <label key={field}>
          {field === "speed" ? "Speed (units/second)" : "Ring width (units)"}
          <input
            aria-label={`Ripple ${field}`} type="number" min="0.001" step="0.1"
            value={effect[field]}
            onChange={(event) => onChange({ ...effect, [field]: Number(event.target.value) })}
          />
        </label>
      ))}
      <label>
        Color
        <input
          aria-label="Ripple color" type="color" value={effect.color}
          onChange={(event) => onChange({ ...effect, color: event.target.value })}
        />
      </label>
      <label>
        Repeat interval ms (0 = once)
        <input
          aria-label="Ripple repeat interval" type="number" min="0" value={effect.repeat_ms ?? 0}
          onChange={(event) => onChange({ ...effect, repeat_ms: Number(event.target.value) || null })}
        />
      </label>
    </fieldset>
  );
}

function interpolationFor(
  kind: TransformKeyframe["interpolation"]["kind"],
): TransformKeyframe["interpolation"] {
  return kind === "bezier"
    ? {
        kind,
        temporal_bezier: [0.25, 0.1, 0.25, 1],
        spatial_in: [0, 0],
        spatial_out: [0, 0],
      }
    : {
        kind,
        temporal_bezier: null,
        spatial_in: null,
        spatial_out: null,
      };
}

function transformLayerMap(value: unknown): Record<string, TransformLayer[]> {
  const record = metadataRecord(value);
  return Object.fromEntries(
    Object.entries(record).map(([spaceId, items]) => [
      spaceId,
      Array.isArray(items)
        ? items.flatMap((item) => {
            const layer = metadataRecord(item);
            const kind = layer.kind;
            const keyframes = transformKeyframeArray(layer.keyframes);
            const durationMs = numberValue(layer.duration_ms, 1000);
            const audio = metadataRecord(layer.audio);
            return typeof layer.id === "string" &&
              typeof layer.name === "string" &&
              (kind === "image" ||
                kind === "video" ||
                kind === "audio" ||
                kind === "composition") &&
              typeof layer.source_id === "string" &&
              typeof layer.track_id === "string" &&
              typeof layer.start_ms === "number" &&
              typeof layer.duration_ms === "number" &&
              (kind === "audio" || keyframes.length)
              ? [
                  {
                    layer_schema_version: 1 as const,
                    id: layer.id,
                    name: layer.name,
                    kind,
                    source_id: layer.source_id,
                    track_id: layer.track_id,
                    start_ms: layer.start_ms,
                    duration_ms: durationMs,
                    in_point_ms: numberValue(layer.in_point_ms, 0),
                    out_point_ms: numberValue(layer.out_point_ms, durationMs),
                    playback: layer.playback === "loop" || layer.playback === "once"
                      ? layer.playback : "hold",
                    z_index: numberValue(layer.z_index, 0),
                    fade_in_ms: numberValue(layer.fade_in_ms, 0),
                    fade_out_ms: numberValue(layer.fade_out_ms, 0),
                    audio:
                      kind === "audio"
                        ? {
                            gain_db: numberValue(audio.gain_db, 0),
                            mute: audio.mute === true,
                            solo: audio.solo === true,
                            mix_order: numberValue(audio.mix_order, 0),
                          }
                        : null,
                    alpha_interpretation:
                      kind === "audio"
                        ? null
                        : layer.alpha_interpretation === "opaque" ||
                            layer.alpha_interpretation === "premultiplied" ||
                            layer.alpha_interpretation === "black_transparent"
                          ? layer.alpha_interpretation
                          : "straight",
                    source_color_space:
                      kind === "audio"
                        ? null
                        : layer.source_color_space === "bt709" ||
                            layer.source_color_space === "linear_srgb"
                          ? layer.source_color_space
                          : "srgb",
                    scaling_behavior:
                      kind === "audio"
                        ? null
                        : layer.scaling_behavior === "fill" ||
                            layer.scaling_behavior === "stretch" ||
                            layer.scaling_behavior === "native"
                          ? layer.scaling_behavior
                          : "fit",
                    keyframes,
                    ...(kind !== "audio" ? layerGeometry(layer) : {}),
                  },
                ]
              : [];
          })
        : [],
    ]),
  );
}

function pointPairs(value: unknown): [number, number][] {
  return Array.isArray(value)
    ? value.flatMap((point) =>
        Array.isArray(point) &&
        point.length === 2 &&
        point.every((item) => typeof item === "number" && Number.isFinite(item))
          ? [[point[0], point[1]] as [number, number]]
          : [],
      )
    : [];
}

function layerGeometry(
  layer: Record<string, unknown>,
): Pick<TransformLayer, "corner_pin" | "mask"> {
  const result: Pick<TransformLayer, "corner_pin" | "mask"> = {};
  const pin = pointPairs(layer.corner_pin);
  if (pin.length === 4) result.corner_pin = pin;
  const mask = metadataRecord(layer.mask);
  const maskPoints = pointPairs(mask.points);
  if (maskPoints.length >= 3) {
    result.mask = {
      points: maskPoints,
      feather: numberValue(mask.feather, 0),
      invert: mask.invert === true,
    };
  }
  return result;
}

function transformKeyframeArray(value: unknown): TransformKeyframe[] {
  return Array.isArray(value)
    ? value.flatMap((item) => {
        const keyframe = metadataRecord(item);
        const interpolation = metadataRecord(keyframe.interpolation);
        const position = numberTuple(keyframe.position, 2);
        const scale = numberTuple(keyframe.scale, 2);
        const anchor = numberTuple(keyframe.anchor, 2);
        const crop = numberTuple(keyframe.crop, 4);
        const kind = interpolation.kind;
        if (
          typeof keyframe.id !== "string" ||
          typeof keyframe.time_ms !== "number" ||
          !position ||
          !scale ||
          typeof keyframe.rotation_deg !== "number" ||
          !anchor ||
          typeof keyframe.opacity !== "number" ||
          !crop ||
          !(
            kind === "hold" ||
            kind === "linear" ||
            kind === "ease_in" ||
            kind === "ease_out" ||
            kind === "bezier"
          )
        ) {
          return [];
        }
        return [
          {
            id: keyframe.id,
            time_ms: keyframe.time_ms,
            position: position as [number, number],
            scale: scale as [number, number],
            rotation_deg: keyframe.rotation_deg,
            anchor: anchor as [number, number],
            opacity: keyframe.opacity,
            crop: crop as [number, number, number, number],
            interpolation: {
              kind,
              temporal_bezier: numberTuple(
                interpolation.temporal_bezier,
                4,
              ) as [number, number, number, number] | null,
              spatial_in: numberTuple(interpolation.spatial_in, 2) as [
                number,
                number,
              ] | null,
              spatial_out: numberTuple(interpolation.spatial_out, 2) as [
                number,
                number,
              ] | null,
            },
          },
        ];
      })
    : [];
}

function numberTuple(value: unknown, length: number): number[] | null {
  return Array.isArray(value) &&
    value.length === length &&
    value.every((item) => typeof item === "number")
    ? value
    : null;
}

function collectEditorialAudioSources(
  scene: ProjectDraftRecord["draft"]["scenes"][number] | undefined,
  composition: Composition | undefined,
  tracks: TimelineTrack[],
  layers: TransformLayer[],
  assets: AssetRecord[],
  durationMs: number,
): EditorialAudioSource[] {
  if (!scene) {
    return [];
  }
  const layeredTrackIds = new Set<string>();
  const sources = layers.flatMap((layer): EditorialAudioSource[] => {
    if (layer.kind !== "audio" || !layer.audio) {
      return [];
    }
    layeredTrackIds.add(layer.track_id);
    const asset = assets.find((candidate) => candidate.id === layer.source_id);
    if (!asset || !assetSupportsEditorialAudio(asset)) {
      return [];
    }
    return [
      {
        id: `layer:${layer.id}`,
        name: layer.name,
        asset,
        start_ms: layer.start_ms,
        duration_ms: layer.duration_ms,
        in_point_ms: layer.in_point_ms,
        gain_db: layer.audio.gain_db,
        mute: layer.audio.mute,
        solo: layer.audio.solo,
        fade_in_ms: layer.fade_in_ms,
        fade_out_ms: layer.fade_out_ms,
      },
    ];
  });
  if (composition) {
    return sources;
  }
  for (const track of tracks) {
    if (
      !["media_output", "audio_reference"].includes(track.kind) ||
      layeredTrackIds.has(track.id)
    ) {
      continue;
    }
    const asset = timelineTrackAsset(scene, track, assets);
    if (!asset || !assetSupportsEditorialAudio(asset)) {
      continue;
    }
    const metadata = metadataRecord(asset.metadata);
    sources.push({
      id: `track:${track.id}`,
      name: track.name,
      asset,
      start_ms: 0,
      duration_ms: Math.min(
        durationMs,
        numberValue(metadata.duration_ms, durationMs),
      ),
      in_point_ms: 0,
      gain_db: 0,
      mute: false,
      solo: false,
      fade_in_ms: 0,
      fade_out_ms: 0,
    });
  }
  return sources;
}

function compositionTargetSuggestions(
  space: Composition["space_kind"],
  mapperExport: InstallationMapperExport | null,
): [string, string][] {
  if (space === "stage") return [["shared-stage", "Whole display"]];
  if (space === "lighting_surface") {
    return (mapperExport?.lighting_surfaces ?? []).map((surface) => [
      surface.semantic_id,
      surface.resource,
    ]);
  }
  return (mapperExport?.projectors ?? []).flatMap((value) => {
    const projector = metadataRecord(value);
    return typeof projector.resource === "string"
      ? [[projector.resource, "Projector"] as [string, string]]
      : [];
  });
}

function trackBindingSuggestions(
  kind: TrackKind,
  assets: AssetRecord[],
  mapperExport: InstallationMapperExport | null,
): [string, string][] {
  if (kind === "audio_reference") {
    return assets
      .filter((asset) => asset.status === "ready" && assetSupportsEditorialAudio(asset))
      .map((asset) => [asset.id, asset.name]);
  }
  if (kind === "fixture") {
    return (mapperExport?.fixtures ?? []).map((fixture) => [fixture.semantic_id, fixture.name]);
  }
  if (kind === "path") {
    return (mapperExport?.paths ?? []).map((path) => [path.semantic_id, path.name]);
  }
  return (mapperExport?.resources ?? []).map((resource) => [resource.resource, resource.kind]);
}

function assetKindLabel(asset: AssetRecord): string {
  const type = asset.media_type ?? "";
  if (type === "image/gif") return "GIF";
  if (type.startsWith("image/")) return "image";
  if (type.startsWith("video/")) return "video";
  if (type.startsWith("audio/")) return "audio";
  return type || "file";
}

function assetMatchesLayerKind(asset: AssetRecord, kind: TransformLayer["kind"]): boolean {
  const type = asset.media_type ?? "";
  if (kind === "image") return type.startsWith("image/") && type !== "image/gif";
  if (kind === "video") return type.startsWith("video/") || type === "image/gif";
  if (kind === "audio") return type.startsWith("audio/") || type.startsWith("video/");
  return false;
}

function assetSupportsEditorialAudio(asset: AssetRecord): boolean {
  if (!hasAssetArtifact(asset, "review")) {
    return false;
  }
  if (asset.media_type?.startsWith("audio/")) {
    return true;
  }
  const codecs = asset.metadata?.audio_codecs;
  return Array.isArray(codecs) && codecs.length > 0;
}

function timelineTrackAsset(
  scene: ProjectDraftRecord["draft"]["scenes"][number],
  track: TimelineTrack,
  assets: AssetRecord[],
): AssetRecord | undefined {
  let assetId: string | null = null;
  if (track.kind === "media_output") {
    if (Array.isArray(scene.media)) {
      const outputId = track.id.startsWith("media:")
        ? track.id.slice("media:".length)
        : track.id;
      assetId =
        scene.media.find((output) => output.id === outputId)?.asset ?? null;
    } else {
      assetId = scene.media.asset;
    }
  } else if (track.kind === "audio_reference") {
    assetId = track.resource_binding;
  }
  return assetId
    ? assets.find((candidate) => candidate.id === assetId)
    : undefined;
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

function syncMarkerMap(value: unknown): Record<string, SyncMarker[]> {
  const record = metadataRecord(value);
  return Object.fromEntries(
    Object.entries(record).map(([spaceId, items]) => [
      spaceId,
      Array.isArray(items)
        ? items.flatMap((item) => {
            const marker = metadataRecord(item);
            return typeof marker.id === "string" &&
              typeof marker.name === "string" &&
              typeof marker.time_ms === "number"
              ? [
                  {
                    id: marker.id,
                    name: marker.name,
                    time_ms: marker.time_ms,
                  },
                ]
              : [];
          })
        : [],
    ]),
  );
}

function syncCueMap(value: unknown): Record<string, SyncCue[]> {
  const record = metadataRecord(value);
  return Object.fromEntries(
    Object.entries(record).map(([spaceId, items]) => [
      spaceId,
      Array.isArray(items)
        ? items.flatMap((item) => {
            const cue = metadataRecord(item);
            const kind = cue.kind;
            const lighting = lightingCueProperties(cue.lighting);
            return typeof cue.id === "string" &&
              typeof cue.name === "string" &&
              isSyncCueKind(kind) &&
              typeof cue.track_id === "string" &&
              (cue.time_ms === null || typeof cue.time_ms === "number") &&
              (cue.marker_id === null || typeof cue.marker_id === "string") &&
              typeof cue.marker_offset_ms === "number" &&
              (kind !== "lighting" || lighting !== undefined)
              ? [
                  {
                    id: cue.id,
                    name: cue.name,
                    kind,
                    track_id: cue.track_id,
                    time_ms: cue.time_ms,
                    marker_id: cue.marker_id,
                    marker_offset_ms: cue.marker_offset_ms,
                    lighting,
                  },
                ]
              : [];
          })
        : [],
    ]),
  );
}

const lightingPrimitives: LightingPrimitive[] = [
  "solid",
  "blackout",
  "envelope",
  "pulse",
  "chase",
  "path_sweep",
  "flicker",
  "sparkle",
  "ripple",
  "color_transition",
];

function defaultLightingCue(
  durationMs: number,
  track: TimelineTrack | undefined,
): LightingCueProperties {
  const targetKind =
    track?.kind === "fixture"
      ? "fixture"
      : track?.kind === "path"
        ? "path"
        : null;
  return {
    primitive: "solid",
    duration_ms: durationMs,
    target:
      targetKind && track
        ? { kind: targetKind, id: track.resource_binding }
        : null,
    color: "#FFFFFF",
    end_color: null,
    brightness: 1,
    end_brightness: null,
    fade_in_ms: 0,
    fade_out_ms: 0,
    period_ms: 200,
    width_pixels: 1,
    density: 0.1,
    seed: 0,
    reverse: false,
    blend_mode: "replace",
    precedence: 0,
  };
}

function cuePresetArray(value: unknown): CuePreset[] {
  return Array.isArray(value)
    ? value.flatMap((item) => {
        const preset = metadataRecord(item);
        const lighting = lightingCueProperties(preset.lighting);
        return typeof preset.id === "string" &&
          typeof preset.name === "string" &&
          lighting
          ? [{ id: preset.id, name: preset.name, lighting }]
          : [];
      })
    : [];
}

function cloneLightingCue(
  lighting: LightingCueProperties,
): LightingCueProperties {
  return {
    ...lighting,
    target: lighting.target ? { ...lighting.target } : null,
  };
}

function canShiftCompoundSelection(
  cues: SyncCue[],
  markers: SyncMarker[],
  layers: TransformLayer[],
  selection: string[],
  shiftMs: number,
  duration: number,
): boolean {
  const cuesFit = cues
    .filter((cue) => selection.includes(cue.id))
    .every((cue) => {
      const start = cueTime(cue, markers) + shiftMs;
      return start >= 0 && start + (cue.lighting?.duration_ms ?? 0) <= duration;
    });
  const layersFit = layers
    .filter((layer) => selection.includes(layer.id))
    .every(
      (layer) =>
        layer.start_ms + shiftMs >= 0 &&
        layer.start_ms + shiftMs + layer.duration_ms <= duration,
    );
  return cuesFit && layersFit;
}

function lightingCueProperties(value: unknown): LightingCueProperties | undefined {
  const lighting = metadataRecord(value);
  const target = lighting.target === null ? null : metadataRecord(lighting.target);
  if (
    !lightingPrimitives.includes(lighting.primitive as LightingPrimitive) ||
    typeof lighting.duration_ms !== "number" ||
    (target !== null &&
      !(
        (target.kind === "segment" ||
          target.kind === "fixture" ||
          target.kind === "path") &&
        typeof target.id === "string"
      )) ||
    typeof lighting.color !== "string" ||
    !(
      lighting.end_color === null || typeof lighting.end_color === "string"
    ) ||
    typeof lighting.brightness !== "number" ||
    !(
      lighting.end_brightness === null ||
      typeof lighting.end_brightness === "number"
    ) ||
    typeof lighting.fade_in_ms !== "number" ||
    typeof lighting.fade_out_ms !== "number" ||
    typeof lighting.period_ms !== "number" ||
    typeof lighting.width_pixels !== "number" ||
    typeof lighting.density !== "number" ||
    typeof lighting.seed !== "number" ||
    typeof lighting.reverse !== "boolean" ||
    !(
      lighting.blend_mode === "replace" ||
      lighting.blend_mode === "add" ||
      lighting.blend_mode === "max" ||
      lighting.blend_mode === "multiply"
    ) ||
    typeof lighting.precedence !== "number"
  ) {
    return undefined;
  }
  return {
    primitive: lighting.primitive as LightingPrimitive,
    duration_ms: lighting.duration_ms,
    target: target as LightingCueProperties["target"],
    color: lighting.color,
    end_color: lighting.end_color,
    brightness: lighting.brightness,
    end_brightness: lighting.end_brightness,
    fade_in_ms: lighting.fade_in_ms,
    fade_out_ms: lighting.fade_out_ms,
    period_ms: lighting.period_ms,
    width_pixels: lighting.width_pixels,
    density: lighting.density,
    seed: lighting.seed,
    reverse: lighting.reverse,
    blend_mode: lighting.blend_mode,
    precedence: lighting.precedence,
  };
}

function isSyncCueKind(value: unknown): value is SyncCueKind {
  return (
    value === "visual" ||
    value === "audio" ||
    value === "lighting" ||
    value === "switch" ||
    value === "physical_effect"
  );
}

function markerTime(markerId: string, markers: SyncMarker[]): number {
  return markers.find((marker) => marker.id === markerId)?.time_ms ?? 0;
}

function cueTime(cue: SyncCue, markers: SyncMarker[]): number {
  return cue.marker_id
    ? markerTime(cue.marker_id, markers) + cue.marker_offset_ms
    : (cue.time_ms ?? 0);
}

function evaluateTimelineState(
  timeMs: number,
  cues: SyncCue[],
  markers: SyncMarker[],
  layers: TransformLayer[],
) {
  const evaluatedCues = cues
    .flatMap((cue) => {
      const start = cueTime(cue, markers);
      const duration = cue.lighting?.duration_ms ?? 0;
      const active =
        duration > 0
          ? timeMs >= start && timeMs < start + duration
          : timeMs === start;
      return active
        ? [
            {
              id: cue.id,
              kind: cue.kind,
              local_time_ms: timeMs - start,
            },
          ]
        : [];
    })
    .sort((left, right) => left.id.localeCompare(right.id));
  const transforms = layers
    .flatMap((layer) => {
      const layerTime = timeMs - layer.start_ms;
      if (layerTime < 0 || layerTime > layer.duration_ms) {
        return [];
      }
      const keyframe = [...layer.keyframes]
        .reverse()
        .find((candidate) => candidate.time_ms <= layerTime);
      return keyframe
        ? [
            {
              id: layer.id,
              keyframe_id: keyframe.id,
              local_time_ms: layerTime,
              position: keyframe.position,
              scale: keyframe.scale,
              rotation_deg: keyframe.rotation_deg,
              opacity: keyframe.opacity,
              crop: keyframe.crop,
            },
          ]
        : [];
    })
    .sort((left, right) => left.id.localeCompare(right.id));
  return {
    timestamp_ms: timeMs,
    cues: evaluatedCues,
    transforms,
  };
}

function markerMoveBounds(
  markerId: string,
  cues: SyncCue[],
  duration: number,
): { minimum: number; maximum: number } {
  const offsets = cues
    .filter((cue) => cue.marker_id === markerId)
    .map((cue) => ({
      start: cue.marker_offset_ms,
      duration: cue.lighting?.duration_ms ?? 0,
    }));
  return {
    minimum: Math.max(0, ...offsets.map((offset) => -offset.start)),
    maximum: Math.min(
      duration,
      ...offsets.map((offset) => duration - offset.start - offset.duration),
    ),
  };
}

type ResolvedSyncCue = {
  cue_id: string;
  cue_name: string;
  kind: SyncCueKind;
  track_id: string;
  marker_id: string | null;
  marker_name: string | null;
  editing_space_id: string;
  context_id: string;
  scene_id: string;
  local_time_ms: number;
  scene_time_ms: number;
  lighting: LightingCueProperties | null;
};

function resolveSyncSchedule(
  scenes: { id: string; duration_ms: number }[],
  compositions: Record<string, Composition>,
  sceneCompositions: Record<string, string[]>,
  instances: Record<string, CompositionInstance[]>,
  markerMap: Record<string, SyncMarker[]>,
  cueMap: Record<string, SyncCue[]>,
): ResolvedSyncCue[] {
  const resolved: ResolvedSyncCue[] = [];
  const emit = (
    sceneId: string,
    spaceId: string,
    contextId: string,
    offsetMs: number,
    timeScale: number,
  ) => {
    const markers = markerMap[spaceId] ?? [];
    for (const cue of cueMap[spaceId] ?? []) {
      const localTime = cueTime(cue, markers);
      const marker = cue.marker_id
        ? markers.find((candidate) => candidate.id === cue.marker_id)
        : undefined;
      resolved.push({
        cue_id: cue.id,
        cue_name: cue.name,
        kind: cue.kind,
        track_id: cue.track_id,
        marker_id: cue.marker_id,
        marker_name: marker?.name ?? null,
        editing_space_id: spaceId,
        context_id: contextId,
        scene_id: sceneId,
        local_time_ms: localTime,
        scene_time_ms: Math.round(offsetMs + localTime * timeScale),
        lighting: cue.lighting ?? null,
      });
    }
  };
  const walk = (
    sceneId: string,
    compositionId: string,
    contextId: string,
    offsetMs: number,
    timeScale: number,
    path: Set<string>,
  ) => {
    if (path.has(compositionId)) {
      return;
    }
    const composition = compositions[compositionId];
    if (!composition) {
      return;
    }
    const nextPath = new Set(path).add(compositionId);
    emit(sceneId, compositionId, contextId, offsetMs, timeScale);
    for (const childId of composition.children) {
      const child = compositions[childId];
      if (child) {
        walk(
          sceneId,
          childId,
          `${contextId}>${childId}`,
          offsetMs + child.local_time_offset_ms * timeScale,
          timeScale,
          nextPath,
        );
      }
    }
  };
  for (const scene of scenes) {
    emit(scene.id, scene.id, `scene:${scene.id}`, 0, 1);
    const placed = new Set((instances[scene.id] ?? []).map((instance) => instance.composition_id));
    for (const rootId of sceneCompositions[scene.id] ?? []) {
      const root = compositions[rootId];
      if (root && !placed.has(rootId)) {
        walk(
          scene.id,
          rootId,
          `scene:${scene.id}>${rootId}`,
          root.local_time_offset_ms,
          1,
          new Set(),
        );
      }
    }
    for (const instance of instances[scene.id] ?? []) {
      walk(
        scene.id,
        instance.composition_id,
        `instance:${instance.id}`,
        instance.start_offset_ms,
        instance.time_scale,
        new Set(),
      );
    }
  }
  return resolved.sort(
    (left, right) =>
      left.scene_id.localeCompare(right.scene_id) ||
      left.scene_time_ms - right.scene_time_ms ||
      left.context_id.localeCompare(right.context_id) ||
      left.cue_id.localeCompare(right.cue_id),
  );
}

function humanizeTrackKind(kind: TrackKind): string {
  return kind.replaceAll("_", " ");
}

function compositionMap(value: unknown): Record<string, Composition> {
  const record = metadataRecord(value);
  return Object.fromEntries(
    Object.entries(record)
      .filter(([, item]) => typeof item === "object" && item !== null)
      .map(([id, item]) => {
        const composition = metadataRecord(item);
        return [
          id,
          {
            id,
            name: String(composition.name ?? id),
            duration_ms: numberValue(composition.duration_ms, 1000),
            space_kind: compositionSpace(composition.space_kind),
            target_id: String(composition.target_id ?? "shared-stage"),
            width: numberValue(composition.width, 1),
            height: numberValue(composition.height, 1),
            local_time_offset_ms: numberValue(
              composition.local_time_offset_ms,
              0,
            ),
            children: stringArray(composition.children),
            stage_lighting_surfaces: stringArray(composition.stage_lighting_surfaces),
            ...(Array.isArray(composition.stage_projector_resources)
              ? { stage_projector_resources: stringArray(composition.stage_projector_resources) }
              : {}),
            ...(typeof composition.source_pixel_units === "number" &&
            Number.isFinite(composition.source_pixel_units) &&
            composition.source_pixel_units > 0
              ? { source_pixel_units: composition.source_pixel_units }
              : {}),
            ...(composition.led_background === "ambient" || composition.led_background === "background"
              ? { led_background: composition.led_background }
              : {}),
            safe_points_ms: numberArray(composition.safe_points_ms),
            protected_intervals: intervalArray(
              composition.protected_intervals,
            ),
            render_settings: renderSettings(composition.render_settings),
            lighting_settings: lightingSettings(composition.lighting_settings),
          },
        ];
      }),
  );
}

function defaultLightingSettings(): Composition["lighting_settings"] {
  return {
    canvas_width: 256,
    canvas_height: 256,
    sampling: "bilinear",
    gamma: 2.2,
    brightness: 1,
  };
}

function lightingSettings(value: unknown): Composition["lighting_settings"] {
  const settings = metadataRecord(value);
  return {
    canvas_width: numberValue(settings.canvas_width, 256),
    canvas_height: numberValue(settings.canvas_height, 256),
    sampling:
      settings.sampling === "nearest" || settings.sampling === "area"
        ? settings.sampling
        : "bilinear",
    gamma: numberValue(settings.gamma, 2.2),
    brightness: numberValue(settings.brightness, 1),
  };
}

/** Raster a composition renders at; stage rasters follow the composition's shape. */
function compositionRasterSize(composition: Composition): [number, number] {
  const { render_width: width, render_height: height } = composition.render_settings;
  return composition.space_kind === "stage"
    ? [width, stageRenderHeight(composition.width, composition.height, width)]
    : [width, height];
}

/** Stage render height that keeps the composition's shape, exactly as the renderer derives it. */
function stageRenderHeight(width: number, height: number, renderWidth: number): number {
  return Math.max(1, Math.round((renderWidth * height) / width));
}

/**
 * Resize a stage composition's render raster (and optionally its logical size) without
 * resizing its media: media size is pinned in composition units before anything changes.
 */
function withStageSize(
  composition: Composition,
  change: { renderWidth: number; width?: number; height?: number },
): Composition {
  const sourcePixelUnits = layerUnitsPerSourcePixel(composition);
  const width = change.width ?? composition.width;
  const height = change.height ?? composition.height;
  return {
    ...composition,
    width,
    height,
    source_pixel_units: sourcePixelUnits,
    render_settings: {
      ...composition.render_settings,
      render_width: change.renderWidth,
      render_height: stageRenderHeight(width, height, change.renderWidth),
    },
  };
}

function defaultRenderSettings(
  resolution: [number, number] = [1920, 1080],
): Composition["render_settings"] {
  return {
    render_pipeline_revision: 1,
    render_width: Math.round(resolution[0]),
    render_height: Math.round(resolution[1]),
    frame_rate_numerator: 30000,
    frame_rate_denominator: 1001,
    working_color_space: "linear_srgb",
    working_alpha_mode: "premultiplied",
    scaling_filter: "lanczos",
    output_background_color: "#000000",
    media_end_behavior: "hold_last_frame",
    output_profile: outputProfile("low_power_h264"),
  };
}

function roundStage(value: number): number {
  return Math.round(value * 1000) / 1000;
}

/** Lights-only overlays only sample the frame at LED positions, so a modest raster suffices. */
const LIGHTS_ONLY_RENDER_WIDTH = 1200;

function suggestedStageRenderResolution(
  mapperExport: InstallationMapperExport | null,
  lightsOnly = false,
): [number, number] {
  const stage = mapperExport?.stage;
  if (lightsOnly) {
    const aspect = stage && stage.width > 0 ? stage.height / stage.width : 9 / 16;
    const width = aspect > 1 ? Math.round(LIGHTS_ONLY_RENDER_WIDTH / aspect) : LIGHTS_ONLY_RENDER_WIDTH;
    return [
      Math.max(2, 2 * Math.round(width / 2)),
      Math.max(2, 2 * Math.round((width * aspect) / 2)),
    ];
  }
  if (stage) {
    const ppu = Math.max(
      1920 / stage.width,
      ...(mapperExport?.projectors ?? []).flatMap((value) => {
        const projector = metadataRecord(value);
        const extent = projector.stage_extent;
        return Array.isArray(extent) &&
          typeof extent[2] === "number" &&
          typeof extent[3] === "number" &&
          typeof projector.native_width === "number" &&
          typeof projector.native_height === "number"
          ? [projector.native_width / extent[2], projector.native_height / extent[3]]
          : [];
      }),
    );
    const scale = Math.min(1, 3840 / (stage.width * ppu), 2160 / (stage.height * ppu));
    return [
      Math.max(2, 2 * Math.round((stage.width * ppu * scale) / 2)),
      Math.max(2, 2 * Math.round((stage.height * ppu * scale) / 2)),
    ];
  }
  if (!mapperExport?.projectors.length) {
    return [1920, 1080];
  }
  const projectors = mapperExport.projectors
    .map((value) => metadataRecord(value))
    .flatMap((projector) => {
      const extent = projector.stage_extent;
      return Array.isArray(extent) &&
        extent.length === 4 &&
        extent.every((item) => typeof item === "number") &&
        typeof projector.native_width === "number" &&
        typeof projector.native_height === "number"
        ? [
            {
              x: extent[0] as number,
              y: extent[1] as number,
              width: extent[2] as number,
              height: extent[3] as number,
              nativeWidth: projector.native_width,
              nativeHeight: projector.native_height,
            },
          ]
        : [];
    });
  if (!projectors.length) {
    return [1920, 1080];
  }
  const minimumX = Math.min(...projectors.map((item) => item.x));
  const minimumY = Math.min(...projectors.map((item) => item.y));
  const maximumX = Math.max(
    ...projectors.map((item) => item.x + item.width),
  );
  const maximumY = Math.max(
    ...projectors.map((item) => item.y + item.height),
  );
  const pixelsPerUnit = Math.max(
    ...projectors.flatMap((item) => [
      item.nativeWidth / item.width,
      item.nativeHeight / item.height,
    ]),
  );
  return [
    Math.ceil((maximumX - minimumX) * pixelsPerUnit),
    Math.ceil((maximumY - minimumY) * pixelsPerUnit),
  ];
}

function outputProfile(
  preset: "low_power_h264" | "legacy_h264",
): Composition["render_settings"]["output_profile"] {
  return preset === "legacy_h264"
    ? {
        preset_id: preset,
        container: "mp4",
        video_codec: "h264",
        video_profile: "baseline",
        video_level: "3.1",
        pixel_format: "yuv420p",
        max_video_bitrate_kbps: 4000,
        audio_codec: "aac",
        audio_sample_rate: 48000,
        audio_channel_layout: "stereo",
        loudness_target_lufs: -16,
        keyframe_interval_frames: 60,
        b_frames: 0,
        hardware_decoder_compatibility: "h264_baseline_level_3_1_yuv420p",
      }
    : {
        preset_id: preset,
        container: "mp4",
        video_codec: "h264",
        video_profile: "main",
        video_level: "4.0",
        pixel_format: "yuv420p",
        max_video_bitrate_kbps: 8000,
        audio_codec: "aac",
        audio_sample_rate: 48000,
        audio_channel_layout: "stereo",
        loudness_target_lufs: -16,
        keyframe_interval_frames: 60,
        b_frames: 0,
        hardware_decoder_compatibility: "h264_main_level_4_yuv420p",
      };
}

function renderSettings(value: unknown): Composition["render_settings"] {
  const settings = metadataRecord(value);
  const rawProfile = metadataRecord(settings.output_profile);
  const preset =
    rawProfile.preset_id === "legacy_h264"
      ? "legacy_h264"
      : "low_power_h264";
  const profile = outputProfile(preset);
  return {
    render_pipeline_revision: 1,
    render_width: numberValue(settings.render_width, 1920),
    render_height: numberValue(settings.render_height, 1080),
    frame_rate_numerator: numberValue(
      settings.frame_rate_numerator,
      30000,
    ),
    frame_rate_denominator: numberValue(
      settings.frame_rate_denominator,
      1001,
    ),
    working_color_space: "linear_srgb",
    working_alpha_mode: "premultiplied",
    scaling_filter:
      settings.scaling_filter === "bicubic" ? "bicubic" : "lanczos",
    output_background_color:
      typeof settings.output_background_color === "string"
        ? settings.output_background_color
        : "#000000",
    media_end_behavior:
      settings.media_end_behavior === "stop" ||
      settings.media_end_behavior === "loop"
        ? settings.media_end_behavior
        : "hold_last_frame",
    output_profile: {
      ...profile,
      max_video_bitrate_kbps: numberValue(
        rawProfile.max_video_bitrate_kbps,
        profile.max_video_bitrate_kbps,
      ),
      loudness_target_lufs: numberValue(
        rawProfile.loudness_target_lufs,
        profile.loudness_target_lufs,
      ),
      keyframe_interval_frames: numberValue(
        rawProfile.keyframe_interval_frames,
        profile.keyframe_interval_frames,
      ),
    },
  };
}

function stringArrayMap(value: unknown): Record<string, string[]> {
  const record = metadataRecord(value);
  return Object.fromEntries(
    Object.entries(record).map(([key, item]) => [key, stringArray(item)]),
  );
}

type ScenePlacedComposition = {
  key: string;
  compositionId: string;
  instanceId: string | null;
  startMs: number;
  durationMs: number;
};

/** Where compositions play in a scene, mirroring the compiler: instances, else scene roots. */
function scenePlacedCompositions(
  sceneId: string,
  compositions: Record<string, Composition>,
  sceneCompositions: Record<string, string[]>,
  compositionInstances: Record<string, CompositionInstance[]>,
): ScenePlacedComposition[] {
  const instances = (compositionInstances[sceneId] ?? []).filter(
    (instance) => compositions[instance.composition_id],
  );
  const instanced = new Set(instances.map((instance) => instance.composition_id));
  const roots = (sceneCompositions[sceneId] ?? [])
    .filter((id) => compositions[id] && !instanced.has(id))
    .map((id) => ({
      key: `root:${id}`,
      compositionId: id,
      instanceId: null,
      startMs: compositions[id].local_time_offset_ms,
      durationMs: compositions[id].duration_ms,
    }));
  return [
    ...roots,
    ...instances.map((instance) => ({
      key: `instance:${instance.id}`,
      compositionId: instance.composition_id,
      instanceId: instance.id,
      startMs: instance.start_offset_ms,
      durationMs: Math.round(compositions[instance.composition_id].duration_ms * instance.time_scale),
    })),
  ].sort((left, right) => left.startMs - right.startMs || left.key.localeCompare(right.key));
}

/** Compositions placed in a scene, their nested children, and the open one, in project order. */
function sceneScopedCompositions(
  placedIds: string[],
  compositions: Record<string, Composition>,
  activeCompositionId: string,
): Composition[] {
  const included = new Set<string>();
  const pending = [...placedIds, activeCompositionId];
  while (pending.length > 0) {
    const id = pending.pop()!;
    if (included.has(id) || !compositions[id]) continue;
    included.add(id);
    pending.push(...compositions[id].children);
  }
  return Object.values(compositions).filter((candidate) => included.has(candidate.id));
}

/** Open a scene without media outputs on its only composition, where its content is edited. */
function defaultCompositionForScene(
  scene: ProjectDraftRecord["draft"]["scenes"][number] | undefined,
  compositions: Record<string, Composition>,
  sceneCompositions: Record<string, string[]>,
  compositionInstances: Record<string, CompositionInstance[]>,
): string {
  if (!scene || !Array.isArray(scene.media) || scene.media.length > 0) return "";
  const placed = new Set(
    scenePlacedCompositions(scene.id, compositions, sceneCompositions, compositionInstances).map(
      (item) => item.compositionId,
    ),
  );
  return placed.size === 1 ? [...placed][0] : "";
}

function compositionInstanceMap(
  value: unknown,
): Record<string, CompositionInstance[]> {
  const record = metadataRecord(value);
  return Object.fromEntries(
    Object.entries(record).map(([sceneId, instances]) => [
      sceneId,
      Array.isArray(instances)
        ? instances.flatMap((item) => {
            const instance = metadataRecord(item);
            const placement = metadataRecord(instance.placement);
            const overrides = metadataRecord(instance.parameter_overrides);
            return typeof instance.id === "string" &&
              typeof instance.composition_id === "string" &&
              typeof instance.start_offset_ms === "number" &&
              typeof instance.time_scale === "number" &&
              typeof placement.x === "number" &&
              typeof placement.y === "number"
              ? [
                  {
                    id: instance.id,
                    composition_id: instance.composition_id,
                    start_offset_ms: instance.start_offset_ms,
                    time_scale: instance.time_scale,
                    parameter_overrides: overrides,
                    placement: { x: placement.x, y: placement.y },
                  },
                ]
              : [];
          })
        : [],
    ]),
  );
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) &&
    value.every((item) => typeof item === "string")
    ? value
    : [];
}

function numberArray(value: unknown): number[] {
  return Array.isArray(value) &&
    value.every((item) => typeof item === "number")
    ? value
    : [];
}

function intervalArray(
  value: unknown,
): { start_ms: number; end_ms: number }[] {
  return Array.isArray(value)
    ? value.flatMap((item) => {
        const interval = metadataRecord(item);
        return typeof interval.start_ms === "number" &&
          typeof interval.end_ms === "number"
          ? [{ start_ms: interval.start_ms, end_ms: interval.end_ms }]
          : [];
      })
    : [];
}

function compositionUsage(
  compositions: Record<string, Composition>,
  scenes: Record<string, string[]>,
  instances: Record<string, CompositionInstance[]>,
  layers: Record<string, TransformLayer[]>,
  sceneNames: Record<string, string>,
  compositionId: string,
): string[] {
  const sceneLabel = (sceneId: string) => `scene ${sceneNames[sceneId] ?? sceneId}`;
  const parents = Object.values(compositions)
    .filter((composition) => composition.children.includes(compositionId))
    .map((composition) => `composition ${composition.name}`);
  const sceneUsage = Object.entries(scenes)
    .filter(([, ids]) => ids.includes(compositionId))
    .map(([sceneId]) => sceneLabel(sceneId));
  const instanceUsage = Object.entries(instances).flatMap(
    ([sceneId, sceneInstances]) =>
      sceneInstances
        .filter((instance) => instance.composition_id === compositionId)
        .map((instance) => `${sceneLabel(sceneId)} instance ${instance.id}`),
  );
  const layerUsage = Object.entries(layers).flatMap(([spaceId, spaceLayers]) =>
    spaceLayers
      .filter((layer) => layer.kind === "composition" && layer.source_id === compositionId)
      .map((layer) => {
        const owner = sceneNames[spaceId] !== undefined
          ? sceneLabel(spaceId)
          : `composition ${compositions[spaceId]?.name ?? spaceId}`;
        return `${owner} media layer ${layer.name}`;
      }),
  );
  return [...new Set([...parents, ...sceneUsage, ...instanceUsage, ...layerUsage])];
}

function wouldCreateCycle(
  compositions: Record<string, Composition>,
  parentId: string,
  childId: string,
): boolean {
  const pending = [childId];
  const visited = new Set<string>();
  while (pending.length) {
    const current = pending.pop()!;
    if (current === parentId) {
      return true;
    }
    if (visited.has(current)) {
      continue;
    }
    visited.add(current);
    pending.push(...(compositions[current]?.children ?? []));
  }
  return false;
}

function compositionSpace(value: unknown): Composition["space_kind"] {
  return value === "projector_native" ||
    value === "lighting_surface" ||
    value === "stage"
    ? value
    : "stage";
}

function humanizeSpace(value: Composition["space_kind"]): string {
  if (value === "stage") return "Whole display overlay";
  if (value === "lighting_surface") return "LED surface";
  return "Projector";
}

function parseObject(value: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(value);
    return typeof parsed === "object" &&
      parsed !== null &&
      !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function compositionBreadcrumbs(
  compositions: Record<string, Composition>,
  sceneRoots: string[],
  activeId: string,
): string[] {
  if (!activeId) {
    return [];
  }
  const allChildren = new Set(
    Object.values(compositions).flatMap((composition) => composition.children),
  );
  const roots = [
    ...new Set([
      ...Object.keys(compositions).filter((id) => !allChildren.has(id)),
      ...sceneRoots,
    ]),
  ];
  function find(current: string, path: string[]): string[] | null {
    if (current === activeId) {
      return [...path, current];
    }
    for (const child of compositions[current]?.children ?? []) {
      const found = find(child, [...path, current]);
      if (found) {
        return found;
      }
    }
    return null;
  }
  for (const root of roots) {
    const path = find(root, []);
    if (path) {
      return path;
    }
  }
  return [activeId];
}

function filterMetadataKeys(
  record: Record<string, unknown>,
  validKeys: Set<string>,
): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(record).filter(([key]) => validKeys.has(key)),
  );
}

function compositionGraphHasCycle(
  compositions: Record<string, Composition>,
): boolean {
  const visiting = new Set<string>();
  const visited = new Set<string>();
  function visit(id: string): boolean {
    if (visiting.has(id)) {
      return true;
    }
    if (visited.has(id)) {
      return false;
    }
    visiting.add(id);
    for (const child of compositions[id]?.children ?? []) {
      if (visit(child)) {
        return true;
      }
    }
    visiting.delete(id);
    visited.add(id);
    return false;
  }
  return Object.keys(compositions).some(visit);
}

function canReplaceComposition(
  compositions: Record<string, Composition>,
  removedId: string,
  replacementId: string,
): boolean {
  if (!compositions[replacementId] || removedId === replacementId) {
    return false;
  }
  const candidate = Object.fromEntries(
    Object.entries(compositions)
      .filter(([id]) => id !== removedId)
      .map(([id, composition]) => [
        id,
        {
          ...composition,
          children: [
            ...new Set(
              composition.children
                .map((child) =>
                  child === removedId ? replacementId : child,
                )
                .filter((child) => child !== id),
            ),
          ],
        },
      ]),
  );
  return !compositionGraphHasCycle(candidate);
}

/** Repeat count to write; undefined removes it (loop repeats forever, one play is the default). */
function repeatCountUpdate(
  count: number | null | undefined,
  endBehavior: unknown,
): { repeat_count?: number | undefined } {
  if (endBehavior === "loop") return { repeat_count: undefined };
  if (count === undefined) return {};
  return { repeat_count: count !== null && count > 1 ? count : undefined };
}

function MediaOutputRepeatControls({
  asset,
  canEdit,
  output,
  sceneDurationMs,
  onRepeatCount,
  onMatchSceneLength,
}: {
  asset: AssetRecord | undefined;
  canEdit: boolean;
  output: SceneMediaOutputRef;
  sceneDurationMs: number;
  onRepeatCount: (count: number | null) => void;
  onMatchSceneLength: (durationMs: number) => void;
}) {
  const looping = output.endBehavior === "loop";
  const plays = output.repeatCount ?? 1;
  const assetDuration = numberValue(asset?.metadata?.duration_ms, 0);
  const playedMs = output.startDelayMs + assetDuration * plays;
  return (
    <>
      <label>
        Plays
        <input
          aria-label="Media plays"
          type="number"
          min="1"
          max="10000"
          step="1"
          disabled={!canEdit || looping}
          value={looping ? 1 : plays}
          onChange={(event) => {
            const value = Math.round(Number(event.target.value));
            onRepeatCount(Number.isFinite(value) && value > 1 ? Math.min(10000, value) : null);
          }}
        />
        <small>
          {looping
            ? "Loop repeats until the scene ends. Choose Hold last frame or Stop to play a set number of times."
            : "Plays the media back to back this many times, then applies the end behavior."}
        </small>
      </label>
      {!looping && assetDuration > 0 && (
        <div className="timeline-media-repeat-summary">
          <small>
            {plays > 1 ? `${plays} × ${formatTimecode(assetDuration)} = ` : ""}
            {formatTimecode(playedMs - output.startDelayMs)}
            {output.startDelayMs > 0 ? ` after a ${formatTimecode(output.startDelayMs)} delay` : ""}
            {" · scene is "}
            {formatTimecode(sceneDurationMs)}
          </small>
          {playedMs !== sceneDurationMs && canEdit && (
            <button type="button" onClick={() => onMatchSceneLength(playedMs)}>
              Match scene length
            </button>
          )}
        </div>
      )}
    </>
  );
}
