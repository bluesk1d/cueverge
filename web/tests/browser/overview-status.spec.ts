import { expect, Page, test } from "@playwright/test";

import {
  demoInstallation, demoInstallations, demoRuntime, demoSession, demoTopology,
} from "../../src/demo/overview-fixtures";
import type { InstallationPhoto } from "../../src/api";
import { installationPhotoUrl } from "../../src/api";

async function overviewRoutes(page: Page) {
  const state = { failed: false, expired: false, playing: false, photoFailed: false, runtimeReads: 0, mutations: 0 };
  const photo: InstallationPhoto = {
    id: "house", installation_id: "demo-installation", name: "Front yard", viewpoint: "Street",
    original_filename: "house.png", media_type: "image/png", digest_sha256: "a".repeat(64),
    size_bytes: 100, orientation_degrees: 0, crop_x: null, crop_y: null, crop_width: null,
    crop_height: null, scale_hint: null, revision: 1, created_at: "2026-10-22T12:00:00Z",
    updated_at: "2026-10-22T12:00:00Z",
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
      state.runtimeReads += 1;
      if (state.expired) return route.fulfill({ status: 401, json: { detail: "session expired" } });
      return state.failed ? route.fulfill({ status: 503, json: { detail: "offline" } })
        : route.fulfill({ json: {
          ...demoRuntime,
          observed_at: new Date().toISOString(),
          status: {
            ...demoRuntime.status, degraded: false, degraded_reason: null,
            active_scene: state.playing ? "running-scene" : null,
            playback_state: state.playing ? "playing" : "stopped",
          },
        } });
    }
    if (path.endsWith("/topology")) return route.fulfill({ json: demoTopology });
    if (path.endsWith("/installations/demo-installation/photos")) {
      return state.photoFailed ? route.fulfill({ status: 403, json: { detail: "not permitted" } })
        : route.fulfill({ json: [photo] });
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

for (const viewport of [
  { name: "desktop", width: 1440, height: 900 },
  { name: "tablet", width: 820, height: 1180 },
  { name: "phone", width: 390, height: 844 },
]) {
  test(`photo-led Overview and account disclosure work at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const state = await overviewRoutes(page);
    await page.goto("/");
    const summary = page.getByRole("region", { name: "Installation summary" });
    await expect(summary.getByRole("img", { name: "Front yard · Street" })).toBeVisible();
    await expect(summary.getByText("Stopped", { exact: true })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Installation", exact: true })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Active Show", exact: true })).toHaveCount(0);
    const account = page.getByRole("button", { name: "Account: visual-review", exact: true });
    await account.focus();
    await page.keyboard.press("Enter");
    const details = page.getByRole("region", { name: "Account details", exact: true });
    await expect(details).toBeVisible();
    await expect(details.getByText("Administrator", { exact: true })).toBeVisible();
    await page.keyboard.press("Tab");
    await expect(details.getByRole("button", { name: "Sign out", exact: true })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(account).toBeFocused();
    await expect(details).toHaveCount(0);
    await page.getByRole("button", { name: "Open shows", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Shows", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Overview", exact: true }).click();
    await page.getByText("System details", { exact: true }).click();
    await expect(page.getByText("Show identity unavailable", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    expect(state.mutations).toBe(0);
  });
}

test("polling updates playback and recovers from failure without reloading the page", async ({ page }) => {
  const state = await overviewRoutes(page);
  await page.goto("/");
  const summary = page.getByRole("region", { name: "Installation summary" });
  await expect(summary.getByText("Stopped", { exact: true })).toBeVisible();
  state.playing = true;
  await expect(summary.getByText("Playing", { exact: true })).toBeVisible();
  expect(state.runtimeReads).toBeGreaterThanOrEqual(2);
  state.failed = true;
  await expect(page.locator(".snapshot-notice")).toContainText("Status unavailable");
  await expect(summary.getByText("Last known: Playing", { exact: true })).toBeVisible();
  await expect(page.locator(".workspace")).toHaveClass(/workspace--last-known/);
  state.failed = false;
  await page.getByRole("button", { name: "Refresh status", exact: true }).click();
  await expect(summary.getByText("Playing", { exact: true })).toBeVisible();
  await expect(page.locator(".snapshot-notice")).toHaveCount(0);
  expect(state.mutations).toBe(0);
});

test("hidden snapshots become stale at ten seconds and refresh when visible", async ({ page }) => {
  await page.clock.install();
  const state = await overviewRoutes(page);
  await page.goto("/");
  const summary = page.getByRole("region", { name: "Installation summary" });
  await expect(summary.getByText("Stopped", { exact: true })).toBeVisible();
  const initialReads = state.runtimeReads;
  await page.evaluate(() => Object.defineProperty(document, "visibilityState", {
    configurable: true, get: () => "hidden",
  }));
  await page.clock.fastForward(10001);
  await expect(page.locator(".snapshot-notice")).toContainText("Status out of date");
  await expect(summary.getByText("Last known: Stopped", { exact: true })).toBeVisible();
  expect(state.runtimeReads).toBe(initialReads);
  await page.getByRole("button", { name: "Operate", exact: true }).click();
  await expect(page.getByRole("button", { name: "Start show", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Stop", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Blackout", exact: true })).toBeEnabled();
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(page.locator(".snapshot-notice")).toHaveCount(0);
});

test("an inaccessible photo is an explicit error, not a misleading empty installation", async ({ page }) => {
  const state = await overviewRoutes(page);
  state.photoFailed = true;
  await page.goto("/");
  await expect(page.getByText("Photo unavailable", { exact: true })).toBeVisible();
  await expect(page.getByText("Add a photo of your setup", { exact: true })).toHaveCount(0);
  state.photoFailed = false;
  await page.getByRole("button", { name: "Refresh status", exact: true }).click();
  await expect(page.getByRole("img", { name: "Front yard · Street" })).toBeVisible();
});

test("expired sessions stop background status requests without silently discarding the workspace", async ({ page }) => {
  await page.clock.install();
  const state = await overviewRoutes(page);
  await page.goto("/");
  await expect(page.getByRole("region", { name: "Installation summary" }).getByText("Stopped", { exact: true })).toBeVisible();
  state.expired = true;
  await page.clock.fastForward(2001);
  await expect(page.getByText("Your session has expired", { exact: true })).toBeVisible();
  const reads = state.runtimeReads;
  await page.clock.fastForward(6000);
  expect(state.runtimeReads).toBe(reads);
  await expect(page.getByRole("heading", { name: "Installation Overview" })).toBeVisible();
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "Sign in again", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Installation Overview" })).toBeVisible();
  state.expired = false;
  await page.getByRole("button", { name: "Refresh status", exact: true }).click();
  await expect(page.getByText("Your session has expired", { exact: true })).toHaveCount(0);
});
