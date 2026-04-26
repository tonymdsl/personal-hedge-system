import { RegimeCard } from "@/components/cards/regime-card";
import { SampleDataPortfolioWarning } from "@/components/cards/sample-data-warning";
import { WatchlistTable } from "@/components/tables/watchlist-table";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { formatDateTime, formatPercent } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function ReportPage() {
  try {
    const report = await api.dailyReport();
    return (
      <div className="space-y-6">
        <div className="flex flex-col justify-between gap-3 md:flex-row md:items-end">
          <div>
            <h2 className="text-2xl font-semibold tracking-normal">Daily Report</h2>
            <p className="mt-1 text-sm text-muted-foreground">Rules-based market context, movers, risk alerts and research notes.</p>
          </div>
          <div className="text-sm text-muted-foreground">Updated {formatDateTime(report.updated_at)}</div>
        </div>

        <SampleDataPortfolioWarning items={report.watchlist_summary} />

        <div className="grid gap-6 xl:grid-cols-[1fr_1.3fr]">
          <RegimeCard regime={report.regime} />
          <Card>
            <CardHeader>
              <CardTitle>Portfolio implications</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="text-3xl font-semibold">{formatPercent(report.confidence)}</div>
              <div className="text-sm text-muted-foreground">Regime confidence</div>
              {report.portfolio_implications.map((item) => (
                <div key={item} className="rounded-md border border-border p-3 text-sm">
                  {item}
                </div>
              ))}
            </CardContent>
          </Card>
        </div>

        <div className="grid gap-6 xl:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Top 5 movers</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {report.top_movers.map((item) => (
                <div key={item.symbol} className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                  <span className="font-medium">{item.symbol}</span>
                  <span className={(item.latest_return || 0) < 0 ? "text-red-300" : "text-success"}>{formatPercent(item.latest_return)}</span>
                </div>
              ))}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Risk alerts</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {report.risk_alerts.length ? (
                report.risk_alerts.map((alert) => (
                  <div key={alert} className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-amber-200">
                    {alert}
                  </div>
                ))
              ) : (
                <div className="rounded-lg border border-border p-6 text-sm text-muted-foreground">No active risk alerts.</div>
              )}
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Watchlist summary</CardTitle>
          </CardHeader>
          <CardContent>
            <WatchlistTable items={report.watchlist_summary} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent FT notes</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {report.ft_notes.length ? (
              report.ft_notes.map((note) => (
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
              ))
            ) : (
              <div className="rounded-lg border border-border p-6 text-sm text-muted-foreground">No recent FT notes.</div>
            )}
          </CardContent>
        </Card>
      </div>
    );
  } catch (error) {
    return <div className="rounded-lg border border-border p-6 text-sm text-muted-foreground">Unable to load daily report. {error instanceof Error ? error.message : null}</div>;
  }
}
