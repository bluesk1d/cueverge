import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { createLightingDevice, removeLightingDevice } from "./api";

const fetchMock = vi.fn<typeof fetch>();
const csrfToken = "lighting-device-regression-csrf";
const headers = {Accept: "application/json", "Content-Type": "application/json", "X-CSRF-Token": csrfToken};

beforeEach(() => {
  document.cookie = `cueverge_csrf=${csrfToken}; path=/`;
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(new Response("{}", {status: 200}));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  document.cookie = "cueverge_csrf=; Max-Age=0; path=/";
  vi.unstubAllGlobals();
});

test("registering a lighting device sends CSRF and registry revision", async () => {
  const strip = {kind: "wled" as const, resource: "roofline", host: "192.168.40.80", expected_pixel_count: 60};
  await createLightingDevice("home", strip, 3);
  expect(fetchMock).toHaveBeenCalledWith("/api/gui/v1/installations/home/lighting-devices", {
    method: "POST", credentials: "include", headers: {...headers, "If-Match": '"3"'}, body: JSON.stringify(strip),
  });
});

test("removing a lighting device targets the encoded resource", async () => {
  fetchMock.mockResolvedValue(new Response(null, {status: 204}));
  await removeLightingDevice("home", "light.left", 4);
  expect(fetchMock).toHaveBeenCalledWith("/api/gui/v1/installations/home/lighting-devices/light.left", {
    method: "DELETE", credentials: "include", headers: {...headers, "If-Match": '"4"'},
  });
});
