"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BarChart3, BookOpen, FileText, Gauge, LayoutDashboard, ShieldAlert, Star } from "lucide-react";

import { cn } from "@/lib/utils";

const nav = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/watchlist", label: "Watchlist", icon: Star },
  { href: "/risk", label: "Risk", icon: ShieldAlert },
  { href: "/report", label: "Daily Report", icon: FileText },
  { href: "/ft-research", label: "FT Research", icon: BookOpen },
  { href: "/asset/SPY", label: "SPY Analysis", icon: BarChart3 }
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-border bg-background/95 px-4 py-5 backdrop-blur lg:block">
      <div className="mb-8 flex items-center gap-3 px-2">
        <div className="flex h-9 w-9 items-center justify-center rounded-md border border-primary/30 bg-primary/10">
          <Gauge className="h-5 w-5 text-primary" />
        </div>
        <div>
          <div className="text-sm font-semibold">Personal Hedge</div>
          <div className="text-xs text-muted-foreground">Market intelligence</div>
        </div>
      </div>
      <nav className="space-y-1">
        {nav.map((item) => {
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex h-10 items-center gap-3 rounded-md px-3 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                active && "bg-muted text-foreground"
              )}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
