import {
  Activity,
  Clock3,
  ImageOff,
  Lightbulb,
  MonitorPlay,
  Radio,
  Server,
} from "lucide-react";

import {
  ActivityEvent,
  InstallationResource,
  InstallationStatus,
  LightingOutputStatus,
  RuntimeStatus,
} from "../api";
import {
  EmptyState,
  ErrorState,
  Panel,
  Skeleton,
  StatusText,
  StatusTone,
  WorkspaceHeader,
} from "../components/ui";

export function OverviewPage({
  installation,
  runtime,
  activity = [],
  installationError,
  runtimeError,
  activityError = false,
  loading,
}: {
  installation: InstallationStatus | null;
  runtime: RuntimeStatus | null;
  activity?: ActivityEvent[];
  installationError: boolean;
  runtimeError: boolean;
  activityError?: boolean;
  loading: boolean;
}) {
  const mediaResources =
    installation?.resources.filter((resource) => resource.kind === "media_agent") ?? [];
  const lightingResources =
    installation?.resources.filter((resource) => resource.kind === "pixel_output") ?? [];
  const pixelCount = lightingResources.reduce(
    (total, resource) => total + (resource.pixel_count ?? 0),
    0,
  );

  return (
    <>
      <WorkspaceHeader
        eyebrow="Installation"
        title="Installation Overview"
        description={
          installation
            ? `${installation.name} · Logical resources and observed controller status`
            : "Logical resources and observed controller status"
        }
        meta={
          runtime && (
            <div className="freshness">
              <Clock3 aria-hidden="true" size={15} />
              <span>
                Data observed <time dateTime={runtime.observed_at}>{formatObserved(runtime)}</time>
              </span>
            </div>
          )
        }
      />

      {installationError && (
        <ErrorState
          title="Installation unavailable"
          description="The selected installation could not be read. Runtime status is shown only where available."
        />
      )}

      <section className="overview-hero" aria-label="Installation summary">
        <div className="overview-hero__placeholder">
          <ImageOff aria-hidden="true" size={28} />
          <div>
            <strong>No installation image configured</strong>
            <p>A registered installation photo will appear here when the mapper supports it.</p>
          </div>
        </div>
        <div className="overview-hero__identity">
          <span className="eyebrow">Selected installation</span>
          <h2>{installation?.name ?? (loading ? "Loading…" : "Unavailable")}</h2>
          <p>
            {installation
              ? `Runtime package compatibility: ${installation.package_schema}`
              : "No package details are available."}
          </p>
        </div>
      </section>

      <section className="health-summary" aria-label="Installation health summary">
        <SummaryMetric
          icon={MonitorPlay}
          label="Media outputs"
          value={installation ? String(mediaResources.length) : "—"}
          detail="Configured logical outputs"
          tone="info"
        />
        <SummaryMetric
          icon={Lightbulb}
          label="LED pixels"
          value={installation ? pixelCount.toLocaleString() : "—"}
          detail="Configured across pixel outputs"
          tone="warning"
        />
        <SummaryMetric
          icon={Radio}
          label="Connected agents"
          value={runtime ? String(runtime.status.connected_agents.length) : "—"}
          detail="Observed media-agent connections"
          tone={runtimeError ? "danger" : runtime ? "success" : "neutral"}
        />
        <SummaryMetric
          icon={Server}
          label="Controller"
          value={runtimeError ? "Unavailable" : runtime?.status.degraded ? "Degraded" : "Observed"}
          detail={
            runtimeError
              ? "Request failed"
              : runtime?.status.degraded_reason
                ? humanize(runtime.status.degraded_reason)
                : "Current status response"
          }
          tone={runtimeError ? "danger" : runtime?.status.degraded ? "warning" : "success"}
        />
      </section>

      <div className="overview-grid">
        <Panel
          title="Active Show"
          description="Deployment and release identity"
          className="overview-grid__show"
        >
          <EmptyState
            compact
            title="Show identity unavailable"
            description={
              runtime?.status.active_scene
                ? "The controller is running a scene, but the current API does not expose an active show release."
                : "No active scene is reported, and the current API does not expose deployment metadata."
            }
          />
        </Panel>

        <Panel
          title="Current Scene"
          description="Controller-authoritative presentation state"
          className="overview-grid__scene"
        >
          {runtimeError ? (
            <ErrorState description="Current scene state could not be observed." />
          ) : loading && !runtime ? (
            <Skeleton />
          ) : runtime?.status.active_scene ? (
            <div className="scene-status">
              <StatusText tone={runtime.status.degraded ? "warning" : "success"}>
                {runtime.status.playback_state
                  ? humanize(runtime.status.playback_state)
                  : "State unavailable"}
              </StatusText>
              <h3>{runtime.status.active_scene}</h3>
              <dl className="detail-list">
                <div>
                  <dt>Position</dt>
                  <dd className="tabular">
                    {runtime.status.estimated_position_ms === null
                      ? "Position unavailable"
                      : formatDuration(runtime.status.estimated_position_ms)}
                  </dd>
                </div>
                <div>
                  <dt>Render loop</dt>
                  <dd>{runtime.status.render_loop_running ? "Running" : "Stopped"}</dd>
                </div>
                <div>
                  <dt>Pending commands</dt>
                  <dd>{runtime.status.pending_commands}</dd>
                </div>
              </dl>
            </div>
          ) : (
            <EmptyState
              compact
              title="Stopped"
              description="The controller does not report an active scene."
            />
          )}
        </Panel>

        <Panel
          title="Device Health"
          description="Configured resources and observed connectivity"
          className="overview-grid__devices"
        >
          {installationError ? (
            <ErrorState description="Configured resources could not be loaded." />
          ) : loading && !installation ? (
            <Skeleton lines={4} />
          ) : installation?.resources.length ? (
            <ul className="device-list">
              {installation.resources.map((resource) => (
                <DeviceRow
                  key={resource.resource}
                  resource={resource}
                  runtime={runtime}
                  runtimeError={runtimeError}
                />
              ))}
            </ul>
          ) : (
            <EmptyState
              compact
              title="No configured resources"
              description="The selected installation package contains no logical resources."
            />
          )}
        </Panel>

        <Panel
          title="Recent Activity"
          description="Authoritative controller and deployment events"
          className="overview-grid__activity"
        >
          {activityError ? (
            <ErrorState description="Recent activity could not be loaded." />
          ) : activity.length ? (
            <ol className="activity-list">
              {activity.slice(0, 8).map((event) => (
                <li key={event.id}>
                  <span className={`activity-list__icon activity-list__icon--${event.category}`}>
                    <Activity aria-hidden="true" size={15} />
                  </span>
                  <div>
                    <strong>{humanize(event.action)}</strong>
                    <small>
                      {event.target} · {event.actor}
                    </small>
                  </div>
                  <div className="activity-list__meta">
                    <StatusText tone={event.result === "success" ? "success" : "danger"}>
                      {humanize(event.result)}
                    </StatusText>
                    <time dateTime={event.occurred_at}>
                      {new Date(event.occurred_at).toLocaleTimeString()}
                    </time>
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <EmptyState
              compact
              title="No recent activity"
              description="Audited controller and device events will appear here."
            />
          )}
        </Panel>

        <Panel
          title="Schedule"
          description="Installation-level operating windows"
          className="overview-grid__schedule"
        >
          <EmptyState
            compact
            title="Not configured"
            description="The current runtime does not expose an installation schedule. No next scene or start time is predicted."
          />
        </Panel>
      </div>
    </>
  );
}

function SummaryMetric({
  icon: Icon,
  label,
  value,
  detail,
  tone,
}: {
  icon: typeof Activity;
  label: string;
  value: string;
  detail: string;
  tone: StatusTone;
}) {
  return (
    <article className="summary-metric">
      <span className={`summary-metric__icon summary-metric__icon--${tone}`} aria-hidden="true">
        <Icon size={20} />
      </span>
      <div>
        <span>{label}</span>
        <strong className="tabular">{value}</strong>
        <small>{detail}</small>
      </div>
    </article>
  );
}

function DeviceRow({
  resource,
  runtime,
  runtimeError,
}: {
  resource: InstallationResource;
  runtime: RuntimeStatus | null;
  runtimeError: boolean;
}) {
  const status = resourceStatus(resource, runtime, runtimeError);
  return (
    <li>
      <span className="device-list__icon" aria-hidden="true">
        {resource.kind === "media_agent" ? <MonitorPlay size={17} /> : <Lightbulb size={17} />}
      </span>
      <span className="device-list__identity">
        <strong>{resource.resource}</strong>
        <small>
          {resource.kind === "media_agent"
            ? resource.target ?? "No agent target"
            : `${resource.pixel_count ?? "Unknown"} configured pixels`}
        </small>
      </span>
      <StatusText tone={status.tone}>{status.label}</StatusText>
    </li>
  );
}

function resourceStatus(
  resource: InstallationResource,
  runtime: RuntimeStatus | null,
  runtimeError: boolean,
): { tone: StatusTone; label: string } {
  if (runtimeError || !runtime) {
    return { tone: "neutral", label: "Unknown" };
  }
  if (resource.kind === "media_agent") {
    return resource.target && runtime.status.connected_agents.includes(resource.target)
      ? { tone: "success", label: "Connected" }
      : { tone: "warning", label: "Disconnected" };
  }
  const activeOutput: LightingOutputStatus | undefined = runtime.status.lighting_outputs.find(
    (output) => output.resource === resource.resource,
  );
  if (activeOutput?.error) {
    return { tone: "danger", label: "Output error" };
  }
  return { tone: "neutral", label: "Not independently detectable" };
}

function formatObserved(runtime: RuntimeStatus): string {
  return new Date(runtime.observed_at).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatDuration(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function humanize(value: string): string {
  return value.replace(/[._]/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
