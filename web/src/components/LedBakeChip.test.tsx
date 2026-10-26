import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import * as api from "../api";
import type { LightingBakeStatus } from "../api";
import { LedBakeChip } from "./LedBakeChip";

const status = (overrides: Partial<LightingBakeStatus> = {}): LightingBakeStatus => ({
  composition_id: "ship",
  name: "External ship lights",
  space_kind: "stage",
  state: "missing",
  reusable_across_revisions: true,
  artifact_id: null,
  baked_revision: null,
  job_id: null,
  progress: null,
  error: null,
  scene_ids: ["ambient"],
  ambient: false,
  ...overrides,
});

afterEach(() => vi.restoreAllMocks());

test("flags a missing LED bake for the scene and bakes it from the chip", async () => {
  const lookup = vi.spyOn(api, "getLightingBakeStatus")
    .mockResolvedValueOnce({ project_id: "p", revision: 7, compositions: [
      status(), status({ composition_id: "other", name: "Other scene", scene_ids: ["finale"] }),
    ] })
    .mockResolvedValue({ project_id: "p", revision: 7, compositions: [
      status({ state: "current", artifact_id: "a", baked_revision: 6 }),
    ] });
  const create = vi.spyOn(api, "createRenderJob").mockResolvedValue({} as api.RenderJob);
  render(<LedBakeChip projectId="p" revision={7} sceneId="ambient" canBake dirty={false}
    onOpenBakeSettings={() => undefined} />);
  const chip = await screen.findByRole("button", { name: /LED bake needed/ });
  expect(lookup).toHaveBeenCalledWith("p", 7);
  fireEvent.click(chip);
  expect(screen.queryByText("Other scene")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Bake now" }));
  await waitFor(() => expect(create).toHaveBeenCalledWith("p", "ship"));
  expect(await screen.findByRole("button", { name: /LED bake current/ })).toBeTruthy();
  expect(screen.getByText(/from revision 6/)).toBeTruthy();
});

test("opens bake settings and blocks baking while the scene has unsaved edits", async () => {
  vi.spyOn(api, "getLightingBakeStatus").mockResolvedValue({
    project_id: "p", revision: 7, compositions: [status({ state: "failed", error: "mapping changed" })],
  });
  const open = vi.fn();
  const { rerender } = render(<LedBakeChip projectId="p" revision={7} sceneId="ambient" canBake
    dirty onOpenBakeSettings={open} />);
  fireEvent.click(await screen.findByRole("button", { name: /LED bake failed/ }));
  expect(screen.getByText("mapping changed")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Bake now" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Bake settings" }) as HTMLButtonElement).disabled).toBe(true);
  rerender(<LedBakeChip projectId="p" revision={7} sceneId="ambient" canBake dirty={false}
    onOpenBakeSettings={open} />);
  fireEvent.click(screen.getByRole("button", { name: "Bake settings" }));
  expect(open).toHaveBeenCalledWith(expect.objectContaining({ composition_id: "ship" }));
});

test("renders nothing for scenes without LED compositions", async () => {
  const lookup = vi.spyOn(api, "getLightingBakeStatus").mockResolvedValue({
    project_id: "p", revision: 7, compositions: [status({ scene_ids: ["finale"] })],
  });
  const { container } = render(<LedBakeChip projectId="p" revision={7} sceneId="ambient" canBake
    dirty={false} onOpenBakeSettings={() => undefined} />);
  await waitFor(() => expect(lookup).toHaveBeenCalled());
  expect(container.textContent).toBe("");
});

test("warns when the ambient composition is also placed on this scene's timeline", async () => {
  vi.spyOn(api, "getLightingBakeStatus").mockResolvedValue({
    project_id: "p", revision: 7, compositions: [status({
      state: "current", ambient: true, scene_ids: ["ambient", "finale"], ambient_scene_timeline_ids: ["ambient"],
    })],
  });
  const { rerender } = render(<LedBakeChip projectId="p" revision={7} sceneId="ambient" canBake
    dirty={false} onOpenBakeSettings={() => undefined} />);
  const note = await screen.findByRole("note");
  expect(note.textContent).toMatch(/External ship lights is the show.s ambient lighting but also placed on this scene.s timeline/);
  rerender(<LedBakeChip projectId="p" revision={7} sceneId="finale" canBake
    dirty={false} onOpenBakeSettings={() => undefined} />);
  expect(screen.queryByRole("note")).toBeNull();
});
