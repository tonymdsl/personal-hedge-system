import { WatchlistActions } from "@/components/watchlist/watchlist-actions";
import { SampleDataPortfolioWarning } from "@/components/cards/sample-data-warning";
import { WatchlistTable } from "@/components/tables/watchlist-table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function WatchlistPage() {
  try {
    const dashboard = await api.dashboard();
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-2xl font-semibold tracking-normal">Watchlist</h2>
          <p className="mt-1 text-sm text-muted-foreground">Track local market data, add assets and refresh free-source prices.</p>
        </div>
        <WatchlistActions />
        <SampleDataPortfolioWarning items={dashboard.watchlist} />
        <Card>
          <CardHeader>
            <CardTitle>Assets</CardTitle>
          </CardHeader>
          <CardContent>
            <WatchlistTable items={dashboard.watchlist} />
          </CardContent>
        </Card>
      </div>
    );
  } catch (error) {
    return <div className="rounded-lg border border-border p-6 text-sm text-muted-foreground">Unable to load watchlist. {error instanceof Error ? error.message : null}</div>;
  }
}
