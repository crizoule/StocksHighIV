# Implied Vol Leaders

A daily screen with two market-cap tabs: US$1B to below US$100B, and US$100B or more. Each tab independently selects up to 100 stocks with the highest implied volatility among US and Canadian listings, with float and short-interest context for squeeze setups and 52-week context for both IV and price. Each row carries a one-line description of what the company does. List size lives in `TOP_N` and the US$100B boundary in `LARGE_MARKET_CAP_USD` in `highiv/config.py`. Companies at exactly US$100B belong to the upper tab. Both tabs retain the same columns, price charts, conservative catalyst checks and listing/HQ filters. Each filter combination is ranked within its selected cap band; sorting a column reorders that top-100 selection. The overview and IV distribution also use the selected cap band.

Output: `output/dashboard.html` (open in any browser). Each run also saves `data/snapshots/<date>.json`, about 13 MB of headlines, price frames and readings. Only the newest is read again, so older ones are gzipped to roughly a third and the last 30 days are kept; the folder settles at about 145 MB instead of growing by 13 MB every trading day. See **Setup** below before the first run.

To serve the generated dashboard locally:

```bash
.venv/bin/python -m http.server 8931 --bind 127.0.0.1 --directory output
```

Then open http://127.0.0.1:8931/dashboard.html.

Generated market data, snapshots, logs, backups and HTML reports are local runtime files and are not included in Git. Run the screen to create them.

## Easy launch (Windows or Mac)

**Windows users:** [Download the Windows app](https://github.com/crizoule/StocksHighIV/releases/latest/download/StocksHighIV-Windows.zip), extract it, and open **StocksHighIV.exe**. Requires Windows 10/11 x64 and Python 3.11+ (enable Add Python to PATH). The launcher checks for updates automatically and offers an Install button. It preserves your data between app versions. Windows may show a SmartScreen prompt because the executable is not Authenticode-signed.

**Mac users:** [Download the signed and Apple-notarized app](https://github.com/crizoule/StocksHighIV/releases/latest/download/StocksHighIV-macOS.zip), extract it, and open **StocksHighIV.app**. Supports Intel and Apple Silicon on macOS 12+. Install Python 3.11+ from python.org first. The app opens the dashboard in your browser; quit it from the Dock/menu when finished. A normal first-open confirmation may appear. GitHub’s source ZIP contains the development launchers, not the notarized app.

From version 1.1.0, the Mac app checks for updates daily and offers **Check for Updates…** in its app menu (⌘,). After you choose to install, an active data download would be interrupted, so the app asks: install when the download finishes, or stop it and install now — a stopped scan resumes where it left off. The launcher window says an update is waiting while you let the download run. If the app's local server is unreachable there is nothing to interrupt, so the update installs immediately rather than waiting for a reply that never comes. Your watchlist, schedule, and data are preserved. Users on 1.0.x need to download this version once manually. Future updates must be published as signed GitHub Releases; pushing source code alone does not trigger an update. The packaged Windows launcher also supports signed in-app updates from version 1.2.0; source launchers still update manually.

Developer build instructions are in [packaging/macos](packaging/macos/README.md). The source-based steps below also support Windows.

1. Download the GitHub ZIP and **extract the entire folder** (or clone the repository).
2. Install **Python 3.11 or newer** from [python.org](https://www.python.org/downloads/) if needed. On Windows, enable **Add Python to PATH** during installation.
3. Double-click **Start StocksHighIV.bat** on Windows or **Start StocksHighIV.command** on Mac.
4. Your browser opens **the saved dashboard directly** at **http://127.0.0.1:8932/**. On the first launch, when no saved data exists, it shows the setup/download page instead. Dependency setup runs automatically. Use **Refresh data / download progress** above the dashboard whenever you want to update it.
5. Click **Download market data**. The page shows the current stock/provider, completed and total quotes, measured companies per minute, elapsed time, scan ETA, failures, and provider retry/backoff messages. Enrichment shows completed companies without inventing a fixed total or ETA.

A first download usually takes about 50 minutes, depending on provider response times. After that, each new close is downloaded in two passes: about 900 likely leaders first (large caps, TSX listings, your watchlist, and the 500 highest IVs of the last scan), then a preliminary dashboard after roughly 20 minutes, marked as such, while the remaining lower-IV stocks download; the complete dashboard replaces it about half an hour later. Downloads before the next close reuse what is already final and take a minute or two. Downloads default to **Manual**. In the menu (☰, top right of the app bar), under **Download schedule & notifications**, choose **Automatic** for weekdays at **11:00 AM ET**, **4:30 PM ET (after close)**, or a custom Eastern time. Settings persist across restarts. The app must be running and the computer awake; reopening after the scheduled time catches up once that weekday. Weekends are skipped, but exchange holidays are not excluded. Failed scheduled runs are not automatically retried; use Resume. **Open saved dashboard** remains available while refreshing; the final report replaces the old one atomically. **Refresh market data** starts a fresh scan; **Resume unfinished download** reuses completed stocks from today and retries unfinished/failed requests, including after an interrupted refresh. Enrichment restarts when resuming. This is a local application, not a public web server; it binds only to `127.0.0.1`.

Keep the launcher window open. Ctrl+C stops the server and its download process; relaunch and choose Resume to continue. Reopening the launcher while it is already running opens the existing app. A closed browser tab does not stop the download. The previous dashboard stays visible during updates; a completion banner links to the new report. Enable browser notifications from the same menu to receive an additional alert while a dashboard/progress tab is open. Browser permission is required.

macOS may block the downloaded, unsigned `.command` launcher on first use. If you trust your downloaded copy, follow [Apple’s instructions](https://support.apple.com/en-gb/102445): dismiss the warning, then use **System Settings → Privacy & Security → Open Anyway** for this launcher. Changing the landing page does not remove this macOS approval requirement.

If the Mac ZIP extraction drops execute permission, run `chmod +x "Start StocksHighIV.command"` once from the extracted folder. You can also launch directly with `python3 launch.py` (Mac) or `py -3 launch.py` (Windows). If port 8932 is occupied by another application, use `python3 launch.py --port 8933` (or `py -3` on Windows). Failed dependency installation can be retried from the page after fixing the internet connection or Python installation. The `.venv` directory belongs to this machine; do not copy it between Windows and Mac.

The command-line examples below use Mac/Linux paths; on Windows replace `.venv/bin/python` with `.venv\Scripts\python.exe`.

## Tabs and weighted labels

The dashboard has five tabs: **IV scan** (the screen itself), **Sentiment**, **Leverage**, **Commodities** and **Searches** (beta). The dashboard remembers the open tab. Sentiment, Leverage and Searches each carry one label, shown on the tab and at the top of its page: **Bullish**, **Leaning bullish**, **Neutral**, **Mixed**, **Leaning bearish**, **Bearish** or **Insufficient data**, plus **historic high** or **historic low** when an input sits within 2 percentile points of either end of its own history.

Each input maps its reading onto −1 (bearish) to +1 (bullish) and has a base weight. Only fresh inputs count, and their weights are rescaled to 100%. A label needs at least three inputs and half the weight; otherwise it reads Insufficient data. The weighted score sets the band: +0.50 and above Bullish, +0.20 Leaning bullish, −0.20 Leaning bearish, −0.50 and below Bearish. Between −0.20 and +0.20 it reads Mixed when strong inputs (±0.50) pull both ways, and Neutral otherwise. **How this label is weighted**, under each label, lists every input with its reading, score, base weight, the share it actually counts for, and its rule. The labels are computed in the page, so a report saved by an older version shows them too.

| Tab | Inputs and base weights | Direction |
|---|---|---|
| Sentiment | VIX 25% (20 = 0, ±10 points = ∓1) · AAII bull–bear spread 25% (±25 pp = ±1) · COT asset managers' 3-year index 20% · equity put/call 15% (0.70 = 0, ±0.25 = ∓1) · Fear & Greed 15% (50 = 0, ±40 = ±1) | The crowd's mood; Fear & Greed weighs less because it is partly built from VIX and put/call. A mood, not a forecast; extremes are often read contrarian. |
| Leverage | ProShares 3× funds: net flows into bull minus bear funds over 20 sessions 35%, bull funds' share of their assets 25% (ranked within 3 years) · FINRA margin debt change 30% · OFR hedge funds incl. derivatives 4% · Fed Z.1 margin loans ÷ stocks 3% · CFTC leveraged funds' net S&P futures 3% | Each measure's percentile in its own history: more leverage reads bullish. The daily fund data carries 60%, monthly FINRA 30%, and the slow sources 10% as context; without the daily data the tab reads Insufficient data. Retail tends to add to bull funds on dips while a rally holds, and bull funds' share of assets falls when leveraged holders give up. Readings older than their cadence allows (75 days for monthly, 200 for quarterly) are left out. |
| Searches | Six terms, equal weight; each blends the latest week against its baseline (60%, 1.5× = −1) with the latest month's rank since 2004 (40%) | Rising worry is bearish. Quiet searches score at most +0.4, because low attention does not mean optimism, so this tab never reads fully Bullish. |

## Sentiment

The **Sentiment** tab shows dated VIX closes, Cboe equity/total/index put-call volume ratios, the AAII weekly bull–bear spread, CNN Fear & Greed when its public website feed is accessible, and weekly futures positioning from the CFTC's Commitments of Traders report. Readings retain observation and retrieval dates. Sources are cached for six hours, and a reading of the latest close is kept until the next session opens (news and social posts excepted); failed requests retain the last good observation, explicitly marked as cached or stale. Stale inputs are excluded (4 calendar days for daily Cboe data, 10 for AAII, 2 for CNN, 14 for COT). AAII and CNN can block automated requests; the app does not bypass those restrictions. For AAII, you enter each week by hand (below). For CNN, a separately labelled replica (below) stands in when CNN has no fresh reading. The panel needs at least three fresh readings to describe an overall tilt and explains overlapping CNN inputs.

**Commitments of Traders (COT).** The CFTC publishes futures positions by trader group every Friday at 3:30 PM ET, as of the previous Tuesday, through its free [public reporting API](https://publicreporting.cftc.gov/) (Traders in Financial Futures, futures only). One request fetches every week since June 2006. The card shows asset managers' (pension funds, mutual funds, insurers) net E-mini S&P 500 position as a share of non-spreading open interest, with its three-year COT index (0 = least long, 100 = most long; ≥80 and ≤20 mark crowded or light positioning). Spread positions hold a long and a short in the same market, take no side and are excluded, the convention published COT charts use; on the latest week that lifts asset managers from +37.0% of all open interest to +48.4%. Dealers, the intermediaries on the other side, are drawn as a second line on the chart (−37.6%), and leveraged funds and VIX futures are listed on the card for reference. Asset managers' net position has moved with the S&P 500 week to week (+0.35 over 10 years), while leveraged funds' has moved against it (−0.54), because much of it hedges stock holdings or arbitrages futures against the cash index; it is therefore not used as the headline reading. COT is positioning, not a survey of opinion.

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
- News (20%, optional): Alpha Vantage ticker sentiment, relevance weighted and mapped from −1…+1 to 0…100. Requires at least three distinct article URLs from two publishers in the past seven days, exact US ticker matches, and relevance ≥0.2. Each refresh takes the shared latest-news feed in one request, then spends the rest of Alpha Vantage's free daily allowance (25 requests, about five a minute) on individual leaders, split evenly between the two cap bands and spaced out over at most two minutes; whatever is left goes to the next refresh. Tickers already carrying an article or two are asked about first, since one request can lift them over the three-article threshold, while a company with no coverage rarely has any to find. Articles are kept for seven days in `data/sentiment/news-feed.json`, so coverage of smaller names accumulates rather than being discarded each refresh. It is still not an exhaustive news search: the shared feed favours large caps, and many small caps get no national coverage at all in a week.
- Social (10%, optional): Stocktwits normalized 24-hour community sentiment, alongside message activity. US listings only; retrieval time is shown because the endpoint does not supply an observation timestamp. Excluded after one calendar day.

Missing components are omitted and available weights are rescaled; they are never assigned a neutral score. Ranking requires both fresh stock and sector prices with matching end dates. Scores below 40 are Negative, 40–60 Mixed, and above 60 Positive. When opinion feeds are missing, the column explicitly says **Price only**. These are screening heuristics, not calibrated probabilities, recommendations or evidence of a catalyst. Open saved dashboards also check age before displaying scores.

Optional feeds require your own authorized credentials: `ALPHAVANTAGE_API_KEY`, and/or `STOCKTWITS_USERNAME` plus `STOCKTWITS_PASSWORD` (Firestream-authorized account). Set them in the environment of the process launching the app, or, for the packaged app — which is opened from Finder or the Start menu and so inherits no shell — write them one per line as `NAME=value` in `data/credentials.env` beside the saved market data (`~/Library/Application Support/StocksHighIV/data` on Mac, the app folder on Windows). Only those three names are read, values never reach the log, and the file belongs to you alone: keep it out of version control. They are never embedded in the dashboard. Provider quotas and licensing apply; a website subscription does not necessarily include API access. Without credentials, momentum still works, the score says so with a lower coverage figure, and the card for a feed that is not connected at all is left out of the expanded row rather than repeating the same notice on every stock. See [Alpha Vantage](https://www.alphavantage.co/documentation/#news-sentiment) and [Stocktwits Firestream](https://firestream.stocktwits.com/documentation/sentiment-detail).

Sentiment is collected automatically during each report build. To refresh it on the saved screen without rescanning IV (while no other build is running):

```sh
python -m highiv sentiment
```

This preserves the underlying market-data generation timestamp. It uses saved stock prices and fetches benchmarks; stale or mismatched stock dates require a normal data refresh before ranking resumes.

## Macro search concerns (beta)

This tab is marked **Beta**: Google Trends samples its data, often refuses automated requests, and can revise history between downloads, so readings are frequently missing or shift.


The **Searches** tab, a separate US Google Trends panel, shows search attention for **recession, layoffs, inflation, bank failure, stock market crash, and war**. It never changes Fear & Greed, macro sentiment agreement, or stock sentiment scores. The **Recent · daily** view compares its latest seven complete days with the preceding 56 days in the same three-month download. At least 1.5× baseline is “Elevated”; at most 0.75× is “Below baseline”. The panel also shows change from the prior week and a chart of daily interest. These descriptive thresholds are not calibrated trading signals: searches measure attention, not opinion, and quiet searches do not imply optimism.

`trendspyg==1.8.0` is pinned because a small adapter validates Google's raw time-series response before the library can turn malformed values into zero. **Google Chrome must be installed**; Selenium downloads its matching driver on first use. The collector uses an isolated browser profile and stores only its own Google session cookies under `data/macro_search/`. It does not access your signed-in Chrome profile.

The **2004–present · monthly** view downloads each term’s full history separately. It ranks the latest complete month against earlier complete months using the percentage below it plus half of ties; the 80th percentile or above is elevated. The current month is excluded even if Google omits its partial flag. Missing months and unexpected non-monthly resolution are rejected, not interpolated. All queries and provider links explicitly use **USA (`geo=US`)**. “War” is a word search and can include entertainment or other meanings.

A completed dashboard build or sentiment refresh attempts each term/view at most once in 24 hours, with 20 seconds between requests and a four-minute total collection budget. Historical downloads are reused for seven days, with a new download due at a month boundary. Recent downloads are prioritized; unfinished historical requests resume on the next refresh. Preliminary builds use saved data. Collection runs only while the app is refreshing; it is not a separate background schedule. A rate limit, browser startup failure, or timeout pauses collection for 24 hours. Each request has a 75-second process timeout, including driver setup. Chrome failures do not prevent the rest of the dashboard from being written.

Failed fetches preserve the last valid result, including its original observation and retrieval dates. Recent readings older than four days are visibly stale and excluded from the elevated count. Historical readings are stale after 14 days since retrieval or when they omit the latest completed month. Sparse series are marked “Limited data”; malformed data, missing daily observations, and partial periods cannot silently enter the comparison. Each series and timeframe has its own relative scale, so raw 0–100 values are never joined or compared across terms or views. Successful daily downloads are retained for 90 days in `data/macro_search/history/` to preserve what was observed at the time.

To refresh only this panel on the saved dashboard, without an IV scan or refreshing other sentiment sources:

```sh
.venv/bin/python -m highiv macro-search
```

## Market leverage

The **Leverage** tab shows how much investors are borrowing, from six free public sources. Each card shows the period its data covers, when that release came out, and when the next one is due. A due date marked **publisher's schedule** comes from the source itself. One marked **~ … estimated** follows the source's usual rhythm and can slip; once it passes without new data, the card says the release is due. The daily ETF card has no release calendar. Each reading is ranked against its own history, and the 80th percentile or above is marked elevated. It is refreshed with sentiment, including by `python -m highiv sentiment`.

| Card | Measure | Source · cadence | Next release |
|---|---|---|---|
| Margin debt | Customer margin debit balances; the chart shows the 12-month change, since 1998 | [FINRA margin statistics](https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics) · monthly | One month after the file's own date (estimated) |
| Margin loans | Broker-dealer margin loans ÷ market value of US stocks, since 1945 | Fed [Z.1 Financial Accounts](https://www.federalreserve.gov/releases/z1/) via FRED · quarterly | The Fed's announced date |
| Hedge fund leverage | Gross assets ÷ net assets, and gross notional exposure ÷ net assets, since 2013; the Fed's dealer survey on hedge-fund leverage | [OFR Hedge Fund Monitor](https://www.financialresearch.gov/hedge-fund-monitor/) (SEC Form PF) · quarterly | The same delay after quarter-end as the last release (estimated) |
| Leveraged funds | Hedge funds' net E-mini S&P 500 futures, % of open interest | CFTC Commitments of Traders (already collected for the COT card) · weekly | Friday 3:30 PM ET |
| Leveraged ETFs | Net money into 3× bull minus bear funds over 20 sessions, as a % of their assets, and bull funds' share of those assets, since 2010; Yahoo trading volume as context | [ProShares daily fund files](https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq) (TQQQ/SQQQ, UPRO/SPXU, UDOW/SDOW, URTY/SRTY) · daily | — |
| Fed Financial Stability Report | Twice-yearly review; its section 3 covers leverage in the financial sector | [Federal Reserve](https://www.federalreserve.gov/publications/financial-stability-report.htm) · spring and fall | Six months on (estimated) |

The chart below the cards works like the sentiment chart: the S&P 500 above, one leverage series below, the same ranges, log switch and correlation table. Monthly and quarterly series are drawn as connected points. A gap is only a break longer than the series' own step. The ETF card reads ProShares' daily files, which give each fund's NAV, shares outstanding and assets since launch. A fund's flow is its change in assets beyond its own NAV return, so gains, losses and reverse splits are not counted as money moving. Direxion's funds (SPXL, SOXL, TNA, TECL and their bear twins) are not included: Direxion publishes only today's shares outstanding, and its pages block automated access. It is a daily read of leveraged retail demand, not a measure of borrowing: nobody publishes retail leverage daily. FINRA refuses browser-like request headers, so these sources are fetched with a plain `StocksHighIV` user agent. The Z.1 ratio falls as the market grows faster than margin loans, so it can read low while FINRA's dollar total sets records.

## Commodities

The **Commodities** tab charts each main commodity futures market's price above its weekly positioning from the CFTC's [Disaggregated Commitments of Traders report](https://publicreporting.cftc.gov/) (futures only, since June 2006). Markets are grouped into Energy, Grains, Oilseeds, Softs, Metals, Livestock, Dairy and Lumber; categories and the markets within them are ordered by open interest, the report's measure of how much is held.

| Category | Markets (CFTC contract · Yahoo continuous price) |
|---|---|
| Energy | WTI crude oil, natural gas (Henry Hub), RBOB gasoline, ULSD heating oil, Brent crude oil (NYMEX) |
| Metals | Gold, silver, copper, steel hot-rolled coil (COMEX); platinum, palladium (NYMEX) |
| Grains | Corn, wheat (soft red winter and hard red winter), rough rice (CBOT) |
| Oilseeds | Soybeans, soybean meal, soybean oil (CBOT) |
| Softs | Sugar No. 11, cotton No. 2, cocoa, coffee C, orange juice (ICE US) |
| Livestock | Lean hogs, live cattle, feeder cattle (CME) |
| Dairy | Class III milk, cheese, butter, nonfat dry milk (CME) |
| Lumber | Lumber (CME, since 2023) |

Each card shows the latest price with its 1-month and 1-year change, managed money's net position (hedge funds and commodity trading advisers, long minus short as a share of open interest), its three-year COT index (0 = least long, 100 = most long; 80 or above reads "crowded long", 20 or below "crowded short"), producers' and merchants' net position, and a small chart of price above both positions. Selecting a card puts that market on the full chart at the top, with the same ranges, log scale and correlation table as the other tabs. Positions are as of Tuesday and published Friday at 3:30 PM ET; the tab gives the report date, its release and the next one.

Every other commodity contract in the report (about 240: regional natural-gas basis swaps and indices, electricity, emissions, propane, canola and others without a free continuous price) is listed below the charts by category, with open interest and managed money's net position. Prices are Yahoo's continuous front-month futures, weekly closes plus the latest close; a roll between contract months can show as a jump.

## Company logos

Displayed companies use 48-pixel WebP logos (shown at 24 pixels), cached in `data/logos` for 90 days and embedded in saved dashboards for offline use. Images come from Financial Modeling Prep using the full listing symbol. Missing logos fall back to ticker initials and are retried after a day. Source downloads are capped at 256 KB and encoded icons at 12 KB; no API key is required for this image endpoint. Logo availability depends on the provider.

## Watchlist

Use the star beside a ticker, or **★ Watchlist** in the menu (☰, top right of the app bar), to save up to 100 favorites. The Watchlist tab shows their available data independently of the top-100 rankings, cap bands, and location filters. Favorites are stored in `data/watchlist.json` (shared across Mac app versions). US symbols and Canadian Yahoo symbols such as `SHOP.TO` are supported. New additions enter the next Refresh or Resume; additions during a scan may require the next run. Watched stocks bypass cap and industry exclusions for the watchlist only. Missing IV or failed enrichment is indicated without inventing values. Removing a favorite stops forced inclusion in future scans. Changes require the local app; exported HTML shows the watchlist saved in its snapshot.

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
refresh takes seconds. One Cboe request for SPY says which session Cboe is serving, whatever the clock
reads — that covers exchange holidays, and a feed still serving yesterday during today's session, which
happens: on 2026-09-23 Cboe's delayed quotes stayed on the previous close from the open until at least
1:30 PM ET, so a refresh would have re-downloaded 2,248 identical quotes. Montréal Exchange quotes have no
reliable session date, so they follow the weekday calendar and are reused only while the market is closed. Likewise, the build keeps leaders
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
