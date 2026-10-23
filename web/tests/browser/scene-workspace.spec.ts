import { expect, Page, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import baseline from "../../../tests/fixtures/usability-authoring.json" with { type: "json" };
import { demoInstallation, demoInstallations, demoRuntime, demoTopology, demoProjects } from "../../src/demo/overview-fixtures";

async function sceneRoutes(page: Page, readOnly = false, cueCount = 10) {
  const document = structuredClone(baseline);
  const output = document.scenes[0].lighting;
  if (!Array.isArray(output) || !output[0].timeline) throw new Error("Invalid original fixture");
  const examples = structuredClone(output[0].timeline);
  output[0].timeline = Array.from({ length: cueCount }, (_, index) => ({
    ...examples[index === 0 ? 0 : 1 + (index - 1) % (examples.length - 1)], id: `sample-${index + 1}`,
  }));
  const state = {
    document, revision: 1, saves: [] as typeof baseline[],
    previewBodies: [] as Record<string, unknown>[], collaborationReads: 0,
    previewDelay: 0, concurrent: 0, maxConcurrent: 0,
    previewPixelCount: 12,
    projectsVisible: true, project: { ...demoProjects[0] }, creations: [] as string[],
    deletions: [] as string[], deletionError: null as string | null, deletionStatus: 422,
  };
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/session")) return route.fulfill({ json: {
      username: readOnly ? "designer-viewer" : "admin",
      role: readOnly ? "designer" : "administrator",
    } });
    if (path.endsWith("/installation")) return route.fulfill({ json: demoInstallation });
    if (path.endsWith("/installations")) return route.fulfill({ json: demoInstallations });
    if (path.endsWith("/runtime/status")) return route.fulfill({ json: demoRuntime });
    if (path.endsWith("/topology")) return route.fulfill({ json: demoTopology });
    if (path.endsWith("/activity") || path.endsWith("/resource-assignments")) return route.fulfill({ json: [] });
    if (path.endsWith("/projects")) return route.fulfill({ json: state.projectsVisible ? [{ ...state.project, head_revision: state.revision }] : [] });
    if (path.endsWith("/projects/from-active")) {
      state.creations.push(request.postDataJSON().name);
      state.project.name = request.postDataJSON().name;
      state.projectsVisible = true;
      return route.fulfill({ status: 201, json: {
        project: { ...state.project, head_revision: state.revision },
        revision: state.revision, draft: state.document,
      } });
    }
    if (request.method() === "DELETE" && /\/scenes\/[^/]+$/.test(path)) {
      if (state.deletionError) return route.fulfill({ status: state.deletionStatus, json: {
        detail: { code: "scene_deletion_invalid", message: state.deletionError },
      } });
      expect(request.headers()["if-match"]).toBe(`"${state.revision}"`);
      const id = decodeURIComponent(path.split("/").at(-1)!);
      state.deletions.push(id);
      state.document.scenes = state.document.scenes.filter((scene) => scene.id !== id);
      state.revision += 1;
      return route.fulfill({ json: {
        project: { ...state.project, head_revision: state.revision },
        revision: state.revision, draft: state.document,
      } });
    }
    if (path.endsWith("/draft")) {
      if (request.method() === "PUT") {
        const value = request.postDataJSON();
        state.saves.push(value);
        state.document = value;
        state.revision += 1;
      }
      return route.fulfill({ json: {
        project: { ...state.project, head_revision: state.revision },
        revision: state.revision, draft: state.document,
      } });
    }
    if (path.endsWith("/scene-lighting-preview")) {
      const body = request.postDataJSON();
      state.previewBodies.push(body);
      state.concurrent += 1;
      state.maxConcurrent = Math.max(state.maxConcurrent, state.concurrent);
      if (state.previewDelay) await new Promise((resolve) => setTimeout(resolve, state.previewDelay));
      state.concurrent -= 1;
      return route.fulfill({ json: {
        project_id: "demo-project", source_revision: body.source_revision, scene_id: body.scene_id,
        timestamp_ms: body.timestamp_ms, duration_ms: 180000, unsaved: Boolean(body.proposed_draft),
        evaluation: "production_lighting_renderer", hardware_commands_sent: false,
        outputs: [{
          output_id: "leds", resource: body.resource, pixel_count: state.previewPixelCount, pixel_offset: 0,
          output_position_ms: body.timestamp_ms, colors: Array.from({ length: state.previewPixelCount }, () => [20, 40, 60]),
          next_pixel_offset: null,
          limitations: [],
        }],
      } });
    }
    if (path.endsWith("/members")) {
      return route.fulfill({ json: [{
        project_id: "demo-project", username: "designer-viewer", role: "viewer", can_live_output: false,
      }] });
    }
    if (/\/(comments|presence|change-requests)$/.test(path)) {
      state.collaborationReads += 1;
      return route.fulfill({ json: [] });
    }
    if (/\/(assets|photos|geometry|mcp-tokens|mcp-proposals|revisions|published-packages|simulation-scenarios|apply-jobs)$/.test(path)) {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });
  return state;
}

test("library opens a scene directly and all 56 raw effects survive an isolated edit", async ({ page }) => {
  const state = await sceneRoutes(page, false, 56);
  const original = structuredClone(state.document);
  await page.goto("/");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Your shows" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Collaboration" })).toHaveCount(0);
  await expect(page.getByLabel("Revision name", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Open show Home Show" }).click();
  await expect(page.getByRole("heading", { name: "Original synthetic timeline" })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Edit effect sample-/ })).toHaveCount(56);
  await expect(page.getByText("56 effects · whole scene fitted below")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save scene" })).toBeDisabled();
  await expect(page.getByRole("region", { name: "Collaboration" })).toHaveCount(0);
  expect(state.collaborationReads).toBe(0);
  await page.getByRole("spinbutton", { name: "Effect brightness (%)", exact: true }).fill("40");
  const preview = page.getByRole("region", { name: "Scene lighting preview", exact: true });
  await expect(preview.getByText(/unsaved edits/)).toBeVisible();
  expect(state.saves).toHaveLength(0);
  await page.getByRole("button", { name: "Save scene", exact: true }).click();
  await expect.poll(() => state.saves.length).toBe(1);
  const expected = structuredClone(original);
  const expectedOutput = expected.scenes[0].lighting;
  if (!Array.isArray(expectedOutput) || !expectedOutput[0].timeline) throw new Error("Invalid fixture");
  expectedOutput[0].timeline[0].brightness = 0.4;
  expect(state.saves[0]).toEqual(expected);
  await expect(page.getByText("Saved · not a live update", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /^Original synthetic legacy sweep/ }).click();
  await expect(page.getByLabel("Legacy color")).toBeVisible();
  await page.getByRole("button", { name: /^Original synthetic timeline/ }).click();
  await expect(page.getByRole("spinbutton", { name: "Effect brightness (%)" })).toHaveValue("40");
  expect(state.saves).toHaveLength(1);
  expect(state.collaborationReads).toBe(0);
  const accessibility = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(accessibility.violations.filter(({ impact }) => impact === "critical" || impact === "serious")).toEqual([]);
});

test("invalid raw cue timing is explained and never saved", async ({ page }) => {
  const state = await sceneRoutes(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByRole("button", { name: "Open show Home Show" }).click();
  await page.getByRole("spinbutton", { name: "Start (seconds)", exact: true }).fill("180");
  await expect(page.getByText(/sample-1: start and duration must fit inside the scene/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Save scene" })).toBeDisabled();
  expect(state.saves).toHaveLength(0);
  await page.getByRole("button", { name: "Cancel scene edits" }).click();
  await expect(page.getByRole("spinbutton", { name: "Start (seconds)", exact: true })).toHaveValue("0");
  await expect(page.getByRole("button", { name: "Save scene" })).toBeDisabled();
});

test("scene deletion requires confirmation, persists, and protects the last scene", async ({ page }) => {
  const state = await sceneRoutes(page);
  state.document.scenes = state.document.scenes.slice(0, 2);
  await page.goto("/");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByRole("button", { name: "Open show Home Show" }).click();
  const remove = page.getByRole("button", { name: "Delete scene", exact: true });
  page.once("dialog", (dialog) => dialog.dismiss());
  await remove.click();
  expect(state.deletions).toEqual([]);
  await page.getByRole("spinbutton", { name: "Effect brightness (%)", exact: true }).fill("40");
  await expect(remove).toBeDisabled();
  await expect(page.getByText("Save or cancel edits before deleting a scene.")).toBeVisible();
  await page.getByRole("button", { name: "Cancel scene edits", exact: true }).click();
  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toContain('Delete scene "Original synthetic timeline"');
    await dialog.accept();
  });
  await remove.click();
  await expect(page.getByRole("heading", { name: "Original synthetic legacy sweep", exact: true })).toBeVisible();
  expect(state.deletions).toEqual(["baseline-timeline"]);
  expect(state.saves).toHaveLength(0);
  await expect(remove).toBeDisabled();
  await expect(page.getByText("Keep at least one scene in the show.")).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByRole("button", { name: "Open show Home Show" }).click();
  await expect(page.getByRole("heading", { name: "Original synthetic legacy sweep", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Original synthetic timeline/ })).toHaveCount(0);
});

for (const status of [422, 409]) {
test(`failed scene deletion explains ${status} and preserves the selected scene`, async ({ page }) => {
  const state = await sceneRoutes(page);
  state.deletionStatus = status;
  state.deletionError = "Scene is still used by: automatic program starting scene. Update these references first.";
  await page.goto("/");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByRole("button", { name: "Open show Home Show" }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Delete scene", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: status === 409
    ? "Reload the saved show before deleting a scene." : state.deletionError })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Original synthetic timeline", exact: true })).toBeVisible();
  expect(state.deletions).toEqual([]);
  expect(state.document.scenes).toHaveLength(3);
});
}

test("project permissions still protect editing when collaboration is closed", async ({ page }) => {
  const state = await sceneRoutes(page, true);
  await page.goto("/");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByRole("button", { name: "Open show Home Show" }).click();
  await expect(page.getByRole("combobox", { name: "Effect", exact: true })).toBeDisabled();
  await expect(page.getByRole("spinbutton", { name: "Effect brightness (%)", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Save scene" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Delete scene", exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Collaboration" })).toHaveCount(0);
  expect(state.saves).toHaveLength(0);
  expect(state.collaborationReads).toBe(0);
});

test("scene selection is shared with the advanced timeline without silent edit loss", async ({ page }) => {
  const state = await sceneRoutes(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByRole("button", { name: "Open show Home Show" }).click();
  await page.getByRole("button", { name: /^Original synthetic legacy sweep/ }).click();
  await page.getByText("Advanced authoring and history", { exact: true }).click();
  await page.getByText("Timeline editor", { exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Scene", exact: true })).toHaveValue("legacy-sweep");
  await page.getByRole("combobox", { name: "Scene", exact: true }).selectOption("baseline-timeline");
  await expect(page.getByRole("spinbutton", { name: "Effect brightness (%)", exact: true })).toBeVisible();
  await page.getByRole("spinbutton", { name: "Effect brightness (%)", exact: true }).fill("45");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "Back to shows" }).click();
  await expect(page.getByRole("spinbutton", { name: "Effect brightness (%)", exact: true })).toHaveValue("45");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "Operate", exact: true }).click();
  await expect(page.getByRole("spinbutton", { name: "Effect brightness (%)", exact: true })).toHaveValue("45");
  expect(state.saves).toHaveLength(0);
});

test("slow preview frames are serialized and still reach the screen while playing", async ({ page }) => {
  const state = await sceneRoutes(page);
  state.previewDelay = 450;
  await page.goto("/");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByRole("button", { name: "Open show Home Show" }).click();
  const preview = page.getByRole("region", { name: "Scene lighting preview" });
  await expect(preview.getByText(/Evaluated 0:00/)).toBeVisible();
  await page.getByRole("button", { name: "Play scene preview", exact: true }).click();
  const layout = await preview.evaluate(async (element) => {
    const samples: number[] = [];
    const observer = new MutationObserver(() => {
      const summary = element.querySelector("summary");
      if (summary) samples.push(summary.getBoundingClientRect().top - element.getBoundingClientRect().top);
    });
    observer.observe(element, { subtree: true, childList: true, characterData: true, attributes: true });
    await new Promise((resolve) => setTimeout(resolve, 1800));
    observer.disconnect();
    return samples;
  });
  expect(layout.length).toBeGreaterThan(3);
  expect(new Set(layout).size).toBe(1);
  await expect(preview.getByText("Evaluating preview…", { exact: true })).toHaveCount(0);
  await expect(preview.getByText(/Evaluated 0:0[1-9]\./)).toBeVisible();
  await page.getByRole("button", { name: "Pause scene preview", exact: true }).click();
  expect(state.maxConcurrent).toBe(1);
  expect(state.saves).toHaveLength(0);
});

test("three-minute looping preview keeps whole-millisecond timestamps across completion", async ({ page }) => {
  const state = await sceneRoutes(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByRole("button", { name: "Open show Home Show" }).click();
  const preview = page.getByRole("region", { name: "Scene lighting preview" });
  await expect(preview.getByText(/Evaluated 0:00/)).toBeVisible();
  await page.getByLabel("Scene preview time (seconds)", { exact: true }).fill("179.999");
  await expect.poll(() => state.previewBodies.some((body) => body.timestamp_ms === 179999)).toBe(true);
  await page.evaluate(() => {
    const now = performance.now.bind(performance);
    performance.now = () => now() * 1.0015;
  });
  await page.getByRole("button", { name: "Play scene preview", exact: true }).click();
  await expect.poll(() => state.previewBodies.some((body) =>
    typeof body.timestamp_ms === "number" && body.timestamp_ms > 0 && body.timestamp_ms < 1000,
  )).toBe(true);
  for (const body of state.previewBodies) {
    expect(Number.isSafeInteger(body.timestamp_ms)).toBe(true);
    expect(body.timestamp_ms).toBeGreaterThanOrEqual(0);
    expect(body.timestamp_ms).toBeLessThan(180000);
  }
  await expect(page.getByRole("button", { name: "Pause scene preview", exact: true })).toBeVisible();
  await expect(preview.getByText("Scene preview unavailable", { exact: true })).toHaveCount(0);
  expect(state.saves).toHaveLength(0);
});

for (const width of [1280, 390]) {
  test(`LED diagnostics stay optional and preserve pixel order at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await sceneRoutes(page);
    state.previewPixelCount = 600;
    await page.goto("/");
    await page.getByRole("button", { name: "Shows", exact: true }).click();
    await page.getByRole("button", { name: "Open show Home Show" }).click();
    const preview = page.getByRole("region", { name: "Scene lighting preview" });
    await expect(preview.getByText(/Evaluated 0:00/)).toBeVisible();
    const strip = preview.getByRole("region", { name: "LED colors in pixel address order" });
    await expect(strip).toBeHidden();
    await preview.getByText("LED output details", { exact: true }).click();
    await expect(strip).toBeVisible();
    const layout = await strip.evaluate((element) => {
      const pixels = Array.from(element.querySelectorAll("span"));
      return {
        rows: new Set(pixels.map((pixel) => pixel.getBoundingClientRect().top)).size,
        count: pixels.length,
        scrolls: element.scrollWidth > element.clientWidth,
        pageFits: document.documentElement.scrollWidth <= window.innerWidth,
      };
    });
    expect(layout).toEqual({ rows: 1, count: 600, scrolls: true, pageFits: true });
    await strip.focus();
    await strip.press("ArrowRight");
    await expect.poll(() => strip.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
  });
}

test("New show is a focused flow and opens the created scene workspace", async ({ page }) => {
  const state = await sceneRoutes(page);
  state.projectsVisible = false;
  await page.goto("/");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Show name", exact: true })).toHaveCount(0);
  await page.getByText("New show", { exact: true }).click();
  await page.getByRole("textbox", { name: "Show name", exact: true }).fill("Autumn display");
  await page.getByRole("button", { name: "Create show", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Autumn display", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Original synthetic timeline", exact: true })).toBeVisible();
  expect(state.creations).toEqual(["Autumn display"]);
  expect(state.saves).toHaveLength(0);
});

test("small valid percentages survive unrelated edits and zero output limit is rejected", async ({ page }) => {
  const state = await sceneRoutes(page);
  const output = state.document.scenes[0].lighting;
  if (!Array.isArray(output) || !output[0].timeline) throw new Error("Invalid fixture");
  output[0].brightness_limit = 1e-10;
  output[0].timeline[6].density = 1e-10;
  output[0].timeline[6].brightness = 0.14;
  await page.goto("/");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByRole("button", { name: "Open show Home Show" }).click();
  await page.getByRole("button", { name: "Edit effect sample-7", exact: true }).click();
  await expect(page.getByRole("spinbutton", { name: "Effect brightness (%)", exact: true })).toHaveValue("14");
  await page.getByRole("spinbutton", { name: "Effect brightness (%)", exact: true }).fill("50");
  await page.getByRole("button", { name: "Save scene", exact: true }).click();
  await expect.poll(() => state.saves.length).toBe(1);
  const savedOutput = state.saves[0].scenes[0].lighting;
  if (!Array.isArray(savedOutput) || !savedOutput[0].timeline) throw new Error("Invalid saved fixture");
  expect(savedOutput[0].brightness_limit).toBe(1e-10);
  expect(savedOutput[0].timeline[6].density).toBe(1e-10);
  await page.getByText("Output details", { exact: true }).click();
  await page.getByRole("spinbutton", { name: "Output brightness limit (%)", exact: true }).fill("0");
  await expect(page.getByText("Output brightness limit must be greater than 0 and at most 100%.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save scene", exact: true })).toBeDisabled();
  expect(state.saves).toHaveLength(1);
});

async function mappedPhotoRoutes(page: Page, width: number, height: number, colors = [[255, 255, 255], [255, 255, 255], [255, 255, 255]]) {
  const state = await sceneRoutes(page);
  const fit = Math.min(1000 / width, 600 / height);
  const point = {
    x: ((1000 - width * fit) / 2 + width * 0.125 * fit) / 1000,
    y: ((600 - height * fit) / 2 + height * 0.1 * fit) / 600,
  };
  const lighting = state.document.scenes[0].lighting;
  if (!Array.isArray(lighting)) throw new Error("Expected multiple lighting outputs");
  const resource = lighting[0].resource;
  await page.route("**/api/gui/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/photos")) return route.fulfill({ json: [{
      id: "alignment-photo", name: "Original night reference", width, height,
      installation_id: "demo-installation", revision: 1,
    }] });
    if (path.endsWith("/geometry")) return route.fulfill({ json: [{
      id: "aligned-segment", semantic_id: "aligned-segment", photo_id: "alignment-photo",
      kind: "pixel_segment", name: "Mapped LEDs", points: [point, { x: point.x + 0.1, y: point.y + 0.1 }],
      start_index: 0, pixel_count: 3, direction: "forward", hidden: false,
    }] });
    if (path.endsWith("/lighting-surface")) return route.fulfill({ json: { resource } });
    if (path.endsWith("/alignment-photo/content")) return route.fulfill({
      contentType: "image/svg+xml",
      body: `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="#10141c"/><circle cx="${width * 0.125}" cy="${height * 0.1}" r="2" fill="white"/></svg>`,
    });
    if (path.endsWith("/scene-lighting-preview")) {
      const body = route.request().postDataJSON();
      return route.fulfill({ json: {
        project_id: "demo-project", source_revision: 1, scene_id: body.scene_id,
        timestamp_ms: body.timestamp_ms, duration_ms: 180000, unsaved: false,
        evaluation: "production_lighting_renderer", hardware_commands_sent: false,
        outputs: [{ output_id: "leds", resource, pixel_count: 3, pixel_offset: 0,
          output_position_ms: body.timestamp_ms, colors, next_pixel_offset: null, limitations: [] }],
      } });
    }
    return route.fallback();
  });
  return state;
}

for (const viewport of [1280, 390]) {
  for (const [width, height] of [[800, 600], [1600, 600], [600, 1000]]) {
    test(`photo overlay matches Installation coordinates for ${width}x${height} at ${viewport}px`, async ({ page }) => {
      await page.setViewportSize({ width: viewport, height: 900 });
      await mappedPhotoRoutes(page, width, height);
      await page.goto("/");
      await page.getByRole("button", { name: "Shows", exact: true }).click();
      await page.getByRole("button", { name: "Open show Home Show" }).click();
      const photo = page.locator(".scene-photo");
      await expect(photo.locator("circle").first()).toBeVisible();
      const difference = await photo.evaluate((element, dimensions) => {
        const [w, h] = dimensions;
        const image = element.querySelector("img, image")!;
        const box = image.getBoundingClientRect();
        const isSvg = image.tagName.toLowerCase() === "image";
        const scale = Math.min(box.width / w, box.height / h);
        const expectedX = box.x + (isSvg ? (box.width - w * scale) / 2 + w * scale * 0.125 : box.width * 0.125);
        const expectedY = box.y + (isSvg ? (box.height - h * scale) / 2 + h * scale * 0.1 : box.height * 0.1);
        const led = element.querySelector("circle")!;
        const position = new DOMPoint(led.cx.baseVal.value, led.cy.baseVal.value).matrixTransform(led.getScreenCTM()!);
        return { x: Math.abs(position.x - expectedX), y: Math.abs(position.y - expectedY) };
      }, [width, height]);
      expect(difference.x).toBeLessThan(0.5);
      expect(difference.y).toBeLessThan(0.5);
    });
  }
}

test("night visualization keeps off LEDs transparent and makes low output visible without altering RGB", async ({ page }) => {
  const state = await mappedPhotoRoutes(page, 800, 600, [[0, 0, 0], [3, 0, 0], [26, 0, 0]]);
  await page.goto("/");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByRole("button", { name: "Open show Home Show" }).click();
  const photo = page.locator(".scene-photo");
  await expect(photo.locator("circle")).toHaveCount(2);
  const style = await photo.locator("circle").first().evaluate((element) => ({
    fill: getComputedStyle(element).fill,
    blend: getComputedStyle(element.parentElement!).mixBlendMode,
    glow: getComputedStyle(element).filter,
  }));
  expect(style.fill).toBe("rgb(28, 0, 0)");
  expect(style.blend).toBe("screen");
  expect(style.glow).toContain("drop-shadow");
  const preview = page.getByRole("region", { name: "Scene lighting preview", exact: true });
  await preview.getByText("LED output details", { exact: true }).click();
  const raw = preview.getByRole("region", { name: "LED colors in pixel address order" }).locator("span");
  await expect(raw.nth(0)).toHaveCSS("background-color", "rgb(0, 0, 0)");
  await expect(raw.nth(1)).toHaveCSS("background-color", "rgb(3, 0, 0)");
  await expect(raw.nth(2)).toHaveCSS("background-color", "rgb(26, 0, 0)");
  expect(state.saves).toHaveLength(0);
});

test("a failed preview photo keeps raw frames available and can be retried", async ({ page }) => {
  const state = await mappedPhotoRoutes(page, 800, 600);
  let failed = true;
  await page.route("**/installation-photos/alignment-photo/content", (route) => failed
    ? route.fulfill({ status: 503, body: "Original image temporarily unavailable" })
    : route.fallback());
  await page.goto("/");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByRole("button", { name: "Open show Home Show" }).click();
  const preview = page.getByRole("region", { name: "Scene lighting preview", exact: true });
  await expect(preview.getByText("Photo unavailable", { exact: true })).toBeVisible();
  await expect(preview.getByText(/Evaluated 0:00/)).toBeVisible();
  await preview.getByText("LED output details", { exact: true }).click();
  await expect(preview.getByRole("region", { name: "LED colors in pixel address order" }).locator("span")).toHaveCount(3);
  failed = false;
  await preview.getByRole("button", { name: "Retry scene preview" }).click();
  await expect(page.locator(".scene-photo circle")).toHaveCount(3);
  await expect(preview.getByText("Photo unavailable", { exact: true })).toHaveCount(0);
  expect(state.saves).toHaveLength(0);
});
