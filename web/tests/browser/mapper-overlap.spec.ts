import { expect, test } from "@playwright/test";
import { openLayout } from "./setup-workspace";

const installationId = "demo-installation";
const firstId = "surface-left";
const secondId = "surface-right";
const sourceArtwork = {
  [firstId]: "asset-left-original",
  [secondId]: "asset-right-original",
};
let savedOverlap: Record<string, unknown> | null = null;

test("overlap calibration is tunable and non-destructive", async ({ page }) => {
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith(`/${installationId}/photos`)) {
      return route.fulfill({ json: [photo()] });
    }
    if (url.pathname.endsWith(`/${installationId}/geometry`)) {
      return route.fulfill({
        json: [
          surface(firstId, "Left projector", 0.05),
          surface(secondId, "Right projector", 0.45),
          motionPath(),
        ],
      });
    }
    if (url.pathname.endsWith("/assets/image-library")) {
      return route.fulfill({ json: [] });
    }
    if (url.pathname.endsWith(`/${firstId}/projector-mapping`)) {
      return route.fulfill({ json: mapping(firstId) });
    }
    if (url.pathname.endsWith(`/${secondId}/projector-mapping`)) {
      return route.fulfill({ json: mapping(secondId) });
    }
    if (
      url.pathname.endsWith(`/${installationId}/projector-overlaps`) &&
      request.method() === "GET"
    ) {
      return route.fulfill({ json: [overlap()] });
    }
    if (
      url.pathname.endsWith(`/${installationId}/projector-overlaps`) &&
      request.method() === "PUT"
    ) {
      savedOverlap = request.postDataJSON();
      return route.fulfill({ json: { ...overlap(), ...savedOverlap, revision: 2 } });
    }
    if (url.pathname.startsWith("/api/gui/v1/installation-photos/")) {
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600"><rect width="1000" height="600" fill="#171717"/></svg>',
      });
    }
    if (
      url.pathname.endsWith("/geometry-suggestions") ||
      url.pathname.endsWith("/discovery/endpoints")
    ) {
      return route.fulfill({ json: [] });
    }
    if (url.pathname.endsWith("/stage-coverage")) {
      return route.fulfill({ json: coverage() });
    }
    if (url.pathname.endsWith(`/${installationId}/stage-reference`)) {
      return route.fulfill({ json: stageMap() });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Installation", exact: true }).click();
  await openLayout(page);
  const mapper = page
    .getByRole("heading", { name: "Visual Mapper" })
    .locator("xpath=ancestor::section");
  const stageMapStatus = page.getByRole("status", { name: "Stage map status" });
  await expect(stageMapStatus).toContainText("Stage view: Two-window elevation");
  await expect(stageMapStatus).toContainText("10 × 6 ft");
  await expect(page.getByText(/projector\.left and projector\.right overlap 12%/)).toBeVisible();

  const coverageToggle = mapper.getByRole("button", { name: "Show projector coverage" });
  await coverageToggle.click();
  await expect(mapper.getByRole("button", { name: "Hide projector coverage" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const key = mapper.getByRole("list", { name: "Projector coverage key" });
  await expect(key).toContainText("One projector");
  await expect(key).toContainText("Projectors overlap (blend the seam)");
  await expect(key).toContainText("Blocked by a mask");
  await expect(key).toContainText("Between projectors, no coverage");
  const regions = mapper.locator(".stage-coverage-overlay polygon");
  await expect(regions).toHaveCount(4);
  await expect(mapper.locator(".stage-coverage-overlay .stage-region--overlap")).toHaveAttribute(
    "points",
    "300,100 400,100 400,225 300,225",
  );
  await mapper.getByRole("button", { name: "Edit" }).first().click();
  await mapper.getByText("Multi-projector overlap calibration", { exact: true }).click();
  await mapper.getByLabel("Paired projector surface").selectOption(secondId);
  await mapper.getByLabel("Overlap gamma").fill("1.35");
  await mapper.getByLabel("First brightness").fill("0.82");
  await mapper.getByLabel("Second black level").fill("0.04");

  const preview = mapper.getByLabel("Non-destructive overlap calibration preview");
  await mapper.getByRole("button", { name: "Native preview" }).click();
  await expect(preview).toHaveClass(/overlap-calibration-preview--native/);
  await mapper.getByRole("button", { name: "Mapped preview" }).click();
  await expect(preview).toHaveClass(/overlap-calibration-preview--mapped/);
  await mapper.getByRole("button", { name: "Save overlap calibration" }).click();

  await expect.poll(() => savedOverlap?.overlap_gamma).toBe(1.35);
  expect(savedOverlap?.first_brightness).toBe(0.82);
  expect(savedOverlap?.second_black_level).toBe(0.04);
  expect(sourceArtwork).toEqual({
    [firstId]: "asset-left-original",
    [secondId]: "asset-right-original",
  });
});

function photo() {
  return {
    id: "overlap-photo",
    installation_id: installationId,
    name: "Two-window elevation",
    viewpoint: "Wide front",
    original_filename: "overlap.jpg",
    media_type: "image/jpeg",
    digest_sha256: "d".repeat(64),
    size_bytes: 1024,
    orientation_degrees: 0,
    crop_x: null,
    crop_y: null,
    crop_width: null,
    crop_height: null,
    scale_hint: null,
    revision: 1,
    created_at: "2026-10-14T12:00:00Z",
    updated_at: "2026-10-14T12:00:00Z",
  };
}

function stageMap() {
  return {
    installation_id: installationId,
    reference: {
      installation_id: installationId,
      photo_id: "overlap-photo",
      unit_label: "ft",
      units_per_canvas: 0.01,
      stage_width: 10,
      stage_height: 6,
      scale_points: null,
      scale_distance: null,
      revision: 1,
      applied_at: "2026-10-23T12:00:00Z",
      created_at: "2026-10-23T12:00:00Z",
      updated_at: "2026-10-23T12:00:00Z",
    },
    counts: { placed: 3, needs_update: 0, manual: 0, unplaced: 0 },
    needs_update: false,
    items: [],
    projector_overlaps: [{ resources: ["projector.left", "projector.right"], fraction: 0.12 }],
  };
}

function coverage() {
  return {
    installation_id: installationId,
    minimum_x: 0,
    minimum_y: 0,
    maximum_x: 7,
    maximum_y: 3.25,
    regions: [
      {
        kind: "covered",
        points: [[0, 0], [3, 0], [3, 2.25], [0, 2.25]],
        resources: ["projector.left"],
        source_ids: [firstId],
      },
      {
        kind: "overlap",
        points: [[3, 1], [4, 1], [4, 2.25], [3, 2.25]],
        resources: ["projector.left", "projector.right"],
        source_ids: [firstId, secondId],
      },
      {
        kind: "uncovered",
        points: [[4, 0], [7, 0], [7, 1], [4, 1]],
        resources: [],
        source_ids: [],
      },
      {
        kind: "occluded",
        points: [[1, 1], [2, 1], [2, 2], [1, 2]],
        resources: ["projector.left"],
        source_ids: [firstId],
      },
    ],
  };
}

function motionPath() {
  return {
    id: "ghost-motion",
    installation_id: installationId,
    photo_id: "overlap-photo",
    kind: "path",
    name: "Ghost crossing",
    semantic_id: "ghost-crossing",
    landmark_ids: [],
    points: [{ x: 0.1, y: 0.5 }, { x: 0.9, y: 0.5 }],
    stage_points: [[0.5, 1.5], [6.5, 1.5]],
    closed: false,
    color: "#ffffff",
    label_visible: true,
    locked: false,
    hidden: false,
    z_index: 2,
    direction: "forward",
    start_index: null,
    pixel_count: null,
    revision: 1,
    created_at: "2026-10-14T12:00:00Z",
    updated_at: "2026-10-14T12:00:00Z",
  };
}

function surface(id: string, name: string, x: number) {
  return {
    id,
    installation_id: installationId,
    photo_id: "overlap-photo",
    kind: "projector_surface",
    name,
    semantic_id: `semantic-${id}`,
    landmark_ids: [`${id}-tl`, `${id}-tr`, `${id}-br`, `${id}-bl`],
    points: [
      { x, y: 0.2 },
      { x: x + 0.5, y: 0.18 },
      { x: x + 0.5, y: 0.8 },
      { x, y: 0.82 },
    ],
    closed: true,
    color: "#6ee7ff",
    label_visible: true,
    locked: false,
    hidden: false,
    z_index: 0,
    direction: "forward",
    start_index: null,
    pixel_count: null,
    revision: 1,
    created_at: "2026-10-14T12:00:00Z",
    updated_at: "2026-10-14T12:00:00Z",
  };
}

function mapping(id: string) {
  return {
    geometry_id: id,
    resource: id === firstId ? "projector.left" : "projector.right",
    native_width: 1920,
    native_height: 1080,
    stage_x: id === firstId ? 0 : 3,
    stage_y: 0,
    stage_width: 4,
    stage_height: 2.25,
    crop_x: 0,
    crop_y: 0,
    crop_width: 1,
    crop_height: 1,
    content_asset_id: sourceArtwork[id],
    surface_model: "planar",
    fit_mode: "preserve",
    overscan: false,
    brightness_limit: 1,
    media_start_delay_ms: 0,
    media_release_delay_ms: 0,
    orientation_degrees: 0,
    mirror_x: false,
    mirror_y: false,
    mask_semantic_ids: [],
    homography: [1, 0, 0, 0, 1, 0, 0, 0, 1],
    diagnostic: null,
    fit_diagnostics: [],
    photo_views: [
      {
        geometry_id: id,
        photo_id: "overlap-photo",
        points: surface(id, "", id === firstId ? 0.05 : 0.45).points,
        homography: [1, 0, 0, 0, 1, 0, 0, 0, 1],
      },
    ],
    revision: 1,
    created_at: "2026-10-14T12:00:00Z",
    updated_at: "2026-10-14T12:00:00Z",
  };
}

function overlap() {
  return {
    id: "overlap-left-right",
    installation_id: installationId,
    first_geometry_id: firstId,
    second_geometry_id: secondId,
    stage_points: [[3, 0], [4, 0], [4, 2.25], [3, 2.25]],
    ramp_width: 0.2,
    overlap_gamma: 1,
    first_brightness: 0.9,
    second_brightness: 0.9,
    first_white: [1, 1, 1],
    second_white: [1, 1, 1],
    first_color_matrix: [1, 0, 0, 0, 1, 0, 0, 0, 1],
    second_color_matrix: [1, 0, 0, 0, 1, 0, 0, 0, 1],
    first_black_level: 0.02,
    second_black_level: 0.02,
    diagnostics: ["Combined overlap brightness may appear double-bright."],
    revision: 1,
    created_at: "2026-10-14T12:00:00Z",
    updated_at: "2026-10-14T12:00:00Z",
  };
}
