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
  assert.match(expanded, /&lt;only&gt;/);
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
  assert.match(imported, /As of 2026-09-17 · reported/);
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
    cot: {name: "COT: asset managers' net, % of open interest", frequency: 'weekly', source: 'CFTC', points: spx.filter((_, i) => i % 5 === 1).map(([d, v]) => [d, (6000 - v) / 100])},
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
  app.click('macro-range-seg', 'mrange', '20Y');
  assert.match(table(), /<tr class="current"><th scope="row">20 years<\/th>/);
  app.click('macro-range-seg', 'mrange', '1M');
  assert.match(table(), /<tr class="current"><th scope="row">1 month<\/th>/);
  assert.equal(app.storage['ivl-view'].mrange, '1M');
  app.click('macro-series-seg', 'mseries', 'aaii');
  assert.match(plot(), /class="dot-ind" cx=/);  // weekly points get markers in short ranges
  app.click('macro-series-seg', 'mseries', 'put_call');
  assert.match(plot(), /No Equity put\/call, 5-day average data in this range/);
  app.click('macro-series-seg', 'mseries', 'cot');
  assert.match(plot(), /COT net<tspan class="ref-label"> · line at 0: net flat<\/tspan>/);
  assert.match(app.element('macro-legend').innerHTML, /COT: asset managers&#39; net, % of open interest −[\d.]+% · Sep 15/);
  const empty = renderEarnings(null, {}, {macro: {cards: []}});
  assert.match(empty.element('macro-plot').innerHTML, /S&amp;P 500 history appears after the next data refresh/);
});

test('COT card shows leveraged funds and asset managers for the S&P 500 and VIX futures', () => {
  const cot = {key: 'cot', name: 'COT positioning', status: 'ok', as_of: '2026-09-15', max_age: 14, value: 37, reading: '+37.0% of OI',
    signal: 'Typical positioning', direction: 0, index: 72, detail: 'CFTC Traders in Financial Futures',
    groups: [{name: 'E-mini S&P 500', leveraged: -293143, leveraged_index: 72, asset_managers: 904684, asset_managers_index: 34},
             {name: 'VIX futures', leveraged: -16504, leveraged_index: 70, asset_managers: -52658, asset_managers_index: 1}]};
  const app = renderEarnings(null, {}, {macro: {cards: [cot]}});
  const card = app.element('macro-cards').innerHTML;
  assert.match(card, /<h3>COT positioning<\/h3><div class="macro-value">\+37.0% of OI/);
  assert.match(card, /Asset managers \+904,684 · index 34 · leveraged funds −293,143 · index 72/);
  assert.match(card, /VIX futures · asset managers −52,658 · index 1 · leveraged funds −16,504 · index 70/);
  assert.match(app.element('macro-note').textContent, /^1\/5 fresh readings/);
  app.click('macro-toggle');
  assert.match(app.element('macro-oneline').innerHTML, /<b>COT<\/b> \+37.0% of OI · Typical positioning/);
});

test('macro panel collapses to one line of readings, and the S&P 500 can use a log scale', () => {
  const cards = [{key:'vix', value:14.81, reading:'14.81', status:'ok', as_of:'2026-09-18', max_age:4, signal:'Calm', direction:1},
    {key:'aaii', value:-24.5, reading:'-24.5 pp', status:'ok', as_of:'2026-08-01', max_age:10, signal:'Bearish tilt', direction:-1},
    {key:'cnn', name:'CNN Fear & Greed', value:28.6, reading:'28.6/100', status:'ok', as_of:'2026-09-18', max_age:2, signal:'Fear', direction:-1}];
  const spx = []; for (let y = 1990; y <= 2026; y++) spx.push([`${y}-01-03`, 300 * 1.1 ** (y - 1990)]);
  const history = {spx, series: {aaii: {points: []}, vix: {points: []}, put_call: {points: []}, fear_greed: {points: []}}};
  const app = renderEarnings(null, {}, {macro: {cards, history}, saved: {macroOpen: false, mrange: 'MAX', mlog: true}});
  assert.equal(app.element('macro-body').hidden, true);
  assert.equal(app.element('macro-oneline').hidden, false);
  assert.equal(app.element('macro-toggle').attributes['aria-expanded'], 'false');
  const line = app.element('macro-oneline').innerHTML;
  assert.match(line, /<b>VIX<\/b> 14.81 · Calm/);
  assert.match(line, /<b>AAII<\/b> stale/);
  assert.match(line, /<b>Put\/call<\/b> —/);
  assert.match(line, /<b>Fear &amp; Greed<\/b> 28.6\/100 · Fear/);
  assert.match(app.element('macro-cards').innerHTML, /<h3>Fear &amp; Greed<\/h3>/);  // reports saved before the rename
  const ticks = [...app.element('macro-plot').innerHTML.matchAll(/class="tick" x="\d+" y="[\d.]+" text-anchor="end">([^<]+)</g)].map(m => m[1]);
  assert.deepEqual(ticks, ['500', '1,000', '2,000', '5,000', '10,000']);  // equal ratios, equal height
  assert.match(app.element('macro-plot').innerHTML, /S&amp;P 500<tspan class="ref-label"> · log scale<\/tspan>/);
  app.element('macro-log').handlers.change({target: {checked: false}});
  assert.doesNotMatch(app.element('macro-plot').innerHTML, /log scale/);
  app.element('macro-toggle').handlers.click({});
  assert.equal(app.element('macro-body').hidden, false);
  assert.deepEqual([app.storage['ivl-view'].mlog, app.storage['ivl-view'].macroOpen], [false, true]);
});

test('macro excludes stale data and retains source observation dates', () => {
  const app = renderEarnings(null, {}, {macro:{cards:[
    {key:'vix', value:15, reading:'15.00', status:'ok', as_of:'2026-09-17', max_age:4, signal:'Calm', direction:1},
    {key:'aaii', value:-25, reading:'-25 pp', status:'ok', as_of:'2026-08-01', max_age:10, signal:'Bearish', direction:-1},
  ]}});
  assert.equal(app.element('macro-summary').textContent, 'Limited coverage');
  assert.match(app.element('macro-note').textContent, /1\/5 fresh readings/);
  assert.match(app.element('macro-cards').innerHTML, /Stale · excluded/);
  assert.match(app.element('macro-cards').innerHTML, /As of 2026-08-01/);
  assert.match(app.element('macro-cards').innerHTML, /Unavailable/);
});
