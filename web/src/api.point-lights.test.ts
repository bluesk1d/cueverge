import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { createPointLightOutput, importPointLightResource } from "./api";

const fetchMock = vi.fn<typeof fetch>();
const csrfToken = "point-light-regression-csrf";

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

test("registering a LIFX output sends session, JSON and CSRF headers", async () => {
  const bulb = {resource: "light.left", host: "192.168.40.71", target: "d073d58b1a6d"};
  await createPointLightOutput("home", bulb);
  expect(fetchMock).toHaveBeenCalledWith("/api/gui/v1/installations/home/point-lights", {
    method: "POST", credentials: "include",
    headers: {Accept: "application/json", "Content-Type": "application/json", "X-CSRF-Token": csrfToken},
    body: JSON.stringify({kind: "lifx", brightness_limit: .25, ...bulb}),
  });
});

test("importing a point light sends CSRF and revision headers together", async () => {
  await importPointLightResource("show", "light.left", 7);
  expect(fetchMock).toHaveBeenCalledWith("/api/gui/v1/projects/show/point-light-resources/light.left", {
    method: "POST", credentials: "include",
    headers: {Accept: "application/json", "Content-Type": "application/json", "X-CSRF-Token": csrfToken, "If-Match": '"7"'},
  });
});
