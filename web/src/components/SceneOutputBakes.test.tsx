import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import * as api from "../api";
import type { BakeRequirement } from "../api";
import { SceneOutputBakes } from "./SceneOutputBakes";

const requirement = (state: BakeRequirement["state"]): api.BakeRequirements => ({
  project_id: "show",
  revision: 1,
  ready: state === "current",
  bakes: [
    {
      id: "scene-output:intro:left",
      label: "Intro · projector",
      kind: "scene_output",
      state,
      job_id: null,
      progress: state === "running" ? 0.5 : null,
      error: null,
      scene_id: "intro",
    },
  ],
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

function view(revision: number) {
  return (
    <SceneOutputBakes
      projectId="show"
      sceneId="intro"
      revision={revision}
      canEdit
      dirty={false}
      renderJobs={[]}
      onJobCreated={() => undefined}
    />
  );
}

test("status follows the saved revision and refreshes while a bake runs", async () => {
  vi.useFakeTimers();
  const load = vi
    .spyOn(api, "getBakeRequirements")
    .mockResolvedValueOnce(requirement("current"))
    .mockResolvedValueOnce(requirement("running"))
    .mockResolvedValueOnce(requirement("current"));
  const { rerender } = render(view(1));
  await act(async () => undefined);
  expect(screen.getByText(/Baked for this saved version/)).toBeTruthy();

  rerender(view(2));
  await act(async () => undefined);
  expect(load).toHaveBeenLastCalledWith("show", 2);
  expect(screen.getByText(/Baking 50%/)).toBeTruthy();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(load).toHaveBeenCalledTimes(3);
  expect(screen.getByText(/Baked for this saved version/)).toBeTruthy();
});
