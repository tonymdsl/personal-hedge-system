import fs from "node:fs/promises";
import path from "node:path";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";
const OUT_DIR = path.resolve("published", "here-now");

async function getJson(pathname) {
  const response = await fetch(`${API_BASE_URL}${pathname}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch ${pathname}: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

function pct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function num(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  return Number(value).toFixed(digits);
}

function money(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(Number(value));
}

function date(value) {
  if (!value) return "n/a";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", { day: "2-digit", month: "short", year: "numeric" }).format(parsed);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function sparkline(points) {
  const values = points.map((point) => Number(point.value)).filter((value) => Number.isFinite(value));
  if (values.length < 2) return "";
  const width = 980;
  const height = 260;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = width / (values.length - 1);
  const d = values
    .map((value, index) => {
      const x = index * step;
      const y = height - ((value - min) / range) * height;
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="SPY cumulative performance"><path d="${d}" fill="none" stroke="#38bdf8" stroke-width="4"/><line x1="0" y1="${height}" x2="${width}" y2="${height}" stroke="#1f2937"/></svg>`;
}

function badge(text, tone = "neutral") {
  return `<span class="badge ${tone}">${escapeHtml(text)}</span>`;
}

function metadataBlock(metadata) {
  if (!metadata) return "";
  return `
    <div class="metadata">
      <div><span>Source</span><strong>${escapeHtml(metadata.source)}</strong></div>
      <div><span>Last updated</span><strong>${escapeHtml(date(metadata.last_updated))}</strong></div>
      <div><span>Range start</span><strong>${escapeHtml(date(metadata.data_range_start))}</strong></div>
      <div><span>Range end</span><strong>${escapeHtml(date(metadata.data_range_end))}</strong></div>
      <div><span>Price type</span><strong>${escapeHtml(metadata.price_type)}</strong></div>
      <div><span>Sample data</span><strong>${metadata.is_sample_data ? "true" : "false"}</strong></div>
    </div>`;
}

function renderSite({ dashboard, report }) {
  const spy = dashboard.watchlist.find((item) => item.symbol === "SPY") || dashboard.watchlist[0];
  const sampleAssets = dashboard.watchlist.filter((item) => item.metadata?.is_sample_data).map((item) => item.symbol);
  const generatedAt = new Date().toISOString();
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Personal Hedge System</title>
  <style>
    :root { color-scheme: dark; --bg: #080b10; --card: #0d1117; --muted: #111827; --border: #1f2937; --text: #e5e7eb; --sub: #94a3b8; --sky: #38bdf8; --green: #22c55e; --red: #ef4444; --amber: #f59e0b; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: radial-gradient(circle at top left, rgba(56,189,248,.08), transparent 30%), var(--bg); color: var(--text); }
    .shell { display: grid; grid-template-columns: 260px 1fr; min-height: 100vh; }
    aside { border-right: 1px solid var(--border); padding: 24px; position: sticky; top: 0; height: 100vh; background: rgba(8,11,16,.92); }
    nav a { display: block; color: var(--sub); padding: 10px 12px; border-radius: 8px; text-decoration: none; margin: 4px 0; }
    nav a:hover { color: var(--text); background: var(--muted); }
    main { padding: 28px; max-width: 1400px; width: 100%; }
    h1, h2, h3 { letter-spacing: 0; margin: 0; }
    h1 { font-size: 28px; }
    h2 { font-size: 20px; margin-bottom: 14px; }
    p { color: var(--sub); }
    section { margin-top: 28px; }
    .top { display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; }
    .grid { display: grid; gap: 16px; }
    .grid4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    .grid2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .card { border: 1px solid var(--border); background: var(--card); border-radius: 10px; padding: 20px; box-shadow: 0 18px 48px rgba(0,0,0,.22); }
    .metric .label { color: var(--sub); text-transform: uppercase; font-size: 12px; letter-spacing: .12em; }
    .metric .value { margin-top: 10px; font-size: 28px; font-weight: 700; }
    .badge { display: inline-flex; border: 1px solid var(--border); background: var(--muted); border-radius: 7px; padding: 3px 8px; font-size: 12px; color: var(--sub); }
    .badge.good { border-color: rgba(34,197,94,.4); background: rgba(34,197,94,.1); color: var(--green); }
    .badge.bad { border-color: rgba(239,68,68,.4); background: rgba(239,68,68,.1); color: #fca5a5; }
    .badge.warn { border-color: rgba(245,158,11,.4); background: rgba(245,158,11,.1); color: #fcd34d; }
    .metadata { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
    .metadata div { border: 1px solid var(--border); border-radius: 8px; padding: 10px; display: flex; justify-content: space-between; gap: 12px; }
    .metadata span { color: var(--sub); }
    table { width: 100%; border-collapse: collapse; overflow: hidden; border-radius: 10px; }
    th { text-align: left; color: var(--sub); background: var(--muted); font-size: 12px; letter-spacing: .12em; text-transform: uppercase; }
    th, td { padding: 12px 14px; border-bottom: 1px solid var(--border); }
    td.num, th.num { text-align: right; }
    .pos { color: var(--green); }
    .neg { color: #fca5a5; }
    .warning { border: 1px solid rgba(245,158,11,.35); background: rgba(245,158,11,.1); color: #fde68a; border-radius: 10px; padding: 14px; }
    svg { width: 100%; height: auto; display: block; }
    ul { margin: 0; padding-left: 20px; color: var(--sub); }
    @media (max-width: 900px) { .shell { grid-template-columns: 1fr; } aside { position: relative; height: auto; } .grid4, .grid2, .metadata { grid-template-columns: 1fr; } main { padding: 18px; } }
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <h3>Personal Hedge</h3>
      <p>Published research snapshot</p>
      <nav>
        <a href="#dashboard">Dashboard</a>
        <a href="#regime">Regime</a>
        <a href="#watchlist">Watchlist</a>
        <a href="#risk">Risk</a>
        <a href="#report">Daily Report</a>
        <a href="#ft">FT Research</a>
      </nav>
    </aside>
    <main>
      <div class="top">
        <div>
          <h1>Personal Hedge System</h1>
          <p>Paper analytics only. Static publication generated from the local FastAPI backend at ${escapeHtml(date(generatedAt))}.</p>
        </div>
        ${badge(dashboard.regime.regime.replace("_", " "), dashboard.regime.regime === "risk_on" ? "good" : dashboard.regime.regime === "market_stress" ? "bad" : "warn")}
      </div>
      ${sampleAssets.length ? `<section><div class="warning">Sample data active for: ${escapeHtml(sampleAssets.join(", "))}. Sample rows are flagged in metadata.</div></section>` : ""}

      <section id="dashboard" class="grid grid4">
        <div class="card metric"><div class="label">SPY price</div><div class="value">${money(spy?.latest_price)}</div></div>
        <div class="card metric"><div class="label">SPY cumulative</div><div class="value">${pct(spy?.metrics?.cumulative_return)}</div></div>
        <div class="card metric"><div class="label">SPY volatility</div><div class="value">${pct(spy?.metrics?.annualized_volatility)}</div></div>
        <div class="card metric"><div class="label">SPY max drawdown</div><div class="value">${pct(spy?.metrics?.max_drawdown)}</div></div>
      </section>

      <section class="card">
        <h2>SPY cumulative performance</h2>
        ${sparkline(dashboard.performance)}
      </section>

      <section class="card">
        <h2>SPY data metadata</h2>
        ${metadataBlock(spy?.metadata)}
      </section>

      <section id="regime" class="grid grid2">
        <div class="card">
          <h2>Market regime</h2>
          <p>Confidence: <strong>${pct(dashboard.regime.confidence)}</strong></p>
          <table><tbody>${Object.entries(dashboard.regime.evidence).map(([k, v]) => `<tr><td>${escapeHtml(k.replaceAll("_", " "))}</td><td class="num">${escapeHtml(v)}</td></tr>`).join("")}</tbody></table>
        </div>
        <div class="card">
          <h2>Regime values</h2>
          <table><tbody>${Object.entries(dashboard.regime.values).map(([k, v]) => `<tr><td>${escapeHtml(k.replaceAll("_", " "))}</td><td class="num">${k.includes("volatility") || k.includes("drawdown") ? pct(v) : num(v, 2)}</td></tr>`).join("")}</tbody></table>
        </div>
      </section>

      <section id="watchlist" class="card">
        <h2>Watchlist</h2>
        <table>
          <thead><tr><th>Symbol</th><th>Name</th><th class="num">Price</th><th class="num">1D</th><th class="num">Cum. return</th><th class="num">Vol.</th><th class="num">Source</th></tr></thead>
          <tbody>
            ${dashboard.watchlist.map((item) => `<tr><td><strong>${escapeHtml(item.symbol)}</strong></td><td>${escapeHtml(item.name)}</td><td class="num">${money(item.latest_price)}</td><td class="num ${Number(item.latest_return) < 0 ? "neg" : "pos"}">${pct(item.latest_return)}</td><td class="num">${pct(item.metrics?.cumulative_return)}</td><td class="num">${pct(item.metrics?.annualized_volatility)}</td><td class="num">${escapeHtml(item.metadata?.source || item.source || "n/a")}${item.metadata?.is_sample_data ? " / sample" : ""}</td></tr>`).join("")}
          </tbody>
        </table>
      </section>

      <section id="risk" class="grid grid2">
        <div class="card">
          <h2>Risk snapshot</h2>
          <p>Assumption: ${escapeHtml(dashboard.risk.assumption.replaceAll("_", " "))}</p>
          <table><tbody>
            <tr><td>Portfolio volatility</td><td class="num">${pct(dashboard.risk.portfolio_volatility)}</td></tr>
            <tr><td>Portfolio max drawdown</td><td class="num">${pct(dashboard.risk.portfolio_max_drawdown)}</td></tr>
            <tr><td>Current portfolio drawdown</td><td class="num">${pct(dashboard.risk.current_portfolio_drawdown)}</td></tr>
            <tr><td>Total exposure</td><td class="num">${pct(dashboard.risk.total_exposure)}</td></tr>
          </tbody></table>
        </div>
        <div class="card">
          <h2>Risk contribution</h2>
          <table><thead><tr><th>Asset</th><th class="num">Weight</th><th class="num">Contribution</th></tr></thead><tbody>${dashboard.risk.risk_contribution.map((item) => `<tr><td>${escapeHtml(item.symbol)}</td><td class="num">${pct(item.weight)}</td><td class="num">${pct(item.contribution)}</td></tr>`).join("")}</tbody></table>
        </div>
      </section>

      <section id="report" class="grid grid2">
        <div class="card">
          <h2>Daily Report</h2>
          <p>Market regime: <strong>${escapeHtml(report.market_regime)}</strong></p>
          <h3>Portfolio implications</h3>
          <ul>${report.portfolio_implications.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
        </div>
        <div class="card">
          <h2>Top 5 movers</h2>
          <table><tbody>${report.top_movers.map((item) => `<tr><td>${escapeHtml(item.symbol)}</td><td class="num ${Number(item.latest_return) < 0 ? "neg" : "pos"}">${pct(item.latest_return)}</td></tr>`).join("")}</tbody></table>
        </div>
      </section>

      <section id="ft" class="card">
        <h2>Recent FT Research Notes</h2>
        ${report.ft_notes.length ? report.ft_notes.map((note) => `<div class="card" style="box-shadow:none;margin-top:12px"><strong>${escapeHtml(note.title)}</strong><p>${escapeHtml(note.summary)}</p>${badge(note.sentiment)} ${badge(note.impact)} ${badge(`${note.portfolio_relevance} relevance`)}</div>`).join("") : `<p>No FT notes in the local database yet.</p>`}
      </section>

      <section class="warning">This public page is a static here.now snapshot. The full interactive app still runs locally with FastAPI and Next.js. No orders are executed.</section>
    </main>
  </div>
</body>
</html>`;
}

const [dashboard, report] = await Promise.all([getJson("/api/dashboard"), getJson("/api/report/daily")]);
await fs.rm(OUT_DIR, { recursive: true, force: true });
await fs.mkdir(OUT_DIR, { recursive: true });
await fs.writeFile(path.join(OUT_DIR, "index.html"), renderSite({ dashboard, report }), "utf8");
console.log(OUT_DIR);
