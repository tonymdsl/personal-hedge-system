import * as React from "react";

import { cn } from "@/lib/utils";

const styles: Record<string, string> = {
  positive: "border-success/40 bg-success/10 text-success",
  neutral: "border-slate-500/40 bg-slate-500/10 text-slate-300",
  negative: "border-destructive/40 bg-destructive/10 text-red-300",
  mixed: "border-warning/40 bg-warning/10 text-amber-300",
  high: "border-destructive/40 bg-destructive/10 text-red-300",
  medium: "border-warning/40 bg-warning/10 text-amber-300",
  low: "border-success/40 bg-success/10 text-success",
  default: "border-border bg-muted text-muted-foreground"
};

export function Badge({ className, tone = "default", ...props }: React.HTMLAttributes<HTMLSpanElement> & { tone?: string }) {
  return (
    <span
      className={cn("inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium", styles[tone] || styles.default, className)}
      {...props}
    />
  );
}
