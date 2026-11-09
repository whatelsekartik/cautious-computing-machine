import { useCallback, useEffect, useRef, useState } from "react";
import { eventStreamUrl } from "./api";
import { deployment, isCloud, loadDeployment, onDeploymentChange } from "./deployment";
import type { CloudConfig, StreamEvent } from "./types";

interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

/** Minimal data loader with manual refresh, optional polling and no state races. */
export function useAsync<T>(
  loader: () => Promise<T>,
  deps: unknown[] = [],
  options: { intervalMs?: number; enabled?: boolean } = {},
) {
  const { intervalMs, enabled = true } = options;
  const [state, setState] = useState<AsyncState<T>>({ data: null, loading: true, error: null });
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const mounted = useRef(true);
  const requestId = useRef(0);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = useCallback(
    async (silent = false) => {
      if (!enabled) return;
      const id = ++requestId.current;
      if (!silent) setState((prev) => ({ ...prev, loading: true }));
      try {
        const data = await loaderRef.current();
        if (!mounted.current || id !== requestId.current) return;
        setState({ data, loading: false, error: null });
      } catch (error) {
        if (!mounted.current || id !== requestId.current) return;
        setState((prev) => ({
          data: prev.data,
          loading: false,
          error: error instanceof Error ? error.message : String(error),
        }));
      }
    },
    [enabled],
  );

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    if (!intervalMs || !enabled) return;
    const timer = window.setInterval(() => void load(true), intervalMs);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, enabled]);

  return {
    ...state,
    refresh: load,
    setData: (data: T) => setState({ data, loading: false, error: null }),
  };
}

/**
 * Live updates from the backend SSE stream (job progress, incidents, cameras).
 *
 * Serverless functions cannot hold a long-lived SSE connection, so in cloud
 * mode there is no stream at all: every page already refreshes on an interval,
 * and `useLiveRefresh` falls back to polling. The hook still reports connected
 * so the shell shows "polling" instead of a red offline light.
 */
export function useEventStream(onEvent: (event: StreamEvent) => void) {
  const [connected, setConnected] = useState(false);
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    if (isCloud()) {
      setConnected(true);
      return;
    }
    let source: EventSource | null = null;
    let retry: number | undefined;
    let disposed = false;

    const connect = () => {
      if (disposed) return;
      source = new EventSource(eventStreamUrl);
      source.onopen = () => setConnected(true);
      source.onerror = () => {
        setConnected(false);
        source?.close();
        retry = window.setTimeout(connect, 3000);
      };
      source.onmessage = (message) => {
        if (!message.data) return;
        try {
          handler.current(JSON.parse(message.data) as StreamEvent);
        } catch {
          /* ignore malformed frame */
        }
      };
      // Named SSE events need explicit listeners.
      ["job.updated", "incident.created", "live.status", "hello"].forEach((type) => {
        source?.addEventListener(type, (message) => {
          const data = (message as MessageEvent).data;
          if (!data) return;
          try {
            handler.current(JSON.parse(data) as StreamEvent);
          } catch {
            /* ignore */
          }
        });
      });
    };

    connect();
    return () => {
      disposed = true;
      if (retry) window.clearTimeout(retry);
      source?.close();
      setConnected(false);
    };
  }, []);

  return { connected };
}

export type ThemeMode = "light" | "dark" | "system";

export function useTheme() {
  const [mode, setMode] = useState<ThemeMode>(() => {
    const stored = localStorage.getItem("civicwatch-theme");
    return stored === "light" || stored === "dark" ? stored : "system";
  });

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const dark = mode === "dark" || (mode === "system" && media.matches);
      document.documentElement.classList.toggle("dark", dark);
      document.documentElement.style.colorScheme = dark ? "dark" : "light";
    };
    apply();
    media.addEventListener("change", apply);
    localStorage.setItem("civicwatch-theme", mode);
    return () => media.removeEventListener("change", apply);
  }, [mode]);

  return { mode, setMode };
}

/** Tracks the natural size of an element (used by the zone canvas). */
export function useElementSize<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return { ref, size };
}

/** Refresh data when the backend reports a relevant change (debounced). */
export function useLiveRefresh(refresh: () => void, types: string[] = ["job.updated", "incident.created", "live.status"]) {
  const callback = useRef(refresh);
  callback.current = refresh;
  useEffect(() => {
    if (isCloud()) {
      // No SSE in serverless: refresh on a gentle interval instead.
      const timer = window.setInterval(() => callback.current(), 15000);
      return () => window.clearInterval(timer);
    }
    let timer: number | undefined;
    const onEvent = (event: Event | MessageEvent) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data as string) as StreamEvent;
        if (!types.includes(payload.type)) return;
      } catch {
        return;
      }
      window.clearTimeout(timer);
      timer = window.setTimeout(() => callback.current(), 700);
    };
    const source = new EventSource(eventStreamUrl);
    types.forEach((type) => source.addEventListener(type, onEvent));
    return () => {
      window.clearTimeout(timer);
      source.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [types.join(",")]);
}


/** The deployment this bundle is talking to (local Mac or cloud dashboard). */
export function useDeployment(): CloudConfig {
  const [config, setConfig] = useState<CloudConfig>(deployment());
  useEffect(() => {
    let active = true;
    void loadDeployment().then((loaded) => {
      if (active) setConfig(loaded);
    });
    const unsubscribe = onDeploymentChange((loaded) => setConfig(loaded));
    return () => {
      active = false;
      unsubscribe();
    };
  }, []);
  return config;
}
