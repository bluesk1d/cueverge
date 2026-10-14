import { useState } from "react";
import {
  Cable,
  Clock3,
  Lightbulb,
  MonitorPlay,
  Network,
  Server,
} from "lucide-react";

import {
  createPairingToken,
  InstallationStatus,
  PairingToken,
  removeResourceAssignment,
  ResourceAssignment,
  Session,
  setDeviceConnectionPolicy,
  setDeviceEnrollmentState,
  setResourceAssignment,
  Topology,
  TopologyNode,
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

export function InstallationPage({
  installation,
  topology,
  assignments = [],
  installationError,
  topologyError,
  loading,
  session,
  onRefresh,
}: {
  installation: InstallationStatus | null;
  topology: Topology | null;
  assignments?: ResourceAssignment[];
  installationError: boolean;
  topologyError: boolean;
  loading: boolean;
  session?: Session;
  onRefresh?: () => Promise<void> | void;
}) {
  const controller = topology?.nodes.find((node) => node.kind === "controller");
  const agents =
    topology?.nodes.filter(
      (node) => node.kind === "direct_media_agent" || node.kind === "downstream_controller",
    ) ?? [];
  const directAgents = agents.filter((node) => node.kind === "direct_media_agent");
  const downstreamControllers = agents.filter((node) => node.kind === "downstream_controller");
  const outputs = topology?.nodes.filter((node) => node.kind === "controller_output") ?? [];

  return (
    <>
      <WorkspaceHeader
        eyebrow="Installation"
        title="Network Topology"
        description="Configured resources and controller-observed endpoint state. No control actions are available."
        meta={
          topology && (
            <div className="freshness">
              <Clock3 aria-hidden="true" size={15} />
              <span>
                Topology observed{" "}
                <time dateTime={topology.observed_at}>
                  {new Date(topology.observed_at).toLocaleTimeString()}
                </time>
              </span>
            </div>
          )
        }
      />

      <section className="topology-summary" aria-label="Topology summary">
        <Summary
          label="Controller"
          value={controller ? "Observed" : topologyError ? "Unavailable" : "Loading"}
          detail="Local runtime process"
          tone={controller ? "success" : topologyError ? "danger" : "neutral"}
        />
        <Summary
          label="Direct media agents"
          value={topology ? String(directAgents.length) : "—"}
          detail={`${directAgents.filter((node) => node.connection === "connected").length} connected`}
          tone={
            directAgents.some((node) => node.connection === "disconnected") ? "warning" : "info"
          }
        />
        <Summary
          label="Controller outputs"
          value={topology ? String(outputs.length) : "—"}
          detail={`${outputs.filter((node) => node.connection === "connected").length} connected`}
          tone={outputs.some((node) => node.connection === "disconnected") ? "warning" : "info"}
        />
        <Summary
          label="Downstream controllers"
          value={topology ? String(downstreamControllers.length) : "—"}
          detail={`${downstreamControllers.filter((node) => node.connection === "connected").length} connected`}
          tone={
            downstreamControllers.some((node) => node.connection === "disconnected")
              ? "warning"
              : "neutral"
          }
        />
      </section>

      <div className="topology-layout">
        <Panel
          title="Topology"
          description="Controller ownership and observed direct connections"
          className="topology-layout__map"
        >
          {topologyError ? (
            <ErrorState description="The controller topology could not be observed." />
          ) : loading && !topology ? (
            <Skeleton lines={6} />
          ) : controller ? (
            <div className="topology-tree">
              <NodeCard node={controller} />
              <div className="topology-tree__children">
                {agents.map((node) => (
                  <NodeCard key={node.id} node={node} />
                ))}
                {outputs.map((node) => (
                  <NodeCard key={node.id} node={node} />
                ))}
              </div>
            </div>
          ) : (
            <EmptyState
              title="No topology available"
              description="The controller returned no topology nodes."
            />
          )}
        </Panel>

        <Panel
          title="Installation Package"
          description="Logical resources in the active runtime package"
          className="topology-layout__resources"
        >
          {installationError ? (
            <ErrorState description="The active package resources could not be loaded." />
          ) : loading && !installation ? (
            <Skeleton lines={4} />
          ) : installation?.resources.length ? (
            <ul className="resource-list">
              {installation.resources.map((resource) => (
                <li key={resource.resource}>
                  <div>
                    <strong>{resource.resource}</strong>
                    <small>{humanize(resource.kind)}</small>
                  </div>
                  <span>
                    {resource.target ?? `${resource.pixel_count ?? "Unknown"} configured pixels`}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              title="No configured resources"
              description="The active installation package contains no logical resources."
            />
          )}
        </Panel>
      </div>
      {session?.role === "administrator" && topology && (
        <DeviceManagement
          agents={agents}
          installation={installation}
          assignments={assignments}
          enrollmentSupported={topology.enrollment_supported}
          onRefresh={onRefresh}
        />
      )}
    </>
  );
}

function DeviceManagement({
  agents,
  installation,
  assignments,
  enrollmentSupported,
  onRefresh,
}: {
  agents: TopologyNode[];
  installation: InstallationStatus | null;
  assignments: ResourceAssignment[];
  enrollmentSupported: boolean;
  onRefresh?: () => Promise<void> | void;
}) {
  const [deviceKind, setDeviceKind] =
    useState<PairingToken["device_kind"]>("direct_media_agent");
  const [pairingToken, setPairingToken] = useState<PairingToken | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function createToken() {
    setCreating(true);
    setError(null);
    try {
      setPairingToken(await createPairingToken(deviceKind));
    } catch {
      setError("Pairing token creation failed.");
    } finally {
      setCreating(false);
    }
  }

  return (
    <Panel
      title="Device Management"
      description="Administrator-only enrollment and connection expectations"
      className="device-management"
    >
      <div className="device-management__grid">
        <section aria-labelledby="pair-device-title">
          <h3 id="pair-device-title">Pair a device</h3>
          {enrollmentSupported ? (
            <>
              <label>
                Device kind
                <select
                  value={deviceKind}
                  onChange={(event) =>
                    setDeviceKind(event.target.value as PairingToken["device_kind"])
                  }
                >
                  <option value="direct_media_agent">Direct media agent</option>
                  <option value="downstream_controller">Downstream controller</option>
                </select>
              </label>
              <button
                className="primary-button"
                disabled={creating}
                onClick={() => void createToken()}
              >
                {creating ? "Creating…" : "Create one-time token"}
              </button>
              {pairingToken && (
                <div className="pairing-token" role="status">
                  <strong>Copy this token now</strong>
                  <code>{pairingToken.pairing_token}</code>
                  <small>
                    Expires {new Date(pairingToken.expires_at).toLocaleTimeString()} and can
                    be used once.
                  </small>
                </div>
              )}
            </>
          ) : (
            <p className="muted">Device enrollment is disabled on this controller.</p>
          )}
        </section>
        <section aria-labelledby="connection-policy-title">
          <h3 id="connection-policy-title">Connection expectations</h3>
          <div className="policy-list">
            {agents.map((agent) => (
              <ConnectionPolicy
                key={agent.id}
                node={agent}
                onRefresh={onRefresh}
                onError={() => setError("Connection policy update failed.")}
              />
            ))}
          </div>
        </section>
        <section aria-labelledby="device-lifecycle-title">
          <h3 id="device-lifecycle-title">Device lifecycle</h3>
          <div className="policy-list">
            {agents.map((agent) => (
              <DeviceLifecycle
                key={agent.id}
                node={agent}
                onRefresh={onRefresh}
                onError={() => setError("Device lifecycle update failed.")}
              />
            ))}
          </div>
        </section>
        <section aria-labelledby="resource-assignment-title">
          <h3 id="resource-assignment-title">Resource assignments</h3>
          <div className="policy-list">
            {installation?.resources
              .filter((resource) => resource.kind === "media_agent")
              .map((resource) => (
                <ResourceAssignmentControl
                  key={resource.resource}
                  resource={resource.resource}
                  agents={agents}
                  assignment={assignments.find(
                    (assignment) => assignment.resource === resource.resource,
                  )}
                  onRefresh={onRefresh}
                  onError={() =>
                    setError(
                      "Assignment update failed. Devices must be disconnected and playback idle.",
                    )
                  }
                />
              ))}
          </div>
        </section>
      </div>
      {error && <p role="alert">{error}</p>}
    </Panel>
  );
}

function DeviceLifecycle({
  node,
  onRefresh,
  onError,
}: {
  node: TopologyNode;
  onRefresh?: () => Promise<void> | void;
  onError: () => void;
}) {
  const [saving, setSaving] = useState(false);

  async function update(
    state: "enrolled" | "suspended" | "revoked",
    confirmation: string,
  ) {
    if (!window.confirm(confirmation)) {
      return;
    }
    setSaving(true);
    try {
      await setDeviceEnrollmentState(node.id.replace("device:", ""), state);
      await onRefresh?.();
    } catch {
      onError();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="policy-row device-lifecycle-row">
      <div>
        <strong>{node.label}</strong>
        <small>
          {humanize(node.enrollment)} · {connectionStatus(node).label}
        </small>
      </div>
      <div className="device-actions">
        {node.enrollment === "enrolled" && (
          <button
            disabled={saving}
            onClick={() =>
              void update(
                "suspended",
                `Suspend ${node.label}? Its authenticated session will be rejected.`,
              )
            }
          >
            Suspend
          </button>
        )}
        {node.enrollment === "suspended" && (
          <button
            disabled={saving}
            onClick={() =>
              void update("enrolled", `Resume ${node.label} using its active certificate?`)
            }
          >
            Resume
          </button>
        )}
        {node.enrollment !== "revoked" && (
          <button
            className="danger-button"
            disabled={saving}
            onClick={() =>
              void update(
                "revoked",
                `Permanently revoke ${node.label}? This cannot be undone and requires new enrollment.`,
              )
            }
          >
            Revoke
          </button>
        )}
        {node.enrollment === "revoked" && (
          <span className="muted">No actions available</span>
        )}
      </div>
    </div>
  );
}

function ResourceAssignmentControl({
  resource,
  agents,
  assignment,
  onRefresh,
  onError,
}: {
  resource: string;
  agents: TopologyNode[];
  assignment?: ResourceAssignment;
  onRefresh?: () => Promise<void> | void;
  onError: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const currentNode = assignment
    ? agents.find((agent) => agent.id === `device:${assignment.device_id}`)
    : undefined;
  const locked = currentNode?.connection === "connected";
  const candidates = agents.filter(
    (agent) =>
      agent.capabilities.includes("media_playback") &&
      (agent.connection === "disconnected" || agent.id === currentNode?.id),
  );

  async function updateAssignment(deviceId: string) {
    setSaving(true);
    try {
      if (deviceId) {
        await setResourceAssignment(
          resource,
          deviceId,
          "media_playback",
          assignment?.revision ?? null,
        );
      } else if (assignment) {
        await removeResourceAssignment(resource, assignment.revision);
      }
      await onRefresh?.();
    } catch {
      onError();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="policy-row">
      <div>
        <strong>{resource}</strong>
        <small>
          {locked
            ? "Disconnect the current device before changing this assignment"
            : "Changes apply on the device's next authenticated connection"}
        </small>
      </div>
      <label>
        <span className="sr-only">Assigned device for {resource}</span>
        <select
          aria-label={`Assigned device for ${resource}`}
          disabled={saving || locked}
          value={assignment?.device_id ?? ""}
          onChange={(event) => void updateAssignment(event.target.value)}
        >
          <option value="">Unassigned</option>
          {candidates.map((agent) => (
            <option key={agent.id} value={agent.id.replace("device:", "")}>
              {agent.label}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

function ConnectionPolicy({
  node,
  onRefresh,
  onError,
}: {
  node: TopologyNode;
  onRefresh?: () => Promise<void> | void;
  onError: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const policy = node.connection_policy === "on_demand" ? "on_demand" : "continuous";

  async function updatePolicy(nextPolicy: "continuous" | "on_demand") {
    setSaving(true);
    try {
      await setDeviceConnectionPolicy(node.id.replace("device:", ""), nextPolicy);
      await onRefresh?.();
    } catch {
      onError();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="policy-row">
      <div>
        <strong>{node.label}</strong>
        <small>{node.assigned_resources.join(", ") || "Unassigned"}</small>
      </div>
      <label>
        <span className="sr-only">Connection policy for {node.label}</span>
        <select
          aria-label={`Connection policy for ${node.label}`}
          disabled={saving}
          value={policy}
          onChange={(event) =>
            void updatePolicy(event.target.value as "continuous" | "on_demand")
          }
        >
          <option value="continuous">Continuous</option>
          <option value="on_demand">On demand</option>
        </select>
      </label>
    </div>
  );
}

function NodeCard({ node }: { node: TopologyNode }) {
  const connection = connectionStatus(node);
  const Icon =
    node.kind === "controller"
      ? Server
      : node.kind === "direct_media_agent"
        ? MonitorPlay
        : node.kind === "downstream_controller"
          ? Network
          : Lightbulb;

  return (
    <article className={`topology-node topology-node--${node.kind}`}>
      <header>
        <span className="topology-node__icon" aria-hidden="true">
          <Icon size={19} />
        </span>
        <div>
          <span>{humanize(node.kind)}</span>
          <h3>{node.label}</h3>
        </div>
        <StatusText tone={connection.tone}>{connection.label}</StatusText>
      </header>
      {(node.kind === "direct_media_agent" || node.kind === "downstream_controller") && (
        <dl className="topology-node__details">
          <Detail
            label="Protocol v2 assignment"
            value={
              node.assigned_resources.length
                ? node.assigned_resources.join(", ")
                : "No logical resource assigned"
            }
          />
          <Detail label="Enrollment" value={enrollmentLabel(node)} />
          <Detail label="Compatibility" value={humanize(node.compatibility)} />
          <Detail
            label="Protocol"
            value={node.protocol_version === null ? "Not observed" : `v${node.protocol_version}`}
          />
          <Detail
            label="Capabilities"
            value={node.capabilities.length ? node.capabilities.join(", ") : "Not observed"}
          />
          <Detail
            label="Last observation"
            value={
              node.last_seen_age_ms === null
                ? "Unavailable"
                : formatObservationAge(node.last_seen_age_ms)
            }
          />
          <Detail label="Software version" value={node.software_version ?? "Not reported"} />
          <Detail
            label="Latency / clock drift"
            value={
              node.latency_ms === null && node.clock_drift_ms === null
                ? "Not reported"
                : `${node.latency_ms ?? "—"} ms / ${node.clock_drift_ms ?? "—"} ms`
            }
          />
        </dl>
      )}
      {node.kind === "controller" && (
        <p className="topology-node__note">
          <Network aria-hidden="true" size={15} />
          Root of the currently observable topology
        </p>
      )}
      {node.kind === "controller_output" && (
        <>
          <dl className="topology-node__details">
            <Detail label="Firmware" value={node.software_version ?? "Not reported"} />
            <Detail
              label="Pixels"
              value={
                node.reported_pixel_count === null ||
                node.reported_pixel_count === undefined
                  ? "Not reported"
                  : node.reported_pixel_count.toLocaleString()
              }
            />
            <Detail
              label="Wi-Fi signal"
              value={
                node.signal_percent === null || node.signal_percent === undefined
                  ? "Not reported"
                  : `${node.signal_percent}%`
              }
            />
            <Detail
              label="Output state"
              value={
                node.output_on === null || node.output_on === undefined
                  ? "Not reported"
                  : node.output_on
                    ? "On"
                    : "Off"
              }
            />
            <Detail
              label="HTTP latency"
              value={node.latency_ms === null ? "Not reported" : `${node.latency_ms} ms`}
            />
          </dl>
          <p className="topology-node__note">
            <Cable aria-hidden="true" size={15} />
            {node.diagnostic ?? "Controller-owned WLED output observed without changing state"}
          </p>
        </>
      )}
    </article>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function Summary({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  tone: StatusTone;
}) {
  return (
    <article className="topology-summary__item">
      <span>{label}</span>
      <strong>{value}</strong>
      <StatusText tone={tone}>{detail}</StatusText>
    </article>
  );
}

function connectionStatus(node: TopologyNode): { tone: StatusTone; label: string } {
  if (node.connection === "connected") {
    return { tone: "success", label: "Connected" };
  }
  if (node.connection === "disconnected") {
    if (node.connection_policy === "on_demand") {
      return { tone: "neutral", label: "Standby" };
    }
    return { tone: "warning", label: "Disconnected" };
  }
  return { tone: "neutral", label: "Not independently detectable" };
}

function formatObservationAge(ageMs: number): string {
  if (ageMs < 1_000) {
    return "Just now";
  }
  if (ageMs < 60_000) {
    return `${Math.floor(ageMs / 1_000)} seconds ago`;
  }
  if (ageMs < 3_600_000) {
    return `${Math.floor(ageMs / 60_000)} minutes ago`;
  }
  return `${Math.floor(ageMs / 3_600_000)} hours ago`;
}

function enrollmentLabel(node: TopologyNode): string {
  return humanize(node.enrollment);
}

function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
