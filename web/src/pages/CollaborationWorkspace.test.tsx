import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { CollaborationWorkspace } from "./CollaborationWorkspace";

const mocks = vi.hoisted(() => ({
  createProjectComment: vi.fn(),
  getProjectChangeRequests: vi.fn(),
  getProjectComments: vi.fn(),
  getProjectMembers: vi.fn(),
  getProjectPresence: vi.fn(),
  heartbeatProjectPresence: vi.fn(),
}));

vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  ...mocks,
}));

test("loads effective project permissions and creates object comments", async () => {
  mocks.getProjectMembers.mockResolvedValue([
    {
      project_id: "project-1",
      username: "designer",
      role: "designer",
      can_live_output: false,
      created_at: "2026-10-13T06:00:00Z",
      updated_at: "2026-10-13T06:00:00Z",
    },
  ]);
  mocks.getProjectComments.mockResolvedValue([]);
  mocks.getProjectChangeRequests.mockResolvedValue([]);
  mocks.getProjectPresence.mockResolvedValue([]);
  mocks.heartbeatProjectPresence.mockResolvedValue(undefined);
  mocks.createProjectComment.mockResolvedValue({
    id: "comment-1",
  });
  const onPermissions = vi.fn();

  render(
    <CollaborationWorkspace
      projectId="project-1"
      revision={3}
      session={{ username: "designer", role: "viewer" }}
      onPermissions={onPermissions}
    />,
  );

  await waitFor(() =>
    expect(onPermissions).toHaveBeenCalledWith("designer", false),
  );
  fireEvent.change(screen.getByLabelText("Object ID (optional)"), {
    target: { value: "scene.welcome" },
  });
  fireEvent.change(screen.getByLabelText("New comment"), {
    target: { value: "Please shorten this cue." },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add comment" }));

  await waitFor(() =>
    expect(mocks.createProjectComment).toHaveBeenCalledWith(
      "project-1",
      3,
      "Please shorten this cue.",
      "scene.welcome",
    ),
  );
});
