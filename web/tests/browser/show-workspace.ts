import type { Page } from "@playwright/test";

export async function openAdvancedShowWorkspace(page: Page) {
  await page.getByRole("button", { name: /^Open show / }).first().click();
  await page.getByText("Show options", { exact: true }).click();
  await page.getByText("Share & review", { exact: true }).click();
  await page.getByText("Assets", { exact: true }).click();
  await page.getByText("Advanced authoring and history", { exact: true }).click();
}

export async function openTimelineEditor(page: Page) {
  await page.getByRole("button", { name: /^Open show / }).first().click();
  const scenesTab = page.getByRole("button", { name: "Scenes", exact: true });
  if ((await scenesTab.getAttribute("aria-pressed")) !== "true") await scenesTab.click();
  await page.getByRole("button", { name: "Edit timeline", exact: true }).click();
}

export async function openTimelineTool(page: Page, name: string) {
  await page.getByRole("tab", { name, exact: true }).click();
}

export function mockReadyAsset(id: string, name: string, mediaType: string) {
  return {
    id,
    project_id: "demo-project",
    name,
    status: "ready",
    current_revision: 1,
    metadata_revision: 1,
    owner: null,
    license: null,
    provenance: null,
    review_status: "approved",
    expires_on: null,
    folder: null,
    tags: [],
    favorite: false,
    error: null,
    digest_sha256: id.padEnd(64, "0").slice(0, 64),
    size_bytes: 1024,
    media_type: mediaType,
    original_filename: `${id}.bin`,
    scan_status: "clean",
    metadata: null,
    original_file_status: "available",
    artifact_faults: [],
    references: [],
    unused: true,
    created_at: "2026-10-14T12:00:00Z",
    updated_at: "2026-10-14T12:00:00Z",
  };
}

export async function openComposition(page: Page, compositionId: string) {
  await page.locator(`[role="tab"][data-composition-id="${compositionId}"]`).click();
}
