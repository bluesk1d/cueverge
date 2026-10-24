import { expect, test } from "@playwright/test";
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
    page.getByAltText("Timeline thumbnails for Window loop"),
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
