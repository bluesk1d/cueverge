import { expect, Page, test } from "@playwright/test";
import baseline from "../../../tests/fixtures/usability-authoring.json" with { type: "json" };
import { demoInstallation, demoInstallations, demoProjects, demoRuntime, demoTopology } from "../../src/demo/overview-fixtures";

async function fixture(page: Page) {
  const identity = { state: "loaded", project_id: demoProjects[0].id, revision: 1,
    digest_sha256: "a".repeat(64), loaded_at: new Date().toISOString(), generation: "observed-generation" };
  const runtime = structuredClone(demoRuntime);
  Object.assign(runtime, { loaded_catalog: identity });
  Object.assign(runtime.status.operations, {
    scene_options: [{ id: baseline.scenes[0].id, name: "Loaded LEDs", ready: true }],
    starting_scene_id: baseline.scenes[0].id,
    output_resources: [{ resource: "roofline.demo", pixel_count: 12 }],
  });
  runtime.status.active_scene = null;
  const state = { starts: [] as Record<string, unknown>[], mutations: [] as string[],
    reads: [] as number[], active: 0, maxActive: 0, job: null as Record<string, unknown> | null };
  await page.route("**/api/gui/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (req.method() !== "GET") state.mutations.push(path);
    if (path.endsWith("/auth/session")) return route.fulfill({ json: { username: "admin", role: "administrator" } });
    if (path.endsWith("/installation")) return route.fulfill({ json: demoInstallation });
    if (path.endsWith("/installations")) return route.fulfill({ json: demoInstallations });
    if (path.endsWith("/runtime/status")) return route.fulfill({ json: runtime });
    if (path.endsWith("/topology")) return route.fulfill({ json: demoTopology });
    if (path.endsWith("/projects")) return route.fulfill({ json: [demoProjects[0]] });
    if (path.endsWith("/draft")) return route.fulfill({ json: { project: demoProjects[0], revision: 1, draft: baseline } });
    if (path.endsWith("/apply-jobs")) return route.fulfill({ json: state.job ? [state.job] : [] });
    if (path.endsWith("/apply")) {
      state.starts.push(req.postDataJSON());
      state.job = { id: "apply-one", project_id: demoProjects[0].id, source_revision: 1, state: "completed",
        package_id: "retained-one", previous_package_id: null, error: null, cancel_requested: false,
        interrupted: true, loaded: true, running: false, loaded_catalog: identity,
        scene_options: [{ id: baseline.scenes[0].id, name: baseline.scenes[0].name }] };
      return route.fulfill({ status: 202, json: { ...state.job, state: "preparing" } });
    }
    if (path.endsWith("/apply-jobs/apply-one")) return route.fulfill({ json: state.job });
    if (path.endsWith("/runtime/output-monitor")) {
      state.reads.push(Date.now()); state.active += 1; state.maxActive = Math.max(state.maxActive, state.active);
      await new Promise((resolve) => setTimeout(resolve, 250)); state.active -= 1;
      return route.fulfill({ json: { observed_at: new Date().toISOString(), source: "successful_controller_sink_frames",
        loaded_catalog: identity, sample_interval_ms: 200, maximum_resources: 32, maximum_samples: 4096,
        truncated_resources: 0, hardware_commands_sent: false, outputs: [{
          output_id: "leds", resource: "roofline.demo", pixel_count: 12, pixel_offset: 0, output_position_ms: 100,
          colors: Array.from({ length: 12 }, () => [20, 40, 60]), next_pixel_offset: null, limitations: [],
          source: "scene", scene_instance_id: "observed-scene", age_ms: 15, stale: false, held: true, error: null,
        }] } });
    }
    if (/\/(assets|members|comments|presence|change-requests|photos|geometry|resource-assignments|activity|mcp-tokens|mcp-proposals|revisions|published-packages|simulation-scenarios)$/.test(path)) return route.fulfill({ json: [] });
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });
  return state;
}

test("Apply reviews an exact saved version and surfaces acknowledged completion and recovery", async ({ page }) => {
  const state = await fixture(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByRole("button", { name: /^Open show / }).first().click();
  await page.getByRole("button", { name: "Apply saved changes", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Apply saved changes" });
  await expect(dialog.getByRole("button", { name: "Apply now", exact: true })).toBeDisabled();
  await dialog.getByLabel("I approve this exact saved version", { exact: false }).check();
  await dialog.getByLabel("I authorize the controller load", { exact: false }).check();
  await dialog.getByRole("button", { name: "Apply now", exact: true }).click();
  await expect(page.getByText("Apply completed · playback remains stopped", { exact: true })).toBeVisible();
  expect(state.starts).toHaveLength(1);
  expect(state.starts[0]).toMatchObject({ source_revision: 1, expected_head_revision: 1,
    approve_release: true, confirm_interruption: true, preserve_controller_looks: true });
  await page.getByText("Loaded identity and retained versions", { exact: true }).click();
  await expect(page.getByRole("button", { name: "Restore retained version 1" })).toBeVisible();
});

test("monitor is opt-in, read-only, bounded, non-overlapping and pauses when hidden", async ({ page }) => {
  const state = await fixture(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Operate", exact: true }).click();
  await expect(page.getByRole("button", { name: "Start show", exact: true })).toBeEnabled();
  expect(state.reads).toHaveLength(0);
  await page.getByRole("button", { name: "Open output monitor" }).click();
  await expect(page.getByRole("heading", { name: "Last sent LED colors" })).toHaveCount(0);
  await expect(page.getByText("Last sent LED colors", { exact: true })).toBeVisible();
  const first = await page.getByText("Device-reported media playback", { exact: true }).boundingBox();
  await page.waitForTimeout(1600);
  const last = await page.getByText("Device-reported media playback", { exact: true }).boundingBox();
  expect(last?.y).toBe(first?.y);
  expect(state.maxActive).toBe(1);
  expect(state.reads.length).toBeLessThanOrEqual(4);
  for (let index = 1; index < state.reads.length; index += 1) expect(state.reads[index] - state.reads[index - 1]).toBeGreaterThanOrEqual(490);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.waitForTimeout(400);
  const count = state.reads.length;
  await page.waitForTimeout(900);
  expect(state.reads).toHaveLength(count);
  expect(state.mutations).toEqual([]);
});
