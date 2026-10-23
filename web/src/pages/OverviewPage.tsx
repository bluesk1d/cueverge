import { Activity, ImageOff, Lightbulb, MonitorPlay } from "lucide-react";
import { useEffect, useState } from "react";

import {
  ActivityEvent,
  getInstallationPhotos,
  InstallationPhoto,
  installationPhotoUrl,
  InstallationStatus,
  RuntimeStatus,
  Topology,
} from "../api";
import type { Workspace } from "../components/AppShell";
import { EmptyState, ErrorState, Panel, Skeleton, StatusText, WorkspaceHeader } from "../components/ui";
import { connectionStatus, controllerStatus, SnapshotFreshness } from "../status";

export function OverviewPage({
  installation,
  runtime,
  topology,
  activity = [],
  installationError,
  runtimeError,
  activityError = false,
  runtimeFreshness,
  topologyFreshness,
  loading,
  onNavigate,
  onManageDevices,
  refreshKey = 0,
}: {
  installation: InstallationStatus | null;
  runtime: RuntimeStatus | null;
  topology: Topology | null;
  activity?: ActivityEvent[];
  installationError: boolean;
  runtimeError: boolean;
  activityError?: boolean;
  runtimeFreshness: SnapshotFreshness;
  topologyFreshness: SnapshotFreshness;
  loading: boolean;
  onNavigate: (workspace: Workspace) => void;
  onManageDevices?: () => void;
  refreshKey?: number;
}) {
  const [photo, setPhoto] = useState<InstallationPhoto | null>(null);
  const [photoLoading, setPhotoLoading] = useState(true);
  const [photoError, setPhotoError] = useState(false);
  const installationId = installation?.installation_id;
  useEffect(() => {
    setPhoto(null);
    setPhotoError(false);
    if (!installationId) {
      setPhotoLoading(false);
      return;
    }
    let current = true;
    setPhotoLoading(true);
    getInstallationPhotos(installationId).then(
      (photos) => { if (current) setPhoto(photos[0] ?? null); },
      () => { if (current) setPhotoError(true); },
    ).finally(() => { if (current) setPhotoLoading(false); });
    return () => { current = false; };
  }, [installationId, refreshKey]);

  const current = runtime !== null && runtimeFreshness === "fresh";
  const health = controllerStatus(runtime, runtimeFreshness);
  const playing = runtime?.status.active_scene != null;
  const playback = runtime
    ? humanize(runtime.status.playback_state ?? (playing ? "State unavailable" : "Stopped"))
    : "Checking playback";
  const playbackLabel = current ? playback
    : runtime ? `Last known: ${playback}` : runtimeError ? "Playback unavailable" : "Checking playback";
  const groups = groupActivity(activity.filter(
    (event) => event.category !== "security" || event.action.startsWith("package.") || activityFailure(event),
  )).slice(0, 4);
  const featuredIds = new Set(groups.flat().map((event) => event.id));
  const history = groupActivity(activity.filter((event) => !featuredIds.has(event.id)));
  const resources = installation?.resources ?? [];

  return (
    <>
      <WorkspaceHeader
        eyebrow="Your display"
        title="Installation Overview"
        description="See what is happening and choose what to work on next."
      />
      {installationError && (
        <ErrorState title="Installation unavailable" description="Setup information could not be refreshed." />
      )}
      <section className="overview-hero" aria-label="Installation summary">
        <div className="overview-hero__photo">
          {photoLoading || (loading && !installation) ? <Skeleton /> : photoError || (installationError && !installation) ? (
            <ErrorState title="Photo unavailable" description="The installation photo could not be loaded." />
          ) : photo ? (
            <img
              src={installationPhotoUrl(photo.id)}
              alt={`${photo.name} · ${photo.viewpoint}`}
              onError={() => setPhotoError(true)}
            />
          ) : (
            <div className="overview-hero__placeholder">
              <ImageOff aria-hidden="true" size={28} />
              <div>
                <strong>Add a photo of your setup</strong>
                <p>Use it to place your lights and projections.</p>
                <button onClick={() => onNavigate("installation")}>Add a photo</button>
              </div>
            </div>
          )}
        </div>
        <div className="overview-hero__identity">
          <h2>{installation?.name ?? (loading ? "Loading installation…" : "Installation unavailable")}</h2>
          <StatusText tone={current && playing ? runtime.status.degraded ? "warning" : "success" : "neutral"}>
            {playbackLabel}
          </StatusText>
          {runtime?.status.active_scene && (
            <p>{current ? "Scene" : "Last known scene"}: <strong>{runtime.status.active_scene}</strong></p>
          )}
          {current && runtime.status.estimated_position_ms !== null && playing && (
            <p>Position {formatDuration(runtime.status.estimated_position_ms)}</p>
          )}
          <StatusText tone={health.tone}>{health.label}</StatusText>
          {runtime && (
            <small>
              Last checked <time dateTime={runtime.observed_at}>
                {new Date(runtime.observed_at).toLocaleTimeString()}
              </time>
              {!current && " · not a live reading"}
            </small>
          )}
          {current && runtime.status.degraded && (
            <p>Check your equipment before relying on the display.</p>
          )}
        </div>
      </section>

      <nav className="overview-actions" aria-label="Display shortcuts">
        <button className="primary-button" onClick={() => onNavigate("shows")}>Open shows</button>
        <button onClick={() => onNavigate("installation")}>Layout &amp; devices</button>
        <button onClick={() => onNavigate("operate")}>Open controls</button>
      </nav>

      <div className="overview-grid">
        <Panel title="Device Health" description="Device connections, not proof of visible output." className="overview-grid__devices">
          {installationError ? <ErrorState description="Equipment configuration could not be refreshed." />
            : loading && !installation ? <Skeleton />
              : resources.length ? (
                <ul className="device-list">
                  {resources.map((resource) => {
                    const node = topologyFreshness === "fresh"
                      ? topology?.nodes.find((candidate) => candidate.assigned_resources.includes(resource.resource))
                      : undefined;
                    const status = node ? connectionStatus(node) : { tone: "neutral" as const, label: "Unknown" };
                    const outputError = current && runtime.status.lighting_outputs.find(
                      (output) => output.resource === resource.resource,
                    )?.error;
                    return (
                      <li key={resource.resource}>
                        <span className="device-list__icon" aria-hidden="true">
                          {resource.kind === "media_agent" ? <MonitorPlay size={17} /> : <Lightbulb size={17} />}
                        </span>
                        <span className="device-list__identity">
                          <strong>{humanize(resource.resource)}</strong>
                          <small>{node?.label ?? resource.target ?? resource.resource}</small>
                        </span>
                        <StatusText tone={outputError ? "danger" : status.tone}>
                          {outputError ? "Output error" : status.label}
                        </StatusText>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <EmptyState title="Add your equipment" description="Connect lights and media devices in Installation." compact />
              )}
          <button className="overview-panel-action" onClick={onManageDevices ?? (() => onNavigate("installation"))}>Manage devices</button>
        </Panel>

        <Panel title="Recent Activity" description="Notable changes, with the full audit details available." className="overview-grid__activity">
          {activityError ? <ErrorState description="Recent activity could not be refreshed." /> : (
            <>
              {groups.length ? (
                <ol className="activity-list">
                  {groups.map((group) => <ActivityGroup key={group[0].id} events={group} />)}
                </ol>
              ) : <EmptyState title="No recent display changes" description="Equipment and show changes will appear here." compact />}
              {history.length > 0 && (
                <details className="overview-details">
                  <summary>More activity ({history.flat().length} events)</summary>
                  <ol className="activity-list">
                    {history.map((group) => <ActivityGroup key={group[0].id} events={group} />)}
                  </ol>
                </details>
              )}
            </>
          )}
        </Panel>
      </div>

      <details className="overview-details">
        <summary>Setup checklist</summary>
        <ul>
          <li>Equipment: {installationError ? "Could not be refreshed." : loading && !installation ? "Checking setup…" : resources.length ? `${resources.length} outputs configured; check connections above.` : "Add equipment in Installation."}</li>
          <li>House photo: {photoLoading ? "Checking…" : photo ? "Added." : photoError || installationError ? "Could not be checked." : "Add one in Installation."}</li>
          <li>Interactive inputs: guided source setup is not yet available.</li>
          <li>Schedule: not yet available. No next start or autonomous scene is predicted.</li>
        </ul>
      </details>
      <details className="overview-details">
        <summary>System details</summary>
        <dl className="detail-list">
          <div><dt>Loaded show</dt><dd>Show identity unavailable</dd></div>
          <div><dt>Configured media outputs</dt><dd>{resources.filter((resource) => resource.kind === "media_agent").length}</dd></div>
          <div><dt>Configured LED pixels</dt><dd>{resources.reduce((count, resource) => count + (resource.pixel_count ?? 0), 0).toLocaleString()}</dd></div>
          <div><dt>Package schema</dt><dd>{installation?.package_schema ?? "Unavailable"}</dd></div>
          <div><dt>Controller detail</dt><dd>{runtime?.status.degraded_reason ?? "No additional detail reported"}</dd></div>
        </dl>
        <p>The controller has not reported a loaded show name. Saved edits are separate from what is running.</p>
      </details>
    </>
  );
}

export function groupActivity(events: ActivityEvent[]): ActivityEvent[][] {
  const groups: ActivityEvent[][] = [];
  for (const event of events) {
    const previous = groups[groups.length - 1];
    const first = previous?.[0];
    if (first && isConnectionEvent(event) && isConnectionEvent(first) &&
      event.target === first.target &&
      Math.abs(Date.parse(first.occurred_at) - Date.parse(event.occurred_at)) <= 60000) {
      previous.push(event);
    } else {
      groups.push([event]);
    }
  }
  return groups;
}

function isConnectionEvent(event: ActivityEvent): boolean {
  return event.result === "success" &&
    (event.action === "device.connected" || event.action === "device.disconnected");
}

export function activityFailure(event: ActivityEvent): boolean {
  return ["error", "failed", "denied", "timeout", "timed_out", "abandoned", "blocked"].includes(event.result)
    || event.action === "browser.error" || event.action.endsWith(".failed");
}

function ActivityGroup({ events }: { events: ActivityEvent[] }) {
  const event = events[0];
  const failed = activityFailure(event);
  const disconnected = event.action === "device.disconnected";
  const repeated = events.length > 2;
  return (
    <li className="overview-activity">
      <Activity aria-hidden="true" size={17} />
      <div>
        <strong>{event.action === "browser.error" ? "Browser issue reported" : humanize(event.action)}</strong>
        <small>
          {event.action === "browser.error" ? "Diagnostics in audit details"
            : event.category === "project" ? "Show workspace"
              : event.target}
          {event.actor !== event.target && ` · ${event.actor}`}
        </small>
        <StatusText tone={failed ? "danger" : disconnected || repeated ? "warning" : "neutral"}>
          {failed ? humanize(event.result) : repeated ? "Repeated connection changes" : disconnected ? "Disconnected" : "Recorded"}
        </StatusText>
        <time dateTime={event.occurred_at} title={new Date(event.occurred_at).toLocaleString()}>
          {relativeTime(event.occurred_at)}
        </time>
        <details>
          <summary>{events.length > 1 ? `${events.length} connection changes · audit details` : "Audit details"}</summary>
          <ul>
            {events.map((item) => (
              <li key={item.id}>
                {humanize(item.action)} · {new Date(item.occurred_at).toLocaleString()} ·
                actor: {item.actor} · target: {item.target} · audit result: {item.result}
              </li>
            ))}
          </ul>
        </details>
      </div>
    </li>
  );
}

function relativeTime(value: string): string {
  const seconds = Math.round((Date.parse(value) - Date.now()) / 1000);
  if (!Number.isFinite(seconds)) return "Time unavailable";
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (Math.abs(seconds) < 60) return formatter.format(seconds, "second");
  if (Math.abs(seconds) < 3600) return formatter.format(Math.round(seconds / 60), "minute");
  if (Math.abs(seconds) < 86400) return formatter.format(Math.round(seconds / 3600), "hour");
  return formatter.format(Math.round(seconds / 86400), "day");
}

function formatDuration(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function humanize(value: string): string {
  return value.replace(/[._-]/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
