import { expect, Page, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import type { InstallationGeometry } from "../../src/api";
import { demoInstallation, demoInstallations, demoRuntime, demoTopology } from "../../src/demo/overview-fixtures";

const photo = {
  id: "front-photo", installation_id: "demo-installation", name: "Front porch", description: "Front porch",
  viewpoint: "Independent legacy view", original_filename: "front.png", media_type: "image/png",
  digest_sha256: "a".repeat(64), size_bytes: 1024, orientation_degrees: 0,
  crop_x: 0.1, crop_y: 0.1, crop_width: 0.8, crop_height: 0.8, scale_hint: 1e-10,
  revision: 1, created_at: "2026-10-20T12:00:00Z", updated_at: "2026-10-20T12:00:00Z",
};
const object: InstallationGeometry = {
  id: "roof", installation_id: "demo-installation", photo_id: photo.id,
  semantic_id: "original-roof", kind: "pixel_segment", name: "Roofline",
  points: [{ x: 0.2, y: 0.3 }, { x: 0.8, y: 0.3 }], closed: false,
  color: "#6ee7ff", label_visible: true, locked: false, hidden: false, z_index: 0,
  direction: "forward", start_index: 0, pixel_count: 10, stage_points: null,
  landmark_ids: [], revision: 1, created_at: "", updated_at: "",
};

async function setup(page: Page, deviceFault = false, noPhoto = false) {
  await page.context().addCookies([{ name: "cueverge_csrf", value: "setup-csrf", url: "http://127.0.0.1:4173" }]);
  const state = {
    photos: noPhoto ? [] : [structuredClone(photo)], objects: noPhoto ? [] : [structuredClone(object)],
    descriptions: [] as Record<string, unknown>[], geometryMethods: [] as string[],
    uploads: [] as Record<string, string>[], failSurfaceOnce: false,
    replacements: [] as Record<string, string>[],
  };
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/session")) return route.fulfill({ json: { username: "admin", role: "administrator" } });
    if (path.endsWith("/installation")) return route.fulfill({ json: demoInstallation });
    if (path.endsWith("/installations")) return route.fulfill({ json: demoInstallations });
    if (path.endsWith("/runtime/status")) return route.fulfill({ json: demoRuntime });
    if (path.endsWith("/topology")) return route.fulfill({ json: deviceFault ? {
      ...demoTopology, nodes: demoTopology.nodes.map((node, index) =>
        index === 1 ? { ...node, artifact_faults: ["Original simulated storage fault"], failed_transfers: 1 } : node),
    } : demoTopology });
    if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method()) && request.headers()["x-csrf-token"] !== "setup-csrf") return route.fulfill({ status: 403 });
    if (path.endsWith("/front-photo/content") && request.method() === "PUT") {
      state.replacements.push(request.headers());
      state.photos[0] = { ...state.photos[0], original_filename: decodeURIComponent(request.headers()["x-original-filename"]),
        digest_sha256: "b".repeat(64), revision: state.photos[0].revision + 1 };
      return route.fulfill({ json: state.photos[0] });
    }
    if (path.endsWith("/installation-photos/front-photo") && request.method() === "DELETE") {
      state.photos = [];
      state.objects = state.objects.map((value) => ({ ...value, photo_id: null, revision: value.revision + 1 }));
      return route.fulfill({ status: 204 });
    }
    if (path.endsWith("/photos")) {
      if (request.method() === "POST") {
        state.uploads.push(request.headers());
        const description = decodeURIComponent(request.headers()["x-photo-description"]);
        state.photos.push({ ...photo, id: "new-photo", name: description, description, viewpoint: description });
        return route.fulfill({ status: 201, json: state.photos.at(-1) });
      }
      return route.fulfill({ json: state.photos });
    }
    if (path.endsWith("/description")) {
      const body = request.postDataJSON();
      state.descriptions.push(body);
      state.photos[0] = { ...state.photos[0], name: body.description, description: body.description, revision: 2 };
      return route.fulfill({ json: state.photos[0] });
    }
    if (path.endsWith("/geometry")) {
      if (request.method() === "POST") {
        state.geometryMethods.push("POST");
        const saved = { ...object, ...request.postDataJSON(), id: "new-roof", semantic_id: "new-roof", revision: 1 };
        state.objects.push(saved);
        return route.fulfill({ status: 201, json: saved });
      }
      return route.fulfill({ json: state.objects });
    }
    if (path.includes("/installation-geometry/") && !path.endsWith("/lighting-surface")) {
      state.geometryMethods.push(request.method());
      return route.fulfill({ json: { ...state.objects.at(-1), ...request.postDataJSON(), revision: 2 } });
    }
    if (path.endsWith("/lighting-surface")) {
      if (request.method() === "PUT" && state.failSurfaceOnce) {
        state.failSurfaceOnce = false;
        return route.fulfill({ status: 500, json: { detail: "Original simulated mapping failure" } });
      }
      return route.fulfill({ json: {
        geometry_id: path.split("/").at(-2), semantic_id: "original-roof",
        resource: "roofline.demo", local_width: 1, local_height: 1,
        local_points: [[0.2, 0.3], [0.8, 0.3]], stage_matrix: [1, 0, 0, 1, 0, 0],
        fixture_id: null, group_id: null, path_id: null, strand_id: "lower",
        calibration_id: null, pixel_footprint_width: 0.01, pixel_footprint_height: 0.01,
        brightness_limit: 1, timing_offset_ms: 0, release_offset_ms: 0,
        pixel_overrides: [], pixels: [], revision: 1,
      } });
    }
    if (path.includes("/installation-photos/")) return route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600"><rect width="1000" height="600" fill="#172331"/></svg>',
    });
    if (path.endsWith("/stage-coverage")) return route.fulfill({ json: {
      installation_id: "demo-installation", minimum_x: 0, minimum_y: 0, maximum_x: 1, maximum_y: 1, regions: [],
    } });
    return route.fulfill({ json: [] });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Installation", exact: true }).click();
  await expect(page.getByRole("button", { name: noPhoto ? "Open layout without a photo" : "Open Layout", exact: true })).toBeVisible();
  return state;
}

test("replacing the reference photo preserves object identities and requests alignment review", async ({ page }) => {
  const state = await setup(page);
  const original = structuredClone(state.objects);
  await page.getByRole("button", { name: "Replace photo", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Photo details" });
  const replace = dialog.getByRole("button", { name: "Replace photo, keep layout" });
  await dialog.getByLabel("Replacement photo", { exact: true }).setInputFiles({
    name: "new-front.png", mimeType: "image/png", buffer: Buffer.from("original synthetic upload fixture"),
  });
  await expect(replace).toBeDisabled();
  await dialog.getByLabel("I will review the new background alignment", { exact: false }).check();
  await replace.click();
  await expect(dialog).toHaveCount(0);
  expect(state.objects).toEqual(original);
  expect(state.photos[0].id).toBe(photo.id);
  expect(state.photos[0].viewpoint).toBe(photo.viewpoint);
  expect(state.replacements[0]).toMatchObject({ "if-match": '"1"', "x-confirm-alignment-review": "true" });
  await expect(page.getByRole("img", { name: "Front porch", exact: true })).toHaveAttribute("src", /revision=2/);
});

test("an installation without photos has an editable layout and visible add controls", async ({ page }) => {
  const state = await setup(page, false, true);
  await page.getByRole("button", { name: "Open layout without a photo", exact: true }).click();
  await expect(page.getByRole("button", { name: "LED segment", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "LED segment", exact: true }).click();
  const canvas = page.getByRole("img", { name: "Installation layout canvas", exact: true });
  await expect(canvas).toBeVisible();
  const box = await canvas.boundingBox();
  if (!box) throw new Error("Missing layout canvas");
  await canvas.click({ position: { x: box.width * 0.2, y: box.height * 0.3 } });
  await canvas.click({ position: { x: box.width * 0.8, y: box.height * 0.3 } });
  await page.getByLabel("Geometry name", { exact: true }).fill("No-photo LEDs");
  await page.getByLabel("First pixel index", { exact: true }).fill("0");
  await page.getByLabel("Pixel count", { exact: true }).fill("8");
  await page.getByRole("button", { name: "Save segment", exact: true }).click();
  await expect.poll(() => state.objects.length).toBe(1);
  expect(state.objects[0].photo_id).toBeNull();
  expect(state.geometryMethods).toEqual(["POST"]);
  await expect(page.getByText("No-photo LEDs", { exact: true }).first()).toBeVisible();
});

test("removing a photo leaves existing layout objects editable without a background", async ({ page }) => {
  const state = await setup(page);
  await page.getByRole("button", { name: "Open Layout", exact: true }).click();
  await page.getByRole("button", { name: "Photo details", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Photo details" });
  await dialog.locator("summary").filter({ hasText: "Remove photo" }).click();
  page.once("dialog", (value) => value.accept());
  await dialog.getByRole("button", { name: "Remove photo", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("img", { name: "Installation layout canvas", exact: true })).toBeVisible();
  expect(state.objects[0].id).toBe(object.id);
  expect(state.objects[0].photo_id).toBeNull();
  await page.locator(".geometry-list").getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.getByLabel("Geometry name", { exact: true })).toHaveValue("Roofline");
});

test("photo-first setup hides metadata and description editing preserves legacy details", async ({ page }) => {
  const state = await setup(page);
  await expect(page.getByRole("heading", { name: "Installation Photos" })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Geometry properties" })).toHaveCount(0);
  await expect(page.getByLabel("Named viewpoint", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Photo details", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Photo details", exact: true });
  await expect(dialog.getByLabel("Photo description", { exact: true })).toHaveValue("Front porch");
  await expect(dialog.getByLabel("Crop width", { exact: true })).toBeHidden();
  await dialog.getByLabel("Photo description", { exact: true }).fill("Front steps");
  await dialog.getByRole("button", { name: "Save photo", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(state.descriptions).toEqual([{ description: "Front steps" }]);
  expect(state.photos[0].viewpoint).toBe("Independent legacy view");
  expect(state.photos[0].crop_width).toBe(0.8);
});

test("photo upload asks for one description and sends encoded metadata explicitly", async ({ page }) => {
  const state = await setup(page);
  await page.getByLabel("Photo description", { exact: true }).fill("Front \u{1f383}");
  await page.getByLabel("Photo file", { exact: true }).setInputFiles({
    name: "front-\u00e9.png", mimeType: "image/png", buffer: Buffer.from("original synthetic image"),
  });
  expect(state.uploads).toHaveLength(0);
  await page.getByRole("button", { name: "Add installation photo", exact: true }).click();
  await expect.poll(() => state.uploads.length).toBe(1);
  expect(decodeURIComponent(state.uploads[0]["x-photo-description"])).toBe("Front \u{1f383}");
  expect(state.uploads[0]["x-photo-metadata-encoding"]).toBe("uri");
  expect(state.uploads[0]).not.toHaveProperty("x-photo-viewpoint");
});

test("Layout starts in Select and selecting a shape reveals only its contextual properties", async ({ page }) => {
  const state = await setup(page);
  await page.getByRole("button", { name: "Open Layout", exact: true }).click();
  const mapper = page.locator(".geometry-mapper");
  await expect(mapper.getByRole("button", { name: "Select", exact: true }).first()).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("complementary", { name: "Geometry properties" })).toHaveCount(0);
  const canvas = mapper.getByRole("img", { name: /Geometry editor/ });
  await canvas.click({ position: { x: 25, y: 25 } });
  await expect(page.getByRole("complementary", { name: "Geometry properties" })).toHaveCount(0);
  const target = await mapper.locator("[data-layout-object='roof'] .layout-hit-target").evaluate((element) => {
    if (!(element instanceof SVGGraphicsElement)) throw new Error("Missing SVG selection target");
    const bounds = element.getBBox(), matrix = element.getScreenCTM();
    if (!matrix) throw new Error("Missing selection transform");
    const point = new DOMPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2).matrixTransform(matrix);
    return { x: point.x, y: point.y };
  });
  await page.mouse.click(target.x, target.y);
  const inspector = page.getByRole("complementary", { name: "Geometry properties" });
  await expect(inspector.getByLabel("Geometry name")).toHaveValue("Roofline");
  await expect(inspector.getByLabel("Strand ID")).toBeHidden();
  expect(state.geometryMethods).toEqual([]);
});

test("a mapping failure retries the saved object rather than creating duplicates", async ({ page }) => {
  const state = await setup(page);
  await page.getByRole("button", { name: "Open Layout", exact: true }).click();
  await page.getByText("Add to photo", { exact: true }).click();
  await page.getByRole("button", { name: "LED segment", exact: true }).click();
  const inspector = page.getByRole("complementary", { name: "Geometry properties" });
  await inspector.getByLabel("Geometry name").fill("Second roofline");
  await inspector.getByLabel("First pixel index").fill("20");
  await inspector.getByLabel("Pixel count", { exact: true }).fill("10");
  const canvas = page.getByRole("img", { name: /Geometry editor/ });
  await canvas.click({ position: { x: 80, y: 80 } });
  await canvas.click({ position: { x: 180, y: 80 } });
  state.failSurfaceOnce = true;
  await inspector.getByRole("button", { name: "Save segment", exact: true }).click();
  await expect(inspector.getByText(/layout object was saved, but its mapping/)).toBeVisible();
  await inspector.getByRole("button", { name: "Update geometry", exact: true }).click();
  await expect(inspector).toHaveCount(0);
  expect(state.geometryMethods).toEqual(["POST", "PUT"]);
});

test("device cards keep telemetry contextual while failures stay visible", async ({ page }) => {
  await setup(page, true);
  await page.getByRole("group", { name: "Installation workspace", exact: true }).getByRole("button", { name: "Devices", exact: true }).click();
  await expect(page.getByText("400 GB free of 500 GB", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Original simulated storage fault", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Details for media-client-left", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Device details: media-client-left", exact: true })).toBeVisible();
  await expect(page.getByText("400 GB free of 500 GB", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("button", { name: "Add a device", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Capability document", exact: true })).toBeHidden();
});

test("compact setup fits a phone and the photo editor has accessible controls", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole("button", { name: "Photo details", exact: true }).click();
  const results = await new AxeBuilder({ page }).include(".setup-dialog").analyze();
  expect(results.violations.filter((value) => ["serious", "critical"].includes(value.impact ?? ""))).toEqual([]);
});
