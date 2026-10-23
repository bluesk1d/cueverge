import {
  Activity,
  CircleGauge,
  Home,
  Layers3,
  MonitorCog,
  RefreshCw,
  Settings,
} from "lucide-react";
import { ReactNode, useEffect, useRef, useState } from "react";

import {
  InstallationStatus,
  RuntimeStatus,
  Session,
} from "../api";
import { StatusText } from "./ui";
import { controllerStatus, SnapshotFreshness } from "../status";

export type Workspace = "overview" | "installation" | "shows" | "operate" | "settings";

const navigation: {
  id: Workspace;
  label: string;
  icon: typeof Home;
}[] = [
  { id: "overview", label: "Overview", icon: Home },
  { id: "installation", label: "Installation", icon: MonitorCog },
  { id: "shows", label: "Shows", icon: Layers3 },
  { id: "operate", label: "Operate", icon: Activity },
  { id: "settings", label: "Settings", icon: Settings },
];

export function AppShell({
  session,
  workspace,
  installation,
  installationUnavailable = false,
  runtime,
  runtimeFreshness,
  topologyFreshness,
  refreshing,
  configurationError,
  sessionExpired = false,
  onReauthenticate,
  onNavigate,
  onRefresh,
  onSignOut,
  children,
}: {
  session: Session;
  workspace: Workspace;
  installation: InstallationStatus | null;
  installationUnavailable?: boolean;
  runtime: RuntimeStatus | null;
  runtimeFreshness: SnapshotFreshness;
  topologyFreshness: SnapshotFreshness;
  refreshing: boolean;
  configurationError?: string | null;
  sessionExpired?: boolean;
  onReauthenticate?: () => void;
  onNavigate: (workspace: Workspace) => void;
  onRefresh: () => void;
  onSignOut: () => void;
  children: ReactNode;
}) {
  const [accountOpen, setAccountOpen] = useState(false);
  const account = useRef<HTMLDivElement>(null);
  const accountButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!accountOpen) return;
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !account.current?.contains(event.target)) {
        setAccountOpen(false);
      }
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [accountOpen]);
  const mode = runtime?.status.operations.mode;
  const health = controllerStatus(runtime, runtimeFreshness);
  const runtimeOld = runtimeFreshness === "stale" || runtimeFreshness === "unavailable";
  const topologyOld = topologyFreshness === "stale" || topologyFreshness === "unavailable";

  return (
    <div className="app-frame">
      <aside className="sidebar">
        <button
          className="brand"
          type="button"
          onClick={() => onNavigate("overview")}
          aria-label="Cueverge overview"
        >
          <span className="brand__mark" aria-hidden="true">
            <CircleGauge size={22} />
          </span>
          <span className="brand__wordmark">
            <strong>Cueverge</strong>
            <small>Show control</small>
          </span>
        </button>

        <nav className="primary-nav" aria-label="Primary">
          {navigation.map((item) => {
            const Icon = item.icon;
            return (
              <button
                type="button"
                key={item.id}
                aria-current={workspace === item.id ? "page" : undefined}
                onClick={() => onNavigate(item.id)}
                title={item.label}
              >
                <Icon aria-hidden="true" size={19} />
                <span className="primary-nav__label">{item.label}</span>
              </button>
            );
          })}
        </nav>

      </aside>

      <div className="app-frame__main">
        <header className="global-header">
          <div className="installation-context" aria-label="Selected installation">
            <span>Installation</span>
            <strong>{installation?.name ?? (installationUnavailable ? "Installation unavailable" : "Loading installation…")}</strong>
          </div>

          <div className="global-header__status">
            <StatusText tone={health.tone}>{health.label}</StatusText>
            {mode && runtimeFreshness === "fresh" && (
              <span className="mode-chip">Mode: {humanize(mode)}</span>
            )}
          </div>

          <div className="global-header__actions">
            <button
              className="icon-button"
              type="button"
              onClick={onRefresh}
              disabled={refreshing}
              aria-label="Refresh status"
              title="Refresh status"
            >
              <RefreshCw aria-hidden="true" size={17} className={refreshing ? "spin" : ""} />
            </button>
            {workspace !== "operate" && <button
              className="operate-link"
              type="button"
              onClick={() => onNavigate("operate")}
            >
              Open Operate
            </button>}
            <div
              className="account-disclosure"
              ref={account}
              onKeyDown={(event) => {
                if (event.key === "Escape" && accountOpen) {
                  setAccountOpen(false);
                  accountButton.current?.focus();
                }
              }}
            >
              <button
                className="account-button"
                ref={accountButton}
                aria-label={`Account: ${session.username}`}
                aria-expanded={accountOpen}
                aria-controls="account-details"
                onClick={() => setAccountOpen(!accountOpen)}
              >
                <span className="account-summary__avatar" aria-hidden="true">
                  {session.username.slice(0, 1).toUpperCase()}
                </span>
              </button>
              {accountOpen && (
                <section id="account-details" className="account-details" aria-label="Account details">
                  <strong>{session.username}</strong>
                  <span>{humanize(session.role)}</span>
                  <button onClick={onSignOut}>Sign out</button>
                </section>
              )}
            </div>
          </div>
        </header>

        <main
          className={`workspace${runtimeOld || topologyOld ? " workspace--last-known" : ""}`}
          id="main-content"
        >
          {sessionExpired && (
            <div className="snapshot-notice" role="alert">
              <strong>Your session has expired</strong>
              <span>Private information cannot be refreshed. Returning to sign-in discards unsaved changes in this tab.</span>
              {onReauthenticate && <button onClick={onReauthenticate}>Sign in again</button>}
            </div>
          )}
          {(runtimeOld || topologyOld) && (
            <div className="snapshot-notice" role="status">
              <strong>{runtimeOld ? health.label : "Device status is not current"}</strong>
              <span>Information below may be last known, not live. Refresh status to retry.</span>
            </div>
          )}
          {configurationError && <div className="snapshot-notice" role="alert">{configurationError}</div>}
          {children}
        </main>
      </div>
    </div>
  );
}

function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
