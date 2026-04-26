import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import type { WatchlistItem } from "@/lib/types";
import { formatCurrency, formatPercent } from "@/lib/utils";

export function WatchlistTable({ items }: { items: WatchlistItem[] }) {
  if (!items.length) {
    return <div className="rounded-lg border border-border p-6 text-sm text-muted-foreground">No assets in the watchlist.</div>;
  }
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <table className="w-full border-collapse text-sm">
        <thead className="bg-muted/70 text-xs uppercase tracking-[0.12em] text-muted-foreground">
          <tr>
            <th className="px-4 py-3 text-left">Symbol</th>
            <th className="px-4 py-3 text-left">Name</th>
            <th className="px-4 py-3 text-right">Price</th>
            <th className="px-4 py-3 text-right">1D</th>
            <th className="px-4 py-3 text-right">Cum. return</th>
            <th className="px-4 py-3 text-right">Vol.</th>
            <th className="px-4 py-3 text-right">Source</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.symbol} className="border-t border-border hover:bg-muted/40">
              <td className="px-4 py-3 font-medium">
                <Link className="text-primary hover:underline" href={`/asset/${item.symbol}`}>
                  {item.symbol}
                </Link>
              </td>
              <td className="px-4 py-3 text-muted-foreground">{item.name}</td>
              <td className="px-4 py-3 text-right">{formatCurrency(item.latest_price)}</td>
              <td className={item.latest_return && item.latest_return < 0 ? "px-4 py-3 text-right text-red-300" : "px-4 py-3 text-right text-success"}>
                {formatPercent(item.latest_return)}
              </td>
              <td className="px-4 py-3 text-right">{formatPercent(item.metrics?.cumulative_return)}</td>
              <td className="px-4 py-3 text-right">{formatPercent(item.metrics?.annualized_volatility)}</td>
              <td className="px-4 py-3 text-right">
                <div className="flex justify-end gap-2">
                  {item.metadata?.is_sample_data ? <Badge tone="mixed">sample</Badge> : null}
                  <Badge tone={item.metadata?.source === "stooq" || item.metadata?.source === "yahoo" ? "positive" : "mixed"}>
                    {item.metadata?.source || item.source || "n/a"}
                  </Badge>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
