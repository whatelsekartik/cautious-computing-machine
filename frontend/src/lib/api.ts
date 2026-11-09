import { isCloud } from "./deployment";
import type {
  AuthStatus,
  BuiltinModel,
  CloudConfig,
  Connector,
  DeviceInfo,
  Camera,
  CategoryStatus,
  CustomModel,
  Diagnostics,
  Incident,
  IncidentStats,
  Job,
  Media,
  Overview,
  Settings,
  StorageInfo,
  UploadTicket,
  Zone,
  ZoneKind,
  ZoneRules,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method || "GET").toUpperCase();
  const mutating = !["GET", "HEAD", "OPTIONS"].includes(method);
  const response = await fetch(path, {
    ...init,
    // The cloud deployment authenticates with an HttpOnly session cookie.
    credentials: "same-origin",
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      // Cookie-authenticated writes additionally require this header, which a
      // cross-site form cannot set (CSRF defence in cloud mode).
      ...(mutating && isCloud() ? { "X-CivicWatch-CSRF": "1" } : {}),
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      if (typeof payload?.detail === "string") detail = payload.detail;
      else if (Array.isArray(payload?.detail)) {
        detail = payload.detail.map((d: { msg?: string }) => d.msg).join("; ");
      }
    } catch {
      /* keep status text */
    }
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    return (await response.text()) as unknown as T;
  }
  return (await response.json()) as T;
}

const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  });
  const text = search.toString();
  return text ? `?${text}` : "";
};

export interface IncidentQuery {
  status?: string;
  category?: string;
  search?: string;
  severity?: string;
  source_type?: string;
  source_ref?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
  sort?: string;
}

export const api = {
  health: () => request<{ status: string }>("/api/health"),
  cloudConfig: () => request<CloudConfig>("/api/cloud/config"),

  auth: {
    status: () => request<AuthStatus>("/api/auth/status"),
    login: (token: string) =>
      request<{ ok: boolean; expires_at: string }>("/api/auth/session", {
        method: "POST",
        body: JSON.stringify({ token }),
      }),
    logout: () => request<{ ok: boolean }>("/api/auth/session", { method: "DELETE" }),
  },

  connectors: {
    list: () => request<{ items: Connector[] }>("/api/connectors"),
    create: (payload: { name: string; notes?: string }) =>
      request<Connector>("/api/connectors", { method: "POST", body: JSON.stringify(payload) }),
    remove: (id: string) =>
      request<{ ok: boolean }>(`/api/connectors/${id}`, { method: "DELETE" }),
  },
  overview: () => request<Overview>("/api/overview"),
  systemInfo: () => request<Record<string, unknown>>("/api/system/info"),
  diagnostics: () => request<Diagnostics>("/api/system/diagnostics"),

  settings: () =>
    request<{ values: Settings; defaults: Settings; device: { device: string; reason: string } }>(
      "/api/settings",
    ),
  updateSettings: (patch: Partial<Settings>) =>
    request<{ values: Settings }>("/api/settings", {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  models: () =>
    request<{
      builtin: BuiltinModel[];
      custom: CustomModel[];
      categories: CategoryStatus[];
      selected: string;
      device: DeviceInfo;
      downloads: Record<string, unknown>;
    }>("/api/models"),
  downloadModel: (id: string, force = false) =>
    request<{ status: string }>(`/api/models/${id}/download${force ? "?force=true" : ""}`, {
      method: "POST",
    }),
  deleteModel: (id: string) => request<{ ok: boolean }>(`/api/models/${id}`, { method: "DELETE" }),
  installCustomModel: (form: FormData) =>
    request<CustomModel>("/api/models/custom", { method: "POST", body: form }),
  toggleCustomModel: (id: string, enabled: boolean) =>
    request<CustomModel>(`/api/models/custom/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    }),
  deleteCustomModel: (id: string) =>
    request<{ ok: boolean }>(`/api/models/custom/${id}`, { method: "DELETE" }),

  storage: () => request<StorageInfo>("/api/storage"),
  cleanup: (payload: { evidence_days?: number; media_days?: number; purge_all?: boolean }) =>
    request<Record<string, unknown>>("/api/storage/cleanup", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  purgeEvidence: () =>
    request<Record<string, unknown>>("/api/privacy/purge-evidence", { method: "POST" }),

  media: {
    list: () => request<{ items: Media[] }>("/api/media"),
    /**
     * Cloud upload: ask the backend for a presigned PUT, send the file straight
     * to object storage (never through a serverless function), then mark it
     * ready. Large videos therefore do not hit the 4.5 MB function limit.
     */
    uploadDirect: async (file: File, onProgress?: (fraction: number) => void) => {
      const ticket = await request<UploadTicket>("/api/media/upload-url", {
        method: "POST",
        body: JSON.stringify({
          filename: file.name,
          content_type: file.type || "video/mp4",
          size_bytes: file.size,
        }),
      });
      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open(ticket.upload.method || "PUT", ticket.upload.url);
        Object.entries(ticket.upload.headers || {}).forEach(([key, value]) =>
          xhr.setRequestHeader(key, value),
        );
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total);
        };
        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) resolve();
          else reject(new ApiError(`Storage rejected the upload (${xhr.status})`, xhr.status));
        };
        xhr.onerror = () =>
          reject(new ApiError("Upload failed — check the storage CORS settings", 0));
        xhr.send(file);
      });
      const ready = await request<{ media_id: string; status: string }>(
        `/api/media/${ticket.media_id}/complete`,
        { method: "POST" },
      );
      return { ...ticket, ...ready };
    },
    upload: (file: File, location?: string) => {
      const form = new FormData();
      form.append("file", file);
      if (location) form.append("location", location);
      return request<Media>("/api/media", { method: "POST", body: form });
    },
    remove: (id: string) => request<{ ok: boolean }>(`/api/media/${id}`, { method: "DELETE" }),
    frameUrl: (id: string, t = 0) => `/api/media/${id}/frame?t=${t}`,
  },

  jobs: {
    list: (limit = 30) => request<{ items: Job[] }>(`/api/jobs?limit=${limit}`),
    create: (payload: {
      media_id: string;
      sample_every?: number;
      imgsz?: number;
      model_id?: string;
      max_width?: number;
      location?: string;
    }) => request<Job>("/api/jobs", { method: "POST", body: JSON.stringify(payload) }),
    get: (id: string) => request<Job>(`/api/jobs/${id}`),
    control: (id: string, action: "pause" | "resume" | "stop") =>
      request<Job>(`/api/jobs/${id}/${action}`, { method: "POST" }),
    remove: (id: string) => request<{ ok: boolean }>(`/api/jobs/${id}`, { method: "DELETE" }),
    streamUrl: (id: string) => `/api/jobs/${id}/stream`,
    frameUrl: (id: string, t = 0) => `/api/jobs/${id}/frame?t=${t}`,
    videoUrl: (id: string) => `/api/jobs/${id}/video`,
  },

  zones: {
    list: (scope_type?: string, scope_id?: string) =>
      request<{ items: Zone[] }>(`/api/zones${qs({ scope_type, scope_id })}`),
    kinds: () => request<{ items: ZoneKind[] }>("/api/zones/kinds"),
    create: (payload: {
      name: string;
      kind: Zone["kind"];
      polygon: [number, number][];
      rules: Partial<ZoneRules>;
      scope_type: Zone["scope_type"];
      scope_id?: string | null;
      enabled?: boolean;
    }) => request<Zone>("/api/zones", { method: "POST", body: JSON.stringify(payload) }),
    update: (id: string, payload: Partial<Zone>) =>
      request<Zone>(`/api/zones/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
    remove: (id: string) => request<{ ok: boolean }>(`/api/zones/${id}`, { method: "DELETE" }),
  },

  cameras: {
    list: () => request<{ items: Camera[] }>("/api/cameras"),
    discover: () =>
      request<{ items: { index: number; label: string; available: boolean; resolution: string | null }[] }>(
        "/api/cameras/webcams/discover",
      ),
    create: (payload: {
      name: string;
      source_type: Camera["source_type"];
      source?: string;
      username?: string;
      password?: string;
      device_index?: number;
      location?: string;
      notes?: string;
      authorised: boolean;
    }) => request<Camera>("/api/cameras", { method: "POST", body: JSON.stringify(payload) }),
    update: (id: string, payload: Record<string, unknown>) =>
      request<Camera>(`/api/cameras/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
    remove: (id: string) => request<{ ok: boolean }>(`/api/cameras/${id}`, { method: "DELETE" }),
    test: (id: string) =>
      request<{ ok: boolean; latency_ms: number; message: string; resolution?: string }>(
        `/api/cameras/${id}/test`,
        { method: "POST" },
      ),
    start: (id: string) => request<Record<string, unknown>>(`/api/cameras/${id}/start`, { method: "POST" }),
    stop: (id: string) => request<Record<string, unknown>>(`/api/cameras/${id}/stop`, { method: "POST" }),
    streamUrl: (id: string) => `/api/cameras/${id}/stream`,
    frameUrl: (id: string, annotated = false) =>
      `/api/cameras/${id}/frame?annotated=${annotated}&_=${Date.now()}`,
  },

  incidents: {
    list: (query: IncidentQuery = {}) =>
      request<{ items: Incident[]; total: number; limit: number; offset: number }>(
        `/api/incidents${qs(query as Record<string, string | number>)}`,
      ),
    stats: () => request<IncidentStats>("/api/incidents/stats"),
    categories: () =>
      request<{ items: { value: string; label: string; count: number }[]; statuses: string[] }>(
        "/api/incidents/categories",
      ),
    get: (id: string) => request<Incident>(`/api/incidents/${id}`),
    update: (id: string, payload: { status?: string; note?: string }) =>
      request<Incident>(`/api/incidents/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
    remove: (id: string) => request<{ ok: boolean }>(`/api/incidents/${id}`, { method: "DELETE" }),
    snapshotUrl: (id: string) => `/api/incidents/${id}/snapshot`,
    previewUrl: (id: string) => `/api/incidents/${id}/preview`,
    clipUrl: (id: string) => `/api/incidents/${id}/clip`,
    exportPdfUrl: (params: { status?: string; ids?: string } = {}) =>
      `/api/incidents/export.pdf${qs(params)}`,
    exportCsvUrl: (params: { status?: string; ids?: string } = {}) =>
      `/api/incidents/export.csv${qs(params)}`,
  },

  audit: () => request<{ items: Record<string, string>[] }>("/api/audit?limit=50"),
};

export const eventStreamUrl = "/api/events";
