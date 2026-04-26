import { Activity, Server } from "lucide-react";

import { API_BASE_URL } from "@/lib/api";

export function Topbar() {
  return (
    <header className="sticky top-0 z-20 border-b border-border bg-background/85 backdrop-blur">
      <div className="flex min-h-16 items-center justify-between px-5 lg:px-8">
        <div>
          <div className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Local research terminal</div>
          <h1 className="text-lg font-semibold tracking-normal">Personal Hedge System</h1>
        </div>
        <div className="hidden items-center gap-4 text-xs text-muted-foreground md:flex">
          <span className="inline-flex items-center gap-2">
            <Activity className="h-4 w-4 text-success" />
            Paper analytics only
          </span>
          <span className="inline-flex items-center gap-2">
            <Server className="h-4 w-4 text-primary" />
            {API_BASE_URL}
          </span>
        </div>
      </div>
    </header>
  );
}
