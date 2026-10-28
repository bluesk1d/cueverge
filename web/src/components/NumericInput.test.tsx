import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { expect, test, vi } from "vitest";

import { NumericInput } from "./NumericInput";

function Harness({ onCommit, allowEmpty }: { onCommit: (value: string) => void; allowEmpty?: boolean }) {
  const [value, setValue] = useState<number | string>(0);
  return (
    <NumericInput
      aria-label="Gain"
      allowEmpty={allowEmpty}
      value={value}
      onChange={(event) => {
        onCommit(event.target.value);
        setValue(event.target.value === "" ? "" : Number(event.target.value));
      }}
    />
  );
}

test("a negative number can be typed without snapping back to zero", () => {
  const onCommit = vi.fn();
  render(<Harness onCommit={onCommit} />);
  const input = screen.getByLabelText("Gain") as HTMLInputElement;
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: "" } });
  fireEvent.change(input, { target: { value: "-2" } });
  fireEvent.change(input, { target: { value: "-20" } });
  expect(onCommit.mock.calls.map(([value]) => value)).toEqual(["-2", "-20"]);
  expect(input.value).toBe("-20");
  fireEvent.blur(input);
  expect(input.value).toBe("-20");
});

test("an emptied field is restored on blur unless empty values are allowed", () => {
  const onCommit = vi.fn();
  const { unmount } = render(<Harness onCommit={onCommit} />);
  const input = screen.getByLabelText("Gain") as HTMLInputElement;
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: "" } });
  expect(onCommit).not.toHaveBeenCalled();
  fireEvent.blur(input);
  expect(input.value).toBe("0");
  unmount();

  const optional = vi.fn();
  render(<Harness onCommit={optional} allowEmpty />);
  const field = screen.getByLabelText("Gain") as HTMLInputElement;
  fireEvent.focus(field);
  fireEvent.change(field, { target: { value: "" } });
  expect(optional).toHaveBeenCalledWith("");
});
