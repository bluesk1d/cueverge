import { useCallback, useEffect, useState } from "react";

import { getOutputMonitor, OutputMonitor } from "../api";

export const OUTPUT_MONITOR_INTERVAL_MS = 500;
const OUTPUT_MONITOR_TIMEOUT_MS = 8000;

/**
 * Polls the read-only output monitor while enabled: at most 2 requests/second,
 * never overlapping, and suspended while the tab is hidden.
 */
export function useOutputMonitor({ enabled, resource = "", offset = 0 }: {
  enabled: boolean; resource?: string; offset?: number;
}): { snapshot: OutputMonitor | null; error: string | null; retry: () => void; clear: () => void } {
  const [snapshot, setSnapshot] = useState<OutputMonitor | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let current = true;
    let pending = false;
    let timer: number | undefined;
    let abort = new AbortController();
    const read = async () => {
      if (!current || pending || document.hidden) return;
      pending = true;
      abort = new AbortController();
      const timeout = window.setTimeout(() => abort.abort(), OUTPUT_MONITOR_TIMEOUT_MS);
      try {
        const result = await getOutputMonitor(resource || undefined, offset, abort.signal);
        if (!Array.isArray(result?.outputs)) throw new Error("Output monitor returned an unexpected response.");
        if (current) { setSnapshot(result); setError(null); }
      } catch (failure) {
        if (current) setError(failure instanceof Error ? failure.message : "Output monitor timed out.");
      } finally {
        window.clearTimeout(timeout); pending = false;
        if (current && !document.hidden) timer = window.setTimeout(read, OUTPUT_MONITOR_INTERVAL_MS);
      }
    };
    const visibility = () => {
      window.clearTimeout(timer);
      if (document.hidden) abort.abort();
      else void read();
    };
    void read();
    document.addEventListener("visibilitychange", visibility);
    return () => {
      current = false; abort.abort(); window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [enabled, resource, offset, attempt]);
  const retry = useCallback(() => setAttempt((value) => value + 1), []);
  const clear = useCallback(() => { setSnapshot(null); setError(null); }, []);
  return { snapshot, error, retry, clear };
}
