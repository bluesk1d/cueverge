import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { openLayout } from "./setup-workspace";

const installationId = "demo-installation";
const photoId = "mapper-front";
const output = "roofline.demo";

test("tracing uses visible labels, one pixel count, and preserves save payloads", async ({ page }) => {
  const writes = await setupMapper(page);
  const mapper = page.locator(".geometry-mapper");
  const inspector = page.getByRole("complementary", { name: "Geometry properties" });
  await expect(inspector.getByRole("heading", { name: "Segment properties" })).toBeVisible();
  await expect(inspector.getByRole("button", { name: "Trace on photo" })).toHaveAttribute("aria-pressed", "true");
  await expect(inspector.getByLabel("Pixel layout generator")).toBeHidden();
  await expect(inspector.getByLabel("Local width")).toBeHidden();
  await expect(inspector.getByRole("spinbutton", { name: "Pixel count", exact: true })).toHaveCount(1);
  expect(await inspector.locator("input:visible, select:visible, textarea:visible").evaluateAll(
    (controls) => controls.every((control) =>
      Array.from((control as HTMLInputElement).labels ?? []).some((label) =>
        Boolean(label.textContent?.trim()),
      ),
    ),
  )).toBe(true);

  await inspector.getByLabel("Geometry name").fill("garage-eave");
  await inspector.getByLabel("First pixel index").fill("0");
  await inspector.getByLabel("Pixel count", { exact: true }).fill("162");
  await inspector.getByText("Optional strand label", { exact: true }).click();
  await inspector.getByLabel("Strand ID").fill("lower");
  await expect(inspector.getByRole("status")).toHaveText("Pixels 0–161 · 162 LEDs");
  await expect(inspector.getByText(/Pixel allocation overlaps/)).toHaveCount(0);

  await traceLine(page);
  await inspector.getByRole("button", { name: "Preview traversal", exact: true }).click();
  await expect(mapper.getByRole("img", { name: "Geometry editor for Front view" })).toHaveClass(/geometry-canvas--traversal/);
  expect(writes.maintenance).toBe(0);
  await inspector.getByRole("button", { name: "Save segment", exact: true }).click();
  await expect.poll(() => writes.geometry).toMatchObject({
    name: "garage-eave", kind: "pixel_segment", start_index: 0, pixel_count: 162,
    direction: "forward", closed: false,
  });
  await expect.poll(() => writes.surface).toMatchObject({
    resource: output, strand_id: "lower", fixture_id: null, group_id: null, path_id: null,
    local_width: 1, local_height: 1, stage_matrix: [1, 0, 0, 1, 0, 0],
  });
  expect(writes.projects).toBe(0);
  expect(writes.maintenance).toBe(0);
  await expect(inspector).toHaveCount(0);
});

test("allocation validates boundaries and warns only for other objects on the same output", async ({ page }) => {
  await setupMapper(page);
  const inspector = page.getByRole("complementary", { name: "Geometry properties" });
  await inspector.getByLabel("Geometry name").fill("next-segment");
  await traceLine(page);
  await inspector.getByLabel("First pixel index").fill("210");
  await inspector.getByLabel("Pixel count", { exact: true }).fill("20");
  await expect(inspector.getByText("Pixel allocation overlaps Existing roofline on roofline.demo.")).toBeVisible();
  await expect(inspector.getByText(/Pixel allocation overlaps/)).toHaveCount(1);
  await inspector.getByText("Grouping & associations", { exact: true }).click();
  await inspector.getByLabel("Link geometry across viewpoints").selectOption("semantic-existing");
  await expect(inspector.getByText(/Pixel allocation overlaps/)).toHaveCount(0);

  await inspector.getByLabel("First pixel index").fill("790");
  await expect(inspector.getByRole("alert")).toContainText("valid indices are 0–799");
  await expect(inspector.getByRole("button", { name: "Save segment", exact: true })).toBeDisabled();
  await inspector.getByLabel("First pixel index").fill("780");
  await expect(inspector.getByRole("status")).toHaveText("Pixels 780–799 · 20 LEDs");
  await expect(inspector.getByRole("button", { name: "Save segment", exact: true })).toBeEnabled();
  await inspector.getByLabel("First pixel index").fill("-1");
  await expect(inspector.getByRole("alert")).toContainText("whole-number");
  await expect(inspector.getByRole("button", { name: "Save segment", exact: true })).toBeDisabled();
});

test("generation shares the allocation count and derives grid and import counts", async ({ page }) => {
  await setupMapper(page);
  const inspector = page.getByRole("complementary", { name: "Geometry properties" });
  await inspector.getByRole("button", { name: "Generate layout", exact: true }).click();
  await inspector.getByLabel("First pixel index").fill("0");
  const count = inspector.getByLabel("Pixel count", { exact: true });
  await count.fill("162");
  await inspector.getByRole("button", { name: "Generate pixel layout", exact: true }).click();
  await expect(page.locator(".geometry-corner-handle")).toHaveCount(2);
  await expect(count).toHaveValue("162");
  await inspector.getByLabel("Pixel layout generator").selectOption("grid");
  await inspector.getByLabel("Pixel rows").fill("3");
  await inspector.getByLabel("Pixels per row").fill("5");
  await expect(count).toHaveValue("15");
  await expect(count).toHaveAttribute("readonly", "");
  await inspector.getByRole("button", { name: "Generate pixel layout", exact: true }).click();
  await expect(page.locator(".geometry-corner-handle")).toHaveCount(6);

  await inspector.getByLabel("Pixel layout generator").selectOption("import");
  await inspector.getByLabel("Imported normalized coordinates").fill("0.1,0.2\n0.5,0.4\n0.9,0.8");
  await expect(count).toHaveValue("3");
  await inspector.getByRole("button", { name: "Generate pixel layout", exact: true }).click();
  await expect(page.locator(".geometry-corner-handle")).toHaveCount(3);
  await inspector.getByRole("button", { name: "Trace on photo" }).click();
  await expect(count).toBeEditable();
  await expect(inspector.getByLabel("Pixel layout generator")).toBeHidden();
  await page.getByRole("button", { name: "Clear points", exact: true }).click();
  await expect(page.locator(".geometry-corner-handle")).toHaveCount(0);
  await expect(count).toHaveValue("3");
});

test("maintenance simulation is separate, accurately labeled, and reports status", async ({ page }) => {
  const writes = await setupMapper(page);
  const inspector = page.getByRole("complementary", { name: "Geometry properties" });
  const record = inspector.getByRole("button", { name: "Record index-chase simulation" });
  await expect(record).toBeHidden();
  await inspector.getByText("Maintenance preview", { exact: true }).click();
  await expect(inspector.getByText(/No physical LEDs are driven/)).toBeVisible();
  await record.click();
  await expect.poll(() => writes.maintenance).toBe(1);
  await expect(inspector.getByRole("status")).toContainText("no hardware command was sent");
  await inspector.getByRole("button", { name: "Dismiss preview record" }).click();
  await expect(inspector.getByText(/Simulation preview recorded/)).toHaveCount(0);
});

test("canvas and inspector are side by side on desktop and stack without overflow on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await setupMapper(page);
  const inspector = page.getByRole("complementary", { name: "Geometry properties" });
  const canvas = page.getByRole("img", { name: "Geometry editor for Front view" });
  const desktopCanvas = await canvas.boundingBox();
  const desktopInspector = await inspector.boundingBox();
  expect(desktopCanvas!.x + desktopCanvas!.width).toBeLessThanOrEqual(desktopInspector!.x);
  await page.setViewportSize({ width: 600, height: 900 });
  await expect.poll(async () => {
    const drawing = await canvas.boundingBox();
    const properties = await inspector.boundingBox();
    return drawing!.y + drawing!.height <= properties!.y;
  }).toBe(true);
  expect(await page.locator(".geometry-mapper").evaluate(
    (element) => element.scrollWidth <= element.clientWidth,
  )).toBe(true);
  await expect(inspector.getByLabel("First pixel index")).toBeVisible();
});

async function traceLine(page: Page) {
  const canvas = page.getByRole("img", { name: "Geometry editor for Front view" });
  const bounds = await canvas.boundingBox();
  await canvas.click({ position: { x: bounds!.width * 0.1, y: bounds!.height * 0.3 } });
  await canvas.click({ position: { x: bounds!.width * 0.8, y: bounds!.height * 0.3 } });
}

test("wheel zoom anchors the cursor and placement uses the transformed SVG coordinates", async ({ page }) => {
  await setupMapper(page);
  const canvas = page.getByRole("img", { name: "Geometry editor for Front view" });
  await canvas.scrollIntoViewIfNeeded();
  const bounds = (await canvas.boundingBox())!;
  const cursor = { x: Math.round(bounds.x + bounds.width * 0.7), y: Math.round(bounds.y + bounds.height * 0.4) };
  const worldAtCursor = () => canvas.evaluate((element, cursor) => {
    const svg = element as SVGSVGElement;
    return new DOMPoint(cursor.x, cursor.y).matrixTransform(svg.getScreenCTM()!.inverse()).toJSON();
  }, cursor);
  const before = await worldAtCursor();
  await page.mouse.move(cursor.x, cursor.y);
  await page.mouse.wheel(0, -100);
  await expect(page.getByLabel("Canvas zoom")).not.toHaveText("100%");
  const after = await worldAtCursor();
  expect(after.x).toBeCloseTo(before.x, 3);
  expect(after.y).toBeCloseTo(before.y, 3);
  await page.mouse.click(cursor.x, cursor.y);
  const handle = canvas.locator(".geometry-corner-handle");
  await expect(handle).toHaveCount(1);
  expect(Number(await handle.getAttribute("cx"))).toBeCloseTo(Math.round(before.x / 10) * 10, 3);
  expect(Number(await handle.getAttribute("cy"))).toBeCloseTo(Math.round(before.y / 6) * 6, 3);

  const radius = Number(await handle.getAttribute("r"));
  const zoom = Number((await page.getByLabel("Canvas zoom").textContent())!.replace("%", "")) / 100;
  expect(radius * zoom).toBeCloseTo(10, 1);
  await page.getByRole("button", { name: "Fit view", exact: true }).click();
  await expect(canvas).toHaveAttribute("viewBox", "0 0 1000 600");
  await expect(handle).toHaveCount(1);
});

test("Hand, Space and middle-button drags pan without creating or moving points", async ({ page }) => {
  await setupMapper(page);
  const canvas = page.getByRole("img", { name: "Geometry editor for Front view" });
  await traceLine(page);
  const original = await canvas.locator(".geometry-corner-handle").evaluateAll(
    (handles) => handles.map((handle) => [handle.getAttribute("cx"), handle.getAttribute("cy")]),
  );
  const bounds = (await canvas.boundingBox())!;
  const x = bounds.x + bounds.width * 0.5;
  const y = bounds.y + bounds.height * 0.5;
  for (const method of ["hand", "space", "middle"]) {
    await page.getByRole("button", { name: "Fit view", exact: true }).click();
    await page.getByRole("button", { name: method === "hand" ? "Hand" : "Draw", exact: true }).click();
    await canvas.focus();
    await page.mouse.move(x, y);
    if (method === "space") await page.keyboard.down("Space");
    const button = method === "middle" ? "middle" : "left";
    await page.mouse.down({ button });
    await page.mouse.move(x + 50, y + 25, { steps: 4 });
    await page.mouse.up({ button });
    if (method === "space") await page.keyboard.up("Space");
    await expect(canvas).not.toHaveAttribute("viewBox", "0 0 1000 600");
    expect(await canvas.locator(".geometry-corner-handle").evaluateAll(
      (handles) => handles.map((handle) => [handle.getAttribute("cx"), handle.getAttribute("cy")]),
    )).toEqual(original);
  }
  await page.getByRole("button", { name: "Fit view", exact: true }).click();
  await page.getByRole("button", { name: "Draw", exact: true }).click();
  await page.mouse.click(x, y);
  await expect(canvas.locator(".geometry-corner-handle")).toHaveCount(3);
});

test("point dragging survives leaving the canvas and works after zoom and letterboxing", async ({ page }) => {
  await setupMapper(page);
  const canvas = page.getByRole("img", { name: "Geometry editor for Front view" });
  // Force letterboxing to verify screen-to-world conversion rather than rect ratios.
  await canvas.evaluate((element) => { (element as SVGSVGElement).style.height = "420px"; });
  await traceLine(page);
  await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  const handle = canvas.locator(".geometry-corner-handle").first();
  const start = (await handle.boundingBox())!;
  await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
  await page.mouse.down();
  const bounds = (await canvas.boundingBox())!;
  await page.mouse.move(bounds.x - 20, bounds.y - 20, { steps: 3 });
  await page.mouse.move(bounds.x + bounds.width * 0.5, bounds.y + bounds.height * 0.5, { steps: 3 });
  await page.mouse.up();
  await expect(canvas.locator(".geometry-corner-handle")).toHaveCount(2);
  await expect(handle).toHaveAttribute("cx", "500");
  await expect(handle).toHaveAttribute("cy", "300");
});

test("trackpad scrolling pans, pinch zooms, and keyboard navigation has bounded zoom", async ({ page }) => {
  await setupMapper(page);
  const canvas = page.getByRole("img", { name: "Geometry editor for Front view" });
  await canvas.scrollIntoViewIfNeeded();
  const bounds = (await canvas.boundingBox())!;
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.mouse.wheel(12, 8);
  await expect(canvas).not.toHaveAttribute("viewBox", "0 0 1000 600");
  await expect(page.getByLabel("Canvas zoom")).toHaveText("100%");
  await page.keyboard.down("Control");
  await page.mouse.wheel(0, -20);
  await page.keyboard.up("Control");
  await expect(page.getByLabel("Canvas zoom")).not.toHaveText("100%");
  await canvas.focus();
  await page.keyboard.press("0");
  await expect(canvas).toHaveAttribute("viewBox", "0 0 1000 600");
  await page.keyboard.press("ArrowRight");
  await expect(canvas).toHaveAttribute("viewBox", "40 0 1000 600");
  for (let index = 0; index < 20; index++) await page.keyboard.press("+");
  await expect(page.getByLabel("Canvas zoom")).toHaveText("800%");
  await expect(page.getByRole("button", { name: "Zoom in", exact: true })).toBeDisabled();
  for (let index = 0; index < 20; index++) await page.keyboard.press("-");
  await expect(page.getByLabel("Canvas zoom")).toHaveText("50%");
  await expect(page.getByRole("button", { name: "Zoom out", exact: true })).toBeDisabled();
  await expect(canvas.locator(".geometry-corner-handle")).toHaveCount(0);
});

test("two-finger touch pans and pinches without a stray geometry point", async ({ page }) => {
  await setupMapper(page);
  const canvas = page.getByRole("img", { name: "Geometry editor for Front view" });
  await canvas.scrollIntoViewIfNeeded();
  const bounds = (await canvas.boundingBox())!;
  const x = bounds.x + bounds.width / 2;
  const y = bounds.y + bounds.height / 2;
  const session = await page.context().newCDPSession(page);
  await session.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: x - 40, y, id: 1 }, { x: x + 40, y, id: 2 }] });
  await session.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: x - 70, y: y + 20, id: 1 }, { x: x + 90, y: y + 20, id: 2 }] });
  await session.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await expect(page.getByLabel("Canvas zoom")).not.toHaveText("100%");
  await expect(canvas).not.toHaveAttribute("viewBox", "0 0 1000 600");
  await expect(canvas.locator(".geometry-corner-handle")).toHaveCount(0);
  await session.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y, id: 1 }] });
  await session.send("Input.dispatchTouchEvent", { type: "touchCancel", touchPoints: [] });
  await expect(canvas.locator(".geometry-corner-handle")).toHaveCount(0);
  await session.detach();
  await page.getByRole("button", { name: "Fit view", exact: true }).click();
  await canvas.click({ position: { x: bounds.width / 2, y: bounds.height / 2 } });
  await expect(canvas.locator(".geometry-corner-handle")).toHaveCount(1);
});

async function setupMapper(page: Page) {
  const writes = {
    geometry: null as Record<string, unknown> | null,
    surface: null as Record<string, unknown> | null,
    projects: 0, maintenance: 0,
  };
  const segments = [
    geometry("existing", photoId, "semantic-existing", 200, 20),
    geometry("linked", "mapper-side", "semantic-existing", 200, 20),
    geometry("other-output", photoId, "semantic-other", 0, 100),
  ];
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.includes("/projects/") && request.method() !== "GET") writes.projects += 1;
    if (path.endsWith(`/${installationId}/photos`)) {
      return route.fulfill({ json: [photo(photoId), photo("mapper-side")] });
    }
    if (path.endsWith(`/${installationId}/geometry`)) {
      if (request.method() === "POST") {
        writes.geometry = request.postDataJSON();
        return route.fulfill({ json: { ...geometry("saved", photoId, "semantic-saved", 0, 162), ...writes.geometry } });
      }
      return route.fulfill({ json: segments });
    }
    if (path.endsWith("/lighting-surface")) {
      const id = path.split("/").at(-2)!;
      if (request.method() === "PUT") writes.surface = request.postDataJSON();
      return route.fulfill({ json: {
        geometry_id: id, semantic_id: "semantic-existing", resource: id === "other-output" ? "porch.demo" : output,
        local_width: 1, local_height: 1, local_points: [[0.1, 0.3], [0.8, 0.3]],
        stage_matrix: [1, 0, 0, 1, 0, 0], fixture_id: null, group_id: null,
        path_id: null, strand_id: null, calibration_id: null,
        pixel_footprint_width: 0.01, pixel_footprint_height: 0.01,
        brightness_limit: 1, timing_offset_ms: 0, release_offset_ms: 0, pixel_overrides: [],
        pixels: [], revision: 1, ...writes.surface,
      } });
    }
    if (path.endsWith("/maintenance/test-pattern-preview")) {
      writes.maintenance += 1;
      return route.fulfill({ json: {
        resource: output, resource_kind: "pixel_output", pattern: "pixel_index_chase",
        started_at: "2026-10-20T20:00:00Z", expires_at: "2026-10-20T20:00:15Z",
      } });
    }
    if (path.includes("/installation-photos/")) {
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600"><rect width="1000" height="600" fill="#172331"/></svg>',
      });
    }
    if (["/assets/image-library", "/projector-overlaps", "/geometry-suggestions", "/discovery/endpoints"].some((suffix) => path.endsWith(suffix))) {
      return route.fulfill({ json: [] });
    }
    if (path.endsWith("/stage-coverage")) {
      return route.fulfill({ json: {
        installation_id: installationId, minimum_x: 0, minimum_y: 0,
        maximum_x: 1, maximum_y: 1, regions: [],
      } });
    }
    return route.fulfill({ status: 404, json: { detail: "Not used by mapper fixture" } });
  });
  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Installation", exact: true }).click();
  await openLayout(page, "pixel_segment");
  await page.getByLabel("Geometry type").selectOption("pixel_segment");
  return writes;
}

function photo(id: string) {
  return {
    id, installation_id: installationId, name: "Front view", viewpoint: "Primary",
    original_filename: "front.png", media_type: "image/png", digest_sha256: "a".repeat(64),
    size_bytes: 1024, orientation_degrees: 0, crop_x: null, crop_y: null,
    crop_width: null, crop_height: null, scale_hint: null, revision: 1,
    created_at: "2026-10-20T20:00:00Z", updated_at: "2026-10-20T20:00:00Z",
  };
}

function geometry(id: string, view: string, semantic: string, start: number, count: number) {
  return {
    id, installation_id: installationId, photo_id: view, kind: "pixel_segment",
    name: "Existing roofline", semantic_id: semantic, landmark_ids: [],
    points: [{ x: 0.1, y: 0.1 }, { x: 0.9, y: 0.1 }], closed: false,
    color: "#6ee7ff", label_visible: true, locked: false, hidden: false, z_index: 0,
    direction: "forward", start_index: start, pixel_count: count, stage_points: null,
    revision: 1, created_at: "2026-10-20T20:00:00Z", updated_at: "2026-10-20T20:00:00Z",
  };
}
