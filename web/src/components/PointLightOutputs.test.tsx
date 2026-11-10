import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { createPointLightOutput } from "../api";
import { PointLightOutputs } from "./PointLightOutputs";

vi.mock("../api", () => ({createPointLightOutput: vi.fn()}));

it("registers a bulb and refreshes installation resources", async () => {
  vi.mocked(createPointLightOutput).mockResolvedValue({});
  const refresh = vi.fn();
  render(<PointLightOutputs installationId="home" onRefresh={refresh} />);
  fireEvent.change(screen.getByLabelText("Output resource"), {target: {value: "light.left"}});
  fireEvent.change(screen.getByLabelText("IPv4 address"), {target: {value: "192.168.40.71"}});
  fireEvent.change(screen.getByLabelText("LIFX target ID"), {target: {value: "d073d58b1a6d"}});
  fireEvent.click(screen.getByRole("button", {name: "Add LIFX output"}));
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  expect(createPointLightOutput).toHaveBeenCalledWith("home", {
    resource: "light.left", host: "192.168.40.71", target: "d073d58b1a6d",
  });
});
