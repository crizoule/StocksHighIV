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
    universe_by_cap: options.stats,
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
