import { expect, test } from "@playwright/test";

const installationId = "demo-installation";
const photoId = "compound-angle-photo";
const geometryId = "compound-angle-surface";
const points = [
  { x: 0.15, y: 0.2 },
  { x: 0.86, y: 0.08 },
  { x: 0.92, y: 0.84 },
  { x: 0.08, y: 0.75 },
];
const homography = [0.00037, -0.00006, 0.15, -0.00006, 0.00051, 0.2, 0.00001, 0.00002, 1];

let selectedAssetId: string | null = "asset-still-a";

test("compound-angle mapping reuses one homography for multiple assets", async ({ page }) => {
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith(`/${installationId}/photos`)) {
      return route.fulfill({ json: [photo()] });
    }
    if (url.pathname.endsWith(`/${installationId}/geometry`)) {
      return route.fulfill({ json: [surface()] });
    }
    if (url.pathname.endsWith("/assets/image-library")) {
      return route.fulfill({
        json: [asset("asset-still-a", "Window still A"), asset("asset-still-b", "Window still B")],
      });
    }
    if (
      url.pathname.endsWith(`/${geometryId}/projector-mapping`) &&
      request.method() === "GET"
    ) {
      return route.fulfill({ json: mapping() });
    }
    if (
      url.pathname.endsWith(`/${geometryId}/projector-mapping`) &&
      request.method() === "PUT"
    ) {
      selectedAssetId = request.postDataJSON().content_asset_id;
      return route.fulfill({ json: mapping() });
    }
    if (
      url.pathname.endsWith(`/installation-geometry/${geometryId}`) &&
      request.method() === "PUT"
    ) {
      expect(request.postDataJSON().points).toEqual(points);
      return route.fulfill({ json: { ...surface(), revision: 2 } });
    }
    if (url.pathname.includes("/assets/asset-still-") && url.pathname.endsWith("/review")) {
      return route.fulfill({
        contentType: "image/svg+xml",
        body: `<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900"><rect width="1600" height="900" fill="${
          url.pathname.includes("still-a") ? "#7c3aed" : "#0891b2"
        }"/></svg>`,
      });
    }
    if (url.pathname.startsWith("/api/gui/v1/installation-photos/")) {
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600"><path d="M0 600 L130 40 L1000 0 L900 600 Z" fill="#222"/></svg>',
      });
    }
    if (
      url.pathname.endsWith("/projector-overlaps") ||
      url.pathname.endsWith("/geometry-suggestions") ||
      url.pathname.endsWith("/discovery/endpoints")
    ) {
      return route.fulfill({ json: [] });
    }
    if (url.pathname.endsWith("/stage-coverage")) {
      return route.fulfill({
        json: {
          installation_id: installationId,
          minimum_x: 0,
          minimum_y: 0,
          maximum_x: 1,
          maximum_y: 1,
          regions: [],
        },
      });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Installation", exact: true }).click();
  const mapper = page
    .getByRole("heading", { name: "Visual Mapper" })
    .locator("xpath=ancestor::section");

  await mapper.getByRole("button", { name: "Edit" }).click();
  const content = mapper.getByLabel("Calibration content");
  await expect(content).toHaveValue("asset-still-a");
  await expect(mapper.getByAltText("Selected projector mapping content")).toHaveAttribute(
    "src",
    "/api/gui/v1/assets/asset-still-a/artifacts/review",
  );
  await expect(mapper.getByText(/Homography:/)).toContainText("0.00037");

  await content.selectOption("asset-still-b");
  await mapper.getByRole("button", { name: "Update geometry" }).click();

  await expect.poll(() => selectedAssetId).toBe("asset-still-b");
  expect(surface().points).toEqual(points);
  expect(mapping().homography).toEqual(homography);
  await expect(mapper.getByAltText("Selected projector mapping content")).toHaveAttribute(
    "src",
    "/api/gui/v1/assets/asset-still-b/artifacts/review",
  );
});

function photo() {
  return {
    id: photoId,
    installation_id: installationId,
    name: "Angled window wall",
    viewpoint: "Front compound angle",
    original_filename: "angle.jpg",
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
    created_at: "2026-10-14T12:00:00Z",
    updated_at: "2026-10-14T12:00:00Z",
  };
}

function surface() {
  return {
    id: geometryId,
    installation_id: installationId,
    photo_id: photoId,
    kind: "projector_surface",
    name: "Angled window projection",
    semantic_id: "semantic-window-projection",
    landmark_ids: ["corner-tl", "corner-tr", "corner-br", "corner-bl"],
    points,
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

function mapping() {
  return {
    geometry_id: geometryId,
    resource: "projector.demo",
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
    content_asset_id: selectedAssetId,
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
    homography,
    diagnostic: null,
    fit_diagnostics: [],
    photo_views: [
      {
        geometry_id: geometryId,
        photo_id: photoId,
        points,
        homography,
      },
    ],
    revision: 1,
    created_at: "2026-10-14T12:00:00Z",
    updated_at: "2026-10-14T12:00:00Z",
  };
}

function asset(id: string, name: string) {
  return {
    id,
    project_id: "demo-project",
    name,
    status: "ready",
    current_revision: 1,
    metadata_revision: 1,
    owner: null,
    license: null,
    provenance: null,
    review_status: "approved",
    expires_on: null,
    folder: null,
    tags: [],
    favorite: false,
    error: null,
    digest_sha256: id.padEnd(64, "0"),
    size_bytes: 1024,
    media_type: "image/png",
    original_filename: `${id}.png`,
    scan_status: "unavailable",
    metadata: {
      status: "ready",
      artifacts: { review: { media_type: "image/jpeg", size_bytes: 128 } },
    },
    original_file_status: "available",
    artifact_faults: [],
    references: [],
    unused: true,
    created_at: "2026-10-14T12:00:00Z",
    updated_at: "2026-10-14T12:00:00Z",
  };
}
