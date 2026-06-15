import { type ReactNode } from "react";
import { STRENGTH_STYLE } from "./constants";

export function AngelOverviewCard({
  icon,
  label,
  value,
  caption,
  tone,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  caption: string;
  tone: "blue" | "teal" | "amber" | "green";
}) {
  const toneStyles = {
    blue: { background: "#eef5ff", color: "#1f6feb" },
    teal: { background: "#e8f7f6", color: "#177b75" },
    amber: { background: "#fff5e6", color: "#b56d00" },
    green: { background: "#eaf8f0", color: "#1f8f5f" },
  }[tone];

  return (
    <div
      className="p-0"
      style={{
        padding: "20px 20px 18px",
        borderRadius: 22,
        border: "1px solid rgba(255,255,255,0.1)",
        background: "rgba(255,255,255,0.1)",
        backdropFilter: "blur(6px)",
      }}
    >
      <div className="flex items-start gap-3">
        <div
          className="flex h-10 w-10 shrink-0 items-center justify-center"
          style={{ borderRadius: 18, ...toneStyles }}
        >
          {icon}
        </div>
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em]" style={{ color: "rgba(255,255,255,0.62)" }}>{label}</p>
          <p className="mt-3 text-[30px] font-bold leading-none" style={{ color: "#ffffff" }}>{value}</p>
          <p className="mt-3 text-[12px] leading-6" style={{ color: "rgba(255,255,255,0.72)" }}>{caption}</p>
        </div>
      </div>
    </div>
  );
}

export function SnapshotRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "blue" | "teal" | "green";
}) {
  const toneStyles = {
    blue: { background: "#eef5ff", color: "#235dc6" },
    teal: { background: "#edf9f8", color: "#177b75" },
    green: { background: "#eaf8f0", color: "#1f8f5f" },
  }[tone];

  return (
    <div
      className="flex items-center justify-between px-4 py-3"
      style={{ borderRadius: 18, border: "1px solid #e4edf5", background: "#fbfdff", padding: "14px 16px" }}
    >
      <span className="text-[13px] font-medium text-[#60758a]">{label}</span>
      <span className="px-2.5 py-1 text-[11px] font-bold" style={{ borderRadius: 999, ...toneStyles }}>{value}</span>
    </div>
  );
}

export function StrengthBadge({
  strength,
  compact = false,
  labelPrefix,
}: {
  strength: number;
  compact?: boolean;
  labelPrefix?: string;
}) {
  return (
    <span
      className={`inline-flex items-center font-bold ${compact ? "px-2.5 py-1 text-[10px]" : "px-3 py-1.5 text-[11px]"}`}
      style={{ borderRadius: 999, ...(STRENGTH_STYLE[strength] || {}) }}
    >
      {labelPrefix ? `${labelPrefix}: ` : ""}
      {strength}/5
    </span>
  );
}
