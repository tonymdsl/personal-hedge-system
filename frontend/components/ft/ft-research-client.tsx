"use client";

import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type { FTNote } from "@/lib/types";

const splitList = (value: string) => value.split(",").map((item) => item.trim()).filter(Boolean);

export function FTResearchClient() {
  const [notes, setNotes] = useState<FTNote[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sentimentFilter, setSentimentFilter] = useState("all");
  const [impactFilter, setImpactFilter] = useState("all");
  const [horizonFilter, setHorizonFilter] = useState("all");
  const [relevanceFilter, setRelevanceFilter] = useState("all");
  const [assetFilter, setAssetFilter] = useState("");

  useEffect(() => {
    api.ftNotes().then(setNotes).catch((error) => setStatus(error instanceof Error ? error.message : "Unable to load FT notes."));
  }, []);

  const filtered = useMemo(
    () =>
      notes.filter((note) => {
        const sentimentOk = sentimentFilter === "all" || note.sentiment === sentimentFilter;
        const impactOk = impactFilter === "all" || note.impact === impactFilter;
        const horizonOk = horizonFilter === "all" || note.horizon === horizonFilter;
        const relevanceOk = relevanceFilter === "all" || note.portfolio_relevance === relevanceFilter;
        const assetOk = !assetFilter || note.assets.some((asset) => asset.toLowerCase().includes(assetFilter.toLowerCase()));
        return sentimentOk && impactOk && horizonOk && relevanceOk && assetOk;
      }),
    [assetFilter, horizonFilter, impactFilter, notes, relevanceFilter, sentimentFilter]
  );

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setStatus(null);
    const form = new FormData(event.currentTarget);
    try {
      const created = await api.addFtNote({
        title: String(form.get("title")),
        url: String(form.get("url") || ""),
        published_date: String(form.get("published_date")),
        summary: String(form.get("summary")),
        assets: splitList(String(form.get("assets") || "")),
        sectors: splitList(String(form.get("sectors") || "")),
        macro_themes: splitList(String(form.get("macro_themes") || "")),
        sentiment: form.get("sentiment") as FTNote["sentiment"],
        impact: form.get("impact") as FTNote["impact"],
        horizon: form.get("horizon") as FTNote["horizon"],
        portfolio_relevance: form.get("portfolio_relevance") as FTNote["portfolio_relevance"],
        notes: String(form.get("notes") || "")
      });
      setNotes((current) => [created, ...current]);
      event.currentTarget.reset();
      setStatus("Manual FT note saved.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to save FT note.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Manual FT Research Input</CardTitle>
        </CardHeader>
        <CardContent>
          <form className="grid gap-4" onSubmit={submit}>
            <div className="grid gap-4 md:grid-cols-2">
              <Input name="title" placeholder="Title" required />
              <Input name="url" placeholder="FT link" />
              <Input name="published_date" type="date" required />
              <Input name="assets" placeholder="Assets: SPY, TLT" />
              <Input name="sectors" placeholder="Sectors" />
              <Input name="macro_themes" placeholder="Macro themes" />
            </div>
            <Textarea name="summary" placeholder="Manual summary only" required />
            <Textarea name="notes" placeholder="Personal notes" />
            <div className="grid gap-4 md:grid-cols-5">
              <Select name="sentiment" defaultValue="neutral">
                <option value="positive">positive</option>
                <option value="neutral">neutral</option>
                <option value="negative">negative</option>
                <option value="mixed">mixed</option>
              </Select>
              <Select name="impact" defaultValue="medium">
                <option value="low">low</option>
                <option value="medium">medium</option>
                <option value="high">high</option>
              </Select>
              <Select name="horizon" defaultValue="weeks">
                <option value="days">days</option>
                <option value="weeks">weeks</option>
                <option value="months">months</option>
              </Select>
              <Select name="portfolio_relevance" defaultValue="medium">
                <option value="low">low relevance</option>
                <option value="medium">medium relevance</option>
                <option value="high">high relevance</option>
              </Select>
              <Button disabled={busy} type="submit">Save note</Button>
            </div>
            {status ? <div className="text-sm text-muted-foreground">{status}</div> : null}
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Research notes</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-5">
            <Select value={sentimentFilter} onChange={(event) => setSentimentFilter(event.target.value)}>
              <option value="all">All sentiment</option>
              <option value="positive">positive</option>
              <option value="neutral">neutral</option>
              <option value="negative">negative</option>
              <option value="mixed">mixed</option>
            </Select>
            <Select value={impactFilter} onChange={(event) => setImpactFilter(event.target.value)}>
              <option value="all">All impact</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </Select>
            <Select value={horizonFilter} onChange={(event) => setHorizonFilter(event.target.value)}>
              <option value="all">All horizons</option>
              <option value="days">days</option>
              <option value="weeks">weeks</option>
              <option value="months">months</option>
            </Select>
            <Select value={relevanceFilter} onChange={(event) => setRelevanceFilter(event.target.value)}>
              <option value="all">All relevance</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </Select>
            <Input placeholder="Filter by asset" value={assetFilter} onChange={(event) => setAssetFilter(event.target.value)} />
          </div>
          <div className="space-y-3">
            {filtered.length ? (
              filtered.map((note) => (
                <div key={note.id} className="rounded-lg border border-border p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="font-medium">{note.title}</div>
                      <div className="mt-1 text-sm text-muted-foreground">{note.summary}</div>
                    </div>
                    <div className="flex gap-2">
                      <Badge tone={note.sentiment}>{note.sentiment}</Badge>
                      <Badge tone={note.impact}>{note.impact}</Badge>
                      <Badge tone={note.portfolio_relevance}>{note.portfolio_relevance} relevance</Badge>
                    </div>
                  </div>
                  <TagRow label="Assets" values={note.assets} />
                  <TagRow label="Sectors" values={note.sectors} />
                  <TagRow label="Themes" values={note.macro_themes} />
                  <div className="mt-3 text-xs text-muted-foreground">Horizon: {note.horizon}</div>
                </div>
              ))
            ) : (
              <div className="rounded-lg border border-border p-6 text-sm text-muted-foreground">No FT notes match the current filters.</div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function TagRow({ label, values }: { label: string; values: string[] }) {
  if (!values.length) return null;
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
      <span className="text-muted-foreground">{label}</span>
      {values.map((value) => (
        <Badge key={`${label}-${value}`}>{value}</Badge>
      ))}
    </div>
  );
}
