import { useEffect, useRef, useState } from "react";

import { ApiError, previewSceneMedia, ProjectDraftRecord, SceneMediaPreview } from "../api";

export interface SceneMediaPreviewState {
  preview: SceneMediaPreview | null;
  error: string | null;
  loading: boolean;
  retry: () => void;
}

/**
 * Polls the controller's scene media preview (rendered projector/media frames on the scene
 * clock) and keeps the latest result; one request is in flight at a time.
 */
export function useSceneMediaPreview({
  projectId, revision, document, dirty, sceneId, position, valid, enabled = true,
}: {
  projectId: string; revision: number; document?: ProjectDraftRecord["draft"]; dirty: boolean;
  sceneId: string; position: number; valid: boolean; enabled?: boolean;
}): SceneMediaPreviewState {
  const [preview, setPreview] = useState<SceneMediaPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const latestPosition = useRef(position);
  latestPosition.current = position;

  useEffect(() => { setPreview(null); }, [sceneId, document]);
  useEffect(() => {
    if (!enabled) return;
    if (!valid) {
      setPreview(null); setLoading(false);
      setError("Correct the scene fields to preview media.");
      return;
    }
    let current = true;
    let pending = false;
    let previousPosition: number | null = null;
    let abort = new AbortController();
    let watchdog: number | undefined;
    const evaluate = () => {
      if (!current || pending || previousPosition === latestPosition.current) return;
      pending = true;
      const requested = latestPosition.current;
      previousPosition = requested;
      abort = new AbortController();
      setLoading(true);
      watchdog = window.setTimeout(() => abort.abort(), 15000);
      previewSceneMedia(projectId, revision, sceneId, requested, dirty ? document : undefined, abort.signal)
        .then((value) => { if (current) { setPreview(value); setError(null); } })
        .catch((failure: unknown) => {
          if (!current) return;
          if (failure instanceof ApiError && failure.status === 429) {
            previousPosition = null;
            return;
          }
          setError(abort.signal.aborted ? "Media preview timed out. Try again after checking the controller."
            : failure instanceof Error ? failure.message : "Media preview failed.");
        })
        .finally(() => {
          window.clearTimeout(watchdog);
          pending = false;
          if (current) setLoading(false);
        });
    };
    const timer = window.setTimeout(evaluate, 120);
    const poll = window.setInterval(evaluate, 250);
    return () => {
      current = false; window.clearTimeout(timer); window.clearInterval(poll);
      window.clearTimeout(watchdog); abort.abort();
    };
  }, [projectId, revision, sceneId, document, dirty, valid, attempt, enabled]);

  return { preview, error, loading, retry: () => setAttempt((value) => value + 1) };
}
