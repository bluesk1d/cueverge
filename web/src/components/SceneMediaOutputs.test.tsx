import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ProjectDraftRecord, SceneMediaPreview } from "../api";
import { SceneWorkspace } from "../pages/SceneWorkspace";
import { SceneMediaOutputs } from "./SceneMediaOutputs";

afterEach(() => {
  vi.restoreAllMocks();
});

const pixel = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

function mediaPreview(timestamp: number): SceneMediaPreview {
  return {
    project_id: "project-1",
    source_revision: 6,
    scene_id: "klown",
    timestamp_ms: timestamp,
    duration_ms: 10_000,
    unsaved: false,
    evaluation: "controller_media_compositor",
    hardware_commands_sent: false,
    limitations: ["Frames are rendered stills on the scene clock."],
    outputs: [
      {
        output_id: "left-window",
        resource: "projector.left",
        required: true,
        start_delay_ms: 0,
        end_behavior: "loop",
        source: { kind: "composition", composition_id: "window", name: "Window show", space_kind: "projector_native" },
        state: "playing",
        media_position_ms: timestamp,
        media_duration_ms: 8_000,
        frame: { media_type: "image/png", width: 1, height: 1, native_width: 1, native_height: 1, data_base64: pixel },
        audio: [{ asset_id: "laugh", name: "Klown laugh", source_position_ms: timestamp, gain_db: -6, mute: false }],
        message: null,
      },
      {
        output_id: "porch",
        resource: "projector.porch",
        required: false,
        start_delay_ms: 2_000,
        end_behavior: "stop",
        source: { kind: "asset", asset_id: "fog", name: "Fog loop", media_type: "video/mp4" },
        state: "waiting",
        media_position_ms: null,
        media_duration_ms: 5_000,
        frame: null,
        audio: [],
        message: null,
      },
    ],
  };
}

function mockMediaFetch() {
  const bodies: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path.endsWith("/scene-media-preview")) {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      bodies.push(body);
      return new Response(JSON.stringify(mediaPreview(Number(body.timestamp_ms))), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    }
    return new Response("{}", { status: 404 });
  });
  return bodies;
}

test("shows each media output's rendered frame, source and timing state", async () => {
  const bodies = mockMediaFetch();
  render(<SceneMediaOutputs projectId="project-1" revision={6} dirty={false} sceneId="klown"
    position={1_500} playing={false} valid assets={[]} />);

  const left = await screen.findByRole("article", { name: "Media output left-window" });
  expect(within(left).getByRole("img", { name: "left-window at the preview time" }))
    .toHaveAttribute("src", `data:image/png;base64,${pixel}`);
  expect(left).toHaveTextContent("Playing Composition: Window show · at 0:01.5 of 0:08.0");
  expect(left).toHaveTextContent("Audio: Klown laugh");
  const porch = screen.getByRole("article", { name: "Media output porch" });
  expect(porch).toHaveTextContent("Waiting for start delay");
  expect(porch).toHaveTextContent("Asset: Fog loop");
  expect(porch).toHaveTextContent("Starts 2 s after the scene.");
  expect(porch).toHaveTextContent("projector.porch · optional");
  expect(screen.getByLabelText("Play audio during preview playback (approximate)")).not.toBeChecked();
  expect(bodies[0]).toMatchObject({ source_revision: 6, scene_id: "klown", timestamp_ms: 1_500 });
});

test("media-only scenes get the combined preview and playback controls", async () => {
  const bodies = mockMediaFetch();
  const draft = {
    project: { id: "project-1", name: "Halloween", head_revision: 6, metadata_revision: 1, archived_at: null, created_at: "", updated_at: "" },
    revision: 6,
    draft: {
      schema_version: 1,
      installation: { schema_version: 1, resources: [] },
      editor_metadata: {},
      scenes: [{
        schema_version: 2, id: "klown", name: "Klown Ship", duration_ms: 10_000,
        resources: ["projector.left"], mode: "media_synchronized", clock_source: "left-window",
        media: [{ id: "left-window", resource: "projector.left", asset: null }], lighting: [],
      }],
    },
  } as unknown as ProjectDraftRecord;
  render(<SceneWorkspace draft={draft} sceneId="klown" canEdit={false} canAdd assets={[]}
    onSelectScene={vi.fn()} onSave={vi.fn()} onDirtyChange={vi.fn()}
    onOpenTimeline={vi.fn()} onDelete={vi.fn()} onCreated={vi.fn()} />);

  expect(await screen.findByRole("article", { name: "Media output left-window" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Scene lighting preview" })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Scene preview time (seconds)"), { target: { value: "4" } });
  await waitFor(() => expect(bodies.at(-1)).toMatchObject({ timestamp_ms: 4_000 }));
  expect(screen.getByRole("button", { name: "Play scene preview" })).toBeEnabled();
});
