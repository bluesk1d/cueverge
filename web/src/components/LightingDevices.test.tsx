import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import {
  createLightingDevice, detectWledController, removeLightingDevice, updateLightingDevice,
  type LightingDevice,
} from "../api";
import { LightingDeviceForm, LightingDeviceList } from "./LightingDevices";

vi.mock("../api", () => ({
  createLightingDevice: vi.fn(), detectWledController: vi.fn(),
  removeLightingDevice: vi.fn(), updateLightingDevice: vi.fn(),
}));

beforeEach(() => vi.resetAllMocks());

it("registers a LIFX bulb with a 25% default cap", async () => {
  vi.mocked(createLightingDevice).mockResolvedValue({} as never);
  const saved = vi.fn();
  render(<LightingDeviceForm installationId="home" kind="lifx" revision={2} onSaved={saved} />);
  expect(screen.queryByLabelText("LED count")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Output resource name"), {target: {value: "light.left"}});
  fireEvent.change(screen.getByLabelText("IPv4 address"), {target: {value: "192.168.40.71"}});
  fireEvent.change(screen.getByLabelText(/LIFX target ID/), {target: {value: "D073D58B1A6D"}});
  fireEvent.click(screen.getByRole("button", {name: "Add LIFX bulb"}));
  await waitFor(() => expect(saved).toHaveBeenCalledWith("light.left", true));
  expect(createLightingDevice).toHaveBeenCalledWith("home", expect.objectContaining({
    kind: "lifx", resource: "light.left", host: "192.168.40.71", target: "d073d58b1a6d", brightness_limit: .25,
  }), 2);
});

it("detects a WLED controller's LED count before registering it", async () => {
  vi.mocked(detectWledController).mockResolvedValue({
    connected: true, latency_ms: 4, software_version: "0.15.0", reported_pixel_count: 150,
    signal_percent: 70, output_on: true, diagnostic: null,
  });
  vi.mocked(createLightingDevice).mockResolvedValue({} as never);
  render(<LightingDeviceForm installationId="home" kind="wled" revision={0} onSaved={vi.fn()} />);
  expect(screen.queryByLabelText(/LIFX target ID/)).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Output resource name"), {target: {value: "roofline"}});
  fireEvent.change(screen.getByLabelText("IPv4 address"), {target: {value: "192.168.40.80"}});
  fireEvent.click(screen.getByRole("button", {name: "Detect WLED"}));
  expect(await screen.findByText(/reports 150 LEDs/)).toBeInTheDocument();
  expect(screen.getByLabelText("LED count")).toHaveValue(150);
  fireEvent.click(screen.getByRole("button", {name: "Add WLED controller"}));
  await waitFor(() => expect(createLightingDevice).toHaveBeenCalledWith("home", expect.objectContaining({
    kind: "wled", resource: "roofline", expected_pixel_count: 150, brightness_limit: 1,
  }), 0));
});

const devices: LightingDevice[] = [
  {kind: "wled", resource: "roofline", host: "192.168.40.80", expected_pixel_count: 60, port: 4048,
    destination_id: 1, max_payload_bytes: 1440, brightness_limit: 1, pixel_count: 60, in_use: false, transport: null,
    managed_by: "registry"},
  {kind: "lifx", resource: "light.left", host: "192.168.40.71", target: "d073d58b1a6d", port: 56700,
    kelvin: 3500, brightness_limit: .25, max_hz: 18, duration_ms: 0, pixel_count: 1, in_use: true, transport: null,
    managed_by: "registry"},
];

it("shows output-config devices read-only", () => {
  render(<LightingDeviceList installationId="home" revision={0} onChanged={vi.fn()} devices={[{
    ...devices[0], resource: "porch", managed_by: "deployment_config",
  }]} />);
  expect(screen.getByText("porch")).toBeInTheDocument();
  expect(screen.getByText(/CUEVERGE_OUTPUT_CONFIG/)).toBeInTheDocument();
  expect(screen.queryByRole("button", {name: "Edit porch"})).not.toBeInTheDocument();
  expect(screen.queryByRole("button", {name: "Remove porch"})).not.toBeInTheDocument();
});

it("edits and removes registered devices with the registry revision", async () => {
  vi.mocked(updateLightingDevice).mockResolvedValue({} as never);
  vi.mocked(removeLightingDevice).mockResolvedValue(undefined);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const changed = vi.fn();
  render(<LightingDeviceList installationId="home" devices={devices} revision={5} onChanged={changed} />);
  expect(screen.getByRole("button", {name: "Remove light.left"})).toBeDisabled();
  fireEvent.click(screen.getByRole("button", {name: "Edit roofline"}));
  expect(screen.getByLabelText("Output resource name")).toBeDisabled();
  expect(screen.getByLabelText(/LED count/)).toBeDisabled();
  fireEvent.change(screen.getByLabelText("IPv4 address"), {target: {value: "192.168.40.81"}});
  fireEvent.click(screen.getByRole("button", {name: "Save changes"}));
  await waitFor(() => expect(updateLightingDevice).toHaveBeenCalledWith("home", expect.objectContaining({
    resource: "roofline", host: "192.168.40.81", expected_pixel_count: 60,
  }), 5));
  fireEvent.click(await screen.findByRole("button", {name: "Remove roofline"}));
  await waitFor(() => expect(removeLightingDevice).toHaveBeenCalledWith("home", "roofline", 5));
  expect(changed).toHaveBeenCalledTimes(2);
});
