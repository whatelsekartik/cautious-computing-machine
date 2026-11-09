import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CloudUpload,
  Cpu,
  Laptop,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Video,
} from "lucide-react";
import { PageHeader } from "../components/AppShell";
import { Badge, Button, Card, EmptyState, Note, ProgressBar, SectionHeader } from "../components/ui";
import { api } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { useToast } from "../components/Toast";
import { formatDateTime } from "../lib/format";
import type { Job } from "../lib/types";

const STATUS_TONE = {
  queued: "amber",
  processing: "teal",
  completed: "positive",
  failed: "danger",
  stopped: "neutral",
  paused: "neutral",
} as const;

/**
 * Cloud video upload.
 *
 * The browser uploads straight to private object storage with a presigned URL
 * (so a 2 GB video never passes through a serverless function), then queues the
 * file as an assignment. A local connector downloads it, runs YOLO/ByteTrack on
 * the operator's own hardware and posts the incidents back here.
 */
export function CloudUploads() {
  const toast = useToast();
  const input = useRef<HTMLInputElement | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [filename, setFilename] = useState<string | null>(null);

  const jobs = useAsync(() => api.jobs.list(20), [], { intervalMs: 4000 });
  const connectors = useAsync(() => api.connectors.list(), [], { intervalMs: 15000 });

  useEffect(() => {
    document.title = "Video uploads · CivicWatch";
  }, []);

  const online = (connectors.data?.items || []).filter((connector) => connector.online);

  const upload = async (file: File) => {
    if (!file) return;
    if (!file.type.startsWith("video/") && !/\.(mp4|avi|mov|mkv|webm)$/i.test(file.name)) {
      toast.push("Choose a video file (MP4, MOV, AVI, MKV or WEBM).", "error");
      return;
    }
    setBusy(true);
    setProgress(0);
    setFilename(file.name);
    try {
      const ticket = await api.media.uploadDirect(file, setProgress);
      const job = await api.jobs.create({ media_id: ticket.media_id });
      toast.push(`Queued ${file.name} — job ${job.id}. A connector will pick it up.`, "success");
      await jobs.refresh(true);
    } catch (problem) {
      toast.push(problem instanceof Error ? problem.message : "Upload failed", "error");
    } finally {
      setBusy(false);
      setFilename(null);
      setProgress(0);
      if (input.current) input.current.value = "";
    }
  };

  const items: Job[] = jobs.data?.items || [];
  const active = items.filter((job) => job.status === "processing" || job.status === "queued");

  return (
    <div>
      <PageHeader
        eyebrow="Cloud mode"
        title="Video uploads"
        description="Send footage straight to private storage. Your connector downloads it, runs inference locally, and pushes reviewed incidents back to this dashboard."
        actions={
          <Button
            variant="ghost"
            size="sm"
            icon={RefreshCw}
            onClick={() => {
              void jobs.refresh(true);
              void connectors.refresh(true);
            }}
          >
            Refresh
          </Button>
        }
      />

      <div className="mb-4 grid gap-3 lg:grid-cols-[1.35fr_1fr]">
        <Card className="relative overflow-hidden">
          <div
            role="button"
            tabIndex={0}
            onClick={() => !busy && input.current?.click()}
            onKeyDown={(event) => {
              if ((event.key === "Enter" || event.key === " ") && !busy) input.current?.click();
            }}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              if (busy) return;
              const file = event.dataTransfer.files?.[0];
              if (file) void upload(file);
            }}
            className={`flex min-h-[220px] cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-8 text-center transition-colors ${
              dragging ? "border-teal bg-teal-soft/60" : "border-line bg-surface-muted/40 hover:border-teal/50"
            } ${busy ? "cursor-wait opacity-80" : ""}`}
          >
            {busy ? (
              <>
                <Loader2 size={26} className="mb-3 animate-spin text-teal" />
                <p className="text-sm font-medium text-ink">Uploading {filename}</p>
                <div className="mt-3 w-full max-w-sm">
                  <ProgressBar value={progress} />
                </div>
                <p className="mt-2 text-[0.72rem] text-muted">
                  {Math.round(progress * 100)}% · sent directly to object storage
                </p>
              </>
            ) : (
              <>
                <CloudUpload size={26} className="mb-3 text-teal" />
                <p className="text-sm font-medium text-ink">
                  Drop a video here or click to choose one
                </p>
                <p className="mt-1 max-w-sm text-[0.75rem] leading-relaxed text-muted">
                  Large files upload directly to private storage — they never pass through a
                  serverless function.
                </p>
              </>
            )}
            <input
              ref={input}
              type="file"
              accept="video/*,.mkv"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void upload(file);
              }}
            />
          </div>
        </Card>

        <Card>
          <SectionHeader icon={Laptop} title="Where processing happens" />
          <ul className="mt-2 space-y-2 text-[0.78rem] leading-relaxed text-muted">
            <li className="flex gap-2">
              <ShieldCheck size={14} className="mt-0.5 shrink-0 text-positive" />
              Vercel hosts the dashboard, database and API only. It never runs inference.
            </li>
            <li className="flex gap-2">
              <Cpu size={14} className="mt-0.5 shrink-0 text-teal" />
              A connector on your Mac downloads the file, runs YOLO + ByteTrack, writes evidence and
              uploads annotated output.
            </li>
            <li className="flex gap-2">
              <Video size={14} className="mt-0.5 shrink-0 text-muted" />
              Incidents appear in the review queue with snapshots and clips in private storage.
            </li>
          </ul>
          <div className="mt-3 border-t border-line pt-3">
            {connectors.loading ? (
              <p className="text-[0.75rem] text-muted">Checking connectors…</p>
            ) : online.length ? (
              <div className="space-y-1.5">
                {online.map((connector) => (
                  <div key={connector.id} className="flex items-center justify-between text-[0.75rem]">
                    <span className="inline-flex items-center gap-1.5 text-ink">
                      <span className="h-1.5 w-1.5 rounded-full bg-positive" />
                      {connector.name}
                    </span>
                    <span className="text-muted">
                      {connector.last_seen_at ? formatDateTime(connector.last_seen_at) : "online"}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <Note tone="warning" icon={AlertTriangle}>
                No connector is online. Start the connector on the Mac that has the cameras —
                run <span className="font-mono text-[0.72rem]">Start Cloud Connector.command</span>.
              </Note>
            )}
          </div>
        </Card>
      </div>

      {active.length ? (
        <Note tone="info" icon={Cpu}>
          {active.length === 1
            ? "1 job is waiting for a connector."
            : `${active.length} jobs are waiting for a connector.`}{" "}
          Progress updates arrive by polling every few seconds.
        </Note>
      ) : null}

      <Card className="mt-4">
        <SectionHeader
          icon={Video}
          title="Processing queue"
          action={
            <span className="text-[0.72rem] text-muted">
              {items.length} job{items.length === 1 ? "" : "s"}
            </span>
          }
        />
        {jobs.loading && !items.length ? (
          <p className="py-8 text-center text-sm text-muted">Loading jobs…</p>
        ) : items.length ? (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-[0.8rem]">
              <thead className="text-[0.68rem] uppercase tracking-[0.08em] text-muted">
                <tr>
                  <th className="pb-2 pr-3 font-medium">Job</th>
                  <th className="pb-2 pr-3 font-medium">Video</th>
                  <th className="pb-2 pr-3 font-medium">Status</th>
                  <th className="pb-2 pr-3 font-medium">Progress</th>
                  <th className="pb-2 pr-3 font-medium">Frames</th>
                  <th className="pb-2 pr-3 font-medium">Incidents</th>
                  <th className="pb-2 font-medium">Queued</th>
                </tr>
              </thead>
              <tbody>
                {items.map((job) => (
                  <tr key={job.id} className="border-t border-line/70">
                    <td className="py-2 pr-3 font-mono text-[0.72rem] text-muted">{job.id}</td>
                    <td className="max-w-[220px] truncate py-2 pr-3 text-ink">{job.media_name}</td>
                    <td className="py-2 pr-3">
                      <Badge tone={STATUS_TONE[job.status] || "neutral"}>{job.status}</Badge>
                    </td>
                    <td className="py-2 pr-3">
                      <div className="flex items-center gap-2">
                        <div className="w-24">
                          <ProgressBar value={job.progress || 0} />
                        </div>
                        <span className="text-[0.72rem] text-muted">
                          {Math.round((job.progress || 0) * 100)}%
                        </span>
                      </div>
                    </td>
                    <td className="py-2 pr-3 text-muted">
                      {job.frames_processed}/{job.frames_total || "?"}
                    </td>
                    <td className="py-2 pr-3 text-muted">{job.incidents}</td>
                    <td className="py-2 text-muted">{formatDateTime(job.started_at || "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={Video}
            title="No videos yet"
            description="Upload a recorded street video to queue it for your connector."
          />
        )}
      </Card>

      <p className="mt-3 text-[0.7rem] leading-relaxed text-muted">
        Uploaded videos live in private object storage and are served through short-lived signed URLs.
        Retention follows your evidence settings; camera streams and credentials never leave the
        connector machine.
      </p>
    </div>
  );
}
