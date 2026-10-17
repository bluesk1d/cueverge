import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { McpWorkspace } from "./McpWorkspace";

const mocks = vi.hoisted(() => ({
  approveMcpProposal: vi.fn(),
  createMcpToken: vi.fn(),
  getMcpProposals: vi.fn(),
  getMcpTokens: vi.fn(),
}));

vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  ...mocks,
}));

test("shows a token once and keeps proposal approval human-controlled", async () => {
  mocks.getMcpTokens.mockResolvedValue([]);
  mocks.getMcpProposals.mockResolvedValue([
    {
      id: "proposal-1",
      project_id: "project-1",
      base_revision: 3,
      title: "Add welcome scene",
      kind: "scene_outline",
      changed_paths: ["/scenes/1"],
      state: "validated",
      client_id: "assistant-one",
      username: "designer",
      validation: [],
      simulation: null,
      approved_by: null,
      disposition_reason: null,
      created_at: "2026-10-13T06:00:00Z",
      updated_at: "2026-10-13T06:00:00Z",
    },
  ]);
  mocks.createMcpToken.mockResolvedValue({
    token: "cvmcp_one-time-secret",
  });
  mocks.approveMcpProposal.mockResolvedValue({});

  render(<McpWorkspace projectId="project-1" canDesign />);

  await screen.findByText("Add welcome scene");
  fireEvent.change(screen.getByLabelText("Client identity"), {
    target: { value: "assistant-one" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create 30-day token" }));

  await screen.findByText("cvmcp_one-time-secret");
  expect(mocks.createMcpToken).toHaveBeenCalledWith(
    "project-1",
    "assistant-one",
    ["read"],
    expect.any(String),
  );

  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  await waitFor(() =>
    expect(mocks.approveMcpProposal).toHaveBeenCalledWith(
      "project-1",
      "proposal-1",
      "Reviewed in the Cueverge GUI",
    ),
  );
});
