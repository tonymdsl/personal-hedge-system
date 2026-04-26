import type { DailyReport, DashboardPayload, FTNote, Metrics, PricePayload, Regime, WatchlistItem } from "@/lib/types";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {})
    }
  });
  if (!response.ok) {
    throw new Error(`API ${response.status}: ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  dashboard: () => request<DashboardPayload>("/api/dashboard"),
  watchlist: () => request<WatchlistItem[]>("/api/watchlist"),
  addWatchlist: (payload: { symbol: string; name?: string; asset_type?: string; currency?: string }) =>
    request<WatchlistItem>("/api/watchlist", { method: "POST", body: JSON.stringify(payload) }),
  refreshData: () => request<{ symbols: number; rows: number; updated: unknown[] }>("/api/data/refresh", { method: "POST" }),
  prices: (symbol: string) => request<PricePayload>(`/api/prices/${encodeURIComponent(symbol)}`),
  metrics: (symbol: string) => request<Metrics>(`/api/metrics/${encodeURIComponent(symbol)}`),
  regime: () => request<Regime>("/api/regime"),
  dailyReport: () => request<DailyReport>("/api/report/daily"),
  ftNotes: () => request<FTNote[]>("/api/ft-notes"),
  addFtNote: (payload: Omit<FTNote, "id" | "created_at">) =>
    request<FTNote>("/api/ft-notes", { method: "POST", body: JSON.stringify(payload) })
};
