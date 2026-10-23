import type { Page } from "@playwright/test";

export async function openAdvancedShowWorkspace(page: Page) {
  await page.getByRole("button", { name: /^Open show / }).first().click();
  await page.getByText("Show options", { exact: true }).click();
  await page.getByText("Share & review", { exact: true }).click();
  await page.getByText("Assets", { exact: true }).click();
  await page.getByText("Advanced authoring and history", { exact: true }).click();
}
