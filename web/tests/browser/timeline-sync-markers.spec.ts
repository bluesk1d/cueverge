import { expect, test } from "@playwright/test";

let savedDraft: Record<string, unknown> | null = null;

test("named markers move linked cues and compile scene timestamps", async ({ page }) => {
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
      return route.fulfill({ json: draftResponse(1, draftDocument()) });
    }
    if (
      url.pathname.endsWith("/projects/demo-project/draft") &&
      request.method() === "PUT"
    ) {
      savedDraft = request.postDataJSON();
      return route.fulfill({ json: draftResponse(2, savedDraft) });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByText("Timeline editor", { exact: true }).click();

  const timeline = page.getByLabel("Timeline canvas");
  await timeline.focus();
  for (let index = 0; index < 10; index += 1) {
    await page.keyboard.press("ArrowRight");
  }
  await page.getByLabel("New sync marker name").fill("Window impact");
  await page.getByRole("button", { name: "Add sync marker" }).click();

  await page.getByLabel("New synchronized cue name").fill("Projection flash");
  await page.getByLabel("New synchronized cue kind").selectOption("visual");
  await page
    .getByLabel("New synchronized cue track")
    .selectOption("media:left-window");
  await page.getByRole("button", { name: "Add synchronized cue" }).click();

  await page.getByLabel("New synchronized cue name").fill("Roofline flash");
  await page.getByLabel("New synchronized cue kind").selectOption("lighting");
  await page
    .getByLabel("New synchronized cue track")
    .selectOption("lighting:roofline");
  await page.getByLabel("New synchronized cue marker offset").fill("100");
  await page.getByRole("button", { name: "Add synchronized cue" }).click();
  await addMarkerCue(page, "Audio hit", "audio", "-50");
  await addMarkerCue(page, "Relay switch", "switch", "200");
  await addMarkerCue(page, "Fog burst", "physical_effect", "300");

  await page.getByLabel("Sync marker time Window impact").fill("1500");
  await expect(page.getByText(/Projection flash/).locator("..")).toContainText(
    "local 1500 ms",
  );
  await expect(page.getByText(/Roofline flash/).locator("..")).toContainText(
    "local 1600 ms",
  );

  const projectionCue = page
    .locator(".timeline-sync-cue-row")
    .filter({ hasText: "Projection flash" });
  await projectionCue.getByRole("button", { name: "Detach cue" }).click();
  await page.getByLabel("Sync marker time Window impact").fill("2000");
  await expect(projectionCue).toContainText("local 1500 ms");
  await expect(projectionCue).toContainText("detached");
  await expect(
    page.locator(".timeline-sync-cue-row").filter({ hasText: "Roofline flash" }),
  ).toContainText("local 2100 ms");
  const lightingCue = page
    .locator(".timeline-sync-cue-row")
    .filter({ hasText: "Roofline flash" });
  await lightingCue.getByLabel("Lighting cue start Roofline flash").fill("2200");
  await lightingCue
    .getByLabel("Lighting cue duration Roofline flash")
    .fill("700");
  await lightingCue
    .getByLabel("Lighting cue primitive Roofline flash")
    .selectOption("color_transition");
  await lightingCue
    .getByLabel("Lighting cue target kind Roofline flash")
    .selectOption("segment");
  await lightingCue
    .getByLabel("Lighting cue target ID Roofline flash")
    .fill("roofline-segment");
  await lightingCue.getByLabel("Lighting cue color Roofline flash").fill("#ff6600");
  await lightingCue
    .getByLabel("Lighting cue end color Roofline flash")
    .fill("#2200ff");
  await lightingCue
    .getByLabel("Lighting cue brightness Roofline flash")
    .fill("0.6");
  await lightingCue
    .getByLabel("Lighting cue blend Roofline flash")
    .selectOption("add");
  await lightingCue
    .getByLabel("Lighting cue precedence Roofline flash")
    .fill("7");
  await lightingCue.getByLabel("Lighting cue seed Roofline flash").fill("42");
  await lightingCue
    .getByLabel("Lighting cue period Roofline flash")
    .fill("300");
  await lightingCue
    .getByLabel("Lighting cue density Roofline flash")
    .fill("0.4");
  await lightingCue.getByLabel("Lighting cue reverse Roofline flash").check();
  await expect(lightingCue).toContainText("local 2200 ms");
  await lightingCue.getByLabel("Select cue Roofline flash").check();
  await page.getByLabel("New cue preset name").fill("Impact flash");
  await page
    .getByRole("button", { name: "Save selected lighting cue as preset" })
    .click();
  await lightingCue
    .getByLabel("Lighting cue brightness Roofline flash")
    .fill("0.2");
  await page
    .getByRole("button", { name: "Apply preset to selected cues" })
    .click();
  await expect(
    lightingCue.getByLabel("Lighting cue brightness Roofline flash"),
  ).toHaveValue("0.6");
  await projectionCue.getByLabel("Select cue Projection flash").check();
  await page.getByLabel("Compound shift milliseconds").fill("100");
  await page.getByRole("button", { name: "Shift selected" }).click();
  await expect(projectionCue).toContainText("local 1600 ms");
  await expect(lightingCue).toContainText("local 2300 ms");
  await page.getByRole("button", { name: "Duplicate selected" }).click();
  await expect(page.getByText("Projection flash copy", { exact: true })).toBeVisible();
  await expect(page.getByText("Roofline flash copy", { exact: true })).toBeVisible();

  await page.getByLabel("Editing space").selectOption("impact-composition");
  await expect(
    page.locator(".timeline-sync-cue-row").filter({ hasText: "Nested relay" }),
  ).toContainText("Scene timestamps: 800 ms");

  await page.getByRole("button", { name: "Save timeline" }).click();
  await expect.poll(() => savedDraft).not.toBeNull();

  const saved = savedDraft as ReturnType<typeof draftDocument>;
  expect(saved.editor_metadata.timeline_markers.ambient[0]).toMatchObject({
    name: "Window impact",
    time_ms: 2000,
  });
  expect(saved.editor_metadata.timeline_cues.ambient).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        name: "Projection flash",
        time_ms: 1600,
        marker_id: null,
        marker_offset_ms: 0,
      }),
      expect.objectContaining({
        name: "Roofline flash",
        time_ms: null,
        marker_offset_ms: 300,
        lighting: expect.objectContaining({
          primitive: "color_transition",
          duration_ms: 700,
          target: { kind: "segment", id: "roofline-segment" },
          color: "#FF6600",
          end_color: "#2200FF",
          brightness: 0.6,
          blend_mode: "add",
          precedence: 7,
          seed: 42,
          period_ms: 300,
          density: 0.4,
          reverse: true,
        }),
      }),
    ]),
  );
  expect(saved.editor_metadata.timeline_sync_schedule).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        cue_name: "Projection flash",
        scene_id: "ambient",
        scene_time_ms: 1600,
      }),
      expect.objectContaining({
        cue_name: "Roofline flash",
        scene_id: "ambient",
        scene_time_ms: 2300,
      }),
      expect.objectContaining({
        cue_name: "Audio hit",
        kind: "audio",
        marker_name: "Window impact",
        scene_time_ms: 1950,
      }),
      expect.objectContaining({
        cue_name: "Relay switch",
        kind: "switch",
        marker_name: "Window impact",
        scene_time_ms: 2200,
      }),
      expect.objectContaining({
        cue_name: "Fog burst",
        kind: "physical_effect",
        marker_name: "Window impact",
        scene_time_ms: 2300,
      }),
      expect.objectContaining({
        cue_name: "Nested relay",
        context_id: "instance:impact-instance",
        scene_id: "ambient",
        scene_time_ms: 800,
      }),
    ]),
  );
  expect(saved.editor_metadata.timeline_cue_presets[0]).toMatchObject({
    name: "Impact flash",
    lighting: {
      primitive: "color_transition",
      brightness: 0.6,
      blend_mode: "add",
    },
  });
  expect(saved.editor_metadata.timeline_cues.ambient).toHaveLength(7);
});

async function addMarkerCue(
  page: import("@playwright/test").Page,
  name: string,
  kind: "audio" | "switch" | "physical_effect",
  offset: string,
) {
  await page.getByLabel("New synchronized cue name").fill(name);
  await page.getByLabel("New synchronized cue kind").selectOption(kind);
  await page
    .getByLabel("New synchronized cue track")
    .selectOption("media:left-window");
  await page.getByLabel("New synchronized cue marker offset").fill(offset);
  await page.getByRole("button", { name: "Add synchronized cue" }).click();
}

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
    editor_metadata: {
      compositions: {
        "impact-composition": {
          id: "impact-composition",
          name: "Impact composition",
          duration_ms: 1000,
          space_kind: "stage",
          target_id: "shared-stage",
          width: 10,
          height: 4,
          local_time_offset_ms: 0,
          children: [],
          safe_points_ms: [],
          protected_intervals: [],
        },
      },
      composition_instances: {
        ambient: [
          {
            id: "impact-instance",
            composition_id: "impact-composition",
            start_offset_ms: 500,
            time_scale: 2,
            parameter_overrides: {},
            placement: { x: 0, y: 0 },
          },
        ],
      },
      timeline_tracks: {
        "impact-composition": [
          {
            id: "relay-track",
            kind: "fixture",
            name: "Relay",
            resource_binding: "relay.front-door",
          },
        ],
      },
      timeline_markers: {
        "impact-composition": [
          { id: "nested-marker", name: "Nested impact", time_ms: 100 },
        ],
      },
      timeline_cues: {
        "impact-composition": [
          {
            id: "nested-cue",
            name: "Nested relay",
            kind: "switch",
            track_id: "relay-track",
            time_ms: null,
            marker_id: "nested-marker",
            marker_offset_ms: 50,
          },
        ],
      },
    },
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
        scheduling: { safe_points_ms: [], protected_intervals: [] },
      },
    ],
  };
}
