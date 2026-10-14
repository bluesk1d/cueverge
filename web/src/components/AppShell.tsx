import {
  Activity,
  ChevronDown,
  CircleGauge,
  Home,
  Layers3,
  LogOut,
  MonitorCog,
  RefreshCw,
  Settings,
  ShieldCheck,
} from "lucide-react";
import { ReactNode } from "react";

import {
  InstallationStatus,
  InstallationSummary,
  RuntimeStatus,
  Session,
} from "../api";
import { StatusText } from "./ui";

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
  installations,
  runtime,
  runtimeError,
  refreshing,
  onNavigate,
  onRefresh,
  onSignOut,
  children,
}: {
  session: Session;
  workspace: Workspace;
  installation: InstallationStatus | null;
  installations: InstallationSummary[];
  runtime: RuntimeStatus | null;
  runtimeError: boolean;
  refreshing: boolean;
  onNavigate: (workspace: Workspace) => void;
  onRefresh: () => void;
  onSignOut: () => void;
  children: ReactNode;
}) {
  const mode = runtime?.status.operations.mode;
  const health = runtimeError ? (
    <StatusText tone="danger">Status unavailable</StatusText>
  ) : runtime?.status.degraded ? (
    <StatusText tone="warning">Controller degraded</StatusText>
  ) : runtime ? (
    <StatusText tone="success">Controller observed</StatusText>
  ) : (
    <StatusText tone="neutral">Checking status</StatusText>
  );

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

        <div className="sidebar__footer">
          <ShieldCheck aria-hidden="true" size={17} />
          <span>
            <strong>Authenticated</strong>
            <small>{session.role}</small>
          </span>
        </div>
      </aside>

      <div className="app-frame__main">
        <header className="global-header">
          <div className="installation-switcher">
            <label htmlFor="installation-select">Installation</label>
            <div className="installation-switcher__control">
              <select
                id="installation-select"
                value={installation?.installation_id ?? ""}
                onChange={() => undefined}
              >
                {!installation && <option value="">Loading installation…</option>}
                {installations.map((item) => (
                  <option
                    disabled={!item.runtime_active}
                    key={item.id}
                    value={item.id}
                  >
                    {item.name}
                    {item.runtime_active ? "" : " (not provisioned)"}
                  </option>
                ))}
              </select>
              <ChevronDown aria-hidden="true" size={15} />
            </div>
          </div>

          <div className="global-header__status">
            {health}
            {mode && <span className="mode-chip">Mode: {humanize(mode)}</span>}
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
            <button
              className="operate-link"
              type="button"
              onClick={() => onNavigate("operate")}
            >
              Open Operate
            </button>
            <span className="account-summary">
              <span className="account-summary__avatar" aria-hidden="true">
                {session.username.slice(0, 1).toUpperCase()}
              </span>
              <span>
                <strong>{session.username}</strong>
                <small>{session.role}</small>
              </span>
            </span>
            <button
              className="icon-button"
              type="button"
              onClick={onSignOut}
              aria-label="Sign out"
              title="Sign out"
            >
              <LogOut aria-hidden="true" size={17} />
            </button>
          </div>
        </header>

        <main className="workspace" id="main-content">
          {children}
        </main>
      </div>
    </div>
  );
}

function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
