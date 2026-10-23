import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { InstallationObjectInventory } from "../../src/api";
import { demoInstallation, demoInstallations, demoRuntime, demoTopology } from "../../src/demo/overview-fixtures";

test("MCP-created fixtures remain discoverable, editable, and linked to layout on desktop and mobile", async ({ page }) => {
  const inventory: InstallationObjectInventory = {
    project_id: "halloween", project_name: "Halloween", revision: 4, archived: false,
    installation_id: "demo-installation", layout_available: true, editable_kinds: ["fixture", "path"],
    resources: [{
      resource: "roofline.demo", kind: "pixel_output", target: "memory", pixel_count: 8,
      segments: [{ id: "left", start: 0, end: 3 }, { id: "right", start: 3, end: 8 }],
    }],
    objects: [{
      resource: "roofline.demo", kind: "fixture", id: "mcp-porch", name: "MCP porch",
      segments: [{ segment: "left", reverse: false }], precedence: 30,
      geometry_semantic_ids: [], pixel_count: 3, geometry_ids: [],
      references: [], missing_geometry_semantic_ids: [],
      traversal: [{ segment: "left", first_pixel: 0, last_pixel: 2, pixel_count: 3, reverse: false }],
    }],
    geometry: [{ semantic_id: "porch-marker", name: "Porch marker", kind: "fixture", resource: null, geometry_ids: ["marker"] }],
  };
  const edits: { method: string; revision?: string; csrf?: string; body: Record<string, unknown> }[] = [];
  await page.context().addCookies([{ name: "cueverge_csrf", value: "object-csrf", url: "http://127.0.0.1:4173" }]);
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/session")) return route.fulfill({ json: { username: "admin", role: "administrator" } });
    if (path.endsWith("/installation")) return route.fulfill({ json: demoInstallation });
    if (path.endsWith("/installations")) return route.fulfill({ json: demoInstallations });
    if (path.endsWith("/runtime/status")) return route.fulfill({ json: demoRuntime });
    if (path.endsWith("/topology")) return route.fulfill({ json: demoTopology });
    if (path.endsWith("/projects")) return route.fulfill({ json: [{
      id: "halloween", name: "Halloween", head_revision: inventory.revision,
      metadata_revision: 1, archived_at: null, created_at: "", updated_at: "",
    }] });
    if (path.endsWith("/installation-objects/update")) {
      const body = request.postDataJSON();
      edits.push({ method: request.method(), revision: request.headers()["if-match"], csrf: request.headers()["x-csrf-token"], body });
      inventory.revision++;
      inventory.objects[0] = {
        ...inventory.objects[0], ...body, geometry_ids: body.geometry_semantic_ids.length ? ["marker"] : [],
      };
      return route.fulfill({ json: { revision: inventory.revision } });
    }
    if (path.endsWith("/installation-objects")) return route.fulfill({ json: inventory });
    if (path.endsWith("/geometry")) return route.fulfill({ json: [{
      id: "marker", installation_id: "demo-installation", photo_id: null, kind: "fixture",
      semantic_id: "porch-marker", name: "Porch marker", points: [{ x: 0.4, y: 0.4 }],
      landmark_ids: [], color: "#6ee7ff", closed: false, label_visible: true, locked: false,
      hidden: false, z_index: 0, direction: "forward", start_index: null, pixel_count: null,
      stage_points: null, revision: 1, created_at: "", updated_at: "",
    }] });
    if (path.endsWith("/stage-coverage")) return route.fulfill({ json: {
      installation_id: "demo-installation", minimum_x: 0, minimum_y: 0, maximum_x: 1, maximum_y: 1, regions: [],
    } });
    return route.fulfill({ json: [] });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Installation", exact: true }).click();
  await page.getByRole("button", { name: "Objects", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Definitions from show" })).toHaveValue("");
  await page.getByRole("combobox", { name: "Definitions from show" }).selectOption("halloween");
  await expect(page.getByText(/Not placed/)).toBeVisible();
  await page.getByRole("button", { name: "Place in Layout" }).click();
  await expect(page.getByLabel("Stable object ID")).toBeDisabled();
  await page.getByLabel("Object name").fill("Porch outline");
  await page.getByRole("button", { name: "Add member segment" }).click();
  await page.getByRole("combobox", { name: "Member segment 2", exact: true }).selectOption("right");
  await page.getByLabel("Reverse member 2", { exact: true }).check();
  await page.getByRole("button", { name: "Move member 2 up" }).click();
  await expect(page.getByRole("list", { name: "Pixel traversal preview" })).toContainText("right: 7 → 3 (5 pixels)");
  await page.getByLabel("Porch marker · fixture").check();
  const accessibility = await new AxeBuilder({ page }).include("main").analyze();
  expect(accessibility.violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole("button", { name: "Save object", exact: true }).click();
  await expect(page.getByRole("button", { name: "Inspect Porch outline" })).toBeVisible();
  expect(edits).toEqual([{
    method: "POST", revision: '"4"', csrf: "object-csrf",
    body: {
      resource: "roofline.demo", kind: "fixture", id: "mcp-porch", name: "Porch outline",
      segments: [{ segment: "right", reverse: true }, { segment: "left", reverse: false }],
      precedence: 30, geometry_semantic_ids: ["porch-marker"],
    },
  }]);
  await page.getByRole("button", { name: "Show in Layout" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Showing layout geometry for Porch outline" })).toBeVisible();
  await expect(page.locator('[data-layout-object="marker"][data-object-highlight="true"]')).toBeVisible();
  await expect(page.locator('[data-layout-object="marker"] .geometry-shape')).toHaveCSS("stroke-width", "7px");
  await page.getByRole("button", { name: "Objects", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Definitions from show" })).toHaveValue("halloween");
  await expect(page.getByRole("button", { name: "Inspect Porch outline" })).toBeVisible();
  expect(edits).toHaveLength(1);
});
