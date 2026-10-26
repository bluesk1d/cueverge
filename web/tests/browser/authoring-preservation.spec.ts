import { expect, test } from "@playwright/test";
import { openAdvancedShowWorkspace, openTimelineEditor, openTimelineTool } from "./show-workspace";

import baseline from "../../../tests/fixtures/usability-authoring.json" with { type: "json" };

for (const scene of baseline.scenes) {
  test(`saving ${scene.name} preserves existing runtime lighting`, async ({ page }) => {
    let saved: typeof baseline | undefined;
    let revision = 1;
    await page.route("**/api/gui/v1/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (path.endsWith("/draft")) {
        if (request.method() === "PUT") {
          saved = request.postDataJSON();
          revision += 1;
        }
        return route.fulfill({
          json: {
            project: { id: "demo-project", name: "Home Show", head_revision: revision },
            revision,
            draft: saved ?? structuredClone(baseline),
          },
        });
      }
      if (/\/(assets|members|comments|change-requests|presence|mcp-tokens|mcp-proposals|simulation-scenarios)$/.test(path)) {
        return route.fulfill({ json: [] });
      }
      return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
    });

    await page.goto("/demo.html");
    await page.getByRole("button", { name: "Shows", exact: true }).click();
    await openTimelineEditor(page);
    await page.getByRole("combobox", { name: "Scene", exact: true }).selectOption(scene.id);
    if (scene.id === "legacy-sweep") {
      await expect(page.getByRole("slider", { name: "Zoom", exact: true })).toHaveValue("100");
      await expect(page.getByRole("combobox", { name: "Snap", exact: true })).toHaveValue("250");
    }
    const saveTimeline = page.getByRole("button", { name: "Save timeline", exact: true });
    // Nothing to save until an edit: an unchanged save would only create a new revision.
    await expect(saveTimeline).toBeDisabled();
    await openTimelineTool(page, "Markers & cues");
    await page.getByLabel("New sync marker name").fill("Preservation check");
    await page.getByRole("button", { name: "Add sync marker" }).click();
    await expect(saveTimeline).toBeEnabled();
    await saveTimeline.click();
    await expect.poll(() => saved).toBeDefined();
    await expect(saveTimeline).toBeDisabled();

    expect(saved!.scenes).toEqual(baseline.scenes);
    expect(saved!.installation).toEqual(baseline.installation);
    expect(saved!.program).toEqual(baseline.program);
    expect(saved!.editor_metadata.baseline_notes).toEqual(baseline.editor_metadata.baseline_notes);
    expect(saved!.editor_metadata.timeline_views["legacy-sweep"]).toEqual(
      baseline.editor_metadata.timeline_views["legacy-sweep"],
    );

    if (scene.id === "baseline-timeline") {
      await page.getByRole("button", { name: "Scene settings" }).click();
      await page.getByLabel("Lighting calibration offset leds", { exact: true }).fill("125");
      await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
      await page.getByRole("button", { name: "Save timeline", exact: true }).click();
      await expect.poll(() => revision).toBe(3);
      const expected = structuredClone(baseline);
      const output = expected.scenes[0].lighting;
      if (!Array.isArray(output)) {
        throw new Error("Synthetic timeline fixture must have multi-output lighting");
      }
      output[0].timing_offset_ms = 125;
      expect(saved!.scenes).toEqual(expected.scenes);
      expect(saved!.editor_metadata.baseline_notes).toEqual(baseline.editor_metadata.baseline_notes);
    }
  });
}
