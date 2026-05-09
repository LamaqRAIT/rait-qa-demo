import { clsx } from "clsx";

type Variant = "success" | "error" | "warning" | "active" | "muted";

const STYLES: Record<Variant, string> = {
  success: "bg-green/15 text-green",
  error:   "bg-red/15 text-red",
  warning: "bg-yellow/15 text-yellow",
  active:  "bg-blue/15 text-blue",
  muted:   "bg-cream/10 text-cream/50",
};

export function Badge({
  children,
  variant = "muted",
  className,
}: {
  children: React.ReactNode;
  variant?: Variant;
  className?: string;
}) {
  return (
    <span
      className={clsx(
        "inline-flex items-center h-5 px-2 rounded-pill",
        "text-[11px] font-semibold uppercase tracking-wide",
        STYLES[variant],
        className
      )}
    >
      {children}
    </span>
  );
}
