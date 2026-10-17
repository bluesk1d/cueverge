import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

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

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getMcpTokens.mockResolvedValue([]);
  mocks.getMcpProposals.mockResolvedValue([]);
  mocks.createMcpToken.mockResolvedValue({ token: "cvmcp_one-time-secret" });
});

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

  render(<McpWorkspace projectId="project-1" canDesign canPublish canActivate />);

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

test("direct editing, publishing, and activation are independently opt-in", async () => {
  render(<McpWorkspace projectId="project-1" canDesign canPublish canActivate />);
  await waitFor(() => expect(mocks.getMcpTokens).toHaveBeenCalled());

  const directEdit = screen.getByLabelText(
    "Edit working drafts directly (no proposal approval)",
  );
  const publish = screen.getByLabelText(
    "Approve releases, publish, and stage packages",
  );
  const activate = screen.getByLabelText("Activate staged packages (live output)");
  expect(directEdit).not.toBeChecked();
  expect(publish).not.toBeChecked();
  expect(activate).not.toBeChecked();
  expect(screen.getByLabelText("Create isolated draft proposals")).not.toBeChecked();

  fireEvent.click(directEdit);
  fireEvent.change(screen.getByLabelText("Client identity"), {
    target: { value: "direct-editor" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create 30-day token" }));
  await waitFor(() =>
    expect(mocks.createMcpToken).toHaveBeenCalledWith(
      "project-1", "direct-editor", ["read", "direct_edit"], expect.any(String),
    ),
  );
  await screen.findByText("cvmcp_one-time-secret");
  expect(directEdit).not.toBeChecked();

  fireEvent.click(publish);
  fireEvent.click(activate);
  fireEvent.change(screen.getByLabelText("Client identity"), {
    target: { value: "deployment-client" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create 30-day token" }));
  await waitFor(() =>
    expect(mocks.createMcpToken).toHaveBeenLastCalledWith(
      "project-1", "deployment-client", ["read", "publish", "activate"], expect.any(String),
    ),
  );
});

test("new scope choices follow effective permissions and drop revoked selections", async () => {
  const { rerender } = render(
    <McpWorkspace projectId="project-1" canDesign canPublish canActivate />,
  );
  await waitFor(() => expect(mocks.getMcpTokens).toHaveBeenCalled());
  fireEvent.click(screen.getByLabelText("Activate staged packages (live output)"));

  rerender(
    <McpWorkspace
      projectId="project-1" canDesign canPublish={false} canActivate={false}
    />,
  );
  expect(screen.getByLabelText(
    "Edit working drafts directly (no proposal approval)",
  )).toBeEnabled();
  expect(screen.getByLabelText(
    "Approve releases, publish, and stage packages",
  )).toBeDisabled();
  const activate = screen.getByLabelText("Activate staged packages (live output)");
  expect(activate).toBeDisabled();
  await waitFor(() => expect(activate).not.toBeChecked());

  fireEvent.change(screen.getByLabelText("Client identity"), {
    target: { value: "read-client" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create 30-day token" }));
  await waitFor(() =>
    expect(mocks.createMcpToken).toHaveBeenCalledWith(
      "project-1", "read-client", ["read"], expect.any(String),
    ),
  );
});
