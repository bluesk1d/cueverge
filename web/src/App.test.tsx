import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { App } from "./App";
import { demoInstallation, demoRuntime } from "./demo/overview-fixtures";
import { OverviewPage } from "./pages/OverviewPage";

afterEach(() => {
  vi.restoreAllMocks();
});

test("shows the authenticated shell and truthful overview", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.endsWith("/auth/session")) {
      return jsonResponse({ username: "operator", role: "administrator" });
    }
    if (path.endsWith("/installation")) {
      return jsonResponse(demoInstallation);
    }
    if (path.endsWith("/runtime/status")) {
      return jsonResponse(demoRuntime);
    }
    throw new Error(`unexpected request: ${path}`);
  });

  render(<App />);

  expect(
    await screen.findByRole("heading", { name: "Installation Overview" }),
  ).toBeInTheDocument();
  for (const label of ["Overview", "Installation", "Shows", "Operate", "Settings"]) {
    expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
  }
  expect(screen.getByRole("button", { name: "Overview" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(screen.getByText("Show identity unavailable")).toBeInTheDocument();
  expect(await screen.findAllByText("Visual Demo Installation")).not.toHaveLength(0);
});

test("navigates across the exact top-level workspaces", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.endsWith("/auth/session")) {
      return jsonResponse({ username: "operator", role: "administrator" });
    }
    if (path.endsWith("/installation")) {
      return jsonResponse(demoInstallation);
    }
    if (path.endsWith("/runtime/status")) {
      return jsonResponse(demoRuntime);
    }
    if (path.endsWith("/projects")) {
      return jsonResponse([]);
    }
    throw new Error(`unexpected request: ${path}`);
  });

  render(<App />);
  await screen.findByRole("heading", { name: "Installation Overview" });

  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(screen.getByRole("heading", { name: "Settings" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Shows" }));
  expect(await screen.findByRole("heading", { name: "Shows" })).toBeInTheDocument();
  expect(screen.getByText("No draft projects")).toBeInTheDocument();
});

test("requires authentication before showing private workspaces", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ detail: { code: "authentication_required" } }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    }),
  );

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Sign in to Cueverge" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Username"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

  await waitFor(() => {
    expect(screen.getByRole("alert")).toHaveTextContent("Sign-in failed");
  });
});

test("shows supported runtime facts and honest unsupported states", () => {
  render(
    <OverviewPage
      installation={demoInstallation}
      runtime={demoRuntime}
      installationError={false}
      runtimeError={false}
      loading={false}
    />,
  );

  expect(screen.getByRole("heading", { name: "Installation Overview" })).toBeInTheDocument();
  expect(screen.getByText("dual-window-sweep")).toBeInTheDocument();
  expect(screen.getByText("00:24")).toBeInTheDocument();
  expect(screen.getByText("Show identity unavailable")).toBeInTheDocument();
  expect(screen.getByText("Activity stream unavailable")).toBeInTheDocument();
  expect(screen.getByText(/No next scene or start time is predicted/)).toBeInTheDocument();
  expect(screen.getByText("media-client-right")).toBeInTheDocument();
  expect(screen.getByText("Disconnected")).toBeInTheDocument();
  expect(screen.getAllByText("Not independently detectable")).toHaveLength(2);
});

test("does not infer status when the runtime request fails", () => {
  render(
    <OverviewPage
      installation={demoInstallation}
      runtime={null}
      installationError={false}
      runtimeError
      loading={false}
    />,
  );

  expect(screen.getByText("Unavailable")).toBeInTheDocument();
  expect(screen.getByText("Current scene state could not be observed.")).toBeInTheDocument();
  expect(screen.getAllByText("Unknown")).toHaveLength(4);
  expect(screen.queryByText("Running")).not.toBeInTheDocument();
});

function jsonResponse(value: unknown): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify(value), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}
