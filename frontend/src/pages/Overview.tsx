import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ClipboardCheck,
  Clock3,
  Cpu,
  Database,
  Film,
  Gauge,
  HardDrive,
  Layers,
  RefreshCw,
  ScanLine,
  Video,
} from "lucide-react";
import { PageHeader, LiveChip } from "../components/AppShell";
import { IncidentRow } from "../components/IncidentBits";
import { IncidentDrawer } from "../components/IncidentDrawer";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  KeyValue,
  LoadingBlock,
  Note,
  ProgressBar,
  SectionHeader,
  Stat,
} from "../components/ui";
import { Squiggle } from "../components/Accents";
import { api } from "../lib/api";
import { useAsync, useLiveRefresh } from "../lib/hooks";
import type { Overview as OverviewData } from "../lib/types";
import { formatBytes } from "../lib/format";

export function Overview() {
  const navigate = useNavigate();
  const { data, loading, error, refresh } = useAsync<OverviewData>(() => api.overview(), [], {
    intervalMs: 8000,
  });
  const [selected, setSelected] = useState<string | null>(null);
  useLiveRefresh(refresh);

  useEffect(() => {
    document.title = "Overview · CivicWatch";
  }, []);

  if (loading && !data) return <LoadingBlock label="Reading local monitoring state" />;
  if (error && !data) {
    return (
      <Card>
        <Note tone="danger" icon={AlertTriangle}>
          The local backend is not responding: {error}. Start CivicWatch with the launcher, then reload.
        </Note>
      </Card>
    );
  }
  if (!data) return null;

  const incidents = data.incidents;
  const confirmed = (incidents.by_status.verified || 0) + (incidents.by_status.forwarded || 0);
  const maxTimeline = Math.max(1, ...data.timeline.map((entry) => entry.count));
  const activeJobs = data.monitoring.processing;

  return (
    <>
      <PageHeader
        eyebrow="Local monitoring"
        title="Civic operations overview"
        description="Everything is processed on this machine. Detections are potential violations and require human verification."
        actions={
          <>
            <LiveChip online={data.monitoring.device !== "cpu"} label={data.monitoring.device.toUpperCase()} />
            <Button icon={RefreshCw} size="sm" onClick={() => void refresh()}>
              Refresh
            </Button>
          </>
        }
      />

      {!data.monitoring.model_installed ? (
        <div className="mb-4">
          <Note tone="warning" icon={AlertTriangle}>
            The detection model <span className="font-mono">{data.monitoring.model_id}</span> is not
            installed.{" "}
            <Link className="underline decoration-dotted" to="/settings">
              Download it in Settings → AI models
            </Link>{" "}
            before processing video.
          </Note>
        </div>
      ) : null}

      {activeJobs.length > 0 ? (
        <div className="mb-4 space-y-2">
          {activeJobs.map((job) => (
            <Card key={job.id} className="!p-3">
              <div className="flex flex-wrap items-center gap-3">
                <span className="inline-flex items-center gap-1.5 text-xs font-medium text-teal">
                  <ScanLine size={13} className={job.status === "processing" ? "animate-pulse" : ""} />
                  {job.status === "paused" ? "Paused" : job.status === "queued" ? "Queued" : "Processing"}
                </span>
                <span className="truncate text-xs text-ink">{job.media_name}</span>
                <span className="font-mono text-[0.7rem] text-muted">
                  {job.frames_processed}/{job.frames_total} frames · {job.incidents} candidate(s)
                </span>
                <div className="ml-auto flex items-center gap-2">
                  <div className="w-32">
                    <ProgressBar value={job.progress} />
                  </div>
                  <span className="w-10 text-right font-mono text-[0.7rem] text-muted">
                    {Math.round(job.progress * 100)}%
                  </span>
                  <Link to="/analysis" className="text-xs text-teal hover:underline">
                    Open
                  </Link>
                </div>
              </div>
            </Card>
          ))}
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Stat
          label="Videos processed"
          value={data.performance.videos_processed}
          hint={`${data.videos.uploaded} uploaded locally`}
          icon={Film}
        />
        <Stat
          label="Detected incidents"
          value={incidents.total}
          hint="All are candidate signals"
          icon={ClipboardCheck}
          tone="blue"
        />
        <Stat
          label="Pending verification"
          value={incidents.pending_review}
          hint={incidents.pending_review > 0 ? "Awaiting a human decision" : "Queue is clear"}
          icon={Clock3}
          tone={incidents.pending_review > 0 ? "amber" : "neutral"}
        />
        <Stat
          label="Confirmed"
          value={confirmed}
          hint="Verified or forwarded"
          icon={CheckCircle2}
          tone="teal"
        />
        <Stat
          label="Resolved"
          value={incidents.by_status.resolved || 0}
          hint={`${incidents.by_status.dismissed || 0} dismissed`}
          icon={CheckCircle2}
          tone="positive"
        />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Card>
            <SectionHeader
              title="Recent detections"
              subtitle="Newest candidate incidents from video and live sources."
              icon={ScanLine}
              action={
                <Link
                  to="/incidents"
                  className="inline-flex items-center gap-1 text-xs font-medium text-teal hover:underline"
                >
                  Review queue <ArrowRight size={12} />
                </Link>
              }
            />
            {data.recent.length === 0 ? (
              <EmptyState
                icon={Video}
                title="No incidents yet"
                description="Upload a road video or connect an authorised camera, draw zones, then start monitoring."
                action={
                  <div className="flex gap-2">
                    <Button size="sm" variant="primary" onClick={() => navigate("/analysis")}>
                      Analyse a video
                    </Button>
                    <Button size="sm" onClick={() => navigate("/zones")}>
                      Draw zones
                    </Button>
                  </div>
                }
              />
            ) : (
              <div className="divide-y divide-line/70">
                {data.recent.map((incident) => (
                  <IncidentRow
                    key={incident.id}
                    incident={incident}
                    onOpen={() => setSelected(incident.id)}
                  />
                ))}
              </div>
            )}
          </Card>

          <Card>
            <SectionHeader
              title="Processing performance"
              subtitle="Measured from completed local jobs — no estimated or simulated figures."
              icon={Gauge}
            />
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div>
                <p className="field-label mb-1">Avg throughput</p>
                <p className="font-mono text-lg text-ink">
                  {data.performance.average_fps ?? "—"}
                  <span className="ml-1 text-[0.65rem] text-muted">frames/s</span>
                </p>
              </div>
              <div>
                <p className="field-label mb-1">Frames analysed</p>
                <p className="font-mono text-lg text-ink">{data.performance.frames_processed}</p>
              </div>
              <div>
                <p className="field-label mb-1">Processing time</p>
                <p className="font-mono text-lg text-ink">{data.performance.processing_hours} h</p>
              </div>
              <div>
                <p className="field-label mb-1">Failed jobs</p>
                <p className="font-mono text-lg text-ink">{data.performance.jobs_failed}</p>
              </div>
            </div>
            {data.performance.recent_jobs.length > 0 ? (
              <div className="mt-4 overflow-hidden rounded-lg border border-line">
                <table className="w-full text-left text-xs">
                  <thead className="bg-surface-muted/60">
                    <tr>
                      <th className="table-head px-3 py-2">Job</th>
                      <th className="table-head px-3 py-2">Device</th>
                      <th className="table-head px-3 py-2 text-right">Frames</th>
                      <th className="table-head px-3 py-2 text-right">F/s</th>
                      <th className="table-head px-3 py-2 text-right">Candidates</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line/70">
                    {data.performance.recent_jobs.map((job) => (
                      <tr key={job.id}>
                        <td className="px-3 py-1.5 font-mono text-[0.7rem] text-muted">{job.id.slice(0, 8)}</td>
                        <td className="px-3 py-1.5 text-muted">{job.device || "—"}</td>
                        <td className="px-3 py-1.5 text-right font-mono">{job.frames_processed}</td>
                        <td className="px-3 py-1.5 text-right font-mono">{job.frames_per_second ?? "—"}</td>
                        <td className="px-3 py-1.5 text-right font-mono">{job.incidents}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="mt-3 text-xs text-muted">No completed jobs yet.</p>
            )}
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <SectionHeader title="Monitoring system" icon={Activity} />
            <KeyValue
              items={[
                {
                  label: "Inference device",
                  value: (
                    <span className="inline-flex items-center gap-1.5">
                      <Cpu size={11} />
                      {data.monitoring.device.toUpperCase()}
                    </span>
                  ),
                },
                { label: "Acceleration", value: data.monitoring.device_reason },
                { label: "AI model", value: <span className="font-mono">{data.monitoring.model_id}</span> },
                {
                  label: "Model installed",
                  value: data.monitoring.model_installed ? (
                    <Badge tone="teal">ready</Badge>
                  ) : (
                    <Badge tone="amber">missing</Badge>
                  ),
                },
                {
                  label: "Cameras live",
                  value: `${data.monitoring.cameras_live} / ${data.monitoring.cameras_total}`,
                },
                { label: "Detection zones", value: data.monitoring.zones },
              ]}
            />
            <div className="mt-3 flex gap-2">
              <Button size="sm" onClick={() => navigate("/live")} className="flex-1">
                Cameras
              </Button>
              <Button size="sm" onClick={() => navigate("/zones")} className="flex-1">
                Zones
              </Button>
            </div>
          </Card>

          <Card>
            <SectionHeader title="Activity, last 7 days" icon={Layers} />
            <div className="flex h-24 items-end gap-1.5">
              {data.timeline.map((entry) => {
                const height = entry.count === 0 ? 3 : Math.max(6, (entry.count / maxTimeline) * 88);
                return (
                  <div key={entry.day} className="group flex flex-1 flex-col items-center gap-1">
                    <div className="relative flex w-full items-end justify-center" style={{ height: 88 }}>
                      <div
                        className="w-full rounded-t-sm bg-teal/70 transition-colors group-hover:bg-teal"
                        style={{ height }}
                        title={`${entry.count} candidate incident(s)`}
                      />
                    </div>
                    <span className="text-[0.6rem] text-muted">
                      {new Date(`${entry.day}T00:00:00Z`).toLocaleDateString(undefined, { weekday: "short" })}
                    </span>
                  </div>
                );
              })}
            </div>
            <div className="mt-2 flex items-center justify-between text-[0.68rem] text-muted">
              <span>{data.timeline.reduce((sum, entry) => sum + entry.count, 0)} candidates this week</span>
              <Squiggle className="text-line" width={56} />
            </div>
          </Card>

          <Card>
            <SectionHeader title="Specialised models" subtitle="Only dedicated models can produce these categories." />
            <div className="space-y-2">
              {data.categories.map((category) => (
                <div
                  key={category.category}
                  className="flex items-start justify-between gap-3 rounded-lg border border-line px-3 py-2"
                >
                  <div>
                    <p className="text-xs font-medium text-ink">{category.label}</p>
                    <p className="mt-0.5 text-[0.68rem] leading-snug text-muted">
                      {category.installed ? category.model?.name : "Not installed — no detections possible"}
                    </p>
                  </div>
                  {category.installed ? (
                    <Badge tone="teal">active</Badge>
                  ) : (
                    <Badge tone="amber">model required</Badge>
                  )}
                </div>
              ))}
            </div>
            <Link
              to="/settings"
              className="mt-2 inline-flex items-center gap-1 text-[0.7rem] text-teal hover:underline"
            >
              Install a specialised model <ArrowRight size={11} />
            </Link>
          </Card>

          <Card>
            <SectionHeader title="Local storage" icon={HardDrive} />
            <KeyValue
              items={[
                { label: "Used by CivicWatch", value: data.storage.human },
                { label: "Evidence", value: formatBytes(data.storage.bytes.evidence) },
                { label: "Source video", value: formatBytes(data.storage.bytes.media) },
                { label: "Free disk", value: `${data.storage.free_gb} GB` },
                {
                  label: "Evidence retention",
                  value: `${data.storage.retention.evidence_days} days`,
                },
              ]}
            />
            <p className="mt-2 flex items-center gap-1 text-[0.65rem] text-muted">
              <Database size={10} />
              <span className="truncate">{data.storage.data_dir}</span>
            </p>
          </Card>
        </div>
      </div>

      <p className="mt-6 max-w-3xl text-[0.7rem] leading-relaxed text-muted">
        {data.safety.disclaimer} CivicWatch never issues challans, penalties or enforcement notices.
        Footage stays on this machine and evidence expires according to the retention policy.
      </p>

      <IncidentDrawer
        incidentId={selected}
        onClose={() => setSelected(null)}
        onChanged={() => void refresh()}
      />
    </>
  );
}
