(() => {
  const el = id => document.getElementById('local-' + id);
  let watchSignature = '';
  let token = '', initialized = false, completion = null;
  const initialStamp = Number(el('controls').dataset.reportStamp);
  const storageGet = key => { try { return localStorage.getItem(key); } catch { return null; } };
  const storageSet = (key, value) => { try { localStorage.setItem(key, value); } catch {} };
  function timeVisibility() {
    el('custom').hidden = el('preset').value !== 'custom';
  }
  el('preset').onchange = timeVisibility;
  el('settings').onsubmit = async event => {
    event.preventDefault();
    const time = el('preset').value === 'custom' ? el('time').value : el('preset').value;
    try {
      const response = await fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-App-Token': token},
        body: JSON.stringify({mode: el('mode').value, time}), signal: AbortSignal.timeout(10000)});
      if (!response.ok) throw Error('Could not save schedule. Check the time and connection.');
      el('settings-result').textContent = el('mode').value === 'auto' ? `Saved · weekdays at ${time} ET` : 'Saved · manual downloads';
    } catch (error) { el('settings-result').textContent = error.message; }
  };
  el('notify').onclick = async () => {
    if (!('Notification' in window)) {
      el('notify-result').textContent = 'This browser does not support notifications. Completion banners are still enabled.';
      return;
    }
    try {
      const permission = await Notification.requestPermission();
      el('notify-result').textContent = permission === 'granted' ? 'Notifications enabled for this browser.' : 'Permission not granted. Completion banners remain enabled.';
    } catch { el('notify-result').textContent = 'Notifications unavailable. Completion banners remain enabled.'; }
  };
  async function changeWatch(symbol, action) {
    try {
      const response = await fetch('/api/watchlist', {method:'POST', headers:{'Content-Type':'application/json','X-App-Token':token}, body:JSON.stringify({symbol,action})});
      if (!response.ok) throw Error('Could not save ticker. Use a valid US or Canadian ticker (maximum 100 favorites).');
      watchSignature = ''; el('watch-result').textContent = 'Saved. New additions are included in the next download.';
      el('watch-symbol').value = '';
    } catch(error) { el('watch-result').textContent = error.message; }
  }
  el('watch-form').onsubmit = event => { event.preventDefault(); changeWatch(el('watch-symbol').value, 'add'); };
  async function poll() {
    try {
      const response = await fetch('/api/status', {cache: 'no-store', signal: AbortSignal.timeout(10000)});
      if (!response.ok) throw Error();
      const state = await response.json();
      token = state.token;
      const signature = JSON.stringify(state.watchlist || []);
      if (watchSignature !== signature) {
        watchSignature = signature;
        el('watch-items').replaceChildren();
        for (const symbol of state.watchlist || []) {
          const button = document.createElement('button');
          button.type = 'button'; button.textContent = symbol + ' ×'; button.title = 'Remove ' + symbol;
          button.onclick = () => changeWatch(symbol, 'remove'); el('watch-items').appendChild(button);
        }
        window.dispatchEvent(new CustomEvent('highiv-watchlist', {detail:state.watchlist || []}));
      }
      if (!initialized) {
        el('mode').value = state.settings.mode;
        el('time').value = state.settings.time;
        el('preset').value = ['11:00', '16:30'].includes(state.settings.time) ? state.settings.time : 'custom';
        timeVisibility(); initialized = true;
      }
      el('saved').hidden = !state.has_dashboard;
      const saved = state.dashboard_saved_at ? ` · saved ${new Date(state.dashboard_saved_at * 1000).toLocaleString()}` : '';
      let scanProgress = '';
      if (state.phase === 'scan' && state.total > 0) {
        const percent = Math.min(100, Math.max(0, Math.floor(state.completed / state.total * 100)));
        const minutes = state.eta == null ? null : Math.max(1, Math.ceil(state.eta / 60));
        const remaining = minutes == null ? 'estimating time remaining' : minutes >= 60
          ? `~${Math.floor(minutes / 60)}h ${minutes % 60}m remaining` : `~${minutes}m remaining`;
        scanProgress = `IV scan ${percent}% · ${remaining} (IV scan only) · `;
      }
      el('progress').textContent = state.status === 'running'
        ? `Downloading · ${scanProgress}${state.activity}${state.rate ? ` · ${state.rate.toFixed(1)} companies/min` : ''}${saved}`
        : state.status === 'error' ? `Download/setup needs attention — see progress${saved}`
        : state.status === 'setup' ? `Setting up${saved}` : `Showing saved market data${saved}`;
      if (state.update_waiting) el('progress').textContent += ' · App update will install after this download finishes';
      if (state.dashboard_saved_at > initialStamp) el('complete').hidden = false;
      if (state.completion_id && completion !== state.completion_id) {
        completion = state.completion_id;
        const key = 'highiv-notified-' + state.identity;
        if (storageGet(key) !== completion) {
          storageSet(key, completion);
          el('complete').hidden = false;
          if ('Notification' in window && Notification.permission === 'granted') {
            try {
              const notice = new Notification('StocksHighIV dashboard ready', {body: 'Your market data download is complete. Open the updated dashboard.', tag: 'highiv-complete'});
              notice.onclick = () => { window.focus(); window.location.href = '/dashboard.html'; notice.close(); };
            } catch { /* The visible banner works even if the browser blocks notifications. */ }
          }
        }
      }
    } catch { el('progress').textContent = 'App disconnected. Reopen the launcher; your displayed dashboard remains available.'; }
    setTimeout(poll, 3000);
  }
  poll();
})();
