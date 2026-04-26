export type RegimeName = "risk_on" | "risk_off" | "market_stress";

export type Metrics = {
  cumulative_return: number;
  annualized_volatility: number;
  current_drawdown: number;
  max_drawdown: number;
  sharpe_ratio: number;
  best_day: number;
  worst_day: number;
};

export type DataMetadata = {
  source: string;
  last_updated: string | null;
  data_range_start: string | null;
  data_range_end: string | null;
  price_type: "close" | "adjusted_close" | string;
  is_sample_data: boolean;
};

export type WatchlistItem = {
  symbol: string;
  name: string;
  asset_type: string;
  currency: string;
  created_at?: string;
  latest_price?: number | null;
  latest_return?: number | null;
  source?: string;
  updated_at?: string;
  metrics?: Metrics;
  metadata?: DataMetadata;
};

export type PricePoint = {
  symbol: string;
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  source: string;
  updated_at?: string;
};

export type PricePayload = {
  symbol: string;
  metadata: DataMetadata;
  prices: PricePoint[];
};

export type Regime = {
  regime: RegimeName;
  confidence: number;
  evidence: Record<string, boolean>;
  thresholds: Record<string, number>;
  values: Record<string, number>;
  updated_at: string;
};

export type FTNote = {
  id: string;
  title: string;
  url?: string | null;
  published_date: string;
  summary: string;
  assets: string[];
  sectors: string[];
  macro_themes: string[];
  sentiment: "positive" | "neutral" | "negative" | "mixed";
  impact: "low" | "medium" | "high";
  horizon: "days" | "weeks" | "months";
  portfolio_relevance: "low" | "medium" | "high";
  notes?: string | null;
  created_at?: string;
};

export type RiskContribution = {
  symbol: string;
  weight: number;
  volatility: number;
  contribution: number;
};

export type RiskSnapshot = {
  assumption: string;
  max_asset_weight: number;
  total_exposure: number;
  current_drawdown: number;
  portfolio_volatility: number;
  portfolio_max_drawdown: number;
  current_portfolio_drawdown: number;
  risk_contribution: RiskContribution[];
  concentration_alerts: string[];
  drawdown_alerts: string[];
  alerts: string[];
};

export type DashboardPayload = {
  watchlist: WatchlistItem[];
  regime: Regime;
  performance: { date: string; value: number }[];
  movers: WatchlistItem[];
  risk: RiskSnapshot;
  ft_notes: FTNote[];
};

export type DailyReport = {
  market_regime: RegimeName;
  confidence: number;
  regime: Regime;
  top_movers: WatchlistItem[];
  risk_alerts: string[];
  watchlist_summary: WatchlistItem[];
  ft_notes: FTNote[];
  portfolio_implications: string[];
  updated_at: string;
};
