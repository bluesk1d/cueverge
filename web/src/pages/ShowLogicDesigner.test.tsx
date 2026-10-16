import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { ProjectDraftRecord } from "../api";
import { ShowLogicDesigner } from "./ShowLogicDesigner";

const draft: ProjectDraftRecord = {
  project: {
    id: "project-1",
    name: "Autonomous show",
    head_revision: 1,
    metadata_revision: 1,
    archived_at: null,
    created_at: "2026-10-13T06:00:00Z",
    updated_at: "2026-10-13T06:00:00Z",
  },
  revision: 1,
  draft: {
    schema_version: 1,
    installation: {
      schema_version: 1,
      resources: [
        {
          resource: "projector.demo",
          kind: "media_agent",
          target: "media-agent-1",
        },
      ],
    },
    editor_metadata: {},
    scenes: [
      {
        schema_version: 2,
        id: "ambient",
        name: "Ambient",
        duration_ms: 10_000,
        resources: ["projector.demo"],
        media: [{ id: "main", asset: null }],
      },
      {
        schema_version: 2,
        id: "welcome",
        name: "Welcome",
        duration_ms: 8_000,
        resources: ["projector.demo"],
        media: [{ id: "main", asset: null }],
      },
    ],
  },
};

test("authors semantic autonomous logic and blocks structurally invalid saves", async () => {
  let saved: ProjectDraftRecord["draft"] | undefined;
  const onSave = vi.fn(async (document: ProjectDraftRecord["draft"]) => {
    saved = document;
  });
  render(
    <ShowLogicDesigner
      canEdit
      draft={draft}
      onSave={onSave}
      projectId="project-1"
    />,
  );

  fireEvent.click(
    screen.getByText("Autonomous show logic designer"),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Visitor interaction template" }),
  );

  expect(
    screen.getByRole("table", {
      name: "Accessible encounter transition editor",
    }),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/wall-clock schedules are intentionally unavailable/i),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/never raw detector payload expressions/i),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("textbox", { name: /raw detector payload/i }),
  ).not.toBeInTheDocument();

  fireEvent.change(
    screen.getByRole("spinbutton", { name: "Recent history exclusion" }),
    { target: { value: "2" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Save autonomous logic" }));

  await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
  if (!saved) {
    throw new Error("autonomous draft was not saved");
  }
  expect(saved.program?.recent_history_size).toBe(2);
  expect(saved.program?.pools[0].scenes[1]).toMatchObject({
    scene_id: "welcome",
    cooldown_ms: 60_000,
    conditions: { audience_state: "present" },
  });
  expect(saved.opportunities?.opportunities[0].trigger_event).toBe(
    "audience.arrived",
  );
  expect(saved.encounter?.transitions).toHaveLength(2);

  fireEvent.click(
    screen.getAllByRole("button", { name: "Remove opportunity" })[0],
  );
  expect(screen.getByText("opportunities_empty")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Save autonomous logic" }),
  ).toBeDisabled();
});
