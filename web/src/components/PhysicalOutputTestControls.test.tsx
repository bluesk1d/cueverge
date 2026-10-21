import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import type { OutputTestOverview, PhysicalOutputTest } from "../api";
import { PhysicalOutputTestControls } from "./PhysicalOutputTestControls";

const mocks = vi.hoisted(() => ({
  getOutputTestOverview: vi.fn(), startPhysicalOutputTest: vi.fn(),
  stopPhysicalOutputTest: vi.fn(), operate: vi.fn(),
}));
vi.mock("../api", () => mocks);

const activeTest: PhysicalOutputTest = {
  id: "test", arm_id: "arm", resource: "roof", pixel_count: 8,
  speed: 30, state: "active", pixel_index: 3, frames_sent: 4,
  started_at: "2026-10-20T12:00:00Z", expires_at: "2026-10-20T12:00:30Z",
  error: null, stop_reason: null, simulation_only: false, segments: [],
};
let overview: OutputTestOverview;

beforeEach(() => {
  vi.clearAllMocks();
  overview = {
    mode: "maintenance", playback_active: false, test: null,
    outputs: [{ resource: "roof", kind: "pixel_output", target: "wled", pixel_count: 8 }],
  };
  mocks.getOutputTestOverview.mockImplementation(async () => overview);
  mocks.startPhysicalOutputTest.mockImplementation(async () => {
    overview = { ...overview, test: activeTest };
    return activeTest;
  });
  mocks.stopPhysicalOutputTest.mockImplementation(async () => {
    const stopped = { ...activeTest, state: "stopped" as const };
    overview = { ...overview, test: stopped };
    return stopped;
  });
});

test("physical start is explicit, exactly confirmed, and uses configured output without a test cap", async () => {
  render(<PhysicalOutputTestControls canOperate installationId="installation" />);
  const start = screen.getByRole("button", { name: "Start physical chase" });
  await waitFor(() => expect(screen.getByLabelText("LED output to test")).toHaveValue("roof"));
  expect(mocks.startPhysicalOutputTest).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Confirm physical output name"), { target: { value: "roof " } });
  expect(start).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Confirm physical output name"), { target: { value: "roof" } });
  fireEvent.click(start);
  await waitFor(() => expect(mocks.startPhysicalOutputTest).toHaveBeenCalledWith({
    resource: "roof", confirmation: "roof", duration_seconds: 30,
    speed: 30, use_mapping: true, installation_id: "installation",
  }));
  expect(await screen.findByText(/roof: active/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Stop physical chase" }));
  await waitFor(() => expect(mocks.stopPhysicalOutputTest).toHaveBeenCalledWith("test"));
  expect(await screen.findByText(/roof: stopped/)).toBeVisible();
});

test("view-only users cannot start or stop a physical test", async () => {
  overview = { ...overview, test: activeTest };
  render(<PhysicalOutputTestControls canOperate={false} />);
  expect(await screen.findByRole("button", { name: "Stop physical chase" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Start physical chase" })).toBeDisabled();
});

test("physical errors remain visible instead of showing successful output", async () => {
  mocks.startPhysicalOutputTest.mockRejectedValue(new Error("saved range overlaps Upper roof"));
  render(<PhysicalOutputTestControls canOperate />);
  await waitFor(() => expect(screen.getByLabelText("LED output to test")).toHaveValue("roof"));
  fireEvent.change(screen.getByLabelText("Confirm physical output name"), { target: { value: "roof" } });
  fireEvent.click(screen.getByRole("button", { name: "Start physical chase" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("saved range overlaps Upper roof");
  expect(screen.queryByRole("button", { name: "Stop physical chase" })).not.toBeInTheDocument();
});

test("unmounting removes status polling without silently stopping physical output", async () => {
  vi.useFakeTimers();
  try {
    const { unmount } = render(<PhysicalOutputTestControls canOperate />);
    await vi.advanceTimersByTimeAsync(1000);
    expect(mocks.getOutputTestOverview).toHaveBeenCalledTimes(2);
    unmount();
    await vi.advanceTimersByTimeAsync(2000);
    expect(mocks.getOutputTestOverview).toHaveBeenCalledTimes(2);
    expect(mocks.stopPhysicalOutputTest).not.toHaveBeenCalled();
  } finally {
    vi.useRealTimers();
  }
});
