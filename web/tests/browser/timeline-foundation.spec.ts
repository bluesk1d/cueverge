import { expect, Page, test } from "@playwright/test";
import { openAdvancedShowWorkspace } from "./show-workspace";

let savedDraft: Record<string, unknown> | null = null;

async function installTimelineRoutes(
  page: Page,
  document: Record<string, unknown> = draftDocument(),
  assets: unknown[] = [],
) {
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/projects/demo-project/assets")) {
      return route.fulfill({ json: assets });
    }
    if (
      url.pathname.endsWith("/projects/demo-project/draft") &&
      request.method() === "GET"
    ) {
      return route.fulfill({ json: draftResponse(1, document) });
    }
    if (
      url.pathname.endsWith("/projects/demo-project/draft") &&
      request.method() === "PUT"
    ) {
      savedDraft = request.postDataJSON();
      return route.fulfill({ json: draftResponse(2, savedDraft) });
    }
    if (url.pathname.endsWith("/installations/demo-installation/stage-coverage")) {
      return route.fulfill({ json: stageCoverage() });
    }
    if (url.pathname.endsWith("/installations/demo-installation/mapper-export")) {
      return route.fulfill({ json: mapperExport() });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });
}

test("timeline ruler markers regions loop and snapping persist", async ({ page }) => {
  await installTimelineRoutes(page);

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openAdvancedShowWorkspace(page);
  await page.getByText("Timeline editor", { exact: true }).click();

  await expect(page.getByText("0s", { exact: true })).toBeVisible();
  await expect(page.getByText("5s", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Marker at 1000 milliseconds" }),
  ).toBeVisible();

  const timeline = page.getByLabel("Timeline canvas");
  await timeline.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByText("Global 100 ms")).toBeVisible();
  await page.getByRole("button", { name: "Add marker" }).click();
  await expect(
    page.getByRole("button", { name: "Marker at 100 milliseconds" }),
  ).toBeVisible();

  await page.getByLabel("Protected start").fill("3000");
  await page.getByLabel("Protected end").fill("3500");
  await page.getByRole("button", { name: "Add protected region" }).click();
  await page.getByLabel("Loop start").fill("500");
  await page.getByLabel("Loop end").fill("4000");
  await page.getByLabel("Scene priority").fill("75");
  await page.getByLabel("Completion policy").selectOption("loop");
  await page.getByLabel("Request policy").selectOption("at_safe_point");
  await page.getByLabel("Request TTL").fill("12000");
  await page
    .getByLabel("Resource claims")
    .fill("projector.left, wled-001");
  await page.getByLabel("Clock source").selectOption("left-window");
  await page.getByLabel("Media calibration offset left-window").fill("125");
  await page.getByLabel("Required lighting output roofline").uncheck();
  await page.getByLabel("Lighting calibration offset roofline").fill("-40");
  await expect(page.getByRole("button", { name: "media output · left-window" })).toBeVisible();
  await expect(page.getByRole("button", { name: "lighting output · roofline" })).toBeVisible();
  await page.getByLabel("New track kind").selectOption("fixture");
  await page.getByLabel("New track name").fill("Window fixture");
  await page.getByLabel("New track resource binding").fill("fixture.window");
  await page.getByRole("button", { name: "Add track" }).click();
  await expect(page.getByText("Kind: fixture")).toBeVisible();
  await expect(page.getByText(/Stable ID:/)).toBeVisible();
  await page.getByRole("button", { name: "Save timeline" }).click();

  await expect.poll(() => savedDraft).not.toBeNull();
  await page.getByLabel("New composition name").fill("Parent composition");
  await page.getByLabel("New composition duration").fill("5000");
  await page.getByLabel("New composition space").selectOption("stage");
  await page.getByLabel("New composition target").fill("shared-stage");
  await page.getByLabel("New composition width").fill("10");
  await page.getByLabel("New composition height").fill("4");
  await page.getByLabel("New composition local offset").fill("0");
  await page.getByRole("button", { name: "Create composition" }).click();
  await page.getByLabel("New composition name").fill("Child composition");
  await page.getByLabel("New composition duration").fill("2000");
  await page
    .getByLabel("New composition space")
    .selectOption("projector_native");
  await page.getByLabel("New composition target").fill("projector.left");
  await page.getByLabel("New composition width").fill("1920");
  await page.getByLabel("New composition height").fill("1080");
  await page.getByLabel("New composition local offset").fill("500");
  await page.getByRole("button", { name: "Create composition" }).click();
  const hierarchy = page.locator(".composition-hierarchy");
  await hierarchy
    .locator("li")
    .filter({ has: page.getByText("Parent composition", { exact: true }) })
    .getByRole("button", { name: "Open timeline" })
    .click();
  await expect(
    page.getByRole("img", { name: "Stage composition overlay" }),
  ).toBeVisible();
  await page.getByLabel("Composition to use").selectOption({
    label: "Child composition",
  });

  await page
    .getByRole("button", { name: "Add child to open composition" })
    .click();
  await page.getByLabel("Instance start offset").fill("750");
  await page.getByLabel("Instance time scale").fill("1.25");
  await page.getByLabel("Instance stage X").fill("2.5");
  await page.getByLabel("Instance stage Y").fill("1.25");
  await page
    .getByLabel("Instance parameter overrides")
    .fill('{"opacity":0.8,"variant":"ghost"}');
  await page.getByRole("button", { name: "Instantiate in scene" }).click();
  await expect(page.getByText(/Start 750 ms · time scale 1.25×/)).toBeVisible();
  await hierarchy
    .locator("li")
    .filter({ has: page.getByText("Child composition", { exact: true }) })
    .getByRole("button", { name: "Open timeline" })
    .click();
  const breadcrumbs = page.getByRole("navigation", {
    name: "Composition breadcrumbs",
  });
  await expect(breadcrumbs.getByRole("button", { name: "Parent composition" })).toBeVisible();
  await expect(breadcrumbs.getByRole("button", { name: "Child composition" })).toBeVisible();
  await expect(page.getByText("Global 100 ms · Local 0 ms")).toBeVisible();
  const linkedInstance = page.locator(".composition-instance").filter({
    hasText: "Instance · Child composition",
  });
  await expect(linkedInstance).toContainText(
    'Overrides: {"opacity":0.8,"variant":"ghost"}',
  );
  await timeline.focus();
  for (let index = 0; index < 5; index += 1) {
    await page.keyboard.press("ArrowRight");
  }
  await expect(page.getByText("Global 600 ms · Local 100 ms")).toBeVisible();
  await page.getByRole("button", { name: "Add marker" }).click();
  await page.getByRole("button", { name: "Save timeline" }).click();
  await expect.poll(() => (savedDraft as { editor_metadata?: unknown })?.editor_metadata).not.toBeNull();

  const saved = savedDraft as ReturnType<typeof draftDocument>;
  expect(saved.scenes[0].scheduling.safe_points_ms).toEqual([100, 1000]);
  expect(saved.scenes[0]).toMatchObject({
    priority: 75,
    resources: ["projector.left", "wled-001"],
    completion: { policy: "loop" },
    clock_source: "left-window",
  });
  expect(saved.scenes[0].scheduling).toMatchObject({
    request_policy: "at_safe_point",
    request_ttl_ms: 12000,
  });
  expect(saved.scenes[0].media[0]).toMatchObject({
    id: "left-window",
    required: true,
    start_delay_ms: 125,
  });
  expect(saved.scenes[0].lighting[0]).toMatchObject({
    id: "roofline",
    required: false,
    timing_offset_ms: -40,
  });
  expect(saved.scenes[0].scheduling.protected_intervals).toEqual([
    { start_ms: 2000, end_ms: 2500 },
    { start_ms: 3000, end_ms: 3500 },
  ]);
  expect(
    (
      saved.editor_metadata.timeline_views as Record<
        string,
        Record<string, number>
      >
    ).ambient,
  ).toMatchObject({
    loop_start_ms: 500,
    loop_end_ms: 4000,
    quantization_ms: 100,
  });
  const customTracks = (
    saved.editor_metadata.timeline_tracks as Record<
      string,
      { id: string; kind: string; name: string; resource_binding: string }[]
    >
  ).ambient;
  expect(customTracks).toHaveLength(1);
  expect(customTracks[0]).toMatchObject({
    kind: "fixture",
    name: "Window fixture",
    resource_binding: "fixture.window",
  });
  expect(customTracks[0].id).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
  const compositions = saved.editor_metadata.compositions as Record<
    string,
    {
      id: string;
      name: string;
      children: string[];
      safe_points_ms: number[];
      space_kind: string;
      target_id: string;
      width: number;
      height: number;
      local_time_offset_ms: number;
    }
  >;
  const parent = Object.values(compositions).find(
    (composition) => composition.name === "Parent composition",
  )!;
  const child = Object.values(compositions).find(
    (composition) => composition.name === "Child composition",
  )!;
  expect(parent.children).toEqual([child.id]);
  expect(parent).toMatchObject({
    space_kind: "stage",
    target_id: "shared-stage",
    width: 10,
    height: 4,
  });
  expect(child).toMatchObject({
    space_kind: "projector_native",
    target_id: "projector.left",
    width: 1920,
    height: 1080,
    local_time_offset_ms: 500,
  });
  expect(child.safe_points_ms).toEqual([100]);
  expect(
    (
      saved.editor_metadata.scene_compositions as Record<string, string[]>
    ).ambient,
  ).toEqual([child.id]);
  const instance = (
    saved.editor_metadata.composition_instances as Record<
      string,
      {
        id: string;
        composition_id: string;
        start_offset_ms: number;
        time_scale: number;
        parameter_overrides: Record<string, unknown>;
        placement: { x: number; y: number };
      }[]
    >
  ).ambient[0];
  expect(instance.id).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
  expect(instance).toMatchObject({
    composition_id: child.id,
    start_offset_ms: 750,
    time_scale: 1.25,
    parameter_overrides: { opacity: 0.8, variant: "ghost" },
    placement: { x: 2.5, y: 1.25 },
  });
  expect(instance.composition_id).toBe(child.id);
});

test("controller clock can be selected with media outputs and saved", async ({ page }) => {
  savedDraft = null;
  await installTimelineRoutes(page);
  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openAdvancedShowWorkspace(page);
  await page.getByText("Timeline editor", { exact: true }).click();
  await page.getByLabel("Clock authority").selectOption("controller_timed");
  await expect(page.getByLabel("Clock source")).toBeDisabled();
  await expect(page.getByLabel("Clock source")).toHaveValue("controller");
  await page.getByLabel("Required media output left-window").uncheck();
  await expect(page.getByLabel("Timeline validation")).toContainText("Timeline valid");
  await page.getByRole("button", { name: "Save timeline" }).click();
  await expect.poll(() => savedDraft).not.toBeNull();
  expect((savedDraft as ReturnType<typeof draftDocument> | null)?.scenes[0]).toMatchObject({
    mode: "controller_timed",
    clock_source: "controller",
    media: [expect.objectContaining({ required: false })],
  });

  await page.getByLabel("Clock authority").selectOption("media_synchronized");
  await expect(page.getByLabel("Timeline validation")).toContainText(
    "The clock-source media output must remain required.",
  );
});

test("LED-only scene remains valid and saves without media dependencies", async ({ page }) => {
  savedDraft = null;
  const document = draftDocument();
  document.scenes[0].mode = "controller_timed";
  document.scenes[0].clock_source = "controller";
  document.scenes[0].media = [];
  document.scenes[0].resources = ["wled-001"];
  await installTimelineRoutes(page, document);
  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openAdvancedShowWorkspace(page);
  await page.getByText("Timeline editor", { exact: true }).click();
  await expect(page.getByLabel("Clock authority")).toHaveValue("controller_timed");
  await expect(page.getByLabel("Timeline validation")).toContainText("Timeline valid");
  await page.getByRole("button", { name: "Save timeline" }).click();
  await expect.poll(() => savedDraft).not.toBeNull();
  expect((savedDraft as ReturnType<typeof draftDocument> | null)?.scenes[0]).toMatchObject({
    mode: "controller_timed",
    clock_source: "controller",
    media: [],
    resources: ["wled-001"],
  });
});

test("timeline marker workflow is keyboard operable", async ({ page }) => {
  savedDraft = null;
  await installTimelineRoutes(page);
  await page.goto("/demo.html");

  await page.getByRole("button", { name: "Shows", exact: true }).focus();
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: /^Open show / }).first().focus();
  await page.keyboard.press("Enter");
  await page.getByText("Advanced authoring and history", { exact: true }).focus();
  await page.keyboard.press("Enter");
  await page.getByText("Timeline editor", { exact: true }).focus();
  await page.keyboard.press("Enter");

  const timeline = page.getByLabel("Timeline canvas");
  await timeline.focus();
  await page.keyboard.press("ArrowRight");
  await page.getByRole("button", { name: "Add marker" }).focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("button", { name: "Marker at 100 milliseconds" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Save timeline" }).focus();
  await page.keyboard.press("Enter");
  await expect.poll(() => savedDraft).not.toBeNull();
});

test("maximum asset collection remains virtualized", async ({ page }) => {
  const document = draftDocument();
  const assets = Array.from({ length: 5000 }, (_, index) => ({
    id: `asset-${index}`,
    project_id: "demo-project",
    name: `Scale asset ${index}`,
    status: "ready",
    current_revision: 1,
    metadata_revision: 1,
    owner: null,
    license: null,
    provenance: null,
    review_status: "approved",
    expires_on: null,
    folder: "Scale fixtures",
    tags: [],
    favorite: false,
    error: null,
    digest_sha256: `${index}`.padStart(64, "0"),
    size_bytes: 1024,
    media_type: "image/png",
    original_filename: `asset-${index}.png`,
    scan_status: "clean",
    metadata: null,
    original_file_status: "available",
    artifact_faults: [],
    references: [],
    unused: true,
    created_at: "2026-10-14T12:00:00Z",
    updated_at: "2026-10-14T12:00:00Z",
  }));
  await installTimelineRoutes(page, document, assets);
  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openAdvancedShowWorkspace(page);

  const assetList = page.locator(
    '.virtual-list[aria-label="Assets in Home Show"]',
  );
  await expect(assetList).toHaveAttribute("data-total-count", "5000");
  expect(await assetList.locator('[role="listitem"]').count()).toBeLessThan(20);
});

test("maximum timeline marker collection remains virtualized", async ({ page }) => {
  const document = draftDocument();
  document.editor_metadata = {
    ...document.editor_metadata,
    timeline_markers: {
      ambient: Array.from({ length: 10_000 }, (_, index) => ({
        id: `marker-${index}`,
        name: `Scale marker ${index}`,
        time_ms: index % 5000,
      })),
    },
  };
  await installTimelineRoutes(page, document);
  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openAdvancedShowWorkspace(page);
  const startedAt = Date.now();
  await page.getByText("Timeline editor", { exact: true }).click();
  const markerList = page.locator(
    '.virtual-list[aria-label="Synchronization markers"]',
  );
  await expect(markerList).toHaveAttribute("data-total-count", "10000");
  expect(Date.now() - startedAt).toBeLessThan(5000);
  expect(await markerList.locator('[role="listitem"]').count()).toBeLessThan(30);
  expect(await page.locator(".timeline-sync-marker").count()).toBeLessThanOrEqual(500);
});

test("semantic autonomous show logic persists from the project workspace", async ({
  page,
}) => {
  savedDraft = null;
  const document: Record<string, unknown> = {
    ...draftDocument(),
    installation: {
      schema_version: 1,
      resources: [
        {
          resource: "projector.left",
          kind: "media_agent",
          target: "media-agent-left",
        },
        {
          resource: "wled-001",
          kind: "pixel_output",
          target: "memory-wled",
          pixel_count: 2,
        },
      ],
    },
  };
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/projects/demo-project/assets")) {
      return route.fulfill({ json: [] });
    }
    if (
      url.pathname.endsWith("/projects/demo-project/draft") &&
      request.method() === "GET"
    ) {
      return route.fulfill({ json: draftResponse(1, document) });
    }
    if (
      url.pathname.endsWith("/projects/demo-project/draft") &&
      request.method() === "PUT"
    ) {
      savedDraft = request.postDataJSON();
      return route.fulfill({ json: draftResponse(2, savedDraft) });
    }
    if (url.pathname.endsWith("/installations/demo-installation/stage-coverage")) {
      return route.fulfill({ json: stageCoverage() });
    }
    if (url.pathname.endsWith("/installations/demo-installation/mapper-export")) {
      return route.fulfill({ json: mapperExport() });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openAdvancedShowWorkspace(page);
  await page.getByText("Autonomous show logic designer", { exact: true }).click();
  await page
    .getByRole("button", { name: "Visitor interaction template" })
    .click();
  await expect(
    page.getByRole("table", {
      name: "Accessible encounter transition editor",
    }),
  ).toBeVisible();
  await expect(
    page.getByText(/wall-clock schedules are intentionally unavailable/i),
  ).toBeVisible();
  await page.getByLabel("Recent history exclusion").fill("2");
  await page.getByRole("button", { name: "Save autonomous logic" }).click();

  await expect.poll(() => savedDraft).not.toBeNull();
  const saved = savedDraft as {
    program: { recent_history_size: number; pools: unknown[] };
    opportunities: { opportunities: unknown[] };
    encounter: { transitions: unknown[] };
  };
  expect(saved.program.recent_history_size).toBe(2);
  expect(saved.program.pools).toHaveLength(1);
  expect(saved.opportunities.opportunities).toHaveLength(1);
  expect(saved.encounter.transitions).toHaveLength(2);
});

test("approved revision can be compared rehearsed published and staged", async ({
  page,
}) => {
  let approval = "draft";
  let packageState: string | null = null;
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/projects/demo-project/assets")) {
      return route.fulfill({ json: [] });
    }
    if (url.pathname.endsWith("/projects/demo-project/draft")) {
      return route.fulfill({ json: draftResponse(2, draftDocument()) });
    }
    if (url.pathname.endsWith("/projects/demo-project/revisions")) {
      return route.fulfill({
        json: [
          {
            revision: 2,
            parent_revision: 1,
            actor: "admin",
            transaction_kind: "update",
            created_at: "2026-10-16T12:00:00Z",
          },
          {
            revision: 1,
            parent_revision: null,
            actor: "admin",
            transaction_kind: "create",
            created_at: "2026-10-16T11:00:00Z",
          },
        ],
      });
    }
    if (url.pathname.endsWith("/revisions/2/release")) {
      if (request.method() === "PUT") {
        approval = request.postDataJSON().approval_state;
      }
      return route.fulfill({
        json: {
          project_id: "demo-project",
          revision: 2,
          name: approval === "approved" ? "Opening night" : null,
          summary: approval === "approved" ? "Ready for staging." : null,
          approval_state: approval,
          approved_by: approval === "approved" ? "admin" : null,
          approved_at:
            approval === "approved" ? "2026-10-16T12:10:00Z" : null,
        },
      });
    }
    if (url.pathname.endsWith("/revision-diff")) {
      return route.fulfill({
        json: {
          project_id: "demo-project",
          base_revision: 1,
          candidate_revision: 2,
          changed_paths: ["/scenes/0/duration_ms"],
        },
      });
    }
    if (url.pathname.endsWith("/publication-validation")) {
      return route.fulfill({
        json: {
          project_id: "demo-project",
          revision: 2,
          passed: true,
          diagnostics: [
            {
              code: "validation_complete",
              severity: "info",
              message: "All publication checks completed.",
            },
          ],
        },
      });
    }
    if (url.pathname.endsWith("/published-packages")) {
      return route.fulfill({
        json:
          packageState === null
            ? []
            : [
                {
                  id: "package-1",
                  project_id: "demo-project",
                  revision: 2,
                  digest_sha256: "a".repeat(64),
                  state: packageState,
                  manifest: {},
                  created_by: "admin",
                  created_at: "2026-10-16T12:11:00Z",
                  staged_at: packageState === "staged" ? "2026-10-16T12:12:00Z" : null,
                  activated_at: null,
                  deactivated_at: null,
                },
              ],
      });
    }
    if (url.pathname.endsWith("/revisions/2/publish")) {
      packageState = "published";
      return route.fulfill({ status: 201, json: {} });
    }
    if (url.pathname.endsWith("/published-packages/package-1/stage")) {
      packageState = "staged";
      return route.fulfill({ json: {} });
    }
    if (url.pathname.endsWith("/simulation-scenarios")) {
      if (request.method() === "GET") {
        return route.fulfill({ json: [] });
      }
      return route.fulfill({
        status: 201,
        json: {
          id: "scenario-1",
          project_id: "demo-project",
          name: "Publication rehearsal r2",
          source_revision: 2,
          steps: request.postDataJSON().steps,
          expected: {},
          created_at: "2026-10-16T12:00:00Z",
          updated_at: "2026-10-16T12:00:00Z",
        },
      });
    }
    if (url.pathname.endsWith("/simulation-scenarios/scenario-1/run")) {
      return route.fulfill({
        json: {
          scenario_id: "scenario-1",
          source_revision: 2,
          passed: true,
          mismatches: [],
          actual: {},
          hardware_adapters_opened: false,
          hardware_commands_sent: false,
        },
      });
    }
    if (url.pathname.endsWith("/installations/demo-installation/stage-coverage")) {
      return route.fulfill({ json: stageCoverage() });
    }
    if (url.pathname.endsWith("/installations/demo-installation/mapper-export")) {
      return route.fulfill({ json: mapperExport() });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openAdvancedShowWorkspace(page);
  await page
    .getByText("Rehearsal, publication, and rollback", { exact: true })
    .click();
  await expect(page.getByText("Preflight passed")).toBeVisible();
  await page.getByLabel("Revision name").fill("Opening night");
  await page.getByLabel("Change summary").fill("Ready for staging.");
  await page.getByLabel("Approval state").selectOption("approved");
  await page.getByRole("button", { name: "Save release metadata" }).click();
  await expect(page.getByText(/Approved by admin/)).toBeVisible();
  await page.getByLabel("Comparison base revision").selectOption("1");
  await page.getByRole("button", { name: "Compare revisions" }).click();
  await expect(page.getByText("/scenes/0/duration_ms")).toBeVisible();
  await page.getByRole("button", { name: "Start rehearsal" }).click();
  await expect(
    page.getByText("Rehearsal completed without hardware commands."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Publish immutable package" }).click();
  await expect(page.getByText("r2 · published")).toBeVisible();
  await page.getByRole("button", { name: "Stage" }).click();
  await expect(page.getByText("r2 · staged")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Activate", exact: true }),
  ).toBeEnabled();
});

function draftResponse(revision: number, draft: Record<string, unknown>) {
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

function draftDocument() {
  return {
    schema_version: 1,
    installation: { schema_version: 1, resources: [] },
    editor_metadata: { installation_id: "demo-installation" },
    scenes: [
      {
        schema_version: 2,
        id: "ambient",
        name: "Ambient",
        mode: "media_synchronized",
        resources: ["projector.left", "wled-001"],
        duration_ms: 5000,
        clock_source: "left-window",
        media: [
          {
            id: "left-window",
            resource: "projector.left",
            asset: null,
            required: true,
            start_delay_ms: 0,
          },
        ],
        lighting: [
          {
            id: "roofline",
            resource: "wled-001",
            required: true,
            effect: "timeline",
            timeline: [],
          },
        ],
        scheduling: {
          safe_points_ms: [1000],
          protected_intervals: [{ start_ms: 2000, end_ms: 2500 }],
        },
      },
    ],
  };
}

function stageCoverage() {
  return {
    installation_id: "demo-installation",
    minimum_x: 0,
    minimum_y: 0,
    maximum_x: 10,
    maximum_y: 4,
    regions: [
      {
        kind: "covered",
        points: [[0, 0], [5, 0], [5, 4], [0, 4]],
        resources: ["projector.left"],
        source_ids: ["surface-left"],
      },
      {
        kind: "covered",
        points: [[5, 0], [10, 0], [10, 4], [5, 4]],
        resources: ["projector.right"],
        source_ids: ["surface-right"],
      },
    ],
  };
}

function mapperExport() {
  return {
    schema_version: 1,
    installation_id: "demo-installation",
    resources: [],
    projectors: [],
    lighting_surfaces: [
      {
        semantic_id: "roofline",
        resource: "wled-001",
        pixels: [
          { output_index: 0, stage_x: 2, stage_y: 3 },
          { output_index: 1, stage_x: 8, stage_y: 3 },
        ],
      },
    ],
    fixtures: [],
    paths: [],
    zones: [
      {
        semantic_id: "audience",
        name: "Audience",
        kind: "audience_zone",
        stage_points: [[3, 2], [7, 2], [7, 4], [3, 4]],
      },
      {
        semantic_id: "walkway",
        name: "No-output walkway",
        kind: "safety_zone",
        stage_points: [[0, 3], [2, 3], [2, 4], [0, 4]],
      },
    ],
  };
}
