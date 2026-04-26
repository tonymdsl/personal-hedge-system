import { FTResearchClient } from "@/components/ft/ft-research-client";

export default function FTResearchPage() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-normal">FT Research</h2>
        <p className="mt-1 text-sm text-muted-foreground">Manual research capture only. No scraping, no full article storage, no paywall workarounds.</p>
      </div>
      <FTResearchClient />
    </div>
  );
}
