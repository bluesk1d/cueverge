import type { Locator, Page } from "@playwright/test";

export async function openLayout(page: Page, add?: "pixel_segment" | "projector_surface") {
  await page.getByRole("button", { name: "Open Layout", exact: true }).first().click();
  if (add) {
    await page.getByText("Add to photo", { exact: true }).click();
    await page.getByRole("button", { name: add === "pixel_segment" ? "LED segment" : "Projector area", exact: true }).click();
  }
}

export async function openProjectorDetails(mapper: Locator) {
  const summary = mapper.getByText("Projector mapping", { exact: true });
  if (!(await summary.locator("..").evaluate((element) => element.hasAttribute("open")))) await summary.click();
}
