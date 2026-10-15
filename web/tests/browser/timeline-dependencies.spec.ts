import { expect, test } from "@playwright/test";

let savedDraft: Record<string, unknown> | null = null;

test("composition replacement prevents orphaned dependencies", async ({ page }) => {
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/projects/demo-project/assets")) {
      return route.fulfill({ json: [] });
    }
    if (
      url.pathname.endsWith("/projects/demo-project/draft") &&
      request.method() === "GET"
    ) {
      return route.fulfill({ json: response(1, document()) });
    }
    if (
      url.pathname.endsWith("/projects/demo-project/draft") &&
      request.method() === "PUT"
    ) {
      savedDraft = request.postDataJSON();
      return route.fulfill({ json: response(2, savedDraft) });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Shows", exact: true }).click();
  await page.getByText("Timeline editor", { exact: true }).click();
  const hierarchy = page.locator(".composition-hierarchy");

  const parent = hierarchy.locator("li").nth(0);
  const child = hierarchy.locator("li").nth(1);
  const unused = hierarchy.locator("li").last();

  await expect(parent.getByText("Used by scene ambient")).toBeVisible();
  await expect(
    child.getByText(
      "Used by composition Parent, scene ambient instance instance-child",
    ),
  ).toBeVisible();
  await unused.getByRole("button", { name: "Delete unused" }).click();
  await expect(
    hierarchy.locator("li > span > strong").filter({ hasText: "Unused" }),
  ).toHaveCount(0);

  await child.getByLabel("Replacement for Child").selectOption("replacement");
  await child.getByRole("button", { name: "Replace and delete" }).click();
  await expect(
    hierarchy.locator("li > span > strong").filter({ hasText: "Child" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Save timeline" }).click();

  await expect.poll(() => savedDraft).not.toBeNull();
  const metadata = (savedDraft as { editor_metadata: Record<string, unknown> })
    .editor_metadata;
  const compositions = metadata.compositions as Record<
    string,
    { children: string[] }
  >;
  expect(Object.keys(compositions).sort()).toEqual(["parent", "replacement"]);
  expect(compositions.parent.children).toEqual(["replacement"]);
  expect(
    (
      metadata.composition_instances as Record<
        string,
        { composition_id: string }[]
      >
    ).ambient[0].composition_id,
  ).toBe("replacement");
  expect(
    Object.keys(metadata.timeline_views as Record<string, unknown>).sort(),
  ).toEqual(["ambient", "parent", "replacement"]);
  expect(
    Object.keys(metadata.timeline_tracks as Record<string, unknown>).sort(),
  ).toEqual(["ambient", "parent", "replacement"]);
});

function response(revision: number, draft: Record<string, unknown>) {
  return {
    project: {
      id: "demo-project",
      name: "Home Show",
      head_revision: revision,
      metadata_revision: 1,
      archived_at: null,
      created_at: "2026-10-14T12:00:00Z",
      updated_at: "2026-10-14T12:00:00Z",
    },
    revision,
    draft,
  };
}

function composition(id: string, name: string, children: string[] = []) {
  return {
    id,
    name,
    duration_ms: 2000,
    space_kind: "stage",
    target_id: "shared-stage",
    width: 10,
    height: 4,
    local_time_offset_ms: 0,
    children,
    safe_points_ms: [],
    protected_intervals: [],
  };
}

function document() {
  return {
    schema_version: 1,
    installation: { schema_version: 1, resources: [] },
    editor_metadata: {
      compositions: {
        parent: composition("parent", "Parent", ["child"]),
        child: composition("child", "Child"),
        replacement: composition("replacement", "Replacement"),
        unused: composition("unused", "Unused"),
      },
      scene_compositions: { ambient: ["parent"] },
      composition_instances: {
        ambient: [
          {
            id: "instance-child",
            composition_id: "child",
            start_offset_ms: 500,
            time_scale: 1,
            parameter_overrides: {},
            placement: { x: 1, y: 1 },
          },
        ],
      },
      timeline_views: {
        ambient: {},
        parent: {},
        child: {},
        replacement: {},
        unused: {},
      },
      timeline_tracks: {
        ambient: [],
        parent: [],
        child: [],
        replacement: [],
        unused: [],
      },
    },
    scenes: [
      {
        schema_version: 2,
        id: "ambient",
        name: "Ambient",
        mode: "media_synchronized",
        resources: ["projector.left"],
        duration_ms: 5000,
        clock_source: "left",
        media: [
          {
            id: "left",
            resource: "projector.left",
            asset: null,
            required: true,
            start_delay_ms: 0,
          },
        ],
        lighting: [],
        scheduling: { safe_points_ms: [], protected_intervals: [] },
      },
    ],
  };
}
