import { expect, test } from "@playwright/test";
import { openAdvancedShowWorkspace } from "./show-workspace";

const viewports = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "tablet", width: 820, height: 1180 },
  { name: "phone", width: 390, height: 844 },
];

for (const viewport of viewports) {
  test(`long timelines stay inside their workspace at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.route("**/api/gui/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/draft")) {
        return route.fulfill({
          json: {
            project: { id: "demo-project", name: "Home Show", head_revision: 1 },
            revision: 1,
            draft: {
              schema_version: 1,
              installation: { schema_version: 1, resources: [] },
              editor_metadata: { installation_id: "demo-installation" },
              scenes: [{
                schema_version: 2,
                id: "preshow",
                name: "Three-minute seasonal pre-show",
                mode: "controller_timed",
                duration_ms: 180000,
                resources: ["roofline.demo"],
                media: [],
                lighting: [{
                  id: "preshow-leds",
                  resource: "roofline.demo",
                  required: true,
                  effect: "timeline",
                  timeline: [],
                }],
                completion: { policy: "loop" },
                scheduling: {
                  safe_points_ms: [0, 60000, 95000, 120000, 130000, 175000],
                  protected_intervals: [],
                },
              }],
            },
          },
        });
      }
      if (/\/(assets|members|comments|change-requests|presence|mcp-tokens|mcp-proposals|revisions|published-packages|simulation-scenarios|apply-jobs)$/.test(path)) {
        return route.fulfill({ json: [] });
      }
      return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
    });
    await page.goto("/demo.html");
    await page.getByRole("button", { name: "Shows", exact: true }).click();
    await openAdvancedShowWorkspace(page);
    await page.getByText("Autonomous show logic designer", { exact: true }).click();
    const graph = page.getByRole("img", { name: "Encounter node flow" });
    await expect(graph).toBeVisible();
    const initialWidth = await graph.evaluate((element) => element.getBoundingClientRect().width);

    await page.getByText("Timeline editor", { exact: true }).click();
    await expect(page.getByRole("button", { name: "lighting output · preshow-leds" })).toBeVisible();
    await page.getByText("Rehearsal, publication, and rollback", { exact: true }).click();
    const card = page.locator(".project-card");
    const cardBox = await card.boundingBox();
    const graphBox = await graph.boundingBox();
    expect(cardBox).not.toBeNull();
    expect(graphBox).not.toBeNull();
    expect(Math.abs(graphBox!.width - initialWidth)).toBeLessThanOrEqual(1);
    expect(graphBox!.x).toBeGreaterThanOrEqual(cardBox!.x);
    expect(graphBox!.x + graphBox!.width).toBeLessThanOrEqual(cardBox!.x + cardBox!.width);
    expect(graphBox!.height).toBeLessThanOrEqual(420);

    for (const section of await card.locator(":scope > *").all()) {
      const box = await section.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x + box!.width).toBeLessThanOrEqual(cardBox!.x + cardBox!.width + 1);
    }

    const timeline = page.locator(".timeline-scroll");
    const timelineSize = await timeline.evaluate((element) => ({
      visible: element.clientWidth,
      content: element.scrollWidth,
    }));
    expect(timelineSize.content).toBeGreaterThanOrEqual(14400);
    expect(timelineSize.visible).toBeLessThan(cardBox!.width);
    const endMarker = page.getByRole("button", { name: "Marker at 175000 milliseconds" });
    await endMarker.scrollIntoViewIfNeeded();
    await expect(endMarker).toBeInViewport();
    expect(await timeline.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
    await endMarker.click();
    await expect(endMarker).toHaveCount(0);

    for (const name of ["Encounter transitions", "Scene transition details"]) {
      const scroller = page.getByRole("region", { name, exact: true });
      await expect(scroller).toBeVisible();
      await scroller.focus();
      await expect(scroller).toBeFocused();
      const bounds = await scroller.evaluate((element) => ({
        width: element.getBoundingClientRect().width,
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
      }));
      expect(bounds.width).toBeLessThan(cardBox!.width);
      if (viewport.name === "phone") {
        expect(bounds.scrollWidth).toBeGreaterThan(bounds.clientWidth);
        await page.keyboard.press("ArrowRight");
        await expect.poll(() => scroller.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
      }
    }

    for (const button of [
      page.getByRole("button", { name: "Run visitor interruption rehearsal" }),
      page.getByRole("button", { name: "Save timeline", exact: true }),
      page.getByRole("button", { name: "Publish immutable package" }),
    ]) {
      await button.scrollIntoViewIfNeeded();
      await expect(button).toBeInViewport();
      const box = await button.boundingBox();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
    }
    expect(await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    )).toBe(false);
  });
}
