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
    await expect(page.getByRole("heading", { name: "Network Topology" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "media-client-left" })).toBeVisible();

    await page.getByRole("button", { name: "Operate", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Show controls" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Runtime counters" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Automation state" })).toBeVisible();

    await page.getByRole("button", { name: "Shows", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Draft projects" })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Name for Home Show" })).toHaveValue(
      "Home Show",
    );

    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Installations" })).toBeVisible();
    await expect(page.getByText("Runtime backed")).toBeVisible();

    const horizontalOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(horizontalOverflow).toBe(false);
  });
}
