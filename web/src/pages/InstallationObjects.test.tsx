import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  editInstallationObject, getInstallationObjects, setInstallationSegments,
  type InstallationLevelObjects, type InstallationObjectRecord,
} from "../api";
import { InstallationObjects } from "./InstallationObjects";

vi.mock("../api", () => ({
  editInstallationObject: vi.fn(), getInstallationObjects: vi.fn(), setInstallationSegments: vi.fn(),
}));

const record = (patch: Partial<InstallationObjectRecord>): InstallationObjectRecord => ({
  resource: "roof", kind: "fixture", id: "porch", name: "Porch outline",
  segments: [{ segment: "left", reverse: false }, { segment: "right", reverse: true }],
  precedence: 20, geometry_semantic_ids: [], pixel_count: 8,
  traversal: [
    { segment: "left", first_pixel: 0, last_pixel: 2, pixel_count: 3, reverse: false },
    { segment: "right", first_pixel: 7, last_pixel: 3, pixel_count: 5, reverse: true },
  ],
  geometry_ids: [], references: [], missing_geometry_semantic_ids: [],
  owner: "installation", scene_ids: [], used_by: [], ...patch,
});
const inventory: InstallationLevelObjects = {
  installation_id: "home", revision: 4, layout_available: true,
  resources: [{ resource: "roof", pixel_count: 8, segments: [{ id: "left", start: 0, end: 3 }, { id: "right", start: 3, end: 8 }] }],
  objects: [
    record({}),
    record({ kind: "segment", id: "left", name: "Garage", segments: [{ segment: "left", reverse: false }], pixel_count: 3,
      traversal: [{ segment: "left", first_pixel: 0, last_pixel: 2, pixel_count: 3, reverse: false }] }),
  ],
  geometry: [{ semantic_id: "marker", name: "Porch marker", kind: "fixture", resource: null, geometry_ids: ["shape"] }],
};
const onLayout = vi.fn(), onDirtyChange = vi.fn();

async function open(role: "administrator" | "viewer" = "administrator") {
  render(<InstallationObjects session={{ username: "admin", role }} installationId="home"
    onDirtyChange={onDirtyChange} onLayout={onLayout} />);
  await screen.findByRole("button", { name: "Inspect Porch outline" });
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getInstallationObjects).mockResolvedValue(structuredClone(inventory));
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("Installation objects", () => {
  it("edits a reusable fixture with a pixel range member without a show selector", async () => {
    vi.mocked(editInstallationObject).mockResolvedValue(structuredClone(inventory));
    await open();
    expect(screen.queryByRole("combobox", { name: "Definitions from show" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Inspect Porch outline" }));
    const inspector = screen.getByRole("region", { name: "Object properties" });
    fireEvent.click(within(inspector).getByLabelText("Only part of right"));
    fireEvent.change(within(inspector).getByLabelText("First pixel of member 2"), { target: { value: "2" } });
    fireEvent.change(within(inspector).getByLabelText("Last pixel of member 2"), { target: { value: "4" } });
    expect(within(inspector).getByRole("list", { name: "Pixel traversal preview" })).toHaveTextContent("right: 6 → 4 (3 pixels)");
    fireEvent.click(within(inspector).getByLabelText(/Porch marker/));
    fireEvent.click(within(inspector).getByRole("button", { name: "Save object" }));
    await waitFor(() => expect(editInstallationObject).toHaveBeenCalledWith("home", 4, "update", expect.objectContaining({
      id: "porch", segments: [{ segment: "left", reverse: false }, { segment: "right", reverse: true, start: 1, end: 4 }],
      geometry_semantic_ids: ["marker"],
    })));
    expect(await screen.findByRole("status")).toHaveTextContent("Every inheriting show");
  });

  it("shows which shows use an object and blocks deleting it", async () => {
    vi.mocked(getInstallationObjects).mockResolvedValue({ ...inventory, objects: [record({
      geometry_ids: ["shape"], used_by: [{ project_id: "show", project_name: "Halloween", scene_ids: ["intro"] }],
    })] });
    await open();
    expect(screen.getByText("1 show")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Inspect Porch outline" }));
    expect(screen.getByText("Halloween: intro")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete definition" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Show in Layout" }));
    expect(onLayout).toHaveBeenCalledWith({ name: "Porch outline", geometryIds: ["shape"] });
  });

  it("replaces an output's wiring with a full contiguous partition", async () => {
    vi.mocked(setInstallationSegments).mockResolvedValue(structuredClone(inventory));
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Edit wiring for roof" }));
    const save = screen.getByRole("button", { name: "Save wiring" });
    fireEvent.change(screen.getByLabelText("Segment 2 pixel count"), { target: { value: "4" } });
    expect(save).toBeDisabled();
    expect(screen.getByText("7 of 8 pixels assigned.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add segment" }));
    fireEvent.change(screen.getByLabelText("Segment 3 ID"), { target: { value: "tip" } });
    fireEvent.click(save);
    await waitFor(() => expect(setInstallationSegments).toHaveBeenCalledWith("home", 4, {
      resource: "roof",
      segments: [{ id: "left", start: 0, end: 3 }, { id: "right", start: 3, end: 7 }, { id: "tip", start: 7, end: 8 }],
      names: { left: "Garage" },
    }));
  });

  it("lets viewers inspect but not change installation objects", async () => {
    await open("viewer");
    expect(screen.queryByRole("button", { name: "Create installation fixture or path" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit wiring for roof" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Inspect Porch outline" }));
    expect(screen.getByLabelText("Object name")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save object" })).not.toBeInTheDocument();
  });
});
