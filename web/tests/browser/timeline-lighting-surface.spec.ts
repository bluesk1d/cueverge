import { expect, test } from "@playwright/test";

let savedDraft: Record<string, unknown> | null = null;

test("lighting surface composition edits in logical local space", async ({ page }) => {
  savedDraft = null;
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/installation-photos/front-photo/content")) {
      return route.fulfill({
        contentType: "image/png",
        body: Buffer.from(
          "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP8z8DAwMDAxMDAwMDAAAANHQEDasKb6QAAAABJRU5ErkJggg==",
          "base64",
        ),
      });
    }
    if (url.pathname.endsWith("/projects/demo-project/assets")) {
      return route.fulfill({ json: [{
        id: "overlay-gif", name: "Ghost GIF", status: "ready", media_type: "image/gif",
        project_id: "demo-project", current_revision: 1, metadata_revision: 1,
        owner: null, license: null, provenance: null, review_status: "unreviewed",
        expires_on: null, folder: null, tags: [], favorite: false, error: null,
        digest_sha256: "a".repeat(64), size_bytes: 100,
        original_filename: "overlay.gif", scan_status: "clean",
        original_file_status: "available", artifact_faults: [], references: [], unused: false,
        created_at: "2026-10-21T18:00:00Z", updated_at: "2026-10-21T18:00:00Z",
        metadata: { width: 2, height: 2, duration_ms: 400 },
      }] });
    }
    if (url.pathname.endsWith("/projects/demo-project/render-jobs")) {
      if (request.method() === "POST") {
        return route.fulfill({ json: {
          id: "led-bake", composition_id: "lighting-composition", source_revision: 2,
          state: "completed", progress: 1, error: null, artifact_id: "lighting-artifact",
        } });
      }
      return route.fulfill({ json: [] });
    }
    if (url.pathname.endsWith("/demo-installation/photos")) {
      return route.fulfill({ json: [{
        id: "front-photo", name: "House front", installation_id: "demo-installation",
      }] });
    }
    if (url.pathname.endsWith("/demo-installation/geometry")) {
      return route.fulfill({ json: [{
        id: "front-geometry", semantic_id: "facade-surface", kind: "pixel_segment",
        photo_id: "front-photo", hidden: false,
        points: [{ x: 0.1, y: 0.1 }, { x: 0.9, y: 0.1 }, { x: 0.9, y: 0.9 }, { x: 0.1, y: 0.9 }],
      }] });
    }
    if (url.pathname.endsWith("/compositions/lighting-composition/lighting-preview")) {
      const timestampMs = Number(url.searchParams.get("timestamp_ms"));
      return route.fulfill({ json: lightingPreview(timestampMs) });
    }
    if (url.pathname.endsWith("/projects/demo-project/draft")) {
      if (request.method() === "PUT") {
        savedDraft = request.postDataJSON();
        return route.fulfill({ json: { ...draftResponse(), revision: 2, draft: savedDraft } });
      }
      return route.fulfill({ json: draftResponse() });
    }
    if (url.pathname.endsWith("/demo-installation/mapper-export")) {
      return route.fulfill({ json: mapperExport() });
    }
    if (url.pathname.endsWith("/demo-installation/stage-coverage")) {
      return route.fulfill({ json: coverage() });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByText("Timeline editor", { exact: true }).click();
  await page.getByLabel("Editing space").selectOption("lighting-composition");

  await expect(
    page.getByRole("img", { name: "Lighting surface local composition canvas" }),
  ).toBeVisible();
  await expect(page.getByRole("img", { name: "Integrated simulation on House front" })).toBeVisible();
  const photoHref = await page
    .getByRole("img", { name: "Integrated simulation on House front" })
    .locator("image")
    .first()
    .getAttribute("href");
  expect(photoHref).not.toBeNull();
  await expect.poll(() => page.evaluate(async (href) => {
    const image = new Image();
    image.src = href;
    await image.decode();
    return image.naturalWidth;
  }, photoHref!)).toBe(2);
  await expect(page.getByLabel("Spatial media overlay on installation photo")).toBeVisible();
  await expect(page.getByText(/local 4×2/)).toBeVisible();
  await expect(page.getByText(/3 calibrated samples/)).toBeVisible();
  await expect(page.getByText(/physical indices remain installation diagnostics only/)).toBeVisible();
  await expect(page.getByText(/Stage transform: 2, 0, 0, 2, 1, 1/)).toBeVisible();
  await expect(
    page.getByLabel("Lighting preview diagnostics"),
  ).toContainText(
    "bilinear sampling · normalized channel load 0.418 · calibration clipping 1 · mapping revision 3",
  );
  await expect(
    page
      .getByRole("img", { name: "Lighting surface local composition canvas" })
      .locator(".lighting-composition-sample")
      .first(),
  ).toHaveAttribute("style", "fill: rgb(255, 32, 16);");
  await expect(
    page
      .getByRole("img", { name: "Lighting surface local composition canvas" })
      .locator(".lighting-composition-footprint"),
  ).toHaveCount(3);
  await expect(page.getByLabel("Lighting device limiting status")).toContainText(
    "Device-reported limiting: not reported · Geometry calibration: current",
  );
  await expect(page.getByLabel("Lighting performance budget")).toContainText(
    "interactive_30fps · 30 fps target · qualified for interactive evaluation",
  );
  await expect(page.getByLabel("Lighting canvas width")).toHaveValue("256");
  await expect(page.getByLabel("Lighting canvas height")).toHaveValue("256");
  await page.getByLabel("Lighting canvas width").fill("320");
  await page.getByLabel("Lighting sampling mode").selectOption("area");
  await expect(
    page
      .getByRole("img", { name: "Lighting surface local composition canvas" })
      .locator(".lighting-composition-sample"),
  ).toHaveCount(3);
  await expect(page.getByText(/pixel 8/i)).toHaveCount(0);

  await page.getByLabel("Position 1").fill("2");
  await page.getByLabel("Position 2").fill("1");
  const timeline = page.getByLabel("Timeline canvas");
  await timeline.focus();
  await page.keyboard.press("ArrowRight");
  await page.getByRole("button", { name: "Add marker" }).click();
  await expect(
    page.getByRole("button", { name: "Marker at 100 milliseconds" }),
  ).toBeVisible();

  await addLightingLayer(page, "Spatial wash", "spatial");
  await page.getByLabel("Spatial lighting primitive").selectOption("linear_gradient");
  await page.getByLabel("Lighting layer blend").selectOption("add");
  await addLightingLayer(page, "Roof chase", "path");
  await page.getByLabel("Lighting semantic path").selectOption("roof-path");
  await addLightingLayer(page, "Window pulse", "fixture");
  await page.getByLabel("Lighting fixture or group target").fill("window-group");
  await page.getByLabel("Lighting layer precedence").fill("20");
  await addLightingLayer(page, "Night baseline", "fixture");
  await page.getByLabel("Lighting fixture or group target").fill("window-group");
  await page.getByLabel("Persistent lighting look").check();
  await expect(page.getByLabel("Lighting layer start")).toBeDisabled();
  await expect(page.getByLabel("Lighting layer duration")).toBeDisabled();
  await page.getByRole("button", { name: "Save timeline" }).click();
  await expect.poll(() => savedDraft).not.toBeNull();
  const layers = (
    savedDraft as {
      editor_metadata: {
        lighting_author_layers: Record<
          string,
          { mode: string; persistent: boolean }[]
        >;
      };
    }
  ).editor_metadata.lighting_author_layers["lighting-composition"];
  expect(layers.map((layer) => layer.mode)).toEqual([
    "spatial",
    "path",
    "fixture",
    "fixture",
  ]);
  expect(layers.map((layer) => layer.persistent)).toEqual([
    false,
    false,
    false,
    true,
  ]);

  await addLightingLayer(page, "Front door ripple", "spatial");
  await page.getByLabel("Spatial lighting primitive").selectOption("ripple");
  await page.getByLabel("Ripple coordinate space").selectOption("stage");
  await page.getByLabel("Ripple speed").fill("2");
  await page.getByLabel("Ripple ring_width").fill("0.6");
  await page.getByLabel("New transform layer name").fill("Ghost overlay");
  await page.getByLabel("New transform layer source").fill("overlay-gif");
  await expect(page.getByLabel("New transform layer kind")).toHaveValue("video");
  await page.getByLabel("New transform layer track").selectOption("surface-track");
  await page.getByLabel("New transform layer duration").fill("1000");
  await page.getByRole("button", { name: "Add transform layer", exact: true }).click();
  await expect(page.getByLabel("Media playback")).toHaveValue("loop");
  await expect(page.getByLabel("Position 1")).toHaveValue("2");
  await expect(page.getByLabel("Position 2")).toHaveValue("1");
  await page.getByRole("button", { name: "Save timeline" }).click();
  await expect.poll(() => JSON.stringify(savedDraft)).toContain('"ring_width":0.6');
  await expect.poll(() => JSON.stringify(savedDraft)).toContain('"playback":"loop"');
  await page.getByRole("button", { name: "Bake LED animation" }).click();
  await expect(page.getByText("LED bake · revision 2 · completed")).toBeVisible();
});

async function addLightingLayer(
  page: import("@playwright/test").Page,
  name: string,
  mode: "spatial" | "path" | "fixture",
) {
  await page.getByLabel("New lighting layer name").fill(name);
  await page.getByLabel("New lighting author mode").selectOption(mode);
  await page.getByRole("button", { name: "Add lighting layer" }).click();
}

function draftResponse() {
  return {
    project: {
      id: "demo-project",
      name: "Home Show",
      head_revision: 1,
      metadata_revision: 1,
      archived_at: null,
      created_at: "2026-10-15T12:00:00Z",
      updated_at: "2026-10-15T12:00:00Z",
    },
    revision: 1,
    draft: {
      schema_version: 1,
      installation: { schema_version: 1, resources: [] },
      editor_metadata: {
        installation_id: "demo-installation",
        compositions: {
          "lighting-composition": {
            id: "lighting-composition",
            name: "Facade pixels",
            duration_ms: 5000,
            space_kind: "lighting_surface",
            target_id: "facade-surface",
            width: 4,
            height: 2,
            local_time_offset_ms: 0,
            children: [],
            safe_points_ms: [],
            protected_intervals: [],
            lighting_settings: {
              canvas_width: 256,
              canvas_height: 256,
              sampling: "bilinear",
              gamma: 2.2,
              brightness: 1,
            },
          },
        },
        scene_compositions: { ambient: ["lighting-composition"] },
        timeline_tracks: {
          "lighting-composition": [
            {
              id: "surface-track",
              kind: "group",
              name: "Facade layers",
              resource_binding: "wled-001",
            },
          ],
        },
        timeline_transform_layers: {
          "lighting-composition": [
            {
              layer_schema_version: 1,
              id: "wash-layer",
              name: "Wash",
              kind: "image",
              source_id: "wash-image",
              track_id: "surface-track",
              start_ms: 0,
              duration_ms: 5000,
              in_point_ms: 0,
              out_point_ms: 5000,
              z_index: 0,
              fade_in_ms: 0,
              fade_out_ms: 0,
              audio: null,
              alpha_interpretation: "straight",
              source_color_space: "srgb",
              scaling_behavior: "fit",
              keyframes: [
                {
                  id: "wash-frame",
                  time_ms: 0,
                  position: [0, 0],
                  scale: [1, 1],
                  rotation_deg: 0,
                  anchor: [0.5, 0.5],
                  opacity: 1,
                  crop: [0, 0, 0, 0],
                  interpolation: {
                    kind: "linear",
                    temporal_bezier: null,
                    spatial_in: null,
                    spatial_out: null,
                  },
                },
              ],
            },
          ],
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
          clock_source: "left",
          media: [
            {
              id: "left",
              resource: "projector.left",
              asset: null,
              required: true,
              start_delay_ms: 0,
            },
          ],
          lighting: [
            {
              id: "facade",
              resource: "wled-001",
              required: true,
              effect: "timeline",
              timeline: [],
            },
          ],
        },
      ],
    },
  };
}

function mapperExport() {
  return {
    schema_version: 1,
    installation_id: "demo-installation",
    resources: [],
    projectors: [],
    lighting_surfaces: [
      {
        semantic_id: "facade-surface",
        resource: "wled-001",
        local_width: 4,
        local_height: 2,
        local_points: [[0, 0], [4, 0], [4, 2], [0, 2]],
        stage_matrix: [2, 0, 0, 2, 1, 1],
        pixels: [
          { output_index: 8, local_x: 0.5, local_y: 1, footprint_width: 0.2, footprint_height: 0.2, stage_x: 2, stage_y: 3 },
          { output_index: 2, local_x: 2, local_y: 1, footprint_width: 0.2, footprint_height: 0.2, stage_x: 5, stage_y: 3 },
          { output_index: 5, local_x: 3.5, local_y: 1, footprint_width: 0.2, footprint_height: 0.2, stage_x: 8, stage_y: 3 },
        ],
      },
    ],
    fixtures: [],
    paths: [
      {
        semantic_id: "roof-path",
        name: "Roof path",
        stage_points: [[0, 0], [1, 1]],
      },
    ],
    zones: [],
  };
}

function lightingPreview(timestampMs: number) {
  return {
    canvas_png_base64: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aUgAAAABJRU5ErkJggg==",
    composition_id: "lighting-composition",
    timestamp_ms: timestampMs,
    surface_semantic_id: "facade-surface",
    surface_resource: "wled-001",
    sampling: "bilinear",
    normalized_channel_load: 0.418,
    clipped_pixels: 1,
    diagnostics: {
      mapping_revision: 3,
      calibration_id: "facade-calibration",
      mapping_stale: false,
      unmapped_pixels: 0,
      duplicate_output_indices: [],
      device_reported_scale: null,
    },
    output_boundary: [[0, 0], [4, 0], [4, 2], [0, 2]],
    performance_budget: {
      profile: "interactive_30fps",
      target_fps: 30,
      maximum_canvas_pixels: 262144,
      maximum_output_pixels: 4096,
      maximum_author_layers: 128,
      qualified: true,
      exceeded_dimensions: [],
    },
    samples: [
      { output_index: 2, requested_color: [16, 255, 32], color: [16, 255, 32] },
      { output_index: 5, requested_color: [32, 16, 255], color: [32, 16, 255] },
      { output_index: 8, requested_color: [255, 32, 16], color: [255, 32, 16] },
    ],
    hardware_command_sent: false,
  };
}

function coverage() {
  return {
    installation_id: "demo-installation",
    minimum_x: 0,
    minimum_y: 0,
    maximum_x: 10,
    maximum_y: 4,
    regions: [],
  };
}
