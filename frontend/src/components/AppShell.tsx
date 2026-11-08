import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  ClipboardList,
  Cpu,
  LayoutDashboard,
  Menu,
  Monitor,
  Moon,
  PenTool,
  Settings as SettingsIcon,
  Sun,
  Video,
  Wifi,
  WifiOff,
  X,
  Zap,
} from "lucide-react";
import { BrandMark } from "./Accents";
import { Dot } from "./ui";
import { useTheme, useEventStream } from "../lib/hooks";
import { api } from "../lib/api";
import type { DeviceInfo } from "../lib/types";

interface NavItem {
  to: string;
  label: string;
  icon: typeof LayoutDashboard;
  hint: string;
}

const NAV: NavItem[] = [
  { to: "/", label: "Overview", icon: LayoutDashboard, hint: "System status" },
  { to: "/analysis", label: "Video analysis", icon: Video, hint: "Upload & process" },
  { to: "/live", label: "Live monitoring", icon: Monitor, hint: "Cameras" },
  { to: "/zones", label: "Zone editor", icon: PenTool, hint: "Restricted areas" },
  { to: "/incidents", label: "Incidents", icon: ClipboardList, hint: "Review queue" },
  { to: "/settings", label: "Settings", icon: SettingsIcon, hint: "Devices & privacy" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const [device, setDevice] = useState<DeviceInfo | null>(null);
  const [model, setModel] = useState<string>("");
  const [mobileOpen, setMobileOpen] = useState(false);
  const { mode, setMode } = useTheme();
  const location = useLocation();
  const { connected } = useEventStream(() => undefined);

  useEffect(() => {
    let active = true;
    api
      .settings()
      .then((response) => {
        if (!active) return;
        setModel(response.values.model_id);
        setDevice(response.device as DeviceInfo);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  const dark =
    mode === "dark" ||
    (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  const nextMode = () => setMode(dark ? "light" : "dark");

  const nav = (
    <nav className="flex flex-1 flex-col gap-0.5">
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === "/"}
          className={({ isActive }) =>
            `group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[0.83rem] transition-colors ${
              isActive
                ? "bg-teal-soft font-medium text-teal"
                : "text-muted hover:bg-surface-muted hover:text-ink"
            }`
          }
        >
          <item.icon size={15} strokeWidth={1.85} />
          <span className="flex-1">{item.label}</span>
        </NavLink>
      ))}
    </nav>
  );

  const footer = (
    <div className="space-y-2 border-t border-line pt-3">
      <div className="flex items-center justify-between text-[0.7rem] text-muted">
        <span className="inline-flex items-center gap-1.5">
          <Cpu size={12} />
          {device ? device.device.toUpperCase() : "…"}
        </span>
        <span className="inline-flex items-center gap-1.5" title={connected ? "Live updates connected" : "Live updates offline"}>
          {connected ? <Wifi size={12} className="text-positive" /> : <WifiOff size={12} className="text-danger" />}
          {connected ? "live" : "offline"}
        </span>
      </div>
      <div className="flex items-center justify-between text-[0.7rem] text-muted">
        <span className="truncate font-mono">{model || "model"}</span>
        <button
          type="button"
          onClick={nextMode}
          aria-label="Toggle dark mode"
          className="rounded-md p-1 text-muted hover:bg-surface-muted hover:text-ink"
        >
          {dark ? <Sun size={13} /> : <Moon size={13} />}
        </button>
      </div>
      <p className="text-[0.65rem] leading-tight text-muted/80">
        Local processing · v1.0 · human review required
      </p>
    </div>
  );

  return (
    <div className="min-h-screen bg-canvas">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[236px] flex-col border-r border-line bg-surface px-3 py-4 lg:flex">
        <div className="mb-5 flex items-center gap-2.5 px-1">
          <BrandMark size={30} />
          <div>
            <p className="text-[0.9rem] font-semibold leading-tight tracking-tight text-ink">CivicWatch</p>
            <p className="text-[0.66rem] uppercase tracking-[0.13em] text-muted">AI Studio</p>
          </div>
        </div>
        {nav}
        {footer}
      </aside>

      {/* Mobile header */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-surface/95 px-4 py-2.5 backdrop-blur lg:hidden">
        <div className="flex items-center gap-2">
          <BrandMark size={24} />
          <div>
            <p className="text-sm font-semibold leading-none text-ink">CivicWatch</p>
            <p className="text-[0.6rem] uppercase tracking-[0.14em] text-muted">AI Studio</p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={nextMode}
            aria-label="Toggle dark mode"
            className="rounded-md p-1.5 text-muted hover:bg-surface-muted"
          >
            {dark ? <Sun size={15} /> : <Moon size={15} />}
          </button>
          <button
            type="button"
            onClick={() => setMobileOpen((value) => !value)}
            aria-label="Menu"
            className="rounded-md p-1.5 text-muted hover:bg-surface-muted"
          >
            {mobileOpen ? <X size={16} /> : <Menu size={16} />}
          </button>
        </div>
      </header>

      {mobileOpen ? (
        <div className="fixed inset-x-0 top-[49px] z-40 border-b border-line bg-surface p-3 lg:hidden">
          {nav}
          <div className="mt-3">{footer}</div>
        </div>
      ) : null}

      <main className="lg:pl-[236px]">
        <div className="mx-auto w-full max-w-[1400px] px-4 pb-16 pt-5 sm:px-6 lg:px-8 lg:pt-7">
          {children}
        </div>
      </main>
    </div>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        {eyebrow ? (
          <p className="mb-1 inline-flex items-center gap-1.5 text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-teal">
            <Zap size={11} strokeWidth={2.2} />
            {eyebrow}
          </p>
        ) : null}
        <h1 className="text-[1.45rem] font-semibold leading-tight tracking-tight text-ink sm:text-[1.6rem]">
          {title}
        </h1>
        {description ? <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function LiveChip({ online, label }: { online: boolean; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2 py-0.5 text-[0.7rem] text-muted">
      <Dot tone={online ? "positive" : "neutral"} pulse={online} />
      {label}
    </span>
  );
}
