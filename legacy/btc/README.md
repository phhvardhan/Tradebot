# Signal Desk

A local personal-trading prototype that makes rule-based decisions on BTC/INR and records simulated trades. Optional read-only account balances; no deposits, transfers, or live order execution.

## Run

Requires Python 3.10 or newer. No third-party packages needed.

Open a terminal in this folder and run:

```sh
python3 app.py --demo
```

Open http://127.0.0.1:8765 in your browser. Click **Run once** for one decision or **Start agent** for continuous checks. On macOS, you can also double-click `Start Demo.command`.

Demo mode generates synthetic prices, advances one synthetic five-minute interval every check, and polls every five seconds. Synthetic market timestamps can be in the future; journal timestamps reflect actual execution time. The demo exercises buys and sells; it is not a historical backtest or performance evidence.

To request public WazirX data instead:

```sh
python3 app.py
```

Public-data mode polls every 60 seconds while running. It never falls back to synthetic data. Failed or stale data blocks the check and displays an error. It needs no API key. Stop the current server with Ctrl+C before switching modes, or select another `--port`.

## Optional real-account viewer

This is **account visibility, not live trading**. Create a dedicated **read-only** WazirX API key using the official account settings. Keep spot/futures trading, transfer, and withdrawal permissions disabled. Never paste the key or secret into chat.

Stop the running server, then use an interactive Terminal:

```sh
python3 app.py --account-readonly
```

Both the API key and secret are entered through hidden prompts. They remain in process memory; they are not written to the database, browser, logs, or configuration files. Restarting requires entering them again. Non-interactive or echoing input is refused. The connector is disabled unless this option is supplied.

Open the dashboard and click **Refresh balances** in **Real account · Read-only**. Reads are on demand, throttled to one attempt per 15 seconds. Snapshots are marked stale after 60 seconds or any refresh failure. Available, locked, and reserved-fee amounts are shown separately without estimating total fiat value. A failed refresh keeps the prior snapshot visibly stale, not zeroed.

`--demo --account-readonly` is also supported: the top panel remains synthetic paper trading, while the separate account panel reads actual balances. Real holdings never fund or alter the simulation.

The connector can call only signed GET requests to `/sapi/v1/account` and `/sapi/v1/funds` at `https://api.wazirx.com`. Redirects are refused. Account-level `canTrade` and `canWithdraw` fields do **not** prove API-key permissions, so the app makes no claim that it verified those permissions. Choose a read-only key at the exchange.

This local Python process is not an isolated execution service: another process running as your OS user may be able to inspect it. No LLM runs in this app. An agent-resistant credential boundary from the audit is not implemented. Do not give this process a trading-enabled key.

## Four paper-trading capabilities

1. BTC/INR chart and transparent trend decisions.
2. Autonomous paper entries and exits while the worker runs.
3. Persistent balance, position, and trade journal with CSV export.
4. Start, pause, and single-check controls with visible errors.

The app starts paused after every restart. Pause stops subsequent checks, including exits; an in-progress check may finish. Closing the browser does not stop the Python process. Closing the terminal, sleeping the computer, or stopping Python stops the worker. This version has no cloud deployment or background OS service.

## Strategy and accounting

- Uses completed five-minute candles; fast average = 5 closes, slow average = 20 closes.
- Enters when fast > slow and no position is open. This is a trend condition, not a crossover-only strategy. At most one entry per candle.
- Allocates at most 20% of current equity, inclusive of modeled entry fees.
- Exits when the observed bid is 2% below modeled entry, 4% above it, or fast < slow.
- Blocks new entries when equity is 5% or more below initial capital, or the spread exceeds 0.5%. The capital threshold is account-wide, not a daily limit. Existing positions still receive exit checks.
- Simulates buys at ask plus slippage and sells at bid minus slippage. Gaps can exceed stop thresholds. There are no exchange-side protective orders.
- Equity = paper cash + quantity marked at bid. Realized P&L includes both entry and exit fees. Equity excludes hypothetical exit fees until an exit occurs.
- Default capital: **4,000 simulated INR**, an illustrative budget, not a conversion of USD 50.
- Default fee: **0.4% per side**; default slippage: **0.1% per side**. These are modeling assumptions, not verified WazirX fee quotes.

Change assumptions with a fresh database:

```sh
python3 app.py --demo --capital 5000 --fee 0.004 --slippage 0.001 --db data/experiment.sqlite
```

Database configuration is checked on startup to prevent silently changing an existing experiment. Demo and public-data modes have separate databases by default. Back up the `data` directory to preserve experiments. Use a new `--db` path for a fresh experiment.

## Architecture

Python standard-library HTTP server and background worker, SQLite persistence, vanilla JavaScript dashboard. One application was chosen for minimal setup; a React/FastAPI stack would provide more UI/framework tooling but add dependencies and deployment work that this personal prototype doesn't yet need.

Entities: persisted account/position state, observed candles, and executed simulated trades. Routes: `GET /api/state`, `GET /api/export`, and `POST /api/start`, `/api/pause`, `/api/step`. The server binds only to 127.0.0.1 and checks Host and a per-process action token. There is no user login; this is for a trusted local machine, not public hosting. Do not expose it through port forwarding or a reverse proxy.

The worker does not call an LLM. Decisions are reproducible strategy rules with explanations. This implementation does not install or incorporate third-party day-trader-skill code. Reusing that repository would require a separate code review and crypto-specific adaptation.

Market contract: https://docs.wazirx.com/ — public `GET /sapi/v1/klines` and `GET /sapi/v1/ticker/24hr`. Only completed candles are used; timestamps, positive finite prices, chronological continuity, and quote freshness are checked. No live trading endpoints exist in this application. The optional account viewer adds `GET /api/account` and `POST /api/account/refresh`, both requiring the per-process action token; account data is held in memory only.

## Validation

```sh
python3 -m unittest -v
python3 -m py_compile app.py
```

25 tests cover read-only endpoint enforcement, request signing, redirect refusal, error redaction, precise balance parsing, refresh throttling and stale account snapshots, plus round-trip accounting, cost assumptions, duplicate-entry prevention, gap exits, drawdown/spread guards, persistence, offline failure, transaction rollback, synthetic buy/sell behavior, incomplete candles, stale data, gaps, and invalid prices.

Build-environment results: all 25 tests passed. JavaScript syntax validation also passed. The public API request could not resolve `api.wazirx.com`. Local server binding was denied by the sandbox, so HTTP/browser end-to-end behavior and the live WazirX response contract remain unverified. Run the commands above locally to finish that validation. A successful local check should show the dashboard, populate the chart after Run once, update decisions when started, stop subsequent checks when paused, and download a CSV.

## Limits and next steps

This is an operational prototype, not evidence of a profitable strategy. It does not model depth, partial fills, queue position, exact exchange quantity/minimum-order filters, taxes or TDS. It has no historical backtesting module, live execution integration, news analysis, notifications, or production execution service. A marked portfolio value may be stale during an outage; the error and last market timestamp are shown.

Next: verify public data on your machine, add verified exchange filters/costs, then evaluate the strategy on held-out historical periods and extended paper runs. Any live execution is separate work; this app cannot be switched into live mode.


## Apply this update to your external Tradebot folder

The updated source is in the Codex output folder. This session cannot write to `/Volumes/Volume/Projects/Tradebot`. Stop the running server first (Ctrl+C), then run these commands in your own Terminal. They update code without copying or replacing your `data` directory:

```sh
source_dir="/Users/harshapeela/Documents/Codex/2026-10-22/https-x-com-argona0x-status-2091898304900571501/outputs/wazirx-paper"
target_dir="/Volumes/Volume/Projects/Tradebot"
cp "$source_dir/app.py" "$source_dir/account.py" "$source_dir/test_app.py" "$source_dir/test_account.py" "$source_dir/README.md" "$target_dir/"
cp "$source_dir/static/index.html" "$source_dir/static/app.js" "$target_dir/static/"
cd "$target_dir"
python3 -m unittest -v
python3 app.py --account-readonly
```

Then open http://127.0.0.1:8765. No account credentials have been used and no authenticated exchange request has been made during development; an actual connection and browser end-to-end test remain outstanding.
