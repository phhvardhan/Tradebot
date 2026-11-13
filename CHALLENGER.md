# Forward test: baseline vs the 20-day-low exit (frozen)

Started when `data/stocks-e.sqlite` was first created (the date is stored in its saved protocol and shown on the Activity page).
Nothing below changes during the test. If it must change, start a new test; do not edit this one.

## What is compared
| | Baseline (live, unchanged) | Challenger |
|---|---|---|
| Starting point | the real paper portfolio | an exact clone of it at the start: same positions, cash, entry prices |
| Watchlist, quotes, entry rule, sizing, costs | identical: one Yahoo fetch per check feeds both, entries use the same 20-day breakout, price check, `max_new`, $500 position, 0.05% slippage |
| Protective stop | −10%, checked every minute | −10%, checked every minute (unchanged) |
| Profit target | +25% | **none** |
| Channel exit | none | **sell when the price, in the last 10 minutes of the session, is under the lowest close of the previous 20 completed sessions** |
| Priority | stop, then target | stop, then channel exit |

## Timing (the part that has to match the research)
- The channel is built only from completed daily closes; today's session is never in it.
- The research decided once a day at the close. The live stand-in is the last 10 minutes of the session (`CLOSING_WINDOW` in `signaldesk/swing.py`),
  using the live price as the close. Early-close days move the window with the close. An intraday dip earlier in the day does not sell.
- Known difference from the research: in the research the stop was also checked only at the close; live, both portfolios check it every minute.

## Records
- `data/decisions-stocks.jsonl` (baseline) and `data/decisions-stocks-e.jsonl` (challenger): one line per BUY, SELL, signal that did not become a trade,
  one end-of-day HOLD per open position, every data problem (NO_DATA) and every failed check (TICK_ERROR). Each has the time, the price seen, the channel low, the action and the reason.
- Every replay and the code hashes are in `data/experiments.jsonl`; the exact prices are in `data/history/`.

## How to judge it
Measures (Activity page, both portfolios, same period): return, worst drop, capital deployed (average of invested/equity at each check),
days with any position open, average open positions, largest position's share of invested capital, entries and exits (turnover), winning exits, realized P&L.
- "Capital deployed" and "days with any position open" are different things. The research's "66-68% invested" was capital deployed.
- A few months shows whether the challenger runs as designed. With only a few dozen closed trades it cannot say which rule earns more; keep going until there are
  enough exits on both sides (the page says when there are too few).
- The fresh 20-stock list used in the research shares the same market period and helped choose this challenger, so it is a robustness check, not unseen data.
- If the baseline's rules are edited during the test, the page says the comparison is no longer like for like.
