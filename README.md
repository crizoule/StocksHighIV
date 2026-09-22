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

From version 1.1.0, the Mac app checks for updates daily and offers **Check for Updates…** in its app menu (⌘,). After you choose to install, it waits for an active data download to finish before replacing and relaunching the app. Your watchlist, schedule, and data are preserved. Users on 1.0.x need to download this version once manually. Future updates must be published as signed GitHub Releases; pushing source code alone does not trigger an update. The packaged Windows launcher also supports signed in-app updates from version 1.2.0; source launchers still update manually.

Developer build instructions are in [packaging/macos](packaging/macos/README.md). The source-based steps below also support Windows.

1. Download the GitHub ZIP and **extract the entire folder** (or clone the repository).
2. Install **Python 3.11 or newer** from [python.org](https://www.python.org/downloads/) if needed. On Windows, enable **Add Python to PATH** during installation.
3. Double-click **Start StocksHighIV.bat** on Windows or **Start StocksHighIV.command** on Mac.
4. Your browser opens **the saved dashboard directly** at **http://127.0.0.1:8932/**. On the first launch, when no saved data exists, it shows the setup/download page instead. Dependency setup runs automatically. Use **Refresh data / download progress** above the dashboard whenever you want to update it.
5. Click **Download market data**. The page shows the current stock/provider, completed and total quotes, measured companies per minute, elapsed time, scan ETA, failures, and provider retry/backoff messages. Enrichment shows completed companies without inventing a fixed total or ETA.

A first download usually takes about 50 minutes, depending on provider response times. After that, each new close is downloaded in two passes: about 900 likely leaders first (large caps, TSX listings, your watchlist, and the 500 highest IVs of the last scan), then a preliminary dashboard after roughly 20 minutes, marked as such, while the remaining lower-IV stocks download; the complete dashboard replaces it about half an hour later. Downloads before the next close reuse what is already final and take a minute or two. Downloads default to **Manual**. Under **Download schedule & notifications**, choose **Automatic** for weekdays at **11:00 AM ET**, **4:30 PM ET (after close)**, or a custom Eastern time. Settings persist across restarts. The app must be running and the computer awake; reopening after the scheduled time catches up once that weekday. Weekends are skipped, but exchange holidays are not excluded. Failed scheduled runs are not automatically retried; use Resume. **Open saved dashboard** remains available while refreshing; the final report replaces the old one atomically. **Refresh market data** starts a fresh scan; **Resume unfinished download** reuses completed stocks from today and retries unfinished/failed requests, including after an interrupted refresh. Enrichment restarts when resuming. This is a local application, not a public web server; it binds only to `127.0.0.1`.

Keep the launcher window open. Ctrl+C stops the server and its download process; relaunch and choose Resume to continue. Reopening the launcher while it is already running opens the existing app. A closed browser tab does not stop the download. The previous dashboard stays visible during updates; a completion banner links to the new report. Enable browser notifications from the schedule panel to receive an additional alert while a dashboard/progress tab is open. Browser permission is required.

macOS may block the downloaded, unsigned `.command` launcher on first use. If you trust your downloaded copy, follow [Apple’s instructions](https://support.apple.com/en-gb/102445): dismiss the warning, then use **System Settings → Privacy & Security → Open Anyway** for this launcher. Changing the landing page does not remove this macOS approval requirement.

If the Mac ZIP extraction drops execute permission, run `chmod +x "Start StocksHighIV.command"` once from the extracted folder. You can also launch directly with `python3 launch.py` (Mac) or `py -3 launch.py` (Windows). If port 8932 is occupied by another application, use `python3 launch.py --port 8933` (or `py -3` on Windows). Failed dependency installation can be retried from the page after fixing the internet connection or Python installation. The `.venv` directory belongs to this machine; do not copy it between Windows and Mac.

The command-line examples below use Mac/Linux paths; on Windows replace `.venv/bin/python` with `.venv\Scripts\python.exe`.

## Sentiment

The macro panel can be collapsed from its title to a single line of the five readings; the dashboard remembers the choice. It shows dated VIX closes, Cboe equity/total/index put-call volume ratios, the AAII weekly bull–bear spread, CNN Fear & Greed when its public website feed is accessible, and weekly futures positioning from the CFTC's Commitments of Traders report. Readings retain observation and retrieval dates. Sources are cached for six hours, and a reading of the latest close is kept until the next session opens (news and social posts excepted); failed requests retain the last good observation, explicitly marked as cached or stale. Stale inputs are excluded (4 calendar days for daily Cboe data, 10 for AAII, 2 for CNN, 14 for COT). AAII and CNN can block automated requests; the app does not bypass those restrictions. For AAII, you enter each week by hand (below). For CNN, a separately labelled replica (below) stands in when CNN has no fresh reading. The panel needs at least three fresh readings to describe an overall tilt and explains overlapping CNN inputs.

**Commitments of Traders (COT).** The CFTC publishes futures positions by trader group every Friday at 3:30 PM ET, as of the previous Tuesday, through its free [public reporting API](https://publicreporting.cftc.gov/) (Traders in Financial Futures, futures only). One request fetches every week since June 2006. The card shows asset managers' (pension funds, mutual funds, insurers) net E-mini S&P 500 position as a share of open interest, with its three-year COT index (0 = least long, 100 = most long; ≥80 and ≤20 mark crowded or light positioning). Leveraged funds and VIX futures are listed for reference. Asset managers' net position has moved with the S&P 500 week to week (+0.35 over 10 years), while leveraged funds' has moved against it (−0.54), because much of it hedges stock holdings or arbitrages futures against the cash index; it is therefore not used as the headline reading. COT is positioning, not a survey of opinion.

**AAII weekly entry.** AAII publishes its survey every Thursday, and the app does not fetch it. When a newer week is out, the AAII card shows a small “New week out” note. Click it and copy the three percentages from [AAII's results page](https://www.aaii.com/sentimentsurvey) into the card. The week is saved on this computer in `data/sentiment/aaii-manual.json` and shows immediately, with no data refresh needed. It needs the dashboard opened through the StocksHighIV app. The card uses whichever is newest: AAII's page, the week you entered, an AAII spreadsheet (`sentiment.xls`, `.xlsx` or `.csv`) you place in `data/imports`, or AAII's weekly history since 1987, bundled in `highiv/aaii_history.csv` for personal use. Like any AAII reading, an entry is excluded once it is more than 10 days old.

**S&P 500 and sentiment chart.** Below the cards, one chart shows the S&P 500 on top and one sentiment series underneath, on the same dates: the AAII bull–bear spread, VIX, Cboe's equity put/call ratio (5-day average), CNN Fear & Greed, COT asset-manager positioning, or the S&P 500's own RSI 14 and MACD 12/26/9. Those last two are technical context, not sentiment: they are calculated from the index's daily closes (no extra download), MACD is shown as a percentage of the index so 1987 and today share a scale and is drawn the usual way — MACD line, signal line, and histogram bars coloured green above zero and red below, faded when a bar is shorter than the one before it. Every session gets its own bar while there is room, up to about a year; longer ranges group them into weekly, monthly or quarterly bars, each keeping its last session's value, and the pane says which. The two lines stay at daily detail throughout. and the correlation column notes that their agreement with the S&P 500 is arithmetic rather than a relationship between two sources. Both are computed from daily closes before older points are thinned to weekly. CNN's feed reaches back about five years, so the replica's own scores continue the Fear & Greed line before that, back to August 2009; the chart's source line names both and where they join. The replica's history is used throughout when CNN's is unavailable. Buttons switch between 1 month, 3, 6, 1 year, 5, 10 and 20 years, and Max (since July 1987, when AAII's survey begins). Hovering shows both values for a date. A Log scale switch puts the S&P 500 on a logarithmic scale, where equal percentage moves take equal height; it keeps decades of growth readable on long ranges. The two lines have separate scales in separate panels, because two y-axes on one plot can make any two lines look related. A table below gives, for every range, the S&P 500's change, the series' average and low–high, and the correlation of its changes with S&P 500 returns between the same dates (weekly for AAII, daily for the others; at least 8 changes). Points from the last 10 years are daily; older points are weekly (each week's last close, weeks ending Wednesday like AAII's survey). Each series covers what its source provides: the S&P 500 and AAII since 1987, VIX since 1990, Fear & Greed since August 2009 (CNN's own feed about 5 years, the replica before it), COT since June 2006, and put/call from Cboe's discontinued 2003–2019 files, then the app's stored daily statistics. Gaps between those are drawn as breaks. Before June 2012, Cboe's equity ratio included ETF options. The Cboe files are refreshed monthly.

**Fear & Greed replica.** When CNN's feed is available, the CNN card shows the replica as a cross-check. When the feed is blocked or stale, the card shows the replica instead, labelled as not CNN's reading. The replica applies CNN's scoring rule, recovered from CNN's published history, to public stand-ins for CNN's seven inputs. Each input is z-scored against its trailing 125 sessions, then ranked against its trailing 500 z-scores. Volatility stays at 50 unless it ranks in extreme fear. The score is the average of the available components; it needs at least five.

| Component | Replica input | Source |
|---|---|---|
| Market momentum | S&P 500 vs its 125-day average | Yahoo `^GSPC` |
| Stock price strength | Net new 52-week highs as % of stocks, 20-session average | Yahoo daily bars for the screen's NYSE stocks (≥ $1B) |
| Stock price breadth | McClellan volume summation index | Same NYSE stocks |
| Put and call options | 5-session average put/call ratio | Cboe daily market statistics |
| Market volatility | VIX vs its 50-day average | Yahoo `^VIX` |
| Junk bond demand | HYG minus LQD trailing 12-distribution yield, lagged one session | Yahoo prices and distributions |
| Safe haven demand | S&P 500 minus IEF return over 20 sessions | Yahoo `^GSPC`, `IEF` |

Checked against CNN's published history on September 18, 2026:

- **Scoring rule:** fed CNN's own published inputs, the recovered rule reproduces CNN's index to within 0.2 points on average, with the same rating on 99.9% of 809 sessions.
- **Full replica:** from public data with all seven components, 272 sessions from August 2025 to September 2026. Correlation 0.97, average gap 3.0 points, 90% of sessions within 6.5 points, largest gap 15.9. The rating matched on 81% of sessions.
- **Six-component version:** without the put/call component, over a longer window of 865 sessions since April 2023. Correlation 0.97, average gap 3.4 points.
- **Long history:** scored back to August 2009. Over the overlap with CNN's published feed (1,254 sessions from September 2021), correlation 0.97, average gap 3.9 points, same rating on 75% of sessions. Sessions whose put/call history has not been backfilled yet score on six components.

The widest component gaps come from put/call, where CNN's series matches no public Cboe ratio exactly, and junk bond demand, where CNN's feed carries one-day spikes around bond ETF payout dates that the replica does not copy.

Each replica refresh downloads 20 years of daily bars for roughly 1,400 NYSE stocks, which takes about 40 seconds, at most once per close once the Cboe history is complete. Stocks are folded into daily market-wide counts a group at a time and released, so the whole market never sits in memory: 20 years now costs about what four years did before (roughly 660 MB peak for the refresh). The replica scores every session back to August 2009, as far as the inputs reach: junk bond demand needs HYG, listed in 2007, and every input needs 625 sessions of warm-up before it can be ranked. Strength and breadth are computed over today's universe (NYSE-listed, ≥ $1B), so older stretches carry survivorship bias; the other five components do not. The first refreshes also backfill Cboe put/call history, stored in `data/sentiment/put_call_history.json`, until it meets the archive files: Cboe's daily page starts on 2019-10-07, the session after the last archive file ends, so the two sources join without a gap. That is about 1,500 sessions from scratch, filled over several refreshes. The backfill runs up to four requests at once, stays within the scan's Cboe rate limit, and stops after three minutes per refresh. Until the history is complete, the replica runs on six components and says so. To re-check agreement with CNN's published history (needs CNN's feed and a complete Cboe history):

```sh
python -m highiv fear-greed-check
```

The sortable **Sentiment · Stock / sector** column shows a 0–100 heuristic and data coverage. Expand a stock to review each component, formula, observation date and source:

- Stock momentum (40% base weight): `clamp(50 + 2 × return_5_sessions + return_20_sessions, 0, 100)`, with returns in percent.
- Sector momentum (30%): the same formula on percentage-point excess returns of the matching US sector ETF over SPY. Uses sector benchmarks, not a biased sample of high-IV peers. Canadian listings also use this explicitly labeled US proxy.
- News (20%, optional): Alpha Vantage ticker sentiment, relevance weighted and mapped from −1…+1 to 0…100. Requires at least three distinct article URLs from two publishers in the past seven days, exact US ticker matches, and relevance ≥0.2. One latest-news feed request per refresh limits cost and coverage; it is not an exhaustive news search.
- Social (10%, optional): Stocktwits normalized 24-hour community sentiment, alongside message activity. US listings only; retrieval time is shown because the endpoint does not supply an observation timestamp. Excluded after one calendar day.

Missing components are omitted and available weights are rescaled; they are never assigned a neutral score. Ranking requires both fresh stock and sector prices with matching end dates. Scores below 40 are Negative, 40–60 Mixed, and above 60 Positive. When opinion feeds are missing, the column explicitly says **Price only**. These are screening heuristics, not calibrated probabilities, recommendations or evidence of a catalyst. Open saved dashboards also check age before displaying scores.

Optional feeds require your own authorized credentials: `ALPHAVANTAGE_API_KEY`, and/or `STOCKTWITS_USERNAME` plus `STOCKTWITS_PASSWORD` (Firestream-authorized account). Set them in the environment of the process launching the app, or, for the packaged app — which is opened from Finder or the Start menu and so inherits no shell — write them one per line as `NAME=value` in `data/credentials.env` beside the saved market data (`~/Library/Application Support/StocksHighIV/data` on Mac, the app folder on Windows). Only those three names are read, values never reach the log, and the file belongs to you alone: keep it out of version control. They are never embedded in the dashboard. Provider quotas and licensing apply; a website subscription does not necessarily include API access. Without credentials, momentum still works and opinion components show unavailable. See [Alpha Vantage](https://www.alphavantage.co/documentation/#news-sentiment) and [Stocktwits Firestream](https://firestream.stocktwits.com/documentation/sentiment-detail).

Sentiment is collected automatically during each report build. To refresh it on the saved screen without rescanning IV (while no other build is running):

```sh
python -m highiv sentiment
```

This preserves the underlying market-data generation timestamp. It uses saved stock prices and fetches benchmarks; stale or mismatched stock dates require a normal data refresh before ranking resumes.

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
.venv/bin/python -m highiv run      # scan + build; after the first run: likely leaders, a preliminary dashboard (~20 min), then the rest
```

```bash
.venv/bin/python -m highiv scan     # IV for every stock only (~45 min after each new close, almost all of it waiting on Cboe's rate limit); resumable
```

```bash
.venv/bin/python -m highiv build    # rank, add float/short/price/earnings data, write the dashboard (~4 min; ~1 min for the same close)
```

```bash
.venv/bin/python -m highiv explain  # refresh the Why this IV column from Yahoo news (~2 min); no rescan
```

Scans resume completed symbols and retry failed requests. A valid response with no usable IV
counts as completed. Quotes downloaded after a session settled (4:30 PM ET) cannot change until the next
session trades, so later runs copy them instead of asking Cboe again: an evening, weekend or next-morning
refresh takes seconds. One Cboe request for SPY tells whether a new session has traded, which also covers
exchange holidays; Montréal Exchange quotes follow the weekday calendar. Likewise, the build keeps leaders
enriched after the same close (refreshing only headlines, borrow terms and the earnings countdown) and
enriches new ones four at a time. To replace quotes from an earlier run on the same day (for example, after
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
4. **Why this IV.** A headline qualifies only when it names the company (or explicitly tags its ticker), describes a concrete event, has a publisher and HTTP(S) source link, and was published within seven days on or before the IV session. Recent financing, merger, guidance, earnings, regulatory and operating events are labeled **possible catalysts**, never proven causes. Price moves, buzzwords, opinion, speculation and law-firm solicitation are rejected. Upcoming earnings carry an estimated, confirmed or unconfirmed date label. Alongside event evidence, structural context identifies commodity cycles, memory and storage cycles, explicit AI/compute infrastructure exposure, issuer-documented AI disruption risk, and crypto activity. These are business sensitivities, not assertions that a theme caused this IV. Rules use the cached company profile; Adobe and Fiverr disruption notes cite reviewed annual reports and require issuer identity and report availability. An incidental AI mention is insufficient. Bitcoin, Ether, crypto mining and trading platforms are distinguished. Missing or truncated profiles can leave coverage gaps. If evidence is insufficient, the cell says **No clear catalyst found**; a failed news lookup is marked separately. Refresh this column with `python -m highiv explain`. The snapshot retains the fetched headlines and lookup time for review. Every report write also recalculates context from cached data; it does not need another news request. The expanded row shows exposure evidence and source links; standing caveats that read the same for every stock live once in “How to read this screen” at the foot of the page, and the full note for a row stays on its Why this IV cell as a tooltip. Roughly three-month stock returns use completed daily closes no later than the IV date, in the listing currency and excluding dividends. Rally/selloff descriptions require a rise of at least 25% followed by a decline of at least 20%; the reverse requires a decline of at least 20% and a rebound of at least 25%. Peer comparisons require at least three other companies sharing the exposure, identical endpoints, at least 50 closes and no gaps longer than seven calendar days. Recent IPOs, stale prices and incomplete windows are excluded. Both market-cap tabs contribute to the enriched peer sample; this is a selected high-IV sample, not an industry index. A peer move is highlighted only when its median is at least +20% or at most −20% and at least two-thirds move in that direction. The subject is excluded and all members and dates are disclosed. No theme rally, hype, causal link or future direction is inferred from co-movement.
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
