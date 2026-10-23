import { expect, test } from "@playwright/test";
import { openLayout } from "./setup-workspace";

const installationId = "demo-installation";
const semanticId = "semantic-front-window";

const photos = [
  photo("wide", "Wide front", "Wide"),
  photo("left-close", "Left close-up", "Left close"),
  photo("right-close", "Right close-up", "Right close"),
  ...Array.from({ length: 9 }, (_, index) =>
    photo(`scale-${index}`, `Scale viewpoint ${index}`, `Scale ${index}`),
  ),
];

let geometry = [
  fixture("fixture-wide", "wide", 0.2, 0.45),
  fixture("fixture-left", "left-close", 0.4, 0.5),
  fixture("fixture-right", "right-close", 0.7, 0.55),
  ...Array.from({ length: 9 }, (_, index) =>
    fixture(`fixture-scale-${index}`, `scale-${index}`, 0.1 + index * 0.08, 0.4),
  ),
];

test("linked fixture synchronizes across twelve photo viewpoints", async ({ page }) => {
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (
      url.pathname ===
        `/api/gui/v1/installations/${installationId}/photos` &&
      request.method() === "GET"
    ) {
      return route.fulfill({ json: photos });
    }
    if (
      url.pathname ===
        `/api/gui/v1/installations/${installationId}/geometry` &&
      request.method() === "GET"
    ) {
      return route.fulfill({ json: geometry });
    }
    if (
      url.pathname.startsWith("/api/gui/v1/installation-geometry/") &&
      request.method() === "PUT"
    ) {
      const id = url.pathname.split("/").at(-1)!;
      const update = request.postDataJSON();
      geometry = geometry.map((item) =>
        item.semantic_id === semanticId
          ? {
              ...item,
              name: update.name,
              color: update.color,
              revision: item.id === id ? item.revision + 1 : item.revision + 1,
            }
          : item,
      );
      return route.fulfill({ json: geometry.find((item) => item.id === id) });
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
      return route.fulfill({ json: emptyCoverage() });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Installation", exact: true }).click();
  await openLayout(page);

  await expect(page.getByRole("heading", { name: "Installation Photos" })).toBeVisible();
  await expect(page.locator(".installation-photos").getByText("Wide front", { exact: true })).toBeVisible();
  await expect(page.locator(".installation-photos").getByText("Left close-up", { exact: true })).toBeVisible();
  await expect(page.locator(".installation-photos").getByText("Right close-up", { exact: true })).toBeVisible();

  const mapper = page
    .getByRole("heading", { name: "Visual Mapper" })
    .locator("xpath=ancestor::section");
  const viewpoint = mapper.getByRole("combobox").first();
  await expect(viewpoint.locator("option")).toHaveCount(13);
  const fixtureCircle = mapper.locator(".geometry-shape--fixture");

  await expect(fixtureCircle).toHaveAttribute("cx", "200");
  await mapper.getByRole("button", { name: "Edit" }).click();
  await mapper.getByRole("textbox", { name: "Geometry name" }).fill("Front window updated");
  await mapper.getByRole("button", { name: "Update geometry" }).click();

  const updatedName = mapper
    .locator(".geometry-list strong")
    .filter({ hasText: "Front window updated" });
  await expect(updatedName).toBeVisible();
  await viewpoint.selectOption("left-close");
  await expect(updatedName).toBeVisible();
  await expect(fixtureCircle).toHaveAttribute("cx", "400");
  await viewpoint.selectOption("right-close");
  await expect(updatedName).toBeVisible();
  await expect(fixtureCircle).toHaveAttribute("cx", "700");
});

function photo(id: string, name: string, viewpoint: string) {
  return {
    id,
    installation_id: installationId,
    name,
    viewpoint,
    original_filename: `${id}.jpg`,
    media_type: "image/jpeg",
    digest_sha256: id.padEnd(64, "0"),
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

function emptyCoverage() {
  return {
    installation_id: installationId,
    minimum_x: 0,
    minimum_y: 0,
    maximum_x: 1,
    maximum_y: 1,
    regions: [],
  };
}

function fixture(id: string, photoId: string, x: number, y: number) {
  return {
    id,
    installation_id: installationId,
    photo_id: photoId,
    kind: "fixture",
    name: "Front window",
    semantic_id: semanticId,
    landmark_ids: ["landmark-front-window"],
    points: [{ x, y }],
    closed: false,
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
