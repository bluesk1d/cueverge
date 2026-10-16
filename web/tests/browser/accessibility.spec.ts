import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("demo workspace has no serious automated accessibility violations", async ({
  page,
}) => {
  await page.goto("/demo.html");

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();

  expect(
    results.violations.filter(({ impact }) => impact === "critical" || impact === "serious"),
  ).toEqual([]);
});

test("operator can pause the show using only the keyboard", async ({ page }) => {
  let operateRequest = "";
  await page.route("**/api/gui/v1/operate/actions", async (route) => {
    operateRequest = route.request().postData() ?? "";
    await route.fulfill({ json: { action: "pause", status: "ok" } });
  });
  await page.goto("/demo.html");

  await page.keyboard.press("Tab");
  const firstControl = page.locator(":focus");
  await expect(firstControl).toBeVisible();

  for (let index = 0; index < 20; index += 1) {
    const name = await page.locator(":focus").getAttribute("aria-label");
    const text = await page.locator(":focus").textContent();
    if (name === "Operate" || text?.trim() === "Operate") {
      await page.keyboard.press("Enter");
      break;
    }
    await page.keyboard.press("Tab");
  }
  await expect(page.getByRole("heading", { name: "Operate" })).toBeVisible();

  for (let index = 0; index < 30; index += 1) {
    if ((await page.locator(":focus").textContent())?.trim() === "Pause") {
      await page.keyboard.press("Enter");
      await expect.poll(() => operateRequest).toContain('"action":"pause"');
      return;
    }
    await page.keyboard.press("Tab");
  }

  throw new Error("Pause action was not reachable within 30 tab stops");
});
