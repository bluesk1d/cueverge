import { useEffect, useRef, useState } from "react";

import {
  assetArtifactUrl,
  AssetRecord,
  ProjectDraftRecord,
  SceneMediaPreviewOutput,
} from "../api";
import { hasAssetArtifact } from "./AssetPreview";
import { ErrorState } from "./ui";
import { useSceneMediaPreview } from "./useSceneMediaPreview";
import type { SceneMediaPreviewState } from "./useSceneMediaPreview";

type Document = ProjectDraftRecord["draft"];

const stateLabels: Record<SceneMediaPreviewOutput["state"], string> = {
  unassigned: "No media assigned",
  waiting: "Waiting for start delay",
  playing: "Playing",
  looping: "Looping",
  holding: "Holding last frame",
  stopped: "Stopped (end of media)",
  error: "Cannot preview",
};

function clock(value: number): string {
  const seconds = Math.max(0, value) / 1000;
  return `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(1).padStart(4, "0")}`;
}

/**
 * Projector/media outputs of a scene at the shared preview position. Frames come from the
 * controller's bake compositor; optional audio uses review proxies in the browser.
 */
export function SceneMediaOutputs({
  projectId, revision, document, dirty, sceneId, position, playing, valid, assets, state,
}: {
  projectId: string; revision: number; document?: Document; dirty: boolean; sceneId: string;
  position: number; playing: boolean; valid: boolean; assets: AssetRecord[];
  /** Shared preview from a parent that also draws these frames on the house photo. */
  state?: SceneMediaPreviewState;
}) {
  const own = useSceneMediaPreview({
    projectId, revision, document, dirty, sceneId, position, valid, enabled: !state,
  });
  const { preview, error, loading, retry } = state ?? own;
  const [audioEnabled, setAudioEnabled] = useState(false);

  const outputs = preview?.outputs ?? [];
  const hasAudio = outputs.some((value) => value.audio.length > 0);
  return <section className="scene-media-outputs" aria-label="Scene media preview">
    <header>
      <strong>Projectors and media</strong>
      <span>Rendered stills on the scene clock · no device commands</span>
    </header>
    {error && <ErrorState title="Media preview unavailable" description={error} />}
    {error && <button onClick={retry}>Retry media preview</button>}
    <div className="scene-media-outputs__grid">
      {outputs.map((output) => <MediaOutputCard key={output.output_id} output={output} />)}
    </div>
    {!preview && !error && <p className="scene-preview-status" aria-busy={loading}>
      {loading ? "Preparing media preview…" : "No evaluated media frame"}
    </p>}
    {preview && <p className="scene-preview-status" aria-busy={loading}>
      Evaluated {clock(preview.timestamp_ms)} · {preview.unsaved ? "unsaved edits" : "saved scene"}
    </p>}
    {hasAudio && <label className="scene-media-outputs__audio-toggle">
      <input type="checkbox" checked={audioEnabled} onChange={(event) => setAudioEnabled(event.target.checked)} />
      Play audio during preview playback (approximate)
    </label>}
    <ScenePreviewAudio enabled={audioEnabled && playing} outputs={outputs}
      evaluatedAt={preview?.timestamp_ms ?? 0} position={position} assets={assets} />
    {preview?.limitations.map((message) => <small key={message}>{message}</small>)}
  </section>;
}

function MediaOutputCard({ output }: { output: SceneMediaPreviewOutput }) {
  const source = output.source;
  const sourceLabel = source === null ? "No media assigned"
    : source.kind === "composition" ? `Composition: ${source.name}` : `Asset: ${source.name}`;
  const timing = output.media_position_ms === null ? null
    : output.media_duration_ms === null ? `at ${clock(output.media_position_ms)}`
      : `at ${clock(output.media_position_ms)} of ${clock(output.media_duration_ms)}`;
  return <article className="scene-media-output" aria-label={`Media output ${output.output_id}`}>
    <header>
      <strong>{output.output_id}</strong>
      <small>{output.resource}{output.required ? "" : " · optional"}</small>
    </header>
    <div className="scene-media-output__frame" data-state={output.state}
      style={output.frame ? { aspectRatio: `${output.frame.width} / ${output.frame.height}` } : undefined}>
      {output.frame
        ? <img alt={`${output.output_id} at the preview time`}
          src={`data:${output.frame.media_type};base64,${output.frame.data_base64}`}
          width={output.frame.width} height={output.frame.height} />
        : <span>{stateLabels[output.state]}</span>}
    </div>
    <p>
      <span className={`scene-media-output__state scene-media-output__state--${output.state}`}>
        {stateLabels[output.state]}
      </span>{" "}
      {sourceLabel}{timing ? ` · ${timing}` : ""}
    </p>
    {output.repeat_count && output.repeat_count > 1 && <small>
      {output.play_number ? `Play ${output.play_number} of ${output.repeat_count}` : `Plays ${output.repeat_count} times`}
    </small>}
    {output.start_delay_ms > 0 && <small>Starts {output.start_delay_ms / 1000} s after the scene.</small>}
    {output.audio.length > 0 && <small>
      Audio: {output.audio.map((value) => `${value.name}${value.mute ? " (muted)" : ""}`).join(", ")}
    </small>}
    {output.message && <small role={output.state === "error" ? "alert" : undefined}>{output.message}</small>}
  </article>;
}

function ScenePreviewAudio({ enabled, outputs, evaluatedAt, position, assets }: {
  enabled: boolean; outputs: SceneMediaPreviewOutput[]; evaluatedAt: number; position: number;
  assets: AssetRecord[];
}) {
  const elements = useRef(new Map<string, HTMLAudioElement>());
  useEffect(() => {
    const active = new Set<string>();
    if (enabled) {
      for (const output of outputs) {
        for (const source of output.audio) {
          const asset = assets.find((value) => value.id === source.asset_id);
          if (!asset || !hasAssetArtifact(asset, "review")) continue;
          const key = `${output.output_id}:${source.asset_id}`;
          active.add(key);
          let element = elements.current.get(key);
          if (!element) {
            element = new Audio(assetArtifactUrl(source.asset_id, "review"));
            elements.current.set(key, element);
          }
          element.volume = source.mute ? 0 : Math.min(1, 10 ** (source.gain_db / 20));
          const target = (source.source_position_ms + Math.max(0, position - evaluatedAt)) / 1000;
          if (Math.abs(element.currentTime - target) > 0.35) element.currentTime = target;
          if (element.paused) void element.play().catch(() => undefined);
        }
      }
    }
    for (const [key, element] of elements.current) {
      if (!active.has(key)) element.pause();
    }
  }, [enabled, outputs, evaluatedAt, position, assets]);
  useEffect(() => {
    const current = elements.current;
    return () => {
      for (const element of current.values()) element.pause();
      current.clear();
    };
  }, []);
  return null;
}
