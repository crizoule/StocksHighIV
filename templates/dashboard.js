(() => {
  "use strict";

  const DATA = JSON.parse(document.getElementById("payload").textContent);
  const S = DATA.settings;
  let watched = new Set(DATA.watchlist || []);
  let U = DATA.universe;
  const CAP_SPLIT = S.large_market_cap_usd || 100e9;
  const CAP_LABEL = { mid: "US$1B–$100B", large: "US$100B+", watch: "Watchlist" };
  const TOP_N = S.top_n;
  const FRAMES = S.price_frames || ["1H", "4H", "1D", "1W"];
  const FRAME_NOTE = {
    "1H": "hourly bars, past week",
    "4H": "4-hour bars, past month",
    "1D": "daily bars, 6 months",
    "1W": "weekly bars, 2 years",
    "1M": "monthly bars, 5 years",
  };
  const FRAME_WINDOW = { "1H": "1 wk", "4H": "1 mo", "1D": "6 mo", "1W": "2 yr", "1M": "5 yr" };
  const $ = (id) => document.getElementById(id);

  const state = { cap: "mid", market: "all", hqOnly: false, frame: FRAMES.includes("1D") ? "1D" : FRAMES[0], sort: { key: "iv30", dir: "desc" }, open: new Set() };
  try {
    const saved = JSON.parse(localStorage.getItem("ivl-view") || "null");
    if (saved && ["mid", "large", "watch"].includes(saved.cap)) state.cap = saved.cap;
    if (saved && ["all", "us", "tsx"].includes(saved.market)) state.market = saved.market;
    if (saved && FRAMES.includes(saved.frame)) state.frame = saved.frame;
    if (saved) state.hqOnly = Boolean(saved.hqOnly);
  } catch (err) { /* storage unavailable: defaults apply */ }
  const persist = () => {
    try {
      localStorage.setItem("ivl-view", JSON.stringify({ cap: state.cap, market: state.market, hqOnly: state.hqOnly, frame: state.frame }));
    } catch (err) { /* ignore */ }
  };

  /* ---------- formatting ---------- */
  const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
  const nf1 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const nf1max = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
  const nf2 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const shortDate = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  const longDate = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  const stamp = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/Toronto", timeZoneName: "short" });
  const dateET = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "America/New_York" });
  const stampET = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/New_York" });

  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const day = (iso) => new Date(`${iso}T00:00:00Z`);
  const fmtDay = (iso) => (iso ? shortDate.format(day(iso)) : "—");
  const pct = (v) => (isNum(v) ? `${nf1.format(v)}%` : "—");
  const money = (v) => (!isNum(v) ? "—" : v >= 1000 ? nf0.format(v) : nf2.format(v));
  const signed = (v) => `${v >= 0 ? "▲" : "▼"}${nf1.format(Math.abs(v))}%`;
  const ordinal = (n) => {
    const s = ["th", "st", "nd", "rd"], v = n % 100;
    return `${n}${s[(v - 20) % 10] || s[v] || s[0]}`;
  };
  const compact = (v, prefix = "") => {
    if (!isNum(v)) return "—";
    for (const [size, unit] of [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "K"]]) {
      if (Math.abs(v) >= size) {
        const scaled = v / size;
        return `${prefix}${scaled >= 100 ? nf0.format(scaled) : nf1max.format(scaled)}${unit}`;
      }
    }
    return `${prefix}${nf0.format(v)}`;
  };
  const windowLabel = (r) => {
    const hist = r.iv_history || [];
    if (hist.length < 2) return "";
    const weeks = Math.round((day(hist[hist.length - 1][0]) - day(hist[0][0])) / (7 * 864e5));
    return weeks >= 51 ? "52w" : `${weeks}w`;
  };

  /* ---------- sentiment: unavailable is not neutral ---------- */
  const sentimentFresh = (iso, maxAge = 4) => {
    if (!iso || !/^\d{4}-\d{2}-\d{2}/.test(iso)) return false;
    const age = (Date.now() - day(iso.slice(0, 10)).getTime()) / 864e5;
    return Number.isFinite(age) && age >= 0 && age < maxAge + 1;
  };
  const sentimentLink = (url, text) => /^https?:\/\//i.test(url || "")
    ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>` : esc(text || "—");
  const sentimentTone = score => !isNum(score) ? "unknown" : score > 60 ? "positive" : score < 40 ? "negative" : "mixed";
  const sentimentLabel = score => !isNum(score) ? "Insufficient evidence" : score > 60 ? "Positive" : score < 40 ? "Negative" : "Mixed";
  const sentimentView = r => {
    const stored = r.sentiment || {};
    const components = (stored.components || []).map(c => ({...c,
      score: isNum(c.score) && sentimentFresh(c.as_of, c.max_age || 4) ? c.score : null,
      stale: isNum(c.score) && !sentimentFresh(c.as_of, c.max_age || 4),
    }));
    const available = components.filter(c => isNum(c.score));
    const stock = available.find(c => c.key === "stock"), sector = available.find(c => c.key === "sector");
    const coverage = available.reduce((sum, c) => sum + c.weight, 0);
    const score = stock && sector && stock.as_of === sector.as_of && coverage > 0
      ? Math.round(available.reduce((sum, c) => sum + c.score * c.weight, 0) / coverage) : null;
    return {...stored, score, label: sentimentLabel(score), components, coverage,
      mode: available.some(c => ["news", "social"].includes(c.key)) ? "Price + opinion" : "Price only", sector};
  };
  const sentimentCell = r => {
    const s = sentimentView(r);
    return `<span class="sentiment-badge ${sentimentTone(s.score)}">${isNum(s.score) ? `${s.score}/100 · ${s.label}` : "Insufficient evidence"}</span>` +
      `<span class="sub">${isNum(s.sector?.score) ? `Sector ${nf0.format(s.sector.score)}/100 · ${esc(s.benchmark || "")}` : "Sector unavailable"}</span>` +
      `<span class="sub">${s.coverage ? `${esc(s.mode)} · ${s.coverage}% coverage` : "No fresh inputs"}</span>`;
  };
  const sentimentDetails = r => {
    const s = sentimentView(r);
    const names = {stock: "Stock momentum", sector: "Sector momentum", news: "News sentiment", social: "Social sentiment"};
    return `<div class="detail-group detail-wide sentiment-details"><h4>Stock / sector sentiment · ${isNum(s.score) ? `${s.score}/100 · ${s.label}` : "Insufficient evidence"}</h4>` +
      `<p class="detail-text">${esc(s.method || "Sentiment was not collected in this saved report. Refresh data to collect it.")}</p>` +
      `<div class="sentiment-components">${s.components.map(c => `<article><h5>${esc(names[c.key] || c.key)} <span>${isNum(c.score) ? `${nf0.format(c.score)}/100` : c.stale ? "Stale · excluded" : "Unavailable"}</span></h5>` +
        `<p>${esc(c.detail)}</p><p class="sentiment-meta">Base weight ${esc(c.weight)}%${isNum(c.score) && isNum(s.score) ? ` · effective ${nf0.format(c.weight / s.coverage * 100)}%` : ""}` +
        `${c.as_of ? ` · As of ${esc(c.as_of)}` : ""}${c.start20 ? ` · 20-session start ${esc(c.start20)}` : ""}` +
        `${c.fetched_at ? ` · ${esc(fetchedLabel(c.fetched_at))}` : ""}${c.status === "cached" ? " · Last good reading; refresh failed" : ""}` +
        ` · ${sentimentLink(c.url, c.source || "Source unavailable")}</p>` +
        ((c.evidence || []).length ? `<ul class="sentiment-evidence">${c.evidence.map(e => `<li>${sentimentLink(e.url, e.title)} <span>${esc(e.source)} · ${esc(e.as_of)} · tone ${esc(e.score)}</span></li>`).join("")}</ul>` : "") +
        `</article>`).join("")}</div></div>`;
  };
  const replicaParts = parts => (parts || []).length ? `<ul class="replica-parts">${parts.map(p =>
    `<li>${esc(p.name)} · ${isNum(p.score) ? `${nf1.format(p.score)} ${esc(p.rating || "")}` : "Unavailable"}<span>${esc(isNum(p.score) ? p.reading : p.detail || "No fresh input")}</span></li>`).join("")}</ul>` : "";
  const renderMacro = () => {
    const defaults = [
      {key:"vix", name:"VIX", url:"https://www.cboe.com/tradable-products/vix/"},
      {key:"put_call", name:"Put/call ratios", url:"https://www.cboe.com/us/options/market_statistics/daily/"},
      {key:"aaii", name:"AAII sentiment", url:"https://www.aaii.com/sentimentsurvey"},
      {key:"cnn", name:"CNN Fear & Greed", url:"https://www.cnn.com/markets/fear-and-greed"},
    ];
    const cards = defaults.map(d => ({...d, ...(DATA.macro_sentiment?.cards || []).find(c => c.key === d.key)}))
      .map(c => ({...c, usable: isNum(c.value) && ["ok", "cached"].includes(c.status) && sentimentFresh(c.as_of, c.max_age || 4)}));
    const available = cards.filter(c => c.usable);
    const positive = available.filter(c => c.direction > 0).length, negative = available.filter(c => c.direction < 0).length;
    $("macro-summary").textContent = available.length < 3 ? "Limited coverage" : positive && negative ? "Mixed signals" : positive >= 2 ? "Risk appetite leaning positive" : negative >= 2 ? "Cautious mood" : "Mixed signals";
    $("macro-note").textContent = `${available.length}/4 fresh readings · Each source keeps its own observation date. ${DATA.macro_sentiment?.checked_at ? `Sources checked ${fetchedLabel(DATA.macro_sentiment.checked_at).replace(/^Fetched /, "")}.` : "Refresh data to collect sentiment."} This panel is independent of the IV scan session.`;
    $("macro-cards").innerHTML = cards.map(c => {
      const stale = isNum(c.value) && !sentimentFresh(c.as_of, c.max_age || 4);
      const tone = !c.usable ? "unknown" : c.direction > 0 ? "positive" : c.direction < 0 ? "negative" : "mixed";
      // A replica is always labelled: it stands in only when CNN has no fresh reading, otherwise it is a cross-check.
      const replica = c.replica && isNum(c.replica.value) && sentimentFresh(c.replica.as_of, 4) ? c.replica : null;
      return `<article class="macro-card"><h3>${esc(c.name)}</h3><div class="macro-value">${esc(c.reading || "—")}</div>` +
        `<span class="sentiment-badge ${tone}">${esc(stale ? "Stale · excluded" : c.usable ? c.signal : "Unavailable")}</span>` +
        `<p class="sentiment-meta">${c.as_of ? `As of ${esc(c.as_of)}` : "Observation date unavailable"}${c.key === "aaii" && c.as_of ? ` · ${esc(c.date_label || "week ending")}` : ""}</p>` +
        (c.source_file ? `<p class="sentiment-meta">From AAII's spreadsheet · ${esc(c.source_file)}</p>` : "") +
        (c.replica_of ? `<p class="sentiment-meta">Not CNN's reading · CNN feed ${c.cnn_status === "stale" && c.cnn_as_of ? `stale since ${esc(c.cnn_as_of)}` : "unavailable"}</p>` : "") +
        (replica && c.usable ? `<p class="sentiment-meta">Replica ${nf1.format(replica.value)} · ${replica.value >= c.value ? "+" : "−"}${nf1.format(Math.abs(replica.value - c.value))} vs CNN</p>` : "") +
        (c.ratios ? `<p class="sentiment-meta">Total ${isNum(c.ratios.total) ? nf2.format(c.ratios.total) : "—"} · Index ${isNum(c.ratios.index) ? nf2.format(c.ratios.index) : "—"}</p>` : "") +
        `<details><summary>Evidence &amp; source</summary><p>${esc(c.detail || c.error || "No verified reading in this report. The source may block automated access; no substitute value is estimated.")}</p>` +
        (c.import_url ? `<p>AAII blocks automated downloads. Once a week, ${sentimentLink(c.import_url, "download the AAII spreadsheet")} in your browser and save it to Downloads (or the app's data/imports folder); the next refresh reads it.${c.file_saved ? ` Current file saved ${esc(c.file_saved.slice(0, 10))}.` : ""}</p>` : "") +
        (c.import_note ? `<p>${esc(c.import_note)}</p>` : "") +
        (c.replica_of ? `<p>${esc(c.method || "")}</p>${replicaParts(c.components)}` : "") +
        (replica ? `<p>Replica cross-check · ${esc(replica.coverage)}/7 components · as of ${esc(replica.as_of)}</p>${replicaParts(replica.components)}` : "") +
        (c.observed_at ? `<p>Provider timestamp: ${esc(c.observed_at)}</p>` : "") +
        (c.fetched_at ? `<p>${esc(fetchedLabel(c.fetched_at))}</p>` : "") +
        (c.status === "cached" ? "<p>Last good observation retained; latest refresh failed.</p>" : "") +
        `${sentimentLink(c.url, c.replica_of ? "CNN's index, for comparison" : "Open provider")}</details></article>`;
    }).join("");
  };

  /* ---------- views ---------- */
  const onTsx = (r) => r.market === "CA" || Boolean(r.also_listed);
  const inView = (r) =>
    !r.watch_only && isNum(r.market_cap_usd) && r.market_cap_usd >= S.min_market_cap_usd &&
    (state.cap === "large" ? r.market_cap_usd >= CAP_SPLIT : r.market_cap_usd < CAP_SPLIT) &&
    !(state.market === "us" && r.market !== "US") &&
    !(state.market === "tsx" && !onTsx(r)) &&
    !(state.hqOnly && !r.hq_north_america);
  const viewRows = () =>
    DATA.rows.filter(state.cap === "watch" ? r => watched.has(r.symbol) : inView).sort((a, b) => b.iv30 - a.iv30).slice(0, state.cap === "watch" ? Infinity : TOP_N).map((r, i) => ({ ...r, rank: i + 1 }));

  const SQUEEZE_ORDER = { high: 3, elevated: 2, low: 1, unknown: 0 };
  const WHY_ORDER = { news: 2, earnings: 1, none: 0 };
  const DEFAULT_DIR = { rank: "asc", float_shares: "asc", earnings_in_days: "asc", industry: "asc" };
  const sortRows = (rows) => {
    const { key, dir } = state.sort;
    const sign = dir === "asc" ? 1 : -1;
    const value = (r) => (key === "sentiment" ? sentimentView(r).score : key === "squeeze" ? SQUEEZE_ORDER[r.squeeze] : key === "iv_why_kind" ? (WHY_ORDER[r.iv_why_kind] || 0) : r[key]);
    return [...rows].sort((a, b) => {
      const va = value(a), vb = value(b);
      const missA = va === null || va === undefined, missB = vb === null || vb === undefined;
      if (missA || missB) return missA && missB ? a.rank - b.rank : missA ? 1 : -1;
      return va === vb ? a.rank - b.rank : va < vb ? -sign : sign;
    });
  };

  /* ---------- sparklines ---------- */
  const SPARK = { w: 120, h: 34, pad: 4 };
  const geometry = (values, w, h, pad) => {
    const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
    return {
      x: (i) => pad + (i / (values.length - 1)) * (w - pad * 2),
      y: (v) => pad + (1 - (v - lo) / span) * (h - pad * 2),
    };
  };
  const sparkMarkup = (values, { cls, data, label, w = SPARK.w, h = SPARK.h }) => {
    const g = geometry(values, w, h, SPARK.pad);
    const line = values.map((v, i) => `${i ? "L" : "M"}${g.x(i).toFixed(1)},${g.y(v).toFixed(1)}`).join("");
    const last = values.length - 1;
    const area = `${line}L${g.x(last).toFixed(1)},${h}L${g.x(0).toFixed(1)},${h}Z`;
    return `<svg class="spark ${cls}" viewBox="0 0 ${w} ${h}" ${data} role="img" aria-label="${esc(label)}">` +
      `<path class="area" d="${area}"/><path class="line" d="${line}"/>` +
      `<circle class="end" cx="${g.x(last).toFixed(1)}" cy="${g.y(values[last]).toFixed(1)}" r="4"/>` +
      `<circle class="probe" r="4" hidden/></svg>`;
  };

  const priceSeries = (r, frame) => {
    const series = r.prices && r.prices[frame];
    return series && series.c && series.c.length > 1 ? series : null;
  };
  const frameChange = (series) => ((series.c[series.c.length - 1] - series.c[0]) / series.c[0]) * 100;

  /* Points behind a hovered sparkline: values to place the dot, labels for the tooltip. */
  const seriesPoints = (symbol, key) => {
    const row = DATA.rows.find((r) => r.symbol === symbol);
    if (!row) return null;
    if (key === "iv") {
      const hist = row.iv_history || [];
      return hist.length < 2 ? null : { values: hist.map((p) => p[1]), labels: hist.map((p) => `${fmtDay(p[0])} · IV30 ${pct(p[1])}`) };
    }
    const series = priceSeries(row, key);
    if (!series) return null;
    const fmt = key === "1H" || key === "4H" ? stampET : dateET;
    return {
      values: series.c,
      labels: series.t.map((t, i) => `${fmt.format(new Date(t * 1000))} · ${money(series.c[i])}`),
    };
  };

  /* ---------- cells ---------- */
  const SQUEEZE_LABEL = { high: "High", elevated: "Elevated", low: "Low", unknown: "No data" };
  const squeezeChip = (level) => {
    const lit = SQUEEZE_ORDER[level] || 0;
    const bars = [0, 1, 2]
      .map((i) => `<rect x="${i * 5}" y="${8 - i * 4}" width="3" height="${4 + i * 4}" rx="1"${i < lit ? ' class="on"' : ""}/>`)
      .join("");
    return `<span class="sq sq-${level}"><svg viewBox="0 0 13 12" aria-hidden="true">${bars}</svg>${SQUEEZE_LABEL[level]}</span>`;
  };

  const ivCell = (r) => {
    const hist = r.iv_history || [];
    if (hist.length < 2) return `<span class="sub">History starts today</span>`;
    return sparkMarkup(hist.map((p) => p[1]), {
      cls: "iv",
      data: `data-symbol="${esc(r.symbol)}" data-series="iv"`,
      label: `${r.symbol} IV30 over ${hist.length} sessions since ${fmtDay(hist[0][0])}`,
    }) + (isNum(r.iv_low)
      ? `<span class="sub">${nf1.format(r.iv_low)} – ${nf1.format(r.iv_high)}% · avg ${nf1.format(r.iv_avg)}%</span>`
      : `<span class="sub">${hist.length} ${hist.length === 1 ? "session" : "sessions"}</span>`);
  };

  const pctileCell = (r) => {
    if (!isNum(r.iv_percentile)) return `<span class="val muted">—</span><span class="sub">needs history</span>`;
    return `<div class="ivline"><span class="val">${ordinal(r.iv_percentile)}</span><span class="delta">${windowLabel(r)}</span></div>` +
      `<span class="meter" data-tip="${r.iv_percentile}% of sessions in the window closed below today's IV30">` +
      `<span class="fill" style="width:${r.iv_percentile}%"></span><span class="dot" style="left:${r.iv_percentile}%"></span></span>`;
  };

  const fetchedLabel = (stamp) => {
    const d = stamp ? new Date(stamp) : null;
    return d && Number.isFinite(d.getTime())
      ? `Fetched ${new Intl.DateTimeFormat("en-US", {timeZone: "America/New_York", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit"}).format(d)} ET`
      : "Fetch time not recorded";
  };
  const fetchStamp = (stamp) => `<span class="sub freshness" data-tip="Retrieval time, not the provider’s last update time. Older snapshots did not record retrieval timestamps.">${esc(fetchedLabel(stamp))}</span>`;
  const shortStamp = (r) => `<span class="sub freshness" data-tip="Yahoo short-interest settlement date. This dates the reported short position, not publication or retrieval. Days to cover also depends on an average-volume window whose update time is not supplied.">${r.short_date ? `SI as of ${esc(r.short_date)}` : "SI date unavailable"}</span>`;
  /* IBKR indicative annual borrow fee and available inventory from the same fetched file. */
  const borrowCell = (r) => {
    if (!isNum(r.borrow_fee)) return `<span class="muted">—</span>`;
    const fee = `${nf1.format(r.borrow_fee)}<small>%</small>`;
    const available = isNum(r.borrow_available)
      ? `${compact(r.borrow_available)}${r.borrow_capped ? "+" : ""} available`
      : "";
    return `<span class="val">${r.borrow_fee >= 20 ? `<span class="chip hot">${fee}</span>` : fee}</span>` +
      (available ? `<span class="sub">${available}</span>` : "") + fetchStamp(r.borrow_fetched_at);
  };

  const industryCell = (r) => {
    if (!r.industry && !r.sector) return `<span class="muted">—</span>`;
    return `<span class="ind">${esc(r.industry || r.sector)}</span>` +
      (r.industry && r.sector ? `<span class="sub">${esc(r.sector)}</span>` : "");
  };

  const hvCell = (r) => {
    if (!isNum(r.hv30)) return `<span class="muted">—</span>`;
    return `<span class="val">${nf1.format(r.hv30)}<small>%</small></span>` +
      (isNum(r.iv_hv)
        ? `<span class="sub" data-tip="IV30 ÷ HV30: above 1 means options price a bigger move than the stock has made">IV ${nf2.format(r.iv_hv)}× HV</span>`
        : "");
  };

  const priceCell = (r) => {
    const series = priceSeries(r, state.frame);
    if (!series) return `<span class="muted">—</span>`;
    const change = frameChange(series);
    return `<div class="ivline"><span class="val">${money(series.c[series.c.length - 1])}</span>` +
      `<span class="delta ${change >= 0 ? "up" : "down"}" data-tip="Move across the whole chart window (${FRAME_NOTE[state.frame]}), not one bar">${signed(change)}</span></div>` +
      sparkMarkup(series.c, {
        cls: "price",
        data: `data-symbol="${esc(r.symbol)}" data-series="${state.frame}"`,
        label: `${r.symbol} close, ${FRAME_NOTE[state.frame]}`,
      }) + momentumLine(r);
  };

  /* RSI and volume against its own normal: both read off the daily bars. */
  const momentumLine = (r) => {
    const parts = [];
    if (isNum(r.rsi14)) {
      const extreme = r.rsi14 >= 70 || r.rsi14 <= 30;
      parts.push(extreme ? `<span class="chip hot">RSI ${nf0.format(r.rsi14)}</span>` : `RSI ${nf0.format(r.rsi14)}`);
    }
    if (isNum(r.volume_surge)) {
      const busy = r.volume_surge >= 2;
      const text = `vol ${nf1max.format(r.volume_surge)}×`;
      parts.push(busy ? `<span class="chip hot">${text}</span>` : text);
    }
    if (!parts.length) return "";
    return `<span class="sub" data-tip="RSI(14) on daily closes (70+ overbought, 30− oversold) and today's volume against its 20-day average">${parts.join(" · ")}</span>`;
  };

  const WHY_LABEL = { news: "Possible event", earnings: "Scheduled event", none: "Unknown" };
  const peerSummary = (p) => `${p.direction === "down" ? p.down : p.up}/${p.count} screened peers ${p.direction === "down" ? "down" : "up"} · median ${signed(p.median_pct)}`;
  const contextCell = (r) => {
    const exposures = r.iv_context || [];
    const peer = (r.iv_peer_trends || []).find(p => p.direction !== "mixed");
    const pattern = r.iv_price_context;
    if (!exposures.length && !pattern) return "";
    return `<div class="why-context">` +
      (exposures.length ? `<span class="chip">Structural context</span><span class="why-text">${esc(exposures.map(c => c.label).join(" · "))}</span>` : `<span class="chip">Observed price</span>`) +
      (pattern ? `<span class="sub" data-tip="${esc(pattern.detail)}">${esc(pattern.label)}</span>` : "") +
      (peer ? `<span class="sub" data-tip="${esc(`${peer.label}: ${peer.start} to ${peer.end}. ${peer.detail}`)}">${esc(peerSummary(peer))}</span>` : "") +
      `</div>`;
  };
  const whyCell = (r) => {
    const kind = r.iv_why_kind || "none";
    const text = r.iv_why || "No clear catalyst found";
    const coverage = r.iv_news_status === "unavailable" ? "News unavailable" : r.iv_news_status === "empty" ? "No headlines returned" : null;
    const meta = [r.iv_why_source, r.iv_why_date ? fmtDay(r.iv_why_date) : null, coverage].filter(Boolean).join(" · ");
    const tip = r.iv_why_detail || text;
    const chip = kind === "none"
      ? ""
      : `<span class="chip${kind === "news" || kind === "earnings" ? " hot" : ""}">${WHY_LABEL[kind] || kind}</span> `;
    return contextCell(r) + `<div data-tip="${esc(tip)}">${chip}<span class="why-text${kind === "none" ? " muted" : ""}">${esc(text)}</span>` +
      (meta ? `<span class="sub">${esc(meta)}</span>` : "") + `</div>`;
  };

  const earningsCell = (r) => {
    if (!isNum(r.earnings_in_days)) return `<span class="muted">—</span>`;
    const when = r.earnings_window_end ? `${fmtDay(r.next_earnings)} – ${fmtDay(r.earnings_window_end)}` : fmtDay(r.next_earnings);
    const hour = r.earnings_estimated === false && r.earnings_time ? `${r.earnings_time} ET · ${r.earnings_session}` : "time not set";
    const countdown = r.earnings_in_days === 0 ? "today" : `in ${r.earnings_in_days} ${r.earnings_in_days === 1 ? "day" : "days"}`;
    return `<span class="val">${when}${r.earnings_estimated ? ' <span class="chip" data-tip="Yahoo estimate, not confirmed by the company">est.</span>' : ""}</span>` +
      `<span class="sub">${esc(hour)}</span>` +
      `<span class="sub">${r.earnings_in_days <= 7 ? `<span class="chip hot">${countdown}</span>` : countdown}</span>`;
  };

  const logoHtml = (r) => {
    const image = typeof r.logo_webp === "string" && /^[A-Za-z0-9+/]+={0,2}$/.test(r.logo_webp) && r.logo_webp.length <= 16000;
    return image ? `<img class="company-logo" src="data:image/webp;base64,${r.logo_webp}" width="24" height="24" alt="" loading="lazy" decoding="async" title="Company logo · Financial Modeling Prep">`
      : `<span class="company-logo logo-initials" aria-hidden="true">${esc(r.symbol.slice(0, 2))}</span>`;
  };

  const rowHtml = (r, maxIv) => {
    const open = state.open.has(r.symbol);
    const chips = `<span class="chip">${esc(r.exchange)}</span>` +
      (r.also_listed ? `<span class="chip" data-tip="Also listed in Canada as ${esc(r.also_listed)}">${esc(r.also_listed)}</span>` : "");
    const change = isNum(r.iv30_change)
      ? `<span class="delta" data-tip="Change vs previous session, in volatility points">${r.iv30_change > 0 ? "▲" : r.iv30_change < 0 ? "▼" : "•"}${nf1.format(Math.abs(r.iv30_change))}</span>`
      : "";
    const ivRank = isNum(r.iv_rank)
      ? `<div class="ivline"><span class="val">${r.iv_rank}</span><span class="delta">${windowLabel(r)} window</span></div>` +
        `<span class="meter" data-tip="IV30 range over ${windowLabel(r)}: ${pct(r.iv_low)} – ${pct(r.iv_high)}"><span class="fill" style="width:${r.iv_rank}%"></span><span class="dot" style="left:${r.iv_rank}%"></span></span>`
      : `<span class="val muted">—</span><span class="sub">Building · ${r.iv_days} ${r.iv_days === 1 ? "session" : "sessions"}</span>`;
    const fromHigh = isNum(r.pct_from_high) ? (r.pct_from_high > -0.5 ? "at 52-wk high" : `${nf1.format(r.pct_from_high)}% from high`) : "";
    const currency = r.currency && r.currency !== "USD" ? ` ${esc(r.currency)}` : "";
    const range = isNum(r.range_pos)
      ? `<div class="rng"><span>${money(r.low_52w)}</span><span class="meter" data-tip="${nf0.format(r.range_pos)}% of the way from 52-wk low to high"><span class="dot ink" style="left:${r.range_pos}%"></span></span><span>${money(r.high_52w)}</span></div>` +
        `<span class="sub">${money(r.price)}${currency} · ${fromHigh}</span>`
      : `<span class="muted">—</span>`;
    const float = isNum(r.float_shares)
      ? `<span class="val">${compact(r.float_shares)}</span><span class="sub">${r.small_float ? '<span class="chip hot">Small float</span> ' : ""}${isNum(r.float_pct_outstanding) ? `${nf0.format(r.float_pct_outstanding)}% of shares` : ""}</span>`
      : `<span class="muted">—</span>`;
    const shortPct = isNum(r.short_pct_float)
      ? `<span class="val">${nf1.format(r.short_pct_float)}<small>%</small></span>` +
        (isNum(r.short_change_pct) ? `<span class="sub">${r.short_change_pct >= 0 ? "▲" : "▼"} ${nf0.format(Math.abs(r.short_change_pct))}% vs prior month</span>` : "")
      : `<span class="muted">—</span>`;
    const dtc = isNum(r.days_to_cover) ? `<span class="val">${nf1.format(r.days_to_cover)}</span><span class="sub">days</span>` : `<span class="muted">—</span>`;

    return `<tr class="row" tabindex="0" aria-expanded="${open}" data-symbol="${esc(r.symbol)}">` +
      `<td class="col-stock"><div class="stock"><span class="rank">${r.rank}</span><div><div class="tick"><button type="button" data-watch="${esc(r.symbol)}" aria-label="${watched.has(r.symbol) ? "Remove from" : "Add to"} watchlist" aria-pressed="${watched.has(r.symbol)}">${watched.has(r.symbol) ? "★" : "☆"}</button>${logoHtml(r)}<span class="sym">${esc(r.symbol)}</span>${chips}</div><div class="nm">${esc(r.name)}</div>${r.business ? `<div class="biz">${esc(r.business)}</div>` : ""}</div></div></td>` +
      `<td class="col-industry">${industryCell(r)}</td>` +
      `<td class="col-sentiment">${sentimentCell(r)}</td>` +
      `<td class="col-iv"><div class="ivline"><span class="big">${nf1.format(r.iv30)}<small>%</small></span>${change}</div><span class="bar"><i style="width:${Math.max(2, (r.iv30 / maxIv) * 100)}%"></i></span></td>` +
      `<td class="col-why">${whyCell(r)}</td>` +
      `<td class="col-ivr">${ivRank}</td>` +
      `<td class="col-pct">${pctileCell(r)}</td>` +
      `<td class="col-trend">${ivCell(r)}</td>` +
      `<td class="r col-hv">${hvCell(r)}</td>` +
      `<td class="col-price">${priceCell(r)}</td>` +
      `<td class="col-range">${range}</td>` +
      `<td class="r">${float}${isNum(r.float_shares) ? fetchStamp(r.details_fetched_at) : ""}</td>` +
      `<td class="r">${shortPct}${isNum(r.shares_short) ? `<span class="sub">${compact(r.shares_short)} shares short</span>` : ""}${isNum(r.short_pct_float) || isNum(r.shares_short) ? shortStamp(r) : ""}</td>` +
      `<td class="r">${dtc}${isNum(r.days_to_cover) ? shortStamp(r) + fetchStamp(r.details_fetched_at) : ""}</td>` +
      `<td class="r col-borrow">${borrowCell(r)}</td>` +
      `<td>${squeezeChip(r.squeeze)}</td>` +
      `<td class="r"><span class="val">${compact(r.market_cap_usd, "$")}</span></td>` +
      `<td class="col-er">${earningsCell(r)}</td></tr>` +
      (open ? detailHtml(r, fromHigh, currency) : "");
  };

  /* MACD gets its own pane under each chart: an oscillator around zero shares no scale with price. */
  const macdMarkup = (series) => {
    if (!series.macd || !series.signal) return "";
    const W = 220, H = 46, pad = 3;
    const pairs = series.macd.map((line, i) => [line, series.signal[i]]);
    const histogram = pairs.map(([line, sig]) => (isNum(line) && isNum(sig) ? line - sig : null));
    const values = [0];
    pairs.forEach(([line, sig]) => { if (isNum(line)) values.push(line); if (isNum(sig)) values.push(sig); });
    histogram.forEach((h) => { if (isNum(h)) values.push(h); });
    if (values.length < 4) return "";
    const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
    const x = (i) => pad + (i / (pairs.length - 1)) * (W - pad * 2);
    const y = (v) => pad + (1 - (v - lo) / span) * (H - pad * 2);
    const trace = (index) => {
      let d = "", drawing = false;
      pairs.forEach((pair, i) => {
        const v = pair[index];
        if (!isNum(v)) { drawing = false; return; }
        d += `${drawing ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
        drawing = true;
      });
      return d;
    };
    const width = Math.max(1, (W - pad * 2) / pairs.length - 1);
    const bars = histogram.map((h, i) => {
      if (!isNum(h)) return "";
      const height = Math.max(0.6, Math.abs(y(h) - y(0)));
      return `<rect class="${h >= 0 ? "bar-up" : "bar-down"}" x="${(x(i) - width / 2).toFixed(1)}" ` +
        `y="${Math.min(y(h), y(0)).toFixed(1)}" width="${width.toFixed(1)}" height="${height.toFixed(1)}"/>`;
    }).join("");
    const [line, sig] = pairs[pairs.length - 1];
    const fine = (v) => (Math.abs(v) < 1 ? v.toFixed(3) : nf2.format(v));
    const readout = isNum(line) && isNum(sig)
      ? `MACD ${fine(line)} · signal ${fine(sig)} · hist ${fine(line - sig)}`
      : "MACD 12/26/9 — warming up";
    return `<svg class="macd" viewBox="0 0 ${W} ${H}" role="img" aria-label="MACD 12/26/9 with signal line and histogram">` +
      bars +
      `<line class="zero" x1="${pad}" x2="${W - pad}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}"/>` +
      `<path class="line-macd" d="${trace(0)}"/><path class="line-signal" d="${trace(1)}"/></svg>` +
      `<span class="macd-note">${readout}</span>`;
  };

  const detailCharts = (r) => {
    const cards = FRAMES.map((frame) => {
      const series = priceSeries(r, frame);
      if (!series) return `<div class="mini"><div class="mini-head"><span>${frame}</span></div><span class="sub">no data</span></div>`;
      const change = frameChange(series);
      return `<div class="mini"><div class="mini-head"><span>${frame}</span>` +
        `<span class="delta ${change >= 0 ? "up" : "down"}">${signed(change)}</span></div>` +
        sparkMarkup(series.c, {
          cls: "price",
          data: `data-symbol="${esc(r.symbol)}" data-series="${frame}"`,
          label: `${r.symbol} close, ${FRAME_NOTE[frame]}`,
          w: 220,
          h: 56,
        }) +
        macdMarkup(series) +
        `<span class="mini-note">${FRAME_NOTE[frame]}</span></div>`;
    }).join("");
    return `<div class="detail-group detail-wide"><h4>Price and MACD (12/26/9), ${FRAMES.length} timeframes</h4>` +
      `<div class="mini-row">${cards}</div></div>`;
  };

  const detailHtml = (r, fromHigh, currency) => {
    const group = (title, pairs, cls = "") =>
      `<div class="detail-group ${cls}"><h4>${title}</h4><dl>${pairs.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl></div>`;
    const ivChange = isNum(r.iv30_change) ? `${r.iv30_change > 0 ? "+" : ""}${nf1.format(r.iv30_change)} pts` : "—";
    const whyLink = r.iv_why_url
      ? `<a href="${esc(r.iv_why_url)}" target="_blank" rel="noopener">${esc(r.iv_why || "Open story")}</a>`
      : esc(r.iv_why || "—");
    const sourceLink = (url, label) => /^https?:\/\//i.test(url || "")
      ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}</a>` : esc(label || "—");
    const structural = (r.iv_context || []).map(c => [esc(c.label),
      `${esc(c.detail)}<span class="sub context-evidence">${esc(c.evidence)} ` +
      sourceLink(c.url, c.source) + (c.reference_url ? ` · ${sourceLink(c.reference_url, c.reference_source)}` : "") + `</span>`]);
    const observed = (r.iv_peer_trends || []).map(p => [esc(p.label),
      `Median ${signed(p.median_pct)}; ${p.up} up / ${p.down} down among ${p.count} screened peers. ${esc(p.start)} to ${esc(p.end)}.` +
      `<span class="sub context-evidence">${esc(p.detail)} Peers: ${esc(p.members.map(m => `${m.symbol} ${signed(m.return_pct)}`).join(", "))}.</span>`]);
    if (r.iv_price_context) observed.unshift(["This stock", esc(r.iv_price_context.label) +
      `<span class="sub context-evidence">${esc(r.iv_price_context.detail)}</span>`]);
    return `<tr class="detail"><td colspan="18"><div class="detail-grid">` +
      sentimentDetails(r) +
      group("Company", [
        ["HQ", esc(r.country || "—")],
        ["Sector", esc(r.sector || "—")],
        ["Industry", esc(r.industry || "—")],
        ["Listing", esc(r.exchange + (r.also_listed ? ` · ${r.also_listed}` : ""))],
      ]) +
      group("Why this IV", [
        ["Evidence", WHY_LABEL[r.iv_why_kind] || "Unknown"],
        ["Event type", esc(r.iv_why_event || "—")],
        ["Headline", whyLink],
        ["Source", esc(r.iv_why_source || "—")],
        ["Date", r.iv_why_date ? fmtDay(r.iv_why_date) : "—"],
        ["IV session assessed", fmtDay(r.iv_why_as_of)],
        ["Notes", esc(r.iv_why_detail || "—")],
      ]) +
      (structural.length ? group("Structural context · possible sensitivities", structural.concat([
        ["Interpretation", "Business exposure can help explain recurring volatility, but does not establish the cause of this session’s IV. Profile classifications are based on the cached business description; truncated or missing descriptions can leave gaps."],
      ]), "detail-wide context-details") : "") +
      (observed.length ? group("Observed stock / peer moves", observed, "detail-wide context-details") : "") +
      group("Implied volatility", [
        ["IV30 today", `${pct(r.iv30)} (${ivChange})`],
        ["IV rank · percentile", isNum(r.iv_rank) ? `${r.iv_rank} · ${ordinal(r.iv_percentile)}` : "Building history"],
        ["Low – high in window", isNum(r.iv_low) ? `${pct(r.iv_low)} – ${pct(r.iv_high)}` : "—"],
        ["Average in window", pct(r.iv_avg)],
        ["HV30 (realized)", pct(r.hv30)],
        ["IV ÷ HV", isNum(r.iv_hv) ? `${nf2.format(r.iv_hv)}×` : "—"],
        ["History", r.iv_since ? `${r.iv_days} sessions since ${fmtDay(r.iv_since)}` : "Starts today"],
        ["Source", esc(r.iv_source)],
      ]) +
      group("Price", [
        ["Last", `${money(r.price)}${currency}`],
        ["52-wk low – high", `${money(r.low_52w)} – ${money(r.high_52w)}`],
        ["Place in range", isNum(r.range_pos) ? `${nf0.format(r.range_pos)}% · ${fromHigh}` : "—"],
        ["RSI(14)", isNum(r.rsi14) ? nf0.format(r.rsi14) : "—"],
        ["Volume vs 20-day", isNum(r.volume_surge) ? `${nf1max.format(r.volume_surge)}×` : "—"],
        ["Market cap", compact(r.market_cap_usd, "US$")],
      ]) +
      group("Short interest", [
        ["Shares short", compact(r.shares_short)],
        ["Float · % of shares", `${compact(r.float_shares)} · ${isNum(r.float_pct_outstanding) ? `${nf0.format(r.float_pct_outstanding)}%` : "—"}`],
        ["Short % float · days to cover", `${pct(r.short_pct_float)} · ${isNum(r.days_to_cover) ? nf1.format(r.days_to_cover) : "—"}`],
        ["Borrow fee", isNum(r.borrow_fee) ? `${nf1.format(r.borrow_fee)}% a year` : "—"],
        ["Shares available to borrow", isNum(r.borrow_available) ? `${compact(r.borrow_available)}${r.borrow_capped ? "+" : ""}` : "—"],
        ["Settlement date", fmtDay(r.short_date)],
        ["Short / float data fetched", esc(fetchedLabel(r.details_fetched_at))],
        ["Borrow fee / availability fetched", esc(fetchedLabel(r.borrow_fetched_at))],
        ["Timing", "The settlement date dates the short position. Fetch times show when the feed was retrieved; the provider’s exact update time for float, days to cover and borrow data is not supplied."],
      ]) +
      (isNum(r.earnings_in_days)
        ? group("Next earnings", [
            ["Date", r.next_earnings ? longDate.format(day(r.next_earnings)) : "—"],
            ["Time", r.earnings_estimated === false && r.earnings_time ? `${r.earnings_time} ET · ${r.earnings_session}` : "not published yet"],
            ["Source", r.earnings_estimated === true ? "Yahoo estimate" : r.earnings_estimated === false ? "company confirmed" : "confirmation unavailable"],
            ["Countdown", `${r.earnings_in_days} ${r.earnings_in_days === 1 ? "day" : "days"}`],
          ])
        : "") +
      detailCharts(r) +
      (r.summary ? `<div class="detail-group detail-wide"><h4>What the business does</h4><p class="detail-text">${esc(r.summary)}</p></div>` : "") +
      `</div></td></tr>`;
  };

  /* ---------- overview ---------- */
  const renderFigures = (view) => {
    const n = view.length;
    const high = view.filter((r) => r.squeeze === "high").length;
    const elevated = view.filter((r) => r.squeeze === "elevated").length;
    const soon = view.filter((r) => isNum(r.earnings_in_days) && r.earnings_in_days <= 14).length;
    const fig = (label, value, sub) =>
      `<div class="figure"><span class="label">${label}</span><span class="value">${value}</span><span class="sub">${sub}</span></div>`;
    $("figures").innerHTML = [
      fig(`IV30 needed for the top ${n || TOP_N}`, n ? `${nf1.format(view[n - 1].iv30)}<small>%</small>` : "—",
        n ? `Leader ${esc(view[0].symbol)} at ${pct(view[0].iv30)}` : "No stocks in this view"),
      fig("Median IV30, this cap band", isNum(U.median_iv) ? `${nf1.format(U.median_iv)}<small>%</small>` : "—", `${nf0.format(U.with_iv)} stocks with IV this session`),
      fig("Squeeze setups in the list", `${high + elevated}<small>of ${n}</small>`, `${high} high · ${elevated} elevated`),
      fig("Earnings within 14 days", `${soon}<small>of ${n}</small>`, "IV tends to build into a report"),
    ].join("");
  };

  const niceStep = (raw) => {
    const mag = 10 ** Math.floor(Math.log10(Math.max(raw, 1)));
    return [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || mag * 10;
  };
  const barPath = (x, base, w, h, r) => {
    const rr = Math.min(r, w / 2, h);
    const top = base - h;
    return `M${x},${base}V${top + rr}Q${x},${top} ${x + rr},${top}H${x + w - rr}Q${x + w},${top} ${x + w},${top + rr}V${base}Z`;
  };

  let lastCutoff = null;
  const drawDistribution = (cutoff) => {
    lastCutoff = cutoff;
    const frame = $("dist-frame"), svg = $("dist-chart");
    const W = frame.clientWidth, H = frame.clientHeight;
    if (!W || !H) return;
    const bins = U.iv_distribution;
    const shown = viewRows().length;
    const m = { top: 24, right: 10, bottom: 22, left: 36 };
    const pw = W - m.left - m.right, ph = H - m.top - m.bottom;
    const step = niceStep(Math.max(...bins.map((b) => b.count), 1) / 3);
    const yMax = Math.ceil(Math.max(...bins.map((b) => b.count), 1) / step) * step;
    const bw = pw / bins.length;
    const base = m.top + ph;
    const y = (c) => base - (c / yMax) * ph;
    let out = `<desc id="dist-desc">Histogram of 30-day implied volatility across ${nf0.format(U.with_iv)} stocks${isNum(cutoff) ? `; this view's top ${shown} start at ${pct(cutoff)}` : ""}.</desc>`;
    for (let t = 0; t <= yMax; t += step) {
      out += `<line class="grid-line" x1="${m.left}" x2="${W - m.right}" y1="${y(t)}" y2="${y(t)}"/>`;
      out += `<text class="axis-label" x="${m.left - 7}" y="${y(t) + 3.5}" text-anchor="end">${nf0.format(t)}</text>`;
    }
    bins.forEach((b, i) => {
      const lead = isNum(cutoff) && (b.to === null || b.to > cutoff);
      const h = b.count ? Math.max(2, (b.count / yMax) * ph) : 0;
      const x = m.left + i * bw + 1, w = Math.max(1, bw - 2);
      if (h) out += `<path class="dist-bar${lead ? " lead" : ""}" d="${barPath(x, base, w, h, 3)}"/>`;
      const label = b.to === null ? `${b.from}%+` : `${b.from}–${b.to}%`;
      out += `<rect class="dist-hit" x="${m.left + i * bw}" y="${m.top}" width="${bw}" height="${ph}" data-tip="IV30 ${label}: ${nf0.format(b.count)} ${b.count === 1 ? "stock" : "stocks"}"/>`;
    });
    out += `<line class="axis-line" x1="${m.left}" x2="${W - m.right}" y1="${base}" y2="${base}"/>`;
    [0, 5, 10, 15, 20].forEach((i) => {
      out += `<text class="axis-label" x="${m.left + i * bw}" y="${H - 6}" text-anchor="${i === 20 ? "start" : "middle"}">${i * 10}%${i === 20 ? "+" : ""}</text>`;
    });
    if (isNum(cutoff)) {
      const xc = m.left + Math.min(cutoff / 10, 20) * bw;
      const flip = xc > W - 170;
      out += `<line class="cutoff-line" x1="${xc}" x2="${xc}" y1="${m.top - 8}" y2="${base}"/>`;
      out += `<text class="cutoff-label" x="${flip ? xc - 6 : xc + 6}" y="${m.top - 10}" text-anchor="${flip ? "end" : "start"}">Top ${shown} ≥ ${pct(cutoff)}</text>`;
    }
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = out;
  };

  /* ---------- render ---------- */
  const MARKET_LABEL = { all: "US and TSX listings", us: "US-listed only", tsx: "TSX-listed (incl. interlisted)" };
  const render = () => {
    U = (DATA.universe_by_cap && DATA.universe_by_cap[state.cap]) || DATA.universe;
    renderMacro();
    renderStatic();
    $("cap-panel").setAttribute("aria-labelledby", `cap-${state.cap}`);
    document.querySelectorAll("#cap-tabs button").forEach((b) => {
      const on = b.dataset.cap === state.cap;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
    });
    const view = viewRows();
    const maxIv = Math.max(1, ...view.map((r) => r.iv30));
    $("rows").innerHTML = sortRows(view).map((r) => rowHtml(r, maxIv)).join("");
    $("empty").hidden = view.length > 0;
    $("top-n").textContent = String(view.length);
    $("price-head").textContent = `Price · ${state.frame} bars · ${FRAME_WINDOW[state.frame]}`;
    $("board-sub").textContent = [
      CAP_LABEL[state.cap],
      MARKET_LABEL[state.market],
      state.hqOnly ? "US/Canada HQ only" : "any HQ country",
      state.cap === "watch" ? "all watched tickers · no cap or location filters" : S.excluded_short,
      "select a row for details",
    ].filter(Boolean).join(" · ");
    $("dist-frame").hidden = state.cap === "watch";
    $("dist-note").hidden = state.cap === "watch";
    renderFigures(view);
    if (state.cap === "watch") $("figures").innerHTML = `<div class="figure">${watched.size} watched tickers · ${view.length} with data in this report. Stars save your favorites on this computer.</div>`;
    drawDistribution(view.length ? view[view.length - 1].iv30 : null);
    document.querySelectorAll("th[data-sort]").forEach((th) => {
      const active = th.dataset.sort === state.sort.key;
      th.setAttribute("aria-sort", active ? (state.sort.dir === "asc" ? "ascending" : "descending") : "none");
    });
    document.querySelectorAll("#market-seg button").forEach((b) => {
      const on = b.dataset.market === state.market;
      b.setAttribute("aria-checked", String(on));
      b.tabIndex = on ? 0 : -1;
    });
    document.querySelectorAll("#frame-seg button").forEach((b) => {
      const on = b.dataset.frame === state.frame;
      b.setAttribute("aria-checked", String(on));
      b.tabIndex = on ? 0 : -1;
    });
    $("hq-only").checked = state.hqOnly;
  };

  const renderStatic = () => {
    const updated = stamp.format(new Date(DATA.generated_at));
    const cap = compact(S.min_market_cap_usd, "US$");
    $("lede").textContent = state.cap === "watch" ? `Your favorite tickers, independent of market cap and IV rank. ${[...watched].filter(symbol => !DATA.rows.some(r => r.symbol === symbol)).join(", ") || "No tickers"} without available data in this report (newly added, missing IV, or a provider failure). Add or remove tickers in the watchlist controls above. New additions enter the next download; unavailable quotes are never invented.` : `The top ${TOP_N} stocks by 30-day implied volatility in the ${CAP_LABEL[state.cap]} market-cap band, drawn from ${nf0.format(U.total)} US and Canadian listings. Each tab has the same data, charts and filters.`;
    $("session").innerHTML = [
      ["Session", longDate.format(day(DATA.quote_date))],
      ["Updated", updated],
      ["Universe", `${nf0.format(U.us)} US · ${nf0.format(U.tsx_only)} TSX-only`],
      ["With IV", nf0.format(U.with_iv)],
    ].map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("");
    $("dist-note").textContent = `IV30 across ${nf0.format(U.with_iv)} ${CAP_LABEL[state.cap]} stocks with options`;
    const h = S.squeeze_high, e = S.squeeze_elevated;
    const share = (f) => `${nf0.format(f * 100)}%`;
    document.querySelectorAll('[data-setting="cap"]').forEach((n) => { n.textContent = cap; });
    document.querySelectorAll('[data-setting="excluded"]').forEach((n) => { n.textContent = S.excluded_label; });
    document.querySelectorAll('[data-setting="small-float"]').forEach((n) => { n.textContent = compact(S.small_float_shares); });
    document.querySelectorAll('[data-setting="squeeze"]').forEach((n) => {
      n.textContent = `High means short interest of at least ${share(h.short_pct_float)} of float with ${h.days_to_cover}+ days to cover, or ${share(h.short_pct_alone)} on its own. Elevated means ${share(e.short_pct_float)} with ${e.days_to_cover}+ days, or ${share(e.short_pct_alone)} on its own.`;
    });
    $("foot-meta").textContent = `Built ${updated} · ${nf0.format(U.interlisted)} Canadian companies counted on their US listing`;
  };

  /* ---------- interaction ---------- */
  const tip = $("tip");
  const showTip = (text, cx, cy) => {
    tip.textContent = text;
    tip.hidden = false;
    const box = tip.getBoundingClientRect();
    let left = cx + 14, top = cy - box.height - 12;
    if (left + box.width > window.innerWidth - 8) left = cx - box.width - 14;
    if (top < 8) top = cy + 18;
    tip.style.left = `${Math.max(8, left)}px`;
    tip.style.top = `${top}px`;
  };
  const hideTip = () => { tip.hidden = true; };

  let probed = null;
  const clearProbe = () => {
    if (probed) probed.querySelector(".probe").setAttribute("hidden", "");
    probed = null;
  };
  const probeSpark = (svg, ev) => {
    const points = seriesPoints(svg.dataset.symbol, svg.dataset.series);
    if (!points) return;
    const box = svg.getBoundingClientRect();
    const viewW = svg.viewBox.baseVal.width || SPARK.w;
    const viewH = svg.viewBox.baseVal.height || SPARK.h;
    const rel = ((ev.clientX - box.left) / box.width) * viewW;
    const i = Math.round(((rel - SPARK.pad) / (viewW - SPARK.pad * 2)) * (points.values.length - 1));
    const idx = Math.max(0, Math.min(points.values.length - 1, i));
    const g = geometry(points.values, viewW, viewH, SPARK.pad);
    if (probed !== svg) clearProbe();
    const dot = svg.querySelector(".probe");
    dot.setAttribute("cx", g.x(idx).toFixed(1));
    dot.setAttribute("cy", g.y(points.values[idx]).toFixed(1));
    dot.removeAttribute("hidden");
    probed = svg;
    showTip(points.labels[idx], ev.clientX, ev.clientY);
  };

  /* Values explain themselves at once; column headers wait for a deliberate 2-second hover. */
  let tipTarget = null, tipTimer = null, tipVisible = false, tipPoint = { x: 0, y: 0 };
  const dropTip = () => {
    clearTimeout(tipTimer);
    tipTimer = null;
    tipTarget = null;
    tipVisible = false;
    hideTip();
  };
  const queueTip = (el, x, y) => {
    tipPoint = { x, y };
    if (el === tipTarget) {
      if (tipVisible) showTip(el.dataset.tip, x, y);
      return;
    }
    clearTimeout(tipTimer);
    hideTip();
    tipTarget = el;
    tipVisible = false;
    const wait = Number(el.dataset.tipWait || 0);
    if (!wait) {
      tipVisible = true;
      showTip(el.dataset.tip, x, y);
      return;
    }
    tipTimer = setTimeout(() => {
      tipVisible = true;
      showTip(el.dataset.tip, tipPoint.x, tipPoint.y);
    }, wait);
  };

  document.addEventListener("pointermove", (ev) => {
    const target = ev.target instanceof Element ? ev.target : null;
    const spark = target && target.closest(".spark");
    if (spark) {
      dropTip();
      return probeSpark(spark, ev);
    }
    clearProbe();
    const tipped = target && target.closest("[data-tip]");
    if (tipped) queueTip(tipped, ev.clientX, ev.clientY);
    else dropTip();
  });
  document.addEventListener("pointerleave", () => { clearProbe(); dropTip(); });
  document.addEventListener("focusin", (ev) => {
    const tipped = ev.target instanceof Element ? ev.target.closest("[data-tip]") : null;
    dropTip();
    if (!tipped) return;
    const box = tipped.getBoundingClientRect();
    tipTarget = tipped;
    tipVisible = true;
    showTip(tipped.dataset.tip, box.left + 12, box.top - 2);
  });
  document.addEventListener("focusout", dropTip);
  window.addEventListener("scroll", dropTip, { passive: true });

  /* Segmented controls: click, plus arrow keys inside the group. */
  const wireSeg = (id, attr, apply, selected = "aria-checked") => {
    const seg = $(id);
    seg.addEventListener("click", (ev) => {
      const button = ev.target.closest(`button[data-${attr}]`);
      if (button) { apply(button.dataset[attr]); render(); }
    });
    seg.addEventListener("keydown", (ev) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(ev.key)) return;
      ev.preventDefault();
      const buttons = [...seg.querySelectorAll("button")];
      const current = buttons.findIndex((b) => b.getAttribute(selected) === "true");
      const index = ev.key === "Home" ? 0 : ev.key === "End" ? buttons.length - 1
        : (current + (ev.key === "ArrowRight" ? 1 : buttons.length - 1)) % buttons.length;
      const next = buttons[index];
      apply(next.dataset[attr]);
      render();
      next.focus();
    });
  };
  wireSeg("cap-tabs", "cap", (v) => { state.cap = v; state.open.clear(); persist(); }, "aria-selected");
  wireSeg("market-seg", "market", (v) => { state.market = v; persist(); });
  wireSeg("frame-seg", "frame", (v) => { state.frame = v; persist(); });
  $("hq-only").addEventListener("change", (ev) => { state.hqOnly = ev.target.checked; persist(); render(); });

  document.querySelector("thead").addEventListener("click", (ev) => {
    const th = ev.target.closest("th[data-sort]");
    if (!th) return;
    const key = th.dataset.sort;
    state.sort = state.sort.key === key
      ? { key, dir: state.sort.dir === "asc" ? "desc" : "asc" }
      : { key, dir: DEFAULT_DIR[key] || "desc" };
    render();
  });

  const toggleRow = (symbol) => {
    if (state.open.has(symbol)) state.open.delete(symbol);
    else state.open.add(symbol);
    render();
    const again = document.querySelector(`tr.row[data-symbol="${CSS.escape(symbol)}"]`);
    if (again) again.focus({ preventScroll: true });
  };
  $("rows").addEventListener("click", async (ev) => {
    const star = ev.target.closest("button[data-watch]");
    if (star) {
      try {
        const status = await (await fetch("/api/status")).json();
        const response = await fetch("/api/watchlist", {method:"POST", headers:{"Content-Type":"application/json", "X-App-Token":status.token}, body:JSON.stringify({symbol:star.dataset.watch, action:watched.has(star.dataset.watch) ? "remove" : "add"})});
        if (!response.ok) throw Error();
        watched = new Set((await response.json()).watchlist); render();
      } catch { alert("Open the dashboard through the local app to save watchlist changes."); }
      return;
    }
    if (ev.target.closest("a")) return;
    const tr = ev.target.closest("tr.row");
    if (tr) toggleRow(tr.dataset.symbol);
  });
  $("rows").addEventListener("keydown", (ev) => {
    if ((ev.key === "Enter" || ev.key === " ") && ev.target.matches("tr.row")) {
      ev.preventDefault();
      toggleRow(ev.target.dataset.symbol);
    }
  });

  if ("ResizeObserver" in window) new ResizeObserver(() => drawDistribution(lastCutoff)).observe($("dist-frame"));

  window.addEventListener("highiv-watchlist", event => { watched = new Set(event.detail); render(); });
  render();
})();
