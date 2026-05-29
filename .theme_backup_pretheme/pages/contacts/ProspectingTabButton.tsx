import { type ReactNode } from "react";

export function ProspectingTabButton({
  active,
  icon,
  label,
  description,
  count,
  countLabel,
  accent,
  onClick,
}: {
  active: boolean;
  icon: ReactNode;
  label: string;
  description: string;
  count: number;
  countLabel: string;
  accent: "blue" | "teal";
  onClick: () => void;
}) {
  const accentStyles = active
    ? accent === "blue"
      ? {
          shell: {
            borderColor: "transparent",
            background: "linear-gradient(135deg, #1c4f93 0%, #1f6feb 100%)",
            boxShadow: "0 16px 32px rgba(31, 111, 235, 0.22)",
            color: "#ffffff",
          },
          icon: { background: "rgba(255,255,255,0.14)", color: "#ffffff" },
          badge: { background: "rgba(255,255,255,0.14)", color: "#ffffff" },
        }
      : {
          shell: {
            borderColor: "transparent",
            background: "linear-gradient(135deg, #124a4c 0%, #1b8a86 100%)",
            boxShadow: "0 16px 32px rgba(27, 138, 134, 0.22)",
            color: "#ffffff",
          },
          icon: { background: "rgba(255,255,255,0.14)", color: "#ffffff" },
          badge: { background: "rgba(255,255,255,0.14)", color: "#ffffff" },
        }
    : accent === "blue"
      ? {
          shell: {
            borderColor: "#d9e1ec",
            background: "linear-gradient(180deg, #ffffff 0%, #f8fbff 100%)",
            boxShadow: "0 8px 20px rgba(17, 34, 68, 0.04)",
            color: "#1d2b3c",
          },
          icon: { background: "#eaf2ff", color: "#1f6feb" },
          badge: { background: "#edf4ff", color: "#1f6feb" },
        }
      : {
          shell: {
            borderColor: "#d9e1ec",
            background: "linear-gradient(180deg, #ffffff 0%, #f8fbff 100%)",
            boxShadow: "0 8px 20px rgba(17, 34, 68, 0.04)",
            color: "#1d2b3c",
          },
          icon: { background: "#e7f7f5", color: "#177b75" },
          badge: { background: "#edf9f8", color: "#177b75" },
        };

  return (
    <button
      type="button"
      onClick={onClick}
      className="min-w-[250px] flex-1 border p-4 text-left transition-all"
      style={{ borderRadius: 22, ...accentStyles.shell }}
    >
      <div className="flex items-start gap-3">
        <div
          className="flex h-11 w-11 shrink-0 items-center justify-center"
          style={{ borderRadius: 18, ...accentStyles.icon }}
        >
          {icon}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-[15px] font-bold">{label}</p>
              <p
                className="mt-1 text-[12px] leading-5"
                style={{ color: active ? "rgba(255,255,255,0.78)" : "#6f8297" }}
              >
                {description}
              </p>
            </div>
            <span
              className="shrink-0 px-2.5 py-1 text-[11px] font-bold"
              style={{ borderRadius: 999, ...accentStyles.badge }}
            >
              {count}
            </span>
          </div>
          <p
            className="mt-4 text-[11px] font-semibold uppercase tracking-[0.14em]"
            style={{ color: active ? "rgba(255,255,255,0.62)" : "#90a3b8" }}
          >
            {countLabel}
          </p>
        </div>
      </div>
    </button>
  );
}
