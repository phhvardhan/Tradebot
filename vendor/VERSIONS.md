# Bundled skills

Copies of the four trader skills the engine imports, so the project runs without `~/.claude/skills`.
They are unmodified copies; nothing in this folder is edited by hand.

| file | copied from | source modified | sha256 (first 16) | status |
|---|---|---|---|---|
| `trader-alerts/alerts.py` | `/Users/harshapeela/.claude/skills/trader-alerts/alerts.py` | 2026-10-22 | `2db22d8e9b27ff17` | same as source today |
| `trader-news/news.py` | `/Users/harshapeela/.claude/skills/trader-news/news.py` | 2026-10-22 | `bf9e12a9c5962de3` | same as source today |
| `trader-pelosi/ptr.py` | `/Users/harshapeela/.claude/skills/trader-pelosi/ptr.py` | 2026-10-22 | `b325c8b56a9580f8` | same as source today |
| `trader-scanner/scan.py` | `/Users/harshapeela/.claude/skills/trader-scanner/scan.py` | 2026-10-22 | `2cc0d99a4f21d2b8` | same as source today |

## Installing / updating
1. Python 3.9+ (the project uses `str.removeprefix`; tested here on 3.14 only), standard library only. Congress filings also need `pdftotext` (`brew install poppler`).
2. To update a skill, copy its `.py` over the file here, then rerun `python3 -m unittest`, and refresh this table
   (`shasum -a 256 vendor/*/*.py`).
3. To use a different folder, set `TRADER_SKILLS=/path/to/skills`.
4. Each replay in `data/experiments.jsonl` records the hashes of `signaldesk/swing.py` (`stocks.py` before 2026-11-12), `scan.py` and `alerts.py` it ran with,
   and the prices it used are kept in `data/history/<hash>.json.gz`.
