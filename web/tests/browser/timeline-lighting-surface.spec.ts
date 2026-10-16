import { expect, test } from "@playwright/test";

let savedDraft: Record<string, unknown> | null = null;

test("lighting surface composition edits in logical local space", async ({ page }) => {
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/projects/demo-project/assets")) {
      return route.fulfill({ json: [] });
    }
    if (url.pathname.endsWith("/projects/demo-project/render-jobs")) {
      return route.fulfill({ json: [] });
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
