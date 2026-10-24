import { expect, test, type Page } from "@playwright/test";
import { demoInstallation, demoInstallations, demoProjects, demoRuntime, demoTopology } from "../../src/demo/overview-fixtures";
import { openTimelineEditor } from "./show-workspace";

type Draft = ReturnType<typeof draft>;

async function routes(page: Page, role: "administrator" | "viewer" = "administrator") {
  const state = {
    document: draft(),
    revision: 1,
    calls: [] as { method: string; path: string; ifMatch?: string; body?: unknown }[],
    removeError: null as string | null,
  };
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const record = () => ({
      project: {
        id: "demo-project", name: "Home Show", head_revision: state.revision, metadata_revision: 1,
        archived_at: null, created_at: "2026-10-15T12:00:00Z", updated_at: "2026-10-15T12:00:00Z",
      },
      revision: state.revision,
      draft: state.document,
    });
    if (path.endsWith("/auth/session") && role === "viewer") {
      return route.fulfill({ json: { username: "viewer", role: "viewer" } });
    }
    if (path.endsWith("/members")) return route.fulfill({ json: [] });
    if (path.endsWith("/projects/demo-project/assets")) return route.fulfill({ json: [] });
    if (path.endsWith("/projects/demo-project/draft")) return route.fulfill({ json: record() });
    const output = path.match(/\/scenes\/ambient\/outputs\/([^/]+)(\/rename)?$/);
    if (output) {
      const id = decodeURIComponent(output[1]);
      state.calls.push({
        method: request.method(), path, ifMatch: request.headers()["if-match"],
        body: request.method() === "POST" ? request.postDataJSON() : undefined,
      });
      const scene = state.document.scenes[0];
      if (output[2]) {
        const next = request.postDataJSON().new_output_id as string;
        for (const item of [...scene.media, ...scene.lighting]) if (item.id === id) item.id = next;
        if (scene.clock_source === id) scene.clock_source = next;
      } else {
        if (state.removeError) {
          return route.fulfill({
            status: 422,
            json: { detail: { code: "scene_output_invalid", message: state.removeError } },
          });
        }
        scene.lighting = scene.lighting.filter((item) => item.id !== id);
        scene.resources = scene.resources.filter((item) => item !== "wled-001");
      }
      state.revision += 1;
      return route.fulfill({ json: record() });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });
  return state;
}

test("timeline Inspector renames and removes saved scene outputs", async ({ page }) => {
  const state = await routes(page);
  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openTimelineEditor(page);
  const inspector = page.getByRole("complementary", { name: "Inspector" });

  await page.getByRole("button", { name: "Media output · left-window" }).click();
  const actions = inspector.getByRole("group", { name: "Manage media output left-window" });
  const rename = actions.getByRole("button", { name: "Rename output" });
  await expect(rename).toBeDisabled();
  await actions.getByLabel("Output ID for left-window").fill("bad id");
  await expect(actions.getByRole("alert")).toContainText("Start with a letter or number");
  await expect(rename).toBeDisabled();
  await actions.getByLabel("Output ID for left-window").fill("main-window");
  await rename.click();
  await expect(page.getByRole("button", { name: "Media output · main-window" })).toBeVisible();
  expect(state.calls[0]).toMatchObject({
    method: "POST",
    path: "/api/gui/v1/projects/demo-project/scenes/ambient/outputs/left-window/rename",
    ifMatch: '"1"',
    body: { new_output_id: "main-window" },
  });

  await page.getByRole("button", { name: "Lighting output · roofline" }).click();
  const lighting = inspector.getByRole("group", { name: "Manage lighting output roofline" });
  state.removeError = "Output roofline cannot be removed while used by: synchronized cue Flash. Update these first.";
  page.once("dialog", (dialog) => dialog.accept());
  await lighting.getByRole("button", { name: "Remove output" }).click();
  await expect(lighting.getByRole("alert")).toContainText("synchronized cue Flash");
  await expect(lighting.getByRole("alert")).toContainText("Nothing was changed.");
  expect(state.revision).toBe(2);

  page.once("dialog", (dialog) => dialog.dismiss());
  state.removeError = null;
  await lighting.getByRole("button", { name: "Remove output" }).click();
  expect(state.calls).toHaveLength(2);

  page.once("dialog", (dialog) => dialog.accept());
  await lighting.getByRole("button", { name: "Remove output" }).click();
  await expect(page.getByRole("button", { name: "Lighting output · roofline" })).toHaveCount(0);
  expect(state.calls[2]).toMatchObject({
    method: "DELETE",
    path: "/api/gui/v1/projects/demo-project/scenes/ambient/outputs/roofline",
    ifMatch: '"2"',
  });

  await page.getByRole("button", { name: "Add marker" }).click();
  await page.getByRole("button", { name: "Media output · main-window" }).click();
  const blocked = inspector.getByRole("group", { name: "Manage media output main-window" });
  await expect(blocked).toContainText("Save timeline or undo your edits");
  await expect(blocked.getByRole("button", { name: "Remove output" })).toBeDisabled();
  await page.getByRole("button", { name: "Undo timeline edit" }).click();
  await expect(blocked.getByRole("button", { name: "Remove output" })).toBeEnabled();
});

test("Scenes page renames media outputs through the same operation", async ({ page }) => {
  const state = await routes(page);
  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByRole("button", { name: /^Open show / }).first().click();
  const scenesTab = page.getByRole("button", { name: "Scenes", exact: true });
  if ((await scenesTab.getAttribute("aria-pressed")) !== "true") await scenesTab.click();
  await page.getByText(/^Media in this scene/).click();
  const actions = page.getByRole("group", { name: "Manage media output left-window" });
  await actions.getByLabel("Output ID for left-window").fill("porch");
  await actions.getByRole("button", { name: "Rename output" }).click();
  await expect(page.getByRole("group", { name: "Manage media output porch" })).toBeVisible();
  expect(state.calls[0].path).toContain("/outputs/left-window/rename");
  await expect(page.getByRole("group", { name: "Manage lighting output roofline" })).toBeVisible();
});

test("viewers can inspect outputs without rename or remove controls", async ({ page }) => {
  await routes(page, "viewer");
  await page.route(/\/api\/gui\/v1\/(installation|installations|runtime\/status|topology|activity|projects)$/, (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/installation")) return route.fulfill({ json: demoInstallation });
    if (path.endsWith("/installations")) return route.fulfill({ json: demoInstallations });
    if (path.endsWith("/runtime/status")) return route.fulfill({ json: demoRuntime });
    if (path.endsWith("/topology")) return route.fulfill({ json: demoTopology });
    if (path.endsWith("/projects")) return route.fulfill({ json: demoProjects });
    return route.fulfill({ json: [] });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openTimelineEditor(page);
  await page.getByRole("button", { name: "Media output · left-window" }).click();
  const inspector = page.getByRole("complementary", { name: "Inspector" });
  await expect(inspector).toContainText("Stable ID: media:left-window");
  await expect(inspector.getByRole("button", { name: "Rename output" })).toBeDisabled();
  await expect(inspector.getByRole("button", { name: "Remove output" })).toBeDisabled();
  await expect(inspector).toContainText("You can inspect this output but not change it.");
});

function draft() {
  return {
    schema_version: 1,
    installation: { schema_version: 1, resources: [] },
    editor_metadata: {} as Record<string, unknown>,
    scenes: [
      {
        schema_version: 2 as const,
        id: "ambient",
        name: "Ambient",
        mode: "media_synchronized",
        resources: ["projector.left", "wled-001"],
        duration_ms: 5000,
        clock_source: "left-window",
        media: [
          { id: "left-window", resource: "projector.left", asset: null, required: true, start_delay_ms: 0 },
        ],
        lighting: [
          { id: "roofline", resource: "wled-001", required: true, effect: "timeline", timeline: [] as unknown[] },
        ],
        scheduling: { safe_points_ms: [] as number[], protected_intervals: [] as unknown[] },
      },
    ],
  };
}

export type { Draft };
