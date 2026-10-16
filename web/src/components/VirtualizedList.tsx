import { ReactNode, UIEvent, useState } from "react";

export function VirtualizedList<T>({
  items,
  estimateSize,
  keyFor,
  renderItem,
  ariaLabel,
}: {
  items: T[];
  estimateSize: number;
  keyFor: (item: T) => string;
  renderItem: (item: T) => ReactNode;
  ariaLabel: string;
}) {
  const [scrollTop, setScrollTop] = useState(0);
  const overscan = 4;
  const viewportSize = 640;
  const start = Math.max(0, Math.floor(scrollTop / estimateSize) - overscan);
  const end = Math.min(
    items.length,
    Math.ceil((scrollTop + viewportSize) / estimateSize) + overscan,
  );
  const visibleItems = items.slice(start, end);

  return (
    <div
      aria-label={ariaLabel}
      className="virtual-list"
      data-total-count={items.length}
      onScroll={(event: UIEvent<HTMLDivElement>) =>
        setScrollTop(event.currentTarget.scrollTop)
      }
      role="list"
    >
      <div
        className="virtual-list__canvas"
        style={{ height: items.length * estimateSize }}
      >
        {visibleItems.map((item, offset) => {
          const index = start + offset;
          return (
            <div
              className="virtual-list__item"
              data-index={index}
              key={keyFor(item)}
              role="listitem"
              style={{
                height: estimateSize,
                transform: `translateY(${index * estimateSize}px)`,
              }}
            >
              {renderItem(item)}
            </div>
          );
        })}
      </div>
    </div>
  );
}
