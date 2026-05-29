import { HTMLAttributes } from "react";
import { cn } from "../../lib/utils";

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: "default" | "success" | "warning" | "danger" | "info" | "ghost" | "purple";
}

export function Badge({ className, variant = "default", ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium leading-tight",
        variant === "default" && "bg-stone-100 text-stone-700",
        variant === "success" && "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200",
        variant === "warning" && "bg-amber-50 text-amber-700 ring-1 ring-amber-200",
        variant === "danger"  && "bg-red-50 text-red-600 ring-1 ring-red-200",
        variant === "info"    && "bg-blue-50 text-blue-700 ring-1 ring-blue-200",
        variant === "ghost"   && "bg-stone-50 text-stone-500 ring-1 ring-stone-200",
        variant === "purple"  && "bg-violet-50 text-violet-700 ring-1 ring-violet-200",
        className
      )}
      {...props}
    />
  );
}
