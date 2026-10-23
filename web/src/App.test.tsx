import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { App } from "./App";
import {
  demoActivity,
  demoInstallation,
  demoInstallations,
  demoRuntime,
  demoTopology,
} from "./demo/overview-fixtures";
import { InstallationPage } from "./pages/InstallationPage";
import { activityFailure, groupActivity, OverviewPage } from "./pages/OverviewPage";

afterEach(() => {
  vi.restoreAllMocks();
});

test("activity separates audit outcomes from failures and retains reconnect history", () => {
  const event = demoActivity[0];
  expect(activityFailure({ ...event, action: "mcp.tool.call", result: "completed" })).toBe(false);
  expect(activityFailure({ ...event, action: "browser.error", result: "reported" })).toBe(true);
  expect(activityFailure({ ...event, result: "denied" })).toBe(true);
  const events = [
    { ...event, id: 4, occurred_at: "2026-10-22T12:01:00Z" },
    { ...event, id: 3, action: "device.disconnected", occurred_at: "2026-10-22T12:00:50Z" },
    { ...event, id: 2, occurred_at: "2026-10-22T12:00:40Z" },
    { ...event, id: 1, result: "failed", occurred_at: "2026-10-22T12:00:30Z" },
  ];
  const groups = groupActivity(events);
  expect(groups.map((group) => group.map((item) => item.id))).toEqual([[4, 3, 2], [1]]);
  expect(groups.flat()).toEqual(events);
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
    if (path.endsWith("/installations")) {
      return jsonResponse(demoInstallations);
    }
    if (path.endsWith("/installations/demo-installation/photos")) {
      return jsonResponse([]);
    }
    if (path.includes("/installations/demo-installation/geometry")) {
      return jsonResponse([]);
    }
    if (path.endsWith("/installations/demo-installation/projector-overlaps")) {
      return jsonResponse([]);
    }
    if (path.endsWith("/installations/demo-installation/geometry-suggestions")) {
      return jsonResponse([]);
    }
    if (path.endsWith("/installations/demo-installation/stage-coverage")) {
      return jsonResponse({
        installation_id: "demo-installation",
        minimum_x: 0,
        minimum_y: 0,
        maximum_x: 1,
        maximum_y: 1,
        regions: [],
      });
    }
    if (path.endsWith("/assets/image-library")) {
      return jsonResponse([]);
    }
    if (path.endsWith("/runtime/status")) {
      return jsonResponse(demoRuntime);
    }
    if (path.endsWith("/topology")) {
      return jsonResponse(demoTopology);
    }
    if (path.endsWith("/resource-assignments")) {
      return jsonResponse([]);
    }
    if (path.endsWith("/activity")) {
      return jsonResponse(demoActivity);
    }
    if (path.endsWith("/devices/pairing-tokens")) {
      return jsonResponse({
        id: "pairing-demo",
        pairing_token: "one-time-demo-token",
        device_kind: "direct_media_agent",
        expires_at: new Date(Date.now() + 300_000).toISOString(),
        device_transport_url: "wss://192.0.2.10:8443/api/device/v2/agent/ws",
      });
    }
    if (path.endsWith("/devices/capabilities/review")) {
      return jsonResponse({
        compatible: true,
        controller_protocol_version: 2,
        capability_kinds: ["media_playback"],
        issues: [],
      });
    }
    if (path.endsWith("/discovery/endpoints")) {
      return jsonResponse([]);
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
    if (path.endsWith("/installations")) {
      return jsonResponse(demoInstallations);
    }
    if (path.endsWith("/installations/demo-installation/photos")) {
      return jsonResponse([]);
    }
    if (path.includes("/installations/demo-installation/geometry")) {
      return jsonResponse([]);
    }
    if (path.endsWith("/installations/demo-installation/projector-overlaps")) {
      return jsonResponse([]);
    }
    if (path.endsWith("/installations/demo-installation/geometry-suggestions")) {
      return jsonResponse([]);
    }
    if (path.endsWith("/installations/demo-installation/stage-coverage")) {
      return jsonResponse({
        installation_id: "demo-installation",
        minimum_x: 0,
        minimum_y: 0,
        maximum_x: 1,
        maximum_y: 1,
        regions: [],
      });
    }
    if (path.endsWith("/assets/image-library")) {
      return jsonResponse([]);
    }
    if (path.endsWith("/runtime/status")) {
      return jsonResponse(demoRuntime);
    }
    if (path.endsWith("/topology")) {
      return jsonResponse(demoTopology);
    }
    if (path.endsWith("/resource-assignments")) {
      return jsonResponse([
        {
          resource: "projector.left",
          device_id: "demo-left",
          capability_kind: "media_playback",
          device_output: null,
          revision: 1,
          actor: "admin",
          created_at: "2026-10-13T06:00:00Z",
          updated_at: "2026-10-13T06:00:00Z",
        },
      ]);
    }
    if (path.endsWith("/activity")) {
      return jsonResponse(demoActivity);
    }
    if (path.endsWith("/devices/pairing-tokens")) {
      return jsonResponse({
        id: "pairing-demo",
        pairing_token: "one-time-demo-token",
        device_kind: "direct_media_agent",
        expires_at: new Date(Date.now() + 300_000).toISOString(),
        device_transport_url: "wss://192.0.2.10:8443/api/device/v2/agent/ws",
      });
    }
    if (path.endsWith("/devices/capabilities/review")) {
      return jsonResponse({
        compatible: true,
        controller_protocol_version: 2,
        capability_kinds: ["media_playback"],
        issues: [],
      });
    }
    if (path.endsWith("/discovery/endpoints")) {
      return jsonResponse([]);
    }
    if (path.endsWith("/projects")) {
      return jsonResponse([
        {
          id: "project-1",
          name: "Home Show",
          head_revision: 2,
          metadata_revision: 1,
          archived_at: null,
          created_at: "2026-10-13T06:00:00Z",
          updated_at: "2026-10-13T06:30:00Z",
        },
      ]);
    }
    if (path.endsWith("/projects/project-1/assets")) {
      return jsonResponse([
        {
          id: "asset-1",
          project_id: "project-1",
          name: "Window loop",
          status: "ready",
          current_revision: 1,
          metadata_revision: 2,
          owner: "Projection team",
          license: "Original production",
          provenance: "Created for the Home installation.",
          review_status: "approved",
          expires_on: "2027-10-31",
          folder: "Visuals/Windows",
          tags: ["window", "halloween"],
          favorite: true,
          error: null,
          digest_sha256: "a".repeat(64),
          size_bytes: 1048576,
          media_type: "video/mp4",
          original_filename: "window-loop.mp4",
          scan_status: "unavailable",
          metadata: {
            duration_ms: 2500,
            width: 1920,
            height: 1080,
            frame_rate: 29.97,
            video_codecs: ["h264"],
            audio_codecs: ["aac"],
            channels: 2,
            sample_rate: 48000,
            loudness_lufs: -18.2,
            artifacts: {
              review: { media_type: "video/mp4", size_bytes: 1024 },
              thumbnail: { media_type: "image/jpeg", size_bytes: 256 },
              waveform: { media_type: "image/png", size_bytes: 128 },
            },
            status: "ready",
          },
          original_file_status: "available",
          artifact_faults: [],
          references: [
            {
              scene_id: "ambient",
              scene_name: "Ambient",
              output_id: "left-window",
            },
          ],
          unused: false,
          created_at: "2026-10-13T06:00:00Z",
          updated_at: "2026-10-13T06:01:00Z",
        },
        {
          id: "asset-2",
          project_id: "project-1",
          name: "Missing loop",
          status: "ready",
          current_revision: 1,
          metadata_revision: 1,
          owner: null,
          license: null,
          provenance: null,
          review_status: "unreviewed",
          expires_on: null,
          folder: null,
          tags: [],
          favorite: false,
          error: null,
          digest_sha256: "b".repeat(64),
          size_bytes: 2048,
          media_type: "video/mp4",
          original_filename: "missing-loop.mp4",
          scan_status: "unavailable",
          metadata: { status: "ready" },
          original_file_status: "missing",
          artifact_faults: ["review: missing"],
          references: [],
          unused: true,
          created_at: "2026-10-13T06:02:00Z",
          updated_at: "2026-10-13T06:02:00Z",
        },
      ]);
    }
    if (path.endsWith("/projects/project-1/draft")) {
      return jsonResponse({
        project: {
          id: "project-1",
          name: "Home Show",
          head_revision: 2,
          metadata_revision: 1,
          archived_at: null,
          created_at: "2026-10-13T06:00:00Z",
          updated_at: "2026-10-13T06:30:00Z",
        },
        revision: 2,
        draft: {
          editor_metadata: {
            scene_organization: {
              ambient: {
                folder: "Scenes/Ambient",
                tags: ["ambient"],
                favorite: true,
              },
            },
            scene_presets: {
              "preset-1": {
                id: "preset-1",
                name: "Ambient base",
                folder: "Presets/Base",
                tags: ["base"],
                favorite: true,
                schema_version: 2,
                template: {},
              },
            },
            scene_preset_usage: { ambient: "preset-1" },
          },
          scenes: [
            {
              schema_version: 2,
              id: "ambient",
              name: "Ambient",
              media: [{ id: "left-window", asset: "asset-1" }],
            },
          ],
        },
      });
    }
    throw new Error(`unexpected request: ${path}`);
  });

  render(<App />);
  await screen.findByRole("heading", { name: "Installation Overview" });

  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(screen.getByRole("heading", { name: "Settings" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Your installation" })).toBeInTheDocument();
  fireEvent.click(screen.getByText("Additional installation records (advanced)", { selector: "summary" }));
  expect(screen.getByRole("button", { name: "Create installation" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Installation settings" }));
  fireEvent.click(screen.getByText("Organization (optional)", { selector: "summary" }));
  expect(await screen.findByRole("button", { name: "Delete" })).toBeDisabled();
  expect(
    screen.getByRole("textbox", { name: "Folder for Visual Demo Installation" }),
  ).toHaveValue("Home");
  expect(screen.getByText("Used by Home Show")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));

  fireEvent.click(screen.getByRole("button", { name: "Installation" }));
  expect(screen.getByRole("heading", { name: "Your installation" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Devices" }));
  expect(screen.getByRole("heading", { name: "Connect and manage devices" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Add a device" }));
  for (const summary of ["Connection expectations", "Advanced device controls", "Assign display roles", "Connection checks", "Find another controller (advanced)"]) {
    fireEvent.click(screen.getByText(summary, { selector: "summary" }));
  }
  expect(screen.getByRole("button", { name: "Create one-time token" })).toBeInTheDocument();
  expect(
    screen.getByRole("combobox", { name: "Connection policy for media-client-right" }),
  ).toHaveValue("on_demand");
  expect(
    screen.getByRole("combobox", { name: "Assigned device for projector.left" }),
  ).toBeDisabled();
  expect(
    screen.getByRole("combobox", { name: "Assigned device for projector.right" }),
  ).toHaveValue("");
  expect(screen.getAllByRole("button", { name: "Suspend" })).toHaveLength(2);
  expect(screen.getAllByRole("button", { name: "Revoke" })).toHaveLength(2);
  expect(screen.getAllByRole("button", { name: "Test connection" })).toHaveLength(2);
  expect(
    screen.getByRole("heading", { name: "Explicit controller discovery" }),
  ).toBeInTheDocument();
  expect(screen.getByText(/Discovery never grants trust/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Create one-time token" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Review capabilities" }));
  expect(await screen.findByText("Compatible with protocol v2")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Create one-time token" }));
  expect(await screen.findByText("one-time-demo-token")).toBeInTheDocument();
  expect(screen.getByText(/cueverge-device-enroll/)).toBeInTheDocument();
  expect(screen.getByText(/CUEVERGE_DEVICE_TRANSPORT_URL=/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Shows" }));
  expect(await screen.findByRole("heading", { name: "Shows" })).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Open show Home Show" }));
  fireEvent.click(screen.getByText("Show options", { exact: true }));
  fireEvent.click(screen.getByText("Assets", { exact: true }));
  fireEvent.click(await screen.findByText("Advanced authoring and history", { exact: true }));
  expect(screen.getByDisplayValue("Home Show")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Duplicate" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Archive" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Export" })).toBeInTheDocument();
  expect(await screen.findByText(/1920×1080/)).toBeInTheDocument();
  expect(screen.getByText(/-18.2 LUFS/)).toBeInTheDocument();
  expect(screen.getAllByText("Malware scan: unavailable")).toHaveLength(2);
  expect(screen.getByText("Original file: available")).toBeInTheDocument();
  expect(screen.getByText("Used by Ambient (left-window)")).toBeInTheDocument();
  expect(screen.getByText("Original file: missing")).toBeInTheDocument();
  expect(screen.getByText("Artifact review: missing")).toBeInTheDocument();
  expect(screen.getByText("Unused in the current draft")).toBeInTheDocument();
  expect(screen.getByText(/Favorite · Visuals\/Windows/)).toBeInTheDocument();
  fireEvent.click(screen.getByText("Organization & governance · approved"));
  expect(screen.getByDisplayValue("Projection team")).toBeInTheDocument();
  expect(screen.getByDisplayValue("Original production")).toBeInTheDocument();
  expect(screen.getByDisplayValue("Created for the Home installation.")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Place asset" })).toHaveLength(2);
  fireEvent.click(screen.getByText("Scenes & reusable presets"));
  expect(screen.getByText("Ambient base")).toBeInTheDocument();
  expect(screen.getByDisplayValue("Scenes/Ambient")).toBeInTheDocument();
  const assetSearch = screen.getByRole("searchbox", {
    name: "Search assets in Home Show",
  });
  fireEvent.change(assetSearch, { target: { value: "halloween" } });
  const assetArea = screen.getByText("Assets", { exact: true }).closest("details");
  if (!assetArea) throw new Error("Assets disclosure is missing");
  const assetQueries = within(assetArea);
  expect(assetQueries.getByText("Window loop")).toBeInTheDocument();
  expect(assetQueries.queryByText("Missing loop")).not.toBeInTheDocument();
  expect(assetQueries.getByText(/Review proxy playback/).closest("video")).toHaveAttribute(
    "src",
    "/api/gui/v1/assets/asset-1/artifacts/review",
  );
  expect(assetQueries.getByAltText("Waveform for Window loop")).toHaveAttribute(
    "src",
    "/api/gui/v1/assets/asset-1/artifacts/waveform",
  );

  fireEvent.click(screen.getByRole("button", { name: "Operate" }));
  expect(screen.getByRole("heading", { name: "Show controls" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Start show" })).toBeDisabled();
  expect(
    screen.getByText(/Start is unavailable until this starting scene's required media devices are connected/),
  ).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Runtime counters" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Recent command failures" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Media outputs" })).toBeInTheDocument();
  expect(screen.getByText("Clock source")).toBeInTheDocument();
  expect(screen.getAllByText("ambient")).toHaveLength(2);
  expect(
    screen.getByText("Reconnect and test the device before retrying the show."),
  ).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Maintenance simulation" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Start simulated pattern" })).toBeDisabled();
  expect(screen.getByText(/Enter maintenance mode to arm simulated patterns/)).toBeInTheDocument();
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
  vi.spyOn(globalThis, "fetch").mockImplementation(() => jsonResponse([]));
  render(
    <OverviewPage
      installation={demoInstallation}
      runtime={demoRuntime}
      topology={demoTopology}
      runtimeFreshness="fresh"
      topologyFreshness="fresh"
      onNavigate={vi.fn()}
      activity={demoActivity}
      installationError={false}
      runtimeError={false}
      loading={false}
    />,
  );

  expect(screen.getByRole("heading", { name: "Installation Overview" })).toBeInTheDocument();
  expect(screen.getByText("dual-window-sweep")).toBeInTheDocument();
  expect(screen.getByText("Position 00:24")).toBeInTheDocument();
  expect(screen.getByText("Show identity unavailable")).toBeInTheDocument();
  expect(screen.getByText("Device Connected")).toBeInTheDocument();
  expect(screen.getByText(/No next start or autonomous scene is predicted/)).toBeInTheDocument();
  expect(screen.getByText("media-client-right")).toBeInTheDocument();
  expect(screen.getByText("Offline · on demand")).toBeInTheDocument();
  expect(screen.getByText("Connection not reported")).toBeInTheDocument();
  expect(screen.getByText("Unknown")).toBeInTheDocument();
});

test("does not infer status when the runtime request fails", () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(() => jsonResponse([]));
  render(
    <OverviewPage
      installation={demoInstallation}
      runtime={null}
      topology={null}
      runtimeFreshness="unavailable"
      topologyFreshness="unavailable"
      onNavigate={vi.fn()}
      installationError={false}
      runtimeError
      loading={false}
    />,
  );

  expect(screen.getByText("Playback unavailable")).toBeInTheDocument();
  expect(screen.getByText("Status unavailable")).toBeInTheDocument();
  expect(screen.getAllByText("Unknown")).toHaveLength(4);
  expect(screen.queryByText("Running")).not.toBeInTheDocument();
});

test("separates topology connectivity, assignment, and unavailable diagnostics", () => {
  render(
    <InstallationPage
      initialSection="devices"
      installation={demoInstallation}
      topology={demoTopology}
      installationError={false}
      topologyError={false}
      loading={false}
    />,
  );

  expect(screen.getByRole("heading", { name: "Your installation" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "media-client-left" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "media-client-right" })).toBeInTheDocument();
  expect(screen.getByText("Offline · on demand")).toBeInTheDocument();
  expect(screen.queryByText("mp4 · h264")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Details for media-client-left" }));
  expect(screen.getByText("Enrolled")).toBeInTheDocument();
  expect(screen.getByText("mp4 · h264")).toBeInTheDocument();
  expect(screen.getByText("Pause, Seek")).toBeInTheDocument();
  expect(screen.getByText("400 GB free of 500 GB")).toBeInTheDocument();
  expect(screen.getByText("12 digest(s) · 8.4 GB · 4 pinned")).toBeInTheDocument();
  expect(screen.getByText("Pinning, Resumable transfer")).toBeInTheDocument();
  expect(screen.getByText("1 active · 0 failed")).toBeInTheDocument();
  expect(screen.getAllByText("projector.left")).not.toHaveLength(0);
});

test("shows enrolled device identity and durable assignments separately from connectivity", () => {
  render(
    <InstallationPage
      initialSection="devices"
      installation={demoInstallation}
      topology={{
        ...demoTopology,
        enrollment_supported: true,
        nodes: [
          demoTopology.nodes[0],
          {
            ...demoTopology.nodes[1],
            id: "device:7ea9567c-28fb-4e74-b4fc-c876e7277966",
            label: "CuevergeClient01",
            connection: "disconnected",
            enrollment: "enrolled",
            compatibility: "compatible",
            protocol_version: 2,
            capabilities: ["media_playback", "artifact_cache"],
            assigned_resources: ["projector.left"],
            last_seen_age_ms: null,
            software_version: "0.1.0",
          },
        ],
      }}
      installationError={false}
      topologyError={false}
      loading={false}
    />,
  );

  expect(screen.getByRole("heading", { name: "CuevergeClient01" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Details for CuevergeClient01" }));
  expect(screen.getByText("Enrolled")).toBeInTheDocument();
  expect(screen.getByText("media_playback, artifact_cache")).toBeInTheDocument();
  expect(screen.getByText("0.1.0")).toBeInTheDocument();
});

function jsonResponse(value: unknown): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify(value), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}
