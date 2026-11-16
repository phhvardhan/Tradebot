# AI brain: trade analyst + strategy researcher (2026-11-16)

## Goal
Give Signal Desk an LLM "second brain" that reasons from real inputs instead of only fixed rules, keeps reworking the
strategy without the owner present, and leaves a measurable record of whether it helped.

## Decisions (owner)
- Provider: OpenRouter (`OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, default `anthropic/claude-opus-5.5`), plain HTTPS via urllib, no new packages.
- Autonomy: **fully free**. The researcher adopts a new strategy itself, no forward-test wait.
- Scope of change: **building blocks** only. The AI composes a recipe from parts the engine already runs; it never writes code.

## Fixed, outside the AI's reach
Paper only (Alpaca paper endpoint), amount per trade, daily loss limit, new buys a day, the replay gate switch.
Every AI output is validated by the same checks as the dashboard's rule form before it is used.

## Recipe (the strategy, as data)
The stock agent's rules plus one new block:
- `entry`: one or more scanner setups: `breakout_20d`, `bull_flag`, `reclaim_ma20`, `oversold`, `oversold_bounce`.
- exits: `stop`, `target`, `trail`, `breakeven`, `exit_low`; filters: `congress_days`, `max_open`, `max_sector`; universe: `symbols`.
The live engine and the 5-year replay run the same code, so a recipe's replay is what it will trade. Default recipe = today's rules (results unchanged).

## Trade analyst
Before an Alpaca buy card can pass, the analyst reads the setup, recent closes, Yahoo headlines and House filings for the
symbol and returns `{verdict approve|veto, confidence, reasons, risks, sources}` (JSON schema). One review per symbol per day.
No key: no analyst, rules only (as before). Key set but call fails: the card fails, retried at the next 5-minute check.
Live only (no look-ahead-free way to replay it), which makes the simulated /stocks portfolio the AI-free control.

## Strategy researcher
Weekly (when the last run is 7+ days old) or from the "Run research now" button. A tool loop:
`run_replay(recipe)` (max 12 per run, 3 s each) and `finish(decision adopt|keep, recipe, memo)`.
Context: current recipe and its replay (with buy-and-hold, worst drop, first/second half), the block catalog, recent trades,
past runs and their outcomes, the analyst scorecard. Adoption goes through `set_rules`; an invalid recipe is returned to the model to fix.
Every run is logged; the last adoption can be rolled back with one click.

## Record and scorecard (`data/ai-log.jsonl`, `/ai` page)
Every review and run: inputs summary, output, model, tokens, cost. Scorecard:
- vetoed vs approved buys: average move since the review;
- each adopted recipe: replay before vs after, simulated portfolio change since adoption;
- total and 30-day AI spend.

## Not in this version
Day-trade strategy stays rule-based (opening-range breakout). SEC 8-K reading (EDGAR needs a contact email in the User-Agent).
