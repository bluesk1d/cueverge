import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

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

test("loops an LED composition and keeps cues added through MCP", async () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  const extra = { id: "mcp-extra", primitive: "ripple", start_ms: 0, duration_ms: 1000 };
  render(<AmbientLightingPanel canEdit onSave={onSave} draft={draft({
    schema_version: 1, excluded_scene_ids: [],
    outputs: [{ resource: "roof", cycle_ms: 2000, cues: [
      { id: "ambient", primitive: "solid", start_ms: 0, duration_ms: 2000, color: "#112233" }, extra,
    ] }],
  })} />);
  fireEvent.click(screen.getByText("Ambient lighting"));
  expect(screen.getByText(/1 more effect set through MCP are kept/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Brightness (%)"), { target: { value: "50" } });
  fireEvent.click(screen.getByRole("button", { name: "Save ambient lighting" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  expect(onSave.mock.calls[0][0].editor_metadata.ambient_lighting.outputs[0].cues[1]).toEqual(extra);
  fireEvent.change(screen.getByLabelText("Source"), { target: { value: "composition" } });
  expect(screen.getByLabelText("Composition")).toHaveValue("loop");
  expect(screen.queryByRole("option", { name: "Projector only" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save ambient lighting" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(2));
  expect(onSave.mock.calls[1][0].editor_metadata.ambient_lighting.outputs[0]).toMatchObject({ composition_id: "loop", cues: [extra] });
});
