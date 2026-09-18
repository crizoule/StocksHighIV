# Implied Vol Leaders

A daily screen with two market-cap tabs: US$1B to below US$100B, and US$100B or more. Each tab independently selects up to 100 stocks with the highest implied volatility among US and Canadian listings, with float and short-interest context for squeeze setups and 52-week context for both IV and price. Each row carries a one-line description of what the company does. List size lives in `TOP_N` and the US$100B boundary in `LARGE_MARKET_CAP_USD` in `highiv/config.py`. Companies at exactly US$100B belong to the upper tab. Both tabs retain the same columns, price charts, conservative catalyst checks and listing/HQ filters. Each filter combination is ranked within its selected cap band; sorting a column reorders that top-100 selection. The overview and IV distribution also use the selected cap band.

Output: `output/dashboard.html` (open in any browser). Each run also saves `data/snapshots/<date>.json`. See **Setup** below before the first run.

To serve the generated dashboard locally:

```bash
.venv/bin/python -m http.server 8931 --bind 127.0.0.1 --directory output
```

Then open http://127.0.0.1:8931/dashboard.html.

Generated market data, snapshots, logs, backups and HTML reports are local runtime files and are not included in Git. Run the screen to create them.

## Easy launch (Windows or Mac)

**Windows users:** [Download the Windows app](https://github.com/crizoule/StocksHighIV/releases/latest/download/StocksHighIV-Windows.zip), extract it, and open **StocksHighIV.exe**. Requires Windows 10/11 x64 and Python 3.11+ (enable Add Python to PATH). The launcher checks for updates automatically and offers an Install button. It preserves your data between app versions. Windows may show a SmartScreen prompt because the executable is not Authenticode-signed.

**Mac users:** [Download the signed and Apple-notarized app](https://github.com/crizoule/StocksHighIV/releases/latest/download/StocksHighIV-macOS.zip), extract it, and open **StocksHighIV.app**. Supports Intel and Apple Silicon on macOS 12+. Install Python 3.11+ from python.org first. The app opens the dashboard in your browser; quit it from the Dock/menu when finished. A normal first-open confirmation may appear. GitHub’s source ZIP contains the development launchers, not the notarized app.

From version 1.1.0, the Mac app checks for updates daily and offers **Check for Updates…** in its app menu. After you choose to install, it waits for an active data download to finish before replacing and relaunching the app. Your watchlist, schedule, and data are preserved. Users on 1.0.x need to download this version once manually. Future updates must be published as signed GitHub Releases; pushing source code alone does not trigger an update. The packaged Windows launcher also supports signed in-app updates from version 1.2.0; source launchers still update manually.

Developer build instructions are in [packaging/macos](packaging/macos/README.md). The source-based steps below also support Windows.

1. Download the GitHub ZIP and **extract the entire folder** (or clone the repository).
2. Install **Python 3.11 or newer** from [python.org](https://www.python.org/downloads/) if needed. On Windows, enable **Add Python to PATH** during installation.
3. Double-click **Start StocksHighIV.bat** on Windows or **Start StocksHighIV.command** on Mac.
4. Your browser opens **the saved dashboard directly** at **http://127.0.0.1:8932/**. On the first launch, when no saved data exists, it shows the setup/download page instead. Dependency setup runs automatically. Use **Refresh data / download progress** above the dashboard whenever you want to update it.
5. Click **Download market data**. The page shows the current stock/provider, completed and total quotes, measured companies per minute, elapsed time, scan ETA, failures, and provider retry/backoff messages. Enrichment shows completed companies without inventing a fixed total or ETA.

A first download usually takes about an hour, depending on provider response times. Downloads default to **Manual**. Under **Download schedule & notifications**, choose **Automatic** for weekdays at **11:00 AM ET**, **4:30 PM ET (after close)**, or a custom Eastern time. Settings persist across restarts. The app must be running and the computer awake; reopening after the scheduled time catches up once that weekday. Weekends are skipped, but exchange holidays are not excluded. Failed scheduled runs are not automatically retried; use Resume. **Open saved dashboard** remains available while refreshing; the final report replaces the old one atomically. **Refresh market data** starts a fresh scan; **Resume unfinished download** reuses completed stocks from today and retries unfinished/failed requests, including after an interrupted refresh. Enrichment restarts when resuming. This is a local application, not a public web server; it binds only to `127.0.0.1`.

Keep the launcher window open. Ctrl+C stops the server and its download process; relaunch and choose Resume to continue. Reopening the launcher while it is already running opens the existing app. A closed browser tab does not stop the download. The previous dashboard stays visible during updates; a completion banner links to the new report. Enable browser notifications from the schedule panel to receive an additional alert while a dashboard/progress tab is open. Browser permission is required.

macOS may block the downloaded, unsigned `.command` launcher on first use. If you trust your downloaded copy, follow [Apple’s instructions](https://support.apple.com/en-gb/102445): dismiss the warning, then use **System Settings → Privacy & Security → Open Anyway** for this launcher. Changing the landing page does not remove this macOS approval requirement.

If the Mac ZIP extraction drops execute permission, run `chmod +x "Start StocksHighIV.command"` once from the extracted folder. You can also launch directly with `python3 launch.py` (Mac) or `py -3 launch.py` (Windows). If port 8932 is occupied by another application, use `python3 launch.py --port 8933` (or `py -3` on Windows). Failed dependency installation can be retried from the page after fixing the internet connection or Python installation. The `.venv` directory belongs to this machine; do not copy it between Windows and Mac.

The command-line examples below use Mac/Linux paths; on Windows replace `.venv/bin/python` with `.venv\Scripts\python.exe`.

## Company logos

Displayed companies use 48-pixel WebP logos (shown at 24 pixels), cached in `data/logos` for 90 days and embedded in saved dashboards for offline use. Images come from Financial Modeling Prep using the full listing symbol. Missing logos fall back to ticker initials and are retried after a day. Source downloads are capped at 256 KB and encoded icons at 12 KB; no API key is required for this image endpoint. Logo availability depends on the provider.

## Watchlist

Use the star beside a ticker or the Watchlist panel to save up to 100 favorites. The Watchlist tab shows their available data independently of the top-100 rankings, cap bands, and location filters. Favorites are stored in `data/watchlist.json` (shared across Mac app versions). US symbols and Canadian Yahoo symbols such as `SHOP.TO` are supported. New additions enter the next Refresh or Resume; additions during a scan may require the next run. Watched stocks bypass cap and industry exclusions for the watchlist only. Missing IV or failed enrichment is indicated without inventing values. Removing a favorite stops forced inclusion in future scans. Changes require the local app; exported HTML shows the watchlist saved in its snapshot.

## Run

Every command starts by moving into the project folder — the paths below are relative to it:

```bash
cd ~/StocksHighIV
```

```bash
.venv/bin/python -m highiv run      # scan + build (~1 hour)
```

```bash
.venv/bin/python -m highiv scan     # IV for every stock only (~45 min, almost all of it waiting on Cboe's rate limit); resumable
```

```bash
.venv/bin/python -m highiv build    # rank, add float/short/price/earnings data, write the dashboard (~20 min)
```

```bash
.venv/bin/python -m highiv explain  # refresh the Why this IV column from Yahoo news (~2 min); no rescan
```

Scans resume completed symbols and retry failed requests. A valid response with no usable IV
counts as completed. To replace quotes from an earlier run on the same day (for example, after
the close following a morning scan), use:

```bash
.venv/bin/python -m highiv run --refresh-quotes
```

`scan` also accepts `--refresh-quotes`. `--refresh-universe` only rebuilds the stock list;
it does not refresh completed quotes. The daily wrapper forwards these options too:
`./run_daily.sh --refresh-quotes`.

Existing databases are migrated automatically on the next scan or build. Successful quotes and
IV history are retained; old results with missing IV are retried because the earlier database
did not distinguish provider failures from valid responses without IV.

`python -m highiv` only finds the package when the project folder is the working directory, so calling the venv's python by absolute path from elsewhere fails with `No module named highiv`. To run from anywhere, either point `PYTHONPATH` at the project:

```bash
PYTHONPATH=~/StocksHighIV ~/StocksHighIV/.venv/bin/python -m highiv run
```

or use the wrapper, which moves into its own directory first and logs to `data/logs/`:

```bash
~/StocksHighIV/run_daily.sh
```

Output paths come from the package location, not the shell's working directory, so every form writes to the same `data/` and `output/` folders.

Run after the US close (4:30 pm ET or later) so IV reflects end-of-day values. `./run_daily.sh` wraps `run` with a dated log in `data/logs/`.

## Setup

Clone the repository and create a Python environment (Python 3.11+):

```bash
git clone https://github.com/crizoule/StocksHighIV.git ~/StocksHighIV
cd ~/StocksHighIV
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Alternatively, with `uv`:

```bash
uv venv .venv --python 3.13
uv pip install --python .venv/bin/python -r requirements.txt
```

Run after the US close (4:30 pm ET or later) so the IV readings are end of day. `run_daily.sh` wraps `run` with a dated log in `data/logs/`, ready for launchd or cron.

## How it works

1. **Universe.** US: Nasdaq's screener (NYSE, Nasdaq, NYSE American) filtered to common stock and ADRs over US$1B that appear in OCC's list of optionable underlyings. Canada: Yahoo's screener for TSX/TSXV, converted to USD, minus preferreds, CDRs and bullion trusts. Canadian companies that also list in the US are kept once, on the US line.
2. **IV30.** US listings: Cboe's delayed-quote `iv30`, capped at 55 requests/minute (Cboe blocks for a minute past ~60). TSX-only listings: Montréal Exchange option chains, at-the-money IV per expiry, interpolated in variance to 30 days.
3. **Enrichment.** The top names for each market-cap tab and dashboard filter get Yahoo Finance float, short interest, days to cover, 52-week range, a one-line business description, price history (1H and 1D bars, with 4H, 1W and 1M resampled from them — hourly over three months and daily over ten years, so every frame's MACD warms up) and the next earnings date. Yahoo pads an estimated earnings date with a placeholder hour, so the hour is shown only when the date is confirmed. Nasdaq and Yahoo occasionally disagree on market cap, so a stock has to clear US$1B on both to stay in the list.
4. **Why this IV.** A headline qualifies only when it names the company (or explicitly tags its ticker), describes a concrete event, has a publisher and HTTP(S) source link, and was published within seven days on or before the IV session. Recent financing, merger, guidance, earnings, regulatory and operating events are labeled **possible catalysts**, never proven causes. Price moves, buzzwords, opinion, speculation and law-firm solicitation are rejected. Upcoming earnings carry an estimated, confirmed or unconfirmed date label. Alongside event evidence, structural context identifies commodity cycles, memory and storage cycles, explicit AI/compute infrastructure exposure, issuer-documented AI disruption risk, and crypto activity. These are business sensitivities, not assertions that a theme caused this IV. Rules use the cached company profile; Adobe and Fiverr disruption notes cite reviewed annual reports and require issuer identity and report availability. An incidental AI mention is insufficient. Bitcoin, Ether, crypto mining and trading platforms are distinguished. Missing or truncated profiles can leave coverage gaps. If evidence is insufficient, the cell says **No clear catalyst found**; a failed news lookup is marked separately. Refresh this column with `python -m highiv explain`. The snapshot retains the fetched headlines and lookup time for review. Every report write also recalculates context from cached data; it does not need another news request. The expanded row shows exposure evidence and source links. Roughly three-month stock returns use completed daily closes no later than the IV date, in the listing currency and excluding dividends. Rally/selloff descriptions require a rise of at least 25% followed by a decline of at least 20%; the reverse requires a decline of at least 20% and a rebound of at least 25%. Peer comparisons require at least three other companies sharing the exposure, identical endpoints, at least 50 closes and no gaps longer than seven calendar days. Recent IPOs, stale prices and incomplete windows are excluded. Both market-cap tabs contribute to the enriched peer sample; this is a selected high-IV sample, not an industry index. A peer move is highlighted only when its median is at least +20% or at most −20% and at least two-thirds move in that direction. The subject is excluded and all members and dates are disclosed. No theme rally, hype, causal link or future direction is inferred from co-movement.
5. **Realized volatility and momentum.** From the daily bars already in hand: HV30 (annualized standard deviation of the last 30 closes) with the IV ÷ HV ratio, RSI(14), and today's volume against its 20-day average. All three use completed sessions, so an in-progress day never distorts them. Each timeframe also carries MACD (12/26/9) for the row-detail charts, computed over the full history and then trimmed to the visible window so the EMAs are warmed up rather than starting mid-chart — which is why hourly bars are pulled over three months.
6. **Borrow costs.** Interactive Brokers publishes a daily lendable-stock file per region over FTP (`ftp2.interactivebrokers.com`, user `shortstock`, `usa.txt` and `canada.txt`): annual borrow fee and shares available. Two files cover every symbol, and class shares differ by region — `BRK B` in the US file, `BBD.B` in the Canadian one.
7. **IV rank / percentile.** Computed from daily IV30 history in `data/highiv.sqlite`. Each US name starts with ~3 months of AlphaQuery history (scaled to Cboe's level on their last shared day); every run adds a day, so the look-back reaches a full 52 weeks after a year of daily runs.

## Squeeze setup heuristic

- **High:** short interest ≥ 20% of float with ≥ 5 days to cover, or ≥ 30% alone
- **Elevated:** ≥ 10% with ≥ 3 days to cover, or ≥ 20% alone
- **Small float:** under 50M freely traded shares

Thresholds live in `highiv/config.py`.

## Caveats

- These are free, unofficial public endpoints meant for light personal use; any of them can change or throttle without notice.
- The Why this IV column screens headline metadata, not article bodies. A listed event is a possible contributor, not a verified cause of IV. Conservative matching can miss valid catalysts, and the news feed is not exhaustive. “No clear catalyst found” means insufficient qualifying evidence, not proof that no catalyst exists.
- Short interest is published twice a month (check the settlement date). For interlisted Canadian names, Yahoo's figure covers the US listing only.
- Yahoo occasionally reports a float from the wrong share class; implausible floats are dropped rather than shown.
- A true 52-week IV rank from day one needs a paid history source (for example AlphaQuery premium or ORATS), or a broker API that exposes IV rank.

## Tests

Run the offline regression suite from the project folder:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -v
node --test tests/test_dashboard.cjs tests/test_launcher.cjs
```

The tests use temporary databases and mocked providers, including a complete dashboard build
with a failed price provider. They do not fetch market data or modify saved scans and snapshots.
The JavaScript tests require Node.js and check the earnings display in both table and detail views.
