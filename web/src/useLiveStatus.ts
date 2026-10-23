import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, getRuntimeStatus, getTopology, RuntimeStatus, Topology } from "./api";
import {
  snapshotFreshness,
  STATUS_REFRESH_MS,
  STATUS_TIMEOUT_MS,
} from "./status";

export function useLiveStatus() {
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [topology, setTopology] = useState<Topology | null>(null);
  const [runtimeError, setRuntimeError] = useState(false);
  const [topologyError, setTopologyError] = useState(false);
  const [runtimeReceivedAt, setRuntimeReceivedAt] = useState<number | null>(null);
  const [topologyReceivedAt, setTopologyReceivedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => performance.now());
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);
  const mounted = useRef(false);
  const pending = useRef<Promise<void> | null>(null);
  const controller = useRef<AbortController | null>(null);
  const requestTimeout = useRef<number | null>(null);
  const expired = useRef(false);

  const refresh = useCallback((manual = false): Promise<void> => {
    if (pending.current) return pending.current;
    if (expired.current && !manual) return Promise.resolve();
    if (manual) {
      expired.current = false;
      setSessionExpired(false);
    }
    const request = new AbortController();
    controller.current = request;
    const current = () => mounted.current && controller.current === request;
    const unauthorized = (error: unknown) => {
      if (current() && error instanceof ApiError && error.status === 401) {
        expired.current = true;
        setSessionExpired(true);
      }
    };
    if (manual) setRefreshing(true);
    const timeout = window.setTimeout(() => request.abort(), STATUS_TIMEOUT_MS);
    requestTimeout.current = timeout;
    pending.current = Promise.allSettled([
      getRuntimeStatus(request.signal).then(
        (value) => {
          if (!current()) return;
          setRuntime(value);
          setRuntimeError(false);
          setRuntimeReceivedAt(performance.now());
          setNow(performance.now());
        },
        (error: unknown) => { unauthorized(error); if (current()) setRuntimeError(true); },
      ),
      getTopology(request.signal).then(
        (value) => {
          if (!current()) return;
          setTopology(value);
          setTopologyError(false);
          setTopologyReceivedAt(performance.now());
          setNow(performance.now());
        },
        (error: unknown) => { unauthorized(error); if (current()) setTopologyError(true); },
      ),
    ]).then(() => {
      window.clearTimeout(timeout);
      if (!current()) return;
      requestTimeout.current = null;
      pending.current = null;
      controller.current = null;
      setLoading(false);
      setRefreshing(false);
    });
    return pending.current;
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    const poll = window.setInterval(() => {
      if (document.visibilityState !== "hidden") void refresh();
    }, STATUS_REFRESH_MS);
    const resume = () => {
      setNow(performance.now());
      if (document.visibilityState !== "hidden") void refresh();
    };
    document.addEventListener("visibilitychange", resume);
    return () => {
      mounted.current = false;
      controller.current?.abort();
      controller.current = null;
      pending.current = null;
      if (requestTimeout.current !== null) window.clearTimeout(requestTimeout.current);
      window.clearInterval(poll);
      document.removeEventListener("visibilitychange", resume);
    };
  }, [refresh]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow((previous) => {
      const current = performance.now();
      return snapshotFreshness(runtimeReceivedAt, runtimeError, previous) ===
        snapshotFreshness(runtimeReceivedAt, runtimeError, current) &&
        snapshotFreshness(topologyReceivedAt, topologyError, previous) ===
        snapshotFreshness(topologyReceivedAt, topologyError, current)
        ? previous : current;
    }), 1000);
    return () => window.clearInterval(timer);
  }, [runtimeReceivedAt, topologyReceivedAt, runtimeError, topologyError]);

  return {
    runtime, topology, runtimeError, topologyError, loading, refreshing, refresh, sessionExpired,
    runtimeFreshness: snapshotFreshness(runtimeReceivedAt, runtimeError, now),
    topologyFreshness: snapshotFreshness(topologyReceivedAt, topologyError, now),
  };
}
