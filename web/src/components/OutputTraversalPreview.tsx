import { useCallback, useEffect, useMemo, useState } from "react";

import { installationPhotoUrl } from "../api";
import type { InstallationGeometry, InstallationPhoto, PhysicalOutputTest } from "../api";
import { outputTraversal } from "./pixel-traversal";
import { PhysicalOutputTestControls } from "./PhysicalOutputTestControls";

export function OutputTraversalPreview({
  geometry,
  resources,
  resource,
  pixelCount,
  photo,
  onClose,
  canTest = false,
  installationId,
}: {
  geometry: InstallationGeometry[];
  resources: Record<string, string>;
  resource: string;
  pixelCount: number;
  photo?: InstallationPhoto;
  installationId?: string;
  onClose: () => void;
  canTest?: boolean;
}) {
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(
    () => !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const [speed, setSpeed] = useState(30);
  const [physicalOpen, setPhysicalOpen] = useState(false);
  const [physicalTest, setPhysicalTest] = useState<PhysicalOutputTest | null>(null);
  const followingPhysical = physicalTest?.resource === resource && physicalTest.state === "active";
  const syncPhysicalTest = useCallback((test: PhysicalOutputTest | null) => {
    setPhysicalTest(test);
    if (test?.resource === resource && test.state === "active") {
      setPlaying(false);
      setPosition(test.pixel_index);
    }
  }, [resource]);
  const { segments, diagnostics } = useMemo(
    () => {
      const plan = outputTraversal(geometry, resources, resource, photo?.id ?? "", pixelCount);
      return {
        ...plan,
        segments: plan.segments.map((segment) => ({
          ...segment,
          photoPoints: segment.pixels.map((pixel) => `${pixel.x * 1000},${pixel.y * 600}`).join(" "),
        })),
      };
    },
    [geometry, resources, resource, photo?.id, pixelCount],
  );
  const index = Math.floor(position);
  const active = segments.filter((segment) => index >= segment.start && index < segment.end);

  useEffect(() => {
    if (!playing) return;
    let previous = performance.now();
    const timer = window.setInterval(() => {
      const now = performance.now();
      const elapsed = now - previous;
      previous = now;
      setPosition((current) => (current + elapsed * speed / 1000) % pixelCount);
    }, 50);
    return () => window.clearInterval(timer);
  }, [playing, speed, pixelCount]);

  return (
    <section className="output-traversal" aria-label="Output traversal preview">
      <h4>Whole-output traversal: {resource}</h4>
      <p>
        Browser only; no hardware commands. Saved mappings only: save edits before
        previewing. A five-pixel pulse follows output indices from 0 to {pixelCount - 1},
        then repeats. Segment numbers and colors show their sequence and boundaries.
      </p>
      <div className="output-traversal__controls">
        <button disabled={followingPhysical} onClick={() => setPlaying((current) => !current)}>
          {playing ? "Pause output traversal" : "Play output traversal"}
        </button>
        <button disabled={followingPhysical} onClick={() => { setPlaying(false); setPosition(0); }}>
          Restart output traversal
        </button>
        <button onClick={onClose}>Close output traversal</button>
        <label>
          Traversal speed
          <select disabled={followingPhysical} value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>
            {[5, 15, 30, 60, 120].map((value) => (
              <option key={value} value={value}>{value} pixels/second</option>
            ))}
          </select>
        </label>
        <label className="output-traversal__position">
          Output pixel index
          <input
            type="range"
            min="0"
            max={pixelCount - 1}
            step="1"
            value={index}
            disabled={followingPhysical}
            onChange={(event) => {
              setPlaying(false);
              setPosition(Number(event.target.value));
            }}
          />
        </label>
      </div>
      <output aria-label="Current traversal pixel">
        Pixel {index} / {pixelCount - 1} ·{" "}
        {active.length
          ? active.map((segment) => `${segment.order}. ${segment.geometry.name}${
              !photo || segment.geometry.photo_id === null || segment.geometry.photo_id === photo.id ? "" : " (on another photo)"
            }`).join("; ")
          : "Unmapped: no layout location for this output index"}
      </output>
      {followingPhysical && <small>Following physical test: controller position sampled once per second.</small>}
      {diagnostics.length > 0 && (
        <ul className="output-traversal__diagnostics" aria-label="Traversal diagnostics">
          {diagnostics.map((message) => <li key={message}>{message}</li>)}
        </ul>
      )}
      <svg
        className="geometry-canvas output-traversal__canvas"
        role="img"
        aria-label={`Output traversal for ${resource} on ${photo?.name ?? "installation canvas"}`}
        viewBox="0 0 1000 600"
      >
        {photo && <image
          width="1000"
          height="600"
          href={installationPhotoUrl(photo.id, photo.revision)}
          preserveAspectRatio="xMidYMid meet"
        />}
        {segments.filter((segment) => !photo || segment.geometry.photo_id === null || segment.geometry.photo_id === photo.id).map((segment) => {
          const first = segment.pixels[0];
          const last = segment.pixels.at(-1)!;
          return (
            <g key={segment.geometry.id} style={{ color: segment.color }}>
              <polyline
                className="output-traversal__segment"
                points={segment.photoPoints}
              />
              <text className="output-traversal__label" x={first.x * 1000 + 8} y={first.y * 600 - 12}>
                {segment.order}. {segment.geometry.name}: {segment.start}
              </text>
              <text className="output-traversal__label" x={last.x * 1000 + 8} y={last.y * 600 + 20}>
                {segment.end - 1}
              </text>
              {segment.pixels.slice(
                Math.max(0, index - 4 - segment.start),
                Math.max(0, index + 1 - segment.start),
              ).map((pixel) => (
                <circle
                  className="output-traversal__pulse"
                  data-output-index={pixel.outputIndex}
                  data-segment-id={segment.geometry.id}
                  cx={pixel.x * 1000}
                  cy={pixel.y * 600}
                  r={pixel.outputIndex === index ? 8 : 5}
                  opacity={1 - (index - pixel.outputIndex) * 0.16}
                  key={pixel.outputIndex}
                >
                  <title>{segment.order}. {segment.geometry.name}: pixel {pixel.outputIndex}</title>
                </circle>
              ))}
            </g>
          );
        })}
      </svg>
      <ol className="output-traversal__legend" aria-label="Output segment order">
        {segments.map((segment) => (
          <li key={segment.geometry.id} aria-current={active.includes(segment) ? "step" : undefined}>
            <span className="output-traversal__swatch" style={{ background: segment.color }} />
            {segment.geometry.name} · {segment.start}-{segment.end - 1} ·{" "}
            {segment.geometry.direction === "forward" ? "first point to last" : "last point to first"}
            {photo && segment.geometry.photo_id !== null && segment.geometry.photo_id !== photo.id && " · on another photo"}
            {segment.geometry.hidden && " · hidden layer shown in preview"}
          </li>
        ))}
      </ol>
      {canTest && (
        <details onToggle={(event) => {
          setPhysicalOpen(event.currentTarget.open);
          if (!event.currentTarget.open) setPhysicalTest(null);
        }}>
          <summary>Test this output on physical LEDs</summary>
          {physicalOpen && (
            <PhysicalOutputTestControls
              canOperate={canTest}
              resource={resource}
              installationId={installationId ?? photo?.installation_id}
              initialSpeed={speed}
              onTestUpdate={syncPhysicalTest}
            />
          )}
        </details>
      )}
    </section>
  );
}
