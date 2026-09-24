// Exercise the real dashboard script with a minimal DOM, without browser dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const root = path.resolve(__dirname, '..');
const script = fs.readFileSync(path.join(root, 'templates/dashboard.js'), 'utf8');

function renderEarnings(estimated, extra = {}, options = {}) {
  const row = {
    symbol: 'TEST', market_cap_usd: 2e9, name: 'Test Company', market: 'US', exchange: 'NASDAQ',
    iv30: 40, squeeze: 'unknown', iv_days: 1, iv_history: [],
    next_earnings: '2026-09-22', earnings_in_days: 4,
    // Even if a stale time exists, unknown and estimated dates must suppress it.
    earnings_time: '1:00 pm', earnings_session: 'during session',
    earnings_estimated: estimated,
    ...extra,
  };
  const payload = {
    quote_date: '2026-09-18', generated_at: '2026-09-18T17:00:00-04:00',
    settings: {
      top_n: 100, min_market_cap_usd: 1e9, small_float_shares: 5e7,
      squeeze_high: { short_pct_float: 0.2, days_to_cover: 5, short_pct_alone: 0.3 },
      squeeze_elevated: { short_pct_float: 0.1, days_to_cover: 3, short_pct_alone: 0.2 },
    },
    universe: { total: 1, us: 1, tsx_only: 0, with_iv: 1, median_iv: 40, interlisted: 0 },
    rows: options.rows || [row],
    watchlist: options.watchlist || [],
    universe_by_cap: options.stats,
    macro_sentiment: options.macro,
    market_leverage: options.leverage,
    macro_search: options.search,
    commodities: options.commodities,
    preliminary: options.preliminary,
  };
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      innerHTML: '', textContent: '', clientWidth: 0, clientHeight: 0,
      handlers: {}, attributes: {}, dataset: {},
      setAttribute(name, value) { this.attributes[name] = value; },
      getAttribute(name) { return this.attributes[name]; },
      addEventListener(event, handler) { this.handlers[event] = handler; },
      focus() {},
    });
    return elements.get(id);
  }
  const groups = {};
  for (const [id, attr, values] of [['cap-tabs', 'cap', ['mid', 'large']], ['market-seg', 'market', ['all', 'us', 'tsx']], ['frame-seg', 'frame', ['1H', '4H', '1D', '1W', '1M']]]) {
    groups[id] = values.map(value => {
      const button = element(`${id}-${value}`);
      button.dataset[attr] = value;
      return button;
    });
    element(id).querySelectorAll = () => groups[id];
  }
  const storage = {};
  element('payload').textContent = JSON.stringify(payload);
  vm.runInNewContext(script, {
    document: {
      getElementById: element,
      querySelector: element,
      querySelectorAll: selector => groups[selector.replace(/^#/, '').replace(/ button$/, '')] || [],
      addEventListener() {},
    },
    window: { addEventListener() {} },
    localStorage: { getItem: () => options.saved ? JSON.stringify(options.saved) : null, setItem: (k, v) => { storage[k] = JSON.parse(v); } },
    CSS: { escape: value => value },
    Intl,
    Date: class extends Date { static now() { return Date.parse('2026-09-18T15:00:00Z'); } },
    fetch: options.fetch,
  });
  const collapsed = element('rows').innerHTML;
  if (!options.rows) element('rows').handlers.click({ target: {
    closest: selector => selector === 'tr.row' ? { dataset: { symbol: 'TEST' } } : null,
  } });
  return { collapsed, expanded: element('rows').innerHTML, element, storage, groups,
    click: (id, attr, value) => element(id).handlers.click({ target: { closest: () => ({ dataset: { [attr]: value } }) } }),
    key: (id, key) => element(id).handlers.keydown({ key, preventDefault() {} }),
  };
}

test('unknown earnings confirmation hides the time and reports unknown status', () => {
  const { collapsed, expanded } = renderEarnings(null);
  assert.match(collapsed, /time not set/);
  assert.match(expanded, /confirmation unavailable/);
  assert.doesNotMatch(expanded, /company confirmed|1:00 pm/);
});

test('estimated earnings hide the time and keep the estimate label', () => {
  const { collapsed, expanded } = renderEarnings(true);
  assert.match(collapsed, /est\.<\/span>/);
  assert.match(expanded, /Yahoo estimate/);
  assert.doesNotMatch(expanded, /company confirmed|1:00 pm/);
});

test('explicitly confirmed earnings retain the time', () => {
  const { collapsed, expanded } = renderEarnings(false);
  assert.match(collapsed, /1:00 pm ET/);
  assert.match(expanded, /company confirmed/);
});

test('missing catalyst and unavailable news remain visible as uncertainty', () => {
  const { collapsed, expanded } = renderEarnings(null, {
    iv_why: 'No clear catalyst found', iv_why_kind: 'none',
    iv_news_status: 'unavailable', iv_why_as_of: '2026-09-18',
  });
  assert.match(collapsed, /No clear catalyst found/);
  assert.match(collapsed, /News unavailable/);
  assert.match(expanded, /IV session assessed/);
  assert.match(expanded, /News coverage<\/dt><dd[^>]*>Lookup failed for this row/);
  // The standing caveats sit once at the bottom of the page, not in every row.
  assert.doesNotMatch(expanded, /does not establish that no catalyst exists/);
  assert.doesNotMatch(expanded, /Timing<\/dt>/);
  const checked = renderEarnings(null, {iv_why_kind: 'none', iv_news_status: 'checked', iv_why_as_of: '2026-09-18'});
  assert.doesNotMatch(checked.expanded, /News coverage/);
});

test('the business description opens the details and sentiment sits beside Why this IV', () => {
  const { expanded } = renderEarnings(null, {summary: 'Test Company designs and sells widgets.', iv_why_as_of: '2026-09-18'});
  const at = (text) => expanded.indexOf(text);
  assert.match(expanded, /class="detail-group detail-summary"><h4>What the business does<\/h4><p class="detail-text">Test Company designs/);
  assert.ok(at('Why this IV') < at('What the business does'));  // under the two short groups
  assert.ok(at('What the business does') < at('Stock / sector sentiment'));  // the panel spans both of those rows
  assert.match(expanded, /class="detail-split"><div class="detail-main"><div class="detail-group "><h4>Company/);
  assert.ok(at('Stock / sector sentiment') < at('class="mini-row"') || at('class="mini-row"') === -1);
});

test('possible events retain source links and escaped full evidence', () => {
  const { collapsed, expanded } = renderEarnings(null, {
    iv_why: 'Possible catalyst: TEST announces public offering', iv_why_kind: 'news',
    iv_why_url: 'https://example.com/event', iv_why_source: 'Example',
    iv_why_detail: 'Headline <only>; causal link not verified.',
    iv_why_event: 'Financing', iv_why_as_of: '2026-09-18',
  });
  assert.match(collapsed, /Possible event/);
  assert.match(expanded, /href="https:\/\/example.com\/event"/);
  assert.match(collapsed, /data-tip="Headline &lt;only&gt;; causal link not verified."/);  // full note stays as the cell's tooltip
  assert.match(expanded, /Financing/);
});

test('structural context, observed moves and unknown event evidence stay separate', () => {
  const { collapsed, expanded } = renderEarnings(null, {
    iv_why: 'No clear catalyst found', iv_why_kind: 'none',
    iv_context: [{ key: 'gold', label: 'Gold cycle exposure', detail: 'Sensitivity <only>, no proven cause.', evidence: 'Industry: Gold.', source: 'Profile', url: 'https://example.com/profile' }],
    iv_price_context: { label: 'Stock rose 80%, then fell 30%', detail: 'Observed closes, not a catalyst.' },
    iv_peer_trends: [{ label: 'Gold cycle exposure', direction: 'down', median_pct: -25, up: 0, down: 3, count: 3, start: '2026-06-18', end: '2026-09-17', detail: 'Selected sample.', members: [{ symbol: 'PEER', return_pct: -25 }] }],
  });
  assert.match(collapsed, /Structural context/);
  assert.match(collapsed, /Gold cycle exposure/);
  assert.match(collapsed, /No clear catalyst found/);
  assert.match(collapsed, /3\/3 screened peers down/);
  assert.match(expanded, /Sensitivity &lt;only&gt;/);
  assert.match(expanded, /href="https:\/\/example.com\/profile"/);
  assert.match(expanded, /PEER ▼25\.0%/);
});

test('mixed peer moves are not highlighted as a theme rally or selloff', () => {
  const { collapsed, expanded } = renderEarnings(null, {
    iv_context: [{ label: 'Example', detail: 'Unknown', url: 'javascript:alert(1)', source: 'Invalid link' }],
    iv_peer_trends: [{ label: 'Example', direction: 'mixed', median_pct: 0, up: 1, down: 1, count: 3, members: [] }],
  });
  assert.doesNotMatch(collapsed, /screened peers/);
  assert.match(expanded, /1 up \/ 1 down among 3 screened peers/);
  assert.doesNotMatch(expanded, /href="javascript/);
});

const symbols = app => [...app.element('rows').innerHTML.matchAll(/class="row"[^>]*data-symbol="([^"]+)"/g)].map(m => m[1]);
const capRows = () => [
  ...Array.from({ length: 110 }, (_, i) => ({ symbol: `MID${i}`, name: `Medium ${i}`, market: 'US', exchange: 'NASDAQ', market_cap_usd: 2e9, iv30: 200 - i, squeeze: 'unknown', hq_north_america: true })),
  ...Array.from({ length: 110 }, (_, i) => ({ symbol: `BIG${i}`, name: `Large ${i}`, market: 'US', exchange: 'NASDAQ', market_cap_usd: 100e9 + i * 1e9, iv30: 80 - i / 10, squeeze: 'unknown', hq_north_america: i >= 3, also_listed: i < 2 ? `BIG${i}.TO` : null })),
];

test('tabs select the top 100 inside each cap range before applying column sorting', () => {
  const app = renderEarnings(null, {}, { rows: capRows() });
  assert.equal(symbols(app).length, 100);
  assert.equal(symbols(app)[0], 'MID0');
  assert.equal(symbols(app)[99], 'MID99');
  app.click('cap-tabs', 'cap', 'large');
  assert.equal(symbols(app).length, 100);
  assert.equal(symbols(app)[0], 'BIG0'); // Exactly US$100B is in the upper tab.
  assert.equal(symbols(app)[99], 'BIG99');
  app.element('thead').handlers.click({ target: { closest: () => ({ dataset: { sort: 'market_cap_usd' } }) } });
  assert.equal(symbols(app)[0], 'BIG99');
  assert.ok(!symbols(app).includes('BIG109'));
  assert.equal(app.storage['ivl-view'].cap, 'large');
});

test('cap tabs preserve venue and HQ filters and show real empty counts', () => {
  const app = renderEarnings(null, {}, { rows: capRows(), saved: { cap: 'large', market: 'all' } });
  app.click('market-seg', 'market', 'tsx');
  assert.deepEqual(symbols(app), ['BIG0', 'BIG1']);
  app.element('hq-only').handlers.change({ target: { checked: true } });
  assert.deepEqual(symbols(app), []);
  assert.equal(app.element('top-n').textContent, '0');
  assert.equal(app.element('empty').hidden, false);
});

test('cap tabs support arrow/Home/End keys and refresh overview statistics', () => {
  const stats = {
    mid: { total: 110, with_iv: 110, median_iv: 145, us: 110, tsx_only: 0, interlisted: 0 },
    large: { total: 110, with_iv: 110, median_iv: 74.5, us: 110, tsx_only: 0, interlisted: 2 },
  };
  const app = renderEarnings(null, {}, { rows: capRows(), stats });
  app.key('cap-tabs', 'ArrowRight');
  assert.equal(symbols(app)[0], 'BIG0');
  assert.equal(app.groups['cap-tabs'][1].getAttribute('aria-selected'), 'true');
  assert.equal(app.element('cap-panel').getAttribute('aria-labelledby'), 'cap-large');
  assert.match(app.element('figures').innerHTML, /74\.5/);
  assert.match(app.element('lede').textContent, /US\$100B\+/);
  app.key('cap-tabs', 'Home');
  assert.equal(symbols(app)[0], 'MID0');
  app.key('cap-tabs', 'End');
  assert.equal(symbols(app)[0], 'BIG0');
});

test('funding and short-position timestamps remain distinct from report generation', () => {
  const { collapsed } = renderEarnings(null, {
    short_date: '2026-08-31', short_pct_float: 20, shares_short: 1e6,
    days_to_cover: 4, float_shares: 5e6,
    borrow_fee: 12, borrow_available: 5000,
    borrow_fetched_at: '2026-09-17T18:30:00Z',
    details_fetched_at: '2026-09-16T17:15:00Z',
  });
  assert.match(collapsed, /SI as of 2026-08-31/);
  assert.match(collapsed, /Fetched Sep 17, 2026, 2:30 PM ET/);
  assert.match(collapsed, /Fetched Sep 16, 2026, 1:15 PM ET/);
  assert.match(collapsed, /1M shares short/);
  assert.doesNotMatch(collapsed, /Fetched Sep 18/);
});

test('a preliminary dashboard says the remaining stocks are still downloading', () => {
  assert.doesNotMatch(renderEarnings(null).element('session').innerHTML, /Preliminary/);
  const { element } = renderEarnings(null, {}, { preliminary: { remaining: 1520, checked: 740 } });
  assert.match(element('session').innerHTML, /Preliminary · 1,520 lower-IV stocks still downloading/);
});

test('legacy snapshots do not invent retrieval dates', () => {
  const { collapsed } = renderEarnings(null, {
    short_pct_float: 10, days_to_cover: 3, borrow_fee: 5, borrow_available: 0,
  });
  assert.match(collapsed, /Fetch time not recorded/);
  assert.match(collapsed, /SI date unavailable/);
  assert.match(collapsed, /0 available/);
  assert.doesNotMatch(collapsed, /Fetched Sep/);
});

test('watchlist keeps favorites outside cap and location filters',()=>{
  const result=renderEarnings(null, {symbol:'FAV',market_cap_usd:1e8,iv30:5,watch_only:true}, {saved:{cap:'watch',market:'tsx',hqOnly:true},watchlist:['FAV','MISSING']});
  assert.match(result.collapsed,/FAV/);
});
test('logos render as small embedded WebP with initials fallback',()=>{
  const withLogo=renderEarnings(null,{logo_webp:'UklGRg=='});
  assert.match(withLogo.collapsed,/data:image\/webp;base64,UklGRg==/);
  assert.match(withLogo.collapsed,/width="24" height="24"/);
  const missing=renderEarnings(null,{logo_webp:null});
  assert.match(missing.collapsed,/logo-initials/);
  assert.doesNotMatch(missing.collapsed,/data:image\/webp/);
});

const sentimentFixture = (score, date = '2026-09-17') => ({
  method: 'Test formula, not a prediction.', benchmark: 'XLK', components: [
    {key:'stock', score, as_of:date, weight:40, detail:'Observed <only>', source:'Yahoo', url:'https://example.com/history'},
    {key:'sector', score, as_of:date, weight:30, detail:'Relative to SPY'},
    {key:'news', score:null, weight:20, detail:'Not configured'},
    {key:'social', score:null, weight:10, detail:'Not configured'},
  ],
});

test('sentiment keeps missing data unknown and expands the evidence with safe links', () => {
  const app = renderEarnings(null, {sentiment:sentimentFixture(75)});
  assert.match(app.collapsed, /75\/100 · Positive/);
  assert.match(app.collapsed, /Price only · 70% coverage/);
  assert.match(app.expanded, /Observed &lt;only&gt;/);
  assert.match(app.expanded, /colspan="18"/);
  assert.match(app.expanded, /Base weight 40% · effective 57%/);
  const missing = renderEarnings(null);
  assert.match(missing.collapsed, /Insufficient evidence/);
  assert.doesNotMatch(missing.collapsed, /50\/100/);
});

test('a feed with no credentials leaves its card out instead of repeating the same notice', () => {
  const parts = (extra) => ({sentiment: {score: 60, label: 'Positive', coverage: 70, mode: 'Price only', method: 'M',
    components: [{key: 'stock', weight: 40, score: 62, detail: 'd'}, {key: 'sector', weight: 30, score: 58, detail: 'd'}, extra]}});
  const off = renderEarnings(null, parts({key: 'social', weight: 10, score: null, detail: 'not connected', configured: false}));
  assert.doesNotMatch(off.expanded, /Social sentiment/);
  assert.match(off.expanded, /Stock momentum/);
  const on = renderEarnings(null, parts({key: 'social', weight: 10, score: null, detail: 'Stocktwits unavailable'}));
  assert.match(on.expanded, /Social sentiment/);  // configured but empty today: still shown
});

test('sentiment sorts both directions with stale and unknown rows always last', () => {
  const rows = capRows().slice(0,4).map((r,i)=>({...r, sentiment:i===3 ? undefined : sentimentFixture([75,25,95][i],i===2?'2026-09-01':'2026-09-17')}));
  const app = renderEarnings(null, {}, {rows});
  const sort = () => app.element('thead').handlers.click({target:{closest:()=>({dataset:{sort:'sentiment'}})}});
  sort(); assert.deepEqual(symbols(app), ['MID0','MID1','MID2','MID3']);
  sort(); assert.deepEqual(symbols(app), ['MID1','MID0','MID2','MID3']);
});

test('fear and greed replica is labelled as a stand-in or as a cross-check, never as CNN', () => {
  const parts = [{key:'momentum', name:'Market momentum', score:25.4, rating:'Fear', reading:'S&P 500 +3.4% vs 125-day average'},
    {key:'put_call', name:'Put and call options', score:null, detail:'Building Cboe history: 120/629 sessions stored'}];
  const replica = {value:31.2, reading:'31.2/100', signal:'Fear', status:'ok', as_of:'2026-09-17', coverage:6, components:parts};
  const standIn = renderEarnings(null, {}, {macro:{cards:[{key:'cnn', name:'Fear & Greed replica', ...replica, max_age:4, direction:-1,
    replica_of:'CNN Fear & Greed', cnn_status:'unavailable', method:'Replica method', detail:'Replica detail'}]}});
  const card = standIn.element('macro-cards').innerHTML;
  assert.match(card, /Fear &amp; Greed replica/);
  assert.match(card, /Not CNN's reading · CNN feed unavailable/);
  assert.match(card, /Market momentum · 25.4 Fear/);
  assert.match(card, /Put and call options · Unavailable<span>Building Cboe history/);
  const crossCheck = renderEarnings(null, {}, {macro:{cards:[{key:'cnn', name:'CNN Fear & Greed', value:28.6, reading:'28.6/100',
    signal:'Fear', status:'ok', as_of:'2026-09-18', max_age:2, direction:-1, replica}]}});
  assert.match(crossCheck.element('macro-cards').innerHTML, /Replica 31.2 · \+2.6 vs CNN/);
  assert.match(crossCheck.element('macro-cards').innerHTML, /Replica cross-check · 6\/7 components/);
  assert.doesNotMatch(crossCheck.element('macro-cards').innerHTML, /Not CNN's reading/);
});

test('aaii card shows its source, flags a newer published week, and saves a week through the local app', async () => {
  const card = extra => ({key:'aaii', name:'AAII sentiment', url:'https://www.aaii.com/sentimentsurvey', max_age:10, status:'ok', signal:'Bearish tilt', direction:-1, ...extra});
  const html = cards => renderEarnings(null, {}, {macro:{cards}}).element('macro-cards').innerHTML;
  const imported = html([card({value:-24.5, reading:'-24.5 pp', as_of:'2026-09-17', date_label:'reported', source_file:'sentiment (1).xls'})]);
  assert.match(imported, /<p class="sentiment-meta data-date">As of Sep 17, 2026 · reported<\/p>/);  // underlined: never read as today's
  assert.doesNotMatch(imported, /aaii-shares/);  // no shares in this reading, so none are shown
  assert.match(imported, /From AAII's spreadsheet · sentiment \(1\).xls/);
  assert.doesNotMatch(imported, /New week out/);  // on Friday Sep 18, the Sep 17 report is the latest published week
  const lastWeek = html([card({value:-10, reading:'-10.0 pp', as_of:'2026-09-09', entered:true})]);
  assert.match(lastWeek, /Entered from AAII's results page/);
  assert.match(lastWeek, /data-aaii-edit>New week out Sep 17 · update</);
  const missing = html([{key:'aaii', status:'unavailable'}]);
  assert.match(missing, /data-aaii-edit>Add this week's results</);
  assert.match(missing, /copy the new week's three percentages from <a href="https:\/\/www.aaii.com\/sentimentsurvey"/);

  const posts = [];
  const app = renderEarnings(null, {}, {macro:{cards:[{key:'aaii', status:'unavailable'}]}, fetch: async (url, init) => {
    if (url === '/api/status') return {json: async () => ({token:'t'})};
    posts.push({url, init});
    return {ok:true, json: async () => ({status:'ok', value:-24.5, reading:'-24.5 pp', signal:'Bearish tilt', direction:-1,
      as_of:'2026-09-16', date_label:'week ending', entered:true})};
  }});
  const cards = app.element('macro-cards');
  cards.handlers.click({target:{closest: selector => selector === '[data-aaii-edit]' ? {} : null}});
  assert.match(cards.innerHTML, /name="week_ending" value="2026-09-16"/);
  const error = {textContent:''};
  const form = {elements:{week_ending:{value:'2026-09-16'}, bullish:{value:'28.8'}, neutral:{value:'17.9'}, bearish:{value:'43.3'}},
    querySelector: () => error};
  const submit = () => cards.handlers.submit({target:{closest: () => form}, preventDefault() {}});
  await submit();
  assert.equal(error.textContent, 'These add up to 90.0%, not 100%.');
  assert.equal(posts.length, 0);  // checked in the page before anything is sent
  form.elements.bearish.value = '53.3';
  await submit();
  assert.equal(posts[0].url, '/api/aaii');
  assert.equal(posts[0].init.headers['X-App-Token'], 't');
  assert.deepEqual(JSON.parse(posts[0].init.body), {week_ending:'2026-09-16', bullish:28.8, neutral:17.9, bearish:53.3});
  assert.match(cards.innerHTML, /-24.5 pp/);
  assert.doesNotMatch(cards.innerHTML, /data-aaii-form|New week out|Add this week/);
});

test('macro chart draws the S&P 500 over the chosen series with range statistics and correlation', () => {
  const days = [];
  for (let t = Date.parse('2024-09-02T00:00:00Z'); t <= Date.parse('2026-09-17T00:00:00Z'); t += 864e5) {
    const d = new Date(t);
    if (d.getUTCDay() % 6) days.push(d.toISOString().slice(0, 10));
  }
  const spx = days.map((d, i) => [d, 5000 + i * 3 + (i % 7) * 20]);
  const tracking = spx.map(([d, v]) => [d, v / 100]).filter(([d]) => d < '2025-03-01' || d > '2025-05-31');  // moves with the index; one gap
  const history = {spx, series: {
    aaii: {name: 'AAII bull–bear spread', frequency: 'weekly', source: 'AAII weekly survey', points: spx.filter((_, i) => i % 5 === 2).map(([d, v]) => [d, (v - 6000) / 50])},
    vix: {name: 'VIX', frequency: 'daily', source: 'Cboe', points: tracking},
    put_call: {name: 'Equity put/call, 5-day average', frequency: 'daily', source: 'Cboe', points: []},
    fear_greed: {name: 'CNN Fear & Greed', frequency: 'daily', source: 'CNN', points: []},
    cot: {name: "COT: asset managers' net, % of non-spreading open interest", frequency: 'weekly', source: 'CFTC',
          points: spx.filter((_, i) => i % 5 === 1).map(([d, v]) => [d, (6000 - v) / 100]),
          signal: spx.filter((_, i) => i % 5 === 1).map(([d, v]) => [d, (v - 6000) / 120])},  // dealers take the other side
    rsi: {name: 'RSI 14', frequency: 'daily', source: 'Computed from S&P 500 daily closes', points: spx.map(([d, v]) => [d, 50 + (v % 40) / 4])},
    macd: {name: 'MACD 12/26/9, % of index', frequency: 'daily', source: 'Computed from S&P 500 daily closes',
           points: spx.map(([d, v]) => [d, (v % 20) / 10 - 1]),
           signal: spx.map(([d, v], i) => [d, (v % 20) / 10 - 1 + ((i % 7) - 3) * 0.05])},  // histogram crosses zero
  }};
  const app = renderEarnings(null, {}, {macro: {cards: [], history}, saved: {mseries: 'vix', mrange: '1Y'}});
  const plot = () => app.element('macro-plot').innerHTML, table = () => app.element('macro-windows').innerHTML;
  assert.match(app.element('macro-legend').innerHTML, /S&amp;P 500 [\d,.]+ · \+[\d.]+% over 1 year/);
  assert.match(app.element('macro-legend').innerHTML, /VIX [\d.]+ · Sep 17/);
  assert.match(plot(), /class="line-spx" d="M/);
  assert.match(plot(), /class="line-ind" d="M/);
  assert.match(plot(), /class="ref"/);  // VIX reference line at 20
  assert.match(plot(), /VIX<tspan class="ref-label"> · line at 20<\/tspan>/);
  assert.equal((table().match(/<tr/g) || []).length, 9);  // header plus eight ranges
  assert.match(table(), /<tr class="current"><th scope="row">1 year<\/th>/);
  assert.match(table(), /\+(0\.9\d|1\.00) · moves with <span class="sub">n=/);
  assert.match(app.element('macro-chart-note').textContent, /VIX · Cboe · since Sep 2, 2024/);
  app.click('macro-range-seg', 'mrange', 'MAX');
  assert.match(table(), /<tr class="current"><th scope="row">Since 1987<\/th>/);
  assert.match(app.element('macro-legend').innerHTML, /% since 1987/);
  assert.equal((plot().match(/class="line-ind" d="[^"]*/)[0].match(/M/g) || []).length, 2);  // the line breaks at the gap
  app.click('macro-series-seg', 'mseries', 'macd');
  app.click('macro-range-seg', 'mrange', '6M');
  assert.match(plot(), /class="line-ind2" d="M/);  // the signal line shares the pane
  assert.match(plot(), /class="hist hist-(up|down)( hist-fading)?" x=/);  // histogram bars between the two lines
  assert.match(plot(), /class="hist hist-up hist-fading"/);  // a bar shorter than the one before it is faint
  assert.match(plot(), /class="hist hist-down"/);
  assert.match(plot(), /MACD<tspan class="ref-label"> · line at 0: MACD crosses its signal<\/tspan>/);
  assert.match(app.element('macro-legend').innerHTML, /key-ind2[^>]*><\/i>signal [+−][\d.]+%/);
  app.click('macro-range-seg', 'mrange', 'MAX');  // long ranges group the bars instead of dropping them
  assert.match(plot(), /class="hist hist-(up|down)/);
  assert.match(plot(), /MACD<tspan class="ref-label"> · line at 0: MACD crosses its signal · (weekly|monthly|quarterly|yearly) bars<\/tspan>/);
  app.click('macro-range-seg', 'mrange', '1M');
  assert.doesNotMatch(plot(), /bars<\/tspan>/);  // every session has its own bar at short ranges
  app.click('macro-series-seg', 'mseries', 'rsi');
  assert.match(plot(), /RSI 14<tspan class="ref-label"> · line at 50: gains balance losses<\/tspan>/);
  assert.match(app.element('macro-windows-note').textContent, /calculated from the S&P 500's own closes, so this correlation reflects that arithmetic/);
  assert.doesNotMatch(plot(), /class="line-ind2"/);  // only MACD has a second line
  app.click('macro-range-seg', 'mrange', '20Y');
  assert.match(table(), /<tr class="current"><th scope="row">20 years<\/th>/);
  app.click('macro-range-seg', 'mrange', '1M');
  assert.match(table(), /<tr class="current"><th scope="row">1 month<\/th>/);
  assert.equal(app.storage['ivl-view'].mrange, '1M');
  app.click('macro-series-seg', 'mseries', 'aaii');
  assert.match(plot(), /class="dot-ind" cx=/);  // weekly points get markers in short ranges; a report without the shares keeps the spread
  app.click('macro-series-seg', 'mseries', 'put_call');
  assert.match(plot(), /No Equity put\/call, 5-day average data in this range/);
  app.click('macro-series-seg', 'mseries', 'cot');
  assert.match(plot(), /COT net<tspan class="ref-label"> · line at 0: net flat<\/tspan>/);
  assert.match(plot(), /class="line-ind2" d="M/);  // dealers drawn beside asset managers
  assert.match(app.element('macro-legend').innerHTML, /key-ind2[^>]*><\/i>dealers [+−][\d.]+%/);
  assert.doesNotMatch(plot(), /class="hist /);  // no histogram: that is MACD's
  assert.match(app.element('macro-legend').innerHTML, /COT: asset managers&#39; net, % of non-spreading open interest −[\d.]+% · Sep 15/);
  const empty = renderEarnings(null, {}, {macro: {cards: []}});
  assert.match(empty.element('macro-plot').innerHTML, /S&amp;P 500 history appears after the next data refresh/);
});

test('COT card shows leveraged funds and asset managers for the S&P 500 and VIX futures', () => {
  const cot = {key: 'cot', name: 'COT positioning', status: 'ok', as_of: '2026-09-15', max_age: 14, value: 48.4, reading: '+48.4% of OI',
    signal: 'Typical positioning', direction: 0, index: 72, detail: 'CFTC Traders in Financial Futures',
    groups: [{name: 'E-mini S&P 500', leveraged: -293143, leveraged_index: 72, asset_managers: 904684, asset_managers_index: 34,
              dealers: -702938, dealers_index: 12},
             {name: 'VIX futures', leveraged: -16504, leveraged_index: 70, asset_managers: -52658, asset_managers_index: 1}]};
  const app = renderEarnings(null, {}, {macro: {cards: [cot]}});
  const card = app.element('macro-cards').innerHTML;
  assert.match(card, /<h3>COT positioning<\/h3><div class="macro-value">\+48.4% of OI/);
  assert.match(card, /Asset managers \+904,684 · index 34 · leveraged funds −293,143 · index 72 · dealers −702,938 · index 12/);
  assert.match(card, /VIX futures · asset managers −52,658 · index 1 · leveraged funds −16,504 · index 70/);
  assert.match(app.element('macro-note').textContent, /^1\/5 fresh readings/);
});

test('the S&P 500 can use a log scale, and reports saved before the rename keep the new card names', () => {
  const cards = [{key:'vix', value:14.81, reading:'14.81', status:'ok', as_of:'2026-09-18', max_age:4, signal:'Calm', direction:1},
    {key:'aaii', value:-24.5, reading:'-24.5 pp', status:'ok', as_of:'2026-08-01', max_age:10, signal:'Bearish tilt', direction:-1},
    {key:'cnn', name:'CNN Fear & Greed', value:28.6, reading:'28.6/100', status:'ok', as_of:'2026-09-18', max_age:2, signal:'Fear', direction:-1}];
  const spx = []; for (let y = 1990; y <= 2026; y++) spx.push([`${y}-01-03`, 300 * 1.1 ** (y - 1990)]);
  const history = {spx, series: {aaii: {points: []}, vix: {points: []}, put_call: {points: []}, fear_greed: {points: []}}};
  const app = renderEarnings(null, {}, {macro: {cards, history}, saved: {mrange: 'MAX', mlog: true}});
  assert.match(app.element('macro-cards').innerHTML, /<h3>Fear &amp; Greed<\/h3>/);  // reports saved before the rename
  const ticks = [...app.element('macro-plot').innerHTML.matchAll(/class="tick" x="\d+" y="[\d.]+" text-anchor="end">([^<]+)</g)].map(m => m[1]);
  assert.deepEqual(ticks, ['500', '1,000', '2,000', '5,000', '10,000']);  // equal ratios, equal height
  assert.match(app.element('macro-plot').innerHTML, /S&amp;P 500<tspan class="ref-label"> · log scale<\/tspan>/);
  app.element('macro-log').handlers.change({target: {checked: false}});
  assert.doesNotMatch(app.element('macro-plot').innerHTML, /log scale/);
  assert.equal(app.storage['ivl-view'].mlog, false);
});

test('macro excludes stale data and retains source observation dates', () => {
  const app = renderEarnings(null, {}, {macro:{cards:[
    {key:'vix', value:15, reading:'15.00', status:'ok', as_of:'2026-09-17', max_age:4, signal:'Calm', direction:1},
    {key:'aaii', value:-25, reading:'-25 pp', status:'ok', as_of:'2026-08-01', max_age:10, signal:'Bearish', direction:-1},
  ]}});
  assert.equal(app.element('tab-label-sentiment').textContent, 'Insufficient data');  // one fresh input of five
  assert.match(app.element('macro-note').textContent, /1\/5 fresh readings/);
  assert.match(app.element('macro-cards').innerHTML, /Stale · excluded/);
  assert.match(app.element('macro-cards').innerHTML, /As of Aug 1, 2026/);
  assert.match(app.element('macro-cards').innerHTML, /Unavailable/);
});

test('market leverage cards give each release date and the next one, and chart against the S&P 500', () => {
  const days = [];
  for (let t = Date.parse('2014-01-01T00:00:00Z'); t <= Date.parse('2026-09-17T00:00:00Z'); t += 864e5) {
    const d = new Date(t);
    if (d.getUTCDay() % 6) days.push(d.toISOString().slice(0, 10));
  }
  const spx = days.map((d, i) => [d, 2000 + i]);
  const quarters = [];
  for (let y = 2014; y <= 2026; y++) for (const q of ['03-31', '06-30', '09-30', '12-31']) if (`${y}-${q}` <= '2026-06-30') quarters.push(`${y}-${q}`);
  const leverage = {
    checked_at: '2026-09-18T14:00:00Z',
    cards: [
      {key: 'finra', status: 'ok', as_of: '2026-08-31', period: 'August 2026', reading: '$1.45T', signal: 'Rapid build-up', tone: 'high',
       frequency: 'monthly', released: '2026-09-14', next: '2026-10-14', next_basis: 'estimated',
       lines: ['12-month change +37.2% · 89th percentile since 1998'], detail: 'FINRA member firms'},
      {key: 'z1', status: 'ok', as_of: '2026-06-30', period: 'Q2 2026', reading: '0.68%', signal: 'Low share of stock value borrowed', tone: 'low',
       frequency: 'quarterly', released: '2026-09-11', next: '2026-12-10', next_basis: 'scheduled', lines: []},
      {key: 'ofr', status: 'cached', as_of: '2026-03-31', period: 'Q1 2026', reading: '2.57×', signal: 'Near the top of its range', tone: 'high',
       frequency: 'quarterly', released: '2026-06-04', next: '2026-09-03', next_basis: 'estimated', lines: []},
      {key: 'cot', status: 'ok', as_of: '2026-09-15', reading: '−15.7% of OI', signal: 'Typical for 3 years', tone: 'normal',
       frequency: 'weekly', released: '2026-09-18', next: '2026-09-25', next_basis: 'scheduled', next_time: '3:30 PM ET', lines: []},
      {key: 'etf', status: 'ok', as_of: '2026-09-17', reading: '70% bull', signal: 'Typical bull–bear mix', tone: 'normal', frequency: 'daily', lines: []},
      {key: 'fsr', status: 'ok', as_of: '2026-05-08', period: 'May 2026', reading: 'May 2026', signal: 'Semiannual review', tone: null,
       frequency: 'semiannual', released: '2026-05-08', next: '2026-11-08', next_basis: 'estimated', next_precision: 'month',
       pdf: 'https://www.federalreserve.gov/publications/files/financial-stability-report-20260508.pdf', lines: []},
    ],
    series: {
      ofr: {name: 'Hedge funds: gross assets ÷ net assets', frequency: 'quarterly', source: 'OFR', points: quarters.map((d, i) => [d, 2 + i / 100])},
      finra: {name: 'Margin debt, 12-month change', frequency: 'monthly', source: 'FINRA', points: []},
    },
  };
  const app = renderEarnings(null, {}, {macro: {cards: [], history: {spx, series: {}}}, leverage, saved: {lseries: 'ofr', lrange: '5Y'}});
  const cards = app.element('lever-cards').innerHTML;
  assert.match(cards, /<h3>Margin debt · FINRA<\/h3><div class="macro-value">\$1.45T<\/div><span class="sentiment-badge elevated">Rapid build-up/);
  assert.match(cards, /<b>Data<\/b>August 2026 · monthly<\/span><span><b>Released<\/b>Sep 14, 2026<\/span><span><b>Next<\/b>~Wed, Oct 14, 2026 · estimated/);
  assert.match(cards, /<b>Next<\/b>Thu, Dec 10, 2026 · publisher&#39;s schedule|<b>Next<\/b>Thu, Dec 10, 2026 · publisher's schedule/);
  assert.match(cards, /<span class="due">Due since ~Thu, Sep 3, 2026 · not in this data yet<\/span>/);  // an estimate that has passed
  assert.match(cards, /Fri, Sep 25, 2026 · 3:30 PM ET · publisher's schedule/);
  assert.match(cards, /<b>Next<\/b>~Nov 2026 · estimated/);
  assert.match(cards, /Open the May 2026 report \(PDF\)/);
  assert.match(cards, /Last good reading retained; the latest refresh failed/);
  assert.match(cards, /<b>Data<\/b>Sep 17, 2026 · daily, after each close<\/span><\/div>/);  // daily: no release calendar
  assert.match(cards, /<li>12-month change \+37.2% · 89th percentile since 1998<\/li>/);
  assert.equal(app.element('tab-label-leverage').textContent, 'Insufficient data');  // one charted series: too little to weigh
  assert.match(app.element('lever-note').textContent, /^6\/6 sources available/);
  const plot = app.element('lever-plot').innerHTML;
  assert.equal((plot.match(/class="line-ind" d="[^"]*/)[0].match(/M/g) || []).length, 1);  // quarterly steps are not gaps
  assert.match(plot, /class="dot-ind" cx=/);  // each quarter marked
  assert.match(app.element('lever-legend').innerHTML, /Hedge funds: gross assets ÷ net assets 2\.\d\d× · Jun 30/);
  assert.match(app.element('lever-windows-note').textContent, /each quarterly change/);
  assert.match(app.element('lever-windows').innerHTML, /<tr class="current"><th scope="row">5 years<\/th>/);
  app.click('lever-series-seg', 'lseries', 'finra');
  assert.match(app.element('lever-plot').innerHTML, /No Margin debt, 12-month change data in this range/);
  assert.equal(app.storage['ivl-view'].lseries, 'finra');
  const empty = renderEarnings(null, {}, {macro: {cards: []}});
  assert.equal(empty.element('tab-label-leverage').textContent, 'Insufficient data');
  assert.match(empty.element('lever-cards').innerHTML, /<h3>Hedge fund leverage · OFR<\/h3><div class="macro-value">—<\/div><span class="sentiment-badge unknown">Unavailable/);
});

test('AAII shows its three shares on the card and as three lines on the chart', () => {
  const days = [];
  for (let t = Date.parse('2025-09-01T00:00:00Z'); t <= Date.parse('2026-09-17T00:00:00Z'); t += 864e5) {
    const d = new Date(t);
    if (d.getUTCDay() % 6) days.push(d.toISOString().slice(0, 10));
  }
  const spx = days.map((d, i) => [d, 6000 + i]);
  const weeks = days.filter(d => new Date(`${d}T00:00:00Z`).getUTCDay() === 3);
  const bull = weeks.map((d, i) => [d, 30 + (i % 10)]), bear = weeks.map((d, i) => [d, 45 - (i % 10)]);
  const neutral = weeks.map((d, i) => [d, 100 - bull[i][1] - bear[i][1]]);
  const history = {spx, series: {aaii: {name: 'AAII bullish / neutral / bearish', frequency: 'weekly', source: 'AAII weekly survey',
    points: weeks.map((d, i) => [d, bull[i][1] - bear[i][1]]), lines: {bullish: bull, neutral, bearish: bear}}}};
  const card = {key: 'aaii', name: 'AAII sentiment', status: 'ok', max_age: 10, as_of: '2026-09-16', value: -24.5, reading: '−24.5 pp',
                signal: 'Bearish tilt', direction: -1, bullish: 28.8, neutral: 17.9, bearish: 53.3, entered: true};
  const app = renderEarnings(null, {}, {macro: {cards: [card], history}, saved: {mseries: 'aaii', mrange: '3M'}});
  assert.match(app.element('macro-cards').innerHTML,
    /<p class="aaii-shares"><span class="bull">Bullish 28.8%<\/span><span class="neutral">Neutral 17.9%<\/span><span class="bear">Bearish 53.3%<\/span><\/p>/);
  const plot = app.element('macro-plot').innerHTML;
  for (const cls of ['bull', 'neutral', 'bear']) {
    assert.match(plot, new RegExp(`class="line-${cls}" d="M`));
    assert.match(plot, new RegExp(`class="dot-${cls}" cx=`));  // weekly points marked in short ranges
    assert.match(plot, new RegExp(`class="dot-${cls}" data-line=`));  // and followed by the crosshair
  }
  assert.doesNotMatch(plot, /class="line-ind"/);  // the spread is not drawn over the three shares
  assert.match(plot, /AAII survey, % of respondents/);
  const legend = app.element('macro-legend').innerHTML;
  assert.match(legend, /key-bull[^>]*><\/i>Bullish [\d.]+% · Sep 16/);
  assert.match(legend, /key-neutral[^>]*><\/i>Neutral [\d.]+%/);
  assert.match(legend, /key-bear[^>]*><\/i>Bearish [\d.]+%/);
  const table = app.element('macro-windows').innerHTML;
  assert.match(table, /<th scope="col">Bullish average<\/th><th scope="col">Neutral average<\/th><th scope="col">Bearish average<\/th><th scope="col">Correlation of changes · AAII spread<\/th>/);
  assert.match(table, /<tr class="current"><th scope="row">3 months<\/th><td class="up">\+[\d.]+%<\/td><td>[\d.]+%<\/td><td>[\d.]+%<\/td><td>[\d.]+%<\/td>/);
  assert.match(app.element('macro-windows-note').textContent, /^The chart draws the three shares; the correlation uses the bull–bear spread/);
});

test('leverage cards underline the period their data covers', () => {
  const card = {key: 'finra', status: 'ok', as_of: '2026-08-31', period: 'August 2026', reading: '$1.45T', signal: 'Rapid build-up', tone: 'high',
                frequency: 'monthly', released: '2026-09-14', next: '2026-10-14', next_basis: 'estimated', lines: []};
  const app = renderEarnings(null, {}, {macro: {cards: []}, leverage: {cards: [card], series: {}}});
  assert.match(app.element('lever-cards').innerHTML, /<span class="data-date"><b>Data<\/b>August 2026 · monthly<\/span>/);
});

test('four page tabs: the IV scan first, each context tab remembered and labelled', () => {
  const app = renderEarnings(null, {}, {macro: {cards: []}});
  assert.equal(app.element('page-scan').hidden, false);
  for (const page of ['sentiment', 'leverage', 'searches']) assert.equal(app.element(`page-${page}`).hidden, true);
  assert.equal(app.element('tab-scan').attributes['aria-selected'], 'true');
  app.click('page-tabs', 'page', 'leverage');
  assert.equal(app.element('page-leverage').hidden, false);
  assert.equal(app.element('page-scan').hidden, true);
  assert.equal(app.element('tab-leverage').attributes['aria-selected'], 'true');
  assert.equal(app.storage['ivl-view'].page, 'leverage');
  const again = renderEarnings(null, {}, {macro: {cards: []}, saved: {page: 'searches'}});
  assert.equal(again.element('page-searches').hidden, false);
  assert.equal(again.element('tab-label-searches').textContent, 'Insufficient data');  // nothing collected: no guessed label
  assert.match(again.element('tab-label-searches').className, /unknown/);
  assert.equal(renderEarnings(null, {}, {saved: {page: 'nowhere'}}).element('page-scan').hidden, false);
});

test('sentiment label weighs its five inputs and flags a historic extreme', () => {
  const card = (key, extra) => ({key, status: 'ok', as_of: '2026-09-17', max_age: 10, ...extra});
  const cards = [card('vix', {value: 15, reading: '15.00'}), card('aaii', {value: -24.5, reading: '−24.5 pp'}),
    card('cot', {value: 48.4, index: 72, reading: '+48.4% of OI'}), card('put_call', {value: 0.52, reading: '0.52 equity'}),
    card('cnn', {value: 28.6, reading: '28.6/100'})];
  const weeks = Array.from({length: 40}, (_, i) => [`2025-${String(1 + (i % 12)).padStart(2, '0')}-${String(1 + i % 27).padStart(2, '0')}`, -20 + i]);
  const history = {spx: [], series: {aaii: {points: weeks}, vix: {points: weeks.map(([d], i) => [d, 10 + i])}}};
  const app = renderEarnings(null, {}, {macro: {cards, history}});
  // VIX +0.50, AAII −0.98, COT +0.44, put/call +0.72, Fear & Greed −0.54: they cancel, with strong inputs on both sides.
  assert.equal(app.element('tab-label-sentiment').textContent, 'Mixed · historic low');
  const box = app.element('verdict-sentiment').innerHTML;
  assert.match(box, /<span class="verdict-label mixed">Mixed<\/span>/);
  assert.match(box, /verdict-score">−0.00 <small>|verdict-score">\+0.00 <small>/);
  assert.match(box, /100% of the weight has fresh data\./);
  assert.match(box, /<strong>Historic low:<\/strong> AAII bull–bear spread is at the 0th percentile of its own history/);
  assert.match(box, /<th scope="row">VIX<\/th><td>15.00<\/td><td class="up">\+0.50<\/td><td>25%<\/td><td>25%<\/td>/);
  assert.match(box, /<th scope="row">AAII bull–bear spread<\/th><td>−24.5 pp<\/td><td class="down">−0.98<\/td>/);
  const partial = renderEarnings(null, {}, {macro: {cards: [cards[0], cards[2], {...cards[3], as_of: '2026-08-01'}, cards[4]]}});
  // put/call is stale and AAII missing: 60% of the weight, three inputs, rescaled.
  assert.match(partial.element('verdict-sentiment').innerHTML, /60% of the weight has fresh data; missing inputs are left out and the rest rescaled/);
  assert.match(partial.element('verdict-sentiment').innerHTML, /<th scope="row">VIX<\/th><td>15.00<\/td><td class="up">\+0.50<\/td><td>25%<\/td><td>42%<\/td>/);
  assert.match(partial.element('verdict-sentiment').innerHTML, /<tr class="excluded"><th scope="row">Equity put\/call<\/th><td>No fresh reading<\/td><td class="">—<\/td><td>15%<\/td><td>excluded<\/td>/);
  assert.equal(partial.element('tab-label-sentiment').textContent, 'Leaning bullish');  // (12.5 + 8.8 − 8.0) ÷ 60 = +0.22
});

test('leverage label: more leverage is bullish, led by the daily 3× fund data', () => {
  const months = Array.from({length: 120}, (_, i) => new Date(Date.UTC(2016, 9 + i, 0)).toISOString().slice(0, 10));
  const quarters = months.filter((_, i) => i % 3 === 2);
  const days = [];
  for (let t = Date.parse('2023-01-02T00:00:00Z'); t <= Date.parse('2026-09-17T00:00:00Z'); t += 864e5) if (new Date(t).getUTCDay() % 6) days.push(new Date(t).toISOString().slice(0, 10));
  const series = {
    etf_flows: {unit: '%', points: days.map((d, i) => [d, -20 + i / 20])},  // the latest inflow is the largest on record
    etf_share: {unit: '%', points: days.map((d) => [d, 90])},                 // exactly typical for 3 years
    finra: {unit: '%', points: months.map((d, i) => [d, i])},
    ofr_gne: {unit: '×', points: quarters.map((d, i) => [d, 6 + i / 10])},
    z1: {unit: '%', points: quarters.map((d, i) => [d, 40 - i])},
    cot_lev: {unit: '%', points: months.map((d, i) => [d, i % 2 ? 5 : -5])},
  };
  const card = (key, frequency, extra = {}) => ({key, status: 'ok', as_of: months.at(-1), frequency, ...extra});
  const cards = [card('finra', 'monthly'), card('z1', 'quarterly'), card('ofr', 'quarterly'), card('cot', 'weekly'), card('etf', 'daily'), card('fsr', 'semiannual')];
  const app = renderEarnings(null, {}, {macro: {cards: []}, leverage: {cards, series}});
  // 35 × 1.00 + 25 × 0 + 30 × 0.99 + 4 × 0.98 + 3 × −0.98 + 3 × 0.50 = +0.67
  assert.equal(app.element('tab-label-leverage').textContent, 'Bullish · historic high');
  const box = app.element('verdict-leverage').innerHTML;
  assert.match(box, /verdict-score">\+0.67 <small>on −1 … \+1 · less leverage \(bearish\) … more leverage \(bullish\)/);
  assert.match(box, /<strong>Historic high:<\/strong> Net money into 3× bull minus bear funds, 20 sessions \(ProShares\) is at the 100th percentile/);
  assert.match(box, /<th scope="row">Net money into 3× bull minus bear funds, 20 sessions \(ProShares\)<\/th><td>\+28.4% · 100th pct<\/td><td class="up">\+1.00<\/td><td>35%<\/td><td>35%<\/td>/);
  assert.match(box, /<th scope="row">Bull funds&#39; share of 3× fund assets \(ProShares\)<\/th><td>90.0% · 50th pct \(3 yr\)<\/td><td class="">0.00<\/td><td>25%<\/td>/);
  assert.match(box, /<th scope="row">Margin debt, 12-month change \(FINRA\)<\/th><td>\+119.0% · 100th pct<\/td><td class="up">\+0.99<\/td><td>30%<\/td>/);
  assert.match(box, /<th scope="row">Margin loans ÷ stock market value \(Fed Z.1\)<\/th><td>1.0% · 1st pct<\/td><td class="down">−0.98<\/td><td>3%<\/td>/);
  assert.match(box, /<th scope="row">Leveraged funds&#39; net S&amp;P futures \(CFTC\)<\/th><td>\+5.0% · 75th pct \(3 yr\)<\/td><td class="up">\+0.50<\/td><td>3%<\/td>/);
  // Without the daily fund data only 40% of the weight is left: no label from slow sources alone.
  const noDaily = renderEarnings(null, {}, {macro: {cards: []}, leverage: {cards: cards.map((c) => c.key === 'etf' ? {...c, status: 'unavailable'} : c), series}});
  assert.equal(noDaily.element('tab-label-leverage').textContent, 'Insufficient data');
  assert.match(noDaily.element('verdict-leverage').innerHTML, /<th scope="row">Net money into 3× bull minus bear funds, 20 sessions \(ProShares\)<\/th><td>Unavailable<\/td>/);
  // A report saved before 3.2.0 has the ETF card but not the fund-flow series: it says to refresh.
  const { etf_flows, etf_share, ...older } = series;
  const saved = renderEarnings(null, {}, {macro: {cards: []}, leverage: {cards, series: older}});
  assert.match(saved.element('verdict-leverage').innerHTML, /<td>No reading · refresh data<\/td>/);
  const stale = renderEarnings(null, {}, {macro: {cards: []}, leverage: {cards, series: {...series,
    finra: {unit: '%', points: months.map((d, i) => [months[i - 6] || d, i]).slice(0, -6)}}}});
  assert.match(stale.element('verdict-leverage').innerHTML, /<th scope="row">Margin debt, 12-month change \(FINRA\)<\/th><td>Stale<\/td>/);  // over 75 days old
});

test('search label: rising worry is bearish, quiet searches never reach Bullish', () => {
  const terms = ['recession', 'layoffs', 'inflation', 'bank failure', 'stock market crash', 'war'];
  const recent = (ratio) => terms.map(term => ({term, status: 'ok', as_of: '2026-09-17', fetched_at: '2026-09-18T12:00:00Z', ratio}));
  const monthly = (percentile) => terms.map(term => ({term, status: 'ok', as_of: '2026-08-31', fetched_at: '2026-09-18T12:00:00Z', percentile}));
  const label = (search) => renderEarnings(null, {}, {macro: {cards: []}, search}).element('tab-label-searches').textContent;
  assert.equal(label({cards: recent(0.3), historical_cards: monthly(1)}), 'Leaning bullish · historic low');  // capped at +0.4
  assert.equal(label({cards: recent(2), historical_cards: monthly(99)}), 'Bearish · historic high');
  assert.equal(label({cards: recent(1), historical_cards: monthly(50)}), 'Neutral');
  assert.equal(label({cards: recent(1.2)}), 'Leaning bearish');  // −0.40: the weekly view alone stands in for both
  const box = renderEarnings(null, {}, {macro: {cards: []}, search: {cards: recent(1.25), historical_cards: monthly(90)}}).element('verdict-searches').innerHTML;
  // 0.6 × −0.50 + 0.4 × −0.80 = −0.62 for every term
  assert.match(box, /<th scope="row">“recession”<\/th><td>1.25× baseline · 90th pct since 2004<\/td><td class="down">−0.62<\/td><td>17%<\/td><td>17%<\/td>/);
});

const cmdtyWeeks = [];
for (let t = Date.parse('2016-09-06T00:00:00Z'); t <= Date.parse('2026-09-15T00:00:00Z'); t += 7 * 864e5) cmdtyWeeks.push(new Date(t).toISOString().slice(0, 10));
const cmdtyMarket = (code, name, symbol, oi, index, extra = {}) => ({code, name, exchange: 'NYMEX', symbol, category: 'energy', open_interest: oi, index,
  lead_name: 'Managed money', second_name: 'Producers', lead_pct: index / 5 - 10, second_pct: 10 - index / 5, as_of: '2026-09-15', price: 95.13,
  price_as_of: '2026-09-24', change_1m: 15.5, change_1y: -4.2, price_history: cmdtyWeeks.map((d, i) => [d, 50 + (i % 40)]),
  lead_history: cmdtyWeeks.map((d, i) => [d, (i % 30) - 10]), second_history: cmdtyWeeks.map((d, i) => [d, 10 - (i % 30)]), ...extra});

test('futures tab: commodities then financial futures, each market led by the group that carries its direction', () => {
  const financial = (code, name, symbol, oi, index, category, lead, second, extra = {}) =>
    cmdtyMarket(code, name, symbol, oi, index, {exchange: 'CBOT', category, lead_name: lead, second_name: second, ...extra});
  const commodities = {as_of: '2026-09-15', released: '2026-09-18', next: '2026-09-25', next_time: '3:30 PM ET', prices_as_of: '2026-09-24', price_status: 'ok',
    categories: {energy: 'Energy', grains: 'Grains', electricity: 'Electricity', treasuries: 'Treasuries', currencies: 'Currencies', volatility: 'Volatility', crypto: 'Crypto'},
    sections: [
      {key: 'physical', name: 'Physical commodities', status: 'ok', as_of: '2026-09-15',
       groups: [{key: 'energy', name: 'Energy', reading: 'Managed money leads; producers hedge.', open_interest: 3775767,
                 markets: [cmdtyMarket('067651', 'WTI crude oil', 'CL=F', 1955764, 55), cmdtyMarket('023651', 'Natural gas (Henry Hub)', 'NG=F', 1820003, 12, {price: 3.321})]},
                {key: 'grains', name: 'Grains', reading: '', open_interest: 1843824, markets: [cmdtyMarket('002602', 'Corn', 'ZC=F', 1843824, 100)]}],
       contracts: [{code: 'X1', name: 'PJM WESTERN HUB', exchange: 'Nodal', category: 'electricity', open_interest: 900000, spec_pct: 0},
                   {code: 'X2', name: 'NAT GAS ICE LD1', exchange: 'ICE Energy', category: 'energy', open_interest: 7995976, spec_pct: 2.4}]},
      {key: 'financial', name: 'Financial futures', status: 'ok', as_of: '2026-09-15',
       groups: [{key: 'treasuries', name: 'Treasuries', reading: 'Leveraged funds’ short is mostly the basis trade.', open_interest: 5377777,
                 markets: [financial('043602', '10-year Treasury note', 'ZN=F', 5377777, 100, 'treasuries', 'Asset managers', 'Leveraged funds', {price: 104.61})]},
                {key: 'currencies', name: 'Currencies', reading: '', open_interest: 542802,
                 markets: [financial('097741', 'Japanese yen', '6J=F', 542802, 97, 'currencies', 'Leveraged funds', 'Asset managers', {price: 0.006335})]},
                {key: 'volatility', name: 'Volatility', reading: '', open_interest: 446060,
                 markets: [financial('1170E1', 'VIX futures', '^VIX', 446060, 5, 'volatility', 'Leveraged funds', 'Asset managers', {price: 15.67})]},
                {key: 'crypto', name: 'Crypto', reading: '', open_interest: 20773,
                 markets: [financial('133741', 'Bitcoin', 'BTC=F', 20773, 6, 'crypto', 'Asset managers', 'Leveraged funds', {price: 84370})]}],
       contracts: [{code: 'Y1', name: 'NANO BITCOIN', exchange: 'Coinbase Derivatives', category: 'crypto', open_interest: 183449, spec_pct: -4.1}]}]};
  const app = renderEarnings(null, {}, {macro: {cards: []}, commodities, saved: {page: 'commodities'}});
  assert.equal(app.element('page-commodities').hidden, false);
  assert.equal(app.element('tab-label-commodities').textContent, '7 markets · COT Sep 15');
  assert.match(app.element('cmdty-note').innerHTML, /<span class="data-date">CFTC positions as of Tue, Sep 15, 2026<\/span>, released Fri, Sep 18, 2026; next report Fri, Sep 25, 2026, 3:30 PM ET\./);
  assert.match(app.element('cmdty-note').innerHTML, /7 markets charted in 6 categories, each ordered by open interest; 3 other contracts listed/);
  const page = app.element('cmdty-groups').innerHTML;
  const at = (text) => page.indexOf(text);
  assert.ok(at('Physical commodities') < at('WTI crude oil') && at('WTI crude oil') < at('Natural gas') && at('Natural gas') < at('Corn') && at('Corn') < at('Financial futures'));
  assert.match(page, /<h4 class="cmdty-group-title">Energy<span>2 markets · open interest 3\.8M<\/span><\/h4><p class="cmdty-reading">Managed money leads; producers hedge\.<\/p>/);
  assert.match(page, /data-commodity="067651"[^>]*aria-pressed="true"/);  // the most liquid market is charted first
  assert.match(page, /<div class="macro-value">95.13<\/div>/);
  assert.match(page, /<div class="macro-value">3.321<\/div>/);  // small prices keep three decimals
  assert.match(page, /<div class="macro-value">0.006335<\/div>/);  // yen futures keep their precision
  assert.match(page, /1 month <span class="up">\+15.5%<\/span> · 1 year <span class="down">−4.2%<\/span>/);
  assert.match(page, /key-ind" aria-hidden="true"><\/i>Managed money <b>\+1.0%<\/b> of OI · index 55 · <i class="key key-ind2" aria-hidden="true"><\/i>producers −1.0%/);
  // The label follows the group that leads and, for VIX, what a long position means.
  assert.match(page, /data-commodity="023651"[\s\S]*?Speculators crowded short/);
  assert.match(page, /data-commodity="002602"[\s\S]*?Speculators crowded long/);
  assert.match(page, /data-commodity="043602"[\s\S]*?Asset managers unusually long[\s\S]*?Asset managers <b>\+10.0%<\/b> of OI · index 100 · <i class="key key-ind2" aria-hidden="true"><\/i>leveraged funds −10.0%/);
  assert.match(page, /data-commodity="097741"[\s\S]*?Speculators crowded long<\/span>/);
  assert.match(page, /data-commodity="1170E1"[\s\S]*?Speculators crowded short volatility/);
  assert.match(page, /data-commodity="133741"[\s\S]*?Asset managers unusually light/);
  assert.match(page, /class="cmdty-mini"[^>]*>.*class="mini-price" d="M.*class="mini-zero".*class="mini-second" d="M.*class="mini-managed" d="M/);
  assert.match(page, /<p class="sentiment-meta data-date">COT Sep 15, 2026 · price Sep 24, 2026<\/p>/);
  // Every other contract, per section, by category and open interest, under the speculative group of its report.
  assert.match(page, /<summary>All 2 other commodity contracts in the COT report<\/summary>.*<th scope="col">Managed money net<\/th>/);
  assert.ok(at('Energy · 1') < at('Electricity · 1'));
  assert.match(page, /<th scope="row">NAT GAS ICE LD1<\/th><td>ICE Energy<\/td><td>7,995,976<\/td><td class="up">\+2.4%<\/td>/);
  assert.match(page, /<summary>All 1 other financial contracts in the COT report<\/summary>.*<th scope="col">Leveraged funds net<\/th>.*Crypto · 1.*NANO BITCOIN<\/th><td>Coinbase Derivatives<\/td><td>183,449<\/td><td class="down">−4.1%/);
  // The chart names the market and its two groups.
  assert.match(app.element('cmdty-chart-title').textContent, /^WTI crude oil and positioning$/);
  assert.match(app.element('cmdty-legend').innerHTML, /key-spx[^>]*><\/i>WTI crude oil 53.00 · [+−][\d.]+% over 5 years/);
  assert.match(app.element('cmdty-legend').innerHTML, /key-ind[^>]*><\/i>Managed money net, % of open interest [+−][\d.]+% · Sep 15/);
  assert.match(app.element('cmdty-legend').innerHTML, /key-ind2[^>]*><\/i>producers/);
  assert.match(app.element('cmdty-plot').innerHTML, /<text class="pane-label"[^>]*>WTI crude oil<\/text>/);
  assert.match(app.element('cmdty-windows').innerHTML, /<th scope="col">WTI crude oil<\/th><th scope="col">Managed money net average<\/th>/);
  assert.match(app.element('cmdty-windows').innerHTML, /<th scope="row">Since 2016<\/th>/);
  // Selecting a card charts it and is remembered; Enter works as well as a click.
  const card = (code) => ({closest: (s) => s === '[data-commodity]' ? {dataset: {commodity: code}} : null});
  app.element('cmdty-groups').handlers.click({type: 'click', target: card('043602')});
  assert.equal(app.storage['ivl-view'].commodity, '043602');
  assert.match(app.element('cmdty-chart-title').textContent, /^10-year Treasury note and positioning$/);
  assert.match(app.element('cmdty-legend').innerHTML, /key-ind[^>]*><\/i>Asset managers net, % of open interest/);
  assert.match(app.element('cmdty-legend').innerHTML, /key-ind2[^>]*><\/i>leveraged funds/);
  assert.match(app.element('cmdty-plot').innerHTML, /Asset managers net<tspan class="ref-label"> · line at 0: net flat<\/tspan>/);
  assert.match(app.element('cmdty-groups').innerHTML, /data-commodity="043602"[^>]*aria-pressed="true"/);
  app.element('cmdty-groups').handlers.keydown({type: 'keydown', key: 'Enter', preventDefault() {}, target: card('097741')});
  assert.match(app.element('cmdty-chart-title').textContent, /^Japanese yen and positioning$/);
  app.element('cmdty-groups').handlers.keydown({type: 'keydown', key: 'a', target: card('067651')});
  assert.match(app.element('cmdty-chart-title').textContent, /^Japanese yen/);  // other keys do nothing
  const empty = renderEarnings(null, {}, {macro: {cards: []}});
  assert.equal(empty.element('tab-label-commodities').textContent, 'Awaiting data');
  assert.match(empty.element('cmdty-note').innerHTML, /appear after the next data refresh/);
  assert.match(empty.element('cmdty-plot').innerHTML, /history appears after the next data refresh/);
});

test('a report saved by 3.3.0 still shows its commodities under the older field names', () => {
  const old = (code, name, oi, index) => ({code, name, exchange: 'NYMEX', symbol: 'CL=F', open_interest: oi, index, managed_pct: 5.4, producers_pct: 15.7,
    as_of: '2026-09-15', price: 95.13, price_as_of: '2026-09-24', price_history: cmdtyWeeks.map((d, i) => [d, 50 + (i % 40)]),
    managed_history: cmdtyWeeks.map((d, i) => [d, (i % 30) - 10]), producer_history: cmdtyWeeks.map((d, i) => [d, 10 - (i % 30)])});
  const commodities = {as_of: '2026-09-15', released: '2026-09-18', next: '2026-09-25', prices_as_of: '2026-09-24', categories: {energy: 'Energy'},
    groups: [{key: 'energy', name: 'Energy', open_interest: 1955764, markets: [old('067651', 'WTI crude oil', 1955764, 55)]}],
    contracts: [{code: 'X2', name: 'NAT GAS ICE LD1', exchange: 'ICE Energy', category: 'energy', open_interest: 7995976, managed_pct: 2.4}]};
  const app = renderEarnings(null, {}, {macro: {cards: []}, commodities, saved: {page: 'commodities'}});
  const page = app.element('cmdty-groups').innerHTML;
  assert.match(page, /<h3 class="cmdty-section-title">Physical commodities<\/h3>/);
  assert.match(page, /Managed money <b>\+5.4%<\/b> of OI · index 55 · <i class="key key-ind2" aria-hidden="true"><\/i>producers \+15.7%/);
  assert.match(page, /class="mini-managed" d="M/);
  assert.match(page, /NAT GAS ICE LD1<\/th><td>ICE Energy<\/td><td>7,995,976<\/td><td class="up">\+2.4%<\/td>/);
  assert.match(app.element('cmdty-legend').innerHTML, /key-ind2[^>]*><\/i>producers/);
});
