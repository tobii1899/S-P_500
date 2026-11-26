import type { LucideIcon } from "lucide-react";
import type { DayStatus } from "../types";

export const STATUS_LABEL: Record<DayStatus, string> = {
  PENDING: "PENDING",
  SEARCHING: "SEARCHING",
  ACTIVE: "ACTIVE",
  WIN: "WIN",
  LOSS: "LOSS",
  NO_TRADE: "NO TRADE",
  CLOSED: "CLOSED",
};

export const STATUS_TEXT: Record<DayStatus, string> = {
  PENDING: "text-idle",
  SEARCHING: "text-accent",
  ACTIVE: "text-accent",
  WIN: "text-win",
  LOSS: "text-loss",
  NO_TRADE: "text-idle",
  CLOSED: "text-idle",
};

export function Pill({ status }: { status: DayStatus }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full bg-soft px-2.5 py-1 text-[12px] font-medium ${STATUS_TEXT[status]}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {STATUS_LABEL[status]}
    </span>
  );
}

export function Metric({ label, value, className = "" }: { label: string; value: string; className?: string }) {
  return (
    <div>
      <dt className="label">{label}</dt>
      <dd className={`mt-1 text-[22px] font-semibold tracking-tight ${className}`}>{value}</dd>
    </div>
  );
}

export function Fact({ icon: Icon, label, value, sub }: { icon: LucideIcon; label: string; value: string; sub?: string }) {
  return (
    <div className="flex items-start gap-3">
      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted" strokeWidth={1.75} />
      <div className="min-w-0">
        <dt className="label">{label}</dt>
        <dd className="text-[15px] font-medium">{value}</dd>
        {sub && <dd className="truncate text-[12px] text-muted">{sub}</dd>}
      </div>
    </div>
  );
}
