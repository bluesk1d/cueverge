import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import {
  editShowObject, getSceneObjects, getShowObjectSummary, getShowObjects, setSceneObjects, showObjectAction,
  type ProjectDraftRecord,
} from "../api";
import { SceneObjectsPanel } from "./SceneObjectsPanel";
import { ShowObjectsPanel } from "./ShowObjectsPanel";

vi.mock("../api", () => ({
  editShowObject: vi.fn(), getSceneObjects: vi.fn(), getShowObjectSummary: vi.fn(), getShowObjects: vi.fn(),
  setSceneObjects: vi.fn(), showObjectAction: vi.fn(),
}));

const draft = {
  project: { id: "show" }, revision: 7,
  draft: { installation: { resources: [{ resource: "roof", kind: "pixel_output", segments: [{ id: "eave", start: 0, end: 8 }] }] } },
} as unknown as ProjectDraftRecord;
const porch = { resource: "roof", kind: "fixture" as const, id: "porch", name: "Porch", owner: "installation" as const, targeted: false };
const mouth = { resource: "roof", kind: "fixture" as const, id: "mouth", name: "Klown mouth", owner: "show" as const, targeted: true };

beforeEach(() => {
  vi.resetAllMocks();
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

it("adds installation objects to the scene only and protects targeted ones", async () => {
  vi.mocked(getSceneObjects).mockResolvedValue({ project_id: "show", revision: 7, scene_id: "intro", objects: [mouth], available: [porch] });
  vi.mocked(setSceneObjects).mockResolvedValue(draft);
  const changed = vi.fn();
  render(<SceneObjectsPanel draft={draft} sceneId="intro" canEdit disabledReason={null} onChanged={changed} />);
  expect(await screen.findByRole("button", { name: "Remove Klown mouth from scene" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Add an object"), { target: { value: "roof/fixture/porch" } });
  fireEvent.click(screen.getByRole("button", { name: "Add to scene" }));
  await waitFor(() => expect(setSceneObjects).toHaveBeenCalledWith("show", "intro", 7, [
    { resource: "roof", kind: "fixture", id: "mouth" }, { resource: "roof", kind: "fixture", id: "porch" },
  ]));
  expect(changed).toHaveBeenCalledWith(draft);
});

it("creates a show fixture straight into the scene", async () => {
  vi.mocked(getSceneObjects).mockResolvedValue({ project_id: "show", revision: 7, scene_id: "intro", objects: [], available: [] });
  vi.mocked(editShowObject).mockResolvedValue(draft);
  render(<SceneObjectsPanel draft={draft} sceneId="intro" canEdit disabledReason={null} onChanged={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "New show fixture or path" }));
  fireEvent.change(screen.getByLabelText("Stable object ID"), { target: { value: "mouth" } });
  fireEvent.change(screen.getByLabelText("Object name"), { target: { value: "Klown mouth" } });
  fireEvent.click(screen.getByRole("button", { name: "Add member segment" }));
  fireEvent.change(screen.getByLabelText("Member segment 1"), { target: { value: "eave" } });
  fireEvent.click(screen.getByRole("button", { name: "Create and add to scene" }));
  await waitFor(() => expect(editShowObject).toHaveBeenCalledWith("show", 7, "create", expect.objectContaining({
    id: "mouth", scene_id: "intro", segments: [{ segment: "eave", reverse: false }],
  })));
});

it("summarizes scene usage read-only and manages only the show's own objects", async () => {
  vi.mocked(getShowObjectSummary).mockResolvedValue({
    project_id: "show", revision: 7, inherits: true,
    scenes: [{ scene_id: "intro", name: "Intro", devices: ["roof"], objects: [{ resource: "roof", kind: "fixture", id: "porch" }] }],
    devices: [{ resource: "roof", kind: "pixel_output", scene_ids: ["intro"] }],
    objects: [{ resource: "roof", kind: "fixture", id: "porch", name: "Porch", owner: "installation", scene_ids: ["intro"] }],
  });
  const own = { resource: "roof", kind: "fixture" as const, id: "mouth", name: "Klown mouth", segments: [{ segment: "eave", reverse: false }],
    precedence: 0, geometry_semantic_ids: [], pixel_count: 8, traversal: [], references: [], geometry_ids: [],
    missing_geometry_semantic_ids: [], owner: "show" as const, scene_ids: [], used_by: [] };
  vi.mocked(getShowObjects).mockResolvedValue({
    project_id: "show", project_name: "Halloween", revision: 7, archived: false, installation_id: "home",
    layout_available: true, resources: [], editable_kinds: ["fixture", "path"], inherits: true, geometry: [],
    objects: [own, { ...own, id: "porch", name: "Porch", owner: "installation" }],
  });
  vi.mocked(showObjectAction).mockResolvedValue(draft);
  const opened = vi.fn();
  render(<ShowObjectsPanel draft={draft} canEdit canEditInstallation onChanged={vi.fn()} onOpenScene={opened} />);
  expect(await screen.findByText("Objects: Porch")).toBeInTheDocument();
  expect(screen.getByText(/1 from the installation, 0 show-only/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Edit Porch" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Open scene Intro" }));
  expect(opened).toHaveBeenCalledWith("intro");
  fireEvent.click(screen.getByRole("button", { name: "Promote Klown mouth to installation" }));
  await waitFor(() => expect(showObjectAction).toHaveBeenCalledWith("show", 7, "promote", { resource: "roof", kind: "fixture", id: "mouth" }));
  fireEvent.click(screen.getByRole("button", { name: "Remove unused show objects (1)" }));
  await waitFor(() => expect(showObjectAction).toHaveBeenCalledWith("show", 7, "remove-unused"));
});
