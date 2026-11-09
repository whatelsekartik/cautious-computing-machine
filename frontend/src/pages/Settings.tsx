import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  BadgeCheck,
  Check,
  Cpu,
  Database,
  Download,
  HardDrive,
  Info,
  KeyRound,
  Loader2,
  RefreshCw,
  Save,
  ShieldCheck,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { PageHeader } from "../components/AppShell";
import { ConnectorsCard } from "../components/ConnectorsCard";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  KeyValue,
  LoadingBlock,
  Note,
  ProgressBar,
  SectionHeader,
  Segmented,
  Select,
  Slider,
  TextInput,
  Toggle,
} from "../components/ui";
import { api } from "../lib/api";
import { useAsync } from "../lib/hooks";
import type { CloudConfig, Diagnostics, Settings } from "../lib/types";
import { useToast } from "../components/Toast";
import { formatBytes, formatDateTime } from "../lib/format";

export function SettingsPage({ deployment }: { deployment: CloudConfig }) {
  const cloud = deployment.mode === "cloud";
  const toast = useToast();
  const settings = useAsync(() => api.settings(), []);
  const models = useAsync(() => api.models(), [], { intervalMs: 2000 });
  const storage = useAsync(() => api.storage(), []);
  const systemInfo = useAsync(() => api.systemInfo(), []);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [runningDiagnostics, setRunningDiagnostics] = useState(false);
  const [draft, setDraft] = useState<Settings | null>(null);
  const [saving, setSaving] = useState(false);
  const [customName, setCustomName] = useState("");
  const [customCategory, setCustomCategory] = useState("pothole");
  const [customLicense, setCustomLicense] = useState("");
  const [installing, setInstalling] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [cleanupDays, setCleanupDays] = useState<number | null>(null);

  useEffect(() => {
    document.title = "Settings · CivicWatch";
  }, []);

  useEffect(() => {
    if (settings.data) setDraft(settings.data.values);
  }, [settings.data]);

  const dirty = useMemo(() => {
    if (!draft || !settings.data) return false;
    return (Object.keys(draft) as (keyof Settings)[]).some(
      (key) => draft[key] !== settings.data!.values[key],
    );
  }, [draft, settings.data]);

  const set = <K extends keyof Settings>(key: K, value: Settings[K]) =>
    setDraft((prev) => (prev ? { ...prev, [key]: value } : prev));

  const save = async () => {
    if (!draft || !settings.data) return;
    const patch: Partial<Settings> = {};
    (Object.keys(draft) as (keyof Settings)[]).forEach((key) => {
      if (draft[key] !== settings.data!.values[key]) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (patch as any)[key] = draft[key];
      }
    });
    setSaving(true);
    try {
      const result = await api.updateSettings(patch);
      setDraft(result.values);
      await settings.refresh(true);
      toast.push("Settings saved", "success");
    } catch (error) {
      toast.push((error as Error).message, "error");
    } finally {
      setSaving(false);
    }
  };

  const runDiagnostics = async () => {
    setRunningDiagnostics(true);
    try {
      setDiagnostics(await api.diagnostics());
    } catch (error) {
      toast.push((error as Error).message, "error");
    } finally {
      setRunningDiagnostics(false);
    }
  };

  const downloadModel = async (id: string, force = false) => {
    try {
      await api.downloadModel(id, force);
      toast.push(
        force
          ? "Re-downloading weights — the current file stays in use until the new one validates"
          : "Download started — weights are verified after transfer",
        "info",
      );
      await models.refresh(true);
    } catch (error) {
      toast.push((error as Error).message, "error");
    }
  };

  const removeModel = async (id: string) => {
    try {
      await api.deleteModel(id);
      toast.push("Model file removed", "success");
      await models.refresh(true);
    } catch (error) {
      toast.push((error as Error).message, "error");
    }
  };

  const installCustom = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      toast.push("Choose a .pt weights file first", "error");
      return;
    }
    if (!customName.trim()) {
      toast.push("Give the model a name", "error");
      return;
    }
    const form = new FormData();
    form.append("file", file);
    form.append("name", customName.trim());
    form.append("category", customCategory);
    form.append("license", customLicense);
    setInstalling(true);
    try {
      await api.installCustomModel(form);
      toast.push("Custom model installed and enabled", "success");
      setCustomName("");
      setCustomLicense("");
      if (fileRef.current) fileRef.current.value = "";
      await models.refresh(true);
    } catch (error) {
      toast.push((error as Error).message, "error");
    } finally {
      setInstalling(false);
    }
  };

  const cleanup = async (purgeAll = false) => {
    try {
      const result = await api.cleanup(
        purgeAll ? { purge_all: true } : { evidence_days: cleanupDays ?? undefined },
      );
      toast.push(
        `Freed ${result.human_freed as string} · ${result.evidence_files_removed} evidence file(s) removed`,
        "success",
      );
      await storage.refresh(true);
    } catch (error) {
      toast.push((error as Error).message, "error");
    }
  };

  if (!draft || !settings.data) return <LoadingBlock label="Loading settings" />;
  const device = models.data?.device;
  const hardware = (systemInfo.data?.hardware ?? null) as {
    platform?: string;
    cpu?: string;
    cpu_cores?: number;
    memory_gb?: number | null;
    python?: string;
    disk?: { free_gb: number; total_gb: number };
  } | null;

  return (
    <>
      <PageHeader
        eyebrow="Configuration"
        title="Settings"
        description={
          cloud
            ? "Dashboard, storage, privacy and connector settings. Detection thresholds are applied by the local connector; inference never runs here."
            : "Hardware, AI models, detection thresholds and privacy controls. Changes apply to new jobs and camera sessions."
        }
        actions={
          <>
            <Button size="sm" variant="ghost" disabled={!dirty} onClick={() => setDraft(settings.data!.values)}>
              Discard
            </Button>
            <Button
              size="sm"
              variant="primary"
              icon={Save}
              disabled={!dirty}
              loading={saving}
              onClick={() => void save()}
            >
              {dirty ? "Save changes" : "Saved"}
            </Button>
          </>
        }
      />

      {cloud ? <ConnectorsCard /> : null}

      <div className="grid gap-4 xl:grid-cols-[1.1fr_1fr]">
        <div className="space-y-4">
          <Card>
            <SectionHeader
              title="Device & acceleration"
              subtitle={device?.reason}
              icon={Cpu}
              action={
                <Button size="sm" icon={RefreshCw} loading={runningDiagnostics} onClick={() => void runDiagnostics()}>
                  Run diagnostics
                </Button>
              }
            />
            <Segmented
              value={draft.device_preference}
              onChange={(value) => set("device_preference", value)}
              options={[
                { value: "auto", label: "Auto" },
                { value: "mps", label: "Apple MPS" },
                { value: "cpu", label: "CPU" },
              ]}
            />
            <p className="mt-2 text-[0.7rem] leading-relaxed text-muted">
              {device?.mps_available
                ? "Apple MPS is available on this Mac — inference runs on the GPU."
                : "MPS is unavailable here, so inference uses the CPU. On Apple Silicon with PyTorch 2.x, MPS is detected automatically."}
            </p>

            {diagnostics ? (
              <div className="mt-3 space-y-1">
                {diagnostics.checks.map((check) => (
                  <div key={check.name} className="flex items-start gap-2 rounded-md px-1.5 py-1 text-[0.72rem]">
                    {check.ok ? (
                      <Check size={13} className="mt-0.5 shrink-0 text-positive" />
                    ) : check.level === "required" ? (
                      <X size={13} className="mt-0.5 shrink-0 text-danger" />
                    ) : (
                      <Info size={13} className="mt-0.5 shrink-0 text-muted" />
                    )}
                    <span className="min-w-0 flex-1">
                      <span className="font-medium text-ink">{check.name}</span>
                      <span className="block text-[0.68rem] leading-snug text-muted">{check.detail}</span>
                    </span>
                    {check.level === "optional" ? <Badge>optional</Badge> : null}
                  </div>
                ))}
                <p className="pt-1 text-[0.65rem] text-muted">
                  Checked {formatDateTime(diagnostics.checked_at)} · data folder{" "}
                  <span className="font-mono">{diagnostics.data_dir}</span>
                </p>
              </div>
            ) : (
              <p className="mt-3 text-[0.7rem] text-muted">
                Run diagnostics to verify the runtime, model files, storage and database on this machine.
              </p>
            )}
          </Card>

          {!cloud ? (
          <>
          <Card>
            <SectionHeader
              title="AI detection model"
              subtitle="Vehicle and pedestrian detection runs locally with Ultralytics YOLO weights."
              icon={BadgeCheck}
            />
            <div className="space-y-2">
              {(models.data?.builtin ?? []).map((model) => {
                const downloading = model.download?.status === "downloading" || model.download?.status === "verifying";
                return (
                  <div key={model.id} className="rounded-lg border border-line p-2.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs font-medium text-ink">{model.name}</span>
                      {model.recommended ? <Badge tone="teal">recommended</Badge> : null}
                      <Badge tone="neutral">{model.license}</Badge>
                      <span className="ml-auto flex items-center gap-1.5">
                        {model.installed ? (
                          <>
                            <Badge tone="positive">
                              <Check size={10} /> installed
                            </Badge>
                            <Button
                              size="sm"
                              variant="ghost"
                              icon={RefreshCw}
                              onClick={() => void downloadModel(model.id, true)}
                              title="Re-download and re-validate the weights"
                            >
                              Update
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              icon={Trash2}
                              onClick={() => void removeModel(model.id)}
                            >
                              Remove
                            </Button>
                          </>
                        ) : downloading ? (
                          <span className="inline-flex items-center gap-1.5 text-[0.68rem] text-muted">
                            <Loader2 size={12} className="animate-spin" /> {Math.round((model.download?.progress || 0) * 100)}%
                          </span>
                        ) : (
                          <Button size="sm" icon={Download} onClick={() => void downloadModel(model.id)}>
                            Download
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant={models.data?.selected === model.id ? "primary" : "ghost"}
                          onClick={() => set("model_id", model.id)}
                          disabled={!model.installed}
                        >
                          {models.data?.selected === model.id ? (
                            "Selected"
                          ) : (
                            "Use"
                          )}
                        </Button>
                      </span>
                    </div>
                    <p className="mt-1 text-[0.68rem] leading-snug text-muted">{model.description}</p>
                    {downloading ? (
                      <div className="mt-1.5">
                        <ProgressBar value={model.download?.progress || 0} />
                      </div>
                    ) : null}
                    {model.download?.status === "error" && model.download.error ? (
                      <p className="mt-1 text-[0.68rem] text-danger">{model.download.error}</p>
                    ) : null}
                  </div>
                );
              })}
            </div>
            <p className="mt-2 text-[0.68rem] leading-relaxed text-muted">
              Weights are validated by loading the checkpoint locally after download. Ultralytics
              YOLO weights are AGPL-3.0; CivicWatch is distributed under the same licence. For a
              closed-source deployment you need an Ultralytics Enterprise licence.
            </p>
          </Card>

          <Card>
            <SectionHeader
              title="Specialised models"
              subtitle="Potholes and waste need their own trained weights. They are never inferred from the vehicle model."
              icon={ShieldCheck}
            />
            <div className="space-y-2">
              {(models.data?.categories ?? []).map((category) => {
                const installed = (models.data?.custom ?? []).filter(
                  (model) => model.category === category.category,
                );
                return (
                  <div key={category.category} className="rounded-lg border border-line p-2.5">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium text-ink">{category.label}</span>
                      {category.installed ? (
                        <Badge tone="positive">active</Badge>
                      ) : (
                        <Badge tone="amber">model required</Badge>
                      )}
                    </div>
                    <p className="mt-1 text-[0.68rem] leading-snug text-muted">{category.description}</p>
                    {installed.length > 0 ? (
                      <div className="mt-2 space-y-1">
                        {installed.map((model) => (
                          <div key={model.id} className="flex items-center gap-2 rounded-md bg-surface-muted/60 px-2 py-1">
                            <span className="min-w-0 flex-1 truncate text-[0.7rem] text-ink">{model.name}</span>
                            <Toggle
                              label=""
                              checked={model.enabled}
                              onChange={async (value) => {
                                await api.toggleCustomModel(model.id, value);
                                await models.refresh(true);
                              }}
                            />
                            <button
                              type="button"
                              className="text-muted hover:text-danger"
                              aria-label={`Delete ${model.name}`}
                              onClick={async () => {
                                await api.deleteCustomModel(model.id);
                                await models.refresh(true);
                              }}
                            >
                              <Trash2 size={12} />
                            </button>
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>

            <div className="mt-3 rounded-lg border border-dashed border-line p-2.5">
              <p className="text-xs font-medium text-ink">Install a custom model</p>
              <p className="mt-0.5 text-[0.68rem] text-muted">
                Ultralytics-format detection weights (.pt). CivicWatch reads the class names and runs
                the model alongside the built-in detector.
              </p>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                <Field label="Model name">
                  <TextInput
                    value={customName}
                    onChange={(event) => setCustomName(event.target.value)}
                    placeholder="e.g. Ward pothole detector v2"
                  />
                </Field>
                <Field label="Category">
                  <Select value={customCategory} onChange={(event) => setCustomCategory(event.target.value)}>
                    <option value="pothole">Pothole</option>
                    <option value="garbage">Garbage / waste</option>
                  </Select>
                </Field>
                <Field label="Weights file (.pt)">
                  <input
                    ref={fileRef}
                    type="file"
                    accept=".pt"
                    className="input !py-1.5 text-xs"
                  />
                </Field>
                <Field label="Licence / source (optional)">
                  <TextInput
                    value={customLicense}
                    onChange={(event) => setCustomLicense(event.target.value)}
                    placeholder="e.g. trained in-house, MIT"
                  />
                </Field>
              </div>
              <Button size="sm" icon={Upload} className="mt-2" loading={installing} onClick={() => void installCustom()}>
                Install model
              </Button>
            </div>
          </Card>

          </>
          ) : null}

          <Card>
            <SectionHeader title="Detection & tracking" subtitle="Applies to new processing runs." />
            <div className="space-y-3">
              <Slider
                label="Confidence threshold"
                value={draft.confidence}
                min={0.05}
                max={0.95}
                step={0.01}
                onChange={(value) => set("confidence", value)}
                display={draft.confidence.toFixed(2)}
                hint="Lower values find more objects but increase false candidates."
              />
              <Slider
                label="NMS IoU threshold"
                value={draft.iou_threshold}
                min={0.1}
                max={0.95}
                step={0.01}
                onChange={(value) => set("iou_threshold", value)}
                display={draft.iou_threshold.toFixed(2)}
              />
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Default image size">
                  <Select value={draft.imgsz} onChange={(event) => set("imgsz", Number(event.target.value))}>
                    {[320, 480, 640, 960, 1280].map((size) => (
                      <option key={size} value={size}>
                        {size} px
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Default frame sampling">
                  <Select
                    value={draft.sample_every}
                    onChange={(event) => set("sample_every", Number(event.target.value))}
                  >
                    {[1, 2, 3, 5, 10, 15, 20, 30].map((value) => (
                      <option key={value} value={value}>
                        {value === 1 ? "Every frame" : `1 in ${value}`}
                      </option>
                    ))}
                  </Select>
                </Field>
              </div>
              <Slider
                label="Tracker track threshold"
                value={draft.tracker_track_thresh}
                min={0.1}
                max={0.9}
                step={0.01}
                onChange={(value) => set("tracker_track_thresh", value)}
                display={draft.tracker_track_thresh.toFixed(2)}
                hint="ByteTrack first-stage activation threshold."
              />
              <Slider
                label="Tracker match threshold"
                value={draft.tracker_match_thresh}
                min={0.3}
                max={0.95}
                step={0.01}
                onChange={(value) => set("tracker_match_thresh", value)}
                display={draft.tracker_match_thresh.toFixed(2)}
              />
              <Slider
                label="Track buffer (frames)"
                value={draft.tracker_buffer}
                min={5}
                max={120}
                onChange={(value) => set("tracker_buffer", value)}
                display={`${draft.tracker_buffer}`}
                hint="How long a lost track is kept alive for re-identification."
              />
              <Toggle
                label="Class-aware tracking"
                hint="Prevent a car track from being reused for a pedestrian."
                checked={draft.tracker_class_aware}
                onChange={(value) => set("tracker_class_aware", value)}
              />
              <Toggle
                label="Save annotated preview video"
                hint="Lets you scrub through boxes and tracking IDs after processing."
                checked={draft.save_annotated_video}
                onChange={(value) => set("save_annotated_video", value)}
              />
            </div>
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <SectionHeader title="Hardware" icon={Cpu} />
            <KeyValue
              items={[
                { label: "Machine", value: device?.machine || hardware?.platform || "—" },
                { label: "Processor", value: hardware?.cpu || "—" },
                { label: "Cores", value: hardware?.cpu_cores ?? "—" },
                { label: "Memory", value: hardware?.memory_gb ? `${hardware.memory_gb} GB` : "—" },
                { label: "Python", value: hardware?.python || "—" },
                { label: "PyTorch", value: device?.torch_version || "not installed" },
                { label: "Apple Silicon", value: device?.arm64 ? "Yes" : "No" },
                { label: "MPS available", value: device?.mps_available ? "Yes" : "No" },
                { label: "Selected device", value: (device?.device || "cpu").toUpperCase() },
              ]}
            />
            {!device?.mps_available ? (
              <div className="mt-2">
                <Note tone="info" icon={Info}>
                  CPU fallback is active. On Apple Silicon, install the arm64 PyTorch wheel (handled
                  by the setup launcher) to enable MPS.
                </Note>
              </div>
            ) : null}
          </Card>

          <Card>
            <SectionHeader title="Evidence & privacy" icon={KeyRound} />
            <div className="space-y-1">
              <Toggle
                label="Store evidence snapshots"
                hint="Annotated JPEG stored with each candidate incident."
                checked={draft.store_snapshots}
                onChange={(value) => set("store_snapshots", value)}
              />
              <Toggle
                label="Store short clips"
                hint="A few seconds around the event, plus a GIF preview for the browser."
                checked={draft.store_clips}
                onChange={(value) => set("store_clips", value)}
              />
              <Toggle
                label="Blur people in evidence"
                hint="Applies a blur over pedestrian detections before evidence is written."
                checked={draft.blur_persons}
                onChange={(value) => set("blur_persons", value)}
              />
            </div>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <Field label="Clip pre-roll (s)">
                <TextInput
                  type="number"
                  min={0}
                  max={15}
                  step={0.5}
                  value={draft.clip_pre_seconds}
                  onChange={(event) => set("clip_pre_seconds", Number(event.target.value))}
                />
              </Field>
              <Field label="Clip post-roll (s)">
                <TextInput
                  type="number"
                  min={0}
                  max={15}
                  step={0.5}
                  value={draft.clip_post_seconds}
                  onChange={(event) => set("clip_post_seconds", Number(event.target.value))}
                />
              </Field>
            </div>
            <div className="mt-3 space-y-2">
              <Field label="Evidence retention (days)">
                <TextInput
                  type="number"
                  min={1}
                  max={3650}
                  value={draft.evidence_retention_days}
                  onChange={(event) => set("evidence_retention_days", Number(event.target.value))}
                />
              </Field>
              <Field label="Source video retention (days)">
                <TextInput
                  type="number"
                  min={1}
                  max={3650}
                  value={draft.media_retention_days}
                  onChange={(event) => set("media_retention_days", Number(event.target.value))}
                />
              </Field>
              <Field label="Duplicate suppression (minutes)" hint="One candidate per zone/track per window.">
                <TextInput
                  type="number"
                  min={0}
                  max={1440}
                  value={draft.incident_cooldown_minutes}
                  onChange={(event) => set("incident_cooldown_minutes", Number(event.target.value))}
                />
              </Field>
            </div>
            <div className="mt-3">
              <Note tone="warning" icon={AlertTriangle}>
                No facial recognition, no cloud upload, no public camera access. The API only accepts
                requests from this Mac.
              </Note>
            </div>
          </Card>

          <Card>
            <SectionHeader title="Storage" icon={HardDrive} />
            <KeyValue
              items={[
                { label: "Total used", value: storage.data?.human || "—" },
                { label: "Evidence", value: formatBytes(storage.data?.bytes.evidence) },
                { label: "Source video", value: formatBytes(storage.data?.bytes.media) },
                { label: "Annotated video", value: formatBytes(storage.data?.bytes.processed) },
                { label: "Model weights", value: formatBytes(storage.data?.bytes.models) },
                { label: "Free disk", value: `${storage.data?.free_gb ?? "—"} GB` },
              ]}
            />
            <div className="mt-3 flex items-end gap-2">
              <div className="flex-1">
                <Field label="Delete evidence older than (days)">
                  <TextInput
                    type="number"
                    min={0}
                    max={3650}
                    value={cleanupDays ?? draft.evidence_retention_days}
                    onChange={(event) => setCleanupDays(Number(event.target.value))}
                  />
                </Field>
              </div>
              <Button size="sm" icon={Database} onClick={() => void cleanup(false)}>
                Clean up
              </Button>
              <Button size="sm" variant="danger" icon={Trash2} onClick={() => void cleanup(true)}>
                Purge footage
              </Button>
            </div>
            <p className="mt-1 text-[0.66rem] text-muted">
              Purging removes snapshots, clips and processed videos; incident metadata is retained for
              the audit trail.
            </p>
          </Card>

          <Card>
            <SectionHeader title="Operator" icon={BadgeCheck} />
            <Field label="Operator name (optional)" hint="Shown on exported PDF reports.">
              <TextInput
                value={draft.operator_name}
                onChange={(event) => set("operator_name", event.target.value)}
                placeholder="e.g. Ward 12 traffic cell"
              />
            </Field>
            <div className="mt-3">
              <Field label="Appearance">
                <Select value={draft.theme} onChange={(event) => set("theme", event.target.value)}>
                  <option value="system">Match system</option>
                  <option value="light">Light</option>
                  <option value="dark">Dark</option>
                </Select>
              </Field>
            </div>
            <div className="mt-3 rounded-md bg-surface-muted/60 px-2.5 py-2 text-[0.68rem] leading-relaxed text-muted">
              CivicWatch AI Studio v1.0 · local-first · Ultralytics YOLO + ByteTrack · SQLite storage
            </div>
          </Card>
        </div>
      </div>

      {!cloud && models.data && (models.data.custom ?? []).length === 0 ? (
        <div className="mt-4">
          <EmptyState
            icon={ShieldCheck}
            title="No specialised models installed"
            description="Pothole and garbage categories stay disabled until you install dedicated weights. This is intentional: a generic detector cannot recognise them."
          />
        </div>
      ) : null}
    </>
  );
}
