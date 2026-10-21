import { expect, test } from "@playwright/test";

const installationId = "demo-installation";
const semanticId = "semantic-roofline";
const pixels = Array.from({ length: 5 }, (_, offset) => ({
  output_index: 100 + offset,
  local_x: offset * 0.5,
  local_y: 0.25,
  stage_x: 5 + offset * 0.5,
  stage_y: 3.25,
  footprint_width: 0.02,
  footprint_height: 0.03,
}));
let savedLightingSurface: Record<string, unknown> | null = null;
let projectDraftWrites = 0;
let mutations = 0;

test("linked LED views preserve physical order and support pre-live preview", async ({
  page,
}) => {
  await page.clock.install();
  await page.route("**/api/gui/v1/**", async (route) => {
    const request = route.request();
    if (request.method() !== "GET") mutations += 1;
    const url = new URL(route.request().url());
    if (url.pathname.includes("/projects/") && url.pathname.endsWith("/draft")) {
      if (request.method() === "PUT") {
        projectDraftWrites += 1;
      }
      return route.fulfill({ status: 404, json: { detail: "not used" } });
    }
    if (url.pathname.endsWith(`/${installationId}/photos`)) {
      return route.fulfill({
        json: [photo("wide-led", "Wide roofline"), photo("close-led", "Roofline close-up")],
      });
    }
    if (url.pathname.endsWith(`/${installationId}/geometry`)) {
      return route.fulfill({
        json: [
          segment("segment-wide", "wide-led", 0.1, 0.9),
          segment("segment-close", "close-led", 0.25, 0.65),
          {
            ...segment("segment-next", "wide-led", 0.2, 0.4),
            semantic_id: "semantic-next",
            name: "Second section",
            start_index: 105,
            pixel_count: 3,
            direction: "reverse",
          },
          {
            ...segment("segment-other-output", "wide-led", 0.2, 0.2),
            semantic_id: "semantic-other",
            name: "Other output",
          },
          overlayGeometry("projector-wide", "wide-led", "projector_surface", [
            [0.1, 0.1],
            [0.6, 0.1],
            [0.6, 0.5],
            [0.1, 0.5],
          ]),
          overlayGeometry("mask-wide", "wide-led", "mask", [
            [0.3, 0.2],
            [0.4, 0.2],
            [0.4, 0.4],
          ]),
          overlayGeometry("path-wide", "wide-led", "path", [
            [0.1, 0.7],
            [0.8, 0.7],
          ]),
          overlayGeometry("fixture-wide", "wide-led", "fixture", [[0.75, 0.35]]),
        ],
      });
    }
    if (
      request.method() === "PUT" &&
      url.pathname.includes("/installation-geometry/") &&
      !url.pathname.endsWith("/lighting-surface")
    ) {
      const geometryId = url.pathname.split("/").at(-1)!;
      return route.fulfill({
        json: segment(
          geometryId,
          geometryId.includes("close") ? "close-led" : "wide-led",
          0.1,
          0.9,
        ),
      });
    }
    if (url.pathname.includes("/lighting-surface")) {
      const geometryId = url.pathname.split("/").at(-2)!;
      if (request.method() === "PUT") {
        savedLightingSurface = request.postDataJSON();
        const response = surface(geometryId);
        response.pixel_overrides = (
          savedLightingSurface as {
            pixel_overrides: typeof response.pixel_overrides;
          }
        ).pixel_overrides;
        response.pixels = response.pixels.map((pixel) =>
          pixel.output_index === 102
            ? { ...pixel, local_x: 1.2, local_y: 0.4 }
            : pixel,
        );
        response.revision = 2;
        return route.fulfill({ json: response });
      }
      return route.fulfill({ json: {
        ...surface(geometryId),
        resource: geometryId === "segment-other-output" ? "porch.demo" : "roofline.demo",
      } });
    }
    if (url.pathname.startsWith("/api/gui/v1/installation-photos/")) {
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600"><rect width="1000" height="600" fill="#161d27"/></svg>',
      });
    }
    if (
      url.pathname.endsWith("/assets/image-library") ||
      url.pathname.endsWith("/projector-overlaps") ||
      url.pathname.endsWith("/geometry-suggestions") ||
      url.pathname.endsWith("/discovery/endpoints")
    ) {
      return route.fulfill({ json: [] });
    }
    if (url.pathname.endsWith("/stage-coverage")) {
      return route.fulfill({
        json: {
          installation_id: installationId,
          minimum_x: 0,
          minimum_y: 0,
          maximum_x: 1,
          maximum_y: 1,
          regions: [],
        },
      });
    }
    return route.fulfill({ status: 404, json: { detail: "fixture route missing" } });
  });

  await page.goto("/demo.html");
  await page.getByRole("button", { name: "Installation", exact: true }).click();
  const mapper = page
    .getByRole("heading", { name: "Visual Mapper" })
    .locator("xpath=ancestor::section");
  const viewpoint = mapper.getByRole("combobox").first();
  const canvas = mapper.getByRole("img", { name: /Geometry editor/ });

  await mapper
    .getByRole("listitem")
    .filter({ hasText: "Roofline pixels" })
    .getByRole("button", { name: "Edit" })
    .click();
  await expect(mapper.getByText(/5 pixels mapped from output index 100 to 104/)).toBeVisible();
  await expect(mapper.locator(".geometry-pixel-label").first()).toHaveText("100");
  await expect(mapper.locator(".geometry-pixel-label").last()).toHaveText("104");
  const wideCx = await mapper.locator(".geometry-pixel-footprint").first().getAttribute("cx");

  await mapper.getByRole("button", { name: "Show simulation overlay" }).click();
  await expect(canvas).toHaveClass(/geometry-canvas--simulation/);
  await expect(mapper.locator(".geometry-simulation-pixel")).toHaveCount(13);
  await expect(mapper.locator(".geometry-shape--projector_surface")).toHaveCount(1);
  await expect(mapper.locator(".geometry-shape--mask")).toHaveCount(1);
  await expect(mapper.locator(".geometry-shape--path")).toHaveCount(1);
  await expect(mapper.locator(".geometry-shape--fixture")).toHaveCount(1);
  await expect(mapper.getByText(/Approximate photo simulation/)).toBeVisible();
  await expect(mapper.getByText(/no hardware adapters or enrolled clients/)).toBeVisible();
  const projectorPoints = await mapper
    .locator(".geometry-shape--projector_surface")
    .getAttribute("points");
  const previewMode = mapper.getByLabel("Simulation preview mode");
  await previewMode.selectOption("logical_composition");
  await expect(mapper.locator(".geometry-simulation-pixel")).toHaveCount(13);
  await expect(mapper.locator(".geometry-shape--projector_surface")).toHaveCount(0);
  await expect(mapper.getByLabel("Simulation context status")).toContainText(
    "logical composition · scene time 0 ms",
  );
  await previewMode.selectOption("parent_context");
  await expect(mapper.locator(".geometry-shape--projector_surface")).toHaveCount(1);
  await previewMode.selectOption("native_projector");
  await expect(mapper.locator(".geometry-shape--projector_surface")).toHaveCount(1);
  await expect(mapper.locator(".geometry-simulation-pixel")).toHaveCount(0);
  await previewMode.selectOption("selected_output");
  await mapper.getByLabel("Simulation output context").selectOption("lighting");
  await expect(mapper.locator(".geometry-simulation-pixel")).toHaveCount(13);
  await expect(mapper.locator(".geometry-shape--projector_surface")).toHaveCount(0);
  await previewMode.selectOption("full_installation");
  await expect(
    mapper.locator(".geometry-shape--projector_surface"),
  ).toHaveAttribute("points", projectorPoints ?? "");
  await expect(mapper.getByLabel("Simulation context status")).toContainText(
    "full installation · scene time 0 ms",
  );
  await mapper
    .getByRole("button", { name: "Show side-by-side viewpoint" })
    .click();
  await expect(
    mapper.getByRole("img", {
      name: "Synchronized simulation for Roofline close-up",
    }),
  ).toBeVisible();
  await expect(mapper.locator(".geometry-simulation-pixel")).toHaveCount(18);
  await previewMode.selectOption("logical_composition");
  await expect(mapper.locator(".geometry-simulation-pixel")).toHaveCount(18);
  await expect(
    mapper
      .getByRole("img", {
        name: "Synchronized simulation for Roofline close-up",
      })
        .locator("title")
        .first(),
  ).toContainText("scene time 0 ms");
  await mapper
    .getByRole("button", { name: "Hide side-by-side viewpoint" })
    .click();
  await expect(
    mapper.getByRole("img", {
      name: "Synchronized simulation for Roofline close-up",
    }),
  ).toHaveCount(0);
  await previewMode.selectOption("full_installation");

  const mutationsBeforeTraversal = mutations;
  await mapper.getByRole("button", { name: "Preview traversal" }).click();
  await expect(canvas).toHaveClass(/geometry-canvas--traversal/);
  const traversal = mapper.getByRole("region", { name: "Output traversal preview" });
  await expect(traversal).toBeVisible();
  const legend = traversal.getByRole("list", { name: "Output segment order" });
  await expect(legend.getByRole("listitem")).toHaveCount(2);
  await expect(legend.getByRole("listitem").nth(0)).toContainText("Roofline pixels · 100-104");
  await expect(legend.getByRole("listitem").nth(1)).toContainText("Second section · 105-107 · last point to first");
  await expect(traversal.getByLabel("Traversal diagnostics")).toContainText("Unmapped output indices 0-99");
  await traversal.getByRole("button", { name: "Restart output traversal" }).click();
  await expect(traversal.getByLabel("Current traversal pixel")).toContainText("Pixel 0 / 799");
  await traversal.getByRole("button", { name: "Play output traversal" }).click();
  await page.clock.runFor(3400);
  await expect(traversal.getByLabel("Current traversal pixel")).toContainText("Pixel 102 / 799");
  const pulse = traversal.locator('.output-traversal__pulse[data-output-index="102"]');
  await expect(pulse).toHaveAttribute("data-segment-id", "segment-wide");
  await page.clock.runFor(100);
  await expect(traversal.getByLabel("Current traversal pixel")).toContainText("2. Second section");
  await expect(traversal.locator('.output-traversal__pulse[data-output-index="105"]')).toHaveAttribute("cx", "800");
  await page.clock.runFor(50);
  await expect(traversal.locator('.output-traversal__pulse[data-output-index="106"]')).toHaveAttribute("cx", "500");
  await traversal.getByRole("button", { name: "Pause output traversal" }).click();
  const pausedIndex = await traversal.getByLabel("Output pixel index").inputValue();
  await page.clock.runFor(500);
  await expect(traversal.getByLabel("Output pixel index")).toHaveValue(pausedIndex);
  await traversal.getByLabel("Output pixel index").press("End");
  await expect(traversal.getByLabel("Current traversal pixel")).toContainText("Pixel 799 / 799");
  await traversal.getByRole("button", { name: "Play output traversal" }).click();
  await page.clock.runFor(100);
  await expect(traversal.getByLabel("Current traversal pixel")).toContainText("Pixel 2 / 799");
  await mapper.getByRole("button", { name: "Stop traversal preview" }).click();
  await expect(canvas).not.toHaveClass(/geometry-canvas--traversal/);
  await expect(traversal).toHaveCount(0);
  expect(mutations).toBe(mutationsBeforeTraversal);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mapper.getByRole("button", { name: "Preview traversal" }).click();
  await expect(traversal.getByRole("button", { name: "Play output traversal" })).toBeVisible();
  await page.clock.runFor(1000);
  await expect(traversal.getByLabel("Output pixel index")).toHaveValue("0");
  const viewport = page.viewportSize()!;
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await traversal.evaluate((element) => element.scrollWidth <= element.clientWidth))
    .toBe(true);
  await traversal.getByRole("button", { name: "Close output traversal" }).click();
  await expect(traversal).toHaveCount(0);
  await page.setViewportSize(viewport);

  await mapper.getByText("Advanced calibration", { exact: true }).click();
  await mapper.getByLabel("Pixel correction output").selectOption("102");
  await mapper.getByLabel("Corrected pixel local X").fill("1.2");
  await mapper.getByLabel("Corrected pixel local Y").fill("0.4");
  await mapper.getByRole("button", { name: "Apply pixel correction" }).click();
  await mapper.getByRole("button", { name: "Update geometry" }).click();
  await expect.poll(() => savedLightingSurface).not.toBeNull();
  expect(
    (
      savedLightingSurface as {
        pixel_overrides: {
          output_index: number;
          local_x: number;
          local_y: number;
        }[];
      }
    ).pixel_overrides,
  ).toEqual([{ output_index: 102, local_x: 1.2, local_y: 0.4 }]);
  expect(projectDraftWrites).toBe(0);

  await viewpoint.selectOption("close-led");
  await mapper
    .getByRole("listitem")
    .filter({ hasText: "Roofline pixels" })
    .getByRole("button", { name: "Edit" })
    .click();
  await expect(mapper.getByText(/5 pixels mapped from output index 100 to 104/)).toBeVisible();
  const closeCx = await mapper.locator(".geometry-pixel-footprint").first().getAttribute("cx");
  expect(closeCx).not.toBe(wideCx);
  expect(surface("segment-wide").pixels).toEqual(surface("segment-close").pixels);
});

function photo(id: string, name: string) {
  return {
    id,
    installation_id: installationId,
    name,
    viewpoint: name,
    original_filename: `${id}.jpg`,
    media_type: "image/jpeg",
    digest_sha256: "e".repeat(64),
    size_bytes: 1024,
    orientation_degrees: 0,
    crop_x: null,
    crop_y: null,
    crop_width: null,
    crop_height: null,
    scale_hint: null,
    revision: 1,
    created_at: "2026-10-14T12:00:00Z",
    updated_at: "2026-10-14T12:00:00Z",
  };
}

function segment(id: string, photoId: string, x: number, y: number) {
  return {
    id,
    installation_id: installationId,
    photo_id: photoId,
    kind: "pixel_segment",
    name: "Roofline pixels",
    semantic_id: semanticId,
    landmark_ids: ["roof-start", "roof-end"],
    points: [{ x, y }, { x: x + 0.6, y }],
    closed: false,
    color: "#f2af45",
    label_visible: true,
    locked: false,
    hidden: false,
    z_index: 0,
    direction: "forward",
    start_index: 100,
    pixel_count: 5,
    revision: 1,
    created_at: "2026-10-14T12:00:00Z",
    updated_at: "2026-10-14T12:00:00Z",
  };
}

function surface(geometryId: string) {
  return {
    geometry_id: geometryId,
    resource: "roofline.demo",
    local_width: 2,
    local_height: 1,
    local_points: [[0, 0.25], [2, 0.25]],
    stage_matrix: [1, 0, 0, 1, 5, 3],
    fixture_id: "roofline",
    group_id: "front",
    path_id: "roof-path",
    strand_id: "strand-a",
    calibration_id: "roof-calibration",
    pixel_footprint_width: 0.02,
    pixel_footprint_height: 0.03,
    brightness_limit: 0.6,
    timing_offset_ms: -45,
    release_offset_ms: 20,
    pixel_overrides: [],
    pixels,
    revision: 1,
    created_at: "2026-10-14T12:00:00Z",
    updated_at: "2026-10-14T12:00:00Z",
  };
}

function overlayGeometry(
  id: string,
  photoId: string,
  kind: "projector_surface" | "mask" | "path" | "fixture",
  points: [number, number][],
) {
  return {
    id,
    installation_id: installationId,
    photo_id: photoId,
    kind,
    name: `${kind.replaceAll("_", " ")} overlay`,
    semantic_id: `semantic-${id}`,
    landmark_ids: [],
    points: points.map(([x, y]) => ({ x, y })),
    closed: kind === "projector_surface" || kind === "mask",
    color: "#38bdf8",
    label_visible: true,
    locked: false,
    hidden: false,
    z_index: 1,
    direction: "forward",
    start_index: null,
    pixel_count: null,
    revision: 1,
    created_at: "2026-10-14T12:00:00Z",
    updated_at: "2026-10-14T12:00:00Z",
  };
}
