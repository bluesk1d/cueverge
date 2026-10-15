import { expect, test } from "@playwright/test";

let savedDraft: Record<string, unknown> | null = null;

test("creator authors a complete multi-output scene without JSON edits", async ({
  page,
}) => {
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/projects/demo-project/assets")) {
      return route.fulfill({ json: assets() });
    }
    if (url.pathname.endsWith("/projects/demo-project/draft")) {
      if (request.method() === "PUT") {
        savedDraft = request.postDataJSON();
        return route.fulfill({ json: response(2, savedDraft) });
      }
      return route.fulfill({ json: response(1, draft()) });
    }
    if (url.pathname.includes("/artifacts/")) {
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="48"><rect width="320" height="48" fill="#38bdf8"/></svg>',
      });
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
  await page.getByRole("button", { name: "Add marker" }).click();
  await page.getByLabel("New sync marker name").fill("Impact");
  await page.getByRole("button", { name: "Add sync marker" }).click();

  await addTrack(page, "audio_reference", "Narration", "audio-asset");
  await addTrack(page, "fixture", "Window fixture", "fixture.window");
  await addTrack(page, "path", "Roof sweep", "path.roofline");
  await expect(
    page.getByAltText("Timeline waveform for Narration reference"),
  ).toBeVisible();

  await addLightingCue(page, "Baseline", "roofline", "0");
  await addLightingCue(page, "Window pulse A", "Window fixture", "0");
  await addLightingCue(page, "Window pulse B", "Window fixture", "100");
  await addLightingCue(page, "Roof path sweep", "Roof sweep", "0");

  const pulseA = page.locator(".timeline-sync-cue-row").filter({
    hasText: "Window pulse A",
  });
  const pulseB = page.locator(".timeline-sync-cue-row").filter({
    hasText: "Window pulse B",
  });
  const pathSweep = page.locator(".timeline-sync-cue-row").filter({
    hasText: "Roof path sweep",
  });
  await pulseA
    .getByLabel("Lighting cue primitive Window pulse A")
    .selectOption("pulse");
  await pulseB
    .getByLabel("Lighting cue primitive Window pulse B")
    .selectOption("pulse");
  await pathSweep
    .getByLabel("Lighting cue primitive Roof path sweep")
    .selectOption("path_sweep");

  await expect(page.getByLabel("Timeline validation")).toContainText(
    "Timeline valid",
  );
  await page.getByRole("button", { name: "Save timeline" }).click();
  await expect.poll(() => savedDraft).not.toBeNull();

  const saved = savedDraft as ReturnType<typeof draft>;
  expect(saved.scenes[0].scheduling.safe_points_ms).toEqual([1000]);
  expect(saved.editor_metadata.timeline_markers.ambient[0]).toMatchObject({
    name: "Impact",
    time_ms: 1000,
  });
  expect(saved.editor_metadata.timeline_tracks.ambient).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        kind: "audio_reference",
        resource_binding: "audio-asset",
      }),
      expect.objectContaining({
        kind: "fixture",
        resource_binding: "fixture.window",
      }),
      expect.objectContaining({
        kind: "path",
        resource_binding: "path.roofline",
      }),
    ]),
  );
  const cues = saved.editor_metadata.timeline_cues.ambient;
  expect(cues).toHaveLength(4);
  expect(cues).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        name: "Baseline",
        marker_offset_ms: 0,
        lighting: expect.objectContaining({ primitive: "solid", target: null }),
      }),
      expect.objectContaining({
        name: "Window pulse A",
        marker_offset_ms: 0,
        lighting: expect.objectContaining({
          primitive: "pulse",
          target: { kind: "fixture", id: "fixture.window" },
        }),
      }),
      expect.objectContaining({
        name: "Window pulse B",
        marker_offset_ms: 100,
        lighting: expect.objectContaining({
          primitive: "pulse",
          target: { kind: "fixture", id: "fixture.window" },
        }),
      }),
      expect.objectContaining({
        name: "Roof path sweep",
        lighting: expect.objectContaining({
          primitive: "path_sweep",
          target: { kind: "path", id: "path.roofline" },
        }),
      }),
    ]),
  );
});

async function addTrack(
  page: import("@playwright/test").Page,
  kind: "audio_reference" | "fixture" | "path",
  name: string,
  binding: string,
) {
  await page.getByLabel("New track kind").selectOption(kind);
  await page.getByLabel("New track name").fill(name);
  await page.getByLabel("New track resource binding").fill(binding);
  await page.getByRole("button", { name: "Add track" }).click();
}

async function addLightingCue(
  page: import("@playwright/test").Page,
  name: string,
  trackLabel: string,
  offset: string,
) {
  await page.getByLabel("New synchronized cue name").fill(name);
  await page.getByLabel("New synchronized cue kind").selectOption("lighting");
  await page
    .getByLabel("New synchronized cue track")
    .selectOption({ label: trackLabel });
  await page.getByLabel("New synchronized cue marker offset").fill(offset);
  await page.getByRole("button", { name: "Add synchronized cue" }).click();
}

function response(revision: number, document: Record<string, unknown>) {
  return {
    project: {
      id: "demo-project",
      name: "Home Show",
      head_revision: revision,
      metadata_revision: 1,
      archived_at: null,
      created_at: "2026-10-15T12:00:00Z",
      updated_at: "2026-10-15T12:00:00Z",
    },
    revision,
    draft: document,
  };
}

function draft() {
  return {
    schema_version: 1,
    installation: { schema_version: 1, resources: [] },
    editor_metadata: {},
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
            asset: "video-asset",
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
            timing_offset_ms: 0,
          },
        ],
        scheduling: { safe_points_ms: [], protected_intervals: [] },
      },
    ],
  };
}

function assets() {
  return [
    asset("video-asset", "Window loop", "video/mp4", {
      thumbnail: { media_type: "image/jpeg", size_bytes: 256 },
      waveform: { media_type: "image/png", size_bytes: 128 },
    }),
    asset("audio-asset", "Narration reference", "audio/wav", {
      waveform: { media_type: "image/png", size_bytes: 128 },
    }),
  ];
}

function asset(
  id: string,
  name: string,
  mediaType: string,
  artifacts: Record<string, { media_type: string; size_bytes: number }>,
) {
  return {
    id,
    project_id: "demo-project",
    name,
    status: "ready",
    current_revision: 1,
    metadata_revision: 1,
    owner: null,
    license: null,
    provenance: null,
    review_status: "approved",
    expires_on: null,
    folder: null,
    tags: [],
    favorite: false,
    error: null,
    digest_sha256: "a".repeat(64),
    size_bytes: 1024,
    media_type: mediaType,
    original_filename: `${id}.media`,
    scan_status: "clean",
    metadata: { artifacts },
    original_file_status: "available",
    artifact_faults: [],
    references: [],
    unused: false,
    created_at: "2026-10-15T12:00:00Z",
    updated_at: "2026-10-15T12:00:00Z",
  };
}
