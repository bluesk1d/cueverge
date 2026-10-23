import { expect, Page, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import baseline from "../../../tests/fixtures/usability-authoring.json" with { type: "json" };
import type { ShowPlan, ShowPlanEdit } from "../../src/api";
import { demoInstallation, demoInstallations, demoProjects, demoRuntime, demoTopology } from "../../src/demo/overview-fixtures";

type Completion = { policy: string; target_scene_id?: string | null };
const endings: Record<string, ShowPlan["ending_choices"][number]["kind"]> = {
  loop: "repeat", transition_to: "play_next", select_next: "shuffle", hold: "freeze", stop: "stop",
};

function planFor(document: typeof baseline, revision: number): ShowPlan {
  const scenes = document.scenes as { id: string; name: string; duration_ms: number; completion?: Completion }[];
  const summary = (id: string) => {
    const scene = scenes.find((item) => item.id === id)!;
    const completion = scene.completion ?? { policy: "hold" };
    const target = scenes.find((item) => item.id === completion.target_scene_id);
    return {
      id, name: scene.name, duration_ms: scene.duration_ms,
      ending: { kind: endings[completion.policy], next_scene_id: completion.target_scene_id ?? null, label: target ? `Then plays ${target.name}` : "Repeats" },
    };
  };
  const main: string[] = [];
  let current = document.program!.initial_scene_id;
  let end: ShowPlan["main_show"]["end"] = { kind: "none", scene_id: null };
  for (;;) {
    main.push(current);
    const completion = scenes.find((item) => item.id === current)!.completion ?? { policy: "hold" };
    if (completion.policy !== "transition_to") { end = { kind: endings[completion.policy], scene_id: current }; break; }
    if (main.includes(completion.target_scene_id!)) { end = { kind: "back_to", scene_id: completion.target_scene_id! }; break; }
    current = completion.target_scene_id!;
  }
  const shuffle = document.program!.pools[0].scenes;
  return {
    source_revision: revision,
    starting_scene_id: document.program!.initial_scene_id,
    main_show: { scenes: main.map(summary), end },
    shuffle_list: {
      pool_id: document.program!.pools[0].id,
      scenes: shuffle.map((entry) => ({ ...summary(entry.scene_id), weight: entry.weight, chance_percent: 100, available: true, has_extra_rules: false })),
    },
    visitor_responses: [],
    unused_scenes: scenes.filter((scene) => !main.includes(scene.id) && !shuffle.some((entry) => entry.scene_id === scene.id) && scene.id !== "legacy-sweep").map((scene) => summary(scene.id)),
    ending_choices: [
      { kind: "repeat", label: "Repeat this scene" },
      { kind: "play_next", label: "Play a specific scene next" },
      { kind: "shuffle", label: "Let the show pick next (shuffle list and visitor responses)" },
      { kind: "freeze", label: "Stay on the last moment" },
      { kind: "stop", label: "Stop the show" },
    ],
    warnings: [],
  };
}

async function routes(page: Page) {
  await page.context().addCookies([{ name: "cueverge_csrf", value: "show-plan-csrf", url: "http://127.0.0.1:4173" }]);
  const state = { revision: 1, document: structuredClone(baseline), writes: [] as ShowPlanEdit[] };
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/session")) return route.fulfill({ json: { username: "admin", role: "administrator" } });
    if (path.endsWith("/installation")) return route.fulfill({ json: demoInstallation });
    if (path.endsWith("/installations")) return route.fulfill({ json: demoInstallations });
    if (path.endsWith("/runtime/status")) return route.fulfill({ json: demoRuntime });
    if (path.endsWith("/topology")) return route.fulfill({ json: demoTopology });
    if (path.endsWith("/projects")) return route.fulfill({ json: [{ ...demoProjects[0], head_revision: state.revision }] });
    if (path.endsWith("/draft")) return route.fulfill({ json: { project: demoProjects[0], revision: state.revision, draft: state.document } });
    if (path.endsWith("/show-plan") && request.method() === "PUT") {
      if (request.headers()["x-csrf-token"] !== "show-plan-csrf") return route.fulfill({ status: 403, json: { detail: { code: "csrf_failed" } } });
      const edit = request.postDataJSON() as ShowPlanEdit;
      expect(edit.source_revision).toBe(state.revision);
      state.writes.push(edit);
      if (edit.operation === "scene_ending") {
        const policy = Object.entries(endings).find(([, kind]) => kind === edit.ending)![0];
        const scene = state.document.scenes.find((item) => item.id === edit.scene_id)!;
        (scene as { completion: Completion }).completion = { policy, ...(edit.next_scene_id ? { target_scene_id: edit.next_scene_id } : {}) };
      }
      state.revision += 1;
      return route.fulfill({ json: { project: demoProjects[0], revision: state.revision, draft: state.document } });
    }
    if (path.endsWith("/show-plan")) return route.fulfill({ json: planFor(state.document, state.revision) });
    if (path.endsWith("/interactions")) return route.fulfill({ json: {
      source_revision: state.revision, cards: [], visitors: null, warnings: [],
      normal_playback: { starting_scene_id: "baseline-timeline", default_pool_id: "background", pools: state.document.program!.pools },
    } });
    if (["activity", "resource-assignments", "assets", "photos", "geometry", "mcp-tokens", "mcp-proposals", "scene-presets", "revisions", "published-packages", "simulation-scenarios", "comments", "presence", "change-requests", "members"].some((suffix) => path.endsWith(`/${suffix}`))) return route.fulfill({ json: [] });
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });
  return state;
}

test("show plan is the default show view and drills into scenes with a breadcrumb back", async ({ page }) => {
  const state = await routes(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByRole("button", { name: "Open show Home Show", exact: true }).click();
  const tabs = page.getByRole("group", { name: "Show workspace", exact: true });
  await expect(tabs.getByRole("button", { name: "Show plan", exact: true })).toHaveAttribute("aria-pressed", "true");
  const plan = page.getByRole("region", { name: "Show plan", exact: true });
  const order = plan.getByRole("list", { name: "Main show order" });
  await expect(order.getByText("Repeats Original synthetic timeline forever")).toBeVisible();
  await expect(plan.getByRole("heading", { name: "Scenes not in the show yet" })).toBeVisible();

  await plan.getByRole("combobox", { name: "When Original synthetic timeline ends" }).selectOption("play_next");
  await expect(order.getByRole("button", { name: /^Open scene Original synthetic legacy sweep/ })).toBeVisible();
  await expect(plan.getByRole("status")).toHaveText(/Saved\. Your live show changes only after you apply/);
  expect(state.writes).toEqual([{ source_revision: 1, operation: "scene_ending", scene_id: "baseline-timeline", ending: "play_next", next_scene_id: "legacy-sweep" }]);

  const results = await new AxeBuilder({ page }).include(".show-plan").analyze();
  expect(results.violations).toEqual([]);

  await order.getByRole("button", { name: /^Open scene Original synthetic legacy sweep/ }).click();
  await expect(tabs.getByRole("button", { name: "Scenes", exact: true })).toHaveAttribute("aria-pressed", "true");
  const crumbs = page.getByRole("navigation", { name: "Breadcrumb" });
  await expect(crumbs.getByText("Original synthetic legacy sweep")).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("heading", { name: "Original synthetic legacy sweep", exact: true })).toBeVisible();
  await crumbs.getByRole("button", { name: "Show plan", exact: true }).click();
  await expect(order).toBeVisible();
});
