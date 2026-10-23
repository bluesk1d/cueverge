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

test("project permissions still protect editing when collaboration is closed", async ({ page }) => {
  const state = await sceneRoutes(page, true);
  await page.goto("/");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByRole("button", { name: "Open show Home Show" }).click();
  await expect(page.getByRole("combobox", { name: "Effect", exact: true })).toBeDisabled();
  await expect(page.getByRole("spinbutton", { name: "Effect brightness (%)", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Save scene" })).toHaveCount(0);
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
