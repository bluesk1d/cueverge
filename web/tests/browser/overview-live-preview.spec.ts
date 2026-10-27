import { expect, Page, test } from "@playwright/test";

import {
  demoInstallation, demoInstallations, demoRuntime, demoSession, demoTopology,
} from "../../src/demo/overview-fixtures";
import type { InstallationGeometry, InstallationPhoto } from "../../src/api";
import { installationPhotoUrl } from "../../src/api";

const generation = "loaded-generation";

async function liveRoutes(page: Page) {
  const state = {
    playing: true, monitorFailed: false, monitorReads: [] as number[], active: 0, maxActive: 0,
    mutations: 0, mappingReads: 0,
  };
  const photo: InstallationPhoto = {
    id: "house", installation_id: "demo-installation", name: "Front yard", viewpoint: "Street",
    original_filename: "house.svg", media_type: "image/svg+xml", digest_sha256: "a".repeat(64),
    size_bytes: 100, orientation_degrees: 0, crop_x: null, crop_y: null, crop_width: null,
    crop_height: null, scale_hint: null, revision: 1, created_at: "2026-10-22T12:00:00Z",
    updated_at: "2026-10-22T12:00:00Z",
  };
  const segment: InstallationGeometry = {
    id: "roof-segment", installation_id: "demo-installation", photo_id: "house", kind: "pixel_segment",
    name: "Roofline", semantic_id: "roofline", landmark_ids: [], color: "#fff", label_visible: true,
    locked: false, hidden: false, z_index: 0, direction: "forward", start_index: 0, pixel_count: 12,
    stage_points: null, points: [{ x: 0.2, y: 0.3 }, { x: 0.8, y: 0.3 }], closed: false, revision: 1,
    created_at: "2026-10-22T12:00:00Z", updated_at: "2026-10-22T12:00:00Z",
  };
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== "GET") state.mutations += 1;
    if (path.endsWith("/auth/session")) return route.fulfill({ json: demoSession });
    if (path.endsWith("/installation")) return route.fulfill({ json: demoInstallation });
    if (path.endsWith("/installations")) return route.fulfill({ json: demoInstallations });
    if (path.endsWith("/resource-assignments") || path.endsWith("/activity") || path.endsWith("/projects")) {
      return route.fulfill({ json: [] });
    }
    if (path.endsWith("/runtime/status")) {
      return route.fulfill({ json: {
        ...demoRuntime,
        observed_at: new Date().toISOString(),
        loaded_catalog: {
          state: "loaded", project_id: "halloween", project_name: "Halloween2026", revision: 57,
          digest_sha256: "b".repeat(64), loaded_at: "2026-10-27T00:58:16Z", generation,
        },
        status: {
          ...demoRuntime.status, degraded: false, degraded_reason: null, media_outputs: [],
          active_scene: state.playing ? "scene-cc28de60" : null,
          playback_state: state.playing ? "playing" : "stopped",
          lighting_outputs: state.playing ? demoRuntime.status.lighting_outputs : [],
          operations: {
            ...demoRuntime.status.operations,
            scene_options: [{ id: "scene-cc28de60", name: "Ship ambient external", ready: true }],
          },
        },
      } });
    }
    if (path.endsWith("/topology")) return route.fulfill({ json: demoTopology });
    if (path.endsWith("/installations/demo-installation/photos")) return route.fulfill({ json: [photo] });
    if (path.endsWith("/installations/demo-installation/geometry")) {
      state.mappingReads += 1;
      return route.fulfill({ json: [segment] });
    }
    if (path.endsWith("/installation-geometry/roof-segment/lighting-surface")) {
      return route.fulfill({ json: { geometry_id: "roof-segment", resource: "roofline.demo" } });
    }
    if (path.endsWith("/runtime/output-monitor")) {
      state.monitorReads.push(Date.now());
      state.active += 1; state.maxActive = Math.max(state.maxActive, state.active);
      await new Promise((resolve) => setTimeout(resolve, 100));
      state.active -= 1;
      if (state.monitorFailed) return route.fulfill({ status: 503, json: { detail: "monitor offline" } });
      return route.fulfill({ json: {
        observed_at: new Date().toISOString(), source: "successful_controller_sink_frames",
        loaded_catalog: { state: "loaded", project_id: "halloween", revision: 57, digest_sha256: "b".repeat(64),
          loaded_at: "2026-10-27T00:58:16Z", generation },
        sample_interval_ms: 200, maximum_resources: 32, maximum_samples: 4096, truncated_resources: 0,
        hardware_commands_sent: false,
        outputs: [{
          output_id: "roofline", resource: "roofline.demo", pixel_count: 800, pixel_offset: 0,
          output_position_ms: 1200, next_pixel_offset: null, limitations: [],
          colors: Array.from({ length: 12 }, (_, index) => index % 2 ? [0, 0, 0] : [255, 80, 0]),
          source: "scene", scene_instance_id: "fixture-scene", age_ms: 20, stale: false, held: false, error: null,
        }],
      } });
    }
    if (path === installationPhotoUrl("house")) {
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><rect width="320" height="180" fill="#243447"/><path d="M40 90L160 20L280 90V160H40Z" fill="#61758b"/></svg>',
      });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });
  return state;
}

test("Overview names the playing show and scene and overlays live LEDs while playing", async ({ page }) => {
  const state = await liveRoutes(page);
  await page.goto("/");
  const summary = page.getByRole("region", { name: "Installation summary" });
  await expect(summary.getByText("Halloween2026 · Ship ambient external", { exact: true })).toBeVisible();
  await expect(summary.getByText(/scene-cc28de60/)).toHaveCount(0);

  const overlay = summary.getByRole("img", { name: "Live LED colors on installation photo" });
  await expect(overlay).toBeVisible();
  // Every other sampled LED is dark and is not drawn.
  await expect(overlay.locator("circle")).toHaveCount(6);
  await expect(summary.getByText("Live · last sent LED colors, not a camera feed")).toBeVisible();
  const photoBox = (await summary.getByRole("img", { name: "Front yard · Street" }).boundingBox())!;
  const overlayBox = (await overlay.boundingBox())!;
  expect(overlayBox).toEqual(photoBox);
  // The first LED sits at x=0.2 of the frame; this 16:9 photo spans the full frame width.
  const first = (await overlay.locator("circle").first().boundingBox())!;
  const viewBox = (await overlay.getAttribute("viewBox"))!.split(" ").map(Number);
  const expectedX = photoBox.x + ((200 - viewBox[0]) / viewBox[2]) * photoBox.width;
  expect(Math.abs(first.x + first.width / 2 - expectedX)).toBeLessThan(2);

  await page.waitForTimeout(1600);
  expect(state.maxActive).toBe(1);
  expect(state.monitorReads.length).toBeLessThanOrEqual(5);
  for (let index = 1; index < state.monitorReads.length; index += 1) {
    expect(state.monitorReads[index] - state.monitorReads[index - 1]).toBeGreaterThanOrEqual(490);
  }

  await summary.getByRole("button", { name: "Pause live preview" }).click();
  await expect(overlay).toHaveCount(0);
  await expect(summary.getByText("Live preview paused")).toBeVisible();
  await page.waitForTimeout(300);
  const paused = state.monitorReads.length;
  await page.waitForTimeout(900);
  expect(state.monitorReads).toHaveLength(paused);
  await summary.getByRole("button", { name: "Resume live preview" }).click();
  await expect(overlay).toBeVisible();

  state.playing = false;
  await expect(summary.getByText("Stopped", { exact: true })).toBeVisible();
  await expect(overlay).toHaveCount(0);
  await expect(summary.getByRole("button", { name: /live preview/ })).toHaveCount(0);
  await page.waitForTimeout(300);
  const stopped = state.monitorReads.length;
  await page.waitForTimeout(900);
  expect(state.monitorReads).toHaveLength(stopped);
  expect(state.mutations).toBe(0);
});

test("a failed live preview falls back to the plain photo and recovers on its own", async ({ page }) => {
  const state = await liveRoutes(page);
  state.monitorFailed = true;
  await page.goto("/");
  const summary = page.getByRole("region", { name: "Installation summary" });
  await expect(summary.getByText("Live preview unavailable · showing the photo only")).toBeVisible();
  await expect(summary.getByRole("button", { name: "Retry live preview" })).toBeVisible();
  await expect(summary.getByRole("img", { name: "Front yard · Street" })).toBeVisible();
  await expect(summary.getByRole("img", { name: "Live LED colors on installation photo" })).toHaveCount(0);
  state.monitorFailed = false;
  await expect(summary.getByRole("img", { name: "Live LED colors on installation photo" })).toBeVisible();
  await page.getByText("System details", { exact: true }).click();
  await expect(page.getByText("Halloween2026 · version 57", { exact: true })).toBeVisible();
  await expect(page.getByText("scene-cc28de60", { exact: true })).toBeVisible();
});
