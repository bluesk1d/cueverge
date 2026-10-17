import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  approveMcpProposal,
  createMcpToken,
  mergeMcpProposal,
  rejectMcpProposal,
  revokeMcpToken,
  validateMcpProposal,
} from "./api";

const csrfToken = "mcp-regression-csrf-token";
const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  document.cookie = `cueverge_csrf=${csrfToken}; path=/`;
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  document.cookie = "cueverge_csrf=; Max-Age=0; path=/";
  vi.unstubAllGlobals();
});

test.each([
  {
    name: "create token",
    invoke: () =>
      createMcpToken("project-1", "assistant", ["read"], "2026-11-01T00:00:00Z"),
    path: "/api/gui/v1/projects/project-1/mcp-tokens",
    method: "POST",
    body: {
      client_id: "assistant",
      scopes: ["read"],
      expires_at: "2026-11-01T00:00:00Z",
    },
  },
  {
    name: "revoke token",
    invoke: () => revokeMcpToken("project-1", "token-1"),
    path: "/api/gui/v1/projects/project-1/mcp-tokens/token-1",
    method: "DELETE",
  },
  ...[
    { action: "validate", invoke: () => validateMcpProposal("project-1", "proposal-1") },
    {
      action: "approve",
      invoke: () => approveMcpProposal("project-1", "proposal-1", "Reviewed"),
      body: { reason: "Reviewed" },
    },
    {
      action: "reject",
      invoke: () => rejectMcpProposal("project-1", "proposal-1", "Not appropriate"),
      body: { reason: "Not appropriate" },
    },
    { action: "merge", invoke: () => mergeMcpProposal("project-1", "proposal-1") },
  ].map(({ action, invoke, ...options }) => ({
    name: `${action} proposal`,
    invoke,
    path: `/api/gui/v1/projects/project-1/mcp-proposals/proposal-1/${action}`,
    method: "POST",
    ...options,
  })),
])("$name sends authenticated mutation headers", async ({ invoke, path, method, body }) => {
  await invoke();

  expect(fetchMock).toHaveBeenCalledOnce();
  expect(fetchMock).toHaveBeenCalledWith(
    path,
    expect.objectContaining({
      method,
      credentials: "include",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    }),
  );
});
