# Signal Desk

A personal decision desk plus paper-trading agents for US stocks, crypto, mutual funds and day trades. The desk knows what you own (one real account, imported
read-only), applies the rules you chose and says what needs attention and why; you place the orders. The agents trade simulated fills only: no broker, no real orders.

    python3 run.py               # http://127.0.0.1:8765  opens Action needed (agents resume on start; --paused to hold them)
    python3 -m unittest          # engines, server, startup drills, watchdog
    node tests/test_ui.mjs       # dashboard smoke test

Or double-click `Start Signal Desk.command`.

## Layout
    run.py                 the only entry point: python3 run.py
    signaldesk/            the application package
      core.py              decimals, project paths, the trader-skill loader, the clock, the Runner every engine builds on
      quotes.py            price feeds: Yahoo daily and 5-minute charts, Coinbase candles, NYSE calendar, S&P 500 benchmark
      swing.py             the 20-day-breakout engine (stocks and crypto), the replay, the frozen challenger
      desk.py              the decision desk: the read-only account import (CSV) and the Action-needed cards built from the swing rules
      day.py               the opening-range-breakout day trader
      funds.py             paper SIPs into Indian and US mutual funds
      congress.py news.py  House filings and headlines used as entry filter and notes
      account.py           optional read-only exchange balances
      server.py            the local HTTP server: one page and one /api/<market>/ namespace per market
      app.py               command line, the four markets on one port, main()
      notify.py watchdog.py supervisor.py   alerts, the independent watchdog, the launchd installer
    static/                the pages: desk.html/js, stocks.html/js (also serves /crypto), day.html/js, funds.html/js, style.css, logos/
    tests/                 python3 -m unittest discovers these; test_ui.mjs is the node smoke test
    research/              backtests and experiments, run as modules: python3 -m research.exit_backtest
    vendor/                copies of the four trader skills the engines use (set TRADER_SKILLS to use another folder)
    data/                  databases, daily backups, decision records, heartbeats, replay history (never in version control)
    legacy/btc/            the earlier BTC 5-minute bot, still runnable: python3 -m legacy.btc.bot --demo
    audit/                 the review of the day-trader skill the engines are built on

## Action needed (`/`, the home page)
- `python3 -m signaldesk.desk import FILE.csv` reads a Robinhood "account activity" CSV: Buy/Sell rows become fills, netted into holdings with the broker's average cost.
  Cash is not in that file, so sizes then use the per-position cap alone and the page says so. Holdings, cash and fills live in `data/holdings.json`, apart from every paper portfolio;
  the page shows the sync time (when the CSV was downloaded, not imported) and flags a sync older than a day, a holding without a price, and an export that does not start at the account opening.
  Held symbols are quoted at every agent check from startup. In session, a card on a quote the feed stopped updating fails instead of passing on an old price.
- Tag a lot: `python3 -m signaldesk.desk tag AAPL swing --entry 100 --stop 90 --target 125` (or `tag AAPL invest`). An investment and a swing trade can share a ticker; swing
  exits (stop, target) apply only to lots tagged swing. Investments are never sold by a swing rule. A tag survives re-imports while the holding stays open; selling out and buying again drops it.
- Each card shows the trigger, the quote and its time, the proposed size, the planned stop and target, the estimated loss at the stop (an estimate: a stop can fill lower),
  and every check it passed or failed: open swing trade on it, new buys today, open positions, sector, price back under the level, the replay gate, the Congress filter, cash, market open.
  The rules are the stock agent's (Edit rules on `/stocks`); the breakout test is the same one the paper agent runs, on the closes it saved. Nothing is placed from the desk.

## Markets
- `/stocks` and `/crypto` (paper): buy when yesterday's close is above the 20 closes before it **and** today's price is still above that level
  (and not more than 5% over the close). Sell at the stop (-10%), the optional trailing stop or break-even stop (off) or the target (+25%).
  $500 per position (less cash buys a smaller position; only an empty account skips), at most 3 new buys a day, no new buys once down $400 on the day.
  Defaults live in `DEFAULT_RULES` (`signaldesk/swing.py`) and are used only for a fresh database; rules saved from the dashboard win, and every change is logged on the Activity page.
- `/day`: automatic paper day trades (`signaldesk/day.py`). The first 15 minutes of the session set a range; the first 5-minute close above it buys (until 11:30),
  the stop is the middle of the range, the target is 2× the risk, the position is sized so the loss at the stop is at most $50 (and at most $1000 a position),
  the stop moves to the entry once up 1R, and everything is sold by 15:55. One trade per symbol a day, 3 a day, none once down $100 on the day. A breakout bar older
  than ten minutes is reported as missed, never chased. Long only, no replay (Yahoo keeps 60 days of 5-minute bars), checks once a minute.
- `/funds`: paper SIPs into Indian (AMFI NAVs via mfapi.in, in rupees) and US (Yahoo, in dollars) mutual funds, following the MoneyPurse rules: monthly buys at the
  first NAV of the month, optional yearly step-up, a horizon check per category, a pause on overheated thematic funds, XIRR per portfolio.

## Safety nets
- A 5-year replay of the rule gates entries (stocks and crypto). It runs on its own thread and is retried hourly if it fails.
- The dashboard warns when an agent is paused with open positions or has not checked for 5 minutes.
- `data/backups/` keeps a daily copy of each database (14 days).

## Keeping it running and watched (macOS)
`python3 -m signaldesk.supervisor install` (run it yourself; `--dry-run` shows what it does) installs two launchd jobs: the server (started at login, restarted whenever it exits,
kept awake with `caffeinate`) and the watchdog (every minute). The server rewrites `data/heartbeat-*.json` after every check; the watchdog, a separate process, alerts when
checks stop, when the data feed has failed for 10 minutes during the session, when an agent is paused with open positions, or when the NYSE calendar is missing.
Alerts go to `data/alerts.log`, a macOS notification, and a phone push if you set `NTFY_TOPIC` (`python3 -m signaldesk.supervisor install --ntfy-topic <long random string>`; the topic acts as a password).
Try `python3 -m signaldesk.watchdog --test` to see an alert arrive. Closing the lid or sleeping the Mac still stops everything; the watchdog cannot run then either, so the alert arrives when it wakes.

## Forward test
`CHALLENGER.md` freezes a second paper portfolio (no +25% target, sells under the 20-day closing low near the close) that runs on the same live quotes as the baseline. The Activity page compares them.

## Limits you should know
- A passing replay check means only "it did not lose money in the replay". It is a safety gate, not validation. The dashboard also shows what holding the same stocks would have made, the worst drop, and first-half vs second-half results; read those before trusting a number.
- The replay decides once a day at the close; the live agent checks every minute. Intraday stop crossings and recoveries can make live results differ.
- Every replay is appended to `data/experiments.jsonl` (rules, symbols, period, assumptions, result, code hashes) so numbers can be reproduced and compared.
- The watchlist was picked with hindsight, so every replay is biased upward. Buy and hold of the same stocks beat every rule setting tested.
- Stops are checked once a minute on free Yahoo quotes; gaps fill past the stop.
- The NYSE calendar covers 2026-2027; after that the agents refuse to trade. `pdftotext` (poppler) is needed for Congress filings.
