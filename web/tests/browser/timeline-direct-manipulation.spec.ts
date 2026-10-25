import { expect, test, type Page } from "@playwright/test";
import { mockReadyAsset, openTimelineEditor } from "./show-workspace";

type Layer = {
  name: string;
  start_ms: number;
  duration_ms: number;
  in_point_ms: number;
  out_point_ms: number;
  keyframes: { time_ms: number }[];
};

let savedDraft: Record<string, unknown> | null = null;

test("media bin, drag and drop, clip move and trim, keyframe retime and composition presets", async ({
  page,
}) => {
  savedDraft = null;
  await page.setViewportSize({ width: 1500, height: 1100 });
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/assets/still-image/artifacts/review")) {
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="90" height="160"><rect width="90" height="160" fill="#c33"/></svg>',
      });
    }
    if (url.pathname.endsWith("/projects/demo-project/assets")) {
      return route.fulfill({
        json: [
          { ...mockReadyAsset("ghost-video", "Ghost video", "video/mp4"), metadata: { duration_ms: 1500 } },
          {
            ...mockReadyAsset("still-image", "Still image", "image/png"),
            metadata: { artifacts: { review: { media_type: "image/svg+xml" } } },
          },
        ],
      });
    }
    if (url.pathname.endsWith("/projects/demo-project/draft")) {
      if (request.method() === "PUT") {
        savedDraft = request.postDataJSON();
        return route.fulfill({ json: response(2, savedDraft!) });
      }
      return route.fulfill({ json: response(1, draft()) });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openTimelineEditor(page);

  const bin = page.getByRole("region", { name: "Media bin" });
  await expect(bin.getByText("Ghost video", { exact: true })).toBeVisible();

  const lane = page.getByLabel("Track left-window");
  await lane.evaluate((element) => element.scrollIntoView({ block: "center" }));
  const laneBox = (await lane.boundingBox())!;
  const xAt = (ms: number) => laneBox.x + (ms / 5000) * laneBox.width;

  await bin
    .getByText("Ghost video", { exact: true })
    .dragTo(lane, { targetPosition: { x: (1000 / 5000) * laneBox.width, y: 30 } });
  const ghost = page.getByRole("button", { name: "Media layer Ghost video", exact: true });
  await expect(ghost).toBeVisible();
  await expect(bin.getByRole("status")).toContainText("Added Ghost video to left-window at 0:01.000");

  await dragBy(page, ghost, "middle", xAt(1500) - xAt(1000));
  await dragBy(page, ghost, "end", xAt(2500) - xAt(3000));
  await dragBy(page, ghost, "start", xAt(1700) - xAt(1500));

  const keyframe = ghost.locator(".timeline-transform-keyframe").first();
  await keyframe.scrollIntoViewIfNeeded();
  const keyBox = (await keyframe.boundingBox())!;
  await page.mouse.move(keyBox.x + keyBox.width / 2, keyBox.y + keyBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(keyBox.x + keyBox.width / 2 + (xAt(2000) - xAt(1700)), keyBox.y + keyBox.height / 2, {
    steps: 6,
  });
  await page.mouse.up();

  const handle = page.getByRole("slider", { name: "Playhead" });
  await lane.evaluate((element) => element.scrollIntoView({ block: "center" }));
  const freshLane = (await lane.boundingBox())!;
  const handleBox = (await handle.boundingBox())!;
  await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(freshLane.x + (3200 / 5000) * freshLane.width, handleBox.y + handleBox.height / 2, {
    steps: 8,
  });
  await page.mouse.up();
  await expect(page.getByLabel("Playhead time")).toContainText("0:03.200");
  await handle.focus();
  await page.keyboard.press("ArrowRight");
  await expect(handle).toHaveAttribute("aria-valuenow", "3300");

  await page.getByLabel("Scrub position").fill("4000");
  await bin.getByRole("button", { name: "Add Still image at playhead" }).click();
  await expect(page.getByRole("button", { name: "Media layer Still image", exact: true })).toBeVisible();

  const monitor = page.getByRole("figure", { name: "Scene program monitor" });
  await page.getByLabel("Scrub position").fill("2000");
  await expect(monitor).toContainText("Ghost video");

  // Portrait media must letterbox inside the 16:9 frame instead of being cropped to its top slice.
  await page.getByLabel("Scrub position").fill("4000");
  const portrait = monitor.getByRole("img", { name: "Program monitor showing Still image" });
  await expect(portrait).toHaveJSProperty("complete", true);
  const [frameBox, portraitBox] = await Promise.all([
    monitor.locator(".timeline-program-monitor__frame").boundingBox(),
    portrait.boundingBox(),
  ]);
  expect(portraitBox!.height).toBeLessThanOrEqual(frameBox!.height + 1);
  expect(Math.abs(portraitBox!.y - frameBox!.y)).toBeLessThanOrEqual(1);

  await bin.getByRole("button", { name: "Add Ghost video at playhead" }).click();
  await expect(bin.getByRole("status")).toContainText("runs past the scene end at 0:05.000");
  await bin.getByRole("button", { name: "Trim clip to fit" }).click();
  const extra = page.getByRole("button", { name: "Media layer Ghost video", exact: true }).nth(1);
  await expect(page.getByRole("button", { name: "Media layer Ghost video", exact: true })).toHaveCount(2);
  const [firstBox, secondBox] = await Promise.all([
    page.getByRole("button", { name: "Media layer Still image", exact: true }).boundingBox(),
    extra.boundingBox(),
  ]);
  expect(secondBox!.y).toBeGreaterThan(firstBox!.y + 10);
  await page
    .getByRole("complementary", { name: "Inspector" })
    .getByRole("button", { name: "Delete media layer Ghost video" })
    .click();
  await expect(page.getByRole("button", { name: "Media layer Ghost video", exact: true })).toHaveCount(1);
  await page.getByRole("button", { name: "Media layer Still image", exact: true }).click();
  await page.keyboard.press("Delete");
  await expect(page.getByRole("button", { name: "Media layer Still image", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Undo timeline edit" }).click();
  await expect(page.getByRole("button", { name: "Media layer Still image", exact: true })).toHaveCount(1);

  await page.getByRole("button", { name: "+ New composition" }).click();
  const dialog = page.getByRole("dialog", { name: "New composition" });
  await expect(dialog.getByLabel("Whole display overlay")).toBeChecked();
  await dialog.getByLabel("Preset composition name").fill("Flag ripple");
  await dialog.getByRole("button", { name: "Create and open" }).click();
  await expect(page.getByRole("tab", { name: "Flag ripple", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await page.getByLabel("Scrub position").fill("0");
  await bin.getByRole("button", { name: "Add Still image at playhead" }).click();
  await page.getByRole("button", { name: "Layout", exact: true }).click();
  const layoutBox = page.getByLabel("Layout box Still image");
  await expect(layoutBox).toBeVisible();
  await expect(page.getByLabel("Position 1")).toHaveValue("5");
  await expect(page.getByLabel("Position 2")).toHaveValue("3");
  const startScale = Number(await page.getByLabel("Scale 1").inputValue());

  let box = (await layoutBox.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + box.width / 4, box.y + box.height / 2, { steps: 8 });
  await page.mouse.up();
  const movedX = Number(await page.getByLabel("Position 1").inputValue());
  expect(movedX).toBeCloseTo(7.5, 1);
  expect(Number(await page.getByLabel("Position 2").inputValue())).toBeCloseTo(3, 1);
  await page.getByRole("button", { name: "Undo timeline edit" }).click();
  await expect(page.getByLabel("Position 1")).toHaveValue("5");
  await page.getByRole("button", { name: "Redo timeline edit" }).click();
  await expect(page.getByLabel("Position 1")).toHaveValue(String(movedX));

  const corner = page.getByLabel("Scale from top left corner");
  const cornerBox = (await corner.boundingBox())!;
  box = (await layoutBox.boundingBox())!;
  await page.mouse.move(cornerBox.x + cornerBox.width / 2, cornerBox.y + cornerBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(
    cornerBox.x + cornerBox.width / 2 + box.width / 4,
    cornerBox.y + cornerBox.height / 2 + box.height / 4,
    { steps: 8 },
  );
  await page.mouse.up();
  const resized = Number(await page.getByLabel("Scale 1").inputValue());
  expect(resized).toBeCloseTo(startScale / 2, 0);
  await expect(page.getByLabel("Scale 2")).toHaveValue(String(resized));

  const knob = (await page.getByLabel("Rotate layer").boundingBox())!;
  box = (await layoutBox.boundingBox())!;
  await page.keyboard.down("Shift");
  await page.mouse.move(knob.x + knob.width / 2, knob.y + knob.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width + 60, box.y + box.height / 2, { steps: 8 });
  await page.mouse.up();
  await page.keyboard.up("Shift");
  await expect(page.getByLabel("Rotation")).toHaveValue("90");

  await page.getByRole("button", { name: "Anchor top left" }).click();
  await expect(page.getByLabel("Anchor 1")).toHaveValue("0");
  await expect(page.getByLabel("Layout box Still image")).toBeVisible();
  await page.getByRole("tab", { name: "Whole scene", exact: true }).click();

  await page.keyboard.press("Control+s");
  await expect.poll(() => savedDraft).not.toBeNull();
  const metadata = (savedDraft as { editor_metadata: Record<string, unknown> }).editor_metadata;
  const layers = (metadata.timeline_transform_layers as Record<string, Layer[]>).ambient;
  const saved = layers.find((layer) => layer.name === "Ghost video")!;
  expect(saved).toMatchObject({
    start_ms: 1700,
    duration_ms: 800,
    in_point_ms: 200,
    out_point_ms: 1000,
  });
  expect(saved.keyframes.map((key) => key.time_ms)).toEqual([300]);
  expect(layers.find((layer) => layer.name === "Still image")).toMatchObject({
    start_ms: 4000,
    duration_ms: 1000,
  });
  const compositions = Object.values(
    metadata.compositions as Record<string, { name: string; space_kind: string; target_id: string }>,
  );
  expect(compositions).toContainEqual(
    expect.objectContaining({ name: "Flag ripple", space_kind: "stage", target_id: "shared-stage" }),
  );
  const instances = (metadata.composition_instances as Record<string, { start_offset_ms: number }[]>)
    .ambient;
  expect(instances).toHaveLength(1);
  const flag = Object.entries(
    metadata.compositions as Record<string, { name: string }>,
  ).find(([, value]) => value.name === "Flag ripple")![0];
  const flagLayer = (metadata.timeline_transform_layers as Record<string, Layer[]>)[flag][0] as Layer & {
    track_id: string;
    keyframes: { position: number[]; rotation_deg: number; anchor: number[] }[];
  };
  expect(flagLayer.keyframes[0].rotation_deg).toBe(90);
  expect(flagLayer.keyframes[0].anchor).toEqual([0, 0]);
  const flagTracks = (metadata.timeline_tracks as Record<string, { id: string; kind: string; resource_binding: string }[]>)[flag];
  expect(flagTracks).toEqual([
    expect.objectContaining({ id: flagLayer.track_id, kind: "group", resource_binding: "shared-stage" }),
  ]);
  expect(instances[0].start_offset_ms).toBe(0);
});

test("overflowing clips can extend the scene and scene duration is editable in the timeline", async ({ page }) => {
  savedDraft = null;
  await page.setViewportSize({ width: 1500, height: 1100 });
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/projects/demo-project/assets")) {
      return route.fulfill({
        json: [{ ...mockReadyAsset("long-video", "Long video", "video/mp4"), metadata: { duration_ms: 3000 } }],
      });
    }
    if (url.pathname.endsWith("/projects/demo-project/draft")) {
      if (request.method() === "PUT") {
        savedDraft = request.postDataJSON();
        return route.fulfill({ json: response(2, savedDraft!) });
      }
      return route.fulfill({ json: response(1, draft()) });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });
  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await openTimelineEditor(page);
  const bin = page.getByRole("region", { name: "Media bin" });
  const clip = page.getByRole("button", { name: "Media layer Long video", exact: true });

  await page.getByLabel("Scrub position").fill("4000");
  await bin.getByRole("button", { name: "Add Long video at playhead" }).click();
  await expect(bin.getByRole("status")).toContainText("Long video (0:03.000) runs past the scene end at 0:05.000");
  await expect(clip).toHaveCount(0);
  await bin.getByRole("button", { name: "Cancel" }).click();
  await expect(bin.getByRole("group", { name: "Clip runs past scene end" })).toHaveCount(0);
  await expect(clip).toHaveCount(0);

  await bin.getByRole("button", { name: "Add Long video at playhead" }).click();
  await bin.getByRole("button", { name: "Extend scene to 0:07.000" }).click();
  await expect(clip).toHaveCount(1);
  await expect(bin.getByRole("status")).toContainText("extended the scene to 0:07.000");
  await page.getByRole("button", { name: "Scene settings" }).click();
  const duration = page.getByLabel("Scene duration (seconds)");
  await expect(duration).toHaveValue("7");
  await page.getByRole("button", { name: "Close" }).click();

  await page.getByRole("button", { name: "Undo timeline edit" }).click();
  await expect(clip).toHaveCount(0);
  await page.getByRole("button", { name: "Scene settings" }).click();
  await expect(duration).toHaveValue("5");
  await page.getByRole("button", { name: "Close" }).click();
  await page.getByRole("button", { name: "Redo timeline edit" }).click();
  await expect(clip).toHaveCount(1);

  await page.getByRole("button", { name: "Scene settings" }).click();
  await duration.fill("6");
  await expect(page.getByLabel("Timeline validation")).toContainText("outside the scene");
  await duration.fill("0");
  await expect(page.getByLabel("Timeline validation")).toContainText("Scene duration must be a positive");
  await duration.fill("8.5");
  await page.getByRole("button", { name: "Close" }).click();
  await expect(page.getByLabel("Timeline validation")).toContainText("Timeline valid");
  await page.getByRole("button", { name: "Save timeline" }).click();
  await expect.poll(() => savedDraft).not.toBeNull();
  const saved = savedDraft as unknown as {
    scenes: { duration_ms: number }[];
    editor_metadata: { timeline_transform_layers: Record<string, Layer[]> };
  };
  expect(saved.scenes[0].duration_ms).toBe(8500);
  expect(saved.editor_metadata.timeline_transform_layers.ambient[0]).toMatchObject({
    start_ms: 4000, duration_ms: 3000, in_point_ms: 0, out_point_ms: 3000,
  });
});

async function dragBy(
  page: Page,
  target: ReturnType<Page["getByRole"]>,
  grip: "start" | "middle" | "end",
  deltaX: number,
) {
  await target.scrollIntoViewIfNeeded();
  const box = (await target.boundingBox())!;
  const x = grip === "start" ? box.x + 2 : grip === "end" ? box.x + box.width - 2 : box.x + box.width / 2;
  const y = box.y + box.height - 3;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + deltaX, y, { steps: 8 });
  await page.mouse.up();
}

function response(revision: number, document: Record<string, unknown>) {
  return {
    project: {
      id: "demo-project",
      name: "Home Show",
      head_revision: revision,
      metadata_revision: 1,
      archived_at: null,
      created_at: "2026-10-15T12:00:00Z",
      updated_at: "2026-10-15T12:00:00Z",
    },
    revision,
    draft: document,
  };
}

function draft() {
  return {
    schema_version: 1,
    installation: { schema_version: 1, resources: [] },
    editor_metadata: {},
    scenes: [
      {
        schema_version: 2,
        id: "ambient",
        name: "Ambient",
        mode: "media_synchronized",
        resources: ["projector.left", "wled-001"],
        duration_ms: 5000,
        clock_source: "left-window",
        media: [
          {
            id: "left-window",
            resource: "projector.left",
            asset: null,
            required: true,
            start_delay_ms: 0,
          },
        ],
        lighting: [
          {
            id: "roofline",
            resource: "wled-001",
            required: true,
            effect: "timeline",
            timeline: [],
          },
        ],
      },
    ],
  };
}
