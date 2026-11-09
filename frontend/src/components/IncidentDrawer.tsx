import { useEffect, useState } from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  Check,
  Download,
  Film,
  Image as ImageIcon,
  Loader2,
  MapPin,
  Send,
  Trash2,
  Undo2,
  X,
} from "lucide-react";
import { api } from "../lib/api";
import type { Incident, IncidentStatus } from "../lib/types";
import { categoryLabel, formatDateTime, statusLabel } from "../lib/format";
import { Badge, Button, KeyValue, Modal, Note, SeverityBadge, StatusBadge, TextInput } from "./ui";
import { useToast } from "./Toast";

const ACTIONS: { status: IncidentStatus; label: string; variant: "primary" | "secondary" | "danger"; icon: typeof Check }[] = [
  { status: "verified", label: "Verify", variant: "primary", icon: Check },
  { status: "dismissed", label: "Dismiss", variant: "secondary", icon: X },
  { status: "forwarded", label: "Forward", variant: "secondary", icon: Send },
  { status: "resolved", label: "Resolve", variant: "secondary", icon: Check },
  { status: "pending_review", label: "Send to review", variant: "secondary", icon: Undo2 },
];

export function IncidentDrawer({
  incidentId,
  onClose,
  onChanged,
}: {
  incidentId: string | null;
  onClose: () => void;
  onChanged?: () => void;
}) {
  const [incident, setIncident] = useState<Incident | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [media, setMedia] = useState<"snapshot" | "clip">("snapshot");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const toast = useToast();

  useEffect(() => {
    if (!incidentId) {
      setIncident(null);
      return;
    }
    let active = true;
    setLoading(true);
    setMedia("snapshot");
    setConfirmDelete(false);
    api.incidents
      .get(incidentId)
      .then((data) => {
        if (active) setIncident(data);
      })
      .catch((error: Error) => toast.push(error.message, "error"))
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incidentId]);

  const applyStatus = async (status: IncidentStatus) => {
    if (!incident) return;
    setBusy(status);
    try {
      const updated = await api.incidents.update(incident.id, {
        status,
        note: note.trim() || undefined,
      });
      setIncident(updated);
      setNote("");
      toast.push(`Marked ${statusLabel(status).toLowerCase()}`, "success");
      onChanged?.();
    } catch (error) {
      toast.push((error as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    if (!incident) return;
    setBusy("delete");
    try {
      await api.incidents.remove(incident.id);
      toast.push("Incident deleted", "success");
      onChanged?.();
      onClose();
    } catch (error) {
      toast.push((error as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal
      open={Boolean(incidentId)}
      onClose={onClose}
      size="xl"
      title={incident ? `${incident.id} · ${categoryLabel(incident.category, incident.label)}` : "Incident"}
      description={
        incident
          ? `${formatDateTime(incident.captured_at)} · ${incident.camera}`
          : "Loading incident"
      }
      footer={
        incident ? (
          <>
            <Button
              size="sm"
              variant="ghost"
              icon={Trash2}
              onClick={() => (confirmDelete ? void remove() : setConfirmDelete(true))}
              loading={busy === "delete"}
            >
              {confirmDelete ? "Confirm delete" : "Delete"}
            </Button>
            <div className="flex-1" />
            {ACTIONS.map((action) => (
              <Button
                key={action.status}
                size="sm"
                variant={action.variant}
                icon={action.icon}
                loading={busy === action.status}
                disabled={incident.status === action.status}
                onClick={() => void applyStatus(action.status)}
              >
                {action.label}
              </Button>
            ))}
          </>
        ) : null
      }
    >
      {loading || !incident ? (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted">
          <Loader2 size={15} className="animate-spin" /> Loading evidence
        </div>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[1.35fr_1fr]">
          <div>
            <div className="overflow-hidden rounded-lg border border-line bg-[#0f1416]">
              {media === "snapshot" ? (
                incident.has_snapshot ? (
                  <img
                    src={api.incidents.snapshotUrl(incident.id)}
                    alt="Evidence snapshot"
                    className="max-h-[420px] w-full object-contain"
                  />
                ) : (
                  <div className="flex h-56 items-center justify-center text-xs text-white/60">
                    No snapshot was stored for this incident
                  </div>
                )
              ) : incident.has_clip ? (
                <img
                  src={api.incidents.previewUrl(incident.id)}
                  alt="Evidence clip preview"
                  className="max-h-[420px] w-full object-contain"
                />
              ) : (
                <div className="flex h-56 items-center justify-center text-xs text-white/60">
                  No clip was stored for this incident
                </div>
              )}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant={media === "snapshot" ? "secondary" : "ghost"}
                icon={ImageIcon}
                onClick={() => setMedia("snapshot")}
              >
                Snapshot
              </Button>
              <Button
                size="sm"
                variant={media === "clip" ? "secondary" : "ghost"}
                icon={Film}
                onClick={() => setMedia("clip")}
                disabled={!incident.has_clip}
              >
                Clip preview
              </Button>
              {incident.has_clip ? (
                <a
                  className="btn btn-ghost btn-sm"
                  href={api.incidents.clipUrl(incident.id)}
                  download={`${incident.id}.mp4`}
                >
                  <Download size={13} /> Download clip
                </a>
              ) : null}
              {incident.has_snapshot ? (
                <a
                  className="btn btn-ghost btn-sm"
                  href={api.incidents.snapshotUrl(incident.id)}
                  download={`${incident.id}.jpg`}
                >
                  <Download size={13} /> Snapshot
                </a>
              ) : null}
            </div>

            <div className="mt-4 space-y-3">
              <div>
                <label className="field-label" htmlFor="review-note">
                  Reviewer note
                </label>
                <TextInput
                  id="review-note"
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  placeholder="Reason for the decision, action taken, reference number…"
                  maxLength={2000}
                />
                <p className="mt-1 text-[0.68rem] text-muted">
                  Notes are stored with the incident and appear in exported reports.
                </p>
              </div>
              <Note tone="warning" icon={AlertTriangle}>
                This is a machine-generated candidate, not an established violation. Verify the
                evidence before forwarding it to any authority.
              </Note>
            </div>
          </div>

          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={incident.status} />
              <SeverityBadge severity={incident.severity} />
              <Badge tone="neutral">{incident.source_type === "live" ? "Live camera" : "Video file"}</Badge>
              {incident.track_id !== null ? (
                <Badge tone="neutral">track #{incident.track_id}</Badge>
              ) : null}
            </div>

            <KeyValue
              items={[
                { label: "Category", value: categoryLabel(incident.category, incident.label) },
                { label: "Detected class", value: incident.class_name || "—" },
                { label: "Confidence", value: incident.confidence.toFixed(2) },
                { label: "Captured (local time)", value: formatDateTime(incident.captured_at) },
                { label: "Camera", value: incident.camera },
                {
                  label: "Location",
                  value: (
                    <span className="inline-flex items-center gap-1">
                      <MapPin size={10} /> {incident.location || "Not recorded"}
                    </span>
                  ),
                },
                { label: "Zone", value: incident.zone_name || "—" },
                {
                  label: "Dwell time",
                  value: incident.dwell_seconds ? `${incident.dwell_seconds.toFixed(1)} s` : "—",
                },
                { label: "Source reference", value: <span className="font-mono">{incident.source_ref}</span> },
              ]}
            />

            <div>
              <p className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-ink">
                <ArrowUpRight size={12} /> Review history
              </p>
              <ol className="space-y-2 border-l border-line pl-3">
                {(incident.events || []).map((event, index) => (
                  <li key={`${event.created_at}-${index}`} className="relative">
                    <span className="absolute -left-[1.05rem] top-1 h-1.5 w-1.5 rounded-full bg-line" />
                    <p className="text-[0.72rem] font-medium text-ink">
                      {event.status ? statusLabel(event.status) : "Note"}
                    </p>
                    {event.note ? (
                      <p className="text-[0.7rem] leading-snug text-muted">{event.note}</p>
                    ) : null}
                    <p className="text-[0.64rem] text-muted/80">{formatDateTime(event.created_at)}</p>
                  </li>
                ))}
              </ol>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
