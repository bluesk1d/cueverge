import { expect, test } from "@playwright/test";

const installationId = "demo-installation";
const geometryId = "projector-surface";
const photoIds = ["wide", "left-close", "right-close"];
let savedDraft: Record<string, unknown> | null = null;
let renderJobs: ReturnType<typeof renderJob>[] = [renderJob("completed")];

test("projector-native edit returns to root and maps through every photo view", async ({
  page,
}) => {
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/projects/demo-project/assets")) {
      return route.fulfill({ json: [] });
    }
    if (url.pathname.endsWith("/projects/demo-project/render-jobs")) {
      if (request.method() === "POST") {
        const job = renderJob("queued");
        renderJobs = [job];
        return route.fulfill({ status: 202, json: job });
      }
      return route.fulfill({ json: renderJobs });
    }
    if (url.pathname.endsWith("/projects/demo-project/preview-evaluation")) {
      return route.fulfill({
        json: {
          project_id: "demo-project",
          source_revision: 1,
          space_id: url.searchParams.get("space_id"),
          space_kind: "projector_native",
          timestamp_ms: Number(url.searchParams.get("timestamp_ms")),
          duration_ms: 5000,
          frame_rate: { numerator: 30, denominator: 1 },
          frame_index: 0,
          frame_timestamp_ms: 0,
          timing_error_ms: 0,
          resources: ["projector.left"],
          degraded_resources: [],
          evaluation: "backend_deterministic",
          hardware_command_sent: false,
        },
      });
    }
    if (
      url.pathname.endsWith("/render-jobs/render-job-1/cancel") &&
      request.method() === "POST"
    ) {
      const job = renderJob("canceled");
      renderJobs = [job];
      return route.fulfill({ json: job });
    }
    if (url.pathname.endsWith("/projects/demo-project/draft")) {
      if (request.method() === "PUT") {
        savedDraft = request.postDataJSON();
      }
      return route.fulfill({ json: draftResponse(savedDraft) });
    }
    if (url.pathname.endsWith("/compositions/projector-composition/preview-frame")) {
      return route.fulfill({
        contentType: "image/png",
        body: Buffer.from(
          "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X4R7WQAAAABJRU5ErkJggg==",
          "base64",
        ),
      });
    }
    if (url.pathname.endsWith("/render-artifacts/artifact-1/preview-frame")) {
      return route.fulfill({
        contentType: "image/png",
        body: Buffer.from(
          "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X4R7WQAAAABJRU5ErkJggg==",
          "base64",
        ),
      });
    }
    if (url.pathname.endsWith(`/${installationId}/photos`)) {
      return route.fulfill({ json: photoIds.map(photo) });
    }
    if (url.pathname.endsWith(`/${installationId}/geometry`)) {
      return route.fulfill({ json: [surface()] });
    }
    if (url.pathname.endsWith(`/${geometryId}/projector-mapping`)) {
      return route.fulfill({ json: mapping() });
    }
    if (url.pathname.startsWith("/api/gui/v1/installation-photos/")) {
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600"><rect width="1000" height="600" fill="#18202b"/></svg>',
      });
    }
    if (
      url.pathname.endsWith("/projector-overlaps") ||
      url.pathname.endsWith("/geometry-suggestions") ||
      url.pathname.endsWith("/assets/image-library") ||
      url.pathname.endsWith("/discovery/endpoints")
    ) {
      return route.fulfill({ json: [] });
    }
    if (url.pathname.endsWith("/stage-coverage")) {
      return route.fulfill({ json: emptyCoverage() });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByText("Timeline editor", { exact: true }).click();
  await page.getByLabel("Editing space").selectOption("projector-composition");
  await expect(
    page.getByAltText("Controller render preview for Left projector"),
  ).toHaveAttribute(
    "src",
    "/api/gui/v1/projects/demo-project/compositions/projector-composition/preview-frame?timestamp_ms=0",
  );
  await expect(
    page.getByLabel("Render pipeline order").locator("li"),
  ).toHaveText([
    "Decode and conform sources",
    "Logical transforms, masks, and alpha composition",
    "Physical-object mapping and occlusion",
    "Projector-native warp, crop, and orientation",
    "Overlap and edge blending",
    "Projector brightness, black level, and color correction",
    "Audio mix, encode, and mux",
  ]);
  await expect(page.getByLabel("Backend preview diagnostics")).toContainText(
    "Backend deterministic scene evaluation",
  );
  await expect(page.getByLabel("Backend preview diagnostics")).toContainText(
    "Degraded resources: none",
  );
  await expect(page.getByLabel("Checksummed bake")).toHaveValue("artifact-1");
  await page.getByLabel("Preview inspection mode").selectOption("side_by_side");
  await expect(
    page.getByLabel(/before and after/).getByRole("img"),
  ).toHaveCount(2);
  await page.getByLabel("Preview inspection mode").selectOption("difference");
  await expect(page.getByLabel(/frame difference/)).toBeVisible();
  await page.getByLabel("Preview speed").selectOption("2");
  await page.getByRole("button", { name: "Next preview frame" }).click();
  await expect(page.getByLabel("Scrub position")).not.toHaveValue("0");

  await expect(
    page.getByText(
      /Authored once in projector native · target projector.left · 1920×1080/,
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: "Stage composition overlay" }),
  ).toHaveCount(0);
  await page.getByLabel("Position 1").fill("960");
  await page.getByLabel("Position 2").fill("540");
  await page.getByLabel("Render frame-rate numerator").fill("24");
  await page.getByLabel("Render frame-rate denominator").fill("1");
  await page.getByLabel("Render scaling filter").selectOption("bicubic");
  await page.getByLabel("Final output background").fill("#112233");
  await page.getByLabel("Client output preset").selectOption("legacy_h264");
  await page.getByLabel("Maximum video bitrate").fill("3500");
  await page.getByLabel("Loudness target").fill("-18");
  await page.getByLabel("Keyframe interval").fill("48");
  await page.getByRole("button", { name: "Start controller render" }).click();
  await expect(page.locator(".timeline-render-job")).toContainText("queued");
  await page.getByRole("button", { name: "Cancel render" }).click();
  await expect(page.locator(".timeline-render-job")).toContainText("canceled");
  await page.getByRole("button", { name: "Save timeline" }).click();
  await expect.poll(() => savedDraft).not.toBeNull();
  expect(
    (
      savedDraft as {
        editor_metadata: {
          compositions: Record<string, { render_settings: Record<string, unknown> }>;
        };
      }
    ).editor_metadata.compositions["projector-composition"].render_settings,
  ).toMatchObject({
    render_pipeline_revision: 1,
    frame_rate_numerator: 24,
    frame_rate_denominator: 1,
    working_color_space: "linear_srgb",
    working_alpha_mode: "premultiplied",
    scaling_filter: "bicubic",
    output_background_color: "#112233",
    output_profile: {
      preset_id: "legacy_h264",
      container: "mp4",
      video_codec: "h264",
      video_profile: "baseline",
      video_level: "3.1",
      pixel_format: "yuv420p",
      max_video_bitrate_kbps: 3500,
      audio_codec: "aac",
      audio_sample_rate: 48000,
      audio_channel_layout: "stereo",
      loudness_target_lufs: -18,
      keyframe_interval_frames: 48,
      b_frames: 0,
      hardware_decoder_compatibility: "h264_baseline_level_3_1_yuv420p",
    },
  });

  await page
    .getByRole("navigation", { name: "Composition breadcrumbs" })
    .getByRole("button", { name: "Scene · Ambient" })
    .click();
  await expect(page.getByLabel("Editing space")).toHaveValue("");
  await expect(page.getByText(/Authored once in scene root · ambient/)).toBeVisible();

  await page.getByRole("button", { name: "Installation", exact: true }).click();
  const mapper = page
    .getByRole("heading", { name: "Visual Mapper" })
    .locator("xpath=ancestor::section");
  await mapper.getByRole("button", { name: "Edit" }).click();
  for (const photoId of photoIds) {
    await expect(mapper.getByText(new RegExp(`Photo view ${photoId}:`))).toBeVisible();
  }
});

test("installation overlay saves LED selection and queues one mixed bake", async ({ page }) => {
  let saved: Record<string, unknown> | null = null;
  const document = draft();
  const composition = document.editor_metadata.compositions["projector-composition"];
  composition.space_kind = "stage";
  composition.name = "House overlay";
  composition.width = 8;
  composition.height = 4;
  let bakeRequests = 0;
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname.endsWith("/projects/demo-project/draft")) {
      if (request.method() === "PUT") saved = request.postDataJSON();
      return route.fulfill({ json: {
        ...draftResponse(saved), draft: saved ?? document,
      } });
    }
    if (pathname.endsWith("/mapper-export")) {
      return route.fulfill({ json: {
        resources: [],
        fixtures: [], paths: [], zones: [],
        projectors: [{
          resource: "projector.left", geometry_id: geometryId,
          native_width: 64, native_height: 36, stage_extent: [0, 0, 8, 4],
          photo_instances: [], mesh_vertices: [], mesh_triangles: [],
        }],
        lighting_surfaces: [{
          semantic_id: "roofline", resource: "wled-001",
          local_width: 8, local_height: 4, stage_matrix: [1, 0, 0, 1, 0, 0],
          pixels: [], local_points: [],
        }],
      } });
    }
    if (pathname.endsWith("/stage-coverage")) {
      return route.fulfill({ json: emptyCoverage() });
    }
    if (pathname.endsWith("/render-jobs")) {
      if (request.method() === "POST") {
        bakeRequests += 1;
        expect(request.postDataJSON()).toEqual({ composition_id: "projector-composition" });
        return route.fulfill({ status: 202, json: renderJob("queued") });
      }
      return route.fulfill({ json: [] });
    }
    if (pathname.endsWith("/stage-lighting-preview")) {
      return route.fulfill({ json: {
        composition_id: "projector-composition", timestamp_ms: 0,
        source: "shared_stage_compositor", surfaces: [], hardware_command_sent: false,
      } });
    }
    if (pathname.endsWith("/photos") || pathname.endsWith("/geometry") || pathname.endsWith("/assets")) {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });
  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByText("Timeline editor", { exact: true }).click();
  await page.getByLabel("Editing space").selectOption("projector-composition");
  await expect(page.getByLabel("Include LED surface roofline")).not.toBeChecked();
  await page.getByLabel("Include LED surface roofline").check();
  await expect(page.getByRole("button", { name: "Bake installation overlay" })).toBeEnabled();
  await expect(
    page.getByRole("group", { name: "Installation overlay LED outputs" }).getByRole("status"),
  ).toContainText("client-group activation alone");
  await page.getByRole("button", { name: "Save timeline" }).click();
  await expect.poll(() => saved).not.toBeNull();
  expect((saved as {
    editor_metadata: { compositions: Record<string, { stage_lighting_surfaces: string[] }> };
  }).editor_metadata.compositions["projector-composition"].stage_lighting_surfaces).toEqual(["roofline"]);
  await page.getByRole("button", { name: "Bake installation overlay" }).click();
  await expect.poll(() => bakeRequests).toBe(1);
});

function draftResponse(saved: Record<string, unknown> | null) {
  return {
    project: {
      id: "demo-project",
      name: "Home Show",
      head_revision: saved ? 2 : 1,
      metadata_revision: 1,
      archived_at: null,
      created_at: "2026-10-15T12:00:00Z",
      updated_at: "2026-10-15T12:00:00Z",
    },
    revision: saved ? 2 : 1,
    draft: saved ?? draft(),
  };
}

function draft() {
  return {
    schema_version: 1,
    installation: { schema_version: 1, resources: [] },
    editor_metadata: {
      installation_id: installationId,
      compositions: {
        "projector-composition": {
          id: "projector-composition",
          name: "Left projector",
          duration_ms: 5000,
          space_kind: "projector_native",
          target_id: "projector.left",
          width: 1920,
          height: 1080,
          local_time_offset_ms: 0,
          children: [],
          safe_points_ms: [],
          protected_intervals: [],
        },
      },
      scene_compositions: { ambient: ["projector-composition"] },
      timeline_tracks: {
        "projector-composition": [
          {
            id: "projector-track",
            kind: "group",
            name: "Projected layers",
            resource_binding: "projector.left",
          },
        ],
      },
      timeline_transform_layers: {
        "projector-composition": [
          {
            id: "ghost-layer",
            name: "Ghost",
            kind: "image",
            source_id: "ghost-image",
            track_id: "projector-track",
            start_ms: 0,
            duration_ms: 5000,
            keyframes: [
              {
                id: "ghost-frame",
                time_ms: 0,
                position: [0, 0],
                scale: [1, 1],
                rotation_deg: 0,
                anchor: [0.5, 0.5],
                opacity: 1,
                crop: [0, 0, 0, 0],
                interpolation: {
                  kind: "linear",
                  temporal_bezier: null,
                  spatial_in: null,
                  spatial_out: null,
                },
              },
            ],
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
            timing_offset_ms: 0,
          },
        ],
      },
    ],
  };
}

function photo(id: string) {
  return {
    id,
    installation_id: installationId,
    name: id,
    viewpoint: id,
    original_filename: `${id}.jpg`,
    media_type: "image/jpeg",
    digest_sha256: id.padEnd(64, "0"),
    size_bytes: 1024,
    orientation_degrees: 0,
    crop_x: null,
    crop_y: null,
    crop_width: null,
    crop_height: null,
    scale_hint: null,
    revision: 1,
    created_at: "2026-10-15T12:00:00Z",
    updated_at: "2026-10-15T12:00:00Z",
  };
}

function surface() {
  return {
    id: geometryId,
    installation_id: installationId,
    photo_id: "wide",
    kind: "projector_surface",
    name: "Left projector",
    semantic_id: "left-projector",
    landmark_ids: ["tl", "tr", "br", "bl"],
    points: [
      { x: 0.1, y: 0.1 },
      { x: 0.9, y: 0.1 },
      { x: 0.9, y: 0.9 },
      { x: 0.1, y: 0.9 },
    ],
    closed: true,
    color: "#6ee7ff",
    label_visible: true,
    locked: false,
    hidden: false,
    z_index: 0,
    direction: "forward",
    start_index: null,
    pixel_count: null,
    revision: 1,
    created_at: "2026-10-15T12:00:00Z",
    updated_at: "2026-10-15T12:00:00Z",
  };
}

function mapping() {
  const homography = [0.001, 0, 0.1, 0, 0.001, 0.1, 0, 0, 1];
  return {
    geometry_id: geometryId,
    resource: "projector.left",
    native_width: 1920,
    native_height: 1080,
    stage_x: 0,
    stage_y: 0,
    stage_width: 5,
    stage_height: 3,
    crop_x: 0,
    crop_y: 0,
    crop_width: 1,
    crop_height: 1,
    content_asset_id: null,
    surface_model: "planar",
    fit_mode: "preserve",
    overscan: false,
    brightness_limit: 1,
    media_start_delay_ms: 0,
    media_release_delay_ms: 0,
    orientation_degrees: 0,
    mirror_x: false,
    mirror_y: false,
    mask_semantic_ids: [],
    homography,
    diagnostic: null,
    fit_diagnostics: [],
    photo_views: photoIds.map((photo_id, index) => ({
      geometry_id: `${geometryId}-${index}`,
      photo_id,
      points: surface().points,
      homography,
    })),
    revision: 1,
    created_at: "2026-10-15T12:00:00Z",
    updated_at: "2026-10-15T12:00:00Z",
  };
}

function emptyCoverage() {
  return {
    installation_id: installationId,
    minimum_x: 0,
    minimum_y: 0,
    maximum_x: 1,
    maximum_y: 1,
    regions: [],
  };
}

function renderJob(
  state: "queued" | "completed" | "canceled",
) {
  return {
    id: "render-job-1",
    project_id: "demo-project",
    source_revision: 1,
    composition_id: "projector-composition",
    cache_key: "a".repeat(64),
    state,
    progress: state === "completed" ? 1 : 0,
    reserved_bytes: 1024,
    error: null,
    artifact_id: state === "completed" ? "artifact-1" : null,
    cancel_requested: state === "canceled",
    created_by: "admin",
    created_at: "2026-10-15T12:00:00Z",
    started_at: null,
    completed_at:
      state === "queued" ? null : "2026-10-15T12:00:01Z",
    updated_at: "2026-10-15T12:00:01Z",
  };
}
