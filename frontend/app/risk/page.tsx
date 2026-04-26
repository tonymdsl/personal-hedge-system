import { MetricCard } from "@/components/cards/metric-card";
import { SampleDataPortfolioWarning } from "@/components/cards/sample-data-warning";
import { WatchlistTable } from "@/components/tables/watchlist-table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { formatPercent } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function RiskPage() {
  try {
    const dashboard = await api.dashboard();
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-2xl font-semibold tracking-normal">Risk</h2>
          <p className="mt-1 text-sm text-muted-foreground">Equal-weight exposure snapshot, drawdowns and local risk alerts.</p>
        </div>
        <div className="rounded-lg border border-border bg-muted/40 p-4 text-sm text-muted-foreground">
          Assumption: {dashboard.risk.assumption.replaceAll("_", " ")}. No real positions or order execution are modeled.
        </div>
        <SampleDataPortfolioWarning items={dashboard.watchlist} />
        <div className="grid gap-4 md:grid-cols-3">
          <MetricCard label="Max asset weight" value={formatPercent(dashboard.risk.max_asset_weight)} />
          <MetricCard label="Total exposure" value={formatPercent(dashboard.risk.total_exposure)} />
          <MetricCard label="Portfolio vol" value={formatPercent(dashboard.risk.portfolio_volatility)} />
          <MetricCard label="Portfolio max drawdown" value={formatPercent(dashboard.risk.portfolio_max_drawdown)} tone="negative" />
          <MetricCard label="Current portfolio drawdown" value={formatPercent(dashboard.risk.current_portfolio_drawdown)} tone="warning" />
        </div>
        <div className="grid gap-6 xl:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Risk contribution by asset</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-hidden rounded-lg border border-border">
                <table className="w-full text-sm">
                  <thead className="bg-muted/70 text-xs uppercase tracking-[0.12em] text-muted-foreground">
                    <tr>
                      <th className="px-4 py-3 text-left">Asset</th>
                      <th className="px-4 py-3 text-right">Weight</th>
                      <th className="px-4 py-3 text-right">Volatility</th>
                      <th className="px-4 py-3 text-right">Contribution</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dashboard.risk.risk_contribution.map((item) => (
                      <tr key={item.symbol} className="border-t border-border">
                        <td className="px-4 py-3 font-medium">{item.symbol}</td>
                        <td className="px-4 py-3 text-right">{formatPercent(item.weight)}</td>
                        <td className="px-4 py-3 text-right">{formatPercent(item.volatility)}</td>
                        <td className="px-4 py-3 text-right">{formatPercent(item.contribution)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Alerts</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <AlertGroup title="Concentration alerts" alerts={dashboard.risk.concentration_alerts} />
              <AlertGroup title="Drawdown alerts" alerts={dashboard.risk.drawdown_alerts} />
            </CardContent>
          </Card>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>Watchlist risk data</CardTitle>
          </CardHeader>
          <CardContent>
            <WatchlistTable items={dashboard.watchlist} />
          </CardContent>
        </Card>
      </div>
    );
  } catch (error) {
    return <div className="rounded-lg border border-border p-6 text-sm text-muted-foreground">Unable to load risk data. {error instanceof Error ? error.message : null}</div>;
  }
}

function AlertGroup({ title, alerts }: { title: string; alerts: string[] }) {
  return (
    <div>
      <div className="mb-2 text-xs uppercase tracking-[0.14em] text-muted-foreground">{title}</div>
      {alerts.length ? (
        <div className="space-y-2">
          {alerts.map((alert) => (
            <div key={alert} className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-amber-200">
              {alert}
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-md border border-border p-3 text-sm text-muted-foreground">No active alerts.</div>
      )}
    </div>
  );
}
