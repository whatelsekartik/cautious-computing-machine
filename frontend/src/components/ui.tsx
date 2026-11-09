import { useEffect, useId, type ReactNode } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Info,
  Loader2,
  X,
  type LucideIcon,
} from "lucide-react";
import { STATUS_LABELS } from "../lib/format";

/* ------------------------------------------------------------------ */
/* Buttons                                                             */
/* ------------------------------------------------------------------ */

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

export function Button({
  children,
  variant = "secondary",
  size = "md",
  icon: Icon,
  loading = false,
  className = "",
  type = "button",
  ...rest
}: {
  children?: ReactNode;
  variant?: ButtonVariant;
  size?: "sm" | "md";
  icon?: LucideIcon;
  loading?: boolean;
  className?: string;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const variantClass =
    variant === "primary"
      ? "btn-primary"
      : variant === "danger"
        ? "btn-danger"
        : variant === "ghost"
          ? "btn-ghost"
          : "btn-secondary";
  return (
    <button
      type={type}
      className={`btn ${variantClass} ${size === "sm" ? "btn-sm" : ""} ${className}`}
      disabled={loading || rest.disabled}
      {...rest}
    >
      {loading ? (
        <Loader2 size={size === "sm" ? 13 : 15} className="animate-spin" />
      ) : Icon ? (
        <Icon size={size === "sm" ? 13 : 15} strokeWidth={1.9} />
      ) : null}
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* Surfaces                                                            */
/* ------------------------------------------------------------------ */

export function Card({
  children,
  className = "",
  padded = true,
}: {
  children: ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return <div className={`card ${padded ? "p-4 sm:p-5" : ""} ${className}`}>{children}</div>;
}

export function SectionHeader({
  title,
  subtitle,
  action,
  icon: Icon,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  icon?: LucideIcon;
}) {
  return (
    <div className="mb-3 flex items-start justify-between gap-3">
      <div className="flex items-start gap-2.5">
        {Icon ? <Icon size={16} className="mt-0.5 text-teal" strokeWidth={1.9} /> : null}
        <div>
          <h2 className="text-[0.95rem] font-semibold tracking-tight text-ink">{title}</h2>
          {subtitle ? <p className="mt-0.5 text-xs leading-relaxed text-muted">{subtitle}</p> : null}
        </div>
      </div>
      {action}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Badges & status                                                     */
/* ------------------------------------------------------------------ */

type Tone = "neutral" | "teal" | "blue" | "amber" | "danger" | "positive";

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "border-line bg-surface-muted text-muted",
  teal: "border-transparent bg-teal-soft text-teal",
  blue: "border-transparent bg-blue-soft text-blue",
  amber: "border-transparent bg-amber-soft text-amber",
  danger: "border-transparent bg-danger-soft text-danger",
  positive: "border-transparent bg-positive-soft text-positive",
};

export function Badge({
  children,
  tone = "neutral",
  className = "",
}: {
  children: ReactNode;
  tone?: Tone;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[0.7rem] font-medium ${TONE_CLASSES[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

export const STATUS_TONES: Record<string, Tone> = {
  detected: "amber",
  pending_review: "blue",
  verified: "teal",
  dismissed: "neutral",
  forwarded: "blue",
  resolved: "positive",
};

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONES[status] || "neutral"}>{STATUS_LABELS[status] || status}</Badge>;
}

export function SeverityBadge({ severity }: { severity: string }) {
  const tone: Tone =
    severity === "high" ? "danger" : severity === "medium" ? "amber" : "neutral";
  return <Badge tone={tone}>{severity}</Badge>;
}

export function Dot({ tone = "teal", pulse = false }: { tone?: Tone; pulse?: boolean }) {
  const color =
    tone === "danger"
      ? "bg-danger"
      : tone === "amber"
        ? "bg-amber"
        : tone === "positive"
          ? "bg-positive"
          : tone === "blue"
            ? "bg-blue"
            : tone === "neutral"
              ? "bg-muted"
              : "bg-teal";
  return (
    <span className="relative inline-flex h-2 w-2">
      {pulse ? (
        <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-60 ${color}`} />
      ) : null}
      <span className={`relative inline-flex h-2 w-2 rounded-full ${color}`} />
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Stats                                                               */
/* ------------------------------------------------------------------ */

export function Stat({
  label,
  value,
  hint,
  icon: Icon,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon?: LucideIcon;
  tone?: Tone;
}) {
  return (
    <div className="card p-4">
      <div className="flex items-start justify-between">
        <p className="text-[0.7rem] font-semibold uppercase tracking-[0.07em] text-muted">{label}</p>
        {Icon ? (
          <span className={`rounded-lg p-1.5 ${TONE_CLASSES[tone]}`}>
            <Icon size={14} strokeWidth={1.9} />
          </span>
        ) : null}
      </div>
      <p className="mt-2 text-2xl font-semibold tracking-tight text-ink tabular-nums">{value}</p>
      {hint ? <p className="mt-1 text-xs leading-snug text-muted">{hint}</p> : null}
    </div>
  );
}

export function KeyValue({ items }: { items: { label: string; value: ReactNode }[] }) {
  return (
    <dl className="divide-y divide-line/70">
      {items.map((item) => (
        <div key={item.label} className="flex items-baseline justify-between gap-4 py-1.5">
          <dt className="text-xs text-muted">{item.label}</dt>
          <dd className="text-right text-xs font-medium text-ink">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/* ------------------------------------------------------------------ */
/* Form controls                                                       */
/* ------------------------------------------------------------------ */

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
}: {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  children: ReactNode;
  htmlFor?: string;
}) {
  return (
    <div>
      <label className="field-label" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
      {hint && !error ? <p className="mt-1 text-[0.7rem] leading-snug text-muted">{hint}</p> : null}
      {error ? <p className="mt-1 text-[0.7rem] leading-snug text-danger">{error}</p> : null}
    </div>
  );
}

export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement> & { mono?: boolean }) {
  const { className = "", mono = false, ...rest } = props;
  return <input {...rest} className={`input ${mono ? "input-mono" : ""} ${className}`} />;
}

export function Select({
  children,
  className = "",
  ...rest
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...rest} className={`input appearance-none pr-8 ${className}`}>
      {children}
    </select>
  );
}

export function Slider({
  value,
  min,
  max,
  step = 1,
  onChange,
  label,
  display,
  hint,
}: {
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (value: number) => void;
  label: string;
  display?: string;
  hint?: string;
}) {
  const id = useId();
  return (
    <div>
      <div className="flex items-center justify-between">
        <label htmlFor={id} className="field-label mb-0">
          {label}
        </label>
        <span className="font-mono text-xs font-medium text-ink">{display ?? value}</span>
      </div>
      <input
        id={id}
        type="range"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
        className="mt-1.5 h-1.5 w-full cursor-pointer appearance-none rounded-full bg-line accent-teal"
      />
      {hint ? <p className="mt-1 text-[0.7rem] text-muted">{hint}</p> : null}
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  hint,
  disabled = false,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  hint?: string;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-1.5">
      <div>
        <p className="text-sm font-medium text-ink">{label}</p>
        {hint ? <p className="mt-0.5 text-[0.72rem] leading-snug text-muted">{hint}</p> : null}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`mt-0.5 inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors ${
          checked ? "border-teal bg-teal" : "border-line bg-surface-muted"
        } ${disabled ? "opacity-50" : ""}`}
      >
        <span
          className={`h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform ${
            checked ? "translate-x-[1.1rem]" : "translate-x-[0.15rem]"
          }`}
        />
      </button>
    </div>
  );
}

export function Checkbox({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm text-ink">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="h-3.5 w-3.5 rounded border-line accent-teal"
      />
      {label}
    </label>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-surface p-0.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
            value === option.value
              ? "bg-teal-soft text-teal"
              : "text-muted hover:text-ink"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Feedback                                                            */
/* ------------------------------------------------------------------ */

export function ProgressBar({
  value,
  tone = "teal",
  className = "",
}: {
  value: number;
  tone?: "teal" | "blue" | "amber" | "danger";
  className?: string;
}) {
  const pct = Math.max(0, Math.min(100, value * 100));
  const bg =
    tone === "blue" ? "bg-blue" : tone === "amber" ? "bg-amber" : tone === "danger" ? "bg-danger" : "bg-teal";
  return (
    <div className={`h-1.5 w-full overflow-hidden rounded-full bg-surface-muted ${className}`}>
      <div className={`h-full rounded-full ${bg} transition-[width] duration-300`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Note({
  children,
  tone = "info",
  icon,
}: {
  children: ReactNode;
  tone?: "info" | "warning" | "success" | "danger";
  icon?: LucideIcon;
}) {
  const Icon = icon || (tone === "warning" || tone === "danger" ? AlertTriangle : tone === "success" ? CheckCircle2 : Info);
  const classes =
    tone === "warning"
      ? "border-amber/30 bg-amber-soft/70 text-ink"
      : tone === "danger"
        ? "border-danger/30 bg-danger-soft/70 text-ink"
        : tone === "success"
          ? "border-positive/30 bg-positive-soft/70 text-ink"
          : "border-line bg-surface-muted/70 text-ink";
  return (
    <div className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-xs leading-relaxed ${classes}`}>
      <Icon size={14} className="mt-0.5 shrink-0 opacity-80" strokeWidth={1.9} />
      <div className="flex-1">{children}</div>
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-muted">
      <Loader2 size={14} className="animate-spin" />
      {label}
    </div>
  );
}

export function LoadingBlock({ label = "Loading" }: { label?: string }) {
  return (
    <div className="card flex items-center justify-center gap-2 p-10 text-sm text-muted">
      <Loader2 size={15} className="animate-spin" />
      {label}
    </div>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-line bg-surface/60 px-6 py-10 text-center">
      <span className="mb-3 rounded-xl bg-surface-muted p-2.5 text-muted">
        <Icon size={18} strokeWidth={1.7} />
      </span>
      <p className="text-sm font-medium text-ink">{title}</p>
      {description ? <p className="mt-1 max-w-sm text-xs leading-relaxed text-muted">{description}</p> : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Modal                                                               */
/* ------------------------------------------------------------------ */

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: "md" | "lg" | "xl";
}) {
  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", handler);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);

  if (!open) return null;
  const width = size === "xl" ? "max-w-5xl" : size === "lg" ? "max-w-3xl" : "max-w-lg";
  return (
    <div className="fixed inset-0 z-[70] flex items-start justify-center overflow-y-auto bg-[rgba(20,26,28,0.45)] p-4 pt-[6vh] animate-fade-in">
      <div className={`w-full ${width} rounded-xl border border-line bg-surface shadow-xl animate-slide-up`}>
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-3.5">
          <div>
            <h3 className="text-sm font-semibold text-ink">{title}</h3>
            {description ? <p className="mt-0.5 text-xs text-muted">{description}</p> : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1 text-muted hover:bg-surface-muted hover:text-ink"
          >
            <X size={15} />
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
        {footer ? <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</div> : null}
      </div>
    </div>
  );
}
