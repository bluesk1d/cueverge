import { expect, test } from "@playwright/test";
import { openAdvancedShowWorkspace } from "./show-workspace";

const viewports = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "tablet", width: 820, height: 1180 },
  { name: "phone", width: 390, height: 844 },
];

for (const viewport of viewports) {
  test(`workspace fields are spaced and themed at ${viewport.name}`, async ({ page }) => {
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
              scenes: [],
            },
          },
        });
      }
      if (/\/(assets|members|comments|change-requests|presence|mcp-tokens|mcp-proposals)$/.test(path)) {
        return route.fulfill({ json: [] });
      }
      return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
    });
    await page.goto("/demo.html");
    await page.getByRole("button", { name: "Shows", exact: true }).click();
    await openAdvancedShowWorkspace(page);
    const collaboration = page.getByRole("region", { name: "Collaboration" });
    await expect(collaboration).toBeVisible();
    await expect(collaboration).toHaveCSS("display", "grid");
    await expect(collaboration.getByText("Project access", { exact: true })).toBeVisible();

    for (const name of [
      "Object ID (optional)", "New comment", "Request title",
      "Requested change", "Username", "Project role",
    ]) {
      const control = name === "Project role"
        ? collaboration.getByRole("combobox", { name: /^Project role/ })
        : collaboration.getByLabel(name, { exact: true });
      const geometry = await control.evaluate((element) => {
        const label = element.closest("label")!;
        const text = Array.from(label.childNodes).find(
          (node) => node.nodeType === Node.TEXT_NODE && node.textContent?.trim(),
        )!;
        const range = document.createRange();
        range.selectNodeContents(text);
        const labelBox = range.getBoundingClientRect();
        const controlBox = element.getBoundingClientRect();
        const panelBox = element.closest("section")!.getBoundingClientRect();
        return {
          gap: controlBox.top - labelBox.bottom,
          left: controlBox.left - panelBox.left,
          right: panelBox.right - controlBox.right,
        };
      });
      expect(geometry.gap).toBeGreaterThanOrEqual(6);
      expect(geometry.left).toBeGreaterThanOrEqual(0);
      expect(geometry.right).toBeGreaterThanOrEqual(0);
      await expect(control).toHaveCSS("border-radius", "7px");
    }

    await page.getByText("Rehearsal, publication, and rollback", { exact: true }).click();
    for (const name of ["New comment", "Requested change", "Change summary"]) {
      const textarea = page.getByRole("textbox", { name, exact: true });
      await expect(textarea).toHaveCSS("background-color", "rgb(11, 17, 27)");
      await expect(textarea).toHaveCSS("color", "rgb(237, 244, 250)");
      await expect(textarea).toHaveCSS("border-radius", "7px");
      await expect(textarea).toHaveCSS("resize", "vertical");
      await textarea.focus();
      await expect(textarea).toHaveCSS("outline-style", "solid");
      await expect(textarea).toHaveCSS("outline-width", "3px");
    }

    const comment = collaboration.getByRole("textbox", { name: "New comment", exact: true });
    await comment.fill("Review the opening cue.");
    await expect(collaboration.getByRole("button", { name: "Add comment" })).toBeEnabled();
    await collaboration.getByRole("checkbox", { name: "Permit live output" }).check();
    await expect(collaboration.getByRole("checkbox", { name: "Permit live output" })).toBeChecked();
    expect(await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    )).toBe(false);

    await page.emulateMedia({ forcedColors: "active" });
    await comment.focus();
    await expect(collaboration).toHaveCSS("display", "grid");
    await expect(comment).toHaveCSS("outline-width", "3px");
    await expect(comment).toHaveCSS("border-top-width", "1px");

    await page.emulateMedia({ forcedColors: "none" });
    await page.locator(".project-card").evaluate((element) => {
      element.style.maxWidth = "360px";
    });
    const sections = collaboration.locator(".collaboration-grid > section");
    const commentsBox = await sections.nth(0).boundingBox();
    const requestsBox = await sections.nth(1).boundingBox();
    expect(commentsBox).not.toBeNull();
    expect(requestsBox).not.toBeNull();
    expect(requestsBox!.y - (commentsBox!.y + commentsBox!.height)).toBeGreaterThanOrEqual(16);
  });
}
