import { useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";

type Point = { x: number; y: number };
type View = Point & { zoom: number };
type Gesture = {
  id: number;
  start: Point;
  last: Point;
  kind: "pan" | "point" | "draw" | "select";
  index: number;
};

const fitView: View = { x: 0, y: 0, zoom: 1 };
const clampZoom = (zoom: number) => Math.max(0.5, Math.min(8, zoom));

// Screen CTM includes SVG letterboxing as well as the current viewBox.
function screenPosition(svg: SVGSVGElement, client: Point): Point {
  const matrix = svg.getScreenCTM();
  if (!matrix) {
    throw new Error("Geometry canvas has no screen transform");
  }
  const point = svg.createSVGPoint();
  point.x = client.x;
  point.y = client.y;
  const world = point.matrixTransform(matrix.inverse());
  const box = svg.viewBox.baseVal;
  return { x: (world.x - box.x) / box.width, y: (world.y - box.y) / box.height };
}

function worldPosition(view: View, position: Point): Point {
  return {
    x: view.x + position.x * 1000 / view.zoom,
    y: view.y + position.y * 600 / view.zoom,
  };
}

export function GeometryCanvas({
  label,
  className,
  canEdit,
  onPlacePoint,
  onMovePoint,
  onSelectGeometry,
  drawing = false,
  canDraw = false,
  children,
}: {
  label: string;
  className: string;
  canEdit: boolean;
  onPlacePoint: (point: Point) => void;
  onMovePoint: (index: number, point: Point) => void;
  onSelectGeometry?: (id: string) => void;
  drawing?: boolean;
  canDraw?: boolean;
  children: (zoom: number) => ReactNode;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const viewRef = useRef(fitView);
  const [view, setView] = useState(fitView);
  const [tool, setTool] = useState<"select" | "draw" | "hand">("select");
  useEffect(() => setTool(drawing ? "draw" : "select"), [drawing]);
  const [space, setSpace] = useState(false);
  const spaceRef = useRef(false);
  const [panning, setPanning] = useState(false);
  const hovered = useRef(false);
  const pointers = useRef(new Map<number, Point>());
  const gesture = useRef<Gesture | null>(null);
  const suppressClick = useRef(false);
  const selectionTarget = useRef<string | null>(null);
  const hand = tool === "hand" || space;

  function updateView(next: View) {
    viewRef.current = next;
    setView(next);
  }

  function zoomAt(position: Point, zoom: number) {
    const current = viewRef.current;
    const anchor = worldPosition(current, position);
    const nextZoom = clampZoom(zoom);
    updateView({
      x: anchor.x - position.x * 1000 / nextZoom,
      y: anchor.y - position.y * 600 / nextZoom,
      zoom: nextZoom,
    });
  }

  useEffect(() => {
    function clearSpace() {
      spaceRef.current = false;
      setSpace(false);
    }
    function cancelGesture() {
      clearSpace();
      for (const id of pointers.current.keys()) {
        if (svgRef.current?.hasPointerCapture(id)) svgRef.current.releasePointerCapture(id);
      }
      pointers.current.clear();
      gesture.current = null;
      suppressClick.current = true;
      setPanning(false);
    }
    function keyDown(event: KeyboardEvent) {
      const target = event.target;
      if (
        event.code !== "Space" ||
        (target instanceof Element && target.closest("input, textarea, select, button, [contenteditable]")) ||
        (!hovered.current && document.activeElement !== svgRef.current)
      ) {
        return;
      }
      event.preventDefault();
      spaceRef.current = true;
      setSpace(true);
    }
    function keyUp(event: KeyboardEvent) {
      if (event.code === "Space") clearSpace();
    }
    window.addEventListener("keydown", keyDown);
    window.addEventListener("keyup", keyUp);
    window.addEventListener("blur", cancelGesture);
    return () => {
      window.removeEventListener("keydown", keyDown);
      window.removeEventListener("keyup", keyUp);
      window.removeEventListener("blur", cancelGesture);
    };
  }, []);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    function wheel(event: WheelEvent) {
      event.preventDefault();
      const position = screenPosition(svg!, { x: event.clientX, y: event.clientY });
      // Browsers expose no wheel device type. Small pixel deltas and horizontal
      // scrolling indicate a trackpad; Shift explicitly pans on any wheel.
      const pan = !event.ctrlKey && !event.metaKey && (
        event.shiftKey || (event.deltaMode === 0 && (event.deltaX !== 0 || Math.abs(event.deltaY) < 50))
      );
      if (pan) {
        const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? svg!.clientHeight : 1;
        const end = screenPosition(svg!, {
          x: event.clientX + (event.shiftKey && !event.deltaX ? event.deltaY : event.deltaX) * unit,
          y: event.clientY + (event.shiftKey ? 0 : event.deltaY) * unit,
        });
        const current = viewRef.current;
        updateView({
          ...current,
          x: current.x + (end.x - position.x) * 1000 / current.zoom,
          y: current.y + (end.y - position.y) * 600 / current.zoom,
        });
      } else {
        const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? svg!.clientHeight : 1);
        zoomAt(position, viewRef.current.zoom * Math.exp(-Math.max(-200, Math.min(200, delta)) * 0.005));
      }
    }
    svg.addEventListener("wheel", wheel, { passive: false });
    return () => svg.removeEventListener("wheel", wheel);
  }, []);

  function pointerDown(event: ReactPointerEvent<SVGSVGElement>) {
    if (event.button !== 0 && event.button !== 1) return;
    event.preventDefault();
    const svg = event.currentTarget;
    svg.focus({ preventScroll: true });
    svg.setPointerCapture(event.pointerId);
    const point = { x: event.clientX, y: event.clientY };
    pointers.current.set(event.pointerId, point);
    if (pointers.current.size > 1) {
      suppressClick.current = true;
      gesture.current = null;
      setPanning(true);
      return;
    }
    const handle = event.target instanceof Element
      ? event.target.closest("[data-geometry-point]")
      : null;
    selectionTarget.current = event.target instanceof Element
      ? event.target.closest("[data-layout-object]")?.getAttribute("data-layout-object") ?? null
      : null;
    const index = Number(handle?.getAttribute("data-geometry-point") ?? -1);
    const kind = hand || spaceRef.current || event.button === 1 ? "pan"
      : canEdit && handle ? "point" : tool === "draw" && canDraw ? "draw" : "select";
    gesture.current = { id: event.pointerId, start: point, last: point, kind, index };
    suppressClick.current = kind === "pan" || kind === "point";
    setPanning(kind === "pan");
  }

  function pointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    if (gesture.current?.kind === "select" &&
        Math.hypot(event.clientX - gesture.current.start.x, event.clientY - gesture.current.start.y) > 4) {
      suppressClick.current = true;
    }
    const previous = pointers.current.get(event.pointerId);
    if (!previous) return;
    const next = { x: event.clientX, y: event.clientY };
    const svg = event.currentTarget;
    if (pointers.current.size >= 2) {
      const other = [...pointers.current.entries()].find(([id]) => id !== event.pointerId)![1];
      const midpoint = (point: Point) => ({ x: (point.x + other.x) / 2, y: (point.y + other.y) / 2 });
      const before = screenPosition(svg, midpoint(previous));
      const after = screenPosition(svg, midpoint(next));
      const distance = (point: Point) => Math.hypot(point.x - other.x, point.y - other.y);
      const oldDistance = distance(previous);
      const current = viewRef.current;
      const anchor = worldPosition(current, before);
      const zoom = clampZoom(current.zoom * (oldDistance > 0 ? distance(next) / oldDistance : 1));
      updateView({ x: anchor.x - after.x * 1000 / zoom, y: anchor.y - after.y * 600 / zoom, zoom });
    } else {
      const active = gesture.current;
      if (active?.id === event.pointerId) {
        const position = screenPosition(svg, next);
        if (Math.hypot(next.x - active.start.x, next.y - active.start.y) > 4) {
          suppressClick.current = true;
        }
        if (active.kind === "pan") {
          const last = screenPosition(svg, active.last);
          const current = viewRef.current;
          updateView({
            ...current,
            x: current.x - (position.x - last.x) * 1000 / current.zoom,
            y: current.y - (position.y - last.y) * 600 / current.zoom,
          });
        } else if (active.kind === "point" && canEdit) {
          const point = worldPosition(viewRef.current, position);
          onMovePoint(active.index, { x: point.x / 1000, y: point.y / 600 });
        }
        active.last = next;
      }
    }
    pointers.current.set(event.pointerId, next);
  }

  function endPointer(event: ReactPointerEvent<SVGSVGElement>) {
    if (!pointers.current.has(event.pointerId)) return;
    if (event.type !== "pointerup") suppressClick.current = true;
    pointers.current.delete(event.pointerId);
    gesture.current = null;
    setPanning(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  return (
    <>
      <div className="geometry-mapper__canvas-tools" role="group" aria-label="Canvas navigation">
        <button type="button" aria-pressed={tool === "select"} onClick={() => setTool("select")}>Select</button>
        {canEdit && canDraw && <button type="button" aria-pressed={tool === "draw"} onClick={() => setTool("draw")}>Draw</button>}
        <button type="button" aria-pressed={tool === "hand"} onClick={() => setTool("hand")}>Hand</button>
        <button type="button" aria-label="Zoom out" disabled={view.zoom <= 0.5} onClick={() => zoomAt({ x: 0.5, y: 0.5 }, viewRef.current.zoom / 1.25)}>-</button>
        <output aria-label="Canvas zoom">{Math.round(view.zoom * 100)}%</output>
        <button type="button" aria-label="Zoom in" disabled={view.zoom >= 8} onClick={() => zoomAt({ x: 0.5, y: 0.5 }, viewRef.current.zoom * 1.25)}>+</button>
        <button type="button" onClick={() => updateView(fitView)}>Fit view</button>
        <small>Scroll to zoom · Space + drag to pan · Pinch to zoom</small>
      </div>
      <svg
        ref={svgRef}
        aria-label={label}
        className={`${className}${tool === "select" ? " geometry-canvas--select" : ""}${hand ? " geometry-canvas--hand" : ""}${panning ? " geometry-canvas--panning" : ""}`}
        role="img"
        tabIndex={0}
        viewBox={`${view.x} ${view.y} ${1000 / view.zoom} ${600 / view.zoom}`}
        onPointerEnter={() => { hovered.current = true; }}
        onPointerLeave={() => { hovered.current = false; }}
        onPointerDown={pointerDown}
        onPointerMove={pointerMove}
        onPointerUp={endPointer}
        onPointerCancel={endPointer}
        onLostPointerCapture={endPointer}
        onClick={(event) => {
          if (hand || suppressClick.current) return;
          if (tool === "select") {
            const object = event.target instanceof Element ? event.target.closest("[data-layout-object]") : null;
            const id = object?.getAttribute("data-layout-object") ?? selectionTarget.current;
            selectionTarget.current = null;
            if (id) onSelectGeometry?.(id);
            return;
          }
          if (!canEdit || !canDraw) return;
          const point = worldPosition(viewRef.current, screenPosition(event.currentTarget, { x: event.clientX, y: event.clientY }));
          onPlacePoint({ x: point.x / 1000, y: point.y / 600 });
        }}
        onKeyDown={(event) => {
          const step = 40 / viewRef.current.zoom;
          const delta = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[event.key];
          if (delta) {
            event.preventDefault();
            updateView({ ...viewRef.current, x: viewRef.current.x + delta[0], y: viewRef.current.y + delta[1] });
          } else if (event.key === "+" || event.key === "=" || event.key === "-") {
            event.preventDefault();
            zoomAt({ x: 0.5, y: 0.5 }, viewRef.current.zoom * (event.key === "-" ? 0.8 : 1.25));
          } else if (event.key === "0") {
            event.preventDefault();
            updateView(fitView);
          }
        }}
      >
        {children(view.zoom)}
      </svg>
    </>
  );
}
