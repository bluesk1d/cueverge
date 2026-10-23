import { expect, Page, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { InputSourceSnapshot } from "../../src/api";
import { demoInstallation, demoInstallations, demoRuntime, demoTopology } from "../../src/demo/overview-fixtures";

async function inputRoutes(page: Page, mode: "gui" | "environment" = "gui", role = "administrator") {
  await page.context().addCookies([{ name: "cueverge_csrf", value: "original-input-csrf", url: "http://127.0.0.1:4173" }]);
  const snapshot: InputSourceSnapshot = {
    validation_context_digest: "original-context",
    installation_id: demoInstallation.installation_id,
    observed_at_ms: Date.now(), management: mode, configuration_revision: 0,
    settings: { enabled: false, host: "", port: 1883, tls: false, topic: "frigate/events", username: null, accepted_labels: ["person"] },
    credential_present: false, state: "disabled", error: null, freshness: "no_events", freshness_ms: 5000,
    messages_received: 0, invalid_messages: 0, filtered_messages: 0, events_delivered: 0,
    controller_accepted: 0, controller_rejected: 0, audience_matches: 0, connection_attempts: 0,
    last_received_at_ms: null, recent_events: [],
    loaded_rules: {
      audience: { schema_version: 1, labels: ["person"], cameras: ["porch"], zones: ["walkway"],
        observation_freshness_ms: 5000, arrival_confirmation_ms: 1000, absence_grace_ms: 5000, long_idle_ms: 300000 },
      encounter: false, opportunities: false, show_mode: "automatic", audience_state: null,
    },
    recovery: "Check Frigate MQTT publishing and the event topic. Connection is not proof of scene playback.",
  };
  const state = { snapshot, writes: [] as Record<string, unknown>[], checks: [] as Record<string, unknown>[], failHealth: false, healthReads: 0, validationDelay: 0 };
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (["PUT", "POST"].includes(request.method()) && /\/input-source(?:\/validate)?$/.test(path)) {
      if (request.headers()["x-csrf-token"] !== "original-input-csrf" ||
          request.headers()["content-type"] !== "application/json") {
        return route.fulfill({ status: 403, json: { detail: { code: "csrf_failed" } } });
      }
    }
    if (path.endsWith("/auth/session")) return route.fulfill({ json: { username: "test", role } });
    if (path.endsWith("/installation")) return route.fulfill({ json: demoInstallation });
    if (path.endsWith("/installations")) return route.fulfill({ json: demoInstallations });
    if (path.endsWith("/runtime/status")) return route.fulfill({ json: demoRuntime });
    if (path.endsWith("/topology")) return route.fulfill({ json: demoTopology });
    if (path.endsWith("/input-source")) {
      if (request.method() === "PUT") {
        const body = request.postDataJSON();
        state.writes.push(body);
        const { password, clear_password, expected_revision, ...settings } = body;
        state.snapshot = { ...state.snapshot, settings,
          state: settings.enabled ? "connecting" : "disabled",
          configuration_revision: expected_revision + 1,
          credential_present: clear_password ? false : Boolean(password) || state.snapshot.credential_present,
        };
      } else {
        state.healthReads++;
        if (state.failHealth) return route.fulfill({ status: 503, json: { detail: "Synthetic unavailable source" } });
      }
      return route.fulfill({ json: state.snapshot });
    }
    if (path.endsWith("/input-source/validate")) {
      state.checks.push(request.postDataJSON());
      if (state.validationDelay) await new Promise((resolve) => setTimeout(resolve, state.validationDelay));
      return route.fulfill({ json: {
        simulation: true, hardware_commands_sent: false, label_filtered: false, audience_configured: true,
        validation_context_digest: state.snapshot.validation_context_digest,
        events: [{ event_type: "frigate.zone.entered", camera: "porch", zone: "walkway", stale: false, audience_relevant: true }],
        note: "Isolated check; no live event or hardware command was sent.",
      } });
    }
    if (["activity", "resource-assignments", "photos", "geometry", "projects", "endpoints"].some((suffix) => path.endsWith(`/${suffix}`))) return route.fulfill({ json: [] });
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });
  return state;
}

async function openInputs(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Installation", exact: true }).click();
  await page.getByRole("group", { name: "Installation workspace", exact: true }).getByRole("button", { name: "Inputs & connections", exact: true }).click();
  const panel = page.getByRole("region", { name: "Camera event connection" });
  await expect(panel.getByText("Camera events off", { exact: true })).toBeVisible();
  return panel;
}

test("guided camera setup saves and reconnects explicitly without exposing a password", async ({ page }) => {
  const state = await inputRoutes(page);
  const panel = await openInputs(page);
  await panel.getByText("Set up Frigate camera events", { exact: true }).click();
  await panel.getByRole("textbox", { name: "Broker hostname", exact: true }).fill("mqtt.local");
  await panel.getByText("Broker login (optional)", { exact: true }).click();
  await panel.getByRole("textbox", { name: "Broker username (optional)", exact: true }).fill("original-test-user");
  await panel.getByLabel("Broker password", { exact: true }).fill("original-test-only-password");
  await panel.getByRole("checkbox", { name: "Enable camera events" }).check();
  page.once("dialog", (dialog) => dialog.accept());
  await panel.getByRole("button", { name: "Save and connect", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(1);
  await expect(panel.getByText("Connecting to broker…", { exact: true })).toBeVisible();
  await expect(panel.getByLabel("Broker password", { exact: true })).toHaveValue("");
  await expect(panel.getByText(/Password stored on this controller/)).toBeVisible();
  await expect(panel.getByText("Subscribed to camera events", { exact: true })).toHaveCount(0);
  expect(state.writes[0].expected_revision).toBe(0);
  expect(state.writes[0].password).toBe("original-test-only-password");
});

test("operator-managed input settings remain visible but read-only", async ({ page }) => {
  const state = await inputRoutes(page, "environment");
  const panel = await openInputs(page);
  await expect(panel.getByText(/Operator-managed connection/)).toBeVisible();
  await panel.getByText("Set up Frigate camera events", { exact: true }).click();
  await expect(panel.getByRole("textbox", { name: "Broker hostname", exact: true })).toBeDisabled();
  expect(state.writes).toHaveLength(0);
});

test("ordinary project designers cannot configure installation connections", async ({ page }) => {
  await inputRoutes(page, "gui", "designer");
  const panel = await openInputs(page);
  await panel.getByText("Set up Frigate camera events", { exact: true }).click();
  await expect(panel.getByRole("textbox", { name: "Broker hostname", exact: true })).toBeDisabled();
});

test("safe example checks camera and zone without changing the live connection", async ({ page }) => {
  const state = await inputRoutes(page);
  state.validationDelay = 300;
  const panel = await openInputs(page);
  await panel.getByText("Try an isolated example", { exact: true }).click();
  await panel.getByRole("textbox", { name: "Example camera", exact: true }).fill("porch");
  await panel.getByRole("textbox", { name: "Example zone (optional)", exact: true }).fill("walkway");
  await panel.getByRole("button", { name: "Check example safely", exact: true }).click();
  await expect(panel.getByRole("button", { name: "Checking…", exact: true })).toBeDisabled();
  await expect(panel.getByRole("textbox", { name: "Example camera", exact: true })).toBeDisabled();
  await expect(panel.getByRole("button", { name: "Saving…", exact: true, includeHidden: true })).toHaveCount(0);
  await expect(panel.getByText("Example matches loaded visitor rules.", { exact: true })).toBeVisible();
  expect(state.checks).toHaveLength(1);
  expect(state.writes).toHaveLength(0);
  expect(state.snapshot.messages_received).toBe(0);
  state.snapshot.validation_context_digest = "changed-context";
  await panel.getByRole("button", { name: "Refresh input health", exact: true }).click();
  await expect(panel.getByText(/Connection settings or loaded visitor rules changed/)).toBeVisible();
  await expect(panel.getByText("Example matches loaded visitor rules.", { exact: true })).toHaveCount(0);
});

test("input polling preserves edits, reports failure and guards navigation", async ({ page }) => {
  const state = await inputRoutes(page);
  const panel = await openInputs(page);
  await panel.getByText("Set up Frigate camera events", { exact: true }).click();
  await panel.getByRole("textbox", { name: "Broker hostname", exact: true }).fill("pending.local");
  const initialReads = state.healthReads;
  await expect.poll(() => state.healthReads).toBeGreaterThan(initialReads);
  await expect(panel.getByRole("textbox", { name: "Broker hostname", exact: true })).toHaveValue("pending.local");
  state.failHealth = true;
  await panel.getByRole("button", { name: "Refresh input health", exact: true }).click();
  await expect(panel.getByText(/Displayed values are last known, not live/)).toBeVisible();
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "Overview", exact: true }).click();
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("textbox", { name: "Broker hostname", exact: true })).toHaveValue("pending.local");
  expect(state.writes).toHaveLength(0);
});

test("input setup fits a phone and exposes accessible form controls", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await inputRoutes(page);
  const panel = await openInputs(page);
  await panel.getByText("Set up Frigate camera events", { exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const results = await new AxeBuilder({ page }).include(".input-source").analyze();
  expect(results.violations.filter((item) => ["serious", "critical"].includes(item.impact ?? ""))).toEqual([]);
});
