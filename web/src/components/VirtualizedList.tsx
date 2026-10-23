import { CSSProperties, ReactNode, UIEvent, useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";

export function VirtualizedList<T>({
  items,
  estimateSize,
  keyFor,
  renderItem,
  ariaLabel,
  measureItems = false,
}: {
  items: T[];
  estimateSize: number;
  keyFor: (item: T) => string;
  renderItem: (item: T) => ReactNode;
  ariaLabel: string;
  measureItems?: boolean;
}) {
  const [scrollTop, setScrollTop] = useState(0);
  const list = useRef<HTMLDivElement>(null);
  const [heights, setHeights] = useState(new Map<string, number>());
  const measure = useCallback((key: string, height: number) => {
    if (height <= 0) return;
    setHeights((current) => current.get(key) === height
      ? current : new Map(current).set(key, height));
  }, []);
  const overscan = 4;
  const viewportSize = 640;
  const offsets = useMemo(() => {
    const values = [0];
    for (const item of items) {
      values.push(values[values.length - 1] +
        (measureItems ? heights.get(keyFor(item)) ?? estimateSize : estimateSize));
    }
    return values;
  }, [items, estimateSize, measureItems, heights, keyFor]);
  const totalHeight = offsets[offsets.length - 1];
  const position = Math.min(scrollTop, Math.max(0, totalHeight - viewportSize));
  const first = Math.max(0, offsets.findIndex((offset, index) => index > 0 && offset > position) - 1);
  const last = offsets.findIndex((offset) => offset >= position + viewportSize);
  const start = Math.max(0, first - overscan);
  const end = Math.min(
    items.length,
    (last < 0 ? items.length : last) + overscan,
  );
  const visibleItems = items.slice(start, end);
  const firstKey = items.length ? keyFor(items[0]) : null;
  const lastKey = items.length ? keyFor(items[items.length - 1]) : null;
  useLayoutEffect(() => {
    if (!measureItems) return;
    if (list.current) list.current.scrollTop = 0;
    setScrollTop(0);
  }, [measureItems, items.length, firstKey, lastKey]);

  return (
    <div
      ref={list}
      aria-label={ariaLabel}
      className="virtual-list"
      data-total-count={items.length}
      style={measureItems ? { height: `min(${totalHeight}px, 640px, 70vh)` } : undefined}
      onScroll={(event: UIEvent<HTMLDivElement>) =>
        setScrollTop(event.currentTarget.scrollTop)
      }
      role="list"
    >
      <div
        className="virtual-list__canvas"
        style={{ height: totalHeight }}
      >
        {visibleItems.map((item, offset) => {
          const index = start + offset;
          return (
            <VirtualizedItem
              key={keyFor(item)}
              itemKey={keyFor(item)}
              index={index}
              measureItems={measureItems}
              onMeasure={measure}
              style={{
                height: measureItems ? undefined : estimateSize,
                transform: `translateY(${offsets[index]}px)`,
              }}
            >
              {renderItem(item)}
            </VirtualizedItem>
          );
        })}
      </div>
    </div>
  );
}

function VirtualizedItem({ itemKey, index, measureItems, onMeasure, style, children }: {
  itemKey: string; index: number; measureItems: boolean;
  onMeasure: (key: string, height: number) => void;
  style: CSSProperties; children: ReactNode;
}) {
  const element = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (!measureItems || !element.current || typeof ResizeObserver === "undefined") return;
    const node = element.current;
    const measure = () => onMeasure(itemKey, Math.ceil(node.getBoundingClientRect().height));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [itemKey, measureItems, onMeasure]);
  return <div ref={element} className="virtual-list__item" data-index={index}
    role="listitem" style={style}>{children}</div>;
}
