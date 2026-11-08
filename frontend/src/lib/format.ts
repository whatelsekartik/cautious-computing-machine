export const STATUS_LABELS: Record<string, string> = {
  detected: "Detected",
  pending_review: "Pending review",
  verified: "Verified",
  dismissed: "Dismissed",
  forwarded: "Forwarded",
  resolved: "Resolved",
};

export const CATEGORY_LABELS: Record<string, string> = {
  illegal_parking: "Potential illegal parking",
  double_parking: "Potential double parking",
  footpath_obstruction: "Potential footpath obstruction",
  restricted_entry: "Potential restricted-zone entry",
  stopped_in_carriageway: "Potential stopping in carriageway",
  pothole: "Potential pothole",
  garbage: "Potential garbage accumulation",
};

export const CLASS_LABELS: Record<string, string> = {
  car: "Car",
  motorcycle: "Motorcycle",
  bus: "Bus",
  truck: "Truck",
  bicycle: "Bicycle",
  person: "Pedestrian",
};

export function categoryLabel(category: string, fallback?: string): string {
  return CATEGORY_LABELS[category] || fallback || category.replace(/_/g, " ");
}

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] || status.replace(/_/g, " ");
}

export function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatRelative(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const diff = Date.now() - date.getTime();
  const minutes = Math.round(diff / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} d ago`;
  return formatDateTime(value);
}

export function formatDuration(seconds?: number | null): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "—";
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function formatBytes(bytes?: number | null): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

export function formatClock(seconds: number): string {
  return formatDuration(seconds);
}

export function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export function titleCase(value: string): string {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}
