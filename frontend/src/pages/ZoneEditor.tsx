import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  Camera as CameraIcon,
  Check,
  Eraser,
  Film,
  Layers,
  MousePointerClick,
  PenTool,
  Plus,
  RefreshCw,
  Save,
  Trash2,
  Undo2,
} from "lucide-react";
import { PageHeader } from "../components/AppShell";
import {
  Badge,
  Button,
  Card,
  Checkbox,
  EmptyState,
  Field,
  LoadingBlock,
  Note,
  SectionHeader,
  Select,
  Slider,
  TextInput,
  Toggle,
} from "../components/ui";
import { api } from "../lib/api";
import { useAsync, useElementSize } from "../lib/hooks";
import type { Zone, ZoneKind } from "../lib/types";
import { useToast } from "../components/Toast";

const CLASS_OPTIONS = [
  { value: "car", label: "Car" },
  { value: "motorcycle", label: "Motorcycle" },
  { value: "bus", label: "Bus" },
  { value: "truck", label: "Truck" },
  { value: "bicycle", label: "Bicycle" },
  { value: "person", label: "Pedestrian" },
];

const KIND_COLORS: Record<string, string> = {
  no_parking: "var(--amber)",
  footpath: "var(--teal)",
  road: "var(--blue)",
  restricted: "var(--danger)",
};

type Point = [number, number];

interface DraftForm {
  name: string;
  kind: Zone["kind"];
  dwell: number;
  classes: string[];
  doubleParking: boolean;
  noStopping: boolean;
  severity: "low" | "medium" | "high";
  scopeAll: boolean;
  enabled: boolean;
}

const DEFAULT_FORM: DraftForm = {
  name: "",
  kind: "no_parking",
  dwell: 20,
  classes: ["car", "motorcycle", "bus", "truck"],
  doubleParking: true,
  noStopping: false,
  severity: "medium",
  scopeAll: true,
  enabled: true,
};

export function ZoneEditor() {
  const toast = useToast();
  const [params] = useSearchParams();
  const media = useAsync(() => api.media.list(), []);
  const cameras = useAsync(() => api.cameras.list(), []);
  const zones = useAsync(() => api.zones.list(), []);
  const kinds = useAsync(() => api.zones.kinds(), []);

  const [source, setSource] = useState<string>("");
  const [previewT, setPreviewT] = useState(0);
  const [frameTick, setFrameTick] = useState(0);
  const [frameError, setFrameError] = useState(false);
  const [draft, setDraft] = useState<Point[] | null>(null);
  const [selectedZoneId, setSelectedZoneId] = useState<string | null>(null);
  const [localPolygon, setLocalPolygon] = useState<Point[] | null>(null);
  const [form, setForm] = useState<DraftForm>(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);
  const [cursor, setCursor] = useState<Point | null>(null);
  const [dragging, setDragging] = useState<{ index: number } | null>(null);
  const { ref: canvasRef, size } = useElementSize<HTMLDivElement>();
  const mouseDownRef = useRef(false);

  // Default source: query params win, then first video, then first camera.
  useEffect(() => {
    if (source) return;
    const mediaParam = params.get("media");
    const cameraParam = params.get("camera");
    if (mediaParam) return setSource(`media:${mediaParam}`);
    if (cameraParam) return setSource(`camera:${cameraParam}`);
    const firstMedia = media.data?.items?.[0];
    const firstCamera = cameras.data?.items?.[0];
    if (firstMedia) setSource(`media:${firstMedia.id}`);
    else if (firstCamera) setSource(`camera:${firstCamera.id}`);
  }, [params, media.data, cameras.data, source]);

  useEffect(() => {
    document.title = "Zone editor · CivicWatch";
  }, []);

  const [sourceKind, sourceId] = source.split(":");
  const mediaItem = media.data?.items.find((item) => item.id === sourceId);
  const cameraItem = cameras.data?.items.find((item) => item.id === sourceId);

  const frameUrl = useMemo(() => {
    if (!sourceId) return "";
    if (sourceKind === "media")
      return `${api.media.frameUrl(sourceId, previewT)}&_=${frameTick}`;
    return api.cameras.frameUrl(sourceId, false);
  }, [sourceKind, sourceId, previewT, frameTick]);

  const applicableZones = useMemo(() => {
    const all = zones.data?.items ?? [];
    return all.filter(
      (zone) =>
        zone.scope_type === "all" ||
        (zone.scope_type === sourceKind && zone.scope_id === sourceId),
    );
  }, [zones.data, sourceKind, sourceId]);

  const selectedZone = applicableZones.find((zone) => zone.id === selectedZoneId) || null;
  const kindMeta: ZoneKind | undefined = kinds.data?.items.find((item) => item.value === form.kind);

  const resetForm = useCallback(() => {
    setSelectedZoneId(null);
    setLocalPolygon(null);
    setDraft(null);
    setForm(DEFAULT_FORM);
  }, []);

  const selectZone = (zone: Zone) => {
    setSelectedZoneId(zone.id);
    setDraft(null);
    setLocalPolygon(zone.polygon.map(([x, y]) => [x, y] as Point));
    setForm({
      name: zone.name,
      kind: zone.kind,
      dwell: zone.rules.dwell_seconds ?? kindDefaults(zone.kind).dwell_seconds,
      classes: zone.rules.classes?.length ? zone.rules.classes : kindDefaults(zone.kind).classes,
      doubleParking: Boolean(zone.rules.double_parking),
      noStopping: Boolean(zone.rules.no_stopping),
      severity: zone.rules.severity || "medium",
      scopeAll: zone.scope_type === "all",
      enabled: zone.enabled,
    });
  };

  const startDrawing = () => {
    setSelectedZoneId(null);
    setLocalPolygon(null);
    setDraft([]);
    setForm({
      ...DEFAULT_FORM,
      name: "",
      classes: [...(kindDefaults(form.kind).classes as string[])],
    });
  };

  const polygon: Point[] | null = draft ?? localPolygon;

  const finishPolygon = () => {
    if (!draft || draft.length < 3) {
      toast.push("Add at least three corners to close a zone", "error");
      return;
    }
    setLocalPolygon(draft);
    setDraft(null);
    if (!form.name) {
      const label = kinds.data?.items.find((item) => item.value === form.kind)?.label || "Zone";
      setForm((prev) => ({ ...prev, name: `${label} ${applicableZones.length + 1}` }));
    }
  };

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setDraft(null);
        setCursor(null);
      }
      if ((event.key === "Enter" || event.key === "Return") && draft) {
        event.preventDefault();
        finishPolygon();
      }
      if (event.key === "Backspace" && draft && draft.length > 0) {
        event.preventDefault();
        setDraft((prev) => (prev ? prev.slice(0, -1) : prev));
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, form.name]);

  const toNormalized = (event: React.MouseEvent<SVGSVGElement>): Point => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    const y = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
    return [Number(x.toFixed(4)), Number(y.toFixed(4))];
  };

  const handleCanvasClick = (event: React.MouseEvent<SVGSVGElement>) => {
    if (!draft) return;
    const point = toNormalized(event);
    setDraft((prev) => [...(prev || []), point]);
  };

  const handleDoubleClick = () => {
    if (draft && draft.length >= 3) finishPolygon();
  };

  const startDrag = (index: number) => (event: React.MouseEvent) => {
    event.stopPropagation();
    setDragging({ index });
    mouseDownRef.current = true;
  };

  const handleMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const point = toNormalized(event);
    if (draft) setCursor(point);
    if (!dragging) return;
    setLocalPolygon((prev) => {
      if (!prev) return prev;
      const next = [...prev];
      next[dragging.index] = point;
      return next;
    });
  };

  useEffect(() => {
    const stop = () => {
      setDragging(null);
      mouseDownRef.current = false;
    };
    window.addEventListener("mouseup", stop);
    return () => window.removeEventListener("mouseup", stop);
  }, []);

  const saveZone = async () => {
    if (!polygon || polygon.length < 3) {
      toast.push("Draw a polygon with at least three corners first", "error");
      return;
    }
    if (!form.name.trim()) {
      toast.push("Give the zone a name", "error");
      return;
    }
    const rules = {
      dwell_seconds: form.dwell,
      classes: form.classes,
      severity: form.severity,
      double_parking: form.kind === "no_parking" ? form.doubleParking : false,
      no_stopping: form.kind === "road" ? form.noStopping : false,
    };
    setSaving(true);
    try {
      if (selectedZone) {
        await api.zones.update(selectedZone.id, {
          name: form.name.trim(),
          kind: form.kind,
          polygon,
          rules,
          enabled: form.enabled,
          scope_type: form.scopeAll ? "all" : (sourceKind as "media" | "camera"),
          scope_id: form.scopeAll ? null : sourceId,
        });
        toast.push("Zone updated", "success");
      } else {
        const created = await api.zones.create({
          name: form.name.trim(),
          kind: form.kind,
          polygon,
          rules,
          scope_type: form.scopeAll ? "all" : (sourceKind as "media" | "camera"),
          scope_id: form.scopeAll ? null : sourceId,
          enabled: form.enabled,
        });
        setSelectedZoneId(created.id);
        toast.push("Zone saved — it applies to the next processing run", "success");
      }
      setDraft(null);
      await zones.refresh(true);
    } catch (error) {
      toast.push((error as Error).message, "error");
    } finally {
      setSaving(false);
    }
  };

  const deleteZone = async () => {
    if (!selectedZone) return;
    setSaving(true);
    try {
      await api.zones.remove(selectedZone.id);
      toast.push("Zone deleted", "success");
      resetForm();
      await zones.refresh(true);
    } catch (error) {
      toast.push((error as Error).message, "error");
    } finally {
      setSaving(false);
    }
  };

  const toggleEnabled = async (zone: Zone) => {
    try {
      await api.zones.update(zone.id, { enabled: !zone.enabled });
      await zones.refresh(true);
    } catch (error) {
      toast.push((error as Error).message, "error");
    }
  };

  const width = size.width || 640;
  const height = size.height || 360;

  return (
    <>
      <PageHeader
        eyebrow="Zone editor"
        title="Draw restricted areas"
        description="Load a frame from a video or camera, click to place corners, then assign the rules for that area. Everything is visual — no coordinates to type."
        actions={
          <Button size="sm" variant="primary" icon={PenTool} onClick={startDrawing}>
            New zone
          </Button>
        }
      />

      <div className="grid gap-4 xl:grid-cols-[1.6fr_1fr]">
        <Card padded={false} className="overflow-hidden">
          <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface-muted/40 px-3 py-2">
            <Select
              value={source}
              onChange={(event) => {
                setSource(event.target.value);
                resetForm();
                setPreviewT(0);
                setFrameError(false);
              }}
              className="!w-auto min-w-[220px]"
              aria-label="Zone source"
            >
              {(media.data?.items ?? []).map((item) => (
                <option key={`media:${item.id}`} value={`media:${item.id}`}>
                  Video · {item.original_name}
                </option>
              ))}
              {(cameras.data?.items ?? []).map((item) => (
                <option key={`camera:${item.id}`} value={`camera:${item.id}`}>
                  Camera · {item.name}
                </option>
              ))}
              {!media.data?.items?.length && !cameras.data?.items?.length ? (
                <option value="">No video or camera sources yet</option>
              ) : null}
            </Select>
            {sourceKind === "media" && mediaItem ? (
              <div className="flex flex-1 items-center gap-2">
                <Film size={13} className="text-muted" />
                <input
                  type="range"
                  min={0}
                  max={Math.max(1, Math.floor(mediaItem.duration_s))}
                  value={previewT}
                  onChange={(event) => setPreviewT(Number(event.target.value))}
                  className="h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-line accent-teal"
                  aria-label="Frame position"
                />
                <span className="w-12 text-right font-mono text-[0.66rem] text-muted">{previewT}s</span>
              </div>
            ) : (
              <span className="inline-flex items-center gap-1.5 text-[0.68rem] text-muted">
                <CameraIcon size={12} />
                {cameraItem ? cameraItem.display_source : "No camera selected"}
              </span>
            )}
            <Button
              size="sm"
              variant="ghost"
              icon={RefreshCw}
              onClick={() => {
                setFrameTick((value) => value + 1);
                setFrameError(false);
              }}
            >
              Reload frame
            </Button>
          </div>

          <div
            ref={canvasRef}
            className="relative select-none bg-[#0f1416]"
            style={{ minHeight: 220 }}
          >
            {frameUrl && !frameError ? (
              <img
                key={frameUrl}
                src={frameUrl}
                alt="Zone editor frame"
                draggable={false}
                onError={() => setFrameError(true)}
                className="block w-full"
              />
            ) : (
              <div className="flex h-[320px] flex-col items-center justify-center gap-2 px-6 text-center text-xs text-white/60">
                <AlertTriangle size={16} />
                {frameError
                  ? "That source could not provide a frame. Check the camera connection or choose another video."
                  : "Choose a video or camera to load a frame."}
                {cameraItem ? (
                  <Button size="sm" onClick={() => setFrameTick((value) => value + 1)}>
                    Retry
                  </Button>
                ) : null}
              </div>
            )}

            <svg
              className="absolute inset-0 h-full w-full"
              viewBox={`0 0 ${width} ${height}`}
              preserveAspectRatio="none"
              onClick={handleCanvasClick}
              onDoubleClick={handleDoubleClick}
              onMouseMove={handleMove}
              onMouseLeave={() => setCursor(null)}
              style={{ cursor: draft ? "crosshair" : "default" }}
            >
              {applicableZones.map((zone) => {
                const points = zone.id === selectedZoneId && localPolygon ? localPolygon : zone.polygon;
                const color = KIND_COLORS[zone.kind] || "var(--teal)";
                return (
                  <g key={zone.id} opacity={zone.enabled ? 1 : 0.4}>
                    <polygon
                      points={points.map(([x, y]) => `${x * width},${y * height}`).join(" ")}
                      fill={color}
                      fillOpacity={zone.id === selectedZoneId ? 0.24 : 0.13}
                      stroke={color}
                      strokeWidth={zone.id === selectedZoneId ? 2.4 : 1.6}
                      strokeDasharray={zone.enabled ? undefined : "5 4"}
                      onClick={(event) => {
                        if (draft) return;
                        event.stopPropagation();
                        selectZone(zone);
                      }}
                      style={{ cursor: draft ? "crosshair" : "pointer" }}
                    />
                    <text
                      x={points.reduce((sum, point) => sum + point[0], 0) / points.length * width}
                      y={points.reduce((sum, point) => sum + point[1], 0) / points.length * height}
                      textAnchor="middle"
                      fontSize={11}
                      fill="#ffffff"
                      style={{ paintOrder: "stroke", stroke: "rgba(15,20,22,0.65)", strokeWidth: 3 }}
                      pointerEvents="none"
                    >
                      {zone.name}
                    </text>
                    {zone.id === selectedZoneId && localPolygon
                      ? localPolygon.map(([x, y], index) => (
                          <circle
                            key={index}
                            cx={x * width}
                            cy={y * height}
                            r={5.5}
                            fill="#ffffff"
                            stroke={color}
                            strokeWidth={2}
                            style={{ cursor: "grab" }}
                            onMouseDown={startDrag(index)}
                          />
                        ))
                      : null}
                  </g>
                );
              })}

              {draft && draft.length > 0 ? (
                <g>
                  <polyline
                    points={[...draft, ...(cursor ? [cursor] : [])]
                      .map(([x, y]) => `${x * width},${y * height}`)
                      .join(" ")}
                    fill="none"
                    stroke={KIND_COLORS[form.kind]}
                    strokeWidth={2}
                    strokeDasharray="6 4"
                  />
                  {draft.map(([x, y], index) => (
                    <circle
                      key={index}
                      cx={x * width}
                      cy={y * height}
                      r={4.5}
                      fill={KIND_COLORS[form.kind]}
                      stroke="#fff"
                      strokeWidth={1.5}
                      style={{ cursor: "grab" }}
                      onMouseDown={startDrag(index)}
                    />
                  ))}
                </g>
              ) : null}
            </svg>

            {draft ? (
              <div className="pointer-events-auto absolute bottom-2 left-2 right-2 flex flex-wrap items-center gap-2 rounded-lg bg-[rgba(20,26,28,0.82)] px-2.5 py-2 text-[0.68rem] text-white">
                <MousePointerClick size={12} />
                <span>
                  Click to add a corner ({draft.length} placed). Double-click or press{" "}
                  <span className="rounded border border-white/30 px-1">Enter</span> to close.
                </span>
                <div className="ml-auto flex gap-1.5">
                  <button
                    type="button"
                    onClick={() => setDraft((prev) => (prev ? prev.slice(0, -1) : prev))}
                    className="rounded border border-white/25 px-2 py-0.5 hover:bg-white/10"
                  >
                    Undo point
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setDraft(null);
                      setCursor(null);
                    }}
                    className="rounded border border-white/25 px-2 py-0.5 hover:bg-white/10"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={finishPolygon}
                    className="rounded bg-white/90 px-2 py-0.5 font-medium text-[#141a1c] hover:bg-white"
                  >
                    Close shape
                  </button>
                </div>
              </div>
            ) : null}
          </div>

          <div className="flex flex-wrap items-center gap-2 border-t border-line px-3 py-2 text-[0.66rem] text-muted">
            <Layers size={12} />
            <span>{applicableZones.length} zone(s) apply to this source</span>
            <span className="ml-auto inline-flex items-center gap-1">
              <span className="h-2 w-2 rounded-full" style={{ background: "var(--amber)" }} /> no parking
              <span className="ml-2 h-2 w-2 rounded-full" style={{ background: "var(--teal)" }} /> footpath
              <span className="ml-2 h-2 w-2 rounded-full" style={{ background: "var(--blue)" }} /> road
              <span className="ml-2 h-2 w-2 rounded-full" style={{ background: "var(--danger)" }} /> restricted
            </span>
          </div>
        </Card>

        <div className="space-y-4">
          <Card>
            <SectionHeader
              title={selectedZone ? "Edit zone" : polygon ? "New zone" : "Zone properties"}
              subtitle={
                selectedZone
                  ? "Drag the white handles on the canvas to reshape."
                  : polygon
                    ? "Name the area and assign its rules."
                    : "Select a zone on the canvas or draw a new one."
              }
              icon={PenTool}
              action={
                polygon ? (
                  <Button size="sm" variant="ghost" icon={Eraser} onClick={resetForm}>
                    Clear
                  </Button>
                ) : null
              }
            />

            {!polygon && !selectedZone ? (
              <div className="space-y-3">
                <EmptyState
                  icon={PenTool}
                  title="Draw your first zone"
                  description="Add a polygon around a no-parking stretch, a footpath or a restricted area, then set how long an object may remain."
                  action={
                    <Button size="sm" variant="primary" icon={Plus} onClick={startDrawing}>
                      Start drawing
                    </Button>
                  }
                />
                {applicableZones.length > 0 ? (
                  <div className="space-y-1">
                    {applicableZones.map((zone) => (
                      <button
                        key={zone.id}
                        type="button"
                        onClick={() => selectZone(zone)}
                        className="flex w-full items-center gap-2 rounded-lg border border-line px-2.5 py-2 text-left hover:bg-surface-muted"
                      >
                        <span
                          className="h-2.5 w-2.5 shrink-0 rounded-full"
                          style={{ background: KIND_COLORS[zone.kind] }}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-xs font-medium text-ink">{zone.name}</span>
                          <span className="block text-[0.64rem] text-muted">
                            {zone.kind_label} · {zone.rules.dwell_seconds}s ·{" "}
                            {zone.scope_type === "all" ? "all sources" : "this source"}
                          </span>
                        </span>
                        {zone.enabled ? <Badge tone="teal">on</Badge> : <Badge>off</Badge>}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : (
              <div className="space-y-3">
                <Field label="Zone name">
                  <TextInput
                    value={form.name}
                    onChange={(event) => setForm({ ...form, name: event.target.value })}
                    placeholder="e.g. Market Road — no parking"
                  />
                </Field>
                <Field label="Zone type" hint={kindMeta?.description}>
                  <Select
                    value={form.kind}
                    onChange={(event) => {
                      const kind = event.target.value as Zone["kind"];
                      const defaults = kindDefaults(kind);
                      setForm({
                        ...form,
                        kind,
                        dwell: defaults.dwell_seconds,
                        classes: [...defaults.classes],
                        doubleParking: Boolean(defaults.double_parking),
                        noStopping: Boolean(defaults.no_stopping),
                      });
                    }}
                  >
                    {(kinds.data?.items ?? []).map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </Select>
                </Field>

                <Slider
                  label="Dwell limit"
                  value={form.dwell}
                  min={1}
                  max={120}
                  onChange={(value) => setForm({ ...form, dwell: value })}
                  display={`${form.dwell}s`}
                  hint="Objects must stay inside this long before a candidate is raised."
                />

                <div>
                  <p className="field-label">Classes watched in this zone</p>
                  <div className="grid grid-cols-2 gap-1">
                    {CLASS_OPTIONS.map((option) => (
                      <Checkbox
                        key={option.value}
                        label={option.label}
                        checked={form.classes.includes(option.value)}
                        onChange={(checked) =>
                          setForm({
                            ...form,
                            classes: checked
                              ? [...form.classes, option.value]
                              : form.classes.filter((value) => value !== option.value),
                          })
                        }
                      />
                    ))}
                  </div>
                  {form.classes.length === 0 ? (
                    <p className="mt-1 text-[0.66rem] text-danger">Select at least one class.</p>
                  ) : null}
                </div>

                <div className="space-y-1">
                  {form.kind === "no_parking" ? (
                    <Toggle
                      label="Flag double parking"
                      hint="Also requires a second stationary vehicle alongside."
                      checked={form.doubleParking}
                      onChange={(value) => setForm({ ...form, doubleParking: value })}
                    />
                  ) : null}
                  {form.kind === "road" ? (
                    <Toggle
                      label="No stopping rule"
                      hint="Road boundaries are visual markers unless this is enabled."
                      checked={form.noStopping}
                      onChange={(value) => setForm({ ...form, noStopping: value })}
                    />
                  ) : null}
                  <Toggle
                    label="Zone active"
                    checked={form.enabled}
                    onChange={(value) => setForm({ ...form, enabled: value })}
                  />
                </div>

                <Field label="Applies to">
                  <Select
                    value={form.scopeAll ? "all" : "this"}
                    onChange={(event) => setForm({ ...form, scopeAll: event.target.value === "all" })}
                  >
                    <option value="all">All videos and cameras (same framing)</option>
                    <option value="this">
                      Only this {sourceKind === "camera" ? "camera" : "video"}
                      {mediaItem ? ` (${mediaItem.original_name})` : cameraItem ? ` (${cameraItem.name})` : ""}
                    </option>
                  </Select>
                </Field>

                {polygon ? (
                  <p className="text-[0.66rem] text-muted">
                    Polygon: {polygon.length} corners ·{" "}
                    {polygon.map(([x, y]) => `${(x * 100).toFixed(1)},${(y * 100).toFixed(1)}`).join(" · ")}
                  </p>
                ) : null}

                <div className="flex gap-2 pt-1">
                  <Button
                    variant="primary"
                    icon={Save}
                    loading={saving}
                    onClick={() => void saveZone()}
                    disabled={!polygon || polygon.length < 3 || !form.name.trim()}
                    className="flex-1"
                  >
                    {selectedZone ? "Save changes" : "Save zone"}
                  </Button>
                  {selectedZone ? (
                    <>
                      <Button
                        icon={Check}
                        onClick={() => void toggleEnabled(selectedZone)}
                        title="Toggle active"
                      >
                        {selectedZone.enabled ? "Disable" : "Enable"}
                      </Button>
                      <Button variant="ghost" icon={Trash2} onClick={() => void deleteZone()}>
                        Delete
                      </Button>
                    </>
                  ) : (
                    <Button variant="ghost" icon={Undo2} onClick={resetForm}>
                      Reset
                    </Button>
                  )}
                </div>
              </div>
            )}
          </Card>

          <Card>
            <SectionHeader title="Rules at a glance" icon={Layers} />
            <div className="space-y-2 text-[0.7rem] leading-relaxed text-muted">
              <p>
                <span className="font-medium text-ink">No parking</span> — vehicles stationary in the
                polygon past the dwell limit. If two vehicles stand side by side, the candidate is
                labelled double parking.
              </p>
              <p>
                <span className="font-medium text-ink">Footpath</span> — vehicles left on a pedestrian
                area. Pedestrians walking through are never flagged.
              </p>
              <p>
                <span className="font-medium text-ink">Road boundary</span> — a visual marker for the
                carriageway. Enable “no stopping” to generate candidates.
              </p>
              <p>
                <span className="font-medium text-ink">Restricted</span> — any selected class inside
                the area for the dwell limit (useful for pedestrian-only plazas).
              </p>
            </div>
            <div className="mt-2">
              <Note tone="warning" icon={AlertTriangle}>
                Zones are matched against the source framing. If a camera moves, redraw its zones.
              </Note>
            </div>
          </Card>
        </div>
      </div>

      {zones.loading && !zones.data ? <LoadingBlock label="Loading zones" /> : null}
    </>
  );
}

function kindDefaults(kind: Zone["kind"]) {
  const map: Record<string, { dwell_seconds: number; classes: string[]; double_parking?: boolean; no_stopping?: boolean }> = {
    no_parking: { dwell_seconds: 20, classes: ["car", "motorcycle", "bus", "truck"], double_parking: true },
    footpath: { dwell_seconds: 5, classes: ["car", "motorcycle", "bus", "truck", "bicycle"] },
    road: { dwell_seconds: 10, classes: [], no_stopping: false },
    restricted: { dwell_seconds: 2, classes: ["person", "car", "motorcycle", "truck"] },
  };
  return map[kind] || map.no_parking;
}
