"""Audience peak hours and daily slot planning.

People only ever see times in their own timezone; everything here works in UTC
epoch seconds and audience-local wall clocks.
"""
import datetime as dt
import random
from zoneinfo import ZoneInfo

# Peak windows in the audience's local time, best first: (start_h, start_m, end_h, end_m)
MARKETS = {
    "US": ("America/New_York", [(19, 0, 22, 30), (12, 0, 14, 0), (16, 0, 18, 0)]),
    "UK": ("Europe/London", [(18, 0, 21, 30), (12, 0, 14, 0), (16, 0, 17, 30)]),
    "CA": ("America/Toronto", [(19, 0, 22, 30), (12, 0, 14, 0), (16, 0, 18, 0)]),
    "AU": ("Australia/Sydney", [(19, 0, 22, 0), (12, 0, 14, 0), (16, 30, 18, 0)]),
}
AUDIENCES = {
    "US+UK": ["UK", "US"],
    "US": ["US"],
    "UK": ["UK"],
    "CA": ["CA"],
    "AU": ["AU"],
}
DEFAULT_AUDIENCE = "US+UK"
DEFAULT_PLAN = {"posts_per_day": 2, "audience": DEFAULT_AUDIENCE, "min_gap_min": 240, "jitter_min": 30}


def normalize_plan(plan):
    """Fill defaults and clamp values. Unknown audience falls back to US+UK."""
    p = dict(DEFAULT_PLAN)
    p.update({k: v for k, v in (plan or {}).items() if v is not None})
    try:
        p["posts_per_day"] = max(1, min(6, int(p["posts_per_day"])))
        p["min_gap_min"] = max(30, min(720, int(p["min_gap_min"])))
        p["jitter_min"] = max(0, min(60, int(p["jitter_min"])))
    except (TypeError, ValueError):
        p = dict(DEFAULT_PLAN)
    if p.get("audience") not in AUDIENCES:
        p["audience"] = DEFAULT_AUDIENCE
    return p


def _window_utc(market, local_date, w):
    tz_name, _ = MARKETS[market]
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # missing tz data must not stop posting: fall back to fixed offsets
        tz = dt.timezone(dt.timedelta(hours={"US": -5, "CA": -5, "UK": 0, "AU": 11}[market]))
    sh, sm, eh, em = w
    start = dt.datetime(local_date.year, local_date.month, local_date.day, sh, sm, tzinfo=tz)
    end = dt.datetime(local_date.year, local_date.month, local_date.day, eh, em, tzinfo=tz)
    return start.timestamp(), end.timestamp()


def candidate_windows(audience, plan_day):
    """Peak windows for one plan day, ordered best first, alternating markets."""
    markets = AUDIENCES.get(audience, AUDIENCES[DEFAULT_AUDIENCE])
    levels = max(len(MARKETS[m][1]) for m in markets)
    out = []
    for level in range(levels):
        for m in markets:
            wins = MARKETS[m][1]
            if level < len(wins):
                s, e = _window_utc(m, plan_day, wins[level])
                out.append({"market": m, "start": s, "end": e})
    return out


def plan_day(plan, plan_day, rng=None):
    """Return [{'due','window_end','market'}] for one day, inside peak windows,
    at natural (randomised) minutes and respecting the minimum gap."""
    rng = rng or random.SystemRandom()
    p = normalize_plan(plan)
    n, gap, jit = p["posts_per_day"], p["min_gap_min"] * 60, p["jitter_min"] * 60
    wins = candidate_windows(p["audience"], plan_day)
    picks = []
    # The requested number of posts wins over the gap: if the peak windows are too
    # tight, the gap is relaxed step by step (never below 90 minutes).
    for g in (gap, gap * 0.75, gap * 0.5, 90 * 60):
        picks = _fill(wins, n, max(g, 90 * 60), jit, rng)
        if len(picks) >= n:
            break
    picks.sort(key=lambda q: q["due"])
    return picks


def _fill(wins, n, gap, jit, rng):
    picks = []
    i = 0
    while len(picks) < n and i < n * 4:
        w = wins[i % len(wins)]
        i += 1
        span = w["end"] - w["start"]
        centre = w["start"] + span * rng.uniform(0.3, 0.7)
        t = centre + rng.uniform(-jit, jit)
        t = min(max(t, w["start"] + 60), w["end"] - 60)
        if all(abs(t - q["due"]) >= gap for q in picks):
            picks.append({"due": round(t), "window_end": w["end"], "market": w["market"]})
            continue
        # try the edges of this window before giving up on it
        for edge in (w["start"] + rng.uniform(60, 600), w["end"] - rng.uniform(60, 600)):
            if all(abs(edge - q["due"]) >= gap for q in picks):
                picks.append({"due": round(edge), "window_end": w["end"], "market": w["market"]})
                break
    return picks


def utc_day(ts=None):
    return dt.datetime.fromtimestamp(ts if ts is not None else dt.datetime.now().timestamp(), dt.timezone.utc).date()


def local_day_bounds(tz_name, ts):
    """UTC epoch bounds of the viewer's local calendar day containing ts."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = dt.timezone(dt.timedelta(hours=5))
    d = dt.datetime.fromtimestamp(ts, tz).date()
    start = dt.datetime(d.year, d.month, d.day, tzinfo=tz)
    return start.timestamp(), (start + dt.timedelta(days=1)).timestamp(), d


def local_hour(tz_name, ts):
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = dt.timezone(dt.timedelta(hours=5))
    return dt.datetime.fromtimestamp(ts, tz).hour


def next_pacific_midnight(ts):
    """YouTube quotas reset at midnight Pacific time."""
    tz = ZoneInfo("America/Los_Angeles")
    now = dt.datetime.fromtimestamp(ts, tz)
    tomorrow = (now + dt.timedelta(days=1)).date()
    return dt.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 5, tzinfo=tz).timestamp()
