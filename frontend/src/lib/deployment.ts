import type { CloudConfig, DeploymentMode } from "./types";

/**
 * One frontend bundle serves both deployments:
 *
 *  - **local**  — FastAPI on this Mac, full inference, SSE live updates;
 *  - **cloud**  — Vercel functions + dashboard; inference runs on a connector.
 *
 * The backend tells us which one we are talking to, so pages can hide controls
 * that only make sense locally (camera capture, model downloads, zone drawing)
 * instead of failing with 404s in production.
 */

const LOCAL_FALLBACK: CloudConfig = {
  app: { name: "CivicWatch", version: "1.0.0" },
  mode: "local",
  auth_required: false,
  deployment: null,
  capabilities: {
    local_inference: true,
    live_capture: true,
    video_upload: true,
    evidence_redirect: false,
    connector: true,
    exports: true,
  },
  limits: {
    max_upload_bytes: 20 * 1024 ** 3,
    max_json_request_bytes: 64 * 1024 * 1024,
    function_timeout_s: 0,
    incidents_per_batch: 500,
  },
};

let current: CloudConfig | null = null;
let loading: Promise<CloudConfig> | null = null;
const listeners = new Set<(config: CloudConfig) => void>();

export function deploymentMode(): DeploymentMode {
  return current?.mode ?? "local";
}

export function deployment(): CloudConfig {
  return current ?? LOCAL_FALLBACK;
}

export function isCloud(): boolean {
  return deploymentMode() === "cloud";
}

export function onDeploymentChange(listener: (config: CloudConfig) => void): () => void {
  listeners.add(listener);
  if (current) listener(current);
  return () => listeners.delete(listener);
}

/** Loads `/api/cloud/config` once and caches it (safe to call repeatedly). */
export function loadDeployment(): Promise<CloudConfig> {
  if (current) return Promise.resolve(current);
  if (loading) return loading;

  const fetchConfig = async (): Promise<CloudConfig> => {
    try {
      const response = await fetch("/api/cloud/config", { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`config ${response.status}`);
      const payload = (await response.json()) as Partial<CloudConfig>;
      return {
        app: { ...LOCAL_FALLBACK.app, ...(payload.app || {}) },
        mode: payload.mode === "cloud" ? "cloud" : "local",
        auth_required: Boolean(payload.auth_required),
        deployment: payload.deployment ?? null,
        capabilities: { ...LOCAL_FALLBACK.capabilities, ...(payload.capabilities || {}) },
        limits: { ...LOCAL_FALLBACK.limits, ...(payload.limits || {}) },
      };
    } catch {
      // Older local builds have no /api/cloud/config: treat them as local.
      return { ...LOCAL_FALLBACK };
    }
  };

  loading = fetchConfig()
    .then((config) => {
      current = config;
      listeners.forEach((listener) => listener(config));
      return config;
    })
    .finally(() => {
      loading = null;
    });
  return loading;
}

/** Test/helper hook: forget the cached config. */
export function resetDeployment(): void {
  current = null;
  loading = null;
}
