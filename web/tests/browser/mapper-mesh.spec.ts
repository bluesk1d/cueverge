import { expect, test } from "@playwright/test";
import { openLayout, openProjectorDetails } from "./setup-workspace";

let savedMapping: Record<string, unknown> | null = null;

test("non-planar projector surface edits an indexed UV mesh", async ({ page }) => {
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/demo-installation/photos")) {
      return route.fulfill({ json: [photo()] });
    }
    if (url.pathname.endsWith("/demo-installation/geometry")) {
      return route.fulfill({ json: [surface()] });
    }
    if (url.pathname.endsWith("/projector-surface/projector-mapping")) {
      if (request.method() === "PUT") {
        savedMapping = request.postDataJSON();
      }
      return route.fulfill({ json: mapping() });
    }
    if (
      url.pathname.endsWith("/installation-geometry/projector-surface") &&
      request.method() === "PUT"
    ) {
      return route.fulfill({ json: { ...surface(), revision: 2 } });
    }
    if (url.pathname.startsWith("/api/gui/v1/installation-photos/")) {
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600"><rect width="1000" height="600" fill="#18202b"/></svg>',
      });
    }
    if (
      url.pathname.endsWith("/projector-overlaps") ||
      url.pathname.endsWith("/geometry-suggestions") ||
      url.pathname.endsWith("/assets/image-library") ||
      url.pathname.endsWith("/discovery/endpoints")
    ) {
      return route.fulfill({ json: [] });
    }
    if (url.pathname.endsWith("/stage-coverage")) {
      return route.fulfill({
        json: {
          installation_id: "demo-installation",
          minimum_x: 0,
          minimum_y: 0,
          maximum_x: 4,
          maximum_y: 2.25,
          regions: [],
        },
      });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Installation", exact: true }).click();
  await openLayout(page);
  const mapper = page
    .getByRole("heading", { name: "Visual Mapper" })
    .locator("xpath=ancestor::section");
  await mapper.getByRole("button", { name: "Edit" }).click();
  await openProjectorDetails(mapper);

  await expect(mapper.getByLabel("Projection calibration source")).toHaveValue(
    "measured_grid",
  );
  await expect(mapper.getByLabel("Projection calibration revision")).toHaveValue("3");
  await expect(mapper.getByLabel("Projection mesh vertices")).toContainText(
    "0.5,0.5,2,1.2",
  );
  await expect(mapper.getByLabel("Projection mesh triangles")).toContainText(
    "3,0,4",
  );
  await expect(mapper.getByText(/indexed mesh 5 vertices \/ 4 triangles/)).toBeVisible();
  await expect(
    mapper.getByRole("img", { name: "Projector native calibrated warp preview" }),
  ).toBeVisible();
  await expect(
    mapper.getByRole("img", { name: "Installation calibrated warp preview" }),
  ).toBeVisible();
  await expect(mapper.getByText(/Installation warp · measured grid · revision 3/)).toBeVisible();
  await expect(
    mapper
      .getByRole("img", { name: "Projector native calibrated warp preview" })
      .locator("polygon"),
  ).toHaveCount(4);

  await mapper
    .getByLabel("Projection calibration source")
    .selectOption("easy_corner_pin");
  await mapper.getByLabel("Projection calibration revision").fill("4");
  await mapper.getByRole("button", { name: "Update geometry" }).click();

  await expect.poll(() => savedMapping).not.toBeNull();
  expect(savedMapping).toMatchObject({
    surface_model: "subdivided",
    calibration_source: "easy_corner_pin",
    calibration_revision: 4,
    mesh_triangles: [[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]],
  });
});

function photo() {
  return {
    id: "mesh-photo",
    installation_id: "demo-installation",
    name: "Mesh wall",
    viewpoint: "Front",
    original_filename: "mesh.jpg",
    media_type: "image/jpeg",
    digest_sha256: "a".repeat(64),
    size_bytes: 1024,
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

function surface() {
  return {
    id: "projector-surface",
    installation_id: "demo-installation",
    photo_id: "mesh-photo",
    kind: "projector_surface",
    name: "Curved wall",
    semantic_id: "curved-wall",
    landmark_ids: [],
    points: [
      { x: 0.1, y: 0.1 },
      { x: 0.9, y: 0.1 },
      { x: 0.9, y: 0.9 },
      { x: 0.1, y: 0.9 },
      { x: 0.5, y: 0.55 },
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
    created_at: "2026-10-15T12:00:00Z",
    updated_at: "2026-10-15T12:00:00Z",
  };
}

function mapping() {
  return {
    geometry_id: "projector-surface",
    resource: "projector.left",
    native_width: 1920,
    native_height: 1080,
    stage_x: 0,
    stage_y: 0,
    stage_width: 4,
    stage_height: 2.25,
    crop_x: 0,
    crop_y: 0,
    crop_width: 1,
    crop_height: 1,
    content_asset_id: null,
    surface_model: "subdivided",
    fit_mode: "preserve",
    overscan: false,
    brightness_limit: 1,
    media_start_delay_ms: 0,
    media_release_delay_ms: 0,
    orientation_degrees: 0,
    mirror_x: false,
    mirror_y: false,
    mask_semantic_ids: [],
    calibration_source: "measured_grid",
    calibration_revision: 3,
    mesh_vertices: [
      [0, 0, 0, 0],
      [1, 0, 4, 0],
      [1, 1, 4, 2.25],
      [0, 1, 0, 2.25],
      [0.5, 0.5, 2, 1.2],
    ],
    mesh_triangles: [[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]],
    homography: null,
    diagnostic: null,
    fit_diagnostics: [],
    photo_views: [],
    revision: 1,
    created_at: "2026-10-15T12:00:00Z",
    updated_at: "2026-10-15T12:00:00Z",
  };
}
