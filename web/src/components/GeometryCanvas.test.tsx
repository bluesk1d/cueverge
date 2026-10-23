import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { GeometryCanvas } from "./GeometryCanvas";

test("read-only viewers can navigate but cannot draw", () => {
  const onPlacePoint = vi.fn();
  const onMovePoint = vi.fn();
  render(
    <GeometryCanvas label="Reference image" className="geometry-canvas" canEdit={false}
      onPlacePoint={onPlacePoint} onMovePoint={onMovePoint}>
      {() => <image href="/photo.png" width="1000" height="600" />}
    </GeometryCanvas>,
  );
  const canvas = screen.getByRole("img", { name: "Reference image" });
  expect(screen.queryByRole("button", { name: "Draw" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Select" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByRole("button", { name: "Hand" }));
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  expect(screen.getByLabelText("Canvas zoom")).toHaveTextContent("125%");
  fireEvent.keyDown(canvas, { key: "ArrowRight" });
  expect(canvas).not.toHaveAttribute("viewBox", "0 0 1000 600");
  fireEvent.click(canvas);
  expect(onPlacePoint).not.toHaveBeenCalled();
  expect(onMovePoint).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Fit view" }));
  expect(canvas).toHaveAttribute("viewBox", "0 0 1000 600");
});
