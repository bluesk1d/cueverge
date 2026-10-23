import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import {
  editInstallationObject, getInstallationObjects, getProjectMembers, getProjects,
  type InstallationObjectInventory,
} from "../api";
import { InstallationObjects } from "./InstallationObjects";

vi.mock("../api", () => ({
  editInstallationObject: vi.fn(), getInstallationObjects: vi.fn(),
  getProjectMembers: vi.fn(), getProjects: vi.fn(),
}));
const fixture: InstallationObjectInventory["objects"][number] = {
  resource: "roof", kind: "fixture", id: "porch", name: "Porch outline",
  segments: [{ segment: "left", reverse: false }, { segment: "right", reverse: true }],
  precedence: 20, geometry_semantic_ids: [], pixel_count: 8,
  traversal: [
    { segment: "left", first_pixel: 0, last_pixel: 2, pixel_count: 3, reverse: false },
    { segment: "right", first_pixel: 7, last_pixel: 3, pixel_count: 5, reverse: true },
  ],
  geometry_ids: [], references: [], missing_geometry_semantic_ids: [],
};
const inventory: InstallationObjectInventory = {
  project_id: "show", project_name: "Halloween", revision: 4, archived: false,
  installation_id: "home", layout_available: true, editable_kinds: ["fixture", "path"],
  resources: [{ resource: "roof", kind: "pixel_output", target: "memory", pixel_count: 8,
    segments: [{ id: "left", start: 0, end: 3 }, { id: "right", start: 3, end: 8 }] }],
  objects: [fixture],
  geometry: [
    { semantic_id: "marker", name: "Porch marker", kind: "fixture", resource: null, geometry_ids: ["shape"] },
    { semantic_id: "foreign", name: "Another output", kind: "pixel_segment", resource: "other", geometry_ids: ["other"] },
  ],
};
const onLayout = vi.fn(), onDirtyChange = vi.fn();
function Harness({ role = "administrator" }: { role?: "administrator" | "viewer" }) {
  const [projectId, setProjectId] = useState("");
  return <InstallationObjects session={{ username: "admin", role }} projectId={projectId}
    onProjectChange={setProjectId} onDirtyChange={onDirtyChange} onLayout={onLayout} />;
}
async function open(role?: "administrator" | "viewer") {
  render(<Harness role={role} />);
  await screen.findByRole("option", { name: "Halloween" });
  expect(getInstallationObjects).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole("combobox", { name: "Definitions from show" }), { target: { value: "show" } });
  await screen.findByRole("button", { name: "Inspect Porch outline" });
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getProjects).mockResolvedValue([{
    id: "show", name: "Halloween", head_revision: 4, metadata_revision: 1,
    archived_at: null, created_at: "", updated_at: "",
  }]);
  vi.mocked(getInstallationObjects).mockResolvedValue(structuredClone(inventory));
  vi.mocked(getProjectMembers).mockResolvedValue([]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});
describe("Installation object inventory", () => {
  it("shows unplaced MCP fixtures, edits stable membership and links without replacing the draft", async () => {
    await open();
    expect(screen.getByText(/Not placed/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Place in Layout" }));
    const inspector = screen.getByRole("region", { name: "Object properties" });
    expect(within(inspector).getByLabelText("Stable object ID")).toBeDisabled();
    expect(within(inspector).queryByLabelText(/Another output/)).not.toBeInTheDocument();
    fireEvent.change(within(inspector).getByLabelText("Object name"), { target: { value: "Roof group" } });
    fireEvent.click(within(inspector).getByRole("button", { name: "Move member 2 up" }));
    fireEvent.click(within(inspector).getByLabelText("Reverse member 1"));
    fireEvent.click(within(inspector).getByLabelText(/Porch marker/));
    expect(within(inspector).getByRole("list", { name: "Pixel traversal preview" })).toHaveTextContent("right: 3 → 7 (5 pixels)");
    fireEvent.click(within(inspector).getByRole("button", { name: "Save object" }));
    await waitFor(() => expect(editInstallationObject).toHaveBeenCalledWith("show", 4, "update", {
      resource: "roof", kind: "fixture", id: "porch", name: "Roof group",
      segments: [{ segment: "right", reverse: false }, { segment: "left", reverse: false }],
      precedence: 20, geometry_semantic_ids: ["marker"],
    }));
    expect(await screen.findByRole("status")).toHaveTextContent("Live playback is unchanged");
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
  });
  it("highlights mapped membership and blocks referenced deletion", async () => {
    vi.mocked(getInstallationObjects).mockResolvedValue({
      ...inventory, objects: [{ ...fixture, geometry_ids: ["shape"], references: ["/scenes/ambient/lighting"] }],
    });
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Show in Layout" }));
    expect(onLayout).toHaveBeenCalledWith({ name: "Porch outline", geometryIds: ["shape"] });
    fireEvent.click(screen.getByRole("button", { name: "Inspect Porch outline" }));
    expect(screen.getByRole("button", { name: "Delete definition" })).toBeDisabled();
    expect(screen.getByText("/scenes/ambient/lighting")).toBeInTheDocument();
  });
  it("retains unsaved edits on conflicts and guards refresh/navigation", async () => {
    vi.mocked(editInstallationObject).mockRejectedValue(new Error("Revision conflict (409)"));
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Inspect Porch outline" }));
    fireEvent.change(screen.getByLabelText("Object name"), { target: { value: "Unsaved" } });
    fireEvent.click(screen.getByRole("button", { name: "Save object" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Revision conflict");
    expect(screen.getByLabelText("Object name")).toHaveValue("Unsaved");
    vi.mocked(window.confirm).mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: "Refresh objects" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Definitions from show" }), { target: { value: "" } });
    expect(screen.getByLabelText("Object name")).toHaveValue("Unsaved");
    expect(getInstallationObjects).toHaveBeenCalledTimes(1);
  });
  it("lets viewers inspect but not change objects or layout associations", async () => {
    await open("viewer");
    expect(screen.queryByRole("button", { name: "Create fixture or path" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Inspect Porch outline" }));
    expect(screen.getByLabelText("Object name")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save object" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete definition" })).not.toBeInTheDocument();
  });
  it("does not replay a saved mutation when inventory refresh fails", async () => {
    vi.mocked(getInstallationObjects).mockResolvedValueOnce(structuredClone(inventory))
      .mockRejectedValueOnce(new Error("Refresh unavailable"));
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Inspect Porch outline" }));
    fireEvent.change(screen.getByLabelText("Object name"), { target: { value: "Saved name" } });
    fireEvent.click(screen.getByRole("button", { name: "Save object" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Object saved, but inventory refresh failed");
    expect(screen.queryByRole("button", { name: "Save object" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Refresh objects" }));
    await screen.findByRole("button", { name: "Inspect Porch outline" });
    expect(editInstallationObject).toHaveBeenCalledTimes(1);
  });
  it("creates an ordered path and deletes an unused definition safely", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Create fixture or path" }));
    fireEvent.change(screen.getByLabelText("Object type"), { target: { value: "path" } });
    fireEvent.change(screen.getByLabelText("Stable object ID"), { target: { value: "walk" } });
    fireEvent.change(screen.getByLabelText("Object name"), { target: { value: "Walk" } });
    fireEvent.click(screen.getByRole("button", { name: "Add member segment" }));
    fireEvent.change(screen.getByLabelText("Member segment 1"), { target: { value: "left" } });
    fireEvent.click(screen.getByRole("button", { name: "Save object" }));
    await waitFor(() => expect(editInstallationObject).toHaveBeenCalledWith("show", 4, "create", expect.objectContaining({
      kind: "path", id: "walk", segments: [{ segment: "left", reverse: false }], precedence: 0,
    })));
    await screen.findByRole("status");
    fireEvent.click(screen.getByRole("button", { name: "Inspect Porch outline" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete definition" }));
    await waitFor(() => expect(editInstallationObject).toHaveBeenLastCalledWith("show", 4, "delete", {
      resource: "roof", kind: "fixture", id: "porch",
    }));
  });
});
