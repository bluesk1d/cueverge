import { expect, test } from "@playwright/test";

const viewports = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "wide-desktop", width: 2560, height: 1440 },
  { name: "tablet", width: 820, height: 1180 },
  { name: "phone", width: 390, height: 844 },
];

for (const viewport of viewports) {
  test(`overview shell is usable at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/demo.html");

    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expect(
      page.getByRole("heading", { name: "Installation Overview" }),
    ).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
    await expect(page.getByText("Isolated visual review fixture")).toBeVisible();
    await expect(page.getByRole("button", { name: "Overview", exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect(page.getByRole("button", { name: "Settings", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Overview", exact: true }).focus();
    await expect(page.getByRole("button", { name: "Overview", exact: true })).toBeFocused();

    await page.getByRole("button", { name: "Installation", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Your installation" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Installation Photos" })).toBeVisible();
    await page.getByRole("group", { name: "Installation workspace", exact: true }).getByRole("button", { name: "Devices", exact: true }).click();
    await expect(page.getByRole("heading", { name: "media-client-left" })).toBeVisible();
    const topologyPanel = page
      .getByRole("heading", { name: "Devices", exact: true })
      .locator("xpath=ancestor::section");
    const topologyBox = await topologyPanel.boundingBox();
    expect(topologyBox).not.toBeNull();

    for (const node of await topologyPanel.locator(".topology-node").all()) {
      const header = node.locator("header");
      const title = await header.locator("h3").boundingBox();
      const status = await header.locator(".status-text").boundingBox();
      expect(title).not.toBeNull();
      expect(status).not.toBeNull();
      expect(status!.y - (title!.y + title!.height)).toBeGreaterThanOrEqual(8);
      const statusText = header.locator(".status-text strong");
      expect(await statusText.evaluate((element) =>
        element.scrollWidth <= element.clientWidth &&
        element.scrollHeight <= element.clientHeight,
      )).toBe(true);
    }

    await page.getByRole("button", { name: "Operate", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Show controls" })).toBeVisible();
    await page.getByText("Command and playback diagnostics", { exact: true }).click();
    await expect(page.getByRole("heading", { name: "Runtime counters" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Automation state" })).toBeVisible();

    await page.getByRole("button", { name: "Shows", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Your shows" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Open show Home Show" })).toBeVisible();

    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Your installation" })).toBeVisible();
    await expect(page.getByText("This controller's installation")).toBeVisible();

    const horizontalOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(horizontalOverflow).toBe(false);
  });
}
