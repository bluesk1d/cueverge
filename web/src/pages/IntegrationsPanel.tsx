import { useCallback, useEffect, useState } from "react";

import {
  configureHomeAssistant,
  createInstallationMcpToken,
  getHomeAssistantIntegration,
  getInstallationMcpTokens,
  HomeAssistantIntegration,
  McpScope,
  McpToken,
  revokeInstallationMcpToken,
} from "../api";
import { optionalScopes } from "../mcpScopes";

const STATE_LABELS: Record<HomeAssistantIntegration["state"], string> = {
  disabled: "Off",
  unavailable: "Needs broker setup",
  connecting: "Connecting…",
  connected: "Connected",
};

export function IntegrationsPanel({ canManage }: { canManage: boolean }) {
  return (
    <div className="integrations-panel">
      <HomeAssistantSection canManage={canManage} />
      {canManage && <InstallationTokensSection />}
    </div>
  );
}

function HomeAssistantSection({ canManage }: { canManage: boolean }) {
  const [status, setStatus] = useState<HomeAssistantIntegration | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setStatus(await getHomeAssistantIntegration());
      setError(null);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Home Assistant status is unavailable.");
    }
  }, []);
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function toggle(enabled: boolean) {
    if (!status) return;
    if (enabled && !window.confirm(
      "Turn on Home Assistant control? Anything allowed to publish to the command topics on your MQTT broker can start, stop or black out the show.",
    )) return;
    setBusy(true);
    setError(null);
    try {
      setStatus(await configureHomeAssistant(enabled, status.configuration_revision));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Home Assistant settings were not saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="integration-section" aria-label="Home Assistant">
      <header>
        <h3>Home Assistant</h3>
        {status && <span className={`integration-state integration-state--${status.state}`} role="status">
          {STATE_LABELS[status.state]}
        </span>}
      </header>
      <p>
        Adds a <strong>Cueverge</strong> device to Home Assistant through MQTT discovery, with
        Start, Stop, Pause, Resume and Blackout buttons, a scene picker, a show mode picker, and
        playback, scene and loaded-show sensors. No Home Assistant add-on is needed; it uses the
        MQTT integration you already have.
      </p>
      {status && <>
        <p>
          Broker: {status.broker.host ? <code>{status.broker.host}:{status.broker.port}{status.broker.tls ? " (TLS)" : ""}</code>
            : "not set up"} — shared with camera events under Installation &gt; Inputs.
        </p>
        {status.error && <p role="alert">{status.error}</p>}
        {canManage && <div className="button-row">
          {status.enabled
            ? <button type="button" disabled={busy} onClick={() => void toggle(false)}>Turn off Home Assistant control</button>
            : <button type="button" className="primary-button" disabled={busy} onClick={() => void toggle(true)}>Turn on Home Assistant control</button>}
        </div>}
        {!canManage && <small>An administrator can turn this on or off.</small>}
        <small>
          Turning it off removes the device from Home Assistant. Restrict who can publish to the
          command topics with your broker&apos;s access control list.
        </small>
        <details>
          <summary>Topics and recent commands</summary>
          <dl className="integration-topics">
            <dt>Commands</dt><dd><code>{status.topics.command}</code> ({status.commands.join(", ")})</dd>
            <dt>Start a scene</dt><dd><code>{status.topics.scene}</code> (scene name)</dd>
            <dt>Show mode</dt><dd><code>{status.topics.mode}</code> ({status.modes.join(", ")})</dd>
            <dt>State</dt><dd><code>{status.topics.state}</code></dd>
            <dt>Online</dt><dd><code>{status.topics.availability}</code></dd>
          </dl>
          <p>{status.commands_received} commands received · {status.commands_rejected} rejected</p>
          <ul className="review-list" aria-label="Recent Home Assistant commands">
            {status.recent_commands.slice().reverse().map((item) => (
              <li key={`${item.received_at_ms}-${item.topic}-${item.payload}`}>
                <strong>{item.payload}</strong>
                <span>{item.accepted ? `Ran ${item.action}` : `Rejected: ${item.error}`}</span>
                <small>{new Date(item.received_at_ms).toLocaleString()}</small>
              </li>
            ))}
          </ul>
        </details>
      </>}
      {!status && error && <p role="alert">{error}</p>}
      {status && error && <p role="alert">{error}</p>}
    </section>
  );
}

function InstallationTokensSection() {
  const [tokens, setTokens] = useState<McpToken[]>([]);
  const [clientId, setClientId] = useState("");
  const [scopes, setScopes] = useState<McpScope[]>(["read"]);
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setTokens(await getInstallationMcpTokens());
    } catch {
      setError("Installation tokens could not be loaded.");
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await load();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "The token action failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="integration-section" aria-label="Assistant access for all shows">
      <header><h3>Assistant access for all shows</h3></header>
      <p>
        One MCP token that works with every show on this installation, such as a ChatGPT or
        other assistant connection. The assistant picks a show for each request and still needs
        your current permission in that show. Live controls default to the show on the display.
      </p>
      {secret && (
        <div className="mcp-secret" role="status">
          <strong>Copy this token now. It will not be shown again.</strong>
          <code>{secret}</code>
          <button type="button" onClick={() => setSecret("")}>I saved it</button>
        </div>
      )}
      <ul className="review-list" aria-label="Installation tokens">
        {tokens.map((token) => (
          <li key={token.id}>
            <strong>{token.client_id}</strong>
            <span>{token.scopes.join(", ")}</span>
            <small>
              {token.username} · expires {new Date(token.expires_at).toLocaleString()}
              {token.revoked_at ? " · revoked" : ""}
            </small>
            {!token.revoked_at && (
              <button type="button" disabled={busy}
                onClick={() => void run(() => revokeInstallationMcpToken(token.id))}>
                Revoke {token.client_id}
              </button>
            )}
          </li>
        ))}
      </ul>
      <label>
        Client identity
        <input maxLength={200} placeholder="chatgpt-tunnel" value={clientId}
          onChange={(event) => setClientId(event.target.value)} />
      </label>
      <fieldset>
        <legend>Allowed actions</legend>
        <label><input checked disabled type="checkbox" /> Read shows and status</label>
        {optionalScopes.map((scope) => (
          <label key={scope.value}>
            <input type="checkbox" checked={scopes.includes(scope.value)}
              onChange={(event) => setScopes((current) => event.target.checked
                ? [...current, scope.value] : current.filter((value) => value !== scope.value))} />
            {scope.label}
          </label>
        ))}
      </fieldset>
      <button type="button" className="primary-button" disabled={busy || !clientId.trim()}
        onClick={() => void run(async () => {
          const created = await createInstallationMcpToken(
            clientId.trim(), scopes,
            new Date(Date.now() + 90 * 24 * 60 * 60 * 1000 - 5 * 60 * 1000).toISOString(),
          );
          setSecret(created.token ?? "");
          setClientId("");
          setScopes(["read"]);
        })}>
        Create 90-day installation token
      </button>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
