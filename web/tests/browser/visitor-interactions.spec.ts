import { expect, Page, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import baseline from "../../../tests/fixtures/usability-authoring.json" with { type: "json" };
import { InteractionEdit, InteractionPreviewInput, OpportunityDefinition } from "../../src/api";
import { demoInstallation, demoInstallations, demoProjects, demoRuntime, demoTopology } from "../../src/demo/overview-fixtures";

async function routes(page: Page, viewer = false) {
  await page.context().addCookies([{ name: "cueverge_csrf", value: "original-visitor-csrf", url: "http://127.0.0.1:4173" }]);
  const response: OpportunityDefinition = {
    id: "original-welcome", scene_id: "legacy-sweep", trigger_event: "audience.arrived",
    trigger_attributes: {}, required_context: { audience: true }, ttl_ms: 30000,
    event_ttl_ms: 3000, qualification_window_ms: 3600000, min_presence_ms: 0,
    min_absence_before_arrival_ms: 0, acknowledgement_cooldown_ms: 60000, priority: 50,
  };
  const visitors = {
    schema_version: 1, labels: ["person"], cameras: ["porch"], zones: ["walkway"],
    arrival_confirmation_ms: 1000, observation_freshness_ms: 5000, absence_grace_ms: 5000, long_idle_ms: 300000,
  };
  const state = {
    revision: 1, document: { ...structuredClone(baseline), audience: visitors },
    response, name: "Welcome visitors", writes: [] as InteractionEdit[],
    previews: [] as InteractionPreviewInput[], delay: 0,
  };
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/session")) return route.fulfill({ json: { username: viewer ? "viewer" : "admin", role: viewer ? "designer" : "administrator" } });
    if (path.endsWith("/installation")) return route.fulfill({ json: demoInstallation });
    if (path.endsWith("/installations")) return route.fulfill({ json: demoInstallations });
    if (path.endsWith("/runtime/status")) return route.fulfill({ json: demoRuntime });
    if (path.endsWith("/topology")) return route.fulfill({ json: demoTopology });
    if (path.endsWith("/projects")) return route.fulfill({ json: [{ ...demoProjects[0], head_revision: state.revision }] });
    if (path.endsWith("/members")) return route.fulfill({ json: [{ project_id: "demo-project", username: "viewer", role: "viewer", can_live_output: false }] });
    if (path.endsWith("/draft")) return route.fulfill({ json: { project: demoProjects[0], revision: state.revision, draft: state.document } });
    if (["PUT", "POST"].includes(request.method()) && /\/(interactions|interaction-preview)$/.test(path)) {
      if (request.headers()["x-csrf-token"] !== "original-visitor-csrf" || request.headers()["content-type"] !== "application/json") return route.fulfill({ status: 403, json: { detail: { code: "csrf_failed" } } });
      if (viewer) return route.fulfill({ status: 403, json: { detail: { code: "insufficient_project_role" } } });
    }
    if (path.endsWith("/interactions")) {
      if (request.method() === "PUT") {
        const edit = request.postDataJSON() as InteractionEdit;
        state.writes.push(edit); state.revision++;
        if (edit.response) {
          state.name = edit.response.name;
          state.response = { ...state.response, scene_id: edit.response.scene_id, ttl_ms: edit.response.ttl_ms };
        }
        return route.fulfill({ json: { project: demoProjects[0], revision: state.revision, draft: state.document } });
      }
      return route.fulfill({ json: {
        source_revision: state.revision, visitors,
        normal_playback: { starting_scene_id: baseline.program.initial_scene_id, default_pool_id: baseline.program.default_pool_id, pools: baseline.program.pools },
        warnings: [],
        cards: [{
          key: "response:original-welcome", kind: "response", id: "original-welcome",
          name: state.name, when: "Visitors arrive", scene_id: state.response.scene_id,
          scene_name: "Original synthetic legacy sweep", editable: true,
          timing: "Waits for a scene that chooses what plays next to finish.",
          afterward: "Holds at the end; it does not return to normal selection.",
          note: "Detection is not a promise of playback.", definition: state.response,
        }],
      } });
    }
    if (path.endsWith("/interaction-preview")) {
      const input = request.postDataJSON() as InteractionPreviewInput;
      state.previews.push(input);
      if (state.delay) await new Promise((resolve) => setTimeout(resolve, state.delay));
      const selected = input.frame_index ?? input.steps.length;
      const trace = Array.from({ length: input.steps.length + 1 }, (_, index) => ({
        index, at_ms: index * 1000, action: index ? input.steps[index - 1].kind : "start",
        observations: index && input.steps[index - 1].kind === "visitor" ? [{ accepted: true, audience_relevant: true }] : [],
        scene_history: ["baseline-timeline"],
        status: {
          active_scene: "baseline-timeline", estimated_position_ms: index * 1000,
          completion_policy: "loop", playback_state: "playing", degraded_reason: null,
          audience: { state: index ? "candidate" : "absent", active_objects: index ? 1 : 0, relevant_observations: index },
          encounter: null, opportunities: { pending: [], qualifying: [], qualifying_count: 0, created_count: 0, consumed_count: 0, expired_count: 0, qualification_expired_count: 0 },
          director: { active_pool_id: "baseline", eligible_scene_ids: ["baseline-timeline"], eligibility: { "baseline-timeline": { available: true, cooldown_remaining_ms: 0, conditions_match: true } } },
        },
      }));
      return route.fulfill({ json: {
        project_id: "demo-project", source_revision: state.revision, draft_digest: "original-test-digest",
        unsaved: Boolean(input.proposed_edit), trace, selected_index: selected,
        frame_unavailable_reason: null, hardware_adapters_opened: false, hardware_commands_sent: false,
        limitations: ["Isolated fixture; no real adapters or commands."],
        scene_timeline: [{ scene_id: "baseline-timeline", at_ms: 0 }],
        frame: {
          project_id: "demo-project", source_revision: state.revision,
          scene_id: "baseline-timeline", timestamp_ms: selected * 1000, duration_ms: 180000,
          unsaved: Boolean(input.proposed_edit), evaluation: "production_lighting_renderer",
          hardware_commands_sent: false, outputs: [{
            output_id: "leds", resource: "leds", pixel_count: 12, pixel_offset: 0,
            output_position_ms: selected * 1000, colors: Array.from({ length: 12 }, () => [20, 40, 60]),
            next_pixel_offset: null, limitations: [],
          }],
        },
      } });
    }
    if (["activity", "resource-assignments", "assets", "photos", "geometry", "mcp-tokens", "mcp-proposals", "scene-presets", "revisions", "published-packages", "simulation-scenarios", "comments", "presence", "change-requests"].some((suffix) => path.endsWith(`/${suffix}`))) return route.fulfill({ json: [] });
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });
  return state;
}

async function openResponses(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByRole("button", { name: "Open show Home Show", exact: true }).click();
  await page.getByRole("group", { name: "Show workspace", exact: true }).getByRole("button", { name: "Visitor responses", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Welcome visitors", exact: true })).toBeVisible();
}

test("guided edits are focused, keep IDs and save only after explicit review", async ({ page }) => {
  const state = await routes(page);
  await openResponses(page);
  await page.getByRole("button", { name: "Edit response", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Edit visitor response" });
  await dialog.getByRole("button", { name: "Next", exact: true }).click();
  await dialog.getByRole("button", { name: "Next", exact: true }).click();
  await dialog.getByRole("spinbutton", { name: "Wait for an eligible boundary for up to (seconds)", exact: true }).fill("45");
  expect(state.writes).toHaveLength(0);
  await dialog.getByRole("button", { name: "Next", exact: true }).click();
  await dialog.getByRole("button", { name: "Save response", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0].response?.id).toBe("original-welcome");
  expect(state.writes[0].response?.ttl_ms).toBe(45000);
  expect(state.writes[0].response?.prepare_completion).toBe(false);
  expect(state.writes[0].response?.qualification_context).toBe("preserve");
  expect(state.writes[0]).not.toHaveProperty("draft");
});

test("pending response preview never submits the response form", async ({ page }) => {
  const state = await routes(page);
  await openResponses(page);
  await page.getByRole("button", { name: "Add visitor response", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Add visitor response" });
  for (let step = 0; step < 3; step++) await dialog.getByRole("button", { name: "Next", exact: true }).click();
  await dialog.getByRole("button", { name: "Try changes before saving", exact: true }).click();
  await expect.poll(() => state.previews.length).toBeGreaterThan(0);
  await dialog.getByRole("button", { name: "Visitor arrives", exact: true }).click();
  await expect(dialog.getByText("Confirming visitor arrival", { exact: false })).toBeVisible();
  expect(state.previews.at(-1)?.proposed_edit?.operation).toBe("response");
  expect(state.writes).toHaveLength(0);
  page.once("dialog", (event) => event.accept());
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(state.writes).toHaveLength(0);
});

test("visitor test shares house preview, records actions and supports checkpoint inspection", async ({ page }) => {
  const state = await routes(page);
  await openResponses(page);
  await page.getByRole("button", { name: "Try it", exact: true }).click();
  const preview = page.getByRole("region", { name: "Preview workspace", exact: true });
  await preview.getByRole("button", { name: "Visitor test", exact: true }).click();
  await expect.poll(() => state.previews.length).toBeGreaterThan(0);
  await preview.getByRole("button", { name: "Visitor arrives", exact: true }).click();
  await expect(preview.getByText("Confirming visitor arrival", { exact: false })).toBeVisible();
  await preview.getByRole("button", { name: "Advance preview time", exact: true }).click();
  await expect.poll(() => state.previews.at(-1)?.steps.length).toBe(2);
  await preview.getByRole("combobox", { name: "Inspect a checkpoint", exact: true }).selectOption("0");
  await expect.poll(() => state.previews.at(-1)?.frame_index).toBe(0);
  expect(state.writes).toHaveLength(0);
});

test("pending response changes are guarded and shared camera CSV fields remain usable", async ({ page }) => {
  await routes(page);
  await openResponses(page);
  await page.getByRole("button", { name: "Edit visitor area", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Visitor area", exact: true });
  await dialog.getByRole("textbox", { name: "Camera names", exact: true }).fill("porch, side");
  await expect(dialog.getByRole("textbox", { name: "Camera names", exact: true })).toHaveValue("porch, side");
  page.once("dialog", (event) => event.dismiss());
  await dialog.press("Escape");
  await expect(dialog).toBeVisible();
  page.once("dialog", (event) => event.accept());
  await dialog.press("Escape");
  await expect(dialog).toHaveCount(0);
});

test("project viewers can inspect responses but cannot edit or run visitor tests", async ({ page }) => {
  const state = await routes(page, true);
  await openResponses(page);
  await expect(page.getByRole("button", { name: "Edit response", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Add visitor response", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Try it", exact: true })).toBeDisabled();
  expect(state.writes).toHaveLength(0);
  expect(state.previews).toHaveLength(0);
});

test("guided response dialog fits a phone and retains accessible controls", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await routes(page);
  await openResponses(page);
  await page.getByRole("button", { name: "Edit response", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Edit visitor response" });
  expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  const result = await new AxeBuilder({ page }).include(".interaction-dialog").analyze();
  expect(result.violations.filter((value) => ["serious", "critical"].includes(value.impact ?? ""))).toEqual([]);
});
