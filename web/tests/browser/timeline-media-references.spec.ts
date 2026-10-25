import { expect, test } from "@playwright/test";
import { demoInstallation, demoInstallations, demoProjects, demoRuntime, demoTopology } from "../../src/demo/overview-fixtures";
import { openAdvancedShowWorkspace, openTimelineEditor } from "./show-workspace";

test("timeline shows media thumbnails and audio waveforms", async ({ page }) => {
  await page.route("**/api/gui/v1/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/projects/demo-project/assets")) {
      return route.fulfill({ json: assets() });
    }
    if (url.pathname.endsWith("/projects/demo-project/draft")) {
      return route.fulfill({ json: draftResponse() });
    }
    if (url.pathname.endsWith("/assets/audio-asset/artifacts/review")) {
      return route.fulfill({
        contentType: "audio/wav",
        body: wavFixture(),
      });
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
  await openTimelineEditor(page);

  await expect(
    page.getByAltText("Head frame of Window loop"),
  ).toHaveAttribute(
    "src",
    "/api/gui/v1/assets/video-asset/artifacts/thumbnail",
  );
  await expect(
    page.getByAltText("Timeline waveform for Window loop"),
  ).toHaveAttribute(
    "src",
    "/api/gui/v1/assets/video-asset/artifacts/waveform",
  );
  await expect(
    page.getByAltText("Timeline waveform for Narration reference"),
  ).toHaveAttribute(
    "src",
    "/api/gui/v1/assets/audio-asset/artifacts/waveform",
  );
  await expect(
    page.getByLabel("Narration timing reference"),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Browser editorial audio" }),
  ).toContainText("do not represent venue output or hardware synchronization");
  await expect(
    page.getByLabel("Editorial audio proxy for Narration"),
  ).toHaveAttribute(
    "src",
    "/api/gui/v1/assets/audio-asset/artifacts/review",
  );
  await expect(
    page.getByAltText("Editorial waveform for Narration reference"),
  ).toBeVisible();
  await expect(page.getByLabel("Narration waveform position")).toBeVisible();
  await expect(page.getByLabel("Narration audio level")).toBeVisible();

  const scrub = page.getByLabel("Scrub position");
  await page.getByRole("button", { name: "Play editorial preview" }).click();
  await expect.poll(async () => Number(await scrub.inputValue())).toBeGreaterThan(50);
  await page.getByRole("button", { name: "Pause editorial preview" }).click();
  const pausedAt = Number(await scrub.inputValue());
  await page.waitForTimeout(150);
  expect(Number(await scrub.inputValue())).toBe(pausedAt);
});

test("scene media clips select editable output properties instead of seeking", async ({ page }) => {
  let current = draftResponse();
  current.draft.scenes[0].media.push({
    id: "right-window", resource: "projector.right", asset: "video-asset",
    required: false, start_delay_ms: 0,
  });
  current.draft.scenes[0].resources.push("projector.right");
  await page.route("**/api/gui/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/projects/demo-project/assets")) {
      return route.fulfill({ json: assets() });
    }
    if (path.endsWith("/projects/demo-project/draft")) {
      if (route.request().method() === "PUT") {
        current = { ...current, revision: current.revision + 1, draft: route.request().postDataJSON() };
      }
      return route.fulfill({ json: current });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });
  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openTimelineEditor(page);
  const scrub = page.getByLabel("Scrub position");
  await scrub.fill("1000");
  const clip = page.getByRole("button", { name: "left-window timing reference", exact: true });
  await clip.click({ position: { x: 180, y: 10 } });
  await expect(scrub).toHaveValue("1000");
  await expect(clip).toHaveAttribute("aria-pressed", "true");
  const inspector = page.getByRole("complementary", { name: "Inspector" });
  await expect(inspector).toContainText("projector.left");
  await expect(inspector.getByLabel("Media asset")).toHaveValue("video-asset");
  await inspector.getByLabel("Media start delay (ms)").fill("500");
  await inspector.getByLabel("Media end behavior").selectOption("loop");
  await expect(inspector.getByLabel("Required media output")).toBeChecked();
  await expect(clip).toHaveAttribute("style", /left: 10%/);
  await page.getByRole("button", { name: "Undo timeline edit" }).click();
  await expect(inspector.getByLabel("Media end behavior")).toHaveValue("hold_last_frame");
  await page.getByRole("button", { name: "Redo timeline edit" }).click();
  await expect(inspector.getByLabel("Media end behavior")).toHaveValue("loop");
  await page.getByRole("button", { name: "Save timeline" }).click();
  await expect.poll(() => current.revision).toBe(2);
  expect(current.draft.scenes[0].media[0]).toMatchObject({
    id: "left-window", asset: "video-asset", resource: "projector.left",
    required: true, start_delay_ms: 500, end_behavior: "loop",
  });
  expect(current.draft.scenes[0].media[1]).toMatchObject({
    id: "right-window", required: false, start_delay_ms: 0,
  });
  expect(current.draft.scenes[0].media[1]).not.toHaveProperty("end_behavior");
  await page.reload();
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openTimelineEditor(page);
  await clip.focus();
  await page.keyboard.press("Enter");
  await expect(inspector.getByLabel("Media start delay (ms)")).toHaveValue("500");
  await expect(inspector.getByLabel("Media end behavior")).toHaveValue("loop");
  const lane = page.getByLabel("Track left-window");
  const bounds = (await lane.boundingBox())!;
  await lane.click({ position: { x: bounds.width * 0.6, y: 5 } });
  await expect(scrub).toHaveValue("3000");
  await page.getByRole("button", { name: "right-window timing reference", exact: true }).focus();
  await page.keyboard.press("Space");
  await expect(inspector).toContainText("projector.right");
  await expect(inspector.getByLabel("Media start delay (ms)")).toHaveValue("0");
  await expect(inspector.getByLabel("Required media output")).not.toBeChecked();
  await inspector.getByLabel("Media asset").selectOption("audio-asset");
  await expect(inspector.getByLabel("Media asset")).toHaveValue("audio-asset");
  await page.getByRole("button", { name: "Save timeline" }).click();
  await expect.poll(() => current.revision).toBe(3);
  expect(current.draft.scenes[0].media[1].asset).toBe("audio-asset");
  expect(current.draft.scenes[0].media[0].asset).toBe("video-asset");
  await inspector.getByLabel("Media start delay (ms)").fill("5001");
  await expect(page.getByLabel("Timeline validation")).toContainText("between 0 and 5000");
  await expect(page.getByRole("button", { name: "Save timeline" })).toBeDisabled();
});

for (const readOnly of [false, true]) {
  test(`legacy media clip supports ${readOnly ? "read-only inspection" : "asset editing"}`, async ({ page }) => {
    const original = draftResponse();
    const legacy = {
      ...original,
      draft: {
        ...original.draft,
        scenes: [{
          schema_version: 1, id: "ambient", name: "Ambient", duration_ms: 5000,
          media: { resource: "projector.left", asset: "video-asset" },
          lighting: { resource: "wled-001", effect: "solid", color: "#ffffff" },
        }],
      },
    };
    let saved: unknown = null;
    await page.route("**/api/gui/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/auth/session") && readOnly) {
        return route.fulfill({ json: { username: "viewer", role: "designer" } });
      }
      if (path.endsWith("/members") && readOnly) return route.fulfill({ json: [] });
      if (path.endsWith("/installation")) return route.fulfill({ json: demoInstallation });
      if (path.endsWith("/installations")) return route.fulfill({ json: demoInstallations });
      if (path.endsWith("/runtime/status")) return route.fulfill({ json: demoRuntime });
      if (path.endsWith("/topology")) return route.fulfill({ json: demoTopology });
      if (path.endsWith("/projects")) return route.fulfill({ json: demoProjects });
      if (path.endsWith("/activity")) return route.fulfill({ json: [] });
      if (path.endsWith("/projects/demo-project/assets")) return route.fulfill({ json: assets() });
      if (path.endsWith("/projects/demo-project/draft")) {
        if (route.request().method() === "PUT") saved = route.request().postDataJSON();
        return route.fulfill({ json: legacy });
      }
      return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
    });
    await page.goto(readOnly ? "/" : "/demo.html");
    await page.getByRole("button", { name: "Shows", exact: true }).click();
    await openTimelineEditor(page);
    await page.getByRole("button", { name: "Media timing reference", exact: true }).click();
    const inspector = page.getByRole("complementary", { name: "Inspector" });
    await expect(inspector).toContainText("projector.left");
    await expect(inspector.getByLabel("Media start delay (ms)")).toHaveCount(0);
    if (readOnly) {
      await expect(inspector.getByLabel("Media asset")).toBeDisabled();
      await expect(page.getByRole("button", { name: "Save timeline" })).toHaveCount(0);
      expect(saved).toBeNull();
    } else {
      await inspector.getByLabel("Media asset").selectOption("audio-asset");
      await page.getByRole("button", { name: "Save timeline" }).click();
      await expect.poll(() => saved).toMatchObject({
        scenes: [{
          schema_version: 1,
          media: { resource: "projector.left", asset: "audio-asset" },
          lighting: legacy.draft.scenes[0].lighting,
        }],
      });
    }
  });
}

function draftResponse() {
  return {
    project: {
      id: "demo-project",
      name: "Home Show",
      head_revision: 1,
      metadata_revision: 1,
      archived_at: null,
      created_at: "2026-10-14T12:00:00Z",
      updated_at: "2026-10-14T12:00:00Z",
    },
    revision: 1,
    draft: {
      schema_version: 1,
      installation: { schema_version: 1, resources: [] },
      editor_metadata: {
        timeline_tracks: {
          ambient: [
            {
              id: "narration-track",
              kind: "audio_reference",
              name: "Narration",
              resource_binding: "audio-asset",
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
            },
          ],
          scheduling: { safe_points_ms: [], protected_intervals: [] },
        },
      ],
    },
  };
}

function assets() {
  return [
    asset("video-asset", "Window loop", "video/mp4", {
      thumbnail: { media_type: "image/jpeg", size_bytes: 256 },
      waveform: { media_type: "image/png", size_bytes: 128 },
    }),
    asset("audio-asset", "Narration reference", "audio/wav", {
      review: { media_type: "audio/mpeg", size_bytes: 512 },
      waveform: { media_type: "image/png", size_bytes: 128 },
    }),
  ];
}

function wavFixture(): Buffer {
  const sampleCount = 800;
  const sampleRate = 8000;
  const output = Buffer.alloc(44 + sampleCount * 2);
  output.write("RIFF", 0);
  output.writeUInt32LE(output.length - 8, 4);
  output.write("WAVEfmt ", 8);
  output.writeUInt32LE(16, 16);
  output.writeUInt16LE(1, 20);
  output.writeUInt16LE(1, 22);
  output.writeUInt32LE(sampleRate, 24);
  output.writeUInt32LE(sampleRate * 2, 28);
  output.writeUInt16LE(2, 32);
  output.writeUInt16LE(16, 34);
  output.write("data", 36);
  output.writeUInt32LE(sampleCount * 2, 40);
  for (let index = 0; index < sampleCount; index += 1) {
    output.writeInt16LE(
      Math.round(6000 * Math.sin((2 * Math.PI * 440 * index) / sampleRate)),
      44 + index * 2,
    );
  }
  return output;
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
    created_at: "2026-10-14T12:00:00Z",
    updated_at: "2026-10-14T12:00:00Z",
  };
}

test("media outputs play a set number of times and can size the scene to fit", async ({ page }) => {
  let current = draftResponse();
  const assetList = assets().map((item) =>
    item.id === "video-asset" ? { ...item, metadata: { ...item.metadata, duration_ms: 1500 } } : item);
  await page.route("**/api/gui/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/projects/demo-project/assets")) return route.fulfill({ json: assetList });
    if (path.endsWith("/projects/demo-project/draft")) {
      if (route.request().method() === "PUT") {
        current = { ...current, revision: current.revision + 1, draft: route.request().postDataJSON() };
      }
      return route.fulfill({ json: current });
    }
    if (path.includes("/artifacts/")) {
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="48"/>',
      });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });
  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openTimelineEditor(page);
  const clip = page.getByRole("button", { name: "left-window timing reference", exact: true });
  await clip.focus();
  await page.keyboard.press("Enter");
  const inspector = page.getByRole("complementary", { name: "Inspector" });
  const plays = inspector.getByLabel("Media plays");
  await expect(plays).toHaveValue("1");
  await plays.fill("3");
  await expect(clip).toContainText("Window loop ×3");
  await expect(inspector).toContainText("3 × 0:01.500 = 0:04.500 · scene is 0:05.000");
  await inspector.getByRole("button", { name: "Match scene length" }).click();
  await expect(inspector).toContainText("scene is 0:04.500");
  await expect(inspector.getByRole("button", { name: "Match scene length" })).toHaveCount(0);
  await page.getByRole("button", { name: "Save timeline" }).click();
  await expect.poll(() => current.revision).toBe(2);
  expect(current.draft.scenes[0].duration_ms).toBe(4500);
  expect(current.draft.scenes[0].media[0]).toMatchObject({ id: "left-window", repeat_count: 3 });

  await clip.focus();
  await page.keyboard.press("Enter");
  await expect(plays).toHaveValue("3");
  await inspector.getByLabel("Media end behavior").selectOption("loop");
  await expect(plays).toBeDisabled();
  await page.getByRole("button", { name: "Save timeline" }).click();
  await expect.poll(() => current.revision).toBe(3);
  expect(current.draft.scenes[0].media[0]).not.toHaveProperty("repeat_count");
  expect(current.draft.scenes[0].media[0].end_behavior).toBe("loop");
});
