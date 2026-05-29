import { useDraggable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import { useNavigate } from "react-router-dom";
import { Clock3, ArrowRight } from "lucide-react";
import type { Deal } from "../../types";
import { formatCurrency } from "../../lib/utils";

const HEALTH_COLOR: Record<string, string> = {
  green:  "#10B981",
  yellow: "#F59E0B",
  red:    "#EF4444",
};

interface DealCardProps {
  deal: Deal;
  companyName?: string;
}

export default function DealCard({ deal, companyName }: DealCardProps) {
  const navigate = useNavigate();
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({ id: deal.id });

  const style = {
    transform: CSS.Translate.toString(transform),
    opacity: isDragging ? 0.25 : 1,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className="deal-card rounded-2xl border border-[#dbe6f1] bg-white p-6 select-none shadow-[0_10px_24px_rgba(15,30,50,0.08)] hover:shadow-[0_14px_28px_rgba(15,30,50,0.12)] hover:border-[#bcd0e6] transition-all duration-150 group"
    >
      <div {...listeners} {...attributes} className="cursor-grab active:cursor-grabbing">
        <div className="deal-card-head flex items-center gap-2 mb-4">
          <span className="h-2.5 w-2.5 rounded-full shrink-0" style={{ background: HEALTH_COLOR[deal.health] ?? "#D6D3D1" }} />
          <span className="text-[12px] font-medium text-[#71859b] truncate">{companyName ?? "Unknown company"}</span>
        </div>
        <p className="deal-card-title text-[14px] font-semibold text-[#223145] line-clamp-2 leading-snug">{deal.name}</p>
      </div>

      <div className="deal-card-footer flex items-center justify-between mt-4 pt-4 border-t border-[#e6edf5]">
        <span className="text-[17px] font-extrabold text-[#1f2a37] tabular">{formatCurrency(deal.value)}</span>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1 text-[12px] text-[#6f849a]">
            <Clock3 className="h-3.5 w-3.5" />
            <span>{deal.days_in_stage ?? 0}d</span>
          </div>
          <button
            onClick={() => navigate(`/pipeline?deal=${deal.id}`)}
            className="opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-0.5 text-[12px] font-semibold text-[#ff6b35]"
          >
            Open <ArrowRight className="h-3 w-3" />
          </button>
        </div>
      </div>
    </div>
  );
}
