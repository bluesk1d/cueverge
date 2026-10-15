import { useEffect, useMemo, useRef, useState } from "react";

import {
  assetArtifactUrl,
  AssetRecord,
  getInstallationMapperExport,
  getStageCoverage,
  InstallationMapperExport,
  ProjectDraftRecord,
  StageCoverage,
} from "../api";

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
  safe_points_ms: number[];
  protected_intervals: { start_ms: number; end_ms: number }[];
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
  id: string;
  name: string;
  kind: "image" | "video" | "composition";
  source_id: string;
  track_id: string;
  start_ms: number;
  duration_ms: number;
  keyframes: TransformKeyframe[];
};

type SceneOperationalPolicy = {
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
  clock_source: string;
  media_required: Record<string, boolean>;
  lighting_required: Record<string, boolean>;
  media_offsets: Record<string, number>;
  lighting_offsets: Record<string, number>;
};

type CuePreset = {
  id: string;
  name: string;
  lighting: LightingCueProperties;
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
};

export function TimelineEditor({
  draft,
  assets,
  canEdit,
  onSave,
}: {
  draft: ProjectDraftRecord;
  assets: AssetRecord[];
  canEdit: boolean;
  onSave: (draft: ProjectDraftRecord["draft"]) => Promise<void>;
}) {
  const [sceneId, setSceneId] = useState(draft.draft.scenes[0]?.id ?? "");
  const scene =
    draft.draft.scenes.find((candidate) => candidate.id === sceneId) ??
    draft.draft.scenes[0];
  const [compositions, setCompositions] = useState<Record<string, Composition>>(
    () => compositionMap(draft.draft.editor_metadata.compositions),
  );
  const [sceneCompositions, setSceneCompositions] = useState<Record<string, string[]>>(
    () => stringArrayMap(draft.draft.editor_metadata.scene_compositions),
  );
  const [compositionInstances, setCompositionInstances] = useState<
    Record<string, CompositionInstance[]>
  >(() => compositionInstanceMap(draft.draft.editor_metadata.composition_instances));
  const [activeCompositionId, setActiveCompositionId] = useState("");
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
  const duration = composition?.duration_ms ?? scene?.duration_ms ?? 1000;
  const localOffset = composition?.local_time_offset_ms ?? 0;
  const [zoom, setZoom] = useState(80);
  const [snapMs, setSnapMs] = useState(100);
  const [playhead, setPlayhead] = useState(0);
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
  const [scenePolicy, setScenePolicy] = useState<SceneOperationalPolicy>(() =>
    readSceneOperationalPolicy(scene),
  );
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
  const [historyVersion, setHistoryVersion] = useState(0);
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
    setSafePoints([
      ...(composition?.safe_points_ms ??
        scene?.scheduling?.safe_points_ms ??
        []),
    ]);
    setRegions([
      ...(composition?.protected_intervals ??
        scene?.scheduling?.protected_intervals ??
        []),
    ]);
    setRegionStart(0);
    setRegionEnd(Math.min(1000, scene?.duration_ms ?? 1000));
    setLoopStart(numberValue(sceneView.loop_start_ms, 0));
    setLoopEnd(numberValue(sceneView.loop_end_ms, scene?.duration_ms ?? 1000));
    setCustomTracks(trackArray(timelineTracks[editingSpaceId]));
    setSelectedTrackId("");
    setSelectedTransformLayerId("");
    setSelectedTransformKeyframeId("");
    setCompoundSelection([]);
  }, [
    composition?.id,
    composition?.duration_ms,
    composition?.local_time_offset_ms,
    editingSpaceId,
    scene?.id,
    scene?.duration_ms,
    sceneView.loop_end_ms,
    sceneView.loop_start_ms,
  ]);

  useEffect(() => {
    const firstLayer = transformLayers[editingSpaceId]?.[0];
    setSelectedTransformLayerId(firstLayer?.id ?? "");
    setSelectedTransformKeyframeId(firstLayer?.keyframes[0]?.id ?? "");
  }, [editingSpaceId]);

  useEffect(() => {
    setScenePolicy(readSceneOperationalPolicy(scene));
  }, [scene?.id]);

  useEffect(() => {
    if (restoringHistoryRef.current) {
      restoringHistoryRef.current = false;
      return;
    }
    const snapshot: TimelineSnapshot = {
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
    };
    const serialized = JSON.stringify(snapshot);
    const current = historyRef.current[historyIndexRef.current];
    if (current && JSON.stringify(current) === serialized) {
      return;
    }
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
  const selectedTrack = tracks.find((track) => track.id === selectedTrackId);
  const parsedInstanceOverrides = parseObject(instanceOverrides);
  const markers = syncMarkers[editingSpaceId] ?? [];
  const cues = syncCues[editingSpaceId] ?? [];
  const spaceTransformLayers = transformLayers[editingSpaceId] ?? [];
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
          duration_ms: candidate.duration_ms ?? 1000,
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
      syncCues,
      syncMarkers,
    ],
  );

  function snapped(value: number): number {
    return Math.max(
      0,
      Math.min(
        scene?.duration_ms ?? duration,
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
      },
    });
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
    if (activeCompositionId === compositionId) {
      setActiveCompositionId(replacementId ?? "");
    }
    setReplacementByComposition((current) => {
      const updated = { ...current };
      delete updated[compositionId];
      return updated;
    });
  }

  if (!scene) {
    return null;
  }

  return (
    <details className="timeline-editor">
      <summary>Timeline editor</summary>
      <div className="timeline-toolbar">
        <label>
          Scene
          <select
            value={scene.id}
            onChange={(event) => {
              setSceneId(event.target.value);
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
        <label>
          Editing space
          <select
            value={activeCompositionId}
            onChange={(event) => setActiveCompositionId(event.target.value)}
          >
            <option value="">Scene root · {scene.name}</option>
            {Object.values(compositions).map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                Composition · {candidate.name}
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
        <span>
          Global {playhead} ms
          {composition ? ` · Local ${localPlayhead} ms` : ""}
        </span>
        <label>
          Scrub position
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
        {canEdit && (
          <>
            <button
              disabled={historyVersion < 0 || historyIndexRef.current <= 0}
              onClick={() => restoreHistory(historyIndexRef.current - 1)}
            >
              Undo timeline edit
            </button>
            <button
              disabled={
                historyIndexRef.current < 0 ||
                historyIndexRef.current >= historyRef.current.length - 1
              }
              onClick={() => restoreHistory(historyIndexRef.current + 1)}
            >
              Redo timeline edit
            </button>
            <button
              onClick={() =>
                setSafePoints((current) =>
                  [...new Set([...current, snappedLocal(localPlayhead)])].sort(
                    (left, right) => left - right,
                  ),
                )
              }
            >
              Add marker
            </button>
            <button
              disabled={timelineDiagnostics.length > 0}
              onClick={() => void save()}
            >
              Save timeline
            </button>
          </>
        )}
      </div>
      <output className="timeline-evaluated-state" aria-label="Timeline evaluated state">
        {JSON.stringify(evaluatedTimelineState)}
      </output>
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
      {composition?.space_kind === "stage" && stageCoverage && mapperExport && (
        <StageCompositionPreview
          composition={composition}
          coverage={stageCoverage}
          mapperExport={mapperExport}
          selectedKeyframe={selectedTransformKeyframe}
          selectedLayer={selectedTransformLayer}
          canEdit={canEdit}
          snapMode={spatialSnapMode}
          onSnapModeChange={setSpatialSnapMode}
          onMoveKeyframe={(position) => {
            if (selectedTransformLayer && selectedTransformKeyframe) {
              updateTransformKeyframe(
                selectedTransformLayer.id,
                selectedTransformKeyframe.id,
                (keyframe) => ({ ...keyframe, position }),
              );
            }
          }}
        />
      )}
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
          <div className="timeline-ruler">
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
          {tracks.map((track) => {
            const asset = timelineTrackAsset(scene, track, assets);
            const showThumbnail =
              track.kind === "media_output" &&
              asset &&
              hasAssetArtifact(asset, "thumbnail");
            const showWaveform =
              (track.kind === "media_output" ||
                track.kind === "audio_reference") &&
              asset &&
              hasAssetArtifact(asset, "waveform");
            return (
              <div
              className={`timeline-track${
                track.id === selectedTrackId ? " timeline-track--selected" : ""
              }`}
              key={track.id}
              >
                <button
                  className="timeline-track__label"
                  onClick={(event) => {
                    event.stopPropagation();
                    setSelectedTrackId(track.id);
                  }}
                >
                  {humanizeTrackKind(track.kind)} · {track.name}
                </button>
                {(track.kind === "media_output" ||
                  track.kind === "audio_reference") && (
                  <div
                    aria-label={`${track.name} timing reference`}
                    className="timeline-track-reference"
                  >
                    {showThumbnail && (
                      <img
                        alt={`Timeline thumbnails for ${asset.name}`}
                        className="timeline-track-reference__thumbnail"
                        src={assetArtifactUrl(asset.id, "thumbnail")}
                      />
                    )}
                    {showWaveform && (
                      <img
                        alt={`Timeline waveform for ${asset.name}`}
                        className="timeline-track-reference__waveform"
                        src={assetArtifactUrl(asset.id, "waveform")}
                      />
                    )}
                    {!showThumbnail && !showWaveform && (
                      <small>
                        {asset
                          ? "Timing derivative unavailable"
                          : "No asset bound for timing reference"}
                      </small>
                    )}
                  </div>
                )}
                {spaceTransformLayers
                  .filter((layer) => layer.track_id === track.id)
                  .map((layer) => (
                    <button
                      aria-label={`Transform layer ${layer.name}`}
                      className={`timeline-transform-layer${
                        timelineDiagnostics.some(
                          (diagnostic) => diagnostic.object_id === layer.id,
                        )
                          ? " timeline-item--invalid"
                          : ""
                      }`}
                      key={layer.id}
                      style={{
                        left: `${(layer.start_ms * 100) / duration}%`,
                        width: `${(layer.duration_ms * 100) / duration}%`,
                      }}
                      onClick={(event) => {
                        event.stopPropagation();
                        setSelectedTransformLayerId(layer.id);
                        setSelectedTransformKeyframeId(
                          layer.keyframes[0]?.id ?? "",
                        );
                      }}
                    >
                      {layer.keyframes.map((keyframe) => (
                        <span
                          className="timeline-transform-keyframe"
                          key={keyframe.id}
                          style={{
                            left: `${(keyframe.time_ms * 100) / layer.duration_ms}%`,
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
                      }}
                    />
                  );
                })}
              </div>
            );
          })}
          {markers.map((marker) => (
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
              className="timeline-marker"
              key={point}
              style={{ left: `${(point * 100) / duration}%` }}
              title="Click to remove marker"
              onClick={(event) => {
                event.stopPropagation();
                if (canEdit) {
                  setSafePoints((current) => current.filter((value) => value !== point));
                }
              }}
            />
          ))}
          <span
            className="timeline-playhead"
            style={{ left: `${(localPlayhead * 100) / duration}%` }}
          />
        </div>
      </div>
      {canEdit && (
        <div className="timeline-region-controls">
          <label>
            Protected start
            <input
              max={duration}
              min="0"
              step={snapMs}
              type="number"
              value={regionStart}
              onChange={(event) =>
                setRegionStart(snappedLocal(Number(event.target.value)))
              }
            />
          </label>
          <label>
            Protected end
            <input
              max={duration}
              min="0"
              step={snapMs}
              type="number"
              value={regionEnd}
              onChange={(event) =>
                setRegionEnd(snappedLocal(Number(event.target.value)))
              }
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
            <input
              max={duration}
              min="0"
              step={snapMs}
              type="number"
              value={loopStart}
              onChange={(event) =>
                setLoopStart(snappedLocal(Number(event.target.value)))
              }
            />
          </label>
          <label>
            Loop end
            <input
              max={duration}
              min="0"
              step={snapMs}
              type="number"
              value={loopEnd}
              onChange={(event) =>
                setLoopEnd(snappedLocal(Number(event.target.value)))
              }
            />
          </label>
        </div>
      )}
      {!composition && (
        <section className="timeline-scene-policy">
          <strong>Scene scheduling and output policy</strong>
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
                Clock source
                <select
                  aria-label="Clock source"
                  disabled={!canEdit}
                  value={scenePolicy.clock_source}
                  onChange={(event) =>
                    setScenePolicy((current) => ({
                      ...current,
                      clock_source: event.target.value,
                    }))
                  }
                >
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
      <section className="timeline-sync-controls">
        <strong>Synchronization anchors</strong>
        <small>
          Named markers move linked cues atomically. Cue offsets remain explicit,
          and detached cues keep their evaluated local time.
        </small>
        {markers.map((marker) => {
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
              <input
                aria-label={`Sync marker time ${marker.name}`}
                disabled={!canEdit}
                max={bounds.maximum}
                min={bounds.minimum}
                step={snapMs}
                type="number"
                value={marker.time_ms}
                onChange={(event) =>
                  setSyncMarkers((current) => ({
                    ...current,
                    [editingSpaceId]: markers.map((candidate) =>
                      candidate.id === marker.id
                        ? {
                            ...candidate,
                            time_ms: Math.max(
                              bounds.minimum,
                              Math.min(
                                bounds.maximum,
                                snappedLocal(Number(event.target.value)),
                              ),
                            ),
                          }
                        : candidate,
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
        })}
        {canEdit && (
          <div className="timeline-sync-create">
            <input
              aria-label="New sync marker name"
              placeholder="Named synchronization marker"
              value={newMarkerName}
              onChange={(event) => setNewMarkerName(event.target.value)}
            />
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
                    <input
                      aria-label={`Lighting cue start ${cue.name}`}
                      disabled={!canEdit}
                      max={duration - cue.lighting.duration_ms}
                      min="0"
                      step={snapMs}
                      type="number"
                      value={effectiveTime}
                      onChange={(event) => {
                        const start = snappedLocal(Number(event.target.value));
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
            <input
              aria-label="New synchronized cue name"
              placeholder="Cue name"
              value={newCueName}
              onChange={(event) => setNewCueName(event.target.value)}
            />
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
            <select
              aria-label="New synchronized cue marker"
              value={newCueMarkerId}
              onChange={(event) => setNewCueMarkerId(event.target.value)}
            >
              <option value="">Detached at playhead</option>
              {markers.map((marker) => (
                <option key={marker.id} value={marker.id}>
                  {marker.name}
                </option>
              ))}
            </select>
            <input
              aria-label="New synchronized cue marker offset"
              disabled={!newCueMarkerId}
              step={snapMs}
              type="number"
              value={newCueOffset}
              onChange={(event) => setNewCueOffset(Number(event.target.value))}
            />
            {newCueKind === "lighting" && (
              <input
                aria-label="New lighting cue duration"
                min="1"
                step={snapMs}
                type="number"
                value={newCueDuration}
                onChange={(event) => setNewCueDuration(Number(event.target.value))}
              />
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
          <strong>Reusable lighting cue presets</strong>
          <input
            aria-label="New cue preset name"
            disabled={!canEdit}
            placeholder="Preset name"
            value={newPresetName}
            onChange={(event) => setNewPresetName(event.target.value)}
          />
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
          <strong>Compound selection</strong>
          {spaceTransformLayers.map((layer) => (
            <label key={layer.id}>
              <input
                aria-label={`Select transform layer ${layer.name}`}
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
          <input
            aria-label="Compound shift milliseconds"
            step={snapMs}
            type="number"
            value={compoundShiftMs}
            onChange={(event) => setCompoundShiftMs(Number(event.target.value))}
          />
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
      <section className="timeline-transform-editor">
        <strong>2D transform layers</strong>
        <small>
          Image, video, and nested-composition transforms are authored in the
          active logical editing space.
        </small>
        <small className="timeline-coordinate-space">
          Authored once in{" "}
          {composition
            ? `${humanizeSpace(composition.space_kind)} · target ${composition.target_id} · ${composition.width}×${composition.height}`
            : `scene root · ${scene.id}`}
          . Static projector calibration and cross-surface handoff remain
          separate from animation keyframes.
        </small>
        <div className="timeline-keyframe-boundary">
          <strong>Deterministic property animation boundary</strong>
          <small>
            Runtime-keyed: position, independent X/Y scale, rotation, anchor,
            opacity, and crop/reveal bounds.
          </small>
          <small>
            Cue-static only: lighting primitive, target, colors, brightness,
            blend, precedence, seed, and primitive parameters. Unsupported
            property animation is not previewed or saved.
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
                aria-label="Transform layer name"
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
              <input
                aria-label="Transform layer start"
                disabled={!canEdit}
                max={duration - selectedTransformLayer.duration_ms}
                min="0"
                step={snapMs}
                type="number"
                value={selectedTransformLayer.start_ms}
                onChange={(event) =>
                  updateTransformLayer(selectedTransformLayer.id, (layer) => ({
                    ...layer,
                    start_ms: snappedLocal(Number(event.target.value)),
                  }))
                }
              />
            </label>
            <label>
              Layer duration
              <input
                aria-label="Transform layer duration"
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
            {canEdit && (
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
                <NumberVectorEditor
                  disabled={!canEdit}
                  label="Position"
                  value={selectedTransformKeyframe.position}
                  onChange={(value) =>
                    updateTransformKeyframe(
                      selectedTransformLayer.id,
                      selectedTransformKeyframe.id,
                      (keyframe) => ({
                        ...keyframe,
                        position: value as [number, number],
                      }),
                    )
                  }
                />
                <NumberVectorEditor
                  disabled={!canEdit}
                  label="Scale"
                  value={selectedTransformKeyframe.scale}
                  onChange={(value) =>
                    updateTransformKeyframe(
                      selectedTransformLayer.id,
                      selectedTransformKeyframe.id,
                      (keyframe) => ({
                        ...keyframe,
                        scale: value as [number, number],
                      }),
                    )
                  }
                />
                <label>
                  Rotation
                  <input
                    aria-label="Rotation"
                    disabled={!canEdit}
                    step="any"
                    type="number"
                    value={selectedTransformKeyframe.rotation_deg}
                    onChange={(event) =>
                      updateTransformKeyframe(
                        selectedTransformLayer.id,
                        selectedTransformKeyframe.id,
                        (keyframe) => ({
                          ...keyframe,
                          rotation_deg: Number(event.target.value),
                        }),
                      )
                    }
                  />
                </label>
                <NumberVectorEditor
                  disabled={!canEdit}
                  label="Anchor"
                  value={selectedTransformKeyframe.anchor}
                  onChange={(value) =>
                    updateTransformKeyframe(
                      selectedTransformLayer.id,
                      selectedTransformKeyframe.id,
                      (keyframe) => ({
                        ...keyframe,
                        anchor: value as [number, number],
                      }),
                    )
                  }
                />
                <label>
                  Opacity
                  <input
                    aria-label="Opacity"
                    disabled={!canEdit}
                    max="1"
                    min="0"
                    step="0.01"
                    type="number"
                    value={selectedTransformKeyframe.opacity}
                    onChange={(event) =>
                      updateTransformKeyframe(
                        selectedTransformLayer.id,
                        selectedTransformKeyframe.id,
                        (keyframe) => ({
                          ...keyframe,
                          opacity: Number(event.target.value),
                        }),
                      )
                    }
                  />
                </label>
                <NumberVectorEditor
                  disabled={!canEdit}
                  label="Crop"
                  max={1}
                  min={0}
                  value={selectedTransformKeyframe.crop}
                  onChange={(value) =>
                    updateTransformKeyframe(
                      selectedTransformLayer.id,
                      selectedTransformKeyframe.id,
                      (keyframe) => ({
                        ...keyframe,
                        crop: value as [number, number, number, number],
                      }),
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
        {canEdit && (
          <div className="timeline-transform-create">
            <input
              aria-label="New transform layer name"
              placeholder="Layer name"
              value={newTransformName}
              onChange={(event) => setNewTransformName(event.target.value)}
            />
            <select
              aria-label="New transform layer kind"
              value={newTransformKind}
              onChange={(event) =>
                setNewTransformKind(event.target.value as TransformLayer["kind"])
              }
            >
              <option value="image">Image</option>
              <option value="video">Video</option>
              <option value="composition">Nested composition</option>
            </select>
            <input
              aria-label="New transform layer source"
              placeholder="Asset or composition ID"
              value={newTransformSource}
              onChange={(event) => setNewTransformSource(event.target.value)}
            />
            <select
              aria-label="New transform layer track"
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
            <input
              aria-label="New transform layer duration"
              min="1"
              step={snapMs}
              type="number"
              value={newTransformDuration}
              onChange={(event) =>
                setNewTransformDuration(Number(event.target.value))
              }
            />
            <button
              disabled={
                !newTransformName.trim() ||
                !newTransformSource.trim() ||
                !newTransformTrack ||
                newTransformDuration <= 0 ||
                localPlayhead + newTransformDuration > duration
              }
              onClick={() => {
                const layer: TransformLayer = {
                  id: crypto.randomUUID(),
                  name: newTransformName.trim(),
                  kind: newTransformKind,
                  source_id: newTransformSource.trim(),
                  track_id: newTransformTrack,
                  start_ms: localPlayhead,
                  duration_ms: newTransformDuration,
                  keyframes: [defaultTransformKeyframe(0)],
                };
                setTransformLayers((current) => ({
                  ...current,
                  [editingSpaceId]: [...spaceTransformLayers, layer],
                }));
                setSelectedTransformLayerId(layer.id);
                setSelectedTransformKeyframeId(layer.keyframes[0].id);
                setNewTransformName("");
              }}
            >
              Add transform layer
            </button>
          </div>
        )}
      </section>
      <div className="timeline-track-properties">
        <strong>Track properties</strong>
        {selectedTrack ? (
          <>
            <small>Stable ID: {selectedTrack.id}</small>
            <small>Kind: {humanizeTrackKind(selectedTrack.kind)}</small>
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
          <small>Select a track to inspect its stable identity and binding.</small>
        )}
        {canEdit && (
          <div className="timeline-track-create">
            <select
              aria-label="New track kind"
              value={newTrackKind}
              onChange={(event) =>
                setNewTrackKind(event.target.value as typeof newTrackKind)
              }
            >
              <option value="audio_reference">Audio reference</option>
              <option value="fixture">Fixture</option>
              <option value="group">Group</option>
              <option value="path">Path</option>
            </select>
            <input
              aria-label="New track name"
              placeholder="Track name"
              value={newTrackName}
              onChange={(event) => setNewTrackName(event.target.value)}
            />
            <input
              aria-label="New track resource binding"
              placeholder="Resource / fixture / group / path ID"
              value={newTrackBinding}
              onChange={(event) => setNewTrackBinding(event.target.value)}
            />
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
                setNewTrackName("");
                setNewTrackBinding("");
              }}
            >
              Add track
            </button>
          </div>
        )}
      </div>
      <div className="composition-hierarchy">
        <strong>Composition hierarchy</strong>
        {Object.values(compositions).length ? (
          <ul>
            {Object.values(compositions).map((candidate) => {
              const usage = compositionUsage(
                compositions,
                sceneCompositions,
                compositionInstances,
                candidate.id,
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
                  {canEdit && usage.length === 0 && (
                    <button
                      className="danger-button"
                      onClick={() => removeComposition(candidate.id)}
                    >
                      Delete unused
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
          </div>
        ))}
        {canEdit && (
          <div className="composition-create">
            <input
              aria-label="New composition name"
              placeholder="Composition name"
              value={newCompositionName}
              onChange={(event) => setNewCompositionName(event.target.value)}
            />
            <input
              aria-label="New composition duration"
              min="1"
              type="number"
              value={newCompositionDuration}
              onChange={(event) =>
                setNewCompositionDuration(Number(event.target.value))
              }
            />
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
                const id = crypto.randomUUID();
                setCompositions((current) => ({
                  ...current,
                  [id]: {
                    id,
                    name: newCompositionName,
                    duration_ms: newCompositionDuration,
                    space_kind: newCompositionSpace,
                    target_id: newCompositionTarget,
                    width: newCompositionWidth,
                    height: newCompositionHeight,
                    local_time_offset_ms: newCompositionOffset,
                    children: [],
                    safe_points_ms: [],
                    protected_intervals: [],
                  },
                }));
                setActiveCompositionId(id);
                setNewCompositionName("");
                setNewCompositionTarget("");
              }}
            >
              Create composition
            </button>
            <select
              aria-label="New composition space"
              value={newCompositionSpace}
              onChange={(event) =>
                setNewCompositionSpace(
                  event.target.value as Composition["space_kind"],
                )
              }
            >
              <option value="projector_native">Projector native pixels</option>
              <option value="lighting_surface">Lighting surface local</option>
              <option value="stage">Shared stage space</option>
            </select>
            <input
              aria-label="New composition target"
              placeholder="projector.left / surface ID / shared-stage"
              value={newCompositionTarget}
              onChange={(event) => setNewCompositionTarget(event.target.value)}
            />
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
            <input
              aria-label="New composition local offset"
              min="0"
              type="number"
              value={newCompositionOffset}
              onChange={(event) =>
                setNewCompositionOffset(Number(event.target.value))
              }
            />
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
              Add child to open composition
            </button>
            <input
              aria-label="Instance start offset"
              min="0"
              type="number"
              value={instanceStartOffset}
              onChange={(event) =>
                setInstanceStartOffset(Number(event.target.value))
              }
            />
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
            <input
              aria-label="Instance stage X"
              step="any"
              type="number"
              value={instanceX}
              onChange={(event) => setInstanceX(Number(event.target.value))}
            />
            <input
              aria-label="Instance stage Y"
              step="any"
              type="number"
              value={instanceY}
              onChange={(event) => setInstanceY(Number(event.target.value))}
            />
            <textarea
              aria-label="Instance parameter overrides"
              rows={2}
              value={instanceOverrides}
              onChange={(event) => setInstanceOverrides(event.target.value)}
            />
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
              Instantiate in scene
            </button>
          </div>
        )}
      </div>
    </details>
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
}): TimelineDiagnostic[] {
  const diagnostics: TimelineDiagnostic[] = [];
  const add = (code: string, message: string, objectId: string) =>
    diagnostics.push({ code, message, object_id: objectId });
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
  if (scene?.schema_version === 2) {
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
  } else if (typeof sceneRecord.clock_source === "string") {
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
  for (const layer of layers) {
    if (!trackIds.has(layer.track_id)) {
      add("transform_track_unknown", "Transform layer track binding does not exist.", layer.id);
    }
    if (
      layer.start_ms < 0 ||
      layer.duration_ms <= 0 ||
      layer.start_ms + layer.duration_ms > duration
    ) {
      add("transform_interval_invalid", "Transform layer interval leaves the editing space.", layer.id);
    }
    const times = layer.keyframes.map((keyframe) => keyframe.time_ms);
    if (new Set(times).size !== times.length) {
      add("keyframe_time_duplicate", "Transform keyframe times must be unique.", layer.id);
    }
    for (const keyframe of layer.keyframes) {
      if (keyframe.time_ms < 0 || keyframe.time_ms > layer.duration_ms) {
        add("keyframe_time_invalid", "Keyframe time leaves its transform layer.", keyframe.id);
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
    return updated;
  }
  return {
    ...updated,
    clock_source: policy.clock_source,
    media: scene.media.map((output) => {
      const record = metadataRecord(output);
      return {
        ...output,
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

function StageCompositionPreview({
  composition,
  coverage,
  mapperExport,
  selectedLayer,
  selectedKeyframe,
  canEdit,
  snapMode,
  onSnapModeChange,
  onMoveKeyframe,
}: {
  composition: Composition;
  coverage: StageCoverage;
  mapperExport: InstallationMapperExport;
  selectedLayer: TransformLayer | undefined;
  selectedKeyframe: TransformKeyframe | undefined;
  canEdit: boolean;
  snapMode: "none" | "landmarks" | "surfaces" | "all";
  onSnapModeChange: (mode: "none" | "landmarks" | "surfaces" | "all") => void;
  onMoveKeyframe: (position: [number, number]) => void;
}) {
  const minimumX = coverage.minimum_x;
  const minimumY = coverage.minimum_y;
  const width = coverage.maximum_x - minimumX || composition.width;
  const height = coverage.maximum_y - minimumY || composition.height;
  const screenPoint = ([x, y]: [number, number]): [number, number] => [
    ((x - minimumX) / width) * 800,
    ((y - minimumY) / height) * 400,
  ];
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
      <svg
        aria-label="Stage composition overlay"
        role="img"
        viewBox="0 0 800 400"
        onClick={(event) => {
          if (!canEdit || !selectedKeyframe) {
            return;
          }
          const bounds = event.currentTarget.getBoundingClientRect();
          const raw: [number, number] = [
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
        {coverage.regions.map((region, index) => (
          <polygon
            key={`${region.kind}:${index}`}
            className={`stage-region stage-region--${region.kind}`}
            points={region.points.map(point).join(" ")}
          />
        ))}
        {mapperExport.zones.map((zone) => (
          <polygon
            key={zone.semantic_id}
            className={`stage-composition-zone stage-composition-zone--${zone.kind}`}
            points={zone.stage_points.map(point).join(" ")}
          >
            <title>{zone.name}</title>
          </polygon>
        ))}
        {mapperExport.lighting_surfaces.flatMap((surface) =>
          (surface.pixels ?? []).map((pixel) => (
            <circle
              key={`${surface.semantic_id}:${pixel.output_index}`}
              className="stage-composition-pixel"
              cx={((pixel.stage_x - minimumX) / width) * 800}
              cy={((pixel.stage_y - minimumY) / height) * 400}
              r="3"
            >
              <title>
                {surface.resource} pixel {pixel.output_index}
              </title>
            </circle>
          )),
        )}
        {motionPath && (
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
        Projector coverage · lighting pixels · audience zones · safety/no-output
        zones
      </small>
      {warnings.length > 0 && (
        <ul className="stage-motion-warnings" aria-label="Motion path warnings">
          {warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
    </section>
  );
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
            return typeof layer.id === "string" &&
              typeof layer.name === "string" &&
              (kind === "image" ||
                kind === "video" ||
                kind === "composition") &&
              typeof layer.source_id === "string" &&
              typeof layer.track_id === "string" &&
              typeof layer.start_ms === "number" &&
              typeof layer.duration_ms === "number" &&
              keyframes.length
              ? [
                  {
                    id: layer.id,
                    name: layer.name,
                    kind,
                    source_id: layer.source_id,
                    track_id: layer.track_id,
                    start_ms: layer.start_ms,
                    duration_ms: layer.duration_ms,
                    keyframes,
                  },
                ]
              : [];
          })
        : [],
    ]),
  );
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
    for (const rootId of sceneCompositions[scene.id] ?? []) {
      const root = compositions[rootId];
      if (root) {
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
            safe_points_ms: numberArray(composition.safe_points_ms),
            protected_intervals: intervalArray(
              composition.protected_intervals,
            ),
          },
        ];
      }),
  );
}

function stringArrayMap(value: unknown): Record<string, string[]> {
  const record = metadataRecord(value);
  return Object.fromEntries(
    Object.entries(record).map(([key, item]) => [key, stringArray(item)]),
  );
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
  compositionId: string,
): string[] {
  const parents = Object.values(compositions)
    .filter((composition) => composition.children.includes(compositionId))
    .map((composition) => `composition ${composition.name}`);
  const sceneUsage = Object.entries(scenes)
    .filter(([, ids]) => ids.includes(compositionId))
    .map(([sceneId]) => `scene ${sceneId}`);
  const instanceUsage = Object.entries(instances).flatMap(
    ([sceneId, sceneInstances]) =>
      sceneInstances
        .filter((instance) => instance.composition_id === compositionId)
        .map((instance) => `scene ${sceneId} instance ${instance.id}`),
  );
  return [...new Set([...parents, ...sceneUsage, ...instanceUsage])];
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
  return value.replaceAll("_", " ");
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
