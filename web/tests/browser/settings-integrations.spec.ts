import { expect, test } from "@playwright/test";

test("Settings integrations toggle Home Assistant and manage installation-wide tokens", async ({ page }) => {
  const state = {
    enabled: false,
    revision: 0,
    puts: [] as unknown[],
    tokens: [] as Record<string, unknown>[],
    created: [] as Record<string, unknown>[],
    revoked: [] as string[],
  };
  const status = () => ({
    installation_id: "home",
    enabled: state.enabled,
    configuration_revision: state.revision,
    state: state.enabled ? "connected" : "disabled",
    error: null,
    broker: { host: "mqtt.local", port: 1883, tls: false },
    topics: {
      availability: "cueverge/home/availability", state: "cueverge/home/state",
      command: "cueverge/home/command", scene: "cueverge/home/scene/set",
      mode: "cueverge/home/mode/set", discovery_prefix: "homeassistant",
    },
    commands: ["blackout", "pause", "resume", "start", "stop"],
    modes: ["Automatic", "Stopped", "Maintenance", "Blackout"],
    commands_received: state.enabled ? 2 : 0,
    commands_rejected: state.enabled ? 1 : 0,
    recent_commands: state.enabled ? [
      { received_at_ms: 1_792_870_000_000, topic: "cueverge/home/command", payload: "start", accepted: true, action: "start" },
      { received_at_ms: 1_792_870_001_000, topic: "cueverge/home/command", payload: "reboot", accepted: false, error: "Unknown command 'reboot'" },
    ] : [],
  });
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/integrations/home-assistant")) {
      if (request.method() === "PUT") {
        const body = request.postDataJSON();
        state.puts.push(body);
        state.enabled = body.enabled;
        state.revision += 1;
      }
      return route.fulfill({ json: status() });
    }
    if (path.endsWith("/integrations/mcp-tokens") && request.method() === "POST") {
      const body = request.postDataJSON();
      state.created.push(body);
      const record = {
        id: "token-1", client_id: body.client_id, username: "visual-review", project_id: "",
        installation_id: "home", scopes: body.scopes, expires_at: body.expires_at,
        revoked_at: null, created_at: "2026-10-24T12:00:00Z",
      };
      state.tokens = [record];
      return route.fulfill({ status: 201, json: { ...record, token: "cvmcp_token-1_secret" } });
    }
    if (path.endsWith("/integrations/mcp-tokens")) return route.fulfill({ json: state.tokens.map((item) => ({ ...item, token: null })) });
    if (/\/integrations\/mcp-tokens\/token-1$/.test(path) && request.method() === "DELETE") {
      state.revoked.push("token-1");
      state.tokens = state.tokens.map((item) => ({ ...item, revoked_at: "2026-10-24T13:00:00Z" }));
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fulfill({ json: [] });
  });

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const ha = page.getByRole("region", { name: "Home Assistant" });
  await expect(ha.getByRole("status")).toHaveText("Off");
  await expect(ha).toContainText("mqtt.local:1883");
  page.once("dialog", (dialog) => dialog.accept());
  await ha.getByRole("button", { name: "Turn on Home Assistant control" }).click();
  await expect(ha.getByRole("status")).toHaveText("Connected");
  expect(state.puts).toEqual([{ enabled: true, expected_revision: 0 }]);
  await ha.getByText("Topics and recent commands").click();
  await expect(ha).toContainText("cueverge/home/command");
  const recent = ha.getByRole("list", { name: "Recent Home Assistant commands" });
  await expect(recent).toContainText("Ran start");
  await expect(recent).toContainText("Rejected: Unknown command 'reboot'");
  await ha.getByRole("button", { name: "Turn off Home Assistant control" }).click();
  await expect(ha.getByRole("status")).toHaveText("Off");
  expect(state.puts[1]).toEqual({ enabled: false, expected_revision: 1 });

  const tokens = page.getByRole("region", { name: "Assistant access for all shows" });
  const create = tokens.getByRole("button", { name: "Create 90-day installation token" });
  await expect(create).toBeDisabled();
  await tokens.getByLabel("Client identity").fill("chatgpt-tunnel");
  await tokens.getByLabel("Operate the loaded show", { exact: false }).check();
  await create.click();
  await expect(tokens.getByRole("status")).toContainText("cvmcp_token-1_secret");
  expect(state.created[0]).toMatchObject({ client_id: "chatgpt-tunnel", scopes: ["read", "live_operation"] });
  const expires = Date.parse(String(state.created[0].expires_at));
  expect(expires).toBeGreaterThan(Date.now() + 89 * 24 * 3600 * 1000);
  expect(expires).toBeLessThan(Date.now() + 90 * 24 * 3600 * 1000);
  await tokens.getByRole("button", { name: "I saved it" }).click();
  await expect(tokens.getByRole("list", { name: "Installation tokens" })).toContainText("read, live_operation");
  await tokens.getByRole("button", { name: "Revoke chatgpt-tunnel" }).click();
  await expect(tokens.getByRole("list", { name: "Installation tokens" })).toContainText("revoked");
  expect(state.revoked).toEqual(["token-1"]);
});
