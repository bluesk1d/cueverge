import { useCallback, useEffect, useMemo, useState } from "react";

import {
  createProjectChangeRequest,
  createProjectComment,
  createReviewLink,
  getProjectChangeRequests,
  getProjectComments,
  getProjectMembers,
  getProjectPresence,
  heartbeatProjectPresence,
  ProjectChangeRequest,
  ProjectComment,
  ProjectMembership,
  ProjectPresence,
  putProjectMember,
  Role,
  Session,
  updateProjectChangeRequest,
  updateProjectComment,
} from "../api";

export function CollaborationWorkspace({
  projectId,
  revision,
  session,
  onPermissions,
}: {
  projectId: string;
  revision: number;
  session: Session;
  onPermissions: (role: Role, canLiveOutput: boolean) => void;
}) {
  const [members, setMembers] = useState<ProjectMembership[]>([]);
  const [comments, setComments] = useState<ProjectComment[]>([]);
  const [requests, setRequests] = useState<ProjectChangeRequest[]>([]);
  const [presence, setPresence] = useState<ProjectPresence[]>([]);
  const [commentBody, setCommentBody] = useState("");
  const [objectId, setObjectId] = useState("");
  const [requestTitle, setRequestTitle] = useState("");
  const [requestBody, setRequestBody] = useState("");
  const [memberName, setMemberName] = useState("");
  const [memberRole, setMemberRole] = useState<Role>("viewer");
  const [memberLive, setMemberLive] = useState(false);
  const [reviewUrl, setReviewUrl] = useState("");
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);

  const effectiveRole = useMemo(
    () =>
      session.role === "administrator"
        ? "administrator"
        : members.find((member) => member.username === session.username)?.role ??
          "viewer",
    [members, session],
  );
  const canDesign = ["designer", "publisher", "administrator"].includes(effectiveRole);
  const canManage = effectiveRole === "administrator";

  const load = useCallback(async () => {
    try {
      const [nextMembers, nextComments, nextRequests, nextPresence] =
        await Promise.all([
          getProjectMembers(projectId),
          getProjectComments(projectId),
          getProjectChangeRequests(projectId),
          getProjectPresence(projectId),
        ]);
      setMembers(nextMembers);
      setComments(nextComments);
      setRequests(nextRequests);
      setPresence(nextPresence);
      const current = nextMembers.find(
        (member) => member.username === session.username,
      );
      onPermissions(
        session.role === "administrator" ? "administrator" : current?.role ?? "viewer",
        session.role === "administrator" || current?.can_live_output === true,
      );
      setError(false);
    } catch {
      setError(true);
    }
  }, [onPermissions, projectId, session]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const heartbeat = async () => {
      await heartbeatProjectPresence(projectId, "project workspace");
      setPresence(await getProjectPresence(projectId));
    };
    void heartbeat().catch(() => setError(true));
    const timer = window.setInterval(
      () => void heartbeat().catch(() => setError(true)),
      30_000,
    );
    return () => window.clearInterval(timer);
  }, [projectId]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    try {
      await action();
      await load();
      setError(false);
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="collaboration-workspace" aria-labelledby={`collaboration-${projectId}`}>
      <header>
        <div>
          <span className="eyebrow">Review</span>
          <h3 id={`collaboration-${projectId}`}>Collaboration</h3>
        </div>
        <div className="presence-list" aria-label="People currently reviewing">
          {presence.map((entry) => (
            <span key={entry.username} title={entry.location}>
              {entry.username}
            </span>
          ))}
        </div>
      </header>
      {error && <p role="alert">Collaboration state could not be synchronized.</p>}
      <div className="collaboration-grid">
        <section>
          <h4>Comments</h4>
          <ul className="review-list">
            {comments.map((comment) => (
              <li key={comment.id}>
                <strong>{comment.created_by}</strong>
                <span>{comment.body}</span>
                <small>
                  r{comment.revision}
                  {comment.object_id ? ` · ${comment.object_id}` : ""} · {comment.state}
                </small>
                {canDesign && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await updateProjectComment(
                          projectId,
                          comment.id,
                          comment.state === "open" ? "resolved" : "open",
                        );
                      })
                    }
                  >
                    {comment.state === "open" ? "Resolve" : "Reopen"}
                  </button>
                )}
              </li>
            ))}
          </ul>
          <label>
            Object ID (optional)
            <input value={objectId} onChange={(event) => setObjectId(event.target.value)} />
          </label>
          <label>
            New comment
            <textarea
              maxLength={4000}
              value={commentBody}
              onChange={(event) => setCommentBody(event.target.value)}
            />
          </label>
          <button
            disabled={busy || !commentBody.trim()}
            onClick={() =>
              void run(async () => {
                await createProjectComment(
                  projectId,
                  revision,
                  commentBody,
                  objectId,
                );
                setCommentBody("");
              })
            }
          >
            Add comment
          </button>
        </section>
        <section>
          <h4>Change requests</h4>
          <ul className="review-list">
            {requests.map((request) => (
              <li key={request.id}>
                <strong>{request.title}</strong>
                <span>{request.body}</span>
                <small>{request.state}</small>
                {canDesign && request.state === "open" && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await updateProjectChangeRequest(
                          projectId,
                          request.id,
                          "accepted",
                        );
                      })
                    }
                  >
                    Accept request
                  </button>
                )}
              </li>
            ))}
          </ul>
          <label>
            Request title
            <input
              maxLength={200}
              value={requestTitle}
              onChange={(event) => setRequestTitle(event.target.value)}
            />
          </label>
          <label>
            Requested change
            <textarea
              maxLength={4000}
              value={requestBody}
              onChange={(event) => setRequestBody(event.target.value)}
            />
          </label>
          <button
            disabled={busy || !requestTitle.trim() || !requestBody.trim()}
            onClick={() =>
              void run(async () => {
                await createProjectChangeRequest(
                  projectId,
                  revision,
                  requestTitle,
                  requestBody,
                  objectId,
                );
                setRequestTitle("");
                setRequestBody("");
              })
            }
          >
            Request change
          </button>
        </section>
      </div>
      {canManage && (
        <section className="member-management">
          <h4>Project access</h4>
          <ul>
            {members.map((member) => (
              <li key={member.username}>
                {member.username} · {member.role}
                {member.can_live_output ? " · live output" : ""}
              </li>
            ))}
          </ul>
          <div>
            <label>
              Username
              <input
                value={memberName}
                onChange={(event) => setMemberName(event.target.value)}
              />
            </label>
            <label>
              Project role
              <select
                value={memberRole}
                onChange={(event) => setMemberRole(event.target.value as Role)}
              >
                <option value="viewer">Viewer</option>
                <option value="designer">Designer</option>
                <option value="operator">Operator</option>
                <option value="publisher">Publisher</option>
                <option value="administrator">Administrator</option>
              </select>
            </label>
            <label>
              <input
                checked={memberLive}
                type="checkbox"
                onChange={(event) => setMemberLive(event.target.checked)}
              />
              Permit live output
            </label>
            <button
              disabled={busy || !memberName.trim()}
              onClick={() =>
                void run(async () => {
                  await putProjectMember(
                    projectId,
                    memberName.trim(),
                    memberRole,
                    memberLive,
                  );
                  setMemberName("");
                })
              }
            >
              Save member
            </button>
          </div>
        </section>
      )}
      {canDesign && (
        <section>
          <h4>Review link</h4>
          <button
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const expiresAt = new Date(Date.now() + 7 * 86_400_000).toISOString();
                const link = await createReviewLink(projectId, revision, expiresAt);
                setReviewUrl(
                  `${window.location.origin}/api/gui/v1/review-links/${encodeURIComponent(link.token)}`,
                );
              })
            }
          >
            Create 7-day review link
          </button>
          {reviewUrl && (
            <output className="review-link">
              <a href={reviewUrl}>{reviewUrl}</a>
            </output>
          )}
        </section>
      )}
    </section>
  );
}
