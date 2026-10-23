import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { ProjectDraftRecord, ShowPlan as ShowPlanData } from "../api";
import * as api from "../api";
import { ShowPlan } from "./ShowPlan";

vi.mock("../api", async (original) => ({
  ...(await original<typeof import("../api")>()),
  getShowPlan: vi.fn(),
  updateShowPlan: vi.fn(),
}));

const scene = (id: string, name: string, duration_ms: number) => ({
  schema_version: 2, id, name, duration_ms, resources: ["roofline"], media: [],
});
const draft = {
  project: { id: "project-1", name: "Halloween", head_revision: 3, metadata_revision: 1, archived_at: null, created_at: "", updated_at: "" },
  revision: 3,
  draft: {
    schema_version: 1,
    installation: { schema_version: 1, resources: [] },
    editor_metadata: {},
    scenes: [scene("intro", "Spooky intro", 30_000), scene("klown", "Klown ship", 180_000), scene("jump", "Jump scare", 8_000), scene("spare", "Spare test", 5_000)],
  },
} as unknown as ProjectDraftRecord;

const plan: ShowPlanData = {
  source_revision: 3,
  starting_scene_id: "intro",
  main_show: {
    scenes: [
      { id: "intro", name: "Spooky intro", duration_ms: 30_000, ending: { kind: "play_next", next_scene_id: "klown", label: "Then plays Klown ship" } },
      { id: "klown", name: "Klown ship", duration_ms: 180_000, ending: { kind: "shuffle", next_scene_id: null, label: "Then the show picks the next scene" } },
    ],
    end: { kind: "shuffle", scene_id: "klown" },
  },
  shuffle_list: {
    pool_id: "main",
    scenes: [
      { id: "klown", name: "Klown ship", duration_ms: 180_000, ending: { kind: "shuffle", next_scene_id: null, label: "Then the show picks the next scene" }, weight: 3, chance_percent: 75, available: true, has_extra_rules: false },
      { id: "intro", name: "Spooky intro", duration_ms: 30_000, ending: { kind: "play_next", next_scene_id: "klown", label: "Then plays Klown ship" }, weight: 1, chance_percent: 25, available: true, has_extra_rules: true },
    ],
  },
  visitor_responses: [{ key: "response:welcome", kind: "response", name: "Welcome", when: "Visitors arrive", scene_id: "jump", scene_name: "Jump scare", afterward: "Then considers waiting visitor responses and the normal show selection." }],
  unused_scenes: [{ id: "spare", name: "Spare test", duration_ms: 5_000, ending: { kind: "freeze", next_scene_id: null, label: "Stays on the last moment" } }],
  ending_choices: [
    { kind: "repeat", label: "Repeat this scene" },
    { kind: "play_next", label: "Play a specific scene next" },
    { kind: "shuffle", label: "Let the show pick next (shuffle list and visitor responses)" },
    { kind: "freeze", label: "Stay on the last moment" },
    { kind: "stop", label: "Stop the show" },
  ],
  warnings: [],
};

beforeEach(() => {
  vi.mocked(api.getShowPlan).mockResolvedValue(structuredClone(plan));
  vi.mocked(api.updateShowPlan).mockResolvedValue({ ...draft, revision: 4 });
});

test("shows main order, shuffle list, visitor lanes and unused scenes with drill-down", async () => {
  const onOpenScene = vi.fn();
  const onVisitorResponses = vi.fn();
  render(<ShowPlan draft={draft} canEdit onUpdated={vi.fn()} onOpenScene={onOpenScene} onVisitorResponses={onVisitorResponses} />);
  const order = await screen.findByRole("list", { name: "Main show order" });
  const steps = within(order).getAllByRole("button", { name: /^Open scene / });
  expect(steps.map((step) => step.querySelector("strong")?.textContent)).toEqual(["Spooky intro", "Klown ship"]);
  expect(within(order).getByText(/Then picks from the shuffle list/)).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Starting scene" })).toHaveValue("intro");
  expect(screen.getByRole("combobox", { name: "Scene after Spooky intro" })).toHaveValue("klown");
  expect(screen.getByText("About 75% of picks")).toBeInTheDocument();
  expect(screen.getByText(/Has extra rules from advanced settings/)).toBeInTheDocument();
  const lanes = screen.getByRole("list", { name: "Visitor response lanes" });
  expect(within(lanes).getByText("When visitors arrive")).toBeInTheDocument();
  fireEvent.click(within(lanes).getByRole("button", { name: /^Open scene Jump scare/ }));
  expect(onOpenScene).toHaveBeenCalledWith("jump");
  fireEvent.click(screen.getByRole("button", { name: /^Open scene Spare test/ }));
  expect(onOpenScene).toHaveBeenLastCalledWith("spare");
  fireEvent.click(screen.getByRole("button", { name: "Edit visitor responses" }));
  expect(onVisitorResponses).toHaveBeenCalled();
});

test("saves scene endings, starting scene and shuffle list as focused revision-bound edits", async () => {
  const onUpdated = vi.fn();
  render(<ShowPlan draft={draft} canEdit onUpdated={onUpdated} onOpenScene={vi.fn()} onVisitorResponses={vi.fn()} />);
  fireEvent.change(await screen.findByRole("combobox", { name: "When Klown ship ends" }), { target: { value: "repeat" } });
  await waitFor(() => expect(api.updateShowPlan).toHaveBeenLastCalledWith("project-1", {
    source_revision: 3, operation: "scene_ending", scene_id: "klown", ending: "repeat",
  }));
  expect(await screen.findByText(/Saved\. Your live show changes only after you apply/)).toBeInTheDocument();
  expect(onUpdated).toHaveBeenCalledWith({ ...draft, revision: 4 });

  fireEvent.change(screen.getByRole("combobox", { name: "Scene after Spooky intro" }), { target: { value: "jump" } });
  await waitFor(() => expect(api.updateShowPlan).toHaveBeenLastCalledWith("project-1", {
    source_revision: 3, operation: "scene_ending", scene_id: "intro", ending: "play_next", next_scene_id: "jump",
  }));
  fireEvent.change(screen.getByRole("combobox", { name: "Starting scene" }), { target: { value: "klown" } });
  await waitFor(() => expect(api.updateShowPlan).toHaveBeenLastCalledWith("project-1", {
    source_revision: 3, operation: "starting_scene", scene_id: "klown",
  }));
  fireEvent.change(screen.getByRole("combobox", { name: "How often Spooky intro is picked" }), { target: { value: "5" } });
  await waitFor(() => expect(api.updateShowPlan).toHaveBeenLastCalledWith("project-1", {
    source_revision: 3, operation: "shuffle_list", shuffle: [{ scene_id: "klown", weight: 3 }, { scene_id: "intro", weight: 5 }],
  }));
  fireEvent.click(screen.getByRole("button", { name: "Remove Klown ship from the shuffle list" }));
  await waitFor(() => expect(api.updateShowPlan).toHaveBeenLastCalledWith("project-1", {
    source_revision: 3, operation: "shuffle_list", shuffle: [{ scene_id: "intro", weight: 1 }],
  }));
  fireEvent.change(screen.getByRole("combobox", { name: "Scene to add to the shuffle list" }), { target: { value: "spare" } });
  fireEvent.click(screen.getByRole("button", { name: "Add to shuffle list" }));
  await waitFor(() => expect(api.updateShowPlan).toHaveBeenLastCalledWith("project-1", {
    source_revision: 3, operation: "shuffle_list", shuffle: [{ scene_id: "klown", weight: 3 }, { scene_id: "intro", weight: 1 }, { scene_id: "spare", weight: 1 }],
  }));
});

test("read-only viewers and failed saves are explicit", async () => {
  const { unmount } = render(<ShowPlan draft={draft} canEdit={false} onUpdated={vi.fn()} onOpenScene={vi.fn()} onVisitorResponses={vi.fn()} />);
  expect(await screen.findByRole("combobox", { name: "When Klown ship ends" })).toBeDisabled();
  for (const open of screen.getAllByRole("button", { name: /^Open scene Klown ship/ })) expect(open).toBeEnabled();
  unmount();
  vi.mocked(api.updateShowPlan).mockRejectedValueOnce(new Error("Choose which scene plays next"));
  render(<ShowPlan draft={draft} canEdit onUpdated={vi.fn()} onOpenScene={vi.fn()} onVisitorResponses={vi.fn()} />);
  fireEvent.change(await screen.findByRole("combobox", { name: "When Klown ship ends" }), { target: { value: "stop" } });
  expect(await screen.findByRole("alert")).toHaveTextContent("Choose which scene plays next");
});
