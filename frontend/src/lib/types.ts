export type IncidentStatus =
  | "detected"
  | "pending_review"
  | "verified"
  | "dismissed"
  | "forwarded"
  | "resolved";

export type Severity = "low" | "medium" | "high";

export interface Incident {
  id: string;
  category: string;
  label: string;
  confidence: number;
  camera: string;
  location: string | null;
  captured_at: string;
  created_at: string;
  updated_at: string;
  track_id: number | null;
  zone_id: string | null;
  zone_name: string | null;
  dwell_seconds: number | null;
  status: IncidentStatus;
  severity: Severity;
  source_type: "video" | "live";
  source_ref: string;
  class_name: string | null;
  snapshot_path: string | null;
  clip_path: string | null;
  has_snapshot: boolean;
  has_clip: boolean;
  evidence: Record<string, unknown>;
  events?: { status: string | null; note: string | null; created_at: string }[];
  disclaimer?: string;
}

export interface IncidentStats {
  total: number;
  pending_review: number;
  by_status: Record<IncidentStatus, number>;
  by_category: Record<string, number>;
}

export interface Zone {
  id: string;
  name: string;
  kind: "no_parking" | "footpath" | "road" | "restricted";
  kind_label: string;
  scope_type: "all" | "media" | "camera";
  scope_id: string | null;
  polygon: [number, number][];
  rules: ZoneRules;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface ZoneRules {
  dwell_seconds: number;
  classes: string[];
  severity: Severity;
  double_parking: boolean;
  no_stopping: boolean;
  overlap_threshold?: number;
  neighbour_ratio?: number;
}

export interface ZoneKind {
  value: Zone["kind"];
  label: string;
  description: string;
  defaults: ZoneRules;
}

export interface Media {
  id: string;
  original_name: string;
  stored_name: string;
  size_bytes: number;
  duration_s: number;
  width: number;
  height: number;
  fps: number;
  frame_count: number;
  created_at: string;
  exists: boolean;
  processed?: boolean;
  jobs?: { id: string; status: string; incidents: number; created_at: string }[];
  location?: string | null;
}

export interface Job {
  id: string;
  media_id: string;
  media_name: string;
  status: "queued" | "processing" | "paused" | "completed" | "stopped" | "failed";
  progress: number;
  frames_processed: number;
  frames_total: number;
  sample_every: number;
  imgsz: number;
  detections: number;
  incidents: number;
  device: string | null;
  model_id: string | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  elapsed_s: number;
  fps?: number;
  eta_s?: number | null;
  has_preview_video?: boolean;
  preview_frame?: boolean;
  annotated_duration_s?: number | null;
}

export interface Camera {
  id: string;
  name: string;
  source_type: "webcam" | "rtsp" | "http";
  display_source: string;
  location: string | null;
  notes: string | null;
  authorised: boolean;
  has_credentials: boolean;
  created_at: string;
  updated_at: string;
  last_status: string;
  last_error: string | null;
  monitoring: boolean;
  session: LiveSession | null;
}

export interface LiveSession {
  camera_id: string;
  status: string;
  error: string | null;
  fps: number;
  frames: number;
  detections: number;
  incidents: number;
  device: string | null;
  started_at: string;
  resolution: string | null;
  has_frame: boolean;
}

export interface BuiltinModel {
  id: string;
  name: string;
  filename: string;
  url: string;
  size_mb: number;
  license: string;
  family: string;
  description: string;
  recommended: boolean;
  installed: boolean;
  installed_bytes: number;
  download: {
    status: "idle" | "downloading" | "verifying" | "ready" | "error";
    progress: number;
    downloaded_bytes: number;
    total_bytes: number;
    error: string | null;
  };
}

export interface CustomModel {
  id: string;
  name: string;
  category: string;
  filename: string;
  license: string | null;
  source: string | null;
  installed_at: string;
  installed: boolean;
  enabled: boolean;
  metadata: { classes?: string[]; sha256?: string; bytes?: number };
}

export interface CategoryStatus {
  category: string;
  label: string;
  description: string;
  installed: boolean;
  model: { id: string; name: string; license: string; classes: string[] } | null;
}

export interface Settings {
  device_preference: "auto" | "mps" | "cpu";
  model_id: string;
  confidence: number;
  iou_threshold: number;
  imgsz: number;
  sample_every: number;
  max_processing_width: number;
  save_annotated_video: boolean;
  tracker_track_thresh: number;
  tracker_match_thresh: number;
  tracker_buffer: number;
  tracker_class_aware: boolean;
  store_snapshots: boolean;
  store_clips: boolean;
  clip_pre_seconds: number;
  clip_post_seconds: number;
  clip_width: number;
  blur_persons: boolean;
  evidence_retention_days: number;
  media_retention_days: number;
  incident_cooldown_minutes: number;
  operator_name: string;
  theme: string;
  live_sample_every: number;
}

export interface DeviceInfo {
  device: string;
  reason: string;
  mps_available: boolean;
  mps_built: boolean;
  cuda_available: boolean;
  torch_version: string | null;
  machine: string;
  arm64: boolean;
}

export interface HardwareInfo {
  platform: string;
  system: string;
  machine: string;
  apple_silicon: boolean;
  cpu: string;
  cpu_cores: number;
  memory_gb: number | null;
  python: string;
  torch: Record<string, unknown>;
  device: DeviceInfo;
  disk: { total_gb: number; free_gb: number; data_dir: string };
  app: { name: string; version: string; offline_capable: boolean };
}

export interface Overview {
  app: { name: string; version: string };
  monitoring: {
    device: string;
    device_reason: string;
    model_id: string;
    model_installed: boolean;
    cameras_live: number;
    cameras_total: number;
    zones: number;
    processing: Job[];
  };
  incidents: IncidentStats;
  recent: Incident[];
  performance: {
    videos_processed: number;
    jobs_failed: number;
    frames_processed: number;
    processing_hours: number;
    average_fps: number | null;
    recent_jobs: {
      id: string;
      status: string;
      device: string;
      model_id: string;
      frames_processed: number;
      incidents: number;
      runtime_s: number;
      frames_per_second: number | null;
      created_at: string;
    }[];
  };
  storage: StorageInfo;
  videos: { uploaded: number; processed: number };
  timeline: { day: string; count: number }[];
  hardware: HardwareInfo;
  cameras: {
    id: string;
    name: string;
    source_type: string;
    monitoring: boolean;
    status: string;
    display_source: string;
    last_error: string | null;
  }[];
  categories: CategoryStatus[];
  safety: { disclaimer: string };
}

export interface StorageInfo {
  bytes: {
    media: number;
    evidence: number;
    processed: number;
    models: number;
    database: number;
    total: number;
  };
  human: string;
  free_gb: number;
  counts: { videos: number; incidents: number; zones: number; cameras: number };
  data_dir: string;
  retention: { evidence_days: number; media_days: number };
}

export interface DiagnosticCheck {
  name: string;
  ok: boolean;
  detail: string;
  level: "required" | "optional";
}

export interface Diagnostics {
  ok: boolean;
  checks: DiagnosticCheck[];
  device: DeviceInfo;
  settings_device: string;
  data_dir: string;
  checked_at: string;
}

export type StreamEvent = {
  type: string;
  seq: number;
  ts: number;
  data: Record<string, unknown>;
};
