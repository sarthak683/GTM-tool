import { ButtonHTMLAttributes, forwardRef } from "react";
import { cn } from "../../lib/utils";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "ghost" | "outline" | "destructive";
  size?: "xs" | "sm" | "md" | "lg";
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", size = "md", ...props }, ref) => (
    <button
      ref={ref}
      className={cn(
        "inline-flex items-center justify-center rounded-lg font-medium transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-1 disabled:opacity-40 disabled:cursor-not-allowed select-none",
        variant === "default"     && "bg-violet-600 text-white hover:bg-violet-700 shadow-sm active:scale-[0.98]",
        variant === "ghost"       && "text-stone-600 hover:bg-stone-100 hover:text-stone-900",
        variant === "outline"     && "border border-stone-300 bg-white text-stone-700 hover:bg-stone-50 shadow-sm active:scale-[0.98]",
        variant === "destructive" && "bg-red-600 text-white hover:bg-red-700 shadow-sm",
        size === "xs" && "px-2 py-1 text-[11px]",
        size === "sm" && "px-2.5 py-1.5 text-xs gap-1.5",
        size === "md" && "px-3.5 py-2 text-sm gap-2",
        size === "lg" && "px-5 py-2.5 text-sm gap-2",
        className
      )}
      {...props}
    />
  )
);
Button.displayName = "Button";
