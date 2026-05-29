import type { CSSProperties } from "react";

export const PERSONA_STYLE: Record<string, CSSProperties> = {
  economic_buyer: { color: "#7b3a1d", background: "#ffe8de", border: "1px solid #ffc8b4" },
  champion: { color: "#1b6f53", background: "#e4fbf3", border: "1px solid #b8efd8" },
  technical_evaluator: { color: "#24567e", background: "#eaf4ff", border: "1px solid #c9e0f8" },
  unknown: { color: "#546679", background: "#edf3f9", border: "1px solid #d7e1eb" },
};

export const PERSONA_LABEL: Record<string, string> = {
  economic_buyer: "Economic Buyer",
  champion: "Champion",
  technical_evaluator: "Tech Eval",
  unknown: "Unknown",
};

export const STRENGTH_STYLE: Record<number, CSSProperties> = {
  5: { color: "#166534", background: "#dcfce7", border: "1px solid #bbf7d0" },
  4: { color: "#1e40af", background: "#dbeafe", border: "1px solid #bfdbfe" },
  3: { color: "#854d0e", background: "#fef9c3", border: "1px solid #fde68a" },
  2: { color: "#9a3412", background: "#ffedd5", border: "1px solid #fed7aa" },
  1: { color: "#991b1b", background: "#fee2e2", border: "1px solid #fecaca" },
};

export const STRENGTH_LABEL: Record<number, string> = {
  5: "Direct fund overlap",
  4: "Same fund, both sides",
  3: "PE/VC peer community",
  2: "Domain/sector community",
  1: "Indirect connection",
};

export const ANGEL_SURFACE: Record<string, CSSProperties> = {
  toolbar: {
    padding: "24px 26px",
    borderRadius: 22,
    border: "1px solid #dbe6f2",
    background: "linear-gradient(180deg, #ffffff 0%, #f7fbff 100%)",
    boxShadow: "0 18px 40px rgba(17, 34, 68, 0.06)",
  },
  hero: {
    overflow: "hidden",
    borderRadius: 24,
    border: "1px solid #d5e3ef",
    background: "linear-gradient(135deg, #0f2744 0%, #175089 44%, #17928e 100%)",
    boxShadow: "0 22px 48px rgba(14, 38, 66, 0.16)",
  },
  panel: {
    borderRadius: 22,
    border: "1px solid #dce7f1",
    background: "linear-gradient(180deg, #ffffff 0%, #f9fcff 100%)",
    boxShadow: "0 14px 28px rgba(17, 34, 68, 0.055)",
  },
  companyCard: {
    overflow: "hidden",
    borderRadius: 24,
    border: "1px solid #dbe5f2",
    background: "#ffffff",
    boxShadow: "0 18px 36px rgba(17, 34, 68, 0.07)",
  },
  contactCard: {
    overflow: "hidden",
    borderRadius: 22,
    border: "1px solid #e3edf7",
    background: "#ffffff",
    boxShadow: "0 12px 28px rgba(17, 34, 68, 0.04)",
  },
  pathCard: {
    borderRadius: 20,
    border: "1px solid #e8eff7",
    background: "#fbfdff",
    padding: 20,
  },
};

export const ANGEL_TEXT = {
  title: "#1c2b4a",
  body: "#5f7390",
  soft: "#7f91ab",
};
