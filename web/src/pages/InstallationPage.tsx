import { useEffect, useState } from "react";
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
  ArtifactCacheCapability,
  DeviceCapabilityDocument,
  DiscoveredEndpoint,
  getDiscoveredEndpoints,
  InstallationStatus,
  MediaPlaybackCapability,
  PairingToken,
  removeResourceAssignment,
  rejectDiscoveredEndpoint,
  reviewDeviceCapabilities,
  CapabilityReview,
  ResourceAssignment,
  Session,
  setDeviceConnectionPolicy,
  setDeviceEnrollmentState,
  setResourceAssignment,
  testDeviceConnection,
  probeEndpoint,
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
      {(session?.role === "administrator" || session?.role === "operator") && topology && (
        <DeviceManagement
          agents={agents}
          installation={installation}
          assignments={assignments}
          enrollmentSupported={topology.enrollment_supported}
          canAdminister={session.role === "administrator"}
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
  canAdminister,
  onRefresh,
}: {
  agents: TopologyNode[];
  installation: InstallationStatus | null;
  assignments: ResourceAssignment[];
  enrollmentSupported: boolean;
  canAdminister: boolean;
  onRefresh?: () => Promise<void> | void;
}) {
  const [deviceKind, setDeviceKind] =
    useState<PairingToken["device_kind"]>("direct_media_agent");
  const [pairingToken, setPairingToken] = useState<PairingToken | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  const [copied, setCopied] = useState<string | null>(null);
  const [capabilityJson, setCapabilityJson] = useState(() =>
    JSON.stringify(capabilityTemplate("direct_media_agent"), null, 2),
  );
  const [capabilityReview, setCapabilityReview] = useState<CapabilityReview | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [discoveryAddress, setDiscoveryAddress] = useState("");
  const [discoveredEndpoints, setDiscoveredEndpoints] = useState<DiscoveredEndpoint[]>([]);
  const [probing, setProbing] = useState(false);

  useEffect(() => {
    if (!pairingToken) {
      return;
    }
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [pairingToken]);

  useEffect(() => {
    if (!canAdminister) {
      return;
    }
    getDiscoveredEndpoints()
      .then(setDiscoveredEndpoints)
      .catch(() => setError("Discovered endpoints could not be loaded."));
  }, [canAdminister]);

  const remainingSeconds = pairingToken
    ? Math.max(0, Math.ceil((new Date(pairingToken.expires_at).getTime() - now) / 1_000))
    : 0;
  const enrollmentCommand = pairingToken
    ? `cueverge-device-enroll --enrollment-url ${window.location.origin}/api/device/v2/enroll --display-name DEVICE_NAME --capabilities ./device-capabilities.json`
    : "";
  const transportSetting = pairingToken
    ? `CUEVERGE_DEVICE_TRANSPORT_URL=${pairingToken.device_transport_url}`
    : "";

  async function copy(label: string, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(label);
    } catch {
      setError("Clipboard access failed. Select and copy the value manually.");
    }
  }

  async function createToken(endpoint?: DiscoveredEndpoint) {
    setCreating(true);
    setError(null);
    try {
      const kind = endpoint?.device_kind ?? deviceKind;
      setDeviceKind(kind);
      setPairingToken(await createPairingToken(kind, endpoint?.id));
      setNow(Date.now());
      setCopied(null);
      if (endpoint) {
        setDiscoveredEndpoints(await getDiscoveredEndpoints());
      }
    } catch {
      setError("Pairing token creation failed.");
    } finally {
      setCreating(false);
    }
  }

  async function reviewCapabilities() {
    setReviewing(true);
    setError(null);
    try {
      const document = JSON.parse(capabilityJson) as unknown;
      setCapabilityReview(await reviewDeviceCapabilities(document));
    } catch {
      setCapabilityReview(null);
      setError("Capability review failed. Check the JSON document and try again.");
    } finally {
      setReviewing(false);
    }
  }

  async function probe() {
    setProbing(true);
    setError(null);
    try {
      await probeEndpoint(discoveryAddress);
      setDiscoveryAddress("");
      setDiscoveredEndpoints(await getDiscoveredEndpoints());
    } catch {
      setError(
        "Discovery failed. Use a trusted HTTPS origin that resolves only to local/private addresses.",
      );
    } finally {
      setProbing(false);
    }
  }

  async function reject(endpointId: string) {
    try {
      await rejectDiscoveredEndpoint(endpointId);
      setDiscoveredEndpoints(await getDiscoveredEndpoints());
    } catch {
      setError("The discovered endpoint could not be rejected.");
    }
  }

  return (
    <Panel
      title="Device Management"
      description="Administrator-only enrollment and connection expectations"
      className="device-management"
    >
      <div className="device-management__grid">
        {canAdminister && (
          <section
            className="device-management__wide"
            aria-labelledby="endpoint-discovery-title"
          >
            <h3 id="endpoint-discovery-title">Explicit controller discovery</h3>
            <div className="endpoint-discovery-form">
              <label>
                HTTPS controller origin
                <input
                  placeholder="https://controller.local"
                  value={discoveryAddress}
                  onChange={(event) => setDiscoveryAddress(event.target.value)}
                />
              </label>
              <button
                disabled={probing || !discoveryAddress.trim()}
                onClick={() => void probe()}
              >
                {probing ? "Probing…" : "Probe endpoint"}
              </button>
            </div>
            {discoveredEndpoints.length ? (
              <div className="discovered-endpoints">
                {discoveredEndpoints.map((endpoint) => (
                  <article key={endpoint.id}>
                    <div>
                      <strong>{endpoint.base_url}</strong>
                      <small>
                        {humanize(endpoint.device_kind)} · software{" "}
                        {endpoint.software_version} · protocol v
                        {endpoint.protocol_minimum}-v{endpoint.protocol_maximum}
                      </small>
                      <small>{endpoint.capability_kinds.join(", ")}</small>
                    </div>
                    <StatusText
                      tone={
                        endpoint.state === "rejected" || endpoint.state === "pairing"
                          ? "neutral"
                          : endpoint.state === "enrolled"
                            ? "success"
                          : endpoint.compatible
                            ? "success"
                            : "danger"
                      }
                    >
                      {endpoint.state === "rejected"
                        ? "Rejected"
                        : endpoint.state === "pairing"
                          ? "Pairing token issued"
                          : endpoint.state === "enrolled"
                            ? "Enrolled"
                        : endpoint.compatible
                          ? "Compatible · untrusted"
                          : "Incompatible · untrusted"}
                    </StatusText>
                    <div className="discovered-endpoint-actions">
                      {endpoint.state === "discovered" && endpoint.compatible && (
                        <button onClick={() => void createToken(endpoint)}>
                          Prepare enrollment
                        </button>
                      )}
                      {endpoint.state !== "enrolled" && (
                        <button
                          disabled={endpoint.state === "rejected"}
                          onClick={() => void reject(endpoint.id)}
                        >
                          Reject
                        </button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <p className="muted">
                No endpoints discovered. Discovery never grants trust or command authority.
              </p>
            )}
          </section>
        )}
        {canAdminister && <section aria-labelledby="pair-device-title">
          <h3 id="pair-device-title">Pair a device</h3>
          {enrollmentSupported ? (
            <>
              <label>
                Device kind
                <select
                  value={deviceKind}
                  onChange={(event) => {
                    const kind = event.target.value as PairingToken["device_kind"];
                    setDeviceKind(kind);
                    setCapabilityJson(JSON.stringify(capabilityTemplate(kind), null, 2));
                    setCapabilityReview(null);
                    setPairingToken(null);
                  }}
                >
                  <option value="direct_media_agent">Direct media agent</option>
                  <option value="downstream_controller">Downstream controller</option>
                </select>
              </label>
              <label>
                Capability document
                <textarea
                  aria-label="Capability document"
                  rows={12}
                  spellCheck={false}
                  value={capabilityJson}
                  onChange={(event) => {
                    setCapabilityJson(event.target.value);
                    setCapabilityReview(null);
                  }}
                />
              </label>
              <button disabled={reviewing} onClick={() => void reviewCapabilities()}>
                {reviewing ? "Reviewing…" : "Review capabilities"}
              </button>
              {capabilityReview && (
                <div
                  className={`capability-review capability-review--${
                    capabilityReview.compatible ? "compatible" : "incompatible"
                  }`}
                  role="status"
                >
                  <StatusText tone={capabilityReview.compatible ? "success" : "danger"}>
                    {capabilityReview.compatible
                      ? `Compatible with protocol v${capabilityReview.controller_protocol_version}`
                      : "Compatibility issues found"}
                  </StatusText>
                  {capabilityReview.issues.length > 0 && (
                    <ul>
                      {capabilityReview.issues.map((issue) => (
                        <li key={issue.code}>{issue.message}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
              <button
                className="primary-button"
                disabled={creating || !capabilityReview?.compatible}
                onClick={() => void createToken()}
              >
                {creating ? "Creating…" : "Create one-time token"}
              </button>
              {pairingToken && (
                <div className="pairing-token" role="status">
                  <strong>Copy this token now</strong>
                  <code>{pairingToken.pairing_token}</code>
                  <button
                    disabled={remainingSeconds === 0}
                    onClick={() =>
                      void copy("token", pairingToken.pairing_token)
                    }
                  >
                    {copied === "token" ? "Token copied" : "Copy token"}
                  </button>
                  <small>
                    {remainingSeconds > 0
                      ? `Expires in ${remainingSeconds} seconds and can be used once.`
                      : "Expired. Create a new token before enrolling."}
                  </small>
                  <div className="enrollment-guide">
                    <strong>1. Review the device capability file</strong>
                    <span>
                      Declare only codecs, outputs, storage, and controls verified on the
                      device.
                    </span>
                    <strong>2. Run enrollment on the device</strong>
                    <code>{enrollmentCommand}</code>
                    <button onClick={() => void copy("command", enrollmentCommand)}>
                      {copied === "command" ? "Command copied" : "Copy command"}
                    </button>
                    <strong>3. Configure the authenticated transport</strong>
                    <code>{transportSetting}</code>
                    <button onClick={() => void copy("transport", transportSetting)}>
                      {copied === "transport" ? "Setting copied" : "Copy setting"}
                    </button>
                    <small>
                      The device generates and retains its private key. Paste the one-time
                      token only at the hidden prompt.
                    </small>
                  </div>
                </div>
              )}
            </>
          ) : (
            <p className="muted">Device enrollment is disabled on this controller.</p>
          )}
        </section>}
        {canAdminister && <section aria-labelledby="connection-policy-title">
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
        </section>}
        {canAdminister && <section className="device-management__wide" aria-labelledby="device-lifecycle-title">
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
        </section>}
        {canAdminister && <section className="device-management__wide" aria-labelledby="resource-assignment-title">
          <h3 id="resource-assignment-title">Resource assignments</h3>
          <div className="policy-list">
            {installation?.resources
              .filter((resource) => resource.kind !== "pixel_output")
              .map((resource) => (
                <ResourceAssignmentControl
                  key={resource.resource}
                  resource={resource.resource}
                  capabilityKind={
                    resource.kind === "media_agent"
                      ? "media_playback"
                      : "downstream_control"
                  }
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
        </section>}
        <section className="device-management__wide" aria-labelledby="connection-test-title">
          <h3 id="connection-test-title">Connection tests</h3>
          <div className="policy-list">
            {agents.map((agent) => (
              <ConnectionTest key={agent.id} node={agent} />
            ))}
          </div>
        </section>
      </div>
      {error && <p role="alert">{error}</p>}
    </Panel>
  );
}

function capabilityTemplate(
  deviceKind: PairingToken["device_kind"],
): Record<string, unknown> {
  return {
    schema_version: 1,
    protocol: { minimum: 2, maximum: 2 },
    device_kind: deviceKind,
    software_version: "0.1.0",
    capabilities:
      deviceKind === "direct_media_agent"
        ? [
            {
              kind: "media_playback",
              codecs: ["h264"],
              containers: ["mp4"],
              max_outputs: 1,
              supports_pause: true,
              supports_seek: true,
            },
          ]
        : [
            {
              kind: "downstream_control",
              operations: ["query_status"],
            },
          ],
  };
}

function ConnectionTest({ node }: { node: TopologyNode }) {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<Awaited<
    ReturnType<typeof testDeviceConnection>
  > | null>(null);
  const [error, setError] = useState(false);

  async function run() {
    setTesting(true);
    setError(false);
    try {
      setResult(await testDeviceConnection(node.id.replace("device:", "")));
    } catch {
      setError(true);
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="connection-test-row">
      <div className="connection-test-row__header">
        <div>
          <strong>{node.label}</strong>
          <small>{connectionStatus(node).label}</small>
        </div>
        <button disabled={testing} onClick={() => void run()}>
          {testing ? "Testing…" : "Test connection"}
        </button>
      </div>
      {result && (
        <div className="connection-test-result" role="status">
          <StatusText tone={result.result === "passed" ? "success" : "danger"}>
            {result.result === "passed" ? "All checks passed" : "Action required"}
          </StatusText>
          <ul>
            {result.checks.map((check) => (
              <li key={check.name}>
                <strong>{humanize(check.name)}</strong>
                <span>{check.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {error && <p role="alert">Connection test could not be completed.</p>}
    </div>
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
  capabilityKind,
  agents,
  assignment,
  onRefresh,
  onError,
}: {
  resource: string;
  capabilityKind: "media_playback" | "downstream_control";
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
      agent.capabilities.includes(capabilityKind) &&
      (agent.connection === "disconnected" || agent.id === currentNode?.id),
  );

  async function updateAssignment(deviceId: string) {
    setSaving(true);
    try {
      if (deviceId) {
        await setResourceAssignment(
          resource,
          deviceId,
          capabilityKind,
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
  const media = mediaCapability(node.capability_document);
  const cache = cacheCapability(node.capability_document);
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
            label="Compatibility detail"
            value={node.compatibility_detail ?? "No compatibility diagnostic"}
          />
          <Detail
            label="Protocol"
            value={
              node.capability_document
                ? `v${node.capability_document.protocol.minimum}–${node.capability_document.protocol.maximum}`
                : node.protocol_version === null
                  ? "Not observed"
                  : `v${node.protocol_version}`
            }
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
            label="Media formats"
            value={
              media
                ? `${media.containers.join(", ")} · ${media.codecs.join(", ")}`
                : "Not declared"
            }
          />
          <Detail
            label="Output capacity"
            value={media ? `${media.max_outputs} simultaneous output(s)` : "Not declared"}
          />
          <Detail
            label="Playback controls"
            value={
              media
                ? [
                    media.supports_pause ? "Pause" : null,
                    media.supports_seek ? "Seek" : null,
                  ]
                    .filter(Boolean)
                    .join(", ") || "Play/stop only"
                : "Not declared"
            }
          />
          <Detail
            label="Storage"
            value={
              node.storage_free_bytes !== null &&
              node.storage_free_bytes !== undefined &&
              node.storage_total_bytes
                ? `${formatBytes(node.storage_free_bytes)} free of ${formatBytes(
                    node.storage_total_bytes,
                  )}`
                : "Not declared"
            }
          />
          <Detail
            label="Artifacts"
            value={
              node.artifact_count === null || node.artifact_count === undefined
                ? "Not reported"
                : `${node.artifact_count} digest(s) · ${formatBytes(
                    node.artifact_bytes ?? 0,
                  )} · ${node.pinned_artifact_count ?? 0} pinned`
            }
          />
          <Detail
            label="Cache behavior"
            value={
              node.supports_pinning !== null &&
              node.supports_pinning !== undefined
                ? [
                    node.supports_pinning ? "Pinning" : "No pinning",
                    node.supports_resumable_transfer
                      ? "Resumable transfer"
                      : "No resumable transfer",
                  ].join(", ")
                : cache
                ? [
                    cache.supports_pinning ? "Pinning" : null,
                    cache.supports_resumable_transfer ? "Resumable transfer" : null,
                  ]
                    .filter(Boolean)
                    .join(", ") || "Inventory only"
                : node.artifact_count !== null && node.artifact_count !== undefined
                  ? "Inventory reporting only"
                  : "Not declared"
            }
          />
          <Detail
            label="Transfers"
            value={
              node.active_transfers === null || node.active_transfers === undefined
                ? "Not reported"
                : `${node.active_transfers} active · ${node.failed_transfers ?? 0} failed`
            }
          />
          <Detail
            label="Certificate"
            value={certificateExpiry(node.certificate_expires_at)}
          />
          {node.artifact_faults?.map((fault) => (
            <div key={fault} className="diagnostic-error">
              <dt>Storage fault</dt>
              <dd>{fault}</dd>
            </div>
          ))}
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

function mediaCapability(
  document: DeviceCapabilityDocument | null | undefined,
): MediaPlaybackCapability | null {
  const capability = document?.capabilities.find(
    (candidate) => candidate.kind === "media_playback",
  );
  return capability && "codecs" in capability
    ? (capability as MediaPlaybackCapability)
    : null;
}

function cacheCapability(
  document: DeviceCapabilityDocument | null | undefined,
): ArtifactCacheCapability | null {
  const capability = document?.capabilities.find(
    (candidate) => candidate.kind === "artifact_cache",
  );
  return capability && "total_bytes" in capability
    ? (capability as ArtifactCacheCapability)
    : null;
}

function formatBytes(value: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = value;
  let unit = 0;
  while (amount >= 1_000 && unit < units.length - 1) {
    amount /= 1_000;
    unit += 1;
  }
  return `${amount.toFixed(amount >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function certificateExpiry(value: string | null | undefined): string {
  if (!value) {
    return "Not reported";
  }
  const expiry = new Date(value);
  const remainingDays = Math.ceil((expiry.getTime() - Date.now()) / 86_400_000);
  if (remainingDays < 0) {
    return `Expired ${expiry.toLocaleDateString()}`;
  }
  return `Expires ${expiry.toLocaleDateString()} · ${remainingDays} day(s)`;
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
