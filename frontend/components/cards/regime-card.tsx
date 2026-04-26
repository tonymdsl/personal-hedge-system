import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Regime } from "@/lib/types";
import { formatNumber, formatPercent } from "@/lib/utils";

export function RegimeCard({ regime }: { regime: Regime }) {
  return (
    <Card className="h-full">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Market regime</CardTitle>
          <Badge tone={regime.regime === "risk_on" ? "positive" : regime.regime === "market_stress" ? "negative" : "mixed"}>
            {regime.regime.replace("_", " ")}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <div className="text-3xl font-semibold">{formatPercent(regime.confidence)}</div>
          <div className="text-sm text-muted-foreground">confidence</div>
        </div>
        <div className="grid gap-2 text-sm">
          {Object.entries(regime.evidence).map(([key, value]) => (
            <div key={key} className="flex items-center justify-between border-b border-border pb-2">
              <span className="text-muted-foreground">{key.replaceAll("_", " ")}</span>
              <span>{String(value)}</span>
            </div>
          ))}
        </div>
        <div className="grid gap-2 text-sm">
          <div className="text-xs uppercase tracking-[0.14em] text-muted-foreground">Values</div>
          {Object.entries(regime.values).map(([key, value]) => (
            <div key={key} className="flex items-center justify-between border-b border-border pb-2">
              <span className="text-muted-foreground">{key.replaceAll("_", " ")}</span>
              <span>{key.includes("volatility") || key.includes("drawdown") ? formatPercent(value) : formatNumber(value, 2)}</span>
            </div>
          ))}
        </div>
        <div className="grid gap-2 text-sm">
          <div className="text-xs uppercase tracking-[0.14em] text-muted-foreground">Thresholds</div>
          {Object.entries(regime.thresholds).map(([key, value]) => (
            <div key={key} className="flex items-center justify-between border-b border-border pb-2">
              <span className="text-muted-foreground">{key.replaceAll("_", " ")}</span>
              <span>{formatPercent(value)}</span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
