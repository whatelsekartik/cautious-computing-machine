import { useState } from "react";
import { ImageOff, MapPin } from "lucide-react";
import type { Incident } from "../lib/types";
import { api } from "../lib/api";
import { categoryLabel, CLASS_LABELS, formatRelative } from "../lib/format";
import { Badge, StatusBadge } from "./ui";

export function IncidentThumb({
  incident,
  className = "",
  showBadge = true,
}: {
  incident: Incident;
  className?: string;
  showBadge?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const src = incident.has_snapshot ? api.incidents.snapshotUrl(incident.id) : null;
  return (
    <div
      className={`relative overflow-hidden rounded-lg border border-line bg-surface-muted ${className}`}
    >
      {src && !failed ? (
        <img
          src={src}
          alt={`Evidence frame for ${incident.id}`}
          className="h-full w-full object-cover"
          loading="lazy"
          onError={() => setFailed(true)}
        />
      ) : (
        <div className="flex h-full w-full flex-col items-center justify-center gap-1 text-muted">
          <ImageOff size={16} />
          <span className="text-[0.65rem]">No snapshot</span>
        </div>
      )}
      {showBadge ? (
        <span className="absolute left-1.5 top-1.5">
          <StatusBadge status={incident.status} />
        </span>
      ) : null}
      {incident.has_clip ? (
        <span className="absolute bottom-1.5 right-1.5 rounded bg-[rgba(20,26,28,0.62)] px-1.5 py-0.5 font-mono text-[0.6rem] text-white">
          clip
        </span>
      ) : null}
    </div>
  );
}

export function IncidentRow({
  incident,
  onOpen,
}: {
  incident: Incident;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full items-center gap-3 rounded-lg px-1.5 py-2 text-left transition-colors hover:bg-surface-muted/70"
    >
      <IncidentThumb incident={incident} className="h-11 w-16 shrink-0" showBadge={false} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-[0.82rem] font-medium text-ink">
            {categoryLabel(incident.category, incident.label)}
          </p>
          <StatusBadge status={incident.status} />
        </div>
        <p className="mt-0.5 flex items-center gap-1.5 truncate text-[0.7rem] text-muted">
          <span className="font-mono">{incident.id}</span>
          <span>·</span>
          <span className="truncate">{incident.camera}</span>
          {incident.zone_name ? (
            <>
              <span>·</span>
              <span className="inline-flex items-center gap-0.5 truncate">
                <MapPin size={9} />
                {incident.zone_name}
              </span>
            </>
          ) : null}
        </p>
      </div>
      <div className="hidden shrink-0 text-right sm:block">
        <p className="font-mono text-[0.72rem] text-ink">{incident.confidence.toFixed(2)}</p>
        <p className="text-[0.65rem] text-muted">{formatRelative(incident.captured_at)}</p>
      </div>
    </button>
  );
}

export function CategoryBadge({ category, label }: { category: string; label?: string }) {
  return <Badge tone="neutral">{categoryLabel(category, label)}</Badge>;
}

export function ClassLabel({ value }: { value?: string | null }) {
  if (!value) return <>—</>;
  return <>{CLASS_LABELS[value] || value}</>;
}
