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

  const PAGES = ["scan", "sentiment", "leverage", "commodities", "searches"];
  const state = { cap: "mid", market: "all", hqOnly: false, frame: FRAMES.includes("1D") ? "1D" : FRAMES[0], sort: { key: "iv30", dir: "desc" }, open: new Set(), mseries: "aaii", mrange: "1Y", mlog: false, lseries: "finra", lrange: "10Y", llog: false, page: "scan", cseries: "cot", crange: "5Y", clog: false, commodity: null, cgroups: null };
  try {
    const saved = JSON.parse(localStorage.getItem("ivl-view") || "null");
    if (saved && ["mid", "large", "watch"].includes(saved.cap)) state.cap = saved.cap;
    if (saved && ["all", "us", "tsx"].includes(saved.market)) state.market = saved.market;
    if (saved && FRAMES.includes(saved.frame)) state.frame = saved.frame;
    if (saved) state.hqOnly = Boolean(saved.hqOnly);
    if (saved && ["aaii", "vix", "put_call", "fear_greed", "cot", "naaim", "rsi", "macd"].includes(saved.mseries)) state.mseries = saved.mseries;
    if (saved && ["1M", "3M", "6M", "1Y", "5Y", "10Y", "20Y", "MAX"].includes(saved.mrange)) state.mrange = saved.mrange;
    if (saved && ["finra", "z1", "ofr", "ofr_gne", "cot_lev", "etf_flows", "etf_share", "etf_activity"].includes(saved.lseries)) state.lseries = saved.lseries;
    if (saved && ["1M", "3M", "6M", "1Y", "5Y", "10Y", "20Y", "MAX"].includes(saved.lrange)) state.lrange = saved.lrange;
    if (saved && PAGES.includes(saved.page)) state.page = saved.page;
    if (saved && ["3M", "6M", "1Y", "5Y", "10Y", "20Y", "MAX"].includes(saved.crange)) state.crange = saved.crange;
    if (saved && typeof saved.commodity === "string") state.commodity = saved.commodity;
    if (saved) state.clog = saved.clog === true;
    if (saved && Array.isArray(saved.cgroups)) state.cgroups = saved.cgroups.filter((g) => typeof g === "string");
    if (saved) { state.mlog = saved.mlog === true; state.llog = saved.llog === true; }
  } catch (err) { /* storage unavailable: defaults apply */ }
  const persist = () => {
    try {
      localStorage.setItem("ivl-view", JSON.stringify({ cap: state.cap, market: state.market, hqOnly: state.hqOnly, frame: state.frame, mseries: state.mseries, mrange: state.mrange, mlog: state.mlog,
        lseries: state.lseries, lrange: state.lrange, llog: state.llog, page: state.page, crange: state.crange, clog: state.clog, commodity: state.commodity, cgroups: state.cgroups }));
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
    return `<div class="detail-group sentiment-details"><h4>Stock / sector sentiment · ${isNum(s.score) ? `${s.score}/100 · ${s.label}` : "Insufficient evidence"}</h4>` +
      `<p class="detail-text">${esc(s.method || "Sentiment was not collected in this saved report. Refresh data to collect it.")}</p>` +
      `<div class="sentiment-components">${s.components.filter(c => c.configured !== false).map(c => `<article><h5>${esc(names[c.key] || c.key)} <span>${isNum(c.score) ? `${nf0.format(c.score)}/100` : c.stale ? "Stale · excluded" : "Unavailable"}</span></h5>` +
        `<p>${esc(c.detail)}</p><p class="sentiment-meta">Base weight ${esc(c.weight)}%${isNum(c.score) && isNum(s.score) ? ` · effective ${nf0.format(c.weight / s.coverage * 100)}%` : ""}` +
        `${c.as_of ? ` · As of ${esc(c.as_of)}` : ""}${c.start20 ? ` · 20-session start ${esc(c.start20)}` : ""}` +
        `${c.fetched_at ? ` · ${esc(fetchedLabel(c.fetched_at))}` : ""}${c.status === "cached" ? " · Last good reading; refresh failed" : ""}` +
        ` · ${sentimentLink(c.url, c.source || "Source unavailable")}</p>` +
        ((c.evidence || []).length ? `<ul class="sentiment-evidence">${c.evidence.map(e => `<li>${sentimentLink(e.url, e.title)} <span>${esc(e.source)} · ${esc(e.as_of)} · tone ${esc(e.score)}</span></li>`).join("")}</ul>` : "") +
        `</article>`).join("")}</div></div>`;
  };
  /* AAII is entered by hand each week; the card notes when AAII has published a newer week. */
  const isoET = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" });
  const isoDay = d => d.toISOString().slice(0, 10);
  const plusDays = (d, n) => new Date(d.getTime() + n * 864e5);
  const aaiiWeek = c => {
    if (!/^\d{4}-\d{2}-\d{2}/.test(c.as_of || "")) return null;
    const week = day(c.as_of.slice(0, 10));
    return c.date_label === "reported" ? plusDays(week, -1) : week;  // spreadsheet rows carry Thursday's report date
  };
  // Survey weeks close on Wednesday and AAII publishes them on Thursday: the latest week out as of today (ET).
  const aaiiPublished = () => { const today = day(isoET.format(new Date(Date.now()))); return plusDays(today, -((today.getUTCDay() + 3) % 7) - 1); };
  let aaiiEditing = false;
  const aaiiRows = c => {
    const latest = aaiiPublished(), week = aaiiWeek(c);
    const source = c.source_file ? `From AAII's spreadsheet · ${esc(c.source_file)}` : c.entered ? "Entered from AAII's results page" : "";
    return (source ? `<p class="sentiment-meta">${source}</p>` : "") +
      ((!week || week < latest) && !aaiiEditing ? `<p class="sentiment-meta aaii-due"><button type="button" class="link-button" data-aaii-edit>` +
        `${week ? `New week out ${esc(fmtDay(isoDay(plusDays(latest, 1))))} · update` : "Add this week's results"}</button></p>` : "") +
      (aaiiEditing ? `<form class="aaii-form" data-aaii-form novalidate>` +
        `<label>Week ending<input type="date" name="week_ending" value="${isoDay(latest)}" required></label>` +
        ["Bullish", "Neutral", "Bearish"].map(k => `<label>${k} %<input type="number" name="${k.toLowerCase()}" min="0" max="100" step="0.1" inputmode="decimal" required></label>`).join("") +
        `<p class="sentiment-meta">Copy the three percentages from ${sentimentLink(c.url, "AAII's results page")}.</p>` +
        `<p class="aaii-error" role="alert"></p><div class="aaii-actions"><button type="submit">Save</button><button type="button" data-aaii-cancel>Cancel</button></div></form>` : "");
  };
  const replicaParts = parts => (parts || []).length ? `<ul class="replica-parts">${parts.map(p =>
    `<li>${esc(p.name)} · ${isNum(p.score) ? `${nf1.format(p.score)} ${esc(p.rating || "")}` : "Unavailable"}<span>${esc(isNum(p.score) ? p.reading : p.detail || "No fresh input")}</span></li>`).join("")}</ul>` : "";
  const contracts = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${nf0.format(Math.abs(v))}`;
  const cotIndex = (v) => isNum(v) ? ` · index ${nf0.format(v)}` : "";
  const cotRows = (c) => (c.groups || []).map((g, i) => `<p class="sentiment-meta">${i ? `${esc(g.name)} · asset managers` : "Asset managers"} ` +
    `${esc(contracts(g.asset_managers))}${esc(cotIndex(g.asset_managers_index))} · leveraged funds ${esc(contracts(g.leveraged))}${esc(cotIndex(g.leveraged_index))}` +
    (isNum(g.dealers) ? ` · dealers ${esc(contracts(g.dealers))}${esc(cotIndex(g.dealers_index))}` : "") + `</p>`).join("");
  // NAAIM sells current readings; the public table runs three months late, so the card says so and stays out of the label.
  const naaimRows = (c) => `<p class="sentiment-meta">Active managers' average US equity exposure${isNum(c.percentile) ? ` · ${ordinal(c.percentile)} percentile since 2006` : ""}</p>` +
    (Array.isArray(c.quartiles) && c.quartiles.every(isNum) ? `<p class="sentiment-meta">Quartiles ${c.quartiles.map((q) => `${nf0.format(q)}%`).join(" · ")}</p>` : "") +
    `<p class="sentiment-meta naaim-delay">Published three months late; not counted in the Sentiment label${isoDate(c.next_public) ? ` · next week public around ${esc(mediumDate.format(day(c.next_public)))}` : ""}</p>`;
  const renamed = (name) => name === "CNN Fear & Greed" ? "Fear & Greed" : name;  // reports saved before 1.6.0
  const renderMacro = () => {
    const defaults = [
      {key:"vix", name:"VIX", url:"https://www.cboe.com/tradable-products/vix/"},
      {key:"put_call", name:"Put/call ratios", url:"https://www.cboe.com/us/options/market_statistics/daily/"},
      {key:"aaii", name:"AAII sentiment", url:"https://www.aaii.com/sentimentsurvey"},
      {key:"cnn", name:"Fear & Greed", url:"https://www.cnn.com/markets/fear-and-greed"},
      {key:"cot", name:"COT positioning", url:"https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm"},
      {key:"naaim", name:"NAAIM exposure", url:"https://naaim.org/programs/naaim-exposure-index/"},
    ];
    const cards = defaults.map(d => ({...d, ...(DATA.macro_sentiment?.cards || []).find(c => c.key === d.key)}))
      .map(c => ({...c, name: renamed(c.name), usable: isNum(c.value) && ["ok", "cached"].includes(c.status) && sentimentFresh(c.as_of, c.max_age || 4)}));
    const available = cards.filter(c => c.usable);
    renderVerdict("sentiment", sentimentParts());
    $("macro-note").textContent = `${available.length}/${cards.length} fresh readings · Each source keeps its own observation date. ${DATA.macro_sentiment?.checked_at ? `Sources checked ${fetchedLabel(DATA.macro_sentiment.checked_at).replace(/^Fetched /, "")}.` : "Refresh data to collect sentiment."} This panel is independent of the IV scan session.`;
    $("macro-cards").innerHTML = cards.map(c => {
      const stale = isNum(c.value) && !sentimentFresh(c.as_of, c.max_age || 4);
      const tone = !c.usable ? "unknown" : c.direction > 0 ? "positive" : c.direction < 0 ? "negative" : "mixed";
      // A replica is always labelled: it stands in only when CNN has no fresh reading, otherwise it is a cross-check.
      const replica = c.replica && isNum(c.replica.value) && sentimentFresh(c.replica.as_of, 4) ? c.replica : null;
      return `<article class="macro-card"><h3>${esc(c.name)}</h3><div class="macro-value">${esc(c.reading || "—")}</div>` +
        `<span class="sentiment-badge ${tone}">${esc(stale ? "Stale · excluded" : c.usable ? c.signal : "Unavailable")}</span>` +
        // The observation date is underlined so a reading is never taken for today's.
        `<p class="sentiment-meta data-date">${isoDate(String(c.as_of || "").slice(0, 10)) ? `As of ${esc(mediumDate.format(day(c.as_of.slice(0, 10))))}` : "Observation date unavailable"}${c.key === "aaii" && c.as_of ? ` · ${esc(c.date_label || "week ending")}` : ""}</p>` +
        (c.key === "aaii" && [c.bullish, c.neutral, c.bearish].every(isNum)
          ? `<p class="aaii-shares"><span class="bull">Bullish ${nf1.format(c.bullish)}%</span><span class="neutral">Neutral ${nf1.format(c.neutral)}%</span><span class="bear">Bearish ${nf1.format(c.bearish)}%</span></p>` : "") +
        (c.key === "aaii" ? aaiiRows(c) : "") +
        (c.replica_of ? `<p class="sentiment-meta">Not CNN's reading · CNN feed ${c.cnn_status === "stale" && c.cnn_as_of ? `stale since ${esc(c.cnn_as_of)}` : "unavailable"}</p>` : "") +
        (replica && c.usable ? `<p class="sentiment-meta">Replica ${nf1.format(replica.value)} · ${replica.value >= c.value ? "+" : "−"}${nf1.format(Math.abs(replica.value - c.value))} vs CNN</p>` : "") +
        (c.key === "cot" ? cotRows(c) : "") +
        (c.key === "naaim" ? naaimRows(c) : "") +
        (c.ratios ? `<p class="sentiment-meta">Total ${isNum(c.ratios.total) ? nf2.format(c.ratios.total) : "—"} · Index ${isNum(c.ratios.index) ? nf2.format(c.ratios.index) : "—"}</p>` : "") +
        `<details><summary>Evidence &amp; source</summary><p>${esc(c.detail || c.error || "No verified reading in this report. The source may block automated access; no substitute value is estimated.")}</p>` +
        (c.key === "aaii" ? `<p>AAII blocks automated access, so the app does not fetch this survey. Each Thursday, copy the new week's three percentages from ${sentimentLink(c.url, "AAII's results page")} into this card. <button type="button" class="link-button" data-aaii-edit>Enter or correct a week</button></p>` : "") +
        (c.import_note ? `<p>${esc(c.import_note)}</p>` : "") +
        (c.replica_of ? `<p>${esc(c.method || "")}</p>${replicaParts(c.components)}` : "") +
        (replica ? `<p>Replica cross-check · ${esc(replica.coverage)}/7 components · as of ${esc(replica.as_of)}</p>${replicaParts(replica.components)}` : "") +
        (c.observed_at ? `<p>Provider timestamp: ${esc(c.observed_at)}</p>` : "") +
        (c.fetched_at ? `<p>${esc(fetchedLabel(c.fetched_at))}</p>` : "") +
        (c.status === "cached" ? "<p>Last good observation retained; latest refresh failed.</p>" : "") +
        `${sentimentLink(c.url, c.replica_of ? "CNN's index, for comparison" : "Open provider")}</details></article>`;
    }).join("");
    renderMacroChart();
  };

  /* ---------- macro search concerns: independent of sentiment scores ---------- */
  const searchTerms = ["recession", "layoffs", "inflation", "bank failure", "stock market crash", "war"];
  let searchView = "recent";
  const searchChart = c => {
    const historical = searchView === "historical";
    const points = (c.points || []).filter(p => isoDate(p[0]) && isNum(p[1]) && p[1] >= 0 && p[1] <= 100);
    if (points.length < 2) return '<p class="macro-note">History unavailable</p>';
    const path = points.map((p, i) => `${i ? "L" : "M"}${(8 + i / (points.length - 1) * 224).toFixed(1)},${(66 - p[1] * .56).toFixed(1)}`).join(" ");
    const label = p => historical ? p[0].slice(0, 4) : shortDate.format(day(p[0]));
    return `<svg class="search-spark" viewBox="0 0 240 94" role="img" aria-label="${esc(c.term)} ${historical ? "monthly" : "daily"} USA search interest, ${esc(points[0][0])} to ${esc(points.at(-1)[0])}, relative scale 0 to 100">` +
      `<text x="8" y="8">100</text><line x1="8" y1="66" x2="232" y2="66"/><path d="${path}"/><text x="8" y="79">0</text>` +
      `<text x="8" y="92">${esc(label(points[0]))}</text><text x="232" y="92" text-anchor="end">${esc(label(points.at(-1)))}</text></svg>`;
  };
  const renderMacroSearch = () => {
    const panel = DATA.macro_search || {}, historical = searchView === "historical";
    const saved = historical ? panel.historical_cards : panel.cards;
    const now = new Date();
    const cards = searchTerms.map(term => ({term, ...(saved || []).find(c => c.term === term)})).map(c => ({...c, ...searchUsable(c, historical, now)}));
    document.querySelectorAll("#search-view-seg button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.searchView === searchView)));
    const usable = cards.filter(c => c.usable);
    renderVerdict("searches", searchParts());
    const strongest = [...usable].sort((a, b) => historical ? b.percentile - a.percentile : b.ratio - a.ratio)[0];
    $("search-note").textContent = `${usable.length}/${searchTerms.length} usable readings · USA only · ` +
      (historical ? "Latest complete month ranked against earlier months in its full history. " : "Latest complete week vs preceding eight weeks. ") +
      (strongest ? `Strongest relative interest: ${strongest.term}, ${historical ? `${nf1.format(strongest.percentile)} percentile` : `${nf2.format(strongest.ratio)}× its baseline`}. ` : "No usable readings for this view yet. ") +
      "Separate from market sentiment." + (panel.error ? ` ${panel.error}` : "") +
      (panel.paused_until && new Date(panel.paused_until) > now ? ` Google access is paused; collection can retry during a refresh after ${stamp.format(new Date(panel.paused_until))}. Saved readings remain visible.` : "");
    $("search-cards").innerHTML = cards.map(c => {
      const stale = c.as_of && !c.fresh;
      const label = stale ? "Stale · excluded" : c.usable ? c.signal : c.status === "ok" ? "Limited data" : c.signal || "Awaiting data";
      const movement = historical ? (c.history_start ? `Compared with earlier months since ${c.history_start.slice(0, 4)}` : "Full history requested from 2004") :
        isNum(c.change_pct) ? `${c.change_pct > 0 ? "+" : ""}${nf1.format(c.change_pct)}% vs prior week` : "Weekly change unavailable";
      const value = historical ? (isNum(c.percentile) ? `${nf1.format(c.percentile)}<span class="search-unit">percentile</span>` : "—") : (isNum(c.ratio) ? `${nf2.format(c.ratio)}×` : "—");
      return `<article class="macro-card"><h3>${esc(c.term)}</h3><p class="macro-note">${esc(c.theme || "Search attention")}</p>` +
        `<p class="macro-value">${value}</p><span class="sentiment-badge${c.usable && (historical ? c.percentile >= 80 : c.ratio >= 1.5) ? " elevated" : ""}">${esc(label)}</span>` +
        `<p class="sentiment-meta">${esc(movement)}</p>${searchChart(c)}` +
        `<p class="sentiment-meta">${isoDate(c.as_of) ? historical ? `Month: ${esc(monthYear.format(day(c.as_of)))}` : `Data through ${esc(mediumDate.format(day(c.as_of)))}` : "No observation date"}<br>${c.fetched_at ? esc(fetchedLabel(c.fetched_at)) : c.attempted_at ? esc(fetchedLabel(c.attempted_at).replace("Fetched", "Last attempt")) : "Not yet collected"}</p>` +
        (c.error ? `<p class="macro-note">${esc(c.error)}</p>` : "") +
        (c.url ? sentimentLink(c.url, "Open Google Trends · USA") : "") + `</article>`;
    }).join("");
  };

  /* ---------- tab labels: one weighted reading per context tab ---------- */
  // Each input maps its reading onto −1 (bearish) … +1 (bullish) and carries a base weight. Only fresh inputs count;
  // their weights are rescaled to 100%. Computed here rather than when the report is saved, so an older saved
  // report gets the same label as a fresh one.
  const clamp1 = (v, lo = -1, hi = 1) => Math.max(lo, Math.min(hi, v));
  const pctRank = (values, current) => {  // share of the history below the reading, ties counted half: 0–100
    if (values.length < 12) return null;
    let below = 0, tied = 0;
    for (const v of values) { if (v < current) below += 1; else if (v === current) tied += 1; }
    return (below + tied / 2) / values.length * 100;
  };
  const HISTORIC = 2;  // percentile distance from either end of a series' own history that counts as historic
  const VERDICT_BANDS = [[0.5, "Bullish", "positive"], [0.2, "Leaning bullish", "positive"], [-0.2, null, "mixed"], [-0.5, "Leaning bearish", "negative"], [-Infinity, "Bearish", "negative"]];
  const MIN_COVERAGE = 50;
  const verdict = (parts) => {
    const used = parts.filter((p) => isNum(p.score));
    const total = parts.reduce((s, p) => s + p.weight, 0), weight = used.reduce((s, p) => s + p.weight, 0);
    const coverage = total ? weight / total * 100 : 0;
    const rows = parts.map((p) => ({ ...p, share: isNum(p.score) && weight ? p.weight / weight * 100 : null }));
    if (used.length < 3 || coverage < MIN_COVERAGE) return { label: "Insufficient data", tone: "unknown", score: null, coverage, rows };
    const score = used.reduce((s, p) => s + p.score * p.weight, 0) / weight;
    const [, name, tone] = VERDICT_BANDS.find(([floor]) => score >= floor);
    // Near zero, strong inputs pulling both ways read as Mixed; weak inputs all round read as Neutral.
    const label = name || (used.some((p) => p.score >= 0.5) && used.some((p) => p.score <= -0.5) ? "Mixed" : "Neutral");
    const extreme = used.filter((p) => isNum(p.pct) && (p.pct >= 100 - HISTORIC || p.pct <= HISTORIC))
      .sort((a, b) => Math.abs(b.pct - 50) - Math.abs(a.pct - 50))[0];
    return { label, tone, score, coverage, rows, historic: extreme ? { side: extreme.pct >= 50 ? "high" : "low", part: extreme } : null };
  };
  const seriesValues = (points) => (Array.isArray(points) ? points : []).filter((p) => Array.isArray(p) && isNum(p[1]));
  const lastPair = (points) => { const s = seriesValues(points); return s.length ? s[s.length - 1] : null; };
  const signed2 = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${nf2.format(Math.abs(v))}`;

  const sentimentParts = () => {
    const cards = DATA.macro_sentiment?.cards || [], history = DATA.macro_sentiment?.history?.series || {};
    const card = (key) => { const c = cards.find((x) => x.key === key) || {}; return isNum(c.value) && ["ok", "cached"].includes(c.status) && sentimentFresh(c.as_of, c.max_age || 4) ? c : null; };
    const ranked = (series, value) => pctRank(seriesValues(history[series]?.points).map((p) => p[1]), value);
    const spec = [
      // Direct measures of different crowds get the most weight; Fear & Greed is partly built from VIX and put/call.
      { key: "vix", name: "VIX", weight: 25, rule: "20 = 0; 10 points lower = +1, higher = −1", score: (c) => clamp1((20 - c.value) / 10), series: "vix" },
      { key: "aaii", name: "AAII bull–bear spread", weight: 25, rule: "0 = 0; ±25 pp = ±1", score: (c) => clamp1(c.value / 25), series: "aaii" },
      { key: "cot", name: "COT asset managers (3-year index)", weight: 20, rule: "index 50 = 0; 100 = +1, 0 = −1", score: (c) => isNum(c.index) ? clamp1((c.index - 50) / 50) : null },
      { key: "put_call", name: "Equity put/call", weight: 15, rule: "0.70 = 0; 0.45 = +1, 0.95 = −1", score: (c) => clamp1((0.7 - c.value) / 0.25) },
      { key: "cnn", name: "Fear & Greed", weight: 15, rule: "50 = 0; 90 = +1, 10 = −1", score: (c) => clamp1((c.value - 50) / 40), series: "fear_greed" },
    ];
    return spec.map((s) => {
      const c = card(s.key), score = c ? s.score(c) : null;
      return { ...s, score, reading: c ? c.reading : "No fresh reading", as_of: c?.as_of, pct: c && s.series && isNum(score) ? ranked(s.series, c.value) : null };
    });
  };
  // More leverage is more money betting on stocks: the higher a measure sits in its own history, the more bullish.
  // The daily retail data (ProShares 3× fund flows and assets) carries 60%, FINRA's monthly margin debt 30%, and
  // the slow quarterly and weekly sources 10% between them, as context.
  const LEVER_MAX_AGE = { daily: 7, weekly: 21, monthly: 75, quarterly: 200 };
  const SIGNED = ["finra", "cot_lev", "etf_flows"];  // changes, flows and net positions carry a sign
  const shown = (key, unit, v) => unit === "×" ? `${nf2.format(v)}×` : unit !== "%" ? nf2.format(v)
    : SIGNED.includes(key) ? signedFmt(1, "%")(v) : `${nf1.format(v)}%`;
  const leverParts = () => {
    const cards = DATA.market_leverage?.cards || [], series = DATA.market_leverage?.series || {};
    const spec = [
      { key: "etf_flows", card: "etf", name: "Net money into 3× bull minus bear funds, 20 sessions (ProShares)", weight: 35 },
      // Bull funds grew from about 43% to over 90% of these assets since 2010, so they are ranked within 3 years.
      { key: "etf_share", card: "etf", name: "Bull funds' share of 3× fund assets (ProShares)", weight: 25, window: 756 },
      { key: "finra", card: "finra", name: "Margin debt, 12-month change (FINRA)", weight: 30 },
      { key: "ofr_gne", card: "ofr", name: "Hedge fund leverage incl. derivatives (OFR)", weight: 4 },
      { key: "z1", card: "z1", name: "Margin loans ÷ stock market value (Fed Z.1)", weight: 3 },
      { key: "cot_lev", card: "cot", name: "Leveraged funds' net S&P futures (CFTC)", weight: 3, window: 156 },
    ];
    return spec.map((s) => {
      const c = cards.find((x) => x.key === s.card) || {}, values = seriesValues(series[s.key]?.points), last = values[values.length - 1];
      const maxAge = LEVER_MAX_AGE[c.frequency] || 7;
      const fresh = ["ok", "cached"].includes(c.status) && last && sentimentFresh(last[0], maxAge);
      const window = s.window ? values.slice(-s.window) : values;
      const pct = fresh ? pctRank(window.map((p) => p[1]), last[1]) : null, unit = series[s.key]?.unit || "";
      return { ...s, score: isNum(pct) ? clamp1((pct - 50) / 50) : null, pct, as_of: last?.[0],
               rule: `50th percentile = 0; 100th = +1, 0th = −1${s.window ? "; ranked within 3 years" : ""}`,
               reading: fresh ? `${shown(s.key, unit, last[1])} · ${ordinal(Math.round(pct))} pct${s.window ? " (3 yr)" : ""}`
                 : !["ok", "cached"].includes(c.status) ? "Unavailable" : last ? "Stale" : "No reading · refresh data" };
    });
  };
  // Searches: more attention to recession, crashes or war reads bearish. Quiet searches cap below the Bullish band
  // (Leaning bullish at most), because low attention does not mean optimism.
  const SEARCH_CAP = 0.4;
  const searchUsable = (c, historical, now = new Date()) => {
    const lastMonthEnd = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 0)).toISOString().slice(0, 10);
    const fresh = c.status === "ok" && (historical ? c.as_of >= lastMonthEnd : sentimentFresh(c.as_of, 4)) && sentimentFresh(String(c.fetched_at || "").slice(0, 10), historical ? 14 : 4);
    return { fresh, usable: fresh && isNum(historical ? c.percentile : c.ratio) };
  };
  const searchParts = () => {
    const panel = DATA.macro_search || {};
    return searchTerms.map((term) => {
      const recent = (panel.cards || []).find((c) => c.term === term) || {}, past = (panel.historical_cards || []).find((c) => c.term === term) || {};
      const r = searchUsable(recent, false).usable ? clamp1(-(recent.ratio - 1) / 0.5, -1, SEARCH_CAP) : null;
      const h = searchUsable(past, true).usable ? clamp1(-(past.percentile - 50) / 50, -1, SEARCH_CAP) : null;
      // The recent week weighs 60%, the month's place in 20 years 40%; either alone stands in for both.
      const score = isNum(r) && isNum(h) ? 0.6 * r + 0.4 * h : isNum(r) ? r : h;
      const reading = [isNum(r) ? `${nf2.format(recent.ratio)}× baseline` : null, isNum(h) ? `${ordinal(Math.round(past.percentile))} pct since 2004` : null].filter(Boolean).join(" · ");
      return { key: term, name: `“${term}”`, weight: 100 / searchTerms.length, score, reading: reading || "No fresh reading", as_of: recent.as_of || past.as_of,
               pct: isNum(h) ? past.percentile : null, rule: "baseline 1.0× = 0; 1.5× = −1; quieter up to +0.4 · 60% week, 40% month's percentile" };
    });
  };
  const VERDICT_TEXT = {
    sentiment: { scale: "bearish mood … bullish mood", method: "The label describes the current mood across five crowds: options traders pricing volatility (VIX), individual investors (AAII), institutions' futures positions (COT), options volume (put/call) and CNN's composite. VIX and AAII measure different people directly and get the most weight; Fear & Greed counts less because it is partly built from VIX and put/call. Extreme readings are often read contrarian; this label reports the mood, not a forecast. NAAIM's survey of active managers is shown but not counted: its public data runs three months late." },
    leverage: { scale: "less leverage (bearish) … more leverage (bullish)", method: "More leverage means more borrowed or leveraged money betting on stocks, so a measure high in its own history reads bullish. The weighting favours what updates often: ProShares' daily 3× fund data carries 60% (net flows 35%, bull funds' share of assets 25%), FINRA's monthly margin debt 30%, and the quarterly OFR and Fed Z.1 figures and weekly CFTC positions 10% between them. Retail tends to add to bull funds on dips while a rally holds, so heavy inflows can arrive during a selloff; when leveraged holders give up, bull funds' share of assets falls. The same leverage makes a selloff sharper if it unwinds." },

    searches: { scale: "rising worry (bearish) … quiet (up to leaning bullish)", method: "Six Google searches from the USA, equally weighted: rising attention to recession, layoffs, inflation, bank failures, crashes or war scores bearish. Each term blends its latest week against the eight before it (60%) with its latest month's rank since 2004 (40%). Quiet searches score at most +0.4, because low attention does not mean optimism, so this tab never reads fully Bullish." },
  };
  const renderVerdict = (key, parts) => {
    const v = verdict(parts), text = VERDICT_TEXT[key];
    const nuance = v.historic ? ` · historic ${v.historic.side}` : "";
    $(`tab-label-${key}`).textContent = `${v.label}${nuance}`;
    $(`tab-label-${key}`).className = `page-tab-label ${v.tone}`;
    const at = isNum(v.score) ? (v.score + 1) / 2 * 100 : null;
    $(`verdict-${key}`).innerHTML =
      `<div class="verdict-head"><span class="verdict-label ${v.tone}">${esc(v.label)}</span>` +
      (isNum(v.score) ? `<span class="verdict-meter" role="img" aria-label="Weighted score ${signed2(v.score)} on a scale from −1 to +1"><span class="verdict-dot" style="left:${at.toFixed(1)}%"></span></span>` +
        `<span class="verdict-score">${signed2(v.score)} <small>on −1 … +1 · ${esc(text.scale)}</small></span>` : "") + `</div>` +
      `<p class="verdict-note">${nf0.format(v.coverage)}% of the weight has fresh data${v.coverage < 100 ? "; missing inputs are left out and the rest rescaled" : ""}.` +
      (v.label === "Insufficient data" ? ` A label needs at least 3 inputs and ${MIN_COVERAGE}% of the weight.` : "") +
      (v.historic ? ` <strong>Historic ${v.historic.side}:</strong> ${esc(v.historic.part.name)} is at the ${esc(ordinal(Math.round(v.historic.part.pct)))} percentile of its own history.` : "") + `</p>` +
      `<details class="verdict-parts"><summary>How this label is weighted</summary><div class="macro-windows-wrap"><table class="macro-windows">` +
      `<thead><tr><th scope="col">Input</th><th scope="col">Reading</th><th scope="col">Score</th><th scope="col">Base weight</th><th scope="col">Counts for</th><th scope="col">Rule</th></tr></thead><tbody>` +
      v.rows.map((p) => `<tr${isNum(p.score) ? "" : ' class="excluded"'}><th scope="row">${esc(p.name)}</th><td>${esc(p.reading)}</td>` +
        `<td class="${isNum(p.score) ? p.score > 0 ? "up" : p.score < 0 ? "down" : "" : ""}">${isNum(p.score) ? signed2(p.score) : "—"}</td>` +
        `<td>${nf0.format(p.weight)}%</td><td>${isNum(p.share) ? `${nf0.format(p.share)}%` : "excluded"}</td><td class="sub">${esc(p.rule)}</td></tr>`).join("") +
      `</tbody></table></div><p class="macro-note">Bands: +0.50 and above Bullish · +0.20 Leaning bullish · between −0.20 and +0.20 Neutral, or Mixed when strong inputs pull both ways · −0.20 Leaning bearish · −0.50 and below Bearish. ` +
      `“Historic high/low” marks an input within ${HISTORIC} percentile points of either end of its own history. ${esc(text.method)}</p></details>`;
    return v;
  };

  /* ---------- market leverage: every card dated by its own release ---------- */
  const LEVER_CARDS = [
    {key: "finra", name: "Margin debt · FINRA", short: "Margin debt", url: "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics"},
    {key: "z1", name: "Margin loans · Fed Z.1", short: "Margin ÷ stocks", url: "https://www.federalreserve.gov/releases/z1/"},
    {key: "ofr", name: "Hedge fund leverage · OFR", short: "Hedge funds", url: "https://www.financialresearch.gov/hedge-fund-monitor/"},
    {key: "cot", name: "Leveraged funds · CFTC", short: "Leveraged funds", url: "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm"},
    {key: "etf", name: "Leveraged ETFs · daily", short: "3× ETFs", url: "https://finance.yahoo.com/quote/TQQQ/history/"},
    {key: "fsr", name: "Fed Financial Stability Report", short: "Fed FSR", url: "https://www.federalreserve.gov/publications/financial-stability-report.htm"},
  ];
  const CADENCE = {daily: "daily, after each close", weekly: "weekly", monthly: "monthly", quarterly: "quarterly", semiannual: "twice a year"};
  const mediumDate = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  const monthYear = new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
  const isoDate = (v) => /^\d{4}-\d{2}-\d{2}$/.test(v || "");
  // A scheduled date is the publisher's own; an estimate follows the source's usual rhythm and can slip.
  const nextRelease = (c) => {
    if (!isoDate(c.next)) return "Not announced";
    const today = isoET.format(new Date(Date.now()));
    const approx = c.next_basis === "scheduled" ? "" : "~";
    const when = `${approx}${c.next_precision === "month" ? monthYear.format(day(c.next)) : longDate.format(day(c.next))}`;
    const basis = c.next_basis === "scheduled" ? `${c.next_time ? `${esc(c.next_time)} · ` : ""}publisher's schedule` : "estimated";
    if (c.next < today) return `<span class="due">Due since ${esc(when)} · not in this data yet</span>`;
    return `${esc(when)}${c.next === today ? " (today)" : ""} · ${basis}`;
  };
  const renderLeverage = () => {
    const saved = DATA.market_leverage?.cards || [];
    const cards = LEVER_CARDS.map(d => ({...d, ...(saved.find(c => c.key === d.key) || {})}))
      .map(c => ({...c, usable: ["ok", "cached"].includes(c.status) && isoDate(c.as_of)}));
    renderVerdict("leverage", leverParts());
    $("lever-note").textContent = `${cards.filter(c => c.usable).length}/${cards.length} sources available · Each reading is ranked against its own history, and each card gives the period its data covers, when that was released and when the next release is due. ${DATA.market_leverage?.checked_at ? `Sources checked ${fetchedLabel(DATA.market_leverage.checked_at).replace(/^Fetched /, "")}.` : "Refresh data to collect leverage."}`;
    $("lever-cards").innerHTML = cards.map(c => {
      const badge = !c.usable ? "unknown" : c.tone === "high" ? "elevated" : "";
      const covers = c.usable ? `${esc(c.period || mediumDate.format(day(c.as_of)))} · ${esc(CADENCE[c.frequency] || c.frequency || "")}` : "—";
      const dates = c.usable ? `<div class="lever-dates"><span class="data-date"><b>Data</b>${covers}</span>` +
        (c.frequency === "daily" ? "" : `<span><b>Released</b>${isoDate(c.released) ? esc(mediumDate.format(day(c.released))) : "Not recorded"}</span><span><b>Next</b>${nextRelease(c)}</span>`) +
        `</div>` : "";
      return `<article class="macro-card"><h3>${esc(c.name)}</h3><div class="macro-value">${esc(c.usable ? c.reading : "—")}</div>` +
        `<span class="sentiment-badge ${badge}">${esc(c.usable ? c.signal : "Unavailable")}</span>` + dates +
        (c.usable && (c.lines || []).length ? `<ul class="lever-lines">${c.lines.map(line => `<li>${esc(line)}</li>`).join("")}</ul>` : "") +
        `<details><summary>Evidence &amp; source</summary><p>${esc(c.detail || c.error || "No reading in this report. Refresh data to collect it; no substitute value is estimated.")}</p>` +
        (c.fetched_at ? `<p>${esc(fetchedLabel(c.fetched_at))}</p>` : "") +
        (c.status === "cached" ? "<p>Last good reading retained; the latest refresh failed.</p>" : "") +
        (c.pdf ? `<p>${sentimentLink(c.pdf, `Open the ${c.period || ""} report (PDF)`)}</p>` : "") +
        `${sentimentLink(c.url, "Open provider")}</details></article>`;
    }).join("");
    renderLeverChart();
  };

  /* ---------- S&P 500 charts: the index above, one chosen series below, on one time axis ---------- */
  // Two panes rather than two y-scales on one plot: rescaling either axis could make any two lines look related.
  const MACRO_RANGES = [["1M", "1 month", 0, 1], ["3M", "3 months", 0, 3], ["6M", "6 months", 0, 6], ["1Y", "1 year", 1, 0], ["5Y", "5 years", 5, 0], ["10Y", "10 years", 10, 0], ["20Y", "20 years", 20, 0], ["MAX", "Since 1987"]];
  const MACRO_EARLIEST = Date.UTC(1987, 6, 1);  // AAII's survey begins in July 1987
  // Longer than a series' own step: a real gap in the data rather than the wait for its next release.
  const GAP_DAYS = { daily: 21, weekly: 21, monthly: 45, quarterly: 120 };
  const gapOf = (series) => (GAP_DAYS[series.frequency] || 21) * 864e5;
  const signedFmt = (digits, unit) => (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(Math.abs(v))}${unit}`;
  const MACRO_SERIES = {
    aaii: { short: "AAII spread", fmt: signedFmt(1, " pp"), ref: 0, refLabel: "line at 0: bulls = bears",
            // Drawn as the three shares; the spread stays for the table's statistics and correlation.
            lines: [{ key: "bullish", label: "Bullish", cls: "bull" }, { key: "neutral", label: "Neutral", cls: "neutral" }, { key: "bearish", label: "Bearish", cls: "bear" }],
            linesShort: "AAII survey, % of respondents", lineFmt: (v) => `${nf1.format(v)}%` },
    vix: { short: "VIX", fmt: (v) => nf2.format(v), ref: 20, refLabel: "line at 20" },
    put_call: { short: "Put/call", fmt: (v) => nf2.format(v), ref: null },
    fear_greed: { short: "Fear & Greed", fmt: (v) => nf1.format(v), ref: 50, refLabel: "line at 50: neutral" },
    rsi: { short: "RSI 14", fmt: (v) => nf1.format(v), ref: 50, refLabel: "line at 50: gains balance losses", derived: true },
    macd: { short: "MACD", fmt: signedFmt(2, "%"), ref: 0, refLabel: "line at 0: MACD crosses its signal", second: "signal", histogram: true, derived: true },
    cot: { short: "COT net", fmt: signedFmt(1, "%"), ref: 0, refLabel: "line at 0: net flat", second: "dealers" },
    naaim: { short: "NAAIM exposure", fmt: (v) => `${nf1.format(v)}%`, ref: 100, refLabel: "line at 100: fully invested; above uses leverage" },
  };
  const LEVER_SERIES = {
    finra: { short: "Margin debt, 12-month change", fmt: signedFmt(1, "%"), ref: 0, refLabel: "line at 0: no change in a year" },
    z1: { short: "Margin loans ÷ stocks", fmt: (v) => `${nf2.format(v)}%`, ref: null },
    ofr: { short: "Hedge fund leverage", fmt: (v) => `${nf2.format(v)}×`, ref: null },
    ofr_gne: { short: "Hedge funds incl. derivatives", fmt: (v) => `${nf2.format(v)}×`, ref: null },
    cot_lev: { short: "Leveraged funds net", fmt: signedFmt(1, "%"), ref: 0, refLabel: "line at 0: net flat" },
    etf_flows: { short: "3× net flows, % of assets", fmt: signedFmt(1, "%"), ref: 0, refLabel: "line at 0: as much money in as out" },
    etf_share: { short: "Bull share of 3× assets", fmt: (v) => `${nf1.format(v)}%`, ref: 50, refLabel: "line at 50: as much in bull as bear funds" },
    etf_activity: { short: "3× ETF volume ÷ SPY + QQQ", fmt: (v) => `${nf1.format(v)}%`, ref: null },
  };
  const monthsBack = (t, years, months, earliest = MACRO_EARLIEST) => {
    if (years === undefined) return earliest;
    const d = new Date(t); d.setUTCMonth(d.getUTCMonth() - months - 12 * years); return d.getTime();
  };
  const datedPoints = (pairs) => (Array.isArray(pairs) ? pairs : [])
    .filter((p) => Array.isArray(p) && /^\d{4}-\d{2}-\d{2}$/.test(p[0]) && isNum(p[1]))
    .map(([d, v]) => ({ t: day(d).getTime(), d, v })).sort((a, b) => a.t - b.t);
  const lastAtOrBefore = (points, t) => {  // binary search; weekly and quarterly series carry forward between releases
    let lo = 0, hi = points.length - 1, found = null;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (points[mid].t <= t) { found = points[mid]; lo = mid + 1; } else hi = mid - 1; }
    return found;
  };
  const pearson = (a, b) => {
    const n = a.length, ma = a.reduce((s, v) => s + v, 0) / n, mb = b.reduce((s, v) => s + v, 0) / n;
    let ab = 0, aa = 0, bb = 0;
    for (let i = 0; i < n; i++) { ab += (a[i] - ma) * (b[i] - mb); aa += (a[i] - ma) ** 2; bb += (b[i] - mb) ** 2; }
    return aa && bb ? ab / Math.sqrt(aa * bb) : null;
  };
  const macroWindow = (spx, points, start, end, gap) => {
    const px = spx.filter((p) => p.t >= start && p.t <= end), ind = points.filter((p) => p.t >= start && p.t <= end);
    const values = ind.map((p) => p.v);
    // Correlation of changes, at the indicator's own frequency: its change vs the S&P 500's return between the same dates.
    const pairs = ind.map((p) => [p.v, lastAtOrBefore(spx, p.t)]).filter(([, c]) => c);
    const dv = [], dr = [];
    for (let i = 1; i < pairs.length; i++) {
      if (ind[i].t - ind[i - 1].t > gap) continue;  // a change across a data gap is not one step of the series
      dv.push(pairs[i][0] - pairs[i - 1][0]); dr.push(pairs[i][1].v / pairs[i - 1][1].v - 1);
    }
    return {
      px, ind, ret: px.length > 1 ? (px[px.length - 1].v / px[0].v - 1) * 100 : null,
      avg: values.length ? values.reduce((s, v) => s + v, 0) / values.length : null,
      lo: values.length ? Math.min(...values) : null, hi: values.length ? Math.max(...values) : null,
      r: dv.length >= 8 ? pearson(dv, dr) : null, n: dv.length,
    };
  };
  const logTicks = (lo, hi) => {  // 1, 2 and 5 × 10ⁿ; powers of ten alone when that would crowd; null when a range is too narrow
    const out = [];
    for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++) for (const k of [1, 2, 5]) { const v = k * 10 ** e; if (v >= lo && v <= hi) out.push({ v, k }); }
    const powers = out.filter((t) => t.k === 1).map((t) => t.v);
    return out.length > 7 && powers.length >= 2 ? powers : out.length >= 3 ? out.map((t) => t.v) : null;
  };
  const niceTick = (raw) => { const mag = 10 ** Math.floor(Math.log10(raw)); return [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw); };
  const ticks = (lo, hi, count) => {
    const make = (step) => {
      const out = [];
      for (let v = Math.ceil(lo / step) * step; v <= hi + step / 1e6; v += step) out.push(Math.round(v / step) * step || 0);  // never "-0"
      return out;
    };
    let step = niceTick((hi - lo) / count || Math.abs(hi) / 10 || 1);
    if (make(step).length < 3) step = niceTick(step / 2);  // rounding the step up can leave a single label
    return { values: make(step), step };
  };
  const tickLabel = (v, step) => {  // as many decimals as the step needs, and a true minus sign
    let digits = Math.max(0, Math.ceil(-Math.log10(step) - 1e-9));
    if (Math.abs(step * 10 ** digits - Math.round(step * 10 ** digits)) > 1e-9) digits += 1;
    const text = new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(Math.abs(v));
    return v < 0 ? `−${text}` : text;
  };
  const relation = (r) => !isNum(r) ? "—" : `${r >= 0 ? "+" : "−"}${nf2.format(Math.abs(r))} · ${Math.abs(r) < 0.2 ? "little relation" : r > 0 ? "moves with" : "moves against"}`;
  const FREQUENCY_NOUN = { daily: "daily", weekly: "weekly", monthly: "monthly", quarterly: "quarterly" };
  /* One chart per panel: `id` prefixes its elements, `keys` names its state (series, range, log scale). The upper
     pane is the S&P 500 unless `top` names another price (a commodity), with its own format and first date. */
  const makeChart = ({ id, meta: SERIES, spx: spxSource, series: seriesSource, keys, top: topSource = () => ({}) }) => {
    const el = (part) => $(`${id}-${part}`);
    let cache = null, hover = null;
    const chartData = () => {
      const spx = spxSource(), history = seriesSource();
      if (cache && cache.spxSource === spx && cache.history === history) return cache;
      const series = {};
      for (const key of Object.keys(SERIES)) series[key] = { ...(history?.[key] || {}), points: datedPoints(history?.[key]?.points), signal: datedPoints(history?.[key]?.signal),
        lines: Object.fromEntries((SERIES[key].lines || []).map((l) => [l.key, datedPoints(history?.[key]?.lines?.[l.key])])) };
      cache = { spxSource: spx, history, spx: datedPoints(spx), series };
      return cache;
    };
    const render = () => {
      document.querySelectorAll(`#${id}-series-seg button`).forEach((b) => { const on = b.dataset[keys.series] === state[keys.series]; b.setAttribute("aria-checked", String(on)); b.tabIndex = on ? 0 : -1; });
      el("log").checked = state[keys.log];
      document.querySelectorAll(`#${id}-range-seg button`).forEach((b) => { const on = b.dataset[keys.range] === state[keys.range]; b.setAttribute("aria-checked", String(on)); b.tabIndex = on ? 0 : -1; });
      const data = chartData(), series = data.series[state[keys.series]];
      // A series can name its own pane and second line (a futures market's leading and second trader groups).
      const meta = { ...SERIES[state[keys.series]], ...(series.short ? { short: series.short } : {}), ...(series.second_label ? { second: series.second_label } : {}) };
      const name = renamed(series.name) || meta.short, gap = gapOf(series), log = state[keys.log];
      const T = { name: "S&P 500", fmt: (v) => nf2.format(v), earliest: MACRO_EARLIEST, since: "1987", ...topSource() };
      hover = null;
      if (data.spx.length < 2) {
        el("legend").innerHTML = "";
        el("plot").innerHTML = `<p class="macro-empty">${esc(T.name)} history appears after the next data refresh.</p>`;
        el("windows").innerHTML = "";
        el("chart-note").textContent = "";
        el("windows-note").textContent = "";
        return;
      }
      const end = data.spx[data.spx.length - 1].t;
      const [, label, years, months] = MACRO_RANGES.find(([key]) => key === state[keys.range]);
      const phrase = years === undefined ? `since ${T.since}` : `over ${label}`;
      const start = monthsBack(end, years, months, T.earliest);
      const w = macroWindow(data.spx, series.points, start, end, gap);
      const first = series.points[0];
      el("chart-note").textContent = ` · ${name} · ${series.source || ""}${first ? ` · since ${fmtDay(first.d)}, ${first.d.slice(0, 4)}` : ""}`;
      const latestPx = w.px[w.px.length - 1], latestInd = w.ind[w.ind.length - 1];
      // Several lines share the lower pane when the series carries them (AAII's three shares); older reports hold one.
      const multi = (meta.lines || []).map((l) => ({ ...l, points: (series.lines?.[l.key] || []).filter((p) => p.t >= start && p.t <= end), all: series.lines?.[l.key] || [] }))
        .filter((l) => l.all.length);
      const lastOf = (points) => points[points.length - 1];
      el("legend").innerHTML =
        `<span><i class="key key-spx" aria-hidden="true"></i>${esc(T.name)} ${latestPx ? esc(T.fmt(latestPx.v)) : "—"}${isNum(w.ret) ? ` · ${w.ret >= 0 ? "+" : "−"}${nf1.format(Math.abs(w.ret))}% ${phrase}` : ""}</span>` +
        (multi.length ? multi.map((l) => `<span><i class="key key-${l.cls}" aria-hidden="true"></i>${esc(l.label)} ${l.points.length ? `${esc(meta.lineFmt(lastOf(l.points).v))} · ${esc(fmtDay(lastOf(l.points).d))}` : "—"}</span>`).join("")
          : `<span><i class="key key-ind" aria-hidden="true"></i>${esc(name)} ${latestInd ? esc(meta.fmt(latestInd.v)) : "—"}${latestInd ? ` · ${esc(fmtDay(latestInd.d))}` : ""}</span>`) +
        (meta.second && series.signal.length ? `<span><i class="key key-ind2" aria-hidden="true"></i>${esc(meta.second)} ${esc(meta.fmt(series.signal[series.signal.length - 1].v))}</span>` : "");
      const W = Math.max(240, el("plot").clientWidth || 960);
      const m = { left: 64, right: 14 }, top = 22, h1 = 186, gapY = 34, h2 = 104, axis = 22, H = top + h1 + gapY + h2 + axis;  // pane labels sit above each pane
      const x = (t) => m.left + ((t - start) / Math.max(end - start, 1)) * (W - m.left - m.right);
      const pane = (points, y0, height, ref, logScale = false) => {  // log: equal percentage moves take equal height
        const f = logScale ? Math.log10 : (v) => v, inv = (v) => (logScale ? 10 ** v : v);
        const values = points.map((p) => f(p.v)).concat(isNum(ref) ? [f(ref)] : []);
        let lo = Math.min(...values), hi = Math.max(...values);
        if (lo === hi) { lo -= 1; hi += 1; }
        const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
        const decades = logScale ? logTicks(inv(lo), inv(hi)) : null;
        const scale = decades ? { values: decades, step: 1 } : ticks(inv(lo), inv(hi), height > 150 ? 4 : 3);
        return { y: (v) => y0 + (1 - (f(v) - lo) / (hi - lo)) * height, step: scale.step, ticks: scale.values.filter((v) => f(v) >= lo && f(v) <= hi) };
      };
      const path = (points, y, step) => points.map((p, i) => `${i && p.t - points[i - 1].t <= step ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
      const top2 = top + h1 + gapY;
      // MACD's signal line shares the pane, so the scale covers both lines and the bars between them.
      const second = meta.second ? series.signal.filter((p) => p.t >= start && p.t <= end) : [];
      const signalAt = new Map(second.map((p) => [p.t, p.v]));
      const bars = meta.histogram ? w.ind.filter((p) => signalAt.has(p.t)).map((p) => ({ t: p.t, v: p.v - signalAt.get(p.t) })) : [];
      const drawn = multi.flatMap((l) => l.points);
      const P = pane(w.px, top, h1, null, log);
      const I = multi.length ? (drawn.length ? pane(drawn, top2, h2, null) : null) : w.ind.length ? pane(w.ind.concat(second, bars), top2, h2, meta.ref) : null;
      const grid = (p, x1) => p.ticks.map((v) => `<line class="grid" x1="${m.left}" x2="${W - m.right}" y1="${p.y(v).toFixed(1)}" y2="${p.y(v).toFixed(1)}"/>` +
        `<text class="tick" x="${x1}" y="${(p.y(v) + 4).toFixed(1)}" text-anchor="end">${tickLabel(v, p.step)}</text>`).join("");
      // Date labels on calendar boundaries: (even) Januaries for multi-year ranges, quarters for a year,
      // every other month for six months, even spacing below that.
      const span = end - start, days = span / 864e5, xTicks = [];
      if (days <= 120) for (let i = 0; i <= 4; i++) xTicks.push(start + (span * i) / 4);
      else {
        const every = days > 20 * 365 ? 60 : days > 6.5 * 365 ? 24 : days > 400 ? 12 : days > 200 ? 3 : 2, firstTick = new Date(start);
        for (let d = new Date(Date.UTC(firstTick.getUTCFullYear(), firstTick.getUTCMonth() + 1, 1)); d.getTime() <= end; d.setUTCMonth(d.getUTCMonth() + 1)) {
          if ((d.getUTCFullYear() * 12 + d.getUTCMonth()) % every === 0) xTicks.push(d.getTime());
        }
      }
      const xFmt = (t) => { const d = new Date(t); return span <= 120 * 864e5 ? shortDate.format(d) : span <= 400 * 864e5 ? `${d.toLocaleString("en-US", { month: "short", timeZone: "UTC" })} ${d.getUTCFullYear()}` : String(d.getUTCFullYear()); };
      const anchor = (t) => x(t) < m.left + 24 ? "start" : x(t) > W - m.right - 24 ? "end" : "middle";
      const labels = [];  // skip labels that would touch on narrow screens (about 7px per mono character)
      for (const t of xTicks) if (!labels.length || x(t) - x(labels[labels.length - 1]) >= (xFmt(t).length + 2) * 7) labels.push(t);
      // Every session gets a bar while there is room; on longer ranges bars group into periods, each keeping its
      // last session's value, the way a weekly or monthly chart does. The lines stay at full daily detail.
      const room = Math.max(1, Math.floor((W - m.left - m.right) / 2.4));
      const perBar = Math.max(1, Math.ceil(bars.length / room));
      const grouped = perBar === 1 ? bars : bars.filter((_, i) => i % perBar === perBar - 1 || i === bars.length - 1);
      const barWidth = Math.max(1, Math.min(8, ((W - m.left - m.right) / Math.max(grouped.length, 1)) * 0.62));
      const barSpan = grouped.length > 1 ? (grouped[grouped.length - 1].t - grouped[0].t) / (grouped.length - 1) / 864e5 : 0;
      const barPeriod = perBar === 1 ? "" : barSpan <= 10 ? "weekly bars" : barSpan <= 45 ? "monthly bars" : barSpan <= 120 ? "quarterly bars" : "yearly bars";
      const histogram = I ? grouped.map((b, i) => {
        const shrinking = i && Math.abs(b.v) < Math.abs(grouped[i - 1].v);
        const y0 = I.y(0), y1 = I.y(b.v);
        return `<rect class="hist ${b.v >= 0 ? "hist-up" : "hist-down"}${shrinking ? " hist-fading" : ""}" x="${(x(b.t) - barWidth / 2).toFixed(1)}" ` +
          `y="${Math.min(y0, y1).toFixed(1)}" width="${barWidth.toFixed(2)}" height="${Math.max(Math.abs(y1 - y0), 0.5).toFixed(1)}"/>`;
      }).join("") : "";
      // Sparse series (weekly or slower) mark each observation while few enough fit, so releases read as points.
      const sparse = (points) => I && points.length <= 60 && series.frequency && series.frequency !== "daily";
      const markers = multi.length
        ? multi.map((l) => sparse(l.points) ? l.points.map((p) => `<circle class="dot-${l.cls}" cx="${x(p.t).toFixed(1)}" cy="${I.y(p.v).toFixed(1)}" r="3.5"/>`).join("") : "").join("")
        : sparse(w.ind) ? w.ind.map((p) => `<circle class="dot-ind" cx="${x(p.t).toFixed(1)}" cy="${I.y(p.v).toFixed(1)}" r="4"/>`).join("") : "";
      el("plot").innerHTML =
        `<svg class="macro-svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(T.name)} ${phrase} above ${esc(name)} on the same dates">` +
        grid(P, m.left - 8) + `<path class="line-spx" d="${path(w.px, P.y, GAP_DAYS.daily * 864e5)}"/>` +
        `<text class="pane-label" x="${m.left}" y="${top - 9}">${esc(T.name)}${log ? `<tspan class="ref-label"> · log scale</tspan>` : ""}</text>` +
        (I ? grid(I, m.left - 8) +
          (isNum(meta.ref) ? `<line class="ref" x1="${m.left}" x2="${W - m.right}" y1="${I.y(meta.ref).toFixed(1)}" y2="${I.y(meta.ref).toFixed(1)}"/>` : "") +
          (multi.length ? multi.map((l) => `<path class="line-${l.cls}" d="${path(l.points, I.y, gap)}"/>`).join("") + markers
            : histogram + (second.length ? `<path class="line-ind2" d="${path(second, I.y, gap)}"/>` : "") + `<path class="line-ind" d="${path(w.ind, I.y, gap)}"/>${markers}`)
          : `<text class="pane-empty" x="${(W + m.left) / 2}" y="${top2 + h2 / 2}" text-anchor="middle">No ${esc(name)} data in this range${first ? ` · history starts ${esc(fmtDay(first.d))}, ${first.d.slice(0, 4)}` : ""}</text>`) +
        (multi.length ? `<text class="pane-label" x="${m.left}" y="${top2 - 9}">${esc(meta.linesShort)}</text>`
          : `<text class="pane-label" x="${m.left}" y="${top2 - 9}">${esc(meta.short)}${I && meta.refLabel && W >= 460 ? `<tspan class="ref-label"> · ${esc(meta.refLabel)}${barPeriod ? ` · ${barPeriod}` : ""}</tspan>` : ""}</text>`) +
        labels.map((t) => `<text class="tick" x="${x(t).toFixed(1)}" y="${H - 6}" text-anchor="${anchor(t)}">${xFmt(t)}</text>`).join("") +
        `<g class="xhair" hidden><line x1="0" x2="0" y1="${top}" y2="${top2 + h2}"/><circle class="dot-spx" r="4"/>` +
        (multi.length ? multi.map((l) => `<circle class="dot-${l.cls}" data-line="${l.key}" r="4"/>`).join("") : `<circle class="dot-ind" r="4"/>`) + `</g>` +
        `<rect class="hit" x="${m.left}" y="${top}" width="${W - m.left - m.right}" height="${top2 + h2 - top}"/></svg>`;
      hover = { x, P, I, w, start, end, name, meta, W, m, multi, T };
      const unit = FREQUENCY_NOUN[series.frequency] || "daily";
      const weeklyNote = unit === "daily" ? " Points older than 10 years are weekly, so long ranges mix weekly and daily changes." : "";
      const columns = multi.length ? multi.map((l) => `<th scope="col">${esc(l.label)} average</th>`).join("")
        : `<th scope="col">${esc(meta.short)} average</th><th scope="col">${esc(meta.short)} low – high</th>`;
      el("windows").innerHTML = `<thead><tr><th scope="col">Range</th><th scope="col">${esc(T.name)}</th>${columns}` +
        `<th scope="col">Correlation of changes${multi.length ? ` · ${esc(meta.short)}` : ""}</th></tr></thead><tbody>` +
        MACRO_RANGES.map(([key, rangeLabel, y, mo]) => {
          const from = monthsBack(end, y, mo, T.earliest), s = macroWindow(data.spx, series.points, from, end, gap);
          const stats = multi.length ? multi.map((l) => { const a = macroWindow(data.spx, l.all, from, end, gap).avg; return `<td>${isNum(a) ? esc(meta.lineFmt(a)) : "—"}</td>`; }).join("")
            : `<td>${isNum(s.avg) ? esc(meta.fmt(s.avg)) : "—"}</td><td>${isNum(s.lo) ? `${esc(meta.fmt(s.lo))} – ${esc(meta.fmt(s.hi))}` : "—"}</td>`;
          return `<tr${key === state[keys.range] ? ' class="current"' : ""}><th scope="row">${key === "MAX" ? `Since ${esc(T.since)}` : rangeLabel}</th>` +
            `<td class="${isNum(s.ret) ? (s.ret >= 0 ? "up" : "down") : ""}">${isNum(s.ret) ? `${s.ret >= 0 ? "+" : "−"}${nf1.format(Math.abs(s.ret))}%` : "—"}</td>` + stats +
            `<td>${esc(relation(s.r))}${s.n >= 8 ? ` <span class="sub">n=${nf0.format(s.n)}</span>` : ""}</td></tr>`;
        }).join("") + "</tbody>";
      el("windows-note").textContent = `${multi.length ? `The chart draws the three shares; the correlation uses the bull–bear spread (bullish minus bearish). ` : ""}Correlation compares each ${unit} change in ${multi.length ? "the spread" : name} with the ${T.name}'s return between the same dates: +1 moves together, −1 moves opposite, near 0 little relation. Changes across gaps in the data are left out.${weeklyNote} It describes the past, not a forecast; needs 8 changes.${meta.derived ? ` ${name} is calculated from the S&P 500's own closes, so this correlation reflects that arithmetic rather than a relationship between two sources.` : ""}`;
    };
    const move = (ev) => {
      const svg = ev.target.closest(".macro-svg");
      if (!svg || !hover) return;
      const { x, P, I, w, start, end, name, meta, W, m, multi, T } = hover;
      const box = svg.getBoundingClientRect();
      const t = start + ((ev.clientX - box.left) * (W / box.width) - m.left) / (W - m.left - m.right) * (end - start);
      const px = lastAtOrBefore(w.px, t) || w.px[0];
      if (!px) return;
      const ind = I ? lastAtOrBefore(w.ind, px.t) : null;
      const hair = svg.querySelector(".xhair");
      hair.removeAttribute("hidden");
      hair.querySelector("line").setAttribute("x1", x(px.t).toFixed(1));
      hair.querySelector("line").setAttribute("x2", x(px.t).toFixed(1));
      const [dotPx, dotInd] = hair.querySelectorAll("circle");
      dotPx.setAttribute("cx", x(px.t).toFixed(1)); dotPx.setAttribute("cy", P.y(px.v).toFixed(1));
      if (multi.length) {
        const values = multi.map((l) => ({ l, p: I ? lastAtOrBefore(l.points, px.t) : null }));
        for (const { l, p } of values) {
          const dot = hair.querySelector(`[data-line="${l.key}"]`);
          if (p) { dot.removeAttribute("hidden"); dot.setAttribute("cx", x(px.t).toFixed(1)); dot.setAttribute("cy", I.y(p.v).toFixed(1)); }
          else dot.setAttribute("hidden", "");
        }
        const week = values.find((v) => v.p)?.p;
        showTip(`${longDate.format(day(px.d))} · ${T.name} ${T.fmt(px.v)} · AAII${week && week.d !== px.d ? ` (${fmtDay(week.d)})` : ""} ` +
          values.map(({ l, p }) => `${l.label.toLowerCase()} ${p ? meta.lineFmt(p.v) : "—"}`).join(", "), ev.clientX, ev.clientY);
        return;
      }
      if (ind) { dotInd.removeAttribute("hidden"); dotInd.setAttribute("cx", x(px.t).toFixed(1)); dotInd.setAttribute("cy", I.y(ind.v).toFixed(1)); }
      else dotInd.setAttribute("hidden", "");
      showTip(`${longDate.format(day(px.d))} · ${T.name} ${T.fmt(px.v)} · ${name} ${ind ? `${meta.fmt(ind.v)}${ind.d !== px.d ? ` (${fmtDay(ind.d)})` : ""}` : "—"}`, ev.clientX, ev.clientY);
    };
    const leave = () => { el("plot").querySelector?.(".xhair")?.setAttribute("hidden", ""); hideTip(); };
    return { render, move, leave };
  };
  const spxHistory = () => DATA.macro_sentiment?.history?.spx;
  const macroChart = makeChart({ id: "macro", meta: MACRO_SERIES, spx: spxHistory, series: () => DATA.macro_sentiment?.history?.series,
                                 keys: { series: "mseries", range: "mrange", log: "mlog" } });
  const leverChart = makeChart({ id: "lever", meta: LEVER_SERIES, spx: spxHistory, series: () => DATA.market_leverage?.series,
                                 keys: { series: "lseries", range: "lrange", log: "llog" } });
  const renderMacroChart = macroChart.render, renderLeverChart = leverChart.render;

  /* ---------- futures: each price above its CFTC positioning; physical commodities, then financial futures ---------- */
  // The leading trader group differs by market: managed money in commodities (producers hedge on the other side),
  // asset managers in stock indices, Treasuries and crypto (leveraged funds' shorts there are mostly basis trades),
  // leveraged funds in currencies, short-term rates and VIX. The data names both groups for each market.
  const COMMODITY_SERIES = { cot: { short: "Net position", fmt: signedFmt(1, "%"), ref: 0, refLabel: "line at 0: net flat", second: "second group" } };
  const nf3 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
  const nf4 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 4 });
  const priceFmt = (v) => !isNum(v) ? "—" : v < 0.1 ? String(v) : v < 10 ? (v < 2 ? nf4 : nf3).format(v) : nf2.format(v);
  // Reports saved by 3.3.0 hold commodities only, under the older field names.
  const cmdtySections = () => {
    const panel = DATA.commodities || {};
    if (panel.sections) return panel.sections;
    if (!panel.groups) return [];
    const legacy = (m) => ({ ...m, lead_name: "Managed money", second_name: "Producers", lead_pct: m.managed_pct, second_pct: m.producers_pct,
                             lead_history: m.managed_history, second_history: m.producer_history });
    return [{ key: "physical", name: "Physical commodities", as_of: panel.as_of, groups: panel.groups.map((g) => ({ ...g, markets: (g.markets || []).map(legacy) })),
              contracts: (panel.contracts || []).map((c) => ({ ...c, spec_pct: c.managed_pct })) }];
  };
  const cmdtyMarkets = () => cmdtySections().flatMap((s) => (s.groups || []).flatMap((g) => g.markets || []));
  const cmdtyMarket = () => { const all = cmdtyMarkets(); return all.find((m) => m.code === state.commodity) || all[0] || null; };
  const cmdtySeries = new Map();  // one stable object per market, so the chart's cache sees the same history
  const cmdtyHistory = (m) => {
    if (!cmdtySeries.has(m.code)) cmdtySeries.set(m.code, { cot: { name: `${m.lead_name} net, % of open interest`, short: `${m.lead_name} net`,
      second_label: (m.second_name || "").toLowerCase(), frequency: "weekly", source: "CFTC Commitments of Traders",
      points: m.lead_history || [], signal: m.second_history || [] } });
    return cmdtySeries.get(m.code);
  };
  const cmdtyChart = makeChart({ id: "cmdty", meta: COMMODITY_SERIES, spx: () => cmdtyMarket()?.price_history, keys: { series: "cseries", range: "crange", log: "clog" },
    series: () => { const m = cmdtyMarket(); return m ? cmdtyHistory(m) : {}; },
    top: () => {
      const m = cmdtyMarket(), first = m?.price_history?.[0]?.[0];
      return m ? { name: m.name, fmt: priceFmt, earliest: first ? day(first).getTime() : MACRO_EARLIEST, since: first ? first.slice(0, 4) : "the first report" } : {};
    } });
  const renderCmdtyChart = cmdtyChart.render;
  const cmdtyTone = (index) => !isNum(index) ? "" : index >= 80 || index <= 20 ? "elevated" : "";
  const cmdtySignal = (m) => {
    if (!isNum(m.index)) return "No 3-year index yet";
    if (m.index > 20 && m.index < 80) return "Typical positioning";
    const long = m.index >= 80;
    if (m.lead_name === "Asset managers") return long ? "Asset managers unusually long" : "Asset managers unusually light";
    if (m.category === "volatility") return long ? "Speculators crowded long volatility" : "Speculators crowded short volatility";
    return long ? "Speculators crowded long" : "Speculators crowded short";
  };
  const signedPct = (v) => isNum(v) ? signedFmt(1, "%")(v) : "—";
  // Positioning at the edge of its own three years: the top or bottom 5% is extreme, the next 15% near-extreme
  // (the same 80/20 line the cards call crowded). Asset managers at the low end are light rather than short.
  const extremeOf = (m) => !isNum(m.index) ? null : m.index >= 95 || m.index <= 5 ? "extreme" : m.index >= 80 || m.index <= 20 ? "near" : null;
  const extremeLabel = (m) => {
    const level = extremeOf(m);
    if (!level) return "";
    const side = m.index >= 50 ? "long" : m.lead_name === "Asset managers" ? "light" : "short";
    return `${level === "extreme" ? "Extreme" : "Near-extreme"} ${side}`;
  };
  const cmdtyGroupKey = (section, group) => `${section.key}:${group.key}`;
  const cmdtyGroupOf = (code) => {
    for (const section of cmdtySections()) for (const group of section.groups || []) if ((group.markets || []).some((m) => m.code === code)) return cmdtyGroupKey(section, group);
    return null;
  };
  const openGroup = (code) => {
    const key = cmdtyGroupOf(code);
    state.cgroups = [...new Set([...(state.cgroups || []), ...(key ? [key] : [])])];
  };
  // Beside the big chart: every section and category, collapsible, each market with its extreme-positioning label.
  const renderCmdtyNav = () => {
    const chosen = cmdtyMarket(), sections = cmdtySections();
    if (state.cgroups === null && chosen) openGroup(chosen.code);  // first visit: the charted market's group is open
    const open = new Set(state.cgroups || []);
    $("cmdty-nav").innerHTML = sections.map((section) => `<p class="cmdty-nav-section">${esc(section.name)}</p>` +
      (section.groups || []).map((group) => {
        const key = cmdtyGroupKey(section, group), markets = group.markets || [];
        const flagged = markets.filter((m) => extremeOf(m)), extreme = flagged.filter((m) => extremeOf(m) === "extreme").length;
        const count = flagged.length ? `<span class="cmdty-nav-flags" title="${extreme} extreme, ${flagged.length - extreme} near-extreme">${extreme ? `${extreme} extreme` : ""}${extreme && flagged.length > extreme ? " · " : ""}${flagged.length > extreme ? `${flagged.length - extreme} near` : ""}</span>` : "";
        return `<details class="cmdty-nav-group" data-group="${esc(key)}"${open.has(key) ? " open" : ""}><summary><span>${esc(group.name)}</span><span class="cmdty-nav-count">${markets.length}</span>${count}</summary><ul>` +
          markets.map((m) => {
            const level = extremeOf(m), on = chosen && m.code === chosen.code;
            return `<li><button type="button" data-commodity="${esc(m.code)}" aria-pressed="${Boolean(on)}"${on ? ' class="selected"' : ""}>` +
              `<span class="cmdty-nav-name">${esc(m.name)}</span>` +
              (level ? `<span class="cmdty-flag ${level}">${esc(extremeLabel(m))}</span>` : "") +
              `<span class="cmdty-nav-index" title="COT index: ${esc(m.lead_name || "")} within 3 years">${isNum(m.index) ? nf0.format(m.index) : "—"}</span></button></li>`;
          }).join("") + `</ul></details>`;
      }).join("")).join("");
  };
  // A small two-pane chart per card: price above, the leading group (blue) and the second group (orange) below.
  const cmdtyMini = (m) => {
    const price = datedPoints(m.price_history), lead = datedPoints(m.lead_history), second = datedPoints(m.second_history);
    if (price.length < 2 && lead.length < 2) return '<p class="macro-note">No history yet</p>';
    const end = Math.max(price.at(-1)?.t || 0, lead.at(-1)?.t || 0);
    const [, , years, months] = MACRO_RANGES.find(([key]) => key === state.crange) || MACRO_RANGES[4];
    const start = monthsBack(end, years, months, Math.min(price[0]?.t ?? end, lead[0]?.t ?? end));
    const W = 300, x = (t) => 4 + (t - start) / Math.max(end - start, 1) * (W - 8);
    const line = (points, y0, h, cls, extra = []) => {
      const inRange = points.filter((p) => p.t >= start && p.t <= end);
      if (inRange.length < 2) return { d: "", y: null };
      const values = inRange.map((p) => p.v).concat(extra);
      let lo = Math.min(...values), hi = Math.max(...values); if (lo === hi) { lo -= 1; hi += 1; }
      const y = (v) => y0 + (1 - (v - lo) / (hi - lo)) * h;
      return { d: `<path class="${cls}" d="${inRange.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("")}"/>`, y };
    };
    const px = line(price, 4, 62, "mini-price");
    // Both position lines share one scale, which always includes zero.
    const shared = lead.concat(second).filter((p) => p.t >= start && p.t <= end).map((p) => p.v).concat([0]);
    const main = line(lead, 78, 38, "mini-managed", shared), other = line(second, 78, 38, "mini-second", shared);
    const zero = main.y ? `<line class="mini-zero" x1="4" x2="${W - 4}" y1="${main.y(0).toFixed(1)}" y2="${main.y(0).toFixed(1)}"/>` : "";
    return `<svg class="cmdty-mini" viewBox="0 0 ${W} 120" preserveAspectRatio="none" role="img" aria-label="${esc(m.name)} price above ${esc(m.lead_name)} and ${esc(m.second_name)} net positions, ${esc(state.crange)}">` +
      `${px.d}${zero}${other.d}${main.d}</svg>`;
  };
  const cmdtyCard = (m, selected) =>
    `<article class="macro-card cmdty-card${selected ? " selected" : ""}" data-commodity="${esc(m.code)}" tabindex="0" role="button" aria-pressed="${selected}" aria-label="Show ${esc(m.name)} on the chart above">` +
    `<h3>${esc(m.name)} <span class="cmdty-exch">${esc(m.exchange)} · ${esc(m.symbol)}</span></h3>` +
    `<div class="macro-value">${esc(priceFmt(m.price))}</div>` +
    `<p class="sentiment-meta">${[["1 month", m.change_1m], ["1 year", m.change_1y]].filter(([, v]) => isNum(v)).map(([k, v]) => `${k} <span class="${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : "−"}${nf1.format(Math.abs(v))}%</span>`).join(" · ") || "Price change unavailable"}</p>` +
    `<span class="sentiment-badge ${cmdtyTone(m.index)}">${esc(cmdtySignal(m))}</span>` +
    `<p class="sentiment-meta cmdty-cot"><i class="key key-ind" aria-hidden="true"></i>${esc(m.lead_name)} <b>${esc(signedPct(m.lead_pct))}</b> of OI${isNum(m.index) ? ` · index ${nf0.format(m.index)}` : ""} ` +
    `· <i class="key key-ind2" aria-hidden="true"></i>${esc((m.second_name || "").toLowerCase())} ${esc(signedPct(m.second_pct))}</p>` +
    cmdtyMini(m) +
    `<p class="sentiment-meta data-date">${isoDate(m.as_of) ? `COT ${esc(mediumDate.format(day(m.as_of)))}` : "No COT report"}${isoDate(m.price_as_of) ? ` · price ${esc(mediumDate.format(day(m.price_as_of)))}` : ""}</p>` +
    `<p class="sentiment-meta">Open interest ${isNum(m.open_interest) ? nf0.format(m.open_interest) : "—"} contracts</p></article>`;
  const cmdtyOthers = (section, names) => {
    const others = section.contracts || [], byCategory = new Map();
    if (!others.length) return "";
    for (const c of others) { if (!byCategory.has(c.category)) byCategory.set(c.category, []); byCategory.get(c.category).push(c); }
    const total = (key) => byCategory.get(key).reduce((s, c) => s + c.open_interest, 0);
    const order = [...byCategory.keys()].sort((a, b) => total(b) - total(a));
    const spec = section.key === "financial" ? "Leveraged funds net" : "Managed money net";
    return `<details class="cmdty-others"><summary>All ${nf0.format(others.length)} other ${section.key === "financial" ? "financial" : "commodity"} contracts in the COT report</summary>` +
      `<div class="macro-windows-wrap"><table class="macro-windows cmdty-others-table"><thead><tr><th scope="col">Contract</th><th scope="col">Exchange</th><th scope="col">Open interest</th><th scope="col">${spec}</th></tr></thead>` +
      order.map((key) => `<tbody><tr class="cmdty-others-group"><th scope="rowgroup" colspan="4">${esc(names[key] || key)} · ${nf0.format(byCategory.get(key).length)}</th></tr>` +
        byCategory.get(key).map((c) => `<tr><th scope="row">${esc(c.name)}</th><td>${esc(c.exchange)}</td><td>${nf0.format(c.open_interest)}</td>` +
          `<td class="${c.spec_pct > 0 ? "up" : c.spec_pct < 0 ? "down" : ""}">${esc(signedPct(c.spec_pct))}</td></tr>`).join("") + `</tbody>`).join("") + `</table></div></details>`;
  };
  const renderCommodities = () => {
    const panel = DATA.commodities || {}, sections = cmdtySections(), markets = cmdtyMarkets(), chosen = cmdtyMarket();
    const others = sections.reduce((s, x) => s + (x.contracts || []).length, 0), categories = sections.reduce((s, x) => s + (x.groups || []).length, 0);
    $("tab-label-commodities").textContent = markets.length ? `${markets.length} markets${isoDate(panel.as_of) ? ` · COT ${fmtDay(panel.as_of)}` : ""}` : "Awaiting data";
    $("tab-label-commodities").className = "page-tab-label";
    $("cmdty-note").innerHTML = markets.length
      ? `<span class="data-date">CFTC positions as of ${esc(longDate.format(day(panel.as_of)))}</span>, released ${esc(isoDate(panel.released) ? longDate.format(day(panel.released)) : "—")}; ` +
        `next report ${esc(isoDate(panel.next) ? longDate.format(day(panel.next)) : "—")}, ${esc(panel.next_time || "")}. Prices through ${esc(isoDate(panel.prices_as_of) ? mediumDate.format(day(panel.prices_as_of)) : "—")}. ` +
        `${markets.length} markets charted in ${categories} categories, each ordered by open interest; ${nf0.format(others)} other contracts listed. Choose a market beside the chart, or a card below, to chart it.` +
        (sections.some((s) => s.status === "cached") || panel.price_status === "cached" || panel.cot_status === "cached" ? " Last good data retained; the latest refresh failed." : "")
      : "Futures prices and CFTC positions appear after the next data refresh.";
    const names = panel.categories || {};
    $("cmdty-groups").innerHTML = sections.map((section) => `<section class="cmdty-section" aria-label="${esc(section.name)}"><h3 class="cmdty-section-title">${esc(section.name)}</h3>` +
      (section.groups || []).map((g) => `<section class="cmdty-group" aria-label="${esc(g.name)}"><h4 class="cmdty-group-title">${esc(g.name)}` +
        `<span>${nf0.format((g.markets || []).length)} markets · open interest ${compact(g.open_interest)}</span></h4>` +
        (g.reading ? `<p class="cmdty-reading">${esc(g.reading)}</p>` : "") +
        `<div class="macro-grid cmdty-grid">${(g.markets || []).map((m) => cmdtyCard(m, chosen && m.code === chosen.code)).join("")}</div></section>`).join("") +
      cmdtyOthers(section, names) + `</section>`).join("");
    $("cmdty-chart-title").textContent = chosen ? `${chosen.name} and positioning` : "Price and positioning";
    renderCmdtyNav();
    renderCmdtyChart();
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
      // A bar shorter than the one before it is drawn faint: the gap to the signal line is closing.
      const fading = i && isNum(histogram[i - 1]) && Math.abs(h) < Math.abs(histogram[i - 1]) ? " bar-fading" : "";
      return `<rect class="${h >= 0 ? "bar-up" : "bar-down"}${fading}" x="${(x(i) - width / 2).toFixed(1)}" ` +
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
      `<div class="detail-split"><div class="detail-main">` +
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
        // The standing caveats live once in "How to read this screen"; only a gap in coverage is per-row news.
        ...(r.iv_news_status === "unavailable" ? [["News coverage", "Lookup failed for this row"]]
          : r.iv_news_status === "empty" ? [["News coverage", "No usable headlines returned"]] : []),
      ]) +
      // The description fills the room under these two short groups, beside the taller sentiment panel.
      (r.summary ? `<div class="detail-group detail-summary"><h4>What the business does</h4><p class="detail-text">${esc(r.summary)}</p></div>` : "") +
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
      ]) +
      (isNum(r.earnings_in_days)
        ? group("Next earnings", [
            ["Date", r.next_earnings ? longDate.format(day(r.next_earnings)) : "—"],
            ["Time", r.earnings_estimated === false && r.earnings_time ? `${r.earnings_time} ET · ${r.earnings_session}` : "not published yet"],
            ["Source", r.earnings_estimated === true ? "Yahoo estimate" : r.earnings_estimated === false ? "company confirmed" : "confirmation unavailable"],
            ["Countdown", `${r.earnings_in_days} ${r.earnings_in_days === 1 ? "day" : "days"}`],
          ])
        : "") +
      // Everything above shares the left side; the sentiment panel keeps the right, so neither leaves a gap.
      `</div>` + sentimentDetails(r) + `</div>` +
      detailCharts(r) +
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
  /* Four pages share one document: the IV scan, then the three market-context tabs, each labelled from its own data. */
  const renderPage = () => {
    for (const key of PAGES) {
      const on = key === state.page;
      $(`tab-${key}`).setAttribute("aria-selected", String(on));
      $(`tab-${key}`).tabIndex = on ? 0 : -1;
      $(`page-${key}`).hidden = !on;
    }
    // A chart drawn while its tab was hidden had no width to measure; draw it again now it shows.
    if (state.page === "sentiment") renderMacroChart();
    if (state.page === "leverage") renderLeverChart();
    if (state.page === "commodities") renderCmdtyChart();
  };
  const render = () => {
    renderPage();
    U = (DATA.universe_by_cap && DATA.universe_by_cap[state.cap]) || DATA.universe;
    renderMacro();
    renderMacroSearch();
    renderLeverage();
    renderCommodities();
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
      ...(DATA.preliminary ? [["Status", `Preliminary · ${nf0.format(DATA.preliminary.remaining)} lower-IV stocks still downloading; the complete dashboard replaces this one when they finish`]] : []),
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
    if (target && target.closest(".macro-svg")) return;  // the macro chart runs its own crosshair
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
  const wireSeg = (id, attr, apply, selected = "aria-checked", redraw = render) => {
    const seg = $(id);
    seg.addEventListener("click", (ev) => {
      const button = ev.target.closest(`button[data-${attr}]`);
      if (button) { apply(button.dataset[attr]); redraw(); }
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
      redraw();
      next.focus();
    });
  };
  wireSeg("page-tabs", "page", (v) => { state.page = v; persist(); }, "aria-selected", renderPage);
  wireSeg("cap-tabs", "cap", (v) => { state.cap = v; state.open.clear(); persist(); }, "aria-selected");
  wireSeg("market-seg", "market", (v) => { state.market = v; persist(); });
  wireSeg("frame-seg", "frame", (v) => { state.frame = v; persist(); });
  wireSeg("macro-series-seg", "mseries", (v) => { state.mseries = v; persist(); }, "aria-checked", renderMacroChart);
  wireSeg("macro-range-seg", "mrange", (v) => { state.mrange = v; persist(); }, "aria-checked", renderMacroChart);
  $("macro-log").addEventListener("change", (ev) => { state.mlog = ev.target.checked; persist(); renderMacroChart(); });
  $("search-view-seg").addEventListener("click", event => {
    const button = event.target.closest("button[data-search-view]");
    if (!button) return;
    searchView = button.dataset.searchView;
    renderMacroSearch();
  });
  $("macro-plot").addEventListener("pointermove", macroChart.move);
  $("macro-plot").addEventListener("pointerleave", macroChart.leave);
  wireSeg("lever-series-seg", "lseries", (v) => { state.lseries = v; persist(); }, "aria-checked", renderLeverChart);
  wireSeg("lever-range-seg", "lrange", (v) => { state.lrange = v; persist(); }, "aria-checked", renderLeverChart);
  $("lever-log").addEventListener("change", (ev) => { state.llog = ev.target.checked; persist(); renderLeverChart(); });
  wireSeg("cmdty-range-seg", "crange", (v) => { state.crange = v; persist(); }, "aria-checked", renderCommodities);
  $("cmdty-log").addEventListener("change", (ev) => { state.clog = ev.target.checked; persist(); renderCmdtyChart(); });
  $("cmdty-plot").addEventListener("pointermove", cmdtyChart.move);
  $("cmdty-plot").addEventListener("pointerleave", cmdtyChart.leave);
  const chooseCommodity = (ev) => {
    const card = ev.target.closest?.("[data-commodity]");
    if (!card || (ev.type === "keydown" && ev.key !== "Enter" && ev.key !== " ")) return;
    if (ev.type === "keydown") ev.preventDefault();
    state.commodity = card.dataset.commodity; openGroup(state.commodity); persist(); renderCommodities();
    $("cmdty-chart").scrollIntoView?.({ behavior: "smooth", block: "start" });
  };
  $("cmdty-groups").addEventListener("click", chooseCommodity);
  $("cmdty-groups").addEventListener("keydown", chooseCommodity);
  // The navigator keeps its own open groups (re-drawing it would otherwise reset them); a market there charts in place.
  $("cmdty-nav").addEventListener("click", (ev) => {
    const summary = ev.target.closest?.("summary"), button = ev.target.closest?.("button[data-commodity]");
    if (summary) {
      ev.preventDefault?.();
      const key = summary.parentElement?.dataset?.group ?? summary.dataset?.group;
      const open = new Set(state.cgroups || []);
      if (open.has(key)) open.delete(key); else open.add(key);
      state.cgroups = [...open]; persist(); renderCmdtyNav();
    } else if (button) {
      state.commodity = button.dataset.commodity; openGroup(state.commodity); persist(); renderCommodities();
    }
  });
  $("lever-plot").addEventListener("pointermove", leverChart.move);
  $("lever-plot").addEventListener("pointerleave", leverChart.leave);
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
  if ("ResizeObserver" in window) new ResizeObserver(() => renderMacroChart()).observe($("macro-plot"));
  if ("ResizeObserver" in window) new ResizeObserver(() => renderLeverChart()).observe($("lever-plot"));
  if ("ResizeObserver" in window) new ResizeObserver(() => renderCmdtyChart()).observe($("cmdty-plot"));

  $("macro-cards").addEventListener("click", (ev) => {
    const edit = ev.target.closest("[data-aaii-edit]");
    if (!edit && !ev.target.closest("[data-aaii-cancel]")) return;
    aaiiEditing = Boolean(edit);
    renderMacro();
  });
  $("macro-cards").addEventListener("submit", async (ev) => {
    const form = ev.target.closest("form[data-aaii-form]");
    if (!form) return;
    ev.preventDefault();
    const fail = message => { form.querySelector(".aaii-error").textContent = message; };
    const share = name => Number.parseFloat(form.elements[name].value);
    const body = {week_ending: form.elements.week_ending.value, bullish: share("bullish"), neutral: share("neutral"), bearish: share("bearish")};
    const parts = [body.bullish, body.neutral, body.bearish];
    if (!parts.every(isNum)) return fail("Enter all three percentages.");
    const total = parts.reduce((sum, v) => sum + v, 0);
    if (Math.abs(total - 100) > 0.5) return fail(`These add up to ${nf1.format(total)}%, not 100%.`);
    try {
      const status = await (await fetch("/api/status")).json();
      const response = await fetch("/api/aaii", {method:"POST", headers:{"Content-Type":"application/json", "X-App-Token":status.token}, body:JSON.stringify(body)});
      const result = await response.json();
      if (!response.ok) return fail(result.error || "This week could not be saved.");
      DATA.macro_sentiment = DATA.macro_sentiment || {};
      const cards = DATA.macro_sentiment.cards || [];
      const old = cards.find(c => c.key === "aaii") || {};
      DATA.macro_sentiment.cards = [...cards.filter(c => c.key !== "aaii"),
        {key:"aaii", name:old.name || "AAII sentiment", url:old.url || "https://www.aaii.com/sentimentsurvey", max_age:old.max_age || 10, ...result}];
      aaiiEditing = false;
      renderMacro();
    } catch { fail("Open the dashboard from the StocksHighIV app to save AAII results."); }
  });

  window.addEventListener("highiv-watchlist", event => { watched = new Set(event.detail); render(); });
  render();
})();
