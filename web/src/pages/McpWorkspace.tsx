import { useCallback, useEffect, useState } from "react";

import {
  approveMcpProposal,
  createMcpToken,
  getMcpProposals,
  getMcpTokens,
  McpProposal,
  McpScope,
  McpToken,
  mergeMcpProposal,
  rejectMcpProposal,
  revokeMcpToken,
  validateMcpProposal,
} from "../api";

const optionalScopes: { value: McpScope; label: string }[] = [
  { value: "draft_edit", label: "Create isolated draft proposals" },
  { value: "direct_edit", label: "Edit working drafts directly (no proposal approval)" },
  { value: "simulation", label: "Run no-hardware simulations" },
  { value: "publish_prepare", label: "Read publication readiness" },
  { value: "publish", label: "Approve releases, publish, and stage packages" },
  { value: "activate", label: "Activate staged packages (live output)" },
];

export function McpWorkspace({
  projectId,
  canDesign,
  canPublish,
  canActivate,
}: {
  projectId: string;
  canDesign: boolean;
  canPublish: boolean;
  canActivate: boolean;
}) {
  const [tokens, setTokens] = useState<McpToken[]>([]);
  const [proposals, setProposals] = useState<McpProposal[]>([]);
  const [clientId, setClientId] = useState("");
  const [scopes, setScopes] = useState<McpScope[]>(["read"]);
  const [newSecret, setNewSecret] = useState("");
  const [reason, setReason] = useState("Reviewed in the Cueverge GUI");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const scopePermissions: Partial<Record<McpScope, boolean>> = {
    direct_edit: canDesign,
    publish: canPublish,
    activate: canActivate,
  };

  useEffect(() => {
    setScopes((current) =>
      current.filter(
        (scope) =>
          (scope !== "direct_edit" || canDesign) &&
          (scope !== "publish" || canPublish) &&
          (scope !== "activate" || canActivate),
      ),
    );
  }, [canDesign, canPublish, canActivate]);

  const load = useCallback(async () => {
    const [nextTokens, nextProposals] = await Promise.all([
      getMcpTokens(projectId),
      getMcpProposals(projectId),
    ]);
    setTokens(nextTokens);
    setProposals(nextProposals);
  }, [projectId]);

  useEffect(() => {
    void load().catch(() => setError("MCP authoring state could not be loaded."));
  }, [load]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
      await load();
    } catch {
      setError("The MCP authoring action failed.");
    } finally {
      setBusy(false);
    }
  }

  function toggleScope(scope: McpScope, checked: boolean) {
    setScopes((current) =>
      checked ? [...current, scope] : current.filter((value) => value !== scope),
    );
  }

  return (
    <details className="mcp-workspace">
      <summary>MCP-assisted authoring</summary>
      <p>
        Connect clients to <code>/mcp/</code>. Tokens are bound to this project.
        Proposal tokens require human approval and merge. Separately granted
        direct-edit tokens can change working drafts immediately. Publishing and
        live package activation each require their own scope.
      </p>
      {error && <p role="alert">{error}</p>}
      <div className="collaboration-grid">
        <section>
          <h4>Client tokens</h4>
          {newSecret && (
            <div className="mcp-secret" role="status">
              <strong>Copy this token now. It will not be shown again.</strong>
              <code>{newSecret}</code>
              <button type="button" onClick={() => setNewSecret("")}>
                I saved it
              </button>
            </div>
          )}
          <ul className="review-list">
            {tokens.map((token) => (
              <li key={token.id}>
                <strong>{token.client_id}</strong>
                <span>{token.scopes.join(", ")}</span>
                <small>
                  {token.username} · expires {new Date(token.expires_at).toLocaleString()}
                  {token.revoked_at ? " · revoked" : ""}
                </small>
                {canDesign && !token.revoked_at && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(() => revokeMcpToken(projectId, token.id))
                    }
                  >
                    Revoke
                  </button>
                )}
              </li>
            ))}
          </ul>
          <label>
            Client identity
            <input
              maxLength={200}
              placeholder="lighting-assistant"
              value={clientId}
              onChange={(event) => setClientId(event.target.value)}
            />
          </label>
          <fieldset>
            <legend>Scopes</legend>
            <label>
              <input checked disabled type="checkbox" /> Read project
            </label>
            {optionalScopes.map((scope) => (
              <label key={scope.value}>
                <input
                  checked={scopes.includes(scope.value)}
                  disabled={scopePermissions[scope.value] === false}
                  type="checkbox"
                  onChange={(event) => toggleScope(scope.value, event.target.checked)}
                />
                {scope.label}
              </label>
            ))}
          </fieldset>
          <p>
            Direct editing, publishing, and activation bypass GUI review for those
            actions. Grant these optional scopes only to trusted clients.
          </p>
          <button
            disabled={busy || !clientId.trim()}
            onClick={() =>
              void run(async () => {
                const created = await createMcpToken(
                  projectId,
                  clientId.trim(),
                  scopes,
                  new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString(),
                );
                setNewSecret(created.token ?? "");
                setClientId("");
                setScopes(["read"]);
              })
            }
          >
            Create 30-day token
          </button>
        </section>
        <section>
          <h4>Human proposal review</h4>
          <label>
            Review rationale
            <textarea
              maxLength={2000}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <ul className="review-list mcp-proposals">
            {proposals.map((proposal) => (
              <li key={proposal.id}>
                <strong>{proposal.title}</strong>
                <span>
                  {proposal.kind} · {proposal.state} · base r{proposal.base_revision}
                </span>
                <small>
                  Proposed by {proposal.username} via {proposal.client_id}
                </small>
                <ul aria-label={`Changed paths for ${proposal.title}`}>
                  {proposal.changed_paths.map((path) => (
                    <li key={path}>
                      <code>{path}</code>
                    </li>
                  ))}
                </ul>
                {proposal.validation.map((diagnostic, index) => (
                  <small key={`${proposal.id}-diagnostic-${index}`}>
                    {String(diagnostic.severity ?? "info")}:{" "}
                    {String(diagnostic.message ?? diagnostic.code ?? "Diagnostic")}
                  </small>
                ))}
                {proposal.disposition_reason && (
                  <small>{proposal.disposition_reason}</small>
                )}
                {canDesign && (
                  <div className="button-row">
                    {proposal.state === "proposed" && (
                      <button
                        disabled={busy}
                        onClick={() =>
                          void run(async () => {
                            await validateMcpProposal(projectId, proposal.id);
                          })
                        }
                      >
                        Validate
                      </button>
                    )}
                    {proposal.state === "validated" && (
                      <button
                        disabled={busy || !reason.trim()}
                        onClick={() =>
                          void run(async () => {
                            await approveMcpProposal(
                              projectId,
                              proposal.id,
                              reason.trim(),
                            );
                          })
                        }
                      >
                        Approve
                      </button>
                    )}
                    {proposal.state === "approved" && (
                      <button
                        disabled={busy}
                        onClick={() =>
                          void run(async () => {
                            await mergeMcpProposal(projectId, proposal.id);
                          })
                        }
                      >
                        Merge into working revision
                      </button>
                    )}
                    {!["merged", "rejected"].includes(proposal.state) && (
                      <button
                        disabled={busy || !reason.trim()}
                        onClick={() =>
                          void run(async () => {
                            await rejectMcpProposal(
                              projectId,
                              proposal.id,
                              reason.trim(),
                            );
                          })
                        }
                      >
                        Reject
                      </button>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
          {!proposals.length && <small>No assistant proposals yet.</small>}
        </section>
      </div>
    </details>
  );
}
