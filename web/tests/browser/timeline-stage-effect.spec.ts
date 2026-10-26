import { expect, test, type Page } from "@playwright/test";
import { mockReadyAsset, openTimelineEditor, openComposition, openTimelineTool } from "./show-workspace";

const installationId = "demo-installation";

async function mockStageApi(
  page: Page,
  document: Record<string, unknown>,
  assets: Record<string, unknown>[] = [],
) {
  const state: { saved: Record<string, unknown> | null } = { saved: null };
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    if (pathname.endsWith("/projects/demo-project/draft")) {
      if (request.method() === "PUT") state.saved = request.postDataJSON();
      return route.fulfill({ json: {
        project: {
          id: "demo-project", name: "Home Show", head_revision: state.saved ? 2 : 1,
          metadata_revision: 1, archived_at: null,
          created_at: "2026-10-15T12:00:00Z", updated_at: "2026-10-15T12:00:00Z",
        },
        revision: state.saved ? 2 : 1,
        draft: state.saved ?? document,
      } });
    }
    if (pathname.endsWith("/mapper-export")) {
      return route.fulfill({ json: {
        schema_version: 1,
        installation_id: installationId,
        stage: {
          width: 50, height: 30, unit: "ft", reference_photo_id: "house",
          reference_revision: 1, canvas_units_per_stage_unit: 20,
        },
        resources: [], fixtures: [], paths: [], zones: [],
        projectors: [
          projector("projector.left", [5, 6, 20, 15]),
          projector("projector.right", [22, 6, 20, 15]),
        ],
        lighting_surfaces: [{
          semantic_id: "roofline", name: "Roofline", resource: "wled-001",
          local_width: 1, local_height: 1, stage_matrix: [50, 0, 0, 30, 0, 0],
          calibration_id: null, mapping_revision: 1, local_points: [],
          pixels: [
            pixel(0, 2.5, 1.5),
            pixel(1, 47.5, 1.5),
          ],
        }, {
          semantic_id: "porch", name: "Porch eave", resource: "wled-001",
          local_width: 1, local_height: 1, stage_matrix: [50, 0, 0, 30, 0, 0],
          calibration_id: null, mapping_revision: 1, local_points: [],
          pixels: [
            pixel(2, 10, 20),
            pixel(3, 20, 20),
          ],
        }],
      } });
    }
    if (pathname.endsWith("/stage-coverage")) {
      return route.fulfill({ json: {
        installation_id: installationId, minimum_x: 5, minimum_y: 6, maximum_x: 42, maximum_y: 21,
        regions: [{
          kind: "overlap", points: [[22, 6], [25, 6], [25, 21], [22, 21]],
          resources: ["projector.left", "projector.right"], source_ids: ["l", "r"],
        }],
      } });
    }
    if (pathname.endsWith("/stage-lighting-preview")) {
      expect(url.searchParams.get("include_canvas")).toBe("true");
      return route.fulfill({ json: {
        composition_id: "flag", timestamp_ms: 0, source: "shared_stage_compositor",
        surfaces: [], hardware_command_sent: false,
        canvas_png_base64:
          "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
      } });
    }
    if (pathname.endsWith(`/installations/${installationId}/photos`)) {
      return route.fulfill({ json: [{
        id: "house", installation_id: installationId, name: "House front", description: "House front",
        viewpoint: "Street", original_filename: "house.jpg", media_type: "image/jpeg",
        digest_sha256: "a".repeat(64), size_bytes: 1024, orientation_degrees: 0,
        crop_x: null, crop_y: null, crop_width: null, crop_height: null, scale_hint: null,
        revision: 1, created_at: "", updated_at: "",
      }] });
    }
    if (pathname.startsWith("/api/gui/v1/installation-photos/")) {
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600"><rect width="1000" height="600" fill="#203040"/></svg>',
      });
    }
    if (pathname.endsWith("/assets")) return route.fulfill({ json: assets });
    if (pathname.endsWith("/render-jobs") || pathname.endsWith("/geometry")) {
      return route.fulfill({ json: [] });
    }
    if (pathname.endsWith("/lighting-preview")) {
      return route.fulfill({ status: 422, json: { detail: { message: "not a lighting composition" } } });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });
  return state;
}

test("a shared stage effect picks outputs, matches the stage and pins media to corners", async ({ page }) => {
  const state = await mockStageApi(page, draft());

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openTimelineEditor(page);
  await openComposition(page, "flag");

  const outputs = page.getByRole("group", { name: "Outputs in this effect" });
  await expect(outputs.getByLabel("Include projector projector.left")).toBeChecked();
  await expect(outputs.getByLabel("Include projector projector.right")).toBeChecked();
  await outputs.getByLabel("Include projector projector.right").uncheck();
  await expect(outputs.getByLabel("Include projector projector.left")).toBeDisabled();
  await outputs.getByLabel("Include LED surface roofline").check();
  await expect(outputs.getByText("Roofline · wled-001 · LEDs 0–1")).toBeVisible();
  await expect(outputs.getByRole("alert")).toContainText("installation stage is 50 × 30 ft");
  await outputs.getByRole("button", { name: "Match installation stage" }).click();
  await expect(outputs.getByRole("alert")).toHaveCount(0);

  const overlay = page.getByRole("img", { name: "Stage composition overlay" });
  await expect(overlay).toHaveAttribute("viewBox", "0 0 800 480");
  await expect(overlay.locator(".stage-composition-photo")).toHaveCount(1);
  await expect(overlay.getByLabel("Rendered stage frame")).toHaveCount(1);
  await page.getByText("Show projector coverage").click();
  await expect(overlay.locator(".stage-region")).toHaveCount(0);

  await page.getByRole("button", { name: "Media layer Flag", exact: true }).click();
  const shape = page.getByRole("group", { name: "Shape on stage" });
  await shape.getByRole("button", { name: "Fit to selected outputs" }).click();
  const pin = overlay.getByLabel("Layer corner pin");
  await expect(pin.locator("circle")).toHaveCount(4);
  const handle = overlay.getByLabel("Bottom right corner");
  await handle.scrollIntoViewIfNeeded();
  const box = await handle.boundingBox();
  if (!box) throw new Error("corner handle is not visible");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 20, box.y + box.height / 2 + 15, { steps: 4 });
  await page.mouse.up();
  await shape.getByLabel("Layer shape mask").selectOption("ellipse");
  await shape.getByLabel("Layer mask soft edge").fill("1.5");
  await expect(overlay.getByLabel("Layer mask outline")).toHaveCount(1);

  await page.getByRole("button", { name: "Save timeline" }).click();
  await expect.poll(() => state.saved).not.toBeNull();
  const metadata = (state.saved as unknown as {
    editor_metadata: {
      compositions: Record<string, Record<string, unknown>>;
      timeline_transform_layers: Record<string, Record<string, unknown>[]>;
    };
  }).editor_metadata;
  const composition = metadata.compositions.flag;
  expect(composition.width).toBe(50);
  expect(composition.height).toBe(30);
  expect(composition.stage_projector_resources).toEqual(["projector.left"]);
  expect(composition.stage_lighting_surfaces).toEqual(["roofline"]);
  const layer = metadata.timeline_transform_layers.flag[0];
  const corners = layer.corner_pin as [number, number][];
  expect(corners).toHaveLength(4);
  expect(corners[0]).toEqual([2.5, 1.5]);
  expect(corners[2][0]).toBeGreaterThan(25);
  expect(corners[2][1]).toBeGreaterThan(21);
  expect(layer.mask).toMatchObject({ feather: 1.5, invert: false });
  expect((layer.mask as { points: unknown[] }).points).toHaveLength(48);
});

test("a lights-only house effect drops every projector and shows the house in Layout", async ({ page }) => {
  const state = await mockStageApi(page, draft());
  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openTimelineEditor(page);
  await openComposition(page, "flag");

  const outputs = page.getByRole("group", { name: "Outputs in this effect" });
  await outputs.getByLabel("Include LED surface roofline").check();
  await outputs.getByLabel("Include projector projector.right").uncheck();
  await outputs.getByLabel("Include projector projector.left").uncheck();
  await expect(outputs.getByLabel("Include projector projector.left")).not.toBeChecked();
  await expect(outputs.getByLabel("Include LED surface roofline")).toBeDisabled();
  await expect(outputs.getByRole("status")).toContainText("Lights only");

  await page.getByRole("button", { name: "Layout", exact: true }).click();
  const layout = page.getByLabel("Composition layout");
  const house = layout.getByRole("img", { name: "Installation reference" });
  await expect(house.locator("image.layout-canvas__photo")).toHaveCount(1);
  await expect(house.locator(".layout-canvas__projector--excluded")).toHaveCount(2);
  const leds = layout.getByRole("img", { name: "Mapped LED positions" });
  await expect(leds.locator("circle.layout-canvas__led:not(.layout-canvas__led--excluded)")).toHaveCount(2);
  await expect(leds.locator("circle.layout-canvas__led--excluded")).toHaveCount(2);
  await expect(leds.locator("circle").first()).toHaveAttribute("cx", "2.5");
  await expect(layout.getByLabel("Layout box Flag")).toBeVisible();
  await layout.getByText("Show house and LEDs").click();
  await expect(layout.getByRole("img", { name: "Installation reference" })).toHaveCount(0);

  await openTimelineTool(page, "Render & bake");
  await expect(page.getByRole("button", { name: "Bake LED overlay", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Use LED bake size (1200 × 720)" }).click();
  await expect(page.getByLabel("Render width", { exact: true })).toHaveValue("1200");
  await expect(page.getByAltText(/Controller render preview/)).toHaveCount(0);
  await expect(page.getByText("Cross-projector rehearsal evidence")).toHaveCount(0);
  await expect(page.getByText(/no projector frames to inspect/)).toBeVisible();

  await page.getByRole("button", { name: "Save timeline" }).click();
  await expect.poll(() => state.saved).not.toBeNull();
  const composition = (state.saved as unknown as {
    editor_metadata: { compositions: Record<string, Record<string, unknown>> };
  }).editor_metadata.compositions.flag;
  expect(composition.stage_projector_resources).toEqual([]);
  expect(composition.stage_lighting_surfaces).toEqual(["roofline"]);
});

test("the whole house lights preset selects every LED surface by its segment name", async ({ page }) => {
  await mockStageApi(page, draft());
  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openTimelineEditor(page);

  await page.getByRole("button", { name: "+ New composition" }).click();
  const dialog = page.getByRole("dialog", { name: "New composition" });
  await dialog.getByText("One LED surface", { exact: true }).click();
  await expect(dialog.getByLabel("Preset LED surface").locator("option")).toContainText([
    "Choose an LED surface",
    "Roofline · wled-001 · LEDs 0–1",
    "Porch eave · wled-001 · LEDs 2–3",
  ]);
  await dialog.getByText("Whole house lights", { exact: true }).click();
  await expect(dialog.getByRole("note")).toContainText("all 2 mapped LED outputs");
  await dialog.getByRole("button", { name: "Create and open" }).click();

  const outputs = page.getByRole("group", { name: "Outputs in this effect" });
  await expect(outputs.getByLabel("Include projector projector.left")).not.toBeChecked();
  await expect(outputs.getByLabel("Include projector projector.right")).not.toBeChecked();
  await expect(outputs.getByLabel("Include LED surface roofline")).toBeChecked();
  await expect(outputs.getByLabel("Include LED surface porch")).toBeChecked();
  await expect(outputs.getByRole("status")).toContainText("Lights only");
  await openTimelineTool(page, "Render & bake");
  await expect(page.getByLabel("Render width", { exact: true })).toHaveValue("1200");
  await expect(page.getByLabel("Render height", { exact: true })).toHaveValue("720");
  await expect(page.getByRole("button", { name: /Use LED bake size/ })).toHaveCount(0);
});

test("changing the render width never resizes or stretches stage media", async ({ page }) => {
  const state = await mockStageApi(page, draft(), [
    { ...mockReadyAsset("flag-gif", "Flag gif", "image/gif"), metadata: { width: 192, height: 108 } },
  ]);
  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openTimelineEditor(page);
  await openComposition(page, "flag");
  await page.getByRole("button", { name: "Layout", exact: true }).click();
  const box = page.getByLabel("Composition layout").getByLabel("Layout box Flag");
  await expect(box).toBeVisible();
  const before = (await box.boundingBox())!;

  await openTimelineTool(page, "Render & bake");
  const height = page.getByLabel("Render height", { exact: true });
  // 10 x 6 composition: the raster follows its shape, so 1920 wide is 1152 high.
  await expect(height).toHaveValue("1152");
  await expect(height).toBeDisabled();
  await page.getByLabel("Render width", { exact: true }).fill("960");
  await expect(height).toHaveValue("576");
  await expect(page.getByText(/render 960×576/)).toBeVisible();
  const after = (await box.boundingBox())!;
  expect(Math.abs(after.width - before.width)).toBeLessThan(1);
  expect(Math.abs(after.height - before.height)).toBeLessThan(1);
  expect(before.width / before.height).toBeCloseTo(192 / 108, 1);

  await page.getByRole("button", { name: "Save timeline" }).click();
  await expect.poll(() => state.saved).not.toBeNull();
  const composition = (state.saved as unknown as {
    editor_metadata: { compositions: Record<string, Record<string, unknown>> };
  }).editor_metadata.compositions.flag;
  expect(composition.source_pixel_units).toBeCloseTo(10 / 1920, 8);
  expect(composition.render_settings).toMatchObject({ render_width: 960, render_height: 576 });
});

function projector(resource: string, extent: [number, number, number, number]) {
  return {
    resource, geometry_id: resource, native_width: 1280, native_height: 720,
    stage_extent: extent, stage_homography: null, mesh_vertices: [], mesh_triangles: [],
  };
}

function pixel(output_index: number, stage_x: number, stage_y: number) {
  return {
    output_index, local_x: stage_x / 50, local_y: stage_y / 30,
    footprint_width: 0.2, footprint_height: 0.2, stage_x, stage_y,
  };
}

function draft() {
  return {
    schema_version: 1,
    installation: { schema_version: 1, resources: [] },
    editor_metadata: {
      installation_id: installationId,
      compositions: {
        flag: {
          id: "flag", name: "Rippling flag", duration_ms: 5000, space_kind: "stage",
          target_id: "shared-stage", width: 10, height: 6, local_time_offset_ms: 0,
          children: [], safe_points_ms: [], protected_intervals: [], stage_lighting_surfaces: [],
        },
      },
      scene_compositions: { ambient: ["flag"] },
      timeline_tracks: {
        flag: [{ id: "flag-track", kind: "group", name: "Flag", resource_binding: "shared-stage" }],
      },
      timeline_transform_layers: {
        flag: [{
          id: "flag-layer", name: "Flag", kind: "video", source_id: "flag-gif",
          track_id: "flag-track", start_ms: 0, duration_ms: 5000,
          keyframes: [{
            id: "flag-frame", time_ms: 0, position: [5, 3], scale: [1, 1], rotation_deg: 0,
            anchor: [0.5, 0.5], opacity: 1, crop: [0, 0, 0, 0],
            interpolation: { kind: "linear", temporal_bezier: null, spatial_in: null, spatial_out: null },
          }],
        }],
      },
    },
    scenes: [{
      schema_version: 2, id: "ambient", name: "Ambient", mode: "media_synchronized",
      resources: ["projector.left", "wled-001"], duration_ms: 5000, clock_source: "left-window",
      media: [{ id: "left-window", resource: "projector.left", asset: null, required: true, start_delay_ms: 0 }],
      lighting: [{
        id: "roofline", resource: "wled-001", required: true, effect: "timeline",
        timeline: [], timing_offset_ms: 0,
      }],
    }],
  };
}

test("looping layers set exact plays and compositions can be resized to fit", async ({ page }) => {
  const stripes = {
    ...mockReadyAsset("flag-gif", "Stripes", "video/mp4"),
    metadata: { width: 640, height: 360, duration_ms: 1735 },
  };
  const document = draft();
  const layer = (document.editor_metadata.timeline_transform_layers.flag as Record<string, unknown>[])[0];
  Object.assign(layer, { duration_ms: 1735, in_point_ms: 0, out_point_ms: 1735, playback: "loop" });
  const state = await mockStageApi(page, document, [stripes]);
  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openTimelineEditor(page);
  await openComposition(page, "flag");

  await page.getByRole("button", { name: "Media layer Flag", exact: true }).click();
  const plays = page.getByLabel("Layer plays");
  await expect(plays).toHaveValue("1");
  await plays.fill("2");
  await expect(page.getByLabel("Media layer duration", { exact: true })).toHaveValue("3470");

  await page.getByRole("button", { name: "Composition settings" }).click();
  const settings = page.getByRole("dialog", { name: "Composition settings · Rippling flag" });
  const length = settings.getByLabel("Composition duration (seconds)");
  await expect(length).toHaveValue("5");
  await settings.getByRole("button", { name: /Fit to layers/ }).click();
  await expect(length).toHaveValue("3.47");
  await settings.getByRole("button", { name: "Close" }).click();

  await plays.fill("3");
  await expect(page.getByLabel("Media layer duration", { exact: true })).toHaveValue("5205");
  await page.getByRole("button", { name: "Composition settings" }).click();
  await expect(length).toHaveValue("5.205");
  await settings.getByRole("button", { name: "Close" }).click();

  await page.getByRole("button", { name: "Save timeline" }).click();
  await expect.poll(() => state.saved).not.toBeNull();
  const metadata = (state.saved as unknown as {
    editor_metadata: {
      compositions: Record<string, Record<string, unknown>>;
      timeline_transform_layers: Record<string, Record<string, unknown>[]>;
    };
  }).editor_metadata;
  expect(metadata.compositions.flag.duration_ms).toBe(5205);
  expect(metadata.timeline_transform_layers.flag[0]).toMatchObject({
    duration_ms: 5205,
    in_point_ms: 0,
    out_point_ms: 5205,
    playback: "loop",
  });
});
