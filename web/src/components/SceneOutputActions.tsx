import { useEffect, useState } from "react";

import {
  ApiError, removeSceneOutput, renameSceneOutput, type ProjectDraftRecord,
} from "../api";

const OUTPUT_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;

/** Rename or remove one saved scene output; each action creates a new draft revision. */
export function SceneOutputActions({
  draft, sceneId, outputId, kind, disabledReason, onChanged,
}: {
  draft: ProjectDraftRecord;
  sceneId: string;
  outputId: string;
  kind: "media" | "lighting";
  disabledReason: string | null;
  onChanged: (updated: ProjectDraftRecord) => void;
}) {
  const [name, setName] = useState(outputId);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setName(outputId); setError(null); }, [outputId, sceneId, draft.revision]);
  const label = kind === "media" ? "media output" : "lighting output";
  const trimmed = name.trim();
  const nameError = trimmed === outputId ? null : !OUTPUT_ID_PATTERN.test(trimmed)
    ? "Start with a letter or number; use letters, numbers, dots, underscores or hyphens."
    : null;

  async function run(action: () => Promise<ProjectDraftRecord>, failure: string) {
    setSaving(true);
    setError(null);
    try {
      onChanged(await action());
    } catch (caught) {
      const message = caught instanceof ApiError && caught.status === 409
        ? "The show changed since you opened it. Reload the saved show and try again."
        : caught instanceof Error ? caught.message : failure;
      setError(`${message} Nothing was changed.`);
    } finally {
      setSaving(false);
    }
  }

  const disabled = saving || disabledReason !== null;
  return (
    <div className="scene-output-actions" role="group" aria-label={`Manage ${label} ${outputId}`}>
      <label>
        Output ID
        <input
          aria-label={`Output ID for ${outputId}`}
          disabled={disabled}
          maxLength={100}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      {nameError && <small role="alert">{nameError}</small>}
      <div className="scene-output-actions__buttons">
        <button
          type="button"
          disabled={disabled || trimmed === outputId || nameError !== null}
          onClick={() => void run(
            () => renameSceneOutput(draft.project.id, draft.revision, sceneId, outputId, trimmed),
            "Could not rename the output.",
          )}
        >
          Rename output
        </button>
        <button
          type="button"
          className="danger-button"
          disabled={disabled}
          onClick={() => {
            if (!window.confirm(
              `Remove ${label} "${outputId}" from this scene? Assets and other scenes are not changed. This does not change live playback.`,
            )) return;
            void run(
              () => removeSceneOutput(draft.project.id, draft.revision, sceneId, outputId),
              "Could not remove the output.",
            );
          }}
        >
          Remove output
        </button>
      </div>
      {disabledReason && <small>{disabledReason}</small>}
      {!disabledReason && (
        <small>
          Renaming updates the clock source and timeline references. Removing is blocked while the
          output is the media clock, the scene&apos;s only output, or used by layers or cues.
        </small>
      )}
      {saving && <small role="status">Saving…</small>}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
