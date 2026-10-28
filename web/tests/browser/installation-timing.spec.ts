import AxeBuilder from "@axe-core/playwright";
import { expect, Page, test } from "@playwright/test";

import type { InstallationTiming, TimingCalibrationRun } from "../../src/api";
import { demoInstallation, demoInstallations, demoRuntime, demoTopology } from "../../src/demo/overview-fixtures";

async function timingRoutes(page: Page, role = "operator") {
  await page.context().addCookies([{ name: "cueverge_csrf", value: "timing-csrf", url: "http://127.0.0.1:4173" }]);
  const id = demoInstallation.installation_id;
  const state = {
    timing: {
      installation_id: id,
      outputs: [
        { resource: "projector.left", kind: "media", delay_ms: 0, minimum_ms: 0, maximum_ms: 5000, revision: 0, updated_by: null, updated_at: null },
        { resource: "wled-001", kind: "lighting", delay_ms: 20, minimum_ms: -5000, maximum_ms: 5000, revision: 1, updated_by: "admin", updated_at: null },
      ],
      calibration: null,
    } as InstallationTiming,
    saves: [] as { resource: string; delay_ms: number; ifMatch?: string }[],
    starts: [] as unknown[],
    adjustments: [] as { resource: string; delay_ms: number }[],
    finished: [] as string[],
  };
  const runtime = structuredClone(demoRuntime);
  (runtime.status as Record<string, unknown>).operations = {
    ...((runtime.status as Record<string, unknown>).operations as Record<string, unknown>),
    mode: "maintenance",
  };
  (runtime.status as Record<string, unknown>).active_scene = null;
  const run = (): TimingCalibrationRun => ({
    id: "run", state: "active", period_ms: 2000, flash_ms: 100, cycles: 1, started_at: new Date().toISOString(),
    error: null, stop_reason: null,
    outputs: state.timing.outputs.map((output) => ({
      resource: output.resource, kind: output.kind,
      delay_ms: state.adjustments.filter((item) => item.resource === output.resource).at(-1)?.delay_ms ?? output.delay_ms,
      saved_ms: output.delay_ms, state: output.kind === "media" ? "playing" : "flashing", error: null,
      drift_ms: output.kind === "media" ? 4 : null,
    })),
  });
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== "GET" && request.headers()["x-csrf-token"] !== "timing-csrf") {
      return route.fulfill({ status: 403, json: { detail: { code: "csrf_failed" } } });
    }
    if (path.endsWith("/auth/session")) return route.fulfill({ json: { username: "test", role } });
    if (path.endsWith("/installation")) return route.fulfill({ json: demoInstallation });
    if (path.endsWith("/installations")) return route.fulfill({ json: demoInstallations });
    if (path.endsWith("/runtime/status")) return route.fulfill({ json: runtime });
    if (path.endsWith("/topology")) return route.fulfill({ json: demoTopology });
    if (path.endsWith(`/installations/${id}/output-timing`)) return route.fulfill({ json: state.timing });
    const saved = path.match(/\/output-timing\/([^/]+)$/);
    if (saved && request.method() === "PUT") {
      const resource = decodeURIComponent(saved[1]);
      state.saves.push({ resource, delay_ms: request.postDataJSON().delay_ms, ifMatch: request.headers()["if-match"] });
      state.timing = {
        ...state.timing,
        outputs: state.timing.outputs.map((output) =>
          output.resource === resource
            ? { ...output, delay_ms: request.postDataJSON().delay_ms, revision: output.revision + 1 }
            : output,
        ),
      };
      return route.fulfill({ json: state.timing.outputs.find((output) => output.resource === resource) });
    }
    if (path.endsWith("/timing-calibration") && request.method() === "POST") {
      state.starts.push(request.postDataJSON());
      state.timing = { ...state.timing, calibration: run() };
      return route.fulfill({ status: 201, json: state.timing.calibration });
    }
    const adjusted = path.match(/\/timing-calibration\/outputs\/([^/]+)$/);
    if (adjusted) {
      state.adjustments.push({ resource: decodeURIComponent(adjusted[1]), delay_ms: request.postDataJSON().delay_ms });
      state.timing = { ...state.timing, calibration: run() };
      return route.fulfill({ json: state.timing.calibration });
    }
    const finished = path.match(/\/timing-calibration\/(accept|cancel)$/);
    if (finished) {
      state.finished.push(finished[1]);
      const savedOutputs = state.timing.calibration!.outputs
        .filter((output) => output.delay_ms !== output.saved_ms)
        .map((output) => ({ ...state.timing.outputs.find((item) => item.resource === output.resource)!, delay_ms: output.delay_ms }));
      state.timing = {
        ...state.timing,
        outputs: state.timing.outputs.map((output) => savedOutputs.find((item) => item.resource === output.resource) ?? output),
        calibration: { ...state.timing.calibration!, state: "stopped", stop_reason: "accepted" },
      };
      return route.fulfill({ json: { run: state.timing.calibration, saved: savedOutputs } });
    }
    if (["activity", "resource-assignments", "photos", "geometry", "projects", "endpoints"].some((suffix) => path.endsWith(`/${suffix}`))) {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });
  return state;
}

async function openTiming(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Installation", exact: true }).click();
  await page.getByRole("group", { name: "Installation workspace", exact: true }).getByRole("button", { name: "Timing", exact: true }).click();
  const panel = page.getByRole("region", { name: "Installation timing" });
  await expect(panel.getByRole("heading", { name: "Output timing" })).toBeVisible();
  return panel;
}

test("operators save output delays and calibrate them live with the wizard", async ({ page }) => {
  const state = await timingRoutes(page);
  const panel = await openTiming(page);

  const led = panel.getByLabel("wled-001 saved delay");
  await led.fill("-45");
  await panel.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => state.saves).toEqual([{ resource: "wled-001", delay_ms: -45, ifMatch: '"1"' }]);

  await panel.getByRole("button", { name: "Start calibration" }).click();
  await expect.poll(() => state.starts).toEqual([
    { projectors: [{ resource: "projector.left", click: true }], leds: ["wled-001"] },
  ]);
  const projector = panel.getByRole("group", { name: "projector.left live delay" });
  await expect(projector.getByText(/Playing · drift 4 ms/)).toBeVisible();
  await projector.getByRole("button", { name: "Later by 100 ms for projector.left" }).click();
  await projector.focus();
  await page.keyboard.press("ArrowRight");
  await expect.poll(() => state.adjustments.at(-1)).toEqual({ resource: "projector.left", delay_ms: 110 });
  await expect(projector.getByRole("button", { name: "Reset to saved" })).toBeVisible();

  const accessibility = await new AxeBuilder({ page }).include(".installation-timing").analyze();
  expect(accessibility.violations).toEqual([]);

  await panel.getByRole("button", { name: "Accept and save" }).click();
  await expect(panel.getByText("Saved projector.left 110 ms.")).toBeVisible();
  expect(state.finished).toEqual(["accept"]);
  await expect(panel.getByLabel("projector.left saved delay")).toHaveValue("110");
  await expect(panel.getByRole("button", { name: "Start calibration" })).toBeVisible();
});

test("designers can read installation timing but cannot calibrate it", async ({ page }) => {
  const state = await timingRoutes(page, "designer");
  const panel = await openTiming(page);
  await expect(panel.getByLabel("wled-001 saved delay")).toBeDisabled();
  await expect(panel.getByRole("button", { name: "Start calibration" })).toHaveCount(0);
  expect(state.saves).toEqual([]);
});
