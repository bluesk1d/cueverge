import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ShowsPage } from "./App";
import * as api from "./api";

afterEach(() => vi.restoreAllMocks());

test("refreshes external scene edits in place and protects unsaved changes", async () => {
  const project = { id: "p", name: "Test show", head_revision: 1, metadata_revision: 1, archived_at: null, created_at: "", updated_at: "" };
  const scene = (id: string) => ({ schema_version: 2 as const, id, name: id, duration_ms: 10000, resources: [], media: [], lighting: [] });
  const draft = { project, revision: 1, draft: { schema_version: 1, installation: { schema_version: 1, resources: [] }, editor_metadata: {}, scenes: [scene("First"), scene("Second")] } } as unknown as api.ProjectDraftRecord;
  vi.spyOn(api, "getAssets").mockResolvedValue([]);
  vi.spyOn(api, "getTrashedAssets").mockResolvedValue([]);
  const load = vi.spyOn(api, "getProjectDraft").mockResolvedValue(draft);
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 404 }));
  render(<ShowsPage session={{ username: "admin", role: "administrator" }} initialProjects={[project]} />);
  fireEvent.click(screen.getByRole("button", { name: "Open show Test show" }));
  await waitFor(() => expect(load).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole("button", { name: "Scenes" }));
  fireEvent.click(await screen.findByRole("button", { name: /Second.*media/ }));
  await screen.findByRole("heading", { name: "Second" });
  load.mockResolvedValue({ ...draft, revision: 2, draft: { ...draft.draft, scenes: [scene("First"), { ...scene("Second"), name: "Updated externally" }] } });
  fireEvent.click(screen.getByRole("button", { name: "Refresh show" }));
  expect(await screen.findByRole("heading", { name: "Updated externally" })).toBeInTheDocument();
  load.mockRejectedValue(new Error("Offline"));
  fireEvent.click(screen.getByRole("button", { name: "Refresh show" }));
  await waitFor(() => expect(screen.getAllByText("Saved scene data could not be refreshed.").length).toBeGreaterThan(0));
  expect(screen.getByRole("heading", { name: "Updated externally" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Scene name"), { target: { value: "Local edits" } });
  expect(screen.getByRole("button", { name: "Refresh show" })).toBeDisabled();
  expect(screen.getByLabelText("Scene name")).toHaveValue("Local edits");
});



