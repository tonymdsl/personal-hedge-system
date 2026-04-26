"use client";

import { RefreshCw, Plus } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { api } from "@/lib/api";

export function WatchlistActions() {
  const [symbol, setSymbol] = useState("");
  const [name, setName] = useState("");
  const [assetType, setAssetType] = useState("Equity");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function addAsset(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setStatus(null);
    try {
      await api.addWatchlist({ symbol, name, asset_type: assetType, currency: "USD" });
      setStatus("Asset added and local prices prepared.");
      setSymbol("");
      setName("");
      window.location.reload();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to add asset.");
    } finally {
      setBusy(false);
    }
  }

  async function refresh() {
    setBusy(true);
    setStatus(null);
    try {
      const result = await api.refreshData();
      setStatus(`Updated ${result.symbols} symbols and ${result.rows} rows.`);
      window.location.reload();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to refresh data.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Watchlist controls</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <form className="grid gap-3 md:grid-cols-[1fr_2fr_1fr_auto]" onSubmit={addAsset}>
          <Input placeholder="AAPL" value={symbol} onChange={(event) => setSymbol(event.target.value)} required />
          <Input placeholder="Asset name" value={name} onChange={(event) => setName(event.target.value)} />
          <Select value={assetType} onChange={(event) => setAssetType(event.target.value)}>
            <option>Equity</option>
            <option>ETF</option>
            <option>Macro</option>
            <option>Crypto</option>
          </Select>
          <Button disabled={busy} type="submit">
            <Plus className="h-4 w-4" />
            Add
          </Button>
        </form>
        <div className="flex items-center gap-3">
          <Button disabled={busy} onClick={refresh} type="button" variant="secondary">
            <RefreshCw className="h-4 w-4" />
            Refresh data
          </Button>
          {status ? <span className="text-sm text-muted-foreground">{status}</span> : null}
        </div>
      </CardContent>
    </Card>
  );
}
