import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { demoRuntime, demoTopology } from "./demo/overview-fixtures";
import { snapshotFreshness } from "./status";
import { useLiveStatus } from "./useLiveStatus";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function clock() {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date", "performance"] });
}

function response(value: unknown) {
  return new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
}

test("snapshot age uses monotonic receipt time with an exact ten-second boundary", () => {
  expect(snapshotFreshness(null, false, 500)).toBe("loading");
  expect(snapshotFreshness(500, false, 10499)).toBe("fresh");
  expect(snapshotFreshness(500, false, 10500)).toBe("stale");
  expect(snapshotFreshness(500, true, 501)).toBe("unavailable");
});

test("runtime updates do not wait for a slow topology request", async () => {
  clock();
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
    String(input).endsWith("/runtime/status") ? response(demoRuntime)
      : new Promise<Response>(() => undefined));
  const { result, unmount } = renderHook(useLiveStatus);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(result.current.runtimeFreshness).toBe("fresh");
  expect(result.current.topologyFreshness).toBe("loading");
  expect(result.current.runtime).toEqual(demoRuntime);
  unmount();
  expect(vi.getTimerCount()).toBe(0);
});

test("polls every two seconds, reports failure, and recovers without dropping the last snapshot", async () => {
  clock();
  let fail = false;
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (fail) throw new Error("connection lost");
    return response(String(input).endsWith("/runtime/status") ? demoRuntime : demoTopology);
  });
  const { result } = renderHook(useLiveStatus);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(result.current.runtimeFreshness).toBe("fresh");
  expect(fetch).toHaveBeenCalledTimes(2);
  fail = true;
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(result.current.runtimeFreshness).toBe("unavailable");
  expect(result.current.runtime).toEqual(demoRuntime);
  fail = false;
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(result.current.runtimeFreshness).toBe("fresh");
  expect(fetch).toHaveBeenCalledTimes(6);
});

test("ages hidden snapshots, resumes on visibility, and never overlaps slow polls", async () => {
  clock();
  const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  let slow = false;
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
    slow ? new Promise<Response>(() => undefined)
      : response(String(input).endsWith("/runtime/status") ? demoRuntime : demoTopology));
  const { result, unmount } = renderHook(useLiveStatus);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  visibility.mockReturnValue("hidden");
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(result.current.runtimeFreshness).toBe("stale");
  expect(fetch).toHaveBeenCalledTimes(2);
  visibility.mockReturnValue("visible");
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
  expect(result.current.runtimeFreshness).toBe("fresh");
  slow = true;
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(fetch).toHaveBeenCalledTimes(6);
  unmount();
  expect(vi.getTimerCount()).toBe(0);
});

test("aborts hung requests after eight seconds and allows the next poll to recover", async () => {
  clock();
  let slow = false;
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (slow) {
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("Timed out", "AbortError")));
      });
    }
    return response(String(input).endsWith("/runtime/status") ? demoRuntime : demoTopology);
  });
  const { result } = renderHook(useLiveStatus);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  slow = true;
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(fetch).toHaveBeenCalledTimes(4);
  expect(result.current.runtimeFreshness).toBe("unavailable");
  slow = false;
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(result.current.runtimeFreshness).toBe("fresh");
  expect(fetch).toHaveBeenCalledTimes(6);
});
