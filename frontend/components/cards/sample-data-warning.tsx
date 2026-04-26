import type { DataMetadata } from "@/lib/types";

export function SampleDataWarning({ metadata }: { metadata?: DataMetadata }) {
  if (!metadata?.is_sample_data) return null;
  return (
    <div className="rounded-lg border border-warning/30 bg-warning/10 p-4 text-sm text-amber-100">
      This view is using sample_data because the free market data source failed or has not been refreshed yet.
    </div>
  );
}

export function SampleDataPortfolioWarning({ items }: { items: { metadata?: DataMetadata; symbol: string }[] }) {
  const sampleSymbols = items.filter((item) => item.metadata?.is_sample_data).map((item) => item.symbol);
  if (!sampleSymbols.length) return null;
  return (
    <div className="rounded-lg border border-warning/30 bg-warning/10 p-4 text-sm text-amber-100">
      Sample data active for: {sampleSymbols.join(", ")}. Real and sample rows are flagged visibly by asset.
    </div>
  );
}
