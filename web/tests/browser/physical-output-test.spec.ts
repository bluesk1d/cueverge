import { expect, test } from "@playwright/test";
import type { PhysicalOutputTest } from "../../src/api";

for (const width of [1440, 390]) {
  test(`configured output can be explicitly tested and stopped at width ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    let mode = "automatic";
    let current: PhysicalOutputTest | null = null;
    let startBody: unknown = null;
    const mutations: string[] = [];
    await page.route("**/api/gui/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (route.request().method() !== "GET") mutations.push(path);
      if (path.endsWith("/operate/actions")) {
        mode = route.request().postDataJSON().action;
        return route.fulfill({ json: { mode } });
      }
      if (path.endsWith("/maintenance/output-tests")) {
        if (route.request().method() === "POST") {
          startBody = route.request().postDataJSON();
          current = {
            id: "physical-test", arm_id: "physical-arm", resource: "roofline.demo",
            pixel_count: 800, speed: 30, state: "active", pixel_index: 0,
            frames_sent: 1, started_at: new Date().toISOString(),
            expires_at: new Date(Date.now() + 30000).toISOString(),
            error: null, stop_reason: null, simulation_only: false,
            segments: [{ name: "Lower roof", start: 0, end: 100, color: [90, 216, 242] }],
          };
          return route.fulfill({ status: 201, json: current });
        }
        return route.fulfill({ json: {
          mode, playback_active: false, test: current,
          outputs: [{ resource: "roofline.demo", kind: "pixel_output", target: "wled", pixel_count: 800 }],
        } });
      }
      if (path.endsWith("/maintenance/output-tests/physical-test/stop") && current) {
        current = { ...current, state: "stopped", frames_sent: 2, stop_reason: "operator" };
        return route.fulfill({ json: current });
      }
      return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
    });
    await page.goto("/demo.html");
    await page.getByRole("button", { name: "Operate", exact: true }).click();
    await page.getByText("Setup tests and maintenance", { exact: true }).click();
    const controls = page.getByRole("region", { name: "Physical LED output test", exact: true });
    await expect(controls.getByLabel("LED output to test")).toContainText("roofline.demo");
    expect(mutations).toEqual([]);
    await expect(controls.getByRole("button", { name: "Start physical chase" })).toBeDisabled();
    await controls.getByRole("button", { name: "Enter maintenance mode" }).click();
    await controls.getByLabel("Confirm physical output name").fill("wrong");
    await expect(controls.getByRole("button", { name: "Start physical chase" })).toBeDisabled();
    await controls.getByLabel("Confirm physical output name").fill("roofline.demo");
    await controls.getByRole("button", { name: "Start physical chase" }).click();
    expect(startBody).toEqual({
      resource: "roofline.demo", confirmation: "roofline.demo",
      duration_seconds: 30, speed: 30, use_mapping: true, installation_id: "demo-installation",
    });
    await expect(controls.getByText("roofline.demo: active · pixel 0 / 799")).toBeVisible();
    await expect(controls.getByRole("button", { name: "Start physical chase" })).toBeDisabled();
    await controls.getByRole("button", { name: "Stop physical chase" }).click();
    await expect(controls.getByText("roofline.demo: stopped · pixel 0 / 799")).toBeVisible();
    expect(mutations).toEqual([
      "/api/gui/v1/operate/actions",
      "/api/gui/v1/maintenance/output-tests",
      "/api/gui/v1/maintenance/output-tests/physical-test/stop",
    ]);
    expect(await controls.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  });
}
