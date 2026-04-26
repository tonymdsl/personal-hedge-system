import { MetricCard } from "@/components/cards/metric-card";
import { DataMetadataCard } from "@/components/cards/data-metadata-card";
import { RegimeCard } from "@/components/cards/regime-card";
import { SampleDataPortfolioWarning } from "@/components/cards/sample-data-warning";
import { PerformanceChart } from "@/components/charts/performance-chart";
import { WatchlistTable } from "@/components/tables/watchlist-table";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { formatCurrency, formatPercent } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  try {
    const dashboard = await api.dashboard();
    const spy = dashboard.watchlist.find((item) => item.symbol === "SPY") || dashboard.watchlist[0];
    return (
      <div className="space-y-6">
        <div className="flex flex-col justify-between gap-3 md:flex-row md:items-end">
          <div>
            <h2 className="text-2xl font-semibold tracking-normal">Dashboard</h2>
            <p className="mt-1 text-sm text-muted-foreground">Market overview, regime evidence, watchlist performance and recent manual research.</p>
          </div>
          <Badge tone={dashboard.regime.regime === "risk_on" ? "positive" : "mixed"}>{dashboard.regime.regime.replace("_", " ")}</Badge>
        </div>

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="SPY price" value={formatCurrency(spy?.latest_price)} detail={spy?.metadata ? `source: ${spy.metadata.source}` : undefined} />
          <MetricCard label="SPY cumulative" value={formatPercent(spy?.metrics?.cumulative_return)} tone={(spy?.metrics?.cumulative_return || 0) >= 0 ? "positive" : "negative"} />
          <MetricCard label="SPY volatility" value={formatPercent(spy?.metrics?.annualized_volatility)} />
          <MetricCard label="SPY max drawdown" value={formatPercent(spy?.metrics?.max_drawdown)} tone="negative" />
        </div>
        <SampleDataPortfolioWarning items={dashboard.watchlist} />

        <div className="grid gap-6 xl:grid-cols-[1.8fr_1fr]">
          <PerformanceChart data={dashboard.performance} title="SPY cumulative performance" />
          <RegimeCard regime={dashboard.regime} />
        </div>
        <DataMetadataCard metadata={spy?.metadata} title="SPY benchmark metadata" />

        <div className="grid gap-6 xl:grid-cols-[1.5fr_1fr]">
          <Card>
            <CardHeader>
              <CardTitle>Watchlist</CardTitle>
            </CardHeader>
            <CardContent>
              <WatchlistTable items={dashboard.watchlist} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Largest moves</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {dashboard.movers.map((item) => (
                <div key={item.symbol} className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                  <span className="font-medium">{item.symbol}</span>
                  <span className={(item.latest_return || 0) < 0 ? "text-red-300" : "text-success"}>{formatPercent(item.latest_return)}</span>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Recent FT notes</CardTitle>
          </CardHeader>
          <CardContent>
            {dashboard.ft_notes.length ? (
              <div className="grid gap-3">
                {dashboard.ft_notes.map((note) => (
                  <div key={note.id} className="rounded-md border border-border p-4">
                    <div className="flex items-center justify-between gap-3">
                      <div className="font-medium">{note.title}</div>
                      <div className="flex gap-2">
                        <Badge tone={note.sentiment}>{note.sentiment}</Badge>
                        <Badge tone={note.portfolio_relevance}>{note.portfolio_relevance}</Badge>
                      </div>
                    </div>
                    <div className="mt-1 text-sm text-muted-foreground">{note.summary}</div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-lg border border-border p-6 text-sm text-muted-foreground">No manual FT notes yet.</div>
            )}
          </CardContent>
        </Card>
      </div>
    );
  } catch (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Backend unavailable</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Start the FastAPI server on port 8000. {error instanceof Error ? error.message : null}
        </CardContent>
      </Card>
    );
  }
}
