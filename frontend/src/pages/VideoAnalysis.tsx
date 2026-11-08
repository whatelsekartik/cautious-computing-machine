import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  Film,
  FolderOpen,
  Gauge,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  ScanLine,
  Square,
  Trash2,
  Upload,
  Video,
} from "lucide-react";
import { PageHeader } from "../components/AppShell";
import { IncidentRow } from "../components/IncidentBits";
import { IncidentDrawer } from "../components/IncidentDrawer";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  LoadingBlock,
  Note,
  ProgressBar,
  SectionHeader,
  Select,
  Slider,
  Spinner,
} from "../components/ui";
import { api } from "../lib/api";
import { useAsync, useLiveRefresh } from "../lib/hooks";
import type { Incident, Job, Media, Settings } from "../lib/types";
import { formatBytes, formatDateTime, formatDuration } from "../lib/format";
import { useToast } from "../components/Toast";

export function VideoAnalysis() {
  const toast = useToast();
  const navigate = useNavigate();
  const fileInput = useRef<HTMLInputElement | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [previewT, setPreviewT] = useState(0);
  const [job, setJob] = useState<Job | null>(null);
  const [reviewT, setReviewT] = useState(0);
  const [openIncident, setOpenIncident] = useState<string | null>(null);
  const [sampleEvery, setSampleEvery] = useState(5);
  const [imgsz, setImgsz] = useState(640);
  const [maxWidth, setMaxWidth] = useState(1280);
  const [modelId, setModelId] = useState("yolov8n");

  const media = useAsync(() => api.media.list(), []);
  const settings = useAsync<{ values: Settings; defaults: Settings; device: { device: string; reason: string } }>(
    () => api.settings(),
    [],
  );
  const models = useAsync(() => api.models(), []);
  const jobs = useAsync(() => api.jobs.list(12), [], { intervalMs: 5000 });

  useLiveRefresh(() => {
    void jobs.refresh(true);
    void media.refresh(true);
  }, ["job.updated"]);

  useEffect(() => {
    document.title = "Video analysis · CivicWatch";
  }, []);

  useEffect(() => {
    if (settings.data) {
      setSampleEvery(settings.data.values.sample_every);
      setImgsz(settings.data.values.imgsz);
      setMaxWidth(settings.data.values.max_processing_width);
      setModelId(settings.data.values.model_id);
    }
  }, [settings.data]);

  const items = media.data?.items ?? [];
  useEffect(() => {
    if (!selectedId && items.length > 0) setSelectedId(items[0].id);
  }, [items, selectedId]);

  const selected = useMemo(
    () => items.find((item) => item.id === selectedId) || null,
    [items, selectedId],
  );

  // Resume showing a running job for this video (e.g. after a page reload).
  useEffect(() => {
    const active = (jobs.data?.items ?? []).find(
      (entry) => entry.media_id === selectedId && ["queued", "processing", "paused"].includes(entry.status),
    );
    if (active) {
      setJob(active);
    } else if (job && ["queued", "processing", "paused"].includes(job.status)) {
      setJob(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs.data, selectedId]);

  // Fast polling only while a job is active.
  useEffect(() => {
    if (!job || !["queued", "processing", "paused"].includes(job.status)) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const updated = await api.jobs.get(job.id);
        if (!cancelled) {
          setJob(updated);
          if (["completed", "stopped", "failed"].includes(updated.status)) {
            setReviewT(0);
            void media.refresh(true);
            void jobs.refresh(true);
            if (updated.status === "completed") {
              toast.push(
                `Analysis finished — ${updated.incidents} candidate incident(s) generated`,
                "success",
              );
            }
            if (updated.status === "failed" && updated.error) toast.push(updated.error, "error");
          }
        }
      } catch {
        /* keep polling; transient backend hiccups are fine */
      }
    }, 1200);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id, job?.status]);

  const jobIncidents = useAsync(
    () =>
      selectedId
        ? api.incidents.list({ source_ref: selectedId, limit: 30 })
        : Promise.resolve({ items: [] as Incident[], total: 0, limit: 30, offset: 0 }),
    [selectedId, job?.status],
    { intervalMs: 8000 },
  );

  const upload = useCallback(
    async (file: File) => {
      if (!file) return;
      setUploading(file.name);
      try {
        const created = await api.media.upload(file);
        setSelectedId(created.id);
        setPreviewT(0);
        setJob(null);
        await media.refresh(true);
        toast.push(`${file.name} uploaded — draw zones, then start analysis`, "success");
      } catch (error) {
        toast.push((error as Error).message, "error");
      } finally {
        setUploading(null);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [media.refresh, toast],
  );

  const startJob = async () => {
    if (!selected) return;
    try {
      const created = await api.jobs.create({
        media_id: selected.id,
        sample_every: sampleEvery,
        imgsz,
        max_width: maxWidth,
        model_id: modelId,
      });
      setJob(created);
      toast.push("Processing started — frames stream below as they are analysed", "info");
      void jobs.refresh(true);
    } catch (error) {
      toast.push((error as Error).message, "error");
    }
  };

  const control = async (action: "pause" | "resume" | "stop") => {
    if (!job) return;
    try {
      const updated = await api.jobs.control(job.id, action);
      setJob(updated);
    } catch (error) {
      toast.push((error as Error).message, "error");
    }
  };

  const removeMedia = async () => {
    if (!selected) return;
    try {
      await api.media.remove(selected.id);
      setSelectedId(null);
      setJob(null);
      await media.refresh(true);
      toast.push("Video and its processed outputs were removed", "success");
    } catch (error) {
      toast.push((error as Error).message, "error");
    }
  };

  const busy = job ? ["queued", "processing", "paused"].includes(job.status) : false;
  const reviewed = job && ["completed", "stopped"].includes(job.status);
  const availableModels = (models.data?.builtin ?? []).filter((model) => model.installed);
  const totalDuration = job?.annotated_duration_s || 0;

  return (
    <>
      <PageHeader
        eyebrow="Video analysis"
        title="Analyse recorded footage"
        description="Upload a road video, preview a frame, choose sampling, then process locally. Annotated frames show boxes and tracking IDs."
        actions={
          <Button icon={RefreshCw} size="sm" onClick={() => void media.refresh()}>
            Refresh
          </Button>
        }
      />

      <div className="grid gap-4 xl:grid-cols-[1.55fr_1fr]">
        <div className="space-y-4">
          <Card padded={false} className="overflow-hidden">
            <div
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                const file = event.dataTransfer.files?.[0];
                if (file) void upload(file);
              }}
              className={`flex flex-wrap items-center gap-3 border-b border-line p-3 transition-colors ${
                dragging ? "bg-teal-soft/60" : "bg-surface-muted/40"
              }`}
            >
              <span className="inline-flex items-center gap-2 rounded-lg border border-dashed border-line bg-surface px-3 py-2 text-xs text-muted">
                <Upload size={14} />
                Drop a video here, or
              </span>
              <Button size="sm" icon={FolderOpen} onClick={() => fileInput.current?.click()} loading={Boolean(uploading)}>
                {uploading ? "Uploading…" : "Choose file"}
              </Button>
              <span className="text-[0.68rem] text-muted">
                MP4, MOV, AVI, MKV, WEBM · processed on this machine only
              </span>
              <input
                ref={fileInput}
                type="file"
                accept="video/mp4,video/quicktime,video/x-msvideo,video/x-matroska,video/webm,.mp4,.mov,.m4v,.avi,.mkv,.webm"
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void upload(file);
                  event.target.value = "";
                }}
              />
            </div>

            <div className="relative bg-[#0f1416]">
              {!selected ? (
                <div className="flex h-[320px] items-center justify-center text-xs text-white/60">
                  Select or upload a video to begin
                </div>
              ) : busy ? (
                <img
                  src={api.jobs.streamUrl(job!.id)}
                  alt="Annotated processing preview"
                  className="max-h-[440px] w-full object-contain"
                />
              ) : reviewed && job?.has_preview_video ? (
                <img
                  src={api.jobs.frameUrl(job.id, reviewT)}
                  alt="Annotated frame"
                  className="max-h-[440px] w-full object-contain"
                />
              ) : (
                <img
                  src={api.media.frameUrl(selected.id, previewT)}
                  alt={`Frame from ${selected.original_name}`}
                  className="max-h-[440px] w-full object-contain"
                />
              )}
              {busy ? (
                <span className="absolute left-2.5 top-2.5 inline-flex items-center gap-1.5 rounded-full bg-[rgba(20,26,28,0.7)] px-2 py-1 text-[0.65rem] text-white">
                  <ScanLine size={11} className="animate-pulse" />
                  {job?.status === "paused" ? "Paused" : "Analysing"}
                </span>
              ) : null}
            </div>

            <div className="space-y-2 p-3">
              {busy || reviewed ? (
                <div className="flex items-center gap-3">
                  <span className="w-14 shrink-0 text-[0.68rem] text-muted">
                    {busy ? `${Math.round((job?.progress ?? 0) * 100)}%` : "review"}
                  </span>
                  <input
                    type="range"
                    min={0}
                    max={busy ? Math.max(1, job?.frames_total || 1) : Math.max(1, Math.round(totalDuration))}
                    value={
                      busy
                        ? Math.round((job?.progress ?? 0) * Math.max(1, job?.frames_total || 1))
                        : Math.min(reviewT, Math.round(totalDuration))
                    }
                    onChange={(event) => {
                      if (busy) return;
                      setReviewT(Number(event.target.value));
                    }}
                    disabled={busy}
                    className="h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-line accent-teal disabled:cursor-not-allowed"
                    aria-label="Annotated footage timeline"
                  />
                  <span className="w-20 shrink-0 text-right font-mono text-[0.66rem] text-muted">
                    {busy
                      ? `${job?.frames_processed}/${job?.frames_total}`
                      : `${formatDuration(reviewT)} / ${formatDuration(totalDuration)}`}
                  </span>
                </div>
              ) : selected ? (
                <div className="flex items-center gap-3">
                  <span className="w-14 shrink-0 text-[0.68rem] text-muted">preview</span>
                  <input
                    type="range"
                    min={0}
                    max={Math.max(1, Math.floor(selected.duration_s))}
                    value={Math.min(previewT, Math.floor(selected.duration_s))}
                    onChange={(event) => setPreviewT(Number(event.target.value))}
                    className="h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-line accent-teal"
                    aria-label="Source video timeline"
                  />
                  <span className="w-20 shrink-0 text-right font-mono text-[0.66rem] text-muted">
                    {formatDuration(previewT)} / {formatDuration(selected.duration_s)}
                  </span>
                </div>
              ) : null}

              {selected ? (
                <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                  <div className="min-w-0">
                    <p className="truncate text-xs font-medium text-ink">{selected.original_name}</p>
                    <p className="text-[0.66rem] text-muted">
                      {selected.width}×{selected.height} · {selected.fps} fps ·{" "}
                      {formatDuration(selected.duration_s)} · {formatBytes(selected.size_bytes)}
                    </p>
                  </div>
                  <div className="flex gap-1.5">
                    {reviewed && job?.has_preview_video ? (
                      <a className="btn btn-secondary btn-sm" href={api.jobs.videoUrl(job.id)} download>
                        <Download size={12} /> Annotated video
                      </a>
                    ) : null}
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={Trash2}
                      onClick={() => void removeMedia()}
                      disabled={busy}
                    >
                      Remove
                    </Button>
                  </div>
                </div>
              ) : null}
            </div>
          </Card>

          {job ? (
            <Card>
              <SectionHeader
                title={`Job ${job.id.slice(0, 8)}`}
                subtitle={`${job.media_name} · ${job.sample_every === 1 ? "every frame" : `every ${job.sample_every}th frame`} · ${job.imgsz}px · ${job.device || "device pending"}`}
                icon={Gauge}
                action={
                  <div className="flex gap-1.5">
                    {job.status === "processing" ? (
                      <Button size="sm" icon={Pause} onClick={() => void control("pause")}>
                        Pause
                      </Button>
                    ) : null}
                    {job.status === "paused" ? (
                      <Button size="sm" icon={Play} onClick={() => void control("resume")} variant="primary">
                        Resume
                      </Button>
                    ) : null}
                    {busy ? (
                      <Button size="sm" variant="danger" icon={Square} onClick={() => void control("stop")}>
                        Stop
                      </Button>
                    ) : null}
                  </div>
                }
              />
              <div className="space-y-3">
                <div>
                  <div className="mb-1 flex items-center justify-between text-[0.7rem] text-muted">
                    <span className="inline-flex items-center gap-1.5">
                      {job.status === "processing" ? (
                        <Loader2 size={11} className="animate-spin" />
                      ) : job.status === "completed" ? (
                        <CheckCircle2 size={11} className="text-positive" />
                      ) : job.status === "failed" ? (
                        <AlertTriangle size={11} className="text-danger" />
                      ) : null}
                      {job.status.replace("_", " ")}
                    </span>
                    <span className="font-mono">
                      {job.frames_processed}/{job.frames_total || "?"} frames
                      {job.fps ? ` · ${job.fps.toFixed(1)} f/s` : ""}
                      {job.eta_s ? ` · ~${formatDuration(job.eta_s)} left` : ""}
                    </span>
                  </div>
                  <ProgressBar
                    value={job.progress}
                    tone={job.status === "failed" ? "danger" : job.status === "completed" ? "teal" : "blue"}
                  />
                </div>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <Metric label="Detections" value={job.detections} />
                  <Metric label="Candidate incidents" value={job.incidents} tone="amber" />
                  <Metric label="Elapsed" value={formatDuration(job.elapsed_s)} />
                  <Metric label="Device" value={(job.device || "—").toUpperCase()} />
                </div>
                {job.error ? (
                  <Note tone="danger" icon={AlertTriangle}>
                    {job.error}
                  </Note>
                ) : null}
                {job.incidents > 0 ? (
                  <Note tone="info">
                    {job.incidents} candidate incident(s) were stored with snapshots and clips.{" "}
                    <button
                      type="button"
                      className="underline decoration-dotted"
                      onClick={() => navigate("/incidents")}
                    >
                      Open the review queue
                    </button>{" "}
                    to verify them.
                  </Note>
                ) : null}
              </div>
            </Card>
          ) : null}

          <Card>
            <SectionHeader
              title="Incidents from this video"
              subtitle="Only candidates generated from the selected source are listed."
              icon={ScanLine}
            />
            {(jobIncidents.data?.items ?? []).length === 0 ? (
              <p className="py-3 text-xs text-muted">
                No incidents recorded for this video yet.
              </p>
            ) : (
              <div className="divide-y divide-line/70">
                {(jobIncidents.data?.items ?? []).map((incident) => (
                  <IncidentRow
                    key={incident.id}
                    incident={incident}
                    onOpen={() => setOpenIncident(incident.id)}
                  />
                ))}
              </div>
            )}
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <SectionHeader title="Processing settings" subtitle="Applies to the next run." icon={Film} />
            <div className="space-y-3">
              <Field label="Detection model" hint="Installed models only. Manage them in Settings.">
                <Select value={modelId} onChange={(event) => setModelId(event.target.value)}>
                  {availableModels.length === 0 ? <option value="">No model installed</option> : null}
                  {availableModels.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Slider
                label="Frame sampling"
                value={sampleEvery}
                min={1}
                max={30}
                onChange={setSampleEvery}
                display={sampleEvery === 1 ? "every frame" : `1 in ${sampleEvery}`}
                hint="Higher values are faster but may miss short events."
              />
              <Field label="Processing resolution">
                <Select value={imgsz} onChange={(event) => setImgsz(Number(event.target.value))}>
                  <option value={320}>320 px — fastest</option>
                  <option value={480}>480 px</option>
                  <option value={640}>640 px — recommended</option>
                  <option value={960}>960 px</option>
                  <option value={1280}>1280 px — most accurate</option>
                </Select>
              </Field>
              <Field label="Maximum video width">
                <Select value={maxWidth} onChange={(event) => setMaxWidth(Number(event.target.value))}>
                  <option value={640}>640 px</option>
                  <option value={960}>960 px</option>
                  <option value={1280}>1280 px</option>
                  <option value={1920}>1920 px</option>
                </Select>
              </Field>
              <Button
                variant="primary"
                className="w-full"
                icon={Play}
                disabled={!selected || busy || availableModels.length === 0}
                onClick={() => void startJob()}
              >
                {busy ? "Processing…" : "Start analysis"}
              </Button>
              {selected ? (
                <Button
                  className="w-full"
                  icon={ScanLine}
                  onClick={() => navigate(`/zones?media=${selected.id}`)}
                >
                  Draw zones for this video
                </Button>
              ) : null}
            </div>
          </Card>

          <Card>
            <SectionHeader title="Local video library" subtitle="Files stay in the local data folder." />
            {media.loading && !media.data ? (
              <Spinner label="Loading" />
            ) : items.length === 0 ? (
              <EmptyState
                icon={Video}
                title="No videos yet"
                description="Upload an MP4 or MOV file to get started."
              />
            ) : (
              <ul className="space-y-1">
                {items.map((item: Media) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedId(item.id);
                        setPreviewT(0);
                        setJob(null);
                      }}
                      className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors ${
                        item.id === selectedId ? "bg-teal-soft/70" : "hover:bg-surface-muted"
                      }`}
                    >
                      <Film size={13} className="shrink-0 text-muted" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs text-ink">{item.original_name}</span>
                        <span className="block text-[0.64rem] text-muted">
                          {formatDuration(item.duration_s)} · {item.processed ? "processed" : "not processed"}
                        </span>
                      </span>
                      {item.processed ? <Badge tone="teal">done</Badge> : null}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            <SectionHeader title="Recent jobs" subtitle="Local processing history." />
            {jobs.loading && !jobs.data ? (
              <Spinner label="Loading" />
            ) : (jobs.data?.items ?? []).length === 0 ? (
              <p className="text-xs text-muted">No jobs yet.</p>
            ) : (
              <ul className="space-y-1.5">
                {(jobs.data?.items ?? []).slice(0, 6).map((entry) => (
                  <li key={entry.id}>
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-surface-muted"
                      onClick={() => {
                        setJob(entry);
                        setSelectedId(entry.media_id);
                        setReviewT(0);
                      }}
                    >
                      <Badge
                        tone={
                          entry.status === "completed"
                            ? "teal"
                            : entry.status === "failed"
                              ? "danger"
                              : entry.status === "processing"
                                ? "blue"
                                : "neutral"
                        }
                      >
                        {entry.status}
                      </Badge>
                      <span className="min-w-0 flex-1 truncate text-[0.7rem] text-muted">
                        {entry.media_name}
                      </span>
                      <span className="shrink-0 font-mono text-[0.64rem] text-muted">
                        {formatDateTime(entry.started_at || undefined).split(",")[0]}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>

      {media.loading && !media.data ? <LoadingBlock /> : null}

      <IncidentDrawer
        incidentId={openIncident}
        onClose={() => setOpenIncident(null)}
        onChanged={() => void jobIncidents.refresh(true)}
      />
    </>
  );
}

function Metric({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "amber" }) {
  return (
    <div>
      <p className="field-label mb-0.5">{label}</p>
      <p className={`font-mono text-sm ${tone === "amber" ? "text-amber" : "text-ink"}`}>{value}</p>
    </div>
  );
}
