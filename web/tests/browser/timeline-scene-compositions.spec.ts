import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { openTimelineEditor } from "./show-workspace";

let savedDraft: Record<string, unknown> | null = null;

async function mockDraftApi(page: Page) {
  savedDraft = null;
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/projects/demo-project/assets")) {
      return route.fulfill({ json: [] });
    }
    if (url.pathname.endsWith("/projects/demo-project/draft") && request.method() === "GET") {
      return route.fulfill({ json: response(1, document()) });
    }
    if (url.pathname.endsWith("/projects/demo-project/draft") && request.method() === "PUT") {
      savedDraft = request.postDataJSON();
      return route.fulfill({ json: response(2, savedDraft) });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });
}

test("a lights-only scene opens on its composition and shows placements on the whole scene", async ({
  page,
}) => {
  await mockDraftApi(page);
  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openTimelineEditor(page);

  const tabs = page.getByRole("tablist", { name: "Open compositions" });
  await expect(tabs.getByRole("tab", { name: "Ship lights" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(tabs.getByRole("tab", { name: "Window overlay" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Undo timeline edit" })).toBeDisabled();

  await tabs.getByRole("tab", { name: "Whole scene" }).click();
  const lane = page.getByLabel("Track Compositions in this scene");
  const clip = lane.getByRole("button", { name: "Open composition Ship lights" });
  await expect(clip).toBeVisible();
  await expect(clip).toHaveAttribute("style", /left: 20%; width: 40%;/);
  await expect(page.getByRole("button", { name: "Undo timeline edit" })).toBeDisabled();

  await clip.click();
  await expect(tabs.getByRole("tab", { name: "Ship lights" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByRole("button", { name: "Undo timeline edit" })).toBeDisabled();
});

test("scenes with media outputs open on the whole scene and list every placement", async ({
  page,
}) => {
  await mockDraftApi(page);
  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openTimelineEditor(page);
  await page.locator(".timeline-header__context select").first().selectOption("projection");

  const tabs = page.getByRole("tablist", { name: "Open compositions" });
  await expect(tabs.getByRole("tab", { name: "Whole scene" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(tabs.getByRole("tab", { name: "Window overlay" })).toBeVisible();
  await expect(tabs.getByRole("tab", { name: "Ship lights" })).toBeVisible();
  const lane = page.getByLabel("Track Compositions in this scene");
  await expect(lane.getByRole("button", { name: /^Open composition / })).toHaveCount(2);
  await expect(
    lane.getByRole("button", { name: "Open composition Window overlay" }),
  ).toHaveAttribute("style", /left: 0%; width: 50%;/);
  await expect(
    lane.getByRole("button", { name: "Open composition Ship lights" }),
  ).toHaveAttribute("style", /left: 50%; width: 50%;/);
  await expect(page.getByRole("button", { name: "Undo timeline edit" })).toBeDisabled();
  expect(savedDraft).toBeNull();
});

function response(revision: number, draft: unknown) {
  return {
    project: {
      id: "demo-project",
      name: "Home Show",
      head_revision: revision,
      metadata_revision: 1,
      archived_at: null,
      created_at: "2026-10-14T12:00:00Z",
      updated_at: "2026-10-14T12:00:00Z",
    },
    revision,
    draft,
  };
}

function composition(id: string, name: string, durationMs: number) {
  return {
    id,
    name,
    duration_ms: durationMs,
    space_kind: "stage",
    target_id: "shared-stage",
    width: 10,
    height: 4,
    local_time_offset_ms: 0,
    children: [],
    safe_points_ms: [],
    protected_intervals: [],
  };
}

function document() {
  return {
    schema_version: 1,
    installation: { schema_version: 1, resources: [] },
    editor_metadata: {
      compositions: {
        lights: composition("lights", "Ship lights", 2000),
        overlay: composition("overlay", "Window overlay", 2000),
      },
      scene_compositions: { ship: ["lights"], projection: ["overlay"] },
      composition_instances: {
        ship: [
          {
            id: "ship-lights",
            composition_id: "lights",
            start_offset_ms: 1000,
            time_scale: 1,
            parameter_overrides: {},
            placement: { x: 0, y: 0 },
          },
        ],
        projection: [
          {
            id: "late-lights",
            composition_id: "lights",
            start_offset_ms: 2000,
            time_scale: 1,
            parameter_overrides: {},
            placement: { x: 0, y: 0 },
          },
        ],
      },
    },
    scenes: [
      {
        schema_version: 2,
        id: "ship",
        name: "Ship ambient",
        mode: "free_running",
        resources: [],
        duration_ms: 5000,
        clock_source: null,
        media: [],
        lighting: [],
        scheduling: { safe_points_ms: [500], protected_intervals: [] },
      },
      {
        schema_version: 2,
        id: "projection",
        name: "Projection",
        mode: "media_synchronized",
        resources: ["projector.left"],
        duration_ms: 4000,
        clock_source: "left",
        media: [
          {
            id: "left",
            resource: "projector.left",
            asset: null,
            required: true,
            start_delay_ms: 0,
          },
        ],
        lighting: [],
        scheduling: { safe_points_ms: [], protected_intervals: [] },
      },
    ],
  };
}
