import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { InstallationGeometry, InstallationPhoto } from "../api";
import * as api from "../api";
import { OutputTraversalPreview } from "./OutputTraversalPreview";

const photo: InstallationPhoto = {
  id: "front", installation_id: "installation", name: "Front",
  viewpoint: "front", original_filename: "front.jpg", media_type: "image/jpeg",
  digest_sha256: "a".repeat(64), size_bytes: 100,
  orientation_degrees: 0, crop_x: null, crop_y: null, crop_width: null,
  crop_height: null, scale_hint: null, revision: 1, created_at: "", updated_at: "",
};
const geometry: InstallationGeometry = {
  id: "strand", installation_id: "installation", photo_id: "front",
  semantic_id: "strand", landmark_ids: [], kind: "pixel_segment", name: "Strand",
  points: [{ x: 0.1, y: 0.1 }, { x: 0.9, y: 0.1 }], closed: false,
  color: "#ffffff", label_visible: true, locked: false, hidden: false, z_index: 0,
  direction: "reverse", start_index: 0, pixel_count: 10, stage_points: null,
  revision: 1, created_at: "", updated_at: "",
};

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("wiring traversal works on the installation canvas with no photo", () => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true })));
  const { container } = render(<OutputTraversalPreview
    geometry={[{ ...geometry, photo_id: null }]} resources={{ strand: "roof" }}
    resource="roof" pixelCount={10} installationId="installation" onClose={() => {}} />);
  expect(screen.getByRole("img", { name: "Output traversal for roof on installation canvas" })).toBeVisible();
  expect(container.querySelector("image")).toBeNull();
  expect(container.querySelector('[data-output-index="0"]')).toHaveAttribute("cx", "900");
  expect(screen.getByLabelText("Current traversal pixel")).toHaveTextContent("Strand");
});

test("reduced motion starts paused and scrubbing exposes exact reversed pixel locations", () => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true })));
  const onClose = vi.fn();
  const { container } = render(
    <OutputTraversalPreview
      geometry={[geometry]} resources={{ strand: "roof" }} resource="roof"
      pixelCount={10} photo={photo} onClose={onClose}
    />,
  );
  expect(screen.getByRole("button", { name: "Play output traversal" })).toBeVisible();
  expect(container.querySelector('[data-output-index="0"]')).toHaveAttribute("cx", "900");
  fireEvent.change(screen.getByLabelText("Output pixel index"), { target: { value: "9" } });
  expect(screen.getByLabelText("Current traversal pixel")).toHaveTextContent("Pixel 9 / 9");
  expect(Number(container.querySelector('[data-output-index="9"]')!.getAttribute("cx")))
    .toBeCloseTo(100);
  expect(container.querySelectorAll(".output-traversal__pulse")).toHaveLength(5);
  fireEvent.click(screen.getByRole("button", { name: "Close output traversal" }));
  expect(onClose).toHaveBeenCalledOnce();
});

test("play advances at the selected speed, pause freezes, and restart clears the timer", () => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false })));
  vi.useFakeTimers();
  const { unmount } = render(
    <OutputTraversalPreview
      geometry={[geometry]} resources={{ strand: "roof" }} resource="roof"
      pixelCount={100} photo={photo} onClose={() => undefined}
    />,
  );
  act(() => vi.advanceTimersByTime(1000));
  expect(screen.getByLabelText("Output pixel index")).toHaveValue("30");
  fireEvent.click(screen.getByRole("button", { name: "Pause output traversal" }));
  act(() => vi.advanceTimersByTime(500));
  expect(screen.getByLabelText("Output pixel index")).toHaveValue("30");
  fireEvent.change(screen.getByLabelText("Traversal speed"), { target: { value: "60" } });
  fireEvent.click(screen.getByRole("button", { name: "Play output traversal" }));
  act(() => vi.advanceTimersByTime(1000));
  expect(screen.getByLabelText("Output pixel index")).toHaveValue("90");
  fireEvent.click(screen.getByRole("button", { name: "Restart output traversal" }));
  expect(screen.getByLabelText("Output pixel index")).toHaveValue("0");
  expect(screen.getByRole("button", { name: "Play output traversal" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Play output traversal" }));
  expect(vi.getTimerCount()).toBeGreaterThan(0);
  unmount();
  expect(vi.getTimerCount()).toBe(0);
});

test("opening physical controls only reads status and follows the controller index", async () => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true })));
  const read = vi.spyOn(api, "getOutputTestOverview").mockResolvedValue({
    mode: "maintenance", playback_active: false,
    outputs: [{ resource: "roof", kind: "pixel_output", target: "wled", pixel_count: 10 }],
    test: {
      id: "physical", arm_id: "arm", resource: "roof", pixel_count: 10, speed: 30,
      state: "active", pixel_index: 7, frames_sent: 15,
      started_at: "", expires_at: "2026-10-20T12:00:30Z",
      error: null, stop_reason: null, simulation_only: false, segments: [],
    },
  });
  const start = vi.spyOn(api, "startPhysicalOutputTest");
  const stop = vi.spyOn(api, "stopPhysicalOutputTest");
  const { container, unmount } = render(
    <OutputTraversalPreview
      geometry={[geometry]} resources={{ strand: "roof" }} resource="roof"
      pixelCount={10} photo={photo} canTest onClose={() => undefined}
    />,
  );
  expect(read).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Test this output on physical LEDs"));
  await waitFor(() => expect(screen.getByLabelText("Output pixel index")).toHaveValue("7"));
  expect(screen.getByLabelText("Output pixel index")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Play output traversal" })).toBeDisabled();
  expect(Number(container.querySelector('[data-output-index="7"]')!.getAttribute("cx")))
    .toBeCloseTo(277.7778);
  expect(start).not.toHaveBeenCalled();
  unmount();
  expect(stop).not.toHaveBeenCalled();
});
