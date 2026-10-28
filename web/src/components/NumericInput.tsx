import { useEffect, useState } from "react";
import type { ChangeEvent, FocusEvent, InputHTMLAttributes } from "react";

type NumericInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "value"> & {
  value: number | string;
  /** Forward an empty field (for optional values); otherwise empty text is never committed. */
  allowEmpty?: boolean;
};

/**
 * A number field that can be typed into freely.
 *
 * Controlled number inputs that convert every keystroke with Number() snap a partial entry
 * such as "-" or an emptied field to 0. This keeps the typed text while it is incomplete and
 * only forwards changes that parse as finite numbers, restoring the value on blur otherwise.
 */
export function NumericInput({
  value,
  allowEmpty = false,
  onChange,
  onBlur,
  onFocus,
  ...props
}: NumericInputProps) {
  const [text, setText] = useState(String(value));
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    if (!editing) setText(String(value));
  }, [editing, value]);

  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    const next = event.target.value;
    setText(next);
    if (next === "" ? allowEmpty : Number.isFinite(Number(next)) && !/[eE.\-+]$/.test(next)) {
      onChange?.(event);
    }
  }

  return (
    <input
      {...props}
      type="number"
      value={editing ? text : String(value)}
      onChange={handleChange}
      onFocus={(event: FocusEvent<HTMLInputElement>) => {
        setEditing(true);
        setText(String(value));
        onFocus?.(event);
      }}
      onBlur={(event: FocusEvent<HTMLInputElement>) => {
        setEditing(false);
        onBlur?.(event);
      }}
    />
  );
}
