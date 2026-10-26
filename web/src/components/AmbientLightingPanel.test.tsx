import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import * as api from "../api";
import { ProjectDraftRecord } from "../api";
import { AmbientLightingPanel } from "./AmbientLightingPanel";

const draft = (ambient?: unknown) => ({
  project: { id: "project-1", name: "Ship", head_revision: 3, metadata_revision: 1, archived_at: null, created_at: "", updated_at: "" },
  revision: 3,
  draft: {
    schema_version: 1,
    installation: { schema_version: 1, resources: [
      { resource: "projector", kind: "media_agent", target: "p", pixel_count: null },
      { resource: "roof", kind: "pixel_output", target: "memory", pixel_count: 8 },
    ] },
    editor_metadata: {
      compositions: {
        loop: { name: "Ambient loop", space_kind: "stage", stage_lighting_surfaces: ["roof-line"] },
        video: { name: "Projector only", space_kind: "stage", stage_lighting_surfaces: [] },
      },
      ...(ambient ? { ambient_lighting: ambient } : {}),
    },
    scenes: [
      { schema_version: 2, id: "intro", name: "Intro", duration_ms: 1000, media: [], lighting: [] },
      { schema_version: 2, id: "finale", name: "Finale", duration_ms: 1000, media: [], lighting: [] },
    ],
  },
}) as unknown as ProjectDraftRecord;

test("authors a simple ambient look and scene inheritance for an LED output", async () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(<AmbientLightingPanel draft={draft()} canEdit onSave={onSave} />);
  fireEvent.click(screen.getByText("Ambient lighting"));
  expect(screen.queryByText(/projector/)).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "Ambient lighting on roof" }));
  fireEvent.change(screen.getByLabelText("Look"), { target: { value: "pulse" } });
  fireEvent.change(screen.getByLabelText("Loop length (seconds)"), { target: { value: "4" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "Finale" }));
  fireEvent.click(screen.getByRole("button", { name: "Save ambient lighting" }));
  await waitFor(() => expect(onSave).toHaveBeenCalled());
  const ambient = onSave.mock.calls[0][0].editor_metadata.ambient_lighting;
  expect(ambient.excluded_scene_ids).toEqual(["finale"]);
  expect(ambient.outputs).toHaveLength(1);
  expect(ambient.outputs[0]).toMatchObject({ resource: "roof", cycle_ms: 4000 });
  expect(ambient.outputs[0].cues[0]).toMatchObject({ primitive: "pulse", start_ms: 0, duration_ms: 4000 });
});

test("lists every MCP effect beside a simple look and keeps them when looping a composition", async () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  const extra = { id: "mcp-extra", primitive: "ripple", start_ms: 0, duration_ms: 1000 };
  render(<AmbientLightingPanel canEdit onSave={onSave} draft={draft({
    schema_version: 1, excluded_scene_ids: [],
    outputs: [{ resource: "roof", cycle_ms: 2000, cues: [
      { id: "ambient", primitive: "solid", start_ms: 0, duration_ms: 2000, color: "#112233" }, extra,
    ] }],
  })} />);
  fireEvent.click(screen.getByText("Ambient lighting"));
  const list = screen.getByRole("region", { name: "Ambient effects on roof" });
  expect(within(list).getByRole("button", { name: "Edit effect ambient" })).toBeInTheDocument();
  expect(within(list).getByRole("button", { name: "Edit effect mcp-extra" })).toBeInTheDocument();
  const inspector = screen.getByRole("group", { name: "Selected ambient effect on roof" });
  fireEvent.change(within(inspector).getByLabelText("Effect brightness (%)"), { target: { value: "50" } });
  fireEvent.click(screen.getByRole("button", { name: "Save ambient lighting" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  const cues = onSave.mock.calls[0][0].editor_metadata.ambient_lighting.outputs[0].cues;
  expect(cues[0].brightness).toBe(0.5);
  expect(cues[1]).toEqual(extra);
  fireEvent.change(screen.getByLabelText("Source"), { target: { value: "composition" } });
  expect(screen.getByLabelText("Composition")).toHaveValue("loop");
  expect(screen.queryByRole("option", { name: "Projector only" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save ambient lighting" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(2));
  expect(onSave.mock.calls[1][0].editor_metadata.ambient_lighting.outputs[0]).toMatchObject({ composition_id: "loop", cues: [extra] });
});

test("inspects, edits, duplicates and removes targeted ambient effects and keeps them in the loop", async () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  const preview = vi.spyOn(api, "previewAmbientLighting").mockResolvedValue({
    project_id: "project-1", source_revision: 3, timestamp_ms: 0, unsaved: false,
    evaluation: "production_lighting_renderer", hardware_commands_sent: false, limitations: [],
    outputs: [{ output_id: "ambient:roof", resource: "roof", pixel_count: 8, pixel_offset: 0,
      output_position_ms: 0, cycle_ms: 4000, colors: Array(8).fill([255, 0, 0]),
      next_pixel_offset: null, limitations: [] }],
  });
  render(<AmbientLightingPanel canEdit onSave={onSave} draft={draft({
    schema_version: 1, excluded_scene_ids: [],
    outputs: [{ resource: "roof", cycle_ms: 4000, cues: [
      { id: "porch-red", primitive: "solid", start_ms: 0, duration_ms: 4000, color: "#FF0000",
        target: { kind: "fixture", id: "porch" } },
      { id: "garage-sweep", primitive: "path_sweep", start_ms: 1000, duration_ms: 3000, color: "#00D8FF",
        target: { kind: "path", id: "garage" }, width_pixels: 3 },
    ] }],
  })} />);
  fireEvent.click(screen.getByText("Ambient lighting"));
  expect(screen.getByText(/2 effects on 1 output/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Look")).toBeNull();
  await waitFor(() => expect(preview).toHaveBeenCalledWith("project-1", 3, 0, null, undefined, expect.anything()));
  const list = screen.getByRole("region", { name: "Ambient effects on roof" });
  fireEvent.click(within(list).getByRole("button", { name: "Edit effect garage-sweep" }));
  const inspector = screen.getByRole("group", { name: "Selected ambient effect on roof" });
  expect(within(inspector).getByLabelText("Effect")).toHaveValue("path_sweep");
  expect(within(inspector).getByLabelText("Target decorations")).toHaveValue("path:garage");
  expect(within(inspector).getByLabelText("Width (LEDs)")).toHaveValue(3);
  fireEvent.change(within(inspector).getByLabelText("Width (LEDs)"), { target: { value: "5" } });
  fireEvent.click(within(inspector).getByRole("button", { name: "Duplicate effect" }));
  expect(within(list).getAllByRole("button", { name: /Edit effect/ })).toHaveLength(3);

  fireEvent.change(screen.getByLabelText("Loop length (seconds)"), { target: { value: "2" } });
  expect(screen.getByText(/Some effects run past the loop/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save ambient lighting" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Loop length (seconds)"), { target: { value: "4" } });

  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(within(list).getByRole("button", { name: "Edit effect porch-red" }));
  fireEvent.click(within(screen.getByRole("group", { name: "Selected ambient effect on roof" }))
    .getByRole("button", { name: "Remove effect" }));
  fireEvent.click(screen.getByRole("button", { name: "Save ambient lighting" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  const output = onSave.mock.calls[0][0].editor_metadata.ambient_lighting.outputs[0];
  expect(output.cycle_ms).toBe(4000);
  expect(output.cues.map((cue: { id: string }) => cue.id)).toEqual(
    ["garage-sweep", expect.stringMatching(/^ambient-/)],
  );
  expect(output.cues[0]).toMatchObject({ width_pixels: 5, target: { kind: "path", id: "garage" } });
  expect(output.cues[1]).toMatchObject({ primitive: "path_sweep", width_pixels: 5, start_ms: 1000 });
});
