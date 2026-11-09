import { useEffect, useMemo, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  Download,
  FileSpreadsheet,
  Filter,
  ImageOff,
  Search,
  ShieldCheck,
} from "lucide-react";
import { PageHeader } from "../components/AppShell";
import { IncidentDrawer } from "../components/IncidentDrawer";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  LoadingBlock,
  Note,
  Select,
  SeverityBadge,
  StatusBadge,
  TextInput,
} from "../components/ui";
import { api } from "../lib/api";
import { useAsync, useLiveRefresh } from "../lib/hooks";
import type { Incident } from "../lib/types";
import { categoryLabel, CLASS_LABELS, formatDateTime, formatRelative } from "../lib/format";

const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: "", label: "All" },
  { value: "detected", label: "Detected" },
  { value: "pending_review", label: "Pending review" },
  { value: "verified", label: "Verified" },
  { value: "forwarded", label: "Forwarded" },
  { value: "resolved", label: "Resolved" },
  { value: "dismissed", label: "Dismissed" },
];

const PAGE_SIZE = 24;

export function Incidents() {
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState("");
  const [severity, setSeverity] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("captured_at_desc");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(0);
    }, 320);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const query = useMemo(
    () => ({
      status: status || undefined,
      category: category || undefined,
      severity: severity || undefined,
      search: search || undefined,
      sort,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    }),
    [status, category, severity, search, sort, page],
  );

  const listing = useAsync(() => api.incidents.list(query), [query], { intervalMs: 15000 });
  const categories = useAsync(() => api.incidents.categories(), []);
  useLiveRefresh(() => void listing.refresh(true), ["incident.created"]);

  useEffect(() => {
    document.title = "Incidents · CivicWatch";
  }, []);

  const total = listing.data?.total ?? 0;
  const items = listing.data?.items ?? [];
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const toggleSelected = (id: string) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]));
  };

  const exportParams = selected.length
    ? { ids: selected.join(",") }
    : { status: status || undefined };

  return (
    <>
      <PageHeader
        eyebrow="Human in the loop"
        title="Incident review"
        description="Every candidate incident carries an evidence snapshot, tracking ID and timestamp. Confirm, dismiss or forward after review."
        actions={
          <>
            <a
              className="btn btn-secondary btn-sm"
              href={api.incidents.exportCsvUrl(exportParams)}
              download
            >
              <FileSpreadsheet size={13} /> CSV
            </a>
            <a
              className="btn btn-primary btn-sm"
              href={api.incidents.exportPdfUrl(exportParams)}
              download
            >
              <Download size={13} /> {selected.length ? `Export ${selected.length}` : "PDF report"}
            </a>
          </>
        }
      />

      <Card className="!p-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[190px] flex-1">
            <Search size={14} className="pointer-events-none absolute left-2.5 top-2.5 text-muted" />
            <TextInput
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="Search ID, camera, zone, class…"
              className="!pl-8"
              aria-label="Search incidents"
            />
          </div>
          <Select
            value={category}
            onChange={(event) => {
              setCategory(event.target.value);
              setPage(0);
            }}
            aria-label="Filter by category"
            className="!w-auto min-w-[180px]"
          >
            <option value="">All categories</option>
            {(categories.data?.items || []).map((item) => (
              <option key={item.value} value={item.value}>
                {categoryLabel(item.value, item.label)} ({item.count})
              </option>
            ))}
          </Select>
          <Select
            value={severity}
            onChange={(event) => {
              setSeverity(event.target.value);
              setPage(0);
            }}
            aria-label="Filter by severity"
            className="!w-auto"
          >
            <option value="">Any severity</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </Select>
          <Select
            value={sort}
            onChange={(event) => setSort(event.target.value)}
            aria-label="Sort"
            className="!w-auto"
          >
            <option value="captured_at_desc">Newest first</option>
            <option value="captured_at_asc">Oldest first</option>
            <option value="confidence_desc">Highest confidence</option>
            <option value="severity_desc">Highest severity</option>
          </Select>
          <span className="inline-flex items-center gap-1 text-[0.68rem] text-muted">
            <Filter size={11} /> {total} result{total === 1 ? "" : "s"}
          </span>
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {STATUS_FILTERS.map((filter) => (
            <button
              key={filter.value}
              type="button"
              onClick={() => {
                setStatus(filter.value);
                setPage(0);
              }}
              className={`chip ${status === filter.value ? "chip-active" : "hover:bg-surface-muted"}`}
            >
              {filter.label}
            </button>
          ))}
          {selected.length > 0 ? (
            <span className="ml-auto inline-flex items-center gap-2 text-[0.7rem] text-muted">
              {selected.length} selected
              <button type="button" className="underline decoration-dotted" onClick={() => setSelected([])}>
                clear
              </button>
            </span>
          ) : null}
        </div>
      </Card>

      <div className="mt-4">
        {listing.loading && !listing.data ? (
          <LoadingBlock label="Loading incidents" />
        ) : items.length === 0 ? (
          <Card>
            <EmptyState
              icon={ClipboardList}
              title={search || status || category ? "No incidents match these filters" : "No incidents recorded yet"}
              description={
                search || status || category
                  ? "Adjust the filters or clear the search to see the full register."
                  : "Process a video or start a camera with zones configured. Candidates appear here for verification."
              }
            />
          </Card>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {items.map((incident) => (
              <IncidentCard
                key={incident.id}
                incident={incident}
                selected={selected.includes(incident.id)}
                onToggle={() => toggleSelected(incident.id)}
                onOpen={() => setOpenId(incident.id)}
              />
            ))}
          </div>
        )}
      </div>

      <div className="mt-4 flex items-center justify-between">
        <p className="text-[0.7rem] text-muted">
          Page {page + 1} of {pages}
        </p>
        <div className="flex gap-2">
          <Button
            size="sm"
            icon={ChevronLeft}
            disabled={page === 0}
            onClick={() => setPage((value) => Math.max(0, value - 1))}
          >
            Previous
          </Button>
          <Button
            size="sm"
            disabled={page + 1 >= pages}
            onClick={() => setPage((value) => Math.min(pages - 1, value + 1))}
          >
            Next <ChevronRight size={13} />
          </Button>
        </div>
      </div>

      <div className="mt-5">
        <Note tone="warning" icon={ShieldCheck}>
          Automated detections are advisory. Reports are generated locally and contain only the
          evidence recorded here — no biometric identification or facial recognition is performed.
        </Note>
      </div>

      <IncidentDrawer
        incidentId={openId}
        onClose={() => setOpenId(null)}
        onChanged={() => void listing.refresh(true)}
      />
    </>
  );
}

function IncidentCard({
  incident,
  selected,
  onToggle,
  onOpen,
}: {
  incident: Incident;
  selected: boolean;
  onToggle: () => void;
  onOpen: () => void;
}) {
  const [failed, setFailed] = useState(false);
  return (
    <div
      className={`card group overflow-hidden transition-shadow hover:shadow-card ${
        selected ? "ring-1 ring-teal" : ""
      }`}
    >
      <button type="button" onClick={onOpen} className="block w-full text-left">
        <div className="relative aspect-video w-full bg-surface-muted">
          {incident.has_snapshot && !failed ? (
            <img
              src={api.incidents.snapshotUrl(incident.id)}
              alt=""
              loading="lazy"
              onError={() => setFailed(true)}
              className="h-full w-full object-cover"
            />
          ) : (
            <div className="flex h-full items-center justify-center text-muted">
              <ImageOff size={18} />
            </div>
          )}
          <div className="absolute left-2 top-2 flex gap-1.5">
            <StatusBadge status={incident.status} />
            <SeverityBadge severity={incident.severity} />
          </div>
          <div className="absolute bottom-2 right-2 rounded bg-[rgba(20,26,28,0.62)] px-1.5 py-0.5 font-mono text-[0.62rem] text-white">
            {incident.confidence.toFixed(2)}
          </div>
        </div>
        <div className="p-3">
          <p className="text-[0.83rem] font-medium leading-snug text-ink">
            {categoryLabel(incident.category, incident.label)}
          </p>
          <p className="mt-0.5 font-mono text-[0.66rem] text-muted">{incident.id}</p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {incident.class_name ? (
              <Badge tone="neutral">{CLASS_LABELS[incident.class_name] || incident.class_name}</Badge>
            ) : null}
            {incident.zone_name ? <Badge tone="teal">{incident.zone_name}</Badge> : null}
            {incident.dwell_seconds ? (
              <Badge tone="neutral">{incident.dwell_seconds.toFixed(0)}s dwell</Badge>
            ) : null}
          </div>
          <p className="mt-2 truncate text-[0.7rem] text-muted">{incident.camera}</p>
          <p className="text-[0.66rem] text-muted" title={formatDateTime(incident.captured_at)}>
            {formatRelative(incident.captured_at)}
          </p>
        </div>
      </button>
      <div className="flex items-center justify-between border-t border-line px-3 py-1.5">
        <label className="inline-flex items-center gap-1.5 text-[0.68rem] text-muted">
          <input
            type="checkbox"
            checked={selected}
            onChange={onToggle}
            aria-label={`Select ${incident.id}`}
            className="h-3 w-3 rounded border-line accent-teal"
          />
          select
        </label>
        <button type="button" onClick={onOpen} className="text-[0.68rem] font-medium text-teal hover:underline">
          Review →
        </button>
      </div>
    </div>
  );
}
