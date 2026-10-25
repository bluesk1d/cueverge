import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ApiError, ProjectDraftRecord } from "../api";
import { SceneWorkspace } from "./SceneWorkspace";

afterEach(() => {
  vi.restoreAllMocks();
});

const scene = (id: string, name: string) => ({
  schema_version: 2, id, name, duration_ms: 10_000, resources: [], media: [], lighting: [],
  clock_source: "controller", mode: "controller_timed",
});

const draft = {
  project: { id: "project-1", name: "Halloween", head_revision: 6, metadata_revision: 1, archived_at: null, created_at: "", updated_at: "" },
  revision: 6,
  draft: {
    schema_version: 1,
    installation: { schema_version: 1, resources: [] },
    editor_metadata: {},
    scenes: [scene("klown", "Klown Ship"), scene("shorty", "Shorty's Ambush")],
  },
} as unknown as ProjectDraftRecord;

test("shows a refused scene deletion beside the Delete scene button", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 404 }));
  const onDelete = vi.fn().mockRejectedValue(new ApiError(
    422,
    "Cueverge API request failed (422): Scene is still used by: the Show plan shuffle list (preshow); "
      + "remove it there, adding another scene first if it is the only one. Update these references first.",
  ));
  render(<SceneWorkspace
    draft={draft} sceneId="klown" canEdit canAdd assets={[]}
    onSelectScene={vi.fn()} onSave={vi.fn()} onDirtyChange={vi.fn()}
    onOpenTimeline={vi.fn()} onDelete={onDelete} onCreated={vi.fn()}
  />);

  const deleteButton = screen.getByRole("button", { name: "Delete scene" });
  fireEvent.click(deleteButton);

  await waitFor(() => expect(onDelete).toHaveBeenCalledWith("klown"));
  const controls = deleteButton.closest(".scene-save-controls");
  if (!(controls instanceof HTMLElement)) throw new Error("Delete controls are missing");
  const alert = await within(controls).findByRole("alert");
  expect(alert).toHaveTextContent(
    "\"Klown Ship\" was not deleted. Scene is still used by: the Show plan shuffle list (preshow)",
  );
  expect(alert).not.toHaveTextContent("Cueverge API request failed");
  expect(deleteButton).toBeEnabled();
});
