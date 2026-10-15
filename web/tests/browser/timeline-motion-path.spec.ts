import { expect, test } from "@playwright/test";

test("stage motion paths expose handles ghosts snapping and coverage warnings", async ({
  page,
}) => {
  await page.route("**/api/gui/v1/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/projects/demo-project/assets")) {
      return route.fulfill({ json: [] });
    }
    if (url.pathname.endsWith("/projects/demo-project/draft")) {
      return route.fulfill({ json: draftResponse() });
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
  await page.getByText("Timeline editor", { exact: true }).click();
  await page.getByLabel("Editing space").selectOption("stage-composition");
  await expect(
    page.getByRole("button", { name: "Transform layer Crossing ghost" }),
  ).toBeVisible();
  await expect(
    page.getByText(
      /Authored once in stage · target shared-stage · 10×4.*Static projector calibration/,
    ),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /Transform layer/ })).toHaveCount(1);

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
    resources: [],
    projectors: [],
    lighting_surfaces: [],
    fixtures: [
      {
        semantic_id: "window-center",
        name: "Window center",
        stage_points: [[2, 2]],
      },
    ],
    paths: [],
    zones: [],
  };
}
