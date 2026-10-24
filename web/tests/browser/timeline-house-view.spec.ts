import { expect, test } from "@playwright/test";
import { mockReadyAsset, openTimelineEditor } from "./show-workspace";

const surfacePoints = [
  { x: 0.6, y: 0.3 },
  { x: 0.8, y: 0.32 },
  { x: 0.79, y: 0.5 },
  { x: 0.61, y: 0.48 },
];

test("whole-scene house view warps scene media onto its projector surface", async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 1100 });
  await page.route("**/api/gui/v1/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/projects/demo-project/assets")) {
      return route.fulfill({
        json: [{ ...mockReadyAsset("ghost-video", "Ghost video", "video/mp4"), metadata: { duration_ms: 2000 } }],
      });
    }
    if (url.pathname.endsWith("/projects/demo-project/draft")) {
      return route.fulfill({ json: response() });
    }
    if (url.pathname.endsWith("/installations/house/photos")) {
      return route.fulfill({ json: [photo()] });
    }
    if (url.pathname.endsWith("/installations/house/geometry")) {
      return route.fulfill({ json: [surface("right-surface", "Right window"), surface("left-surface", "Left window")] });
    }
    if (url.pathname.endsWith("/installation-geometry/right-surface/projector-mapping")) {
      return route.fulfill({ json: mapping() });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openTimelineEditor(page);

  const house = page.getByRole("figure", { name: "Scene house view" });
  await expect(house).toContainText("Right window ← Ghost video · not playing yet");
  const surfaceMedia = house.getByLabel("Ghost video on Right window").first();
  await expect(surfaceMedia).toHaveCSS("visibility", "hidden");
  await expect(surfaceMedia).toHaveAttribute("style", /matrix3d\(/);
  await expect(house.locator(".scene-house-view__outline--idle")).toHaveCount(1);

  const reference = page.getByLabel("right-window timing reference");
  await expect(reference).toHaveAttribute("style", /left: 20%; width: 80%/);
  await expect(reference.locator(".timeline-track-reference__name")).toHaveText("Ghost video");
  await expect(reference.locator(".timeline-track-reference__hold")).toHaveText("hold");

  await page.getByLabel("Scrub position").fill("2500");
  await expect(house).toContainText("Right window ← Ghost video · source 0:01.500");
  await expect(surfaceMedia).toHaveCSS("visibility", "visible");

  await page.getByLabel("Scrub position").fill("4500");
  await expect(house).toContainText("source 0:01.999");

  await page.getByRole("group", { name: "Scene viewer mode" }).getByRole("button", { name: "Program monitor" }).click();
  await expect(page.getByRole("figure", { name: "Scene program monitor" })).toContainText(
    "Ghost video (projector.right)",
  );
});

function response() {
  return {
    project: {
      id: "demo-project",
      name: "Demo show",
      head_revision: 1,
      metadata_revision: 1,
      archived_at: null,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-01T00:00:00Z",
    },
    revision: 1,
    draft: {
      schema_version: 1,
      installation: { schema_version: 1, resources: [] },
      editor_metadata: { installation_id: "house" },
      scenes: [
        {
          schema_version: 2,
          id: "ambush",
          name: "Ambush",
          mode: "media_synchronized",
          resources: ["projector.right"],
          duration_ms: 5000,
          clock_source: "right-window",
          media: [
            {
              id: "right-window",
              resource: "projector.right",
              asset: "ghost-video",
              required: true,
              start_delay_ms: 1000,
              end_behavior: "hold_last_frame",
            },
          ],
          lighting: [],
        },
      ],
    },
  };
}

function photo() {
  return {
    id: "front",
    installation_id: "house",
    name: "Front of house",
    viewpoint: "street",
    original_filename: "front.jpg",
    media_type: "image/jpeg",
    digest_sha256: "0".repeat(64),
    size_bytes: 1,
    orientation_degrees: 0,
    crop_x: null,
    crop_y: null,
    crop_width: null,
    crop_height: null,
    scale_hint: null,
    revision: 1,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
  };
}

function surface(id: string, name: string) {
  return {
    id,
    installation_id: "house",
    photo_id: "front",
    kind: "projector_surface",
    name,
    semantic_id: id,
    landmark_ids: [],
    color: "#a855f7",
    label_visible: true,
    locked: false,
    hidden: false,
    z_index: 0,
    direction: "forward",
    start_index: null,
    pixel_count: null,
    stage_points: null,
    points: id === "right-surface" ? surfacePoints : surfacePoints.map((point) => ({ x: point.x - 0.5, y: point.y })),
    closed: true,
    revision: 1,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
  };
}

function mapping() {
  return {
    geometry_id: "right-surface",
    resource: "projector.right",
    native_width: 1920,
    native_height: 1080,
    fit_mode: "preserve",
    photo_views: [{ geometry_id: "right-surface", photo_id: "front", points: surfacePoints, homography: null }],
  };
}
