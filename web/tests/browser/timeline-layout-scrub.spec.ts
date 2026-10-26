import { expect, test } from "@playwright/test";
import { mockReadyAsset, openComposition, openTimelineEditor } from "./show-workspace";

test("layout view shows each video layer's frame at the scrubbed playhead", async ({ page }) => {
  await page.route("**/api/gui/v1/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/projects/demo-project/assets")) {
      const video = (id: string, name: string) => ({
        ...mockReadyAsset(id, name, "video/mp4"),
        metadata: {
          duration_ms: 2000,
          width: 1920,
          height: 1080,
          artifacts: {
            review: { media_type: "video/mp4", size_bytes: 10 },
            thumbnail: { media_type: "image/jpeg", size_bytes: 10 },
          },
        },
      });
      return route.fulfill({ json: [video("loop-video", "Window loop"), video("once-video", "Ghost once")] });
    }
    if (url.pathname.includes("/assets/") && url.pathname.includes("/artifacts/")) {
      return route.fulfill({ status: 404, body: "" });
    }
    if (url.pathname.endsWith("/projects/demo-project/draft")) {
      return route.fulfill({ json: response(document()) });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openTimelineEditor(page);
  await openComposition(page, "window");
  const layout = page.getByLabel("Composition layout");
  const scrub = page.getByLabel("Scrub position");

  await scrub.fill("1000");
  await expect(layout.getByLabel("Window loop frame at 0:00.500")).toBeVisible();
  await expect(layout.getByLabel("Ghost once frame at 0:01.000")).toBeVisible();

  await scrub.fill("2200");
  await expect(layout.getByLabel("Window loop frame at 0:01.700")).toBeVisible();
  await expect(layout.locator("video[aria-label^='Ghost once frame']")).toHaveCount(0);
  await expect(layout.getByText("Ghost once · finished (plays once)")).toBeVisible();

  // The loop wraps by the source length, exactly as the renderer does.
  await scrub.fill("2800");
  await expect(layout.getByLabel("Window loop frame at 0:00.300")).toBeVisible();
  const seconds = await layout
    .locator("video[aria-label^='Window loop frame']")
    .evaluate((element) => (element as HTMLVideoElement).currentTime);
  expect(seconds).toBeCloseTo(0.3, 2);
});

function response(draft: unknown) {
  return {
    project: {
      id: "demo-project",
      name: "Home Show",
      head_revision: 1,
      metadata_revision: 1,
      archived_at: null,
      created_at: "2026-10-14T12:00:00Z",
      updated_at: "2026-10-14T12:00:00Z",
    },
    revision: 1,
    draft,
  };
}

function layer(id: string, name: string, sourceId: string, overrides: Record<string, unknown>) {
  return {
    layer_schema_version: 1,
    id,
    name,
    kind: "video",
    source_id: sourceId,
    track_id: "window-track",
    start_ms: 0,
    duration_ms: 4000,
    in_point_ms: 0,
    out_point_ms: 2000,
    playback: "hold",
    z_index: 1,
    fade_in_ms: 0,
    fade_out_ms: 0,
    audio: null,
    alpha_interpretation: null,
    source_color_space: null,
    scaling_behavior: "fit",
    keyframes: [
      {
        id: `${id}-key`,
        time_ms: 0,
        position: [960, 540],
        scale: [0.5, 0.5],
        rotation_deg: 0,
        anchor: [0.5, 0.5],
        opacity: 1,
        crop: [0, 0, 0, 0],
        interpolation: { kind: "hold", temporal_bezier: null, spatial_in: null, spatial_out: null },
      },
    ],
    ...overrides,
  };
}

function document() {
  return {
    schema_version: 1,
    installation: { schema_version: 1, resources: [] },
    editor_metadata: {
      compositions: {
        window: {
          id: "window",
          name: "Window",
          duration_ms: 5000,
          space_kind: "projector_native",
          target_id: "projector.left",
          width: 1920,
          height: 1080,
          local_time_offset_ms: 0,
          children: [],
          safe_points_ms: [],
          protected_intervals: [],
        },
      },
      scene_compositions: { ambient: ["window"] },
      timeline_tracks: {
        window: [{ id: "window-track", kind: "visual", name: "Window track", built_in: false }],
      },
      timeline_transform_layers: {
        window: [
          layer("loop-layer", "Window loop", "loop-video", {
            start_ms: 1000,
            duration_ms: 3000,
            in_point_ms: 500,
            playback: "loop",
            z_index: 2,
          }),
          layer("once-layer", "Ghost once", "once-video", { playback: "once" }),
        ],
      },
    },
    scenes: [
      {
        schema_version: 2,
        id: "ambient",
        name: "Ambient",
        mode: "media_synchronized",
        resources: ["projector.left"],
        duration_ms: 5000,
        clock_source: "left",
        media: [
          { id: "left", resource: "projector.left", asset: null, required: true, start_delay_ms: 0 },
        ],
        lighting: [],
        scheduling: { safe_points_ms: [], protected_intervals: [] },
      },
    ],
  };
}
