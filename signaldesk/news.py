"""Headline check for a symbol about to be bought: what Yahoo Finance said in the last 2 days, as one short note.
Information only. It never blocks a trade. Reuses the installed trader-news skill's feed parser and topic tags."""
import importlib.util
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .core import SKILLS
SKILL = SKILLS / 'trader-news' / 'news.py'
TTL = 1800  # seconds a symbol's note is reused
_cache = {}

def _news():
    spec = importlib.util.spec_from_file_location('skill_news', SKILL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def headlines(sym, hours=48):
    """Yahoo headlines for `sym` from the last `hours`, newest first: [{time, title, summary, tags}]. None when the feed is unavailable. Cached for TTL."""
    hit = _cache.get((sym, hours))
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    try:
        news = _news()
        req = urllib.request.Request(news.YAHOO % sym, headers={'User-Agent': news.BROWSER_UA})
        with urllib.request.urlopen(req, timeout=6) as resp:
            items = news.parse_rss(resp.read(), sym)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        recent = [i for i in items if i['time'] and i['time'].astimezone(timezone.utc) >= cutoff] if items and hasattr(items[0]['time'], 'astimezone') else []
        out = [dict(time=i['time'].astimezone(timezone.utc).isoformat(), title=i['title'], summary=(i.get('summary') or '')[:300], tags=i['tags'])
               for i in sorted(news.tag(recent, {sym: ''}), key=lambda i: i['time'], reverse=True)]
    except Exception:
        _cache[(sym, hours)] = (time.time() - TTL + 300, None)  # remember the miss for 5 minutes so a dead feed is not retried on every check
        return None
    _cache[(sym, hours)] = (time.time(), out)
    return out

def note(sym, hours=48):
    """'News: 3 headlines in 48h (earnings, analyst).' or 'News: no headlines in 48h.' or '' when the feed is unavailable."""
    recent = headlines(sym, hours)
    if recent is None:
        return ''  # no feed, no note; a missing note is not a reason to skip a trade
    tags = sorted({t for i in recent for t in i['tags']})
    return f'News: {len(recent)} headline{"s" * (len(recent) != 1)} in {hours}h' + (f' ({", ".join(tags)}).' if tags else '.') if recent else f'News: no headlines in {hours}h.'
