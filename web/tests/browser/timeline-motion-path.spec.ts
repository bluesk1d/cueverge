import { expect, test } from "@playwright/test";
import { openAdvancedShowWorkspace, openTimelineEditor, openComposition } from "./show-workspace";

let savedScenario: Record<string, unknown> | null = null;

test("stage motion paths expose handles ghosts snapping and coverage warnings", async ({
  page,
}) => {
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/projects/demo-project/assets")) {
      return route.fulfill({ json: [] });
    }
    if (url.pathname.endsWith("/projects/demo-project/draft")) {
      return route.fulfill({ json: draftResponse() });
    }
    if (url.pathname.endsWith("/projects/demo-project/simulation-scenarios")) {
      if (request.method() === "POST") {
        savedScenario = {
          id: "scenario-1",
          project_id: "demo-project",
          source_revision: 1,
          created_at: "2026-10-16T12:00:00Z",
          updated_at: "2026-10-16T12:00:00Z",
          ...request.postDataJSON(),
        };
        return route.fulfill({ status: 201, json: savedScenario });
      }
      return route.fulfill({ json: savedScenario ? [savedScenario] : [] });
    }
    if (
      url.pathname.endsWith(
        "/projects/demo-project/simulation-scenarios/scenario-1/run",
      )
    ) {
      return route.fulfill({
        json: {
          scenario_id: "scenario-1",
          source_revision: 1,
          passed: true,
          mismatches: [],
          actual: {
            clock_ms: 1000,
            scene_history: ["ambient"],
            lighting_frame_digests: { "wled-001": "abc123" },
          },
          hardware_adapters_opened: false,
          hardware_commands_sent: false,
        },
      });
    }
    if (url.pathname.endsWith("/projects/demo-project/preview-evaluation")) {
      return route.fulfill({
        json: {
          project_id: "demo-project",
          source_revision: 1,
          space_id: url.searchParams.get("space_id"),
          space_kind: "stage",
          timestamp_ms: Number(url.searchParams.get("timestamp_ms")),
          duration_ms: 2000,
          frame_rate: { numerator: 30, denominator: 1 },
          frame_index: 0,
          frame_timestamp_ms: 0,
          timing_error_ms: 0,
          resources: ["shared-stage"],
          degraded_resources: [],
          evaluation: "backend_deterministic",
          hardware_command_sent: false,
        },
      });
    }
    if (url.pathname.endsWith("/compositions/lighting-composition/lighting-preview")) {
      return route.fulfill({ json: lightingPreview() });
    }
    if (
      url.pathname.endsWith(
        "/compositions/stage-composition/stage-lighting-preview",
      )
    ) {
      return route.fulfill({
        json: {
          composition_id: "stage-composition",
          timestamp_ms: Number(url.searchParams.get("timestamp_ms")),
          source: "shared_stage_compositor",
          surfaces: [
            {
              surface_semantic_id: "facade-surface",
              surface_resource: "wled-001",
              mapped_brightness_limit: 0.8,
              device_reported_scale: 0.5,
              samples: [
                {
                  output_index: 7,
                  requested_color: [255, 128, 0],
                  color: [102, 51, 0],
                },
                {
                  output_index: 8,
                  requested_color: [0, 128, 255],
                  color: [0, 51, 102],
                },
              ],
            },
          ],
          hardware_command_sent: false,
        },
      });
    }
    if (url.pathname.endsWith("/installations/demo-installation/photos")) {
      return route.fulfill({ json: [photo()] });
    }
    if (url.pathname.endsWith("/installations/demo-installation/geometry")) {
      return route.fulfill({ json: photoGeometry() });
    }
    if (
      url.pathname.endsWith("/installation-photos/front-photo/content") ||
      url.pathname.endsWith(
        "/installation-geometry/projector-surface/projector-simulation-overlay",
      )
    ) {
      return route.fulfill({
        contentType: "image/png",
        body: Buffer.from(
          "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X4R7WQAAAABJRU5ErkJggg==",
          "base64",
        ),
      });
    }
    if (url.pathname.endsWith("/installations/demo-installation/stage-coverage")) {
      return route.fulfill({ json: coverage() });
    }
    if (url.pathname.endsWith("/installations/demo-installation/mapper-export")) {
      return route.fulfill({ json: mapperExport() });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openTimelineEditor(page);
  await openComposition(page, "stage-composition");
  await expect(
    page.getByRole("button", { name: "Media layer Crossing ghost", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(
      /Placed in Whole display overlay · shared-stage · 10×4.*Projector calibration/,
    ),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /^Media layer / })).toHaveCount(1);

  const overlay = page.getByRole("img", { name: "Stage composition overlay" });
  await expect(overlay.locator(".stage-motion-path")).toHaveCount(1);
  await expect(overlay.locator(".stage-motion-path")).toHaveAttribute("d", /C /);
  await expect(overlay.locator('[aria-label="Selected motion position"]')).toBeVisible();
  await expect(overlay.locator('[aria-label="Next position ghost"]')).toBeVisible();
  await expect(overlay.locator('[aria-label="Spatial in handle"]')).toBeVisible();
  await expect(overlay.locator('[aria-label="Spatial out handle"]')).toBeVisible();
  await expect(page.getByLabel("Motion path warnings")).toContainText(
    "occluded region",
  );
  await expect(page.getByLabel("Motion path warnings")).toContainText(
    "uncovered region",
  );
  await expect(page.getByLabel("Motion path warnings")).toContainText(
    "insufficiently calibrated region",
  );
  await expect(overlay.locator(".stage-composition-pixel")).toHaveCount(2);
  await expect(overlay.locator(".stage-composition-pixel").first()).toHaveAttribute(
    "style",
    "fill: rgb(102, 51, 0);",
  );
  await expect(overlay.locator(".stage-composition-pixel-footprint")).toHaveCount(2);
  await expect(overlay.locator(".stage-composition-output-order")).toHaveCount(0);
  await page.getByText("Show LED numbers").click();
  await expect(overlay.locator(".stage-composition-output-order")).toHaveText([
    "7",
    "8",
  ]);
  await expect(overlay.locator(".stage-composition-fixture")).toHaveCount(1);
  await expect(overlay.locator(".stage-composition-lighting-path")).toHaveCount(1);
  const photoPreview = page.getByRole("img", {
    name: "Integrated simulation on Front",
  });
  await expect(photoPreview).toBeVisible();
  await expect(photoPreview.locator(".projector-simulation-overlay")).toHaveCount(1);
  await expect(photoPreview.locator(".stage-composition-pixel")).toHaveCount(2);
  await expect(
    page.getByText(/sampled colors include controller power limiting/),
  ).toBeVisible();
  await expect(page.getByLabel("Shared stage lighting sampling")).toContainText(
    "Shared stage visual sampled onto calibrated LEDs",
  );

  await page
    .getByLabel("Transform keyframe", { exact: true })
    .selectOption("middle-frame");
  await expect(overlay.locator('[aria-label="Previous position ghost"]')).toBeVisible();
  await expect(overlay.locator('[aria-label="Next position ghost"]')).toBeVisible();
  await page
    .getByLabel("Snap transform keyframe to marker")
    .selectOption("impact-marker");
  await expect(page.getByLabel("Transform keyframe time")).toHaveValue("750");

  const box = await overlay.boundingBox();
  expect(box).not.toBeNull();
  await overlay.click({
    position: {
      x: (2 / 12) * box!.width,
      y: (2 / 4) * box!.height,
    },
  });
  await expect(page.getByLabel("Position 1")).toHaveValue("2");
  await expect(page.getByLabel("Position 2")).toHaveValue("2");

  await page.getByRole("button", { name: "Back to scene", exact: true }).click();
  await page.getByRole("button", { name: "Preview", exact: true }).click();
  await page.getByText("Fault-injection test scenarios (advanced)", { exact: true }).click();
  await page.getByRole("button", { name: "Add accelerated advance" }).click();
  await page.getByRole("button", { name: "Add client disconnect" }).click();
  await page.getByRole("button", { name: "Add output failure" }).click();
  await expect(page.getByLabel("Simulation scenario steps").locator("li")).toHaveCount(3);
  await page.getByRole("button", { name: "Save simulation scenario" }).click();
  await expect.poll(() => savedScenario).not.toBeNull();
  await expect(page.getByText("Rehearsal scenario", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Run saved scenario" }).click();
  await expect(page.getByLabel("Simulation scenario result")).toContainText(
    "Expected outcome matched",
  );
  await expect(page.getByLabel("Simulation scenario result")).toContainText(
    "Hardware adapters opened: no · hardware commands sent: no",
  );
});

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
          "stage-composition": {
            id: "stage-composition",
            name: "Shared stage",
            duration_ms: 2000,
            space_kind: "stage",
            target_id: "shared-stage",
            width: 10,
            height: 4,
            local_time_offset_ms: 0,
            children: [],
            safe_points_ms: [],
            protected_intervals: [],
          },
          "lighting-composition": {
            id: "lighting-composition",
            name: "Facade pixels",
            duration_ms: 2000,
            space_kind: "lighting_surface",
            target_id: "facade-surface",
            width: 4,
            height: 2,
            local_time_offset_ms: 0,
            children: [],
            safe_points_ms: [],
            protected_intervals: [],
          },
        },
        scene_compositions: { ambient: ["stage-composition"] },
        timeline_tracks: {
          "stage-composition": [
            {
              id: "ghost-track",
              kind: "group",
              name: "Ghost",
              resource_binding: "stage.ghost",
            },
          ],
        },
        timeline_markers: {
          "stage-composition": [
            {
              id: "impact-marker",
              name: "Impact",
              time_ms: 750,
            },
          ],
        },
        timeline_transform_layers: {
          "stage-composition": [
            {
              id: "crossing-ghost",
              name: "Crossing ghost",
              kind: "image",
              source_id: "ghost-image",
              track_id: "ghost-track",
              start_ms: 0,
              duration_ms: 1500,
              keyframes: [
                keyframe("start-frame", 0, [1, 2], "bezier"),
                keyframe("middle-frame", 500, [6, 2], "bezier"),
                keyframe("end-frame", 1000, [11, 2], "ease_out"),
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
    },
  };
}

function keyframe(
  id: string,
  time: number,
  position: [number, number],
  interpolation: "bezier" | "linear" | "ease_out",
) {
  return {
    id,
    time_ms: time,
    position,
    scale: [1, 1],
    rotation_deg: 0,
    anchor: [0.5, 0.5],
    opacity: 0.6,
    crop: [0, 0, 0, 0],
    interpolation:
      interpolation === "bezier"
        ? {
            kind: interpolation,
            temporal_bezier: [0.25, 0.1, 0.25, 1],
            spatial_in: [-0.5, 0],
            spatial_out: [0.5, 0],
          }
        : {
            kind: interpolation,
            temporal_bezier: null,
            spatial_in: null,
            spatial_out: null,
          },
  };
}

function coverage() {
  return {
    installation_id: "demo-installation",
    minimum_x: 0,
    minimum_y: 0,
    maximum_x: 12,
    maximum_y: 4,
    regions: [
      {
        kind: "covered",
        points: [[0, 0], [5, 0], [5, 4], [0, 4]],
        resources: ["projector.left"],
        source_ids: ["left"],
      },
      {
        kind: "covered",
        points: [[5, 0], [10, 0], [10, 4], [5, 4]],
        resources: ["projector.right"],
        source_ids: ["right"],
      },
      {
        kind: "overlap",
        points: [[4.5, 0], [5.5, 0], [5.5, 4], [4.5, 4]],
        resources: ["projector.left", "projector.right"],
        source_ids: ["left", "right"],
      },
      {
        kind: "uncovered",
        points: [[10, 0], [12, 0], [12, 4], [10, 4]],
        resources: [],
        source_ids: [],
      },
      {
        kind: "occluded",
        points: [[4, 1], [5, 1], [5, 3], [4, 3]],
        resources: ["projector.left"],
        source_ids: ["mask"],
      },
    ],
  };
}

function mapperExport() {
  return {
    schema_version: 1,
    installation_id: "demo-installation",
    resources: [
      {
        resource: "wled-001",
        kind: "pixel_output",
        target: null,
        pixel_count: 2,
      },
    ],
    projectors: [],
    lighting_surfaces: [
      {
        semantic_id: "facade-surface",
        resource: "wled-001",
        local_width: 4,
        local_height: 2,
        local_points: [[0, 0], [4, 0], [4, 2], [0, 2]],
        stage_matrix: [1, 0, 0, 1, 0, 0],
        calibration_id: "calibration-1",
        mapping_revision: 1,
        pixels: [
          {
            output_index: 7,
            local_x: 1,
            local_y: 1,
            footprint_width: 0.5,
            footprint_height: 0.5,
            stage_x: 2,
            stage_y: 2,
          },
          {
            output_index: 8,
            local_x: 3,
            local_y: 1,
            footprint_width: 0.5,
            footprint_height: 0.5,
            stage_x: 8,
            stage_y: 2,
          },
        ],
      },
    ],
    fixtures: [
      {
        semantic_id: "window-center",
        name: "Window center",
        stage_points: [[2, 2]],
      },
    ],
    paths: [
      {
        semantic_id: "roof-path",
        name: "Roof path",
        stage_points: [[0, 1], [10, 1]],
      },
    ],
    zones: [],
  };
}

function lightingPreview() {
  return {
    composition_id: "lighting-composition",
    timestamp_ms: 0,
    surface_semantic_id: "facade-surface",
    surface_resource: "wled-001",
    sampling: "bilinear",
    normalized_channel_load: 0.5,
    clipped_pixels: 0,
    diagnostics: {
      mapping_revision: 1,
      calibration_id: "calibration-1",
      mapping_stale: false,
      unmapped_pixels: 0,
      duplicate_output_indices: [],
      device_reported_scale: 0.8,
    },
    output_boundary: [[0, 0], [4, 0], [4, 2], [0, 2]],
    performance_budget: {
      profile: "interactive_30fps",
      target_fps: 30,
      maximum_canvas_pixels: 65536,
      maximum_output_pixels: 2048,
      maximum_author_layers: 16,
      qualified: true,
      exceeded_dimensions: [],
    },
    samples: [
      { output_index: 7, requested_color: [255, 0, 0], color: [204, 0, 0] },
      { output_index: 8, requested_color: [0, 255, 0], color: [0, 204, 0] },
    ],
    hardware_command_sent: false,
  };
}

function photo() {
  return {
    id: "front-photo",
    installation_id: "demo-installation",
    name: "Front",
    viewpoint: "front",
    original_filename: "front.png",
    media_type: "image/png",
    digest_sha256: "a".repeat(64),
    size_bytes: 100,
    orientation_degrees: 0,
    crop_x: null,
    crop_y: null,
    crop_width: null,
    crop_height: null,
    scale_hint: null,
    revision: 1,
    created_at: "2026-10-15T12:00:00Z",
    updated_at: "2026-10-15T12:00:00Z",
  };
}

function photoGeometry() {
  const base = {
    installation_id: "demo-installation",
    photo_id: "front-photo",
    landmark_ids: [],
    color: "#ffffff",
    label_visible: true,
    locked: false,
    hidden: false,
    z_index: 1,
    direction: "forward",
    start_index: null,
    pixel_count: null,
    stage_points: null,
    closed: true,
    revision: 1,
    created_at: "2026-10-15T12:00:00Z",
    updated_at: "2026-10-15T12:00:00Z",
  };
  return [
    {
      ...base,
      id: "facade-geometry",
      kind: "pixel_segment",
      name: "Facade",
      semantic_id: "facade-surface",
      points: [
        { x: 0.1, y: 0.2 },
        { x: 0.9, y: 0.2 },
        { x: 0.9, y: 0.8 },
        { x: 0.1, y: 0.8 },
      ],
    },
    {
      ...base,
      id: "projector-surface",
      kind: "projector_surface",
      name: "Main projector",
      semantic_id: "projector-main",
      points: [
        { x: 0.2, y: 0.2 },
        { x: 0.8, y: 0.2 },
        { x: 0.8, y: 0.7 },
        { x: 0.2, y: 0.7 },
      ],
    },
  ];
}
