import { expect, test } from "vitest";
import { nightPreviewColor } from "./led-preview";

test("off LEDs contribute no opaque pixels", () => {
  expect(nightPreviewColor([0, 0, 0])).toBeNull();
});

test("nighttime display transfer makes low PWM levels visible without changing input", () => {
  const raw: [number, number, number] = [1, 3, 26];
  expect(nightPreviewColor(raw)).toBe("rgb(13,28,90)");
  expect(raw).toEqual([1, 3, 26]);
  expect(nightPreviewColor([255, 255, 255])).toBe("rgb(255,255,255)");
  expect(nightPreviewColor([0, 3, 0])).toBe("rgb(0,28,0)");
});
