import { DrawdownChart } from "@/components/charts/drawdown-chart";
import { PriceChart } from "@/components/charts/price-chart";
import { ReturnsChart } from "@/components/charts/returns-chart";
import { DataMetadataCard } from "@/components/cards/data-metadata-card";
import { MetricCard } from "@/components/cards/metric-card";
import { SampleDataWarning } from "@/components/cards/sample-data-warning";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { formatPercent } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function AssetPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  try {
    const [pricePayload, metrics] = await Promise.all([api.prices(symbol), api.metrics(symbol)]);
    const prices = pricePayload.prices;
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-2xl font-semibold tracking-normal">{symbol.toUpperCase()} analysis</h2>
          <p className="mt-1 text-sm text-muted-foreground">Price history, drawdown and return profile.</p>
        </div>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="Cumulative return" value={formatPercent(metrics.cumulative_return)} tone={metrics.cumulative_return >= 0 ? "positive" : "negative"} />
          <MetricCard label="Annualized vol" value={formatPercent(metrics.annualized_volatility)} />
          <MetricCard label="Max drawdown" value={formatPercent(metrics.max_drawdown)} tone="negative" />
          <MetricCard label="Sharpe" value={metrics.sharpe_ratio.toFixed(2)} />
          <MetricCard label="Current drawdown" value={formatPercent(metrics.current_drawdown)} tone="warning" />
          <MetricCard label="Best day" value={formatPercent(metrics.best_day)} tone="positive" />
          <MetricCard label="Worst day" value={formatPercent(metrics.worst_day)} tone="negative" />
        </div>
        <SampleDataWarning metadata={pricePayload.metadata} />
        <DataMetadataCard metadata={pricePayload.metadata} title={`${symbol.toUpperCase()} data metadata`} />
        <PriceChart data={prices} symbol={symbol.toUpperCase()} />
        <div className="grid gap-6 xl:grid-cols-2">
          <DrawdownChart data={prices} />
          <ReturnsChart data={prices} />
        </div>
      </div>
    );
  } catch (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Asset unavailable</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">{error instanceof Error ? error.message : "Unable to load asset."}</CardContent>
      </Card>
    );
  }
}
