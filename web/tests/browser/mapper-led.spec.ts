import { expect, test } from "@playwright/test";

const installationId = "demo-installation";
const semanticId = "semantic-roofline";
const pixels = Array.from({ length: 5 }, (_, offset) => ({
  output_index: 100 + offset,
  local_x: offset * 0.5,
  local_y: 0.25,
  stage_x: 5 + offset * 0.5,
  stage_y: 3.25,
  footprint_width: 0.02,
  footprint_height: 0.03,
}));

test("linked LED views preserve physical order and support pre-live preview", async ({
  page,
}) => {
  await page.route("**/api/gui/v1/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith(`/${installationId}/photos`)) {
      return route.fulfill({
        json: [photo("wide-led", "Wide roofline"), photo("close-led", "Roofline close-up")],
      });
    }
    if (url.pathname.endsWith(`/${installationId}/geometry`)) {
      return route.fulfill({
        json: [
          segment("segment-wide", "wide-led", 0.1, 0.9),
          segment("segment-close", "close-led", 0.25, 0.65),
        ],
      });
    }
    if (url.pathname.includes("/lighting-surface")) {
      const geometryId = url.pathname.split("/").at(-2)!;
      return route.fulfill({ json: surface(geometryId) });
    }
    if (url.pathname.startsWith("/api/gui/v1/installation-photos/")) {
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600"><rect width="1000" height="600" fill="#161d27"/></svg>',
      });
    }
    if (
      url.pathname.endsWith("/assets/image-library") ||
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
  const viewpoint = mapper.getByRole("combobox").first();
  const canvas = mapper.getByRole("img", { name: /Geometry editor/ });

  await mapper.getByRole("button", { name: "Edit" }).click();
  await expect(mapper.getByText(/5 pixels mapped from output index 100 to 104/)).toBeVisible();
  await expect(mapper.locator(".geometry-pixel-label").first()).toHaveText("100");
  await expect(mapper.locator(".geometry-pixel-label").last()).toHaveText("104");
  const wideCx = await mapper.locator(".geometry-pixel-footprint").first().getAttribute("cx");

  await mapper.getByRole("button", { name: "Preview traversal" }).click();
  await expect(canvas).toHaveClass(/geometry-canvas--traversal/);
  await mapper.getByRole("button", { name: "Stop traversal preview" }).click();
  await expect(canvas).not.toHaveClass(/geometry-canvas--traversal/);

  await viewpoint.selectOption("close-led");
  await mapper.getByRole("button", { name: "Edit" }).click();
  await expect(mapper.getByText(/5 pixels mapped from output index 100 to 104/)).toBeVisible();
  const closeCx = await mapper.locator(".geometry-pixel-footprint").first().getAttribute("cx");
  expect(closeCx).not.toBe(wideCx);
  expect(surface("segment-wide").pixels).toEqual(surface("segment-close").pixels);
});

function photo(id: string, name: string) {
  return {
    id,
    installation_id: installationId,
    name,
    viewpoint: name,
    original_filename: `${id}.jpg`,
    media_type: "image/jpeg",
    digest_sha256: "e".repeat(64),
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

function segment(id: string, photoId: string, x: number, y: number) {
  return {
    id,
    installation_id: installationId,
    photo_id: photoId,
    kind: "pixel_segment",
    name: "Roofline pixels",
    semantic_id: semanticId,
    landmark_ids: ["roof-start", "roof-end"],
    points: [{ x, y }, { x: x + 0.6, y }],
    closed: false,
    color: "#f2af45",
    label_visible: true,
    locked: false,
    hidden: false,
    z_index: 0,
    direction: "forward",
    start_index: 100,
    pixel_count: 5,
    revision: 1,
    created_at: "2026-10-14T12:00:00Z",
    updated_at: "2026-10-14T12:00:00Z",
  };
}

function surface(geometryId: string) {
  return {
    geometry_id: geometryId,
    resource: "wled-001",
    local_width: 2,
    local_height: 1,
    local_points: [[0, 0.25], [2, 0.25]],
    stage_matrix: [1, 0, 0, 1, 5, 3],
    fixture_id: "roofline",
    group_id: "front",
    path_id: "roof-path",
    strand_id: "strand-a",
    calibration_id: "roof-calibration",
    pixel_footprint_width: 0.02,
    pixel_footprint_height: 0.03,
    brightness_limit: 0.6,
    timing_offset_ms: -45,
    release_offset_ms: 20,
    pixels,
    revision: 1,
    created_at: "2026-10-14T12:00:00Z",
    updated_at: "2026-10-14T12:00:00Z",
  };
}
