import { expect, test } from "@playwright/test";
import { openAdvancedShowWorkspace } from "./show-workspace";

let savedDraft: Record<string, unknown> | null = null;

test("transform layers persist independent axes crop and Bezier controls", async ({
  page,
}) => {
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/projects/demo-project/assets")) {
      return route.fulfill({ json: [] });
    }
    if (url.pathname.endsWith("/projects/demo-project/draft")) {
      if (request.method() === "PUT") {
        savedDraft = request.postDataJSON();
        return route.fulfill({ json: response(2, savedDraft) });
      }
      return route.fulfill({
        json: response(savedDraft ? 2 : 1, savedDraft ?? draft()),
      });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openAdvancedShowWorkspace(page);
  await page.getByText("Timeline editor", { exact: true }).click();
  await expect(
    page.getByText(/Runtime-keyed: position, independent X\/Y scale/),
  ).toBeVisible();
  await expect(
    page.getByText(/Cue-static only: lighting primitive/),
  ).toBeVisible();

  await page.getByLabel("New transform layer name").fill("Flying ghost");
  await page.getByLabel("New transform layer kind").selectOption("video");
  await page.getByLabel("New transform layer source").fill("ghost-video");
  await page
    .getByLabel("New transform layer track")
    .selectOption("media:left-window");
  await page.getByLabel("New transform layer duration").fill("1000");
  await page.getByRole("button", { name: "Add transform layer" }).click();
  await expect(
    page.getByRole("button", {
      name: "Transform layer Flying ghost",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByLabel("Source alpha interpretation")
    .selectOption("premultiplied");
  await page.getByLabel("Source color space").selectOption("bt709");
  await page.getByLabel("Source scaling behavior").selectOption("fill");

  await page.getByLabel("Position 1").fill("100");
  await page.getByLabel("Position 2").fill("50");
  await page.getByLabel("Position 1").fill("200");
  await page.getByRole("button", { name: "Undo timeline edit" }).click();
  await expect(page.getByLabel("Position 1")).toHaveValue("100");
  await page.getByRole("button", { name: "Redo timeline edit" }).click();
  await expect(page.getByLabel("Position 1")).toHaveValue("200");
  await page.getByLabel("Scale 1").fill("0");
  await expect(page.getByLabel("Timeline validation")).toContainText(
    "Independent X/Y scale cannot be zero.",
  );
  await expect(page.getByRole("button", { name: "Save timeline" })).toBeDisabled();
  await page.getByLabel("Scale 1").fill("1.5");
  await expect(page.getByLabel("Timeline validation")).toContainText(
    "Timeline valid",
  );
  await expect(page.getByRole("button", { name: "Save timeline" })).toBeEnabled();
  await page.getByLabel("Scale 2").fill("0.75");
  await page.getByLabel("Rotation").fill("30");
  await page.getByLabel("Anchor 1").fill("0.25");
  await page.getByLabel("Anchor 2").fill("0.75");
  await page.getByLabel("Opacity").fill("0.6");
  await page.getByLabel("Crop 1").fill("0.1");
  await page.getByLabel("Crop 3").fill("0.1");
  await page.getByLabel("Transform interpolation").selectOption("bezier");
  await page.getByLabel("Temporal Bezier 1").fill("0.2");
  await page.getByLabel("Temporal Bezier 2").fill("0.3");
  await page.getByLabel("Spatial in 1").fill("-25");
  await page.getByLabel("Spatial out 1").fill("25");

  const timeline = page.getByLabel("Timeline canvas");
  await timeline.focus();
  for (let index = 0; index < 5; index += 1) {
    await page.keyboard.press("ArrowRight");
  }
  await page.getByRole("button", { name: "Add keyframe at playhead" }).click();
  await page.getByLabel("Position 1").fill("300");
  await page.getByLabel("Position 2").fill("150");
  await page.getByLabel("Transform interpolation").selectOption("ease_out");
  await page.getByLabel("Select transform layer Flying ghost").check();
  await page.getByLabel("Compound shift milliseconds").fill("250");
  await page.getByRole("button", { name: "Shift selected" }).click();
  await page.getByRole("button", { name: "Duplicate selected" }).click();
  await expect(
    page.getByRole("button", { name: "Transform layer Flying ghost copy" }),
  ).toBeVisible();
  await page.getByLabel("Scrub position").fill("500");
  const firstEvaluation = await page
    .getByLabel("Timeline evaluated state")
    .textContent();
  await page.getByLabel("Scrub position").fill("700");
  await page.getByLabel("Scrub position").fill("500");
  await expect(page.getByLabel("Timeline evaluated state")).toHaveText(
    firstEvaluation!,
  );
  await expect(
    page.getByRole("button", { name: "Keyframe lighting properties" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Time-remap nested composition" }),
  ).toBeDisabled();
  await page.getByLabel("New transform layer name").fill("Narration clip");
  await page.getByLabel("New transform layer kind").selectOption("audio");
  await page.getByLabel("New transform layer source").fill("audio-asset");
  await page
    .getByLabel("New transform layer track")
    .selectOption("media:left-window");
  await page.getByLabel("New transform layer duration").fill("800");
  await page.getByRole("button", { name: "Add transform layer" }).click();
  await page.getByLabel("Layer source in").fill("100");
  await page.getByLabel("Layer visual order").fill("3");
  await page.getByLabel("Layer fade in").fill("100");
  await page.getByLabel("Layer fade out").fill("150");
  await page.getByLabel("Audio gain").fill("-6");
  await page.getByLabel("Audio mute").check();
  await page.getByLabel("Audio mix order").fill("0");
  await page.getByRole("button", { name: "Save timeline" }).click();

  await expect.poll(() => savedDraft).not.toBeNull();
  const saved = savedDraft as ReturnType<typeof draft>;
  const layer = saved.editor_metadata.timeline_transform_layers.ambient[0];
  expect(layer).toMatchObject({
    name: "Flying ghost",
    kind: "video",
    source_id: "ghost-video",
    track_id: "media:left-window",
    start_ms: 250,
    duration_ms: 1000,
    alpha_interpretation: "premultiplied",
    source_color_space: "bt709",
    scaling_behavior: "fill",
  });
  expect(layer.keyframes).toHaveLength(2);
  expect(layer.keyframes[0]).toMatchObject({
    time_ms: 0,
    position: [200, 50],
    scale: [1.5, 0.75],
    rotation_deg: 30,
    anchor: [0.25, 0.75],
    opacity: 0.6,
    crop: [0.1, 0, 0.1, 0],
    interpolation: {
      kind: "bezier",
      temporal_bezier: [0.2, 0.3, 0.25, 1],
      spatial_in: [-25, 0],
      spatial_out: [25, 0],
    },
  });
  expect(layer.keyframes[1]).toMatchObject({
    time_ms: 500,
    position: [300, 150],
    interpolation: {
      kind: "ease_out",
      temporal_bezier: null,
      spatial_in: null,
      spatial_out: null,
    },
  });
  expect(saved.editor_metadata.timeline_transform_layers.ambient).toHaveLength(3);
  expect(
    saved.editor_metadata.timeline_transform_layers.ambient.find(
      (candidate) => candidate.name === "Narration clip",
    ),
  ).toMatchObject({
    layer_schema_version: 1,
    kind: "audio",
    source_id: "audio-asset",
    start_ms: 500,
    duration_ms: 800,
    in_point_ms: 100,
    out_point_ms: 900,
    z_index: 3,
    fade_in_ms: 100,
    fade_out_ms: 150,
    audio: {
      gain_db: -6,
      mute: true,
      solo: false,
      mix_order: 0,
    },
    keyframes: [],
  });

  await page.reload();
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openAdvancedShowWorkspace(page);
  await page.getByText("Timeline editor", { exact: true }).click();
  await expect(
    page.getByRole("button", {
      name: "Transform layer Flying ghost",
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByLabel("Transform layer start")).toHaveValue("250");
});

function response(revision: number, document: Record<string, unknown>) {
  return {
    project: {
      id: "demo-project",
      name: "Home Show",
      head_revision: revision,
      metadata_revision: 1,
      archived_at: null,
      created_at: "2026-10-14T12:00:00Z",
      updated_at: "2026-10-14T12:00:00Z",
    },
    revision,
    draft: document,
  };
}

function draft() {
  return {
    schema_version: 1,
    installation: { schema_version: 1, resources: [] },
    editor_metadata: {
      timeline_transform_layers: {
        ambient: [] as Array<{
          name: string;
          kind: string;
          source_id: string;
          track_id: string;
          start_ms: number;
          duration_ms: number;
          keyframes: Array<Record<string, unknown>>;
        }>,
      },
    },
    scenes: [
      {
        schema_version: 2,
        id: "ambient",
        name: "Ambient",
        mode: "media_synchronized",
        resources: ["projector.left", "wled-001"],
        duration_ms: 5000,
        clock_source: "left-window",
        media: [
          {
            id: "left-window",
            resource: "projector.left",
            asset: null,
            required: true,
            start_delay_ms: 0,
          },
        ],
        lighting: [
          {
            id: "roofline",
            resource: "wled-001",
            required: true,
            effect: "timeline",
            timeline: [],
          },
        ],
      },
    ],
  };
}
