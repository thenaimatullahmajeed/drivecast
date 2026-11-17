"""Dashboard and report numbers. All "day" buckets use the viewer's timezone."""
import datetime as dt
import time
from collections import defaultdict
from zoneinfo import ZoneInfo

from . import db
from .timeplan import local_day_bounds


def _tz(name):
    try:
        return ZoneInfo(name)
    except Exception:
        return dt.timezone(dt.timedelta(hours=5))


def _channel_daily(ws_id, days):
    """{channel_id: {utc_day: views gained that day}} from daily channel snapshots."""
    since = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=days + 1)).isoformat()
    rows = db.all("""SELECT p.channel_id, p.day, p.views FROM channel_points p JOIN channels c ON c.id=p.channel_id
                     WHERE c.workspace_id=? AND p.day>=? ORDER BY p.channel_id, p.day""", (ws_id, since))
    out, prev = defaultdict(dict), {}
    for r in rows:
        if r["channel_id"] in prev and r["views"] is not None and prev[r["channel_id"]] is not None:
            out[r["channel_id"]][r["day"]] = max(0, r["views"] - prev[r["channel_id"]])
        prev[r["channel_id"]] = r["views"]
    return out


def _day_list(n):
    today = dt.datetime.now(dt.timezone.utc).date()
    return [(today - dt.timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


def dashboard(ws_id, tz_name, now=None):
    now = now or time.time()
    tz = _tz(tz_name)
    chans = db.all("SELECT c.*, g.number group_number, g.status group_status FROM channels c "
                   "LEFT JOIN ip_groups g ON g.id=c.group_id WHERE c.workspace_id=? AND c.state!='setup'", (ws_id,))
    cmap = {c["id"]: c for c in chans}
    start, end, _ = local_day_bounds(tz_name, now)

    # --- Today card ---
    tslots = db.all("""SELECT s.* FROM slots s JOIN channels c ON c.id=s.channel_id
                       WHERE c.workspace_id=? AND s.due_at>=? AND s.due_at<?""", (ws_id, start, end))
    by_ch = defaultdict(lambda: {"done": 0, "total": 0, "next": None, "states": []})
    counts = defaultdict(int)
    for s in tslots:
        counts[s["status"]] += 1
        b = by_ch[s["channel_id"]]
        b["total"] += 1
        b["states"].append(s["status"])
        if s["status"] == "done":
            b["done"] += 1
        if s["status"] in ("planned", "queued") and s["due_at"] >= now and (b["next"] is None or s["due_at"] < b["next"]):
            b["next"] = s["due_at"]
    nxt = db.one("""SELECT s.due_at, s.channel_id, c.title FROM slots s JOIN channels c ON c.id=s.channel_id
                    WHERE c.workspace_id=? AND s.status IN ('planned','queued') AND s.due_at>=? ORDER BY s.due_at LIMIT 1""",
                 (ws_id, now - 60))
    daily = _channel_daily(ws_id, 62)
    days = _day_list(62)
    total_by_day = [sum(daily[c].get(d, 0) for c in daily) for d in days]
    today_card = {
        "done": counts["done"], "total": len(tslots),
        "upcoming": counts["planned"] + counts["queued"] + counts["running"],
        "held": counts["held"] + counts["failed"] + counts["uncertain"] + counts["missed"],
        "next": nxt, "views_today": total_by_day[-1] if total_by_day else 0,
        "channels": sorted([{"id": cid, "title": cmap[cid]["title"], **{k: v for k, v in b.items()}}
                            for cid, b in by_ch.items() if cid in cmap], key=lambda x: x["title"].lower()),
    }

    # --- KPIs ---
    v7, p7 = sum(total_by_day[-7:]), sum(total_by_day[-14:-7])
    ups = db.all("""SELECT u.*, c.title channel_title FROM uploads u JOIN channels c ON c.id=u.channel_id
                    WHERE u.workspace_id=? AND u.status='published' AND u.published_at>? ORDER BY u.published_at DESC""",
                 (ws_id, now - 60 * 86400))
    first24 = _first_day_views(ups)
    recent = [first24[u["id"]] for u in ups if u["id"] in first24 and u["published_at"] > now - 30 * 86400]
    older = [first24[u["id"]] for u in ups if u["id"] in first24 and u["published_at"] <= now - 30 * 86400]
    past = db.all("""SELECT s.status FROM slots s JOIN channels c ON c.id=s.channel_id
                     WHERE c.workspace_id=? AND s.due_at BETWEEN ? AND ? AND s.status NOT IN ('planned','queued','running','skipped')""",
                  (ws_id, now - 7 * 86400, now))
    ok = sum(1 for s in past if s["status"] == "done")
    kpis = {
        "views_7d": v7, "views_7d_prev": p7, "views_series": total_by_day[-14:],
        "published_today": counts["done"], "planned_today": len(tslots),
        "avg_first24": round(sum(recent) / len(recent)) if recent else None,
        "avg_first24_prev": round(sum(older) / len(older)) if older else None,
        "health": round(100 * ok / len(past)) if past else None,
    }

    # --- views trend (30 days, with previous 30) ---
    trend = {"days": days[-30:], "views": total_by_day[-30:], "previous": total_by_day[-60:-30],
             "total_views": sum((c["total_views"] or 0) for c in chans)}

    # --- leaderboard ---
    board = []
    for c in chans:
        series = [daily[c["id"]].get(d, 0) for d in days[-14:]]
        board.append({"id": c["id"], "title": c["title"], "handle": c["handle"], "thumbnail": c["thumbnail"],
                      "views_7d": sum(series[-7:]), "prev_7d": sum(series[:7]), "series": series})
    board.sort(key=lambda x: -x["views_7d"])

    # --- best hours heatmap (viewer's local weekday x hour) ---
    cells = defaultdict(list)
    for u in ups:
        if u["id"] in first24:
            t = dt.datetime.fromtimestamp(u["published_at"], tz)
            cells[(t.weekday(), t.hour)].append(first24[u["id"]])
    heat = [[round(sum(cells[(d, h)]) / len(cells[(d, h)])) if cells[(d, h)] else None for h in range(24)] for d in range(7)]

    # --- consistency (14 local days) ---
    cons = []
    for k in range(13, -1, -1):
        s0, e0, d0 = local_day_bounds(tz_name, now - k * 86400)
        pl = db.one("""SELECT COUNT(*) n FROM slots s JOIN channels c ON c.id=s.channel_id
                       WHERE c.workspace_id=? AND s.due_at>=? AND s.due_at<? AND s.status!='skipped'""", (ws_id, s0, e0))["n"]
        pb = db.one("SELECT COUNT(*) n FROM uploads WHERE workspace_id=? AND status='published' AND published_at>=? AND published_at<?",
                    (ws_id, s0, e0))["n"]
        cons.append({"day": d0.isoformat(), "planned": pl, "published": pb, "today": k == 0})

    upnext = db.all("""SELECT s.id, s.due_at, s.status, s.market, c.id channel_id, c.title, g.number group_number, g.status group_status
                       FROM slots s JOIN channels c ON c.id=s.channel_id LEFT JOIN ip_groups g ON g.id=c.group_id
                       WHERE c.workspace_id=? AND s.status IN ('planned','queued','running') AND s.due_at>=?
                       ORDER BY s.due_at LIMIT 6""", (ws_id, now - 120))
    from . import worker
    today_card["live"] = [{"channel_id": cid, "channel": cmap[cid]["title"], **p} for cid, p in list(worker.PROGRESS.items()) if cid in cmap]
    return {"now": now, "today": today_card, "kpis": kpis, "trend": trend, "leaderboard": board[:8],
            "heatmap": heat, "heat_samples": sum(len(v) for v in cells.values()), "consistency": cons,
            "upnext": upnext, "attention": attention(ws_id, now),
            "recent": [{"id": u["id"], "title": u["title"], "channel": u["channel_title"], "published_at": u["published_at"],
                        "views": u["views"], "video_id": u["external_video_id"], "visibility": u["visibility"]} for u in ups[:8]]}


def _first_day_views(ups):
    """Views one day after publishing (from daily snapshots), else latest known."""
    if not ups:
        return {}
    ids = [u["id"] for u in ups]
    pts = defaultdict(dict)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in db.all(f"SELECT upload_id, day, views FROM stat_points WHERE upload_id IN ({q})", chunk):
            pts[r["upload_id"]][r["day"]] = r["views"]
    out = {}
    for u in ups:
        pub = dt.datetime.fromtimestamp(u["published_at"], dt.timezone.utc).date()
        nextday = (pub + dt.timedelta(days=1)).isoformat()
        if nextday in pts[u["id"]]:
            out[u["id"]] = pts[u["id"]][nextday]
        elif u["views"] is not None and time.time() - u["published_at"] > 86400:
            out[u["id"]] = u["views"]
    return out


def attention(ws_id, now=None):
    now = now or time.time()
    items = []
    for g in db.all("SELECT * FROM ip_groups WHERE workspace_id=? AND status='down'", (ws_id,)):
        n = db.one("SELECT COUNT(*) n FROM channels WHERE group_id=?", (g["id"],))["n"]
        items.append({"level": "error", "kind": "proxy", "title": f"Group {g['number']} is not responding",
                      "detail": f"{n} channel(s) held. Nothing is sent from the server IP.", "action": "groups",
                      "ref": g["id"]})
    for c in db.all("SELECT * FROM channels WHERE workspace_id=? AND state='attention'", (ws_id,)):
        items.append({"level": "error", "kind": "channel", "title": c["title"], "detail": c["state_reason"] or "Needs attention",
                      "action": "channels", "ref": c["id"]})
    for c in db.all("SELECT * FROM channels WHERE workspace_id=? AND state='live' AND queue_count IS NOT NULL AND queue_count<=2",
                    (ws_id,)):
        items.append({"level": "warn", "kind": "folder", "title": f"{c['title']} — {c['queue_count']} video(s) left",
                      "detail": "Add videos to its Drive folder.", "action": "channels", "ref": c["id"]})
    for u in db.all("SELECT * FROM uploads WHERE workspace_id=? AND status='uncertain' ORDER BY reserved_at DESC LIMIT 5", (ws_id,)):
        items.append({"level": "error", "kind": "uncertain", "title": f"Check “{u['title']}” in YouTube Studio",
                      "detail": u["error"] or "Upload result unknown. It will not be retried.", "action": "uncertain",
                      "ref": u["id"]})
    seen = set()
    for e in db.all("""SELECT * FROM events WHERE workspace_id=? AND resolved=0 AND level IN ('warn','error') AND at>?
                       ORDER BY at DESC LIMIT 40""", (ws_id, now - 3 * 86400)):
        if e["kind"] in ("proxy", "uncertain") or e["message"] in seen:
            continue
        seen.add(e["message"])
        items.append({"level": e["level"], "kind": e["kind"], "title": e["message"], "detail": None, "action": "event",
                      "ref": e["id"], "at": e["at"]})
    return items[:12]


def user_report(ws_id, tz_name, days=30, now=None):
    now = now or time.time()
    dash = dashboard(ws_id, tz_name, now)
    since = now - days * 86400
    per = db.all("""SELECT c.id, c.title, c.handle, c.state, g.number group_number,
                    COUNT(u.id) uploads, COALESCE(SUM(u.views),0) views, MAX(u.published_at) last_post
                    FROM channels c LEFT JOIN ip_groups g ON g.id=c.group_id
                    LEFT JOIN uploads u ON u.channel_id=c.id AND u.status='published' AND u.published_at>=?
                    WHERE c.workspace_id=? GROUP BY c.id ORDER BY views DESC""", (since, ws_id))
    status = {r["status"]: r["n"] for r in db.all(
        "SELECT status, COUNT(*) n FROM uploads WHERE workspace_id=? AND reserved_at>=? GROUP BY status", (ws_id, since))}
    daily_up = []
    for k in range(days - 1, -1, -1):
        s0, e0, d0 = local_day_bounds(tz_name, now - k * 86400)
        n = db.one("SELECT COUNT(*) n FROM uploads WHERE workspace_id=? AND status='published' AND published_at>=? AND published_at<?",
                   (ws_id, s0, e0))["n"]
        daily_up.append({"day": d0.isoformat(), "published": n})
    groups = db.all("SELECT id, number, status, outgoing_ip, latency_ms FROM ip_groups WHERE workspace_id=?", (ws_id,))
    events = db.all("SELECT level, kind, message, at FROM events WHERE workspace_id=? ORDER BY at DESC LIMIT 25", (ws_id,))
    return {"trend": dash["trend"], "kpis": dash["kpis"], "channels": per, "status": status,
            "daily_uploads": daily_up, "groups": groups, "events": events}
