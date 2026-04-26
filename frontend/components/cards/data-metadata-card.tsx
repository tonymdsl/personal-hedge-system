import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { DataMetadata } from "@/lib/types";
import { formatChartDate, formatDateTime } from "@/lib/utils";

export function DataMetadataCard({ metadata, title = "Data metadata" }: { metadata?: DataMetadata; title?: string }) {
  if (!metadata) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{title}</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">No metadata available.</CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>{title}</CardTitle>
          <Badge tone={metadata.is_sample_data ? "mixed" : "positive"}>{metadata.is_sample_data ? "sample data" : metadata.source}</Badge>
        </div>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm md:grid-cols-2">
        <MetaRow label="Source" value={metadata.source} />
        <MetaRow label="Price type" value={metadata.price_type} />
        <MetaRow label="Last updated" value={formatDateTime(metadata.last_updated)} />
        <MetaRow label="Range start" value={formatChartDate(metadata.data_range_start)} />
        <MetaRow label="Range end" value={formatChartDate(metadata.data_range_end)} />
        <MetaRow label="Sample flag" value={metadata.is_sample_data ? "true" : "false"} />
      </CardContent>
    </Card>
  );
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-md border border-border px-3 py-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right font-medium">{value}</span>
    </div>
  );
}
