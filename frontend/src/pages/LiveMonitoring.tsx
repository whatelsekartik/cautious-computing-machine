import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  Camera as CameraIcon,
  CameraOff,
  CheckCircle2,
  Info,
  Lock,
  Monitor,
  PenTool,
  Play,
  Plus,
  Radio,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Square,
  Trash2,
  Wifi,
} from "lucide-react";
import { PageHeader, LiveChip } from "../components/AppShell";
import {
  Badge,
  Button,
  Card,
  Checkbox,
  Dot,
  EmptyState,
  Field,
  LoadingBlock,
  Modal,
  Note,
  SectionHeader,
  Select,
  Spinner,
  TextInput,
} from "../components/ui";
import { api } from "../lib/api";
import { useAsync, useLiveRefresh } from "../lib/hooks";
import type { Camera, Zone } from "../lib/types";
import { useToast } from "../components/Toast";
import { statusLabel } from "../lib/format";

type CameraForm = {
  name: string;
  source_type: Camera["source_type"];
  source: string;
  username: string;
  password: string;
  device_index: number;
  location: string;
  notes: string;
  authorised: boolean;
};

const EMPTY_FORM: CameraForm = {
  name: "",
  source_type: "rtsp",
  source: "",
  username: "",
  password: "",
  device_index: 0,
  location: "",
  notes: "",
  authorised: false,
};

export function LiveMonitoring() {
  const toast = useToast();
  const navigate = useNavigate();
  const cameras = useAsync(() => api.cameras.list(), [], { intervalMs: 5000 });
  const zones = useAsync(() => api.zones.list(), []);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Camera | null>(null);
  const [form, setForm] = useState<CameraForm>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, { ok: boolean; message: string }>>({});
  const [webcams, setWebcams] = useState<{ index: number; available: boolean; resolution: string | null }[] | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  useLiveRefresh(() => void cameras.refresh(true), ["live.status", "incident.created"]);

  useEffect(() => {
    document.title = "Live monitoring · CivicWatch";
  }, []);

  const items = cameras.data?.items ?? [];
  const liveCount = items.filter((camera) => camera.monitoring).length;

  const zoneCounts = useMemo(() => {
    const counts: Record<string, number> = { all: 0 };
    (zones.data?.items ?? []).forEach((zone: Zone) => {
      if (zone.scope_type === "all") counts.all += 1;
      else if (zone.scope_id) counts[zone.scope_id] = (counts[zone.scope_id] || 0) + 1;
    });
    return counts;
  }, [zones.data]);

  const openCreate = (sourceType: Camera["source_type"] = "rtsp") => {
    setEditing(null);
    setForm({ ...EMPTY_FORM, source_type: sourceType, name: sourceType === "webcam" ? "Built-in camera" : "" });
    setWebcams(null);
    setFormOpen(true);
  };

  const openEdit = (camera: Camera) => {
    setEditing(camera);
    setForm({
      ...EMPTY_FORM,
      name: camera.name,
      source_type: camera.source_type,
      location: camera.location || "",
      notes: camera.notes || "",
      authorised: camera.authorised,
    });
    setWebcams(null);
    setFormOpen(true);
  };

  const save = async () => {
    setSaving(true);
    try {
      if (editing) {
        await api.cameras.update(editing.id, {
          name: form.name,
          location: form.location || undefined,
          notes: form.notes || undefined,
          authorised: form.authorised,
          ...(form.source ? { source: form.source } : {}),
          ...(form.username ? { username: form.username } : {}),
          ...(form.password ? { password: form.password } : {}),
        });
        toast.push("Camera updated", "success");
      } else {
        await api.cameras.create({
          name: form.name,
          source_type: form.source_type,
          source: form.source_type === "webcam" ? undefined : form.source,
          username: form.username || undefined,
          password: form.password || undefined,
          device_index: form.device_index,
          location: form.location || undefined,
          notes: form.notes || undefined,
          authorised: form.authorised,
        });
        toast.push("Camera added — test the connection before monitoring", "success");
      }
      setFormOpen(false);
      await cameras.refresh(true);
    } catch (error) {
      toast.push((error as Error).message, "error");
    } finally {
      setSaving(false);
    }
  };

  const startStop = async (camera: Camera) => {
    setBusyId(camera.id);
    try {
      if (camera.monitoring) {
        await api.cameras.stop(camera.id);
        toast.push(`${camera.name} monitoring stopped`, "info");
      } else {
        await api.cameras.start(camera.id);
        toast.push(`${camera.name} is live — detections appear in the incident queue`, "success");
      }
      await cameras.refresh(true);
    } catch (error) {
      toast.push((error as Error).message, "error");
    } finally {
      setBusyId(null);
    }
  };

  const test = async (camera: Camera) => {
    setBusyId(camera.id);
    try {
      const result = await api.cameras.test(camera.id);
      setTestResult((prev) => ({ ...prev, [camera.id]: result }));
      toast.push(result.message, result.ok ? "success" : "error");
    } catch (error) {
      toast.push((error as Error).message, "error");
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (camera: Camera) => {
    setBusyId(camera.id);
    try {
      await api.cameras.remove(camera.id);
      toast.push("Camera removed", "success");
      await cameras.refresh(true);
    } catch (error) {
      toast.push((error as Error).message, "error");
    } finally {
      setBusyId(null);
    }
  };

  const discover = async () => {
    setDiscovering(true);
    try {
      const result = await api.cameras.discover();
      setWebcams(result.items);
      if (!result.items.some((item) => item.available)) {
        toast.push("No webcam could be opened. Check macOS camera permissions.", "error");
      }
    } catch (error) {
      toast.push((error as Error).message, "error");
    } finally {
      setDiscovering(false);
    }
  };

  return (
    <>
      <PageHeader
        eyebrow="Live monitoring"
        title="Authorised camera feeds"
        description="Connect a webcam or an NVR/RTSP stream you are permitted to monitor. Frames never leave this machine."
        actions={
          <>
            <LiveChip online={liveCount > 0} label={`${liveCount} live`} />
            <Button icon={Plus} size="sm" variant="primary" onClick={() => openCreate()}>
              Add camera
            </Button>
          </>
        }
      />

      <div className="mb-4 grid gap-2 sm:grid-cols-2">
        <Note tone="warning" icon={Lock}>
          Stream credentials are encrypted at rest with a key that never leaves this Mac. URLs
          are shown masked and are never written to logs.
        </Note>
        <Note tone="info" icon={ShieldCheck}>
          Only connect cameras you are authorised to monitor. CivicWatch requires an explicit
          authorisation confirmation and performs no facial recognition.
        </Note>
      </div>

      {cameras.loading && !cameras.data ? (
        <LoadingBlock label="Loading cameras" />
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            icon={CameraOff}
            title="No cameras configured"
            description="Add the built-in webcam for a quick test, or an RTSP stream from an authorised CCTV/NVR."
            action={
              <div className="flex gap-2">
                <Button size="sm" variant="primary" icon={Plus} onClick={() => openCreate("rtsp")}>
                  Add RTSP camera
                </Button>
                <Button size="sm" icon={Monitor} onClick={() => openCreate("webcam")}>
                  Use built-in camera
                </Button>
              </div>
            }
          />
        </Card>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {items.map((camera) => {
            const session = camera.session;
            const status = session?.status || camera.last_status || "idle";
            const error = session?.error || camera.last_error;
            return (
              <Card key={camera.id} padded={false} className="overflow-hidden">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3.5 py-2.5">
                  <div className="flex min-w-0 items-center gap-2">
                    <Dot
                      tone={
                        camera.monitoring ? "positive" : status === "error" ? "danger" : "neutral"
                      }
                      pulse={camera.monitoring}
                    />
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-ink">{camera.name}</p>
                      <p className="truncate font-mono text-[0.65rem] text-muted">{camera.display_source}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Badge tone={camera.source_type === "webcam" ? "blue" : "neutral"}>
                      {camera.source_type.toUpperCase()}
                    </Badge>
                    {!camera.authorised ? <Badge tone="amber">authorisation needed</Badge> : null}
                    <Badge tone={camera.monitoring ? "positive" : status === "error" ? "danger" : "neutral"}>
                      {statusLabel(status)}
                    </Badge>
                  </div>
                </div>

                <div className="relative bg-[#0f1416]">
                  {camera.monitoring ? (
                    <img
                      key={`${camera.id}-${refreshKey}`}
                      src={api.cameras.streamUrl(camera.id)}
                      alt={`Live annotated feed from ${camera.name}`}
                      className="aspect-video w-full object-contain"
                    />
                  ) : (
                    <img
                      key={`${camera.id}-${refreshKey}`}
                      src={api.cameras.frameUrl(camera.id)}
                      alt={`Snapshot from ${camera.name}`}
                      className="aspect-video w-full object-contain"
                      onError={(event) => {
                        (event.target as HTMLImageElement).style.visibility = "hidden";
                      }}
                    />
                  )}
                  {!camera.monitoring ? (
                    <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 text-white/60">
                      <CameraIcon size={18} />
                      <span className="text-[0.68rem]">Not monitoring — snapshot only</span>
                    </div>
                  ) : null}
                </div>

                <div className="space-y-2.5 p-3.5">
                  {error ? (
                    <Note tone="danger" icon={AlertTriangle}>
                      {error}
                    </Note>
                  ) : null}
                  {testResult[camera.id] ? (
                    <Note tone={testResult[camera.id].ok ? "success" : "warning"} icon={testResult[camera.id].ok ? CheckCircle2 : AlertTriangle}>
                      {testResult[camera.id].message}
                    </Note>
                  ) : null}

                  <div className="grid grid-cols-2 gap-2 text-[0.7rem] sm:grid-cols-4">
                    <Stat label="FPS" value={session?.fps?.toFixed(1) ?? "—"} />
                    <Stat label="Frames" value={session?.frames ?? 0} />
                    <Stat label="Detections" value={session?.detections ?? 0} />
                    <Stat label="Candidates" value={session?.incidents ?? 0} />
                  </div>

                  <div className="flex flex-wrap items-center gap-1.5">
                    <Button
                      size="sm"
                      variant={camera.monitoring ? "danger" : "primary"}
                      icon={camera.monitoring ? Square : Play}
                      loading={busyId === camera.id}
                      onClick={() => void startStop(camera)}
                    >
                      {camera.monitoring ? "Stop monitoring" : "Start monitoring"}
                    </Button>
                    <Button size="sm" icon={Wifi} onClick={() => void test(camera)} disabled={busyId === camera.id}>
                      Test
                    </Button>
                    <Button
                      size="sm"
                      icon={PenTool}
                      onClick={() => navigate(`/zones?camera=${camera.id}`)}
                    >
                      Zones ({zoneCounts[camera.id] || zoneCounts.all || 0})
                    </Button>
                    <Button size="sm" variant="ghost" icon={Settings2} onClick={() => openEdit(camera)}>
                      Edit
                    </Button>
                    <Button size="sm" variant="ghost" icon={RefreshCw} onClick={() => setRefreshKey((v) => v + 1)}>
                      Snapshot
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={Trash2}
                      onClick={() => void remove(camera)}
                      disabled={busyId === camera.id}
                    >
                      Delete
                    </Button>
                  </div>
                  {!camera.authorised ? (
                    <Note tone="warning" icon={Info}>
                      Confirm authorisation in Edit before starting monitoring.
                    </Note>
                  ) : null}
                </div>
              </Card>
            );
          })}
        </div>
      )}

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <Card>
          <SectionHeader title="How monitoring works" icon={Radio} />
          <ul className="space-y-1.5 text-[0.72rem] leading-relaxed text-muted">
            <li>• Frames are sampled locally and run through the same detector and tracker as video files.</li>
            <li>• Zones drawn for this camera (or global zones) are evaluated every sampled frame.</li>
            <li>• Candidates are stored with a snapshot and short clip for human review.</li>
          </ul>
        </Card>
        <Card>
          <SectionHeader title="RTSP support" icon={CameraIcon} />
          <p className="text-[0.72rem] leading-relaxed text-muted">
            Use <span className="font-mono">rtsp://host:554/stream</span> and enter credentials
            separately. CivicWatch builds the authenticated URL in the backend and masks it
            everywhere in the interface. H.264 streams are the most reliable.
          </p>
        </Card>
        <Card>
          <SectionHeader title="Privacy boundaries" icon={Lock} />
          <p className="text-[0.72rem] leading-relaxed text-muted">
            No face recognition, no cloud inference, no public exposure of the feed. The API only
            accepts requests from this machine, and evidence expires per the retention policy.
          </p>
        </Card>
      </div>

      <Modal
        open={formOpen}
        onClose={() => setFormOpen(false)}
        title={editing ? "Edit camera" : "Add camera"}
        description={
          editing
            ? "Credentials are never displayed. Leave the URL blank to keep the stored stream."
            : "Connect a webcam or an authorised RTSP/HTTP stream."
        }
        footer={
          <>
            <Button size="sm" variant="ghost" onClick={() => setFormOpen(false)}>
              Cancel
            </Button>
            <Button
              size="sm"
              variant="primary"
              onClick={() => void save()}
              loading={saving}
              disabled={!form.name.trim() || !form.authorised || (form.source_type !== "webcam" && !form.source && !editing)}
            >
              {editing ? "Save changes" : "Add camera"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Camera name">
            <TextInput
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="e.g. Ward 12 — Market Road junction"
            />
          </Field>
          <Field label="Source type">
            <Select
              value={form.source_type}
              onChange={(event) =>
                setForm({ ...form, source_type: event.target.value as Camera["source_type"] })
              }
              disabled={Boolean(editing)}
            >
              <option value="rtsp">RTSP camera / NVR</option>
              <option value="http">HTTP MJPEG stream</option>
              <option value="webcam">Built-in webcam</option>
            </Select>
          </Field>

          {form.source_type === "webcam" ? (
            <>
              <Field label="Camera index" hint="0 is usually the built-in camera.">
                <TextInput
                  type="number"
                  min={0}
                  max={16}
                  value={form.device_index}
                  onChange={(event) => setForm({ ...form, device_index: Number(event.target.value) })}
                />
              </Field>
              <div className="flex items-center gap-2">
                <Button size="sm" onClick={() => void discover()} loading={discovering} icon={Monitor}>
                  Detect cameras
                </Button>
                {webcams ? (
                  <span className="text-[0.68rem] text-muted">
                    {webcams.filter((item) => item.available).length} available
                  </span>
                ) : null}
              </div>
              {webcams ? (
                <div className="space-y-1">
                  {webcams.map((item) => (
                    <label
                      key={item.index}
                      className="flex cursor-pointer items-center gap-2 rounded-md border border-line px-2 py-1 text-[0.72rem]"
                    >
                      <input
                        type="radio"
                        name="webcam"
                        disabled={!item.available}
                        checked={form.device_index === item.index}
                        onChange={() => setForm({ ...form, device_index: item.index })}
                        className="accent-teal"
                      />
                      <span className={item.available ? "text-ink" : "text-muted"}>
                        Camera {item.index} {item.available ? `(${item.resolution})` : "— not available"}
                      </span>
                    </label>
                  ))}
                </div>
              ) : null}
            </>
          ) : (
            <>
              <Field
                label="Stream URL"
                hint={editing ? "Leave blank to keep the existing URL." : "Do not include credentials here."}
              >
                <TextInput
                  mono
                  value={form.source}
                  onChange={(event) => setForm({ ...form, source: event.target.value })}
                  placeholder="rtsp://192.168.1.64:554/Streaming/Channels/101"
                />
              </Field>
              <div className="grid grid-cols-2 gap-2">
                <Field label="Username (optional)">
                  <TextInput
                    value={form.username}
                    autoComplete="off"
                    onChange={(event) => setForm({ ...form, username: event.target.value })}
                  />
                </Field>
                <Field label="Password (optional)">
                  <TextInput
                    type="password"
                    value={form.password}
                    autoComplete="new-password"
                    onChange={(event) => setForm({ ...form, password: event.target.value })}
                  />
                </Field>
              </div>
            </>
          )}

          <div className="grid grid-cols-2 gap-2">
            <Field label="Location (optional)">
              <TextInput
                value={form.location}
                onChange={(event) => setForm({ ...form, location: event.target.value })}
                placeholder="Junction / ward"
              />
            </Field>
            <Field label="Notes (optional)">
              <TextInput
                value={form.notes}
                onChange={(event) => setForm({ ...form, notes: event.target.value })}
                placeholder="Owner, permit reference…"
              />
            </Field>
          </div>

          <div className="rounded-lg border border-line bg-surface-muted/50 p-2.5">
            <Checkbox
              checked={form.authorised}
              onChange={(value) => setForm({ ...form, authorised: value })}
              label="I am authorised to monitor this camera"
            />
            <p className="mt-1 pl-5 text-[0.68rem] leading-snug text-muted">
              Required. CivicWatch refuses to start monitoring without this confirmation.
            </p>
          </div>
        </div>
      </Modal>
    </>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md bg-surface-muted/60 px-2 py-1">
      <p className="text-[0.6rem] uppercase tracking-[0.08em] text-muted">{label}</p>
      <p className="font-mono text-xs text-ink">{value}</p>
    </div>
  );
}

export function SpinnerInline() {
  return <Spinner label="Connecting" />;
}
