import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import * as api from "../api";
import type { InstallationTiming, TimingCalibrationRun } from "../api";
import { InstallationTimingPanel } from "./InstallationTimingPanel";

const outputs: InstallationTiming["outputs"] = [
  { resource: "projector.left", kind: "media", delay_ms: 40, minimum_ms: 0, maximum_ms: 5000, revision: 2, updated_by: "admin", updated_at: null },
  { resource: "wled-001", kind: "lighting", delay_ms: 0, minimum_ms: -5000, maximum_ms: 5000, revision: 0, updated_by: null, updated_at: null },
];

const run = (overrides: Partial<TimingCalibrationRun> = {}): TimingCalibrationRun => ({
  id: "run",
  state: "active",
  period_ms: 2000,
  flash_ms: 100,
  cycles: 1,
  started_at: "2026-10-28T00:00:00Z",
  error: null,
  stop_reason: null,
  outputs: [
    { resource: "projector.left", kind: "media", delay_ms: 40, saved_ms: 40, state: "playing", error: null, drift_ms: 3 },
    { resource: "wled-001", kind: "lighting", delay_ms: 0, saved_ms: 0, state: "flashing", error: null, drift_ms: null },
  ],
  ...overrides,
});

function status(mode: string) {
  return { status: { operations: { mode }, active_scene: null } } as unknown as api.RuntimeStatus;
}

let timing: InstallationTiming;

beforeEach(() => {
  timing = { installation_id: "home", outputs, calibration: null };
  vi.spyOn(api, "getInstallationTiming").mockImplementation(async () => timing);
  vi.spyOn(api, "getRuntimeStatus").mockResolvedValue(status("maintenance"));
});

afterEach(() => {
  vi.restoreAllMocks();
});

function panel(canConfigureDevices = false) {
  return render(
    <InstallationTimingPanel
      installationId="home"
      assignments={[
        { resource: "projector.left", device_id: "client01", capability_kind: "media_playback", device_output: null, revision: 1, actor: "admin", created_at: "", updated_at: "" },
      ]}
      canOperate
      canConfigureDevices={canConfigureDevices}
    />,
  );
}

test("saved delays can be edited and saved per output", async () => {
  const save = vi.spyOn(api, "setOutputTiming").mockResolvedValue(outputs[1]);
  panel();
  const input = await screen.findByLabelText("wled-001 saved delay");
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: "-35" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(save).toHaveBeenCalledWith("home", outputs[1], -35));
});

test("starting requires idle maintenance and sends the chosen outputs", async () => {
  vi.mocked(api.getRuntimeStatus).mockResolvedValue(status("automatic"));
  const operate = vi.spyOn(api, "operate").mockResolvedValue();
  const start = vi.spyOn(api, "startTimingCalibration").mockImplementation(async () => {
    timing = { ...timing, calibration: run() };
    return run();
  });
  panel();
  expect(await screen.findByText(/needs maintenance mode/)).toBeTruthy();
  const button = screen.getByRole("button", { name: "Start calibration" }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  vi.mocked(api.getRuntimeStatus).mockResolvedValue(status("maintenance"));
  fireEvent.click(screen.getByRole("button", { name: /Switch to maintenance/ }));
  await waitFor(() => expect(operate).toHaveBeenCalledWith("maintenance"));
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(screen.getByLabelText("Play the click"));
  fireEvent.click(button);
  await waitFor(() =>
    expect(start).toHaveBeenCalledWith("home", [{ resource: "projector.left", click: false }], ["wled-001"]),
  );
  expect(await screen.findByRole("group", { name: "projector.left live delay" })).toBeTruthy();
});

test("live delays nudge by buttons and arrow keys, and accept saves", async () => {
  timing = { ...timing, calibration: run() };
  const adjust = vi.spyOn(api, "adjustTimingCalibration").mockResolvedValue(run());
  const finish = vi.spyOn(api, "finishTimingCalibration").mockResolvedValue({
    run: run({ state: "stopped" }),
    saved: [{ ...outputs[1], delay_ms: 10, revision: 1 }],
  });
  panel();
  fireEvent.click(await screen.findByRole("button", { name: "Later by 10 ms for wled-001" }));
  await waitFor(() => expect(adjust).toHaveBeenLastCalledWith("home", "wled-001", 10));
  fireEvent.keyDown(screen.getByRole("group", { name: "projector.left live delay" }), {
    key: "ArrowLeft",
    shiftKey: true,
  });
  // Projector delays never go below 0.
  await waitFor(() => expect(adjust).toHaveBeenLastCalledWith("home", "projector.left", 0));
  expect(screen.getByText(/drift 3 ms/)).toBeTruthy();
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Accept and save" }));
  });
  await waitFor(() => expect(finish).toHaveBeenCalledWith("home", "accept"));
  expect(await screen.findByText("Saved wled-001 10 ms.")).toBeTruthy();
});

test("administrators can tune each projector client's audio delay live", async () => {
  timing = { ...timing, calibration: run() };
  const settings = {
    device_id: "client01",
    display_name: "CuevergeClient01",
    connected: true,
    supported: true,
    revision: 3,
    overrides: { keep_awake: "display" as const },
    sync: "applied" as const,
    effective: { keep_awake: "display" as const, player_mode: "always" as const, fullscreen_screen: null, audio_device: null, audio_delay_ms: 100, telemetry_hz: 10 },
    pending_player_restart: [],
    displays: [],
    audio_devices: [],
  };
  vi.spyOn(api, "getDeviceAgentSettings").mockResolvedValue(settings);
  const update = vi.spyOn(api, "updateDeviceAgentSettings").mockResolvedValue({
    ...settings,
    revision: 4,
    effective: { ...settings.effective, audio_delay_ms: 110 },
  });
  panel(true);
  expect(await screen.findByText("Client audio delay 100 ms")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Audio later by 10 ms on CuevergeClient01" }));
  await waitFor(() =>
    expect(update).toHaveBeenCalledWith("client01", { keep_awake: "display", audio_delay_ms: 110 }, 3),
  );
  expect(await screen.findByText("Client audio delay 110 ms")).toBeTruthy();
});
