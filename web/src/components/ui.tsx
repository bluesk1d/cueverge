import { AlertCircle, Inbox, LoaderCircle } from "lucide-react";
import { ReactNode } from "react";

export type StatusTone = "success" | "warning" | "danger" | "info" | "neutral";

export function Panel({
  title,
  description,
  className = "",
  children,
}: {
  title: string;
  description?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`panel ${className}`.trim()} aria-labelledby={panelId(title)}>
      <header className="panel__header">
        <div>
          <h2 id={panelId(title)}>{title}</h2>
          {description && <p>{description}</p>}
        </div>
      </header>
      <div className="panel__body">{children}</div>
    </section>
  );
}

export function StatusText({
  tone,
  children,
  detail,
}: {
  tone: StatusTone;
  children: ReactNode;
  detail?: string;
}) {
  return (
    <span className={`status-text status-text--${tone}`}>
      <span className="status-text__dot" aria-hidden="true" />
      <span>
        <strong>{children}</strong>
        {detail && <small>{detail}</small>}
      </span>
    </span>
  );
}

export function EmptyState({
  title,
  description,
  compact = false,
}: {
  title: string;
  description: string;
  compact?: boolean;
}) {
  return (
    <div className={`state-message ${compact ? "state-message--compact" : ""}`.trim()}>
      <Inbox aria-hidden="true" size={20} />
      <div>
        <strong>{title}</strong>
        <p>{description}</p>
      </div>
    </div>
  );
}

export function ErrorState({
  title = "Unable to load this information",
  description = "The last request failed. No status has been inferred.",
}: {
  title?: string;
  description?: string;
}) {
  return (
    <div className="state-message state-message--error" role="alert">
      <AlertCircle aria-hidden="true" size={20} />
      <div>
        <strong>{title}</strong>
        <p>{description}</p>
      </div>
    </div>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton" aria-label="Loading">
      <LoaderCircle className="skeleton__icon" aria-hidden="true" size={18} />
      <div className="skeleton__lines" aria-hidden="true">
        {Array.from({ length: lines }, (_, index) => (
          <span key={index} />
        ))}
      </div>
    </div>
  );
}

export function WorkspaceHeader({
  eyebrow,
  title,
  description,
  meta,
}: {
  eyebrow: string;
  title: string;
  description: string;
  meta?: ReactNode;
}) {
  return (
    <header className="workspace-header">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p className="workspace-header__description">{description}</p>
      </div>
      {meta && <div className="workspace-header__meta">{meta}</div>}
    </header>
  );
}

function panelId(title: string): string {
  return `panel-${title.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-")}`;
}

