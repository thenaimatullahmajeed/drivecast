"""Dashboard and report numbers. All "day" buckets use the viewer's timezone."""
import bisect
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


def _series(rows, key):
    """rows sorted by (id, at) -> {id: ([at...], [value...])}"""
    out = defaultdict(lambda: ([], []))
    for r in rows:
        if r[key] is None:
            continue
        a, v = out[r["id"]]
        a.append(r["at"])
        v.append(r[key])
    return out


def _at(ser, ts):
    """Last reading at or before ts, else None."""
    ats, vals = ser
    i = bisect.bisect_right(ats, ts)
    return vals[i - 1] if i else None


def _gain(ser, s, e):
    """Growth between s and e. Without a reading before s, the first reading inside the window is the base."""
    ats, vals = ser
    end = _at(ser, e)
    if end is None:
        return 0
    base = _at(ser, s)
    if base is None:
        i = bisect.bisect_left(ats, s)
        if i >= len(ats) or ats[i] >= e:
            return 0
        base = vals[i]
    return end - base


def _day_bounds(tz_name, now, n):
    """n local days ending today: [(start, end, iso_day)] oldest first."""
    out = []
    for k in range(n - 1, -1, -1):
        s0, e0, d0 = local_day_bounds(tz_name, now - k * 86400)
        out.append((s0, e0, d0.isoformat()))
    return out


class Views:
    """Views gained per channel in any window: the larger of the channel counter
    (YouTube updates it slowly) and the sum of our own videos' counters (fast)."""

    def __init__(self, ws_id, since, now):
        self.now = now
        ct = db.all("""SELECT t.channel_id id, t.at, t.views, t.subscribers FROM channel_ticks t JOIN channels c ON c.id=t.channel_id
                       WHERE c.workspace_id=? AND t.at>=? ORDER BY t.channel_id, t.at""", (ws_id, since - 86400))
        self.ch = _series(ct, "views")
        self.subs = _series(ct, "subscribers")
        ups = db.all("""SELECT id, channel_id, published_at FROM uploads WHERE workspace_id=? AND status='published'
                        AND published_at>?""", (ws_id, since - 30 * 86400))
        self.up_ch = defaultdict(list)
        self.up = {}
        if ups:
            ids = [u["id"] for u in ups]
            rows = []
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                rows += db.all(f"SELECT upload_id id, at, views FROM upload_ticks WHERE upload_id IN ({','.join('?' * len(chunk))}) ORDER BY upload_id, at", chunk)
            ser = _series(rows, "views")
            for u in ups:
                a, v = ser[u["id"]] if u["id"] in ser else ([], [])
                # a video starts at zero views when it is published
                self.up[u["id"]] = ([u["published_at"]] + a, [0] + v)
                self.up_ch[u["channel_id"]].append(u["id"])

    def video_gain(self, uid, s, e):
        return max(0, _gain(self.up[uid], s, e)) if uid in self.up else 0

    def channel(self, cid, s, e):
        c = max(0, _gain(self.ch[cid], s, e)) if cid in self.ch else 0
        v = sum(self.video_gain(u, s, e) for u in self.up_ch.get(cid, []))
        return max(c, v)

    def subs_gain(self, cid, s, e):
        return _gain(self.subs[cid], s, e) if cid in self.subs else 0


def dashboard(ws_id, tz_name, now=None, since=None):
    now = now or time.time()
    tz = _tz(tz_name)
    chans = db.all("SELECT c.*, g.number group_number, g.status group_status, g.country group_country FROM channels c "
                   "LEFT JOIN ip_groups g ON g.id=c.group_id WHERE c.workspace_id=? AND c.state!='setup'", (ws_id,))
    cmap = {c["id"]: c for c in chans}
    start, end, _ = local_day_bounds(tz_name, now)
    bounds = _day_bounds(tz_name, now, 60)
    V = Views(ws_id, bounds[0][0], now)
    days = [b[2] for b in bounds]
    per_ch = {c["id"]: [V.channel(c["id"], s0, e0) for s0, e0, _ in bounds] for c in chans}
    total_by_day = [sum(per_ch[c][i] for c in per_ch) for i in range(len(bounds))]

    # --- Today card ---
    tslots = db.all("""SELECT s.* FROM slots s JOIN channels c ON c.id=s.channel_id
                       WHERE c.workspace_id=? AND s.due_at>=? AND s.due_at<? ORDER BY s.due_at""", (ws_id, start, end))
    by_ch = defaultdict(lambda: {"done": 0, "total": 0, "next": None, "states": []})
    counts = defaultdict(int)
    for s in tslots:
        counts[s["status"]] += 1
        b = by_ch[s["channel_id"]]
        b["total"] += 1
        b["states"].append(s["status"])
        if s["status"] == "done":
            b["done"] += 1
    nxt_rows = db.all("""SELECT s.channel_id, MIN(s.due_at) due_at FROM slots s JOIN channels c ON c.id=s.channel_id
                         WHERE c.workspace_id=? AND s.status IN ('planned','queued') AND s.due_at>=? GROUP BY s.channel_id""",
                      (ws_id, now - 60))
    next_by_ch = {r["channel_id"]: r["due_at"] for r in nxt_rows}
    nxt = db.one("""SELECT s.due_at, s.channel_id, c.title FROM slots s JOIN channels c ON c.id=s.channel_id
                    WHERE c.workspace_id=? AND s.status IN ('planned','queued') AND s.due_at>=? ORDER BY s.due_at LIMIT 1""",
                 (ws_id, now - 60))
    if nxt:  # what will go out next, and from where (Instagram, your Drive, Inbox, YouTube, TikTok)
        q = db.jload((db.one("SELECT queue_json FROM channels WHERE id=?", (nxt["channel_id"],)) or {}).get("queue_json"), []) or []
        nxt = dict(nxt)
        nxt["item"] = q[0] if q else None
    from . import worker
    live = {cid: p for cid, p in list(worker.PROGRESS.items()) if cid in cmap}
    today_card = {
        "done": counts["done"], "total": len(tslots),
        "upcoming": counts["planned"] + counts["queued"] + counts["running"],
        "held": counts["held"] + counts["failed"] + counts["uncertain"] + counts["missed"],
        "next": nxt, "views_today": total_by_day[-1],
        "channels": sorted([{"id": cid, "title": cmap[cid]["title"], **b, "next": next_by_ch.get(cid)}
                            for cid, b in by_ch.items() if cid in cmap], key=lambda x: x["title"].lower()),
        "live": [{"channel_id": cid, "channel": cmap[cid]["title"], "thumbnail": cmap[cid]["thumbnail"], **p} for cid, p in live.items()],
    }

    # --- uploads ---
    ups = db.all("""SELECT u.*, c.title channel_title, c.thumbnail channel_thumb FROM uploads u JOIN channels c ON c.id=u.channel_id
                    WHERE u.workspace_id=? AND u.status='published' AND u.published_at>? ORDER BY u.published_at DESC""",
                 (ws_id, now - 60 * 86400))
    first24 = _first_day_views(ups)
    recent = [first24[u["id"]] for u in ups if u["id"] in first24 and u["published_at"] > now - 30 * 86400]
    older = [first24[u["id"]] for u in ups if u["id"] in first24 and u["published_at"] <= now - 30 * 86400]
    ups30 = [u for u in ups if u["published_at"] > now - 30 * 86400 and u["views"] is not None]
    posts7 = sum(1 for u in ups if u["published_at"] > now - 7 * 86400)
    posts7p = sum(1 for u in ups if now - 14 * 86400 < u["published_at"] <= now - 7 * 86400)
    past = db.all("""SELECT s.status FROM slots s JOIN channels c ON c.id=s.channel_id
                     WHERE c.workspace_id=? AND s.due_at BETWEEN ? AND ? AND s.status NOT IN ('planned','queued','running','skipped')""",
                  (ws_id, now - 7 * 86400, now))
    ok = sum(1 for s in past if s["status"] == "done")

    # --- channel pulse ---
    w7 = (bounds[-7][0], now)
    pulse = []
    for c in chans:
        plan = db.jload(c["plan_json"], {})
        ppd = int(plan.get("posts_per_day") or 2)
        q = c["queue_count"]
        last = next((u for u in ups if u["channel_id"] == c["id"]), None)
        b = by_ch.get(c["id"], {"done": 0, "total": 0, "states": []})
        series = per_ch[c["id"]][-14:]
        pulse.append({
            "id": c["id"], "title": c["title"], "handle": c["handle"], "thumbnail": c["thumbnail"], "state": c["state"],
            "group_number": c["group_number"], "group_status": c["group_status"], "group_country": c["group_country"],
            "posts_per_day": ppd, "queue": q, "runway_days": None if q is None else round(q / max(1, ppd), 1),
            "done": b["done"], "total": b["total"], "states": b["states"], "next": next_by_ch.get(c["id"]),
            "live": live.get(c["id"]), "subscribers": c["subscribers"], "subs_7d": V.subs_gain(c["id"], *w7),
            "views_today": per_ch[c["id"]][-1], "views_7d": sum(series[-7:]), "prev_7d": sum(series[:7]), "series": series,
            "total_views": c["total_views"], "stats_at": c["stats_at"],
            "last_post": {"title": last["title"], "at": last["published_at"], "views": last["views"]} if last else None,
        })
    pulse.sort(key=lambda x: (x["state"] != "live", -(x["views_7d"] or 0), x["title"].lower()))
    runways = [p["runway_days"] for p in pulse if p["state"] == "live" and p["runway_days"] is not None]
    subs_total = sum(c["subscribers"] or 0 for c in chans)

    kpis = {
        "views_today": total_by_day[-1], "views_yesterday": total_by_day[-2],
        "views_7d": sum(total_by_day[-7:]), "views_7d_prev": sum(total_by_day[-14:-7]), "views_series": total_by_day[-14:],
        "subscribers": subs_total, "subs_7d": sum(p["subs_7d"] for p in pulse),
        "posts_7d": posts7, "posts_7d_prev": posts7p,
        "published_today": counts["done"], "planned_today": len(tslots),
        "avg_first24": round(sum(recent) / len(recent)) if recent else None,
        "avg_first24_prev": round(sum(older) / len(older)) if older else None,
        "avg_views": round(sum(u["views"] for u in ups30) / len(ups30)) if ups30 else None,
        "health": round(100 * ok / len(past)) if past else None,
        "runway_min": min(runways) if runways else None,
        "stats_at": max((c["stats_at"] or 0) for c in chans) if chans else None,
    }

    allpub = db.one("""SELECT COUNT(*) n, COALESCE(SUM(views),0) v, COALESCE(SUM(likes),0) l,
                              COALESCE(SUM(CASE WHEN destination IS NULL OR destination LIKE 'youtube%' THEN 1 ELSE 0 END),0) yt
                       FROM uploads WHERE workspace_id=? AND status='published' AND IFNULL(yt_state,'') NOT IN ('removed','rejected')""", (ws_id,))
    best = db.one("""SELECT u.title, u.views, u.external_video_id, c.title channel FROM uploads u JOIN channels c ON c.id=u.channel_id
                     WHERE u.workspace_id=? AND u.status='published' AND u.views IS NOT NULL ORDER BY u.views DESC LIMIT 1""", (ws_id,))
    since7 = now - 7 * 86400
    gained = db.one("""SELECT COALESCE(SUM(t.views),0) v FROM (
                          SELECT MAX(k.views) - COALESCE((SELECT views FROM upload_ticks x WHERE x.upload_id=k.upload_id AND x.at<? ORDER BY x.at DESC LIMIT 1),0) views
                          FROM upload_ticks k JOIN uploads u ON u.id=k.upload_id WHERE u.workspace_id=? AND k.at>=? GROUP BY k.upload_id) t""",
                    (since7, ws_id, since7))
    lenses = {
        "account": {"views_total": sum((c["total_views"] or 0) for c in chans), "subscribers": subs_total,
                    "videos": sum((c.get("video_count") or 0) for c in chans), "views_7d": kpis["views_7d"],
                    "channels": len(chans), "subs_7d": kpis["subs_7d"]},
        "posted": {"posts": allpub["n"], "youtube_posts": allpub["yt"], "views": allpub["v"], "likes": allpub["l"],
                   "views_7d": max(0, gained["v"] or 0), "avg": round(allpub["v"] / allpub["yt"]) if allpub["yt"] else None,
                   "best": best},
    }

    trend = {"days": days[-30:], "views": total_by_day[-30:], "previous": total_by_day[-60:-30],
             "total_views": sum((c["total_views"] or 0) for c in chans)}

    board = [{"id": p["id"], "title": p["title"], "handle": p["handle"], "thumbnail": p["thumbnail"],
              "views_7d": p["views_7d"], "prev_7d": p["prev_7d"], "series": p["series"]} for p in pulse]
    board.sort(key=lambda x: -x["views_7d"])

    cells = defaultdict(list)
    for u in ups:
        if u["id"] in first24:
            t = dt.datetime.fromtimestamp(u["published_at"], tz)
            cells[(t.weekday(), t.hour)].append(first24[u["id"]])
    heat = [[round(sum(cells[(d, h)]) / len(cells[(d, h)])) if cells[(d, h)] else None for h in range(24)] for d in range(7)]

    cons = []
    for s0, e0, d0 in bounds[-14:]:
        pl = db.one("""SELECT COUNT(*) n FROM slots s JOIN channels c ON c.id=s.channel_id
                       WHERE c.workspace_id=? AND s.due_at>=? AND s.due_at<? AND s.status!='skipped'""", (ws_id, s0, e0))["n"]
        # same ledger as the Schedule page: a slot is "published" when its status is done, so published can never exceed planned
        pb = db.one("""SELECT COUNT(*) n FROM slots s JOIN channels c ON c.id=s.channel_id
                       WHERE c.workspace_id=? AND s.due_at>=? AND s.due_at<? AND s.status='done'""", (ws_id, s0, e0))["n"]
        cons.append({"day": d0, "planned": pl, "published": pb, "today": s0 <= now < e0})

    upnext = db.all("""SELECT s.id, s.due_at, s.status, s.market, c.id channel_id, c.title, c.thumbnail, g.number group_number, g.status group_status
                       FROM slots s JOIN channels c ON c.id=s.channel_id LEFT JOIN ip_groups g ON g.id=c.group_id
                       WHERE c.workspace_id=? AND s.status IN ('planned','queued','running') AND s.due_at>=?
                       ORDER BY s.due_at LIMIT 8""", (ws_id, now - 120))

    recent_posts = []
    for u in ups[:10]:
        hours = max(0.25, (now - u["published_at"]) / 3600)
        recent_posts.append({"id": u["id"], "title": u["title"], "channel": u["channel_title"], "thumbnail": u["channel_thumb"],
                             "published_at": u["published_at"], "views": u["views"], "likes": u["likes"], "comments": u["comments"],
                             "video_id": u["external_video_id"], "visibility": u["visibility"],
                             "per_hour": round((u["views"] or 0) / hours) if u["views"] is not None else None,
                             "last_hour": V.video_gain(u["id"], now - 3600, now), "today": V.video_gain(u["id"], start, now)})

    feed = [{"at": u["published_at"], "level": "ok", "kind": "published", "message": f"Published “{u['title']}”",
             "channel": u["channel_title"], "video_id": u["external_video_id"]} for u in ups[:20]]
    for e in db.all("SELECT e.level, e.kind, e.message, e.at, c.title channel FROM events e LEFT JOIN channels c ON c.id=e.channel_id "
                    "WHERE e.workspace_id=? ORDER BY e.at DESC LIMIT 25", (ws_id,)):
        feed.append(dict(e))
    feed.sort(key=lambda x: -x["at"])

    away = None
    if since and since < now - 300:
        since = max(since, now - 30 * 86400)
        away = {"since": since,
                "published": sum(1 for u in ups if u["published_at"] >= since),
                "views": sum(V.channel(c["id"], since, now) for c in chans),
                "problems": db.one("SELECT COUNT(*) n FROM events WHERE workspace_id=? AND level='error' AND at>=?", (ws_id, since))["n"]}

    return {"now": now, "today": today_card, "kpis": kpis, "lenses": lenses, "trend": trend, "leaderboard": board[:8], "pulse": pulse,
            "heatmap": heat, "heat_samples": sum(len(v) for v in cells.values()), "consistency": cons,
            "upnext": upnext, "attention": attention(ws_id, now), "recent": recent_posts, "feed": feed[:30], "away": away}


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
    """Problems a person can fix, each with the button that fixes it."""
    from . import errors
    now = now or time.time()
    items = []
    # phone / fleet alerts (owner only): replaced phone, battery, heat, phones offline or full, quota, backup test
    owner = db.one("SELECT workspace_id FROM users WHERE role='owner' ORDER BY id LIMIT 1")
    if owner and owner["workspace_id"] == ws_id:
        for a in db.jload(db.meta_get("alerts_json"), []) or []:
            items.append({"level": a["level"], "kind": "alert", "title": a["title"], "detail": a["message"],
                          "action": "fix" if a.get("fix") else None, "fix": a.get("fix"), "ref": None, "code": a["code"]})
    for g in db.all("SELECT * FROM ip_groups WHERE workspace_id=? AND status='down' AND moved_to IS NULL", (ws_id,)):
        n = db.one("SELECT COUNT(*) n FROM channels WHERE group_id=?", (g["id"],))["n"]
        items.append({"level": "error", "kind": "proxy", "title": f"Pool {g['number']} is not responding",
                      "detail": f"{n} channel(s) held. Nothing is sent from the phone’s own IP.", "action": "fix",
                      "fix": "replace_proxy", "ref": g["id"], "code": "PROXY_DEAD"})
    for g in db.all("SELECT * FROM ip_groups WHERE workspace_id=? AND status='unstable' AND moved_to IS NULL", (ws_id,)):
        items.append({"level": "warn", "kind": "proxy", "title": f"Pool {g['number']}: proxy slow to answer — retrying",
                      "detail": f"Missed {g['fail_count'] or 1} check(s). DriveCast keeps trying; nothing to do yet.",
                      "action": "fix", "fix": "test_group", "ref": g["id"], "code": "PROXY_RETRYING"})
    for a in db.all("""SELECT a.*, g.number FROM fb_accounts a JOIN ip_groups g ON g.id=a.group_id
                       WHERE a.workspace_id=? AND a.status='needs_reconnect'""", (ws_id,)):
        i = errors.info(a["error_code"] or "FB_TOKEN_EXPIRED", group=a["number"])
        items.append({"level": "error", "kind": "facebook", "title": i["title"] + f" · Pool {a['number']}", "detail": i["message"],
                      "action": "fix", "fix": "reconnect_facebook", "ref": a["group_id"], "code": i["code"]})
    for a in db.all("""SELECT a.*, g.number FROM ig_accounts a JOIN ip_groups g ON g.id=a.group_id
                       WHERE a.workspace_id=? AND a.status='needs_cookies'""", (ws_id,)):
        i = errors.info(a["error_code"] or "IG_COOKIES_EXPIRED", group=a["number"])
        items.append({"level": "error", "kind": "instagram", "title": i["title"] + f" · Pool {a['number']}", "detail": i["message"],
                      "action": "fix", "fix": "replace_cookies", "ref": a["group_id"], "code": i["code"]})
    for d in db.all("""SELECT d.*, c.title FROM ig_dest d LEFT JOIN channels c ON c.id=d.channel_id
                       WHERE d.workspace_id=? AND d.status='needs_reconnect'""", (ws_id,)):
        i = errors.info(d["error_code"] or "IGD_TOKEN_EXPIRED")
        items.append({"level": "error", "kind": "instagram", "title": f"@{d['username'] or d['ig_user_id']}: {i['title']}",
                      "detail": i["message"], "action": "fix", "fix": "connect_ig_dest", "ref": d["group_id"], "code": i["code"]})
    for s in db.all("""SELECT s.*, c.title FROM sources s JOIN channels c ON c.id=s.channel_id WHERE c.workspace_id=? AND s.state='active'
                       AND s.error_code IN ('IG_PROFILE_NOT_FOUND','IG_PROFILE_PRIVATE','DRIVE_FOLDER_GONE',
                                            'VID_NOT_FOUND','VID_LOGIN_NEEDED','VID_BOT_CHECK','YTDLP_OLD','YTDLP_MISSING')""", (ws_id,)):
        site = {"youtube": "YouTube", "tiktok": "TikTok"}.get(s["kind"], "Instagram")
        i = errors.info(s["error_code"], handle=s["handle"], site=site)
        items.append({"level": "error", "kind": "source", "title": f"{s['title']}: {i['title']}", "detail": i["message"],
                      "action": "fix", "fix": i["fix"] or "edit_source", "ref": s["channel_id"], "code": i["code"]})
    for g in db.all("""SELECT g.* FROM ip_groups g WHERE g.workspace_id=? AND NOT EXISTS (SELECT 1 FROM ig_accounts a WHERE a.group_id=g.id)
                       AND EXISTS (SELECT 1 FROM sources s JOIN channels c ON c.id=s.channel_id WHERE c.group_id=g.id AND s.state='active'
                                   AND s.kind='instagram')""", (ws_id,)):
        i = errors.info("IG_NO_ACCOUNT", group=g["number"])
        items.append({"level": "error", "kind": "instagram", "title": i["title"], "detail": i["message"], "action": "fix",
                      "fix": "add_cookies", "ref": g["id"], "code": i["code"]})
    for c in db.all("SELECT * FROM channels WHERE workspace_id=? AND state='attention'", (ws_id,)):
        items.append({"level": "error", "kind": "channel", "title": c["title"], "detail": c["state_reason"] or "Needs attention",
                      "action": "fix", "fix": "open_channel", "ref": c["id"]})
    for c in db.all("""SELECT * FROM channels c WHERE workspace_id=? AND state='live' AND queue_count IS NOT NULL AND queue_count<=2
                       AND NOT EXISTS (SELECT 1 FROM sources s WHERE s.channel_id=c.id AND s.state='active')""", (ws_id,)):
        items.append({"level": "warn", "kind": "folder", "title": f"{c['title']} — {c['queue_count']} video(s) left",
                      "detail": "Add videos to its Drive folder, or connect the client’s Instagram.", "action": "fix",
                      "fix": "open_channel", "ref": c["id"]})
    for u in db.all("SELECT * FROM uploads WHERE workspace_id=? AND status='uncertain' ORDER BY reserved_at DESC LIMIT 5", (ws_id,)):
        where = {"facebook": "the Facebook page", "instagram": "Instagram"}.get((u["destination"] or "").split(":")[0], "YouTube Studio")
        items.append({"level": "error", "kind": "uncertain", "title": f"Check “{u['title']}” in {where}",
                      "detail": u["error"] or "Upload result unknown. It will not be retried.", "action": "fix",
                      "fix": "resolve_upload", "ref": u["id"], "code": "YT_UNCERTAIN"})
    shown = {i.get("code") for i in items if i.get("code")}
    seen = set()
    for e in db.all("""SELECT * FROM events WHERE workspace_id=? AND resolved=0 AND level IN ('warn','error') AND at>?
                       ORDER BY at DESC LIMIT 40""", (ws_id, now - 3 * 86400)):
        if e["kind"] in ("proxy", "uncertain") or e["message"] in seen or (e["code"] and e["code"] in shown):
            continue
        seen.add(e["message"])
        fix = errors.CATALOG.get(e["code"] or "", (None, None, None, None))[3]
        items.append({"level": e["level"], "kind": e["kind"], "title": e["message"], "detail": None, "action": "event",
                      "ref": e["id"], "at": e["at"], "code": e["code"], "fix": fix, "channel_id": e["channel_id"],
                      "has_detail": bool(e.get("detail"))})
    return items[:14]


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


# ---------------------------------------------------------------- Accounts: every connected account, by platform
def accounts(ws_id):
    now = time.time()
    w7 = now - 7 * 86400

    def posts(where, args):
        r = db.one(f"""SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN published_at>? THEN 1 ELSE 0 END),0) n7,
                       COALESCE(SUM(views),0) v, MAX(published_at) last,
                       COALESCE(SUM(CASE WHEN status IN ('failed','uncertain') AND reserved_at>? THEN 1 ELSE 0 END),0) bad7
                       FROM uploads WHERE workspace_id=? AND {where}""", (w7, w7, ws_id, *args))
        return {"posts": r["n"], "posts_7d": r["n7"], "views": r["v"], "last_post": r["last"], "failed_7d": r["bad7"]}

    yt = []
    for c in db.all("""SELECT c.*, g.number pool FROM channels c LEFT JOIN ip_groups g ON g.id=c.group_id
                       WHERE c.workspace_id=? AND c.platform='youtube' AND c.external_id IS NOT NULL ORDER BY c.title""", (ws_id,)):
        p = posts("destination=? AND status='published'", (f"youtube:{c['external_id']}",))
        p["failed_7d"] = posts("destination=?", (f"youtube:{c['external_id']}",))["failed_7d"]
        yt.append({"id": c["id"], "name": c["title"], "handle": c["handle"], "picture": c["thumbnail"], "pool": c["pool"],
                   "state": c["state"], "subscribers": c["subscribers"], "total_views": c["total_views"],
                   "videos": c.get("video_count"), "youtube_on": c.get("youtube_on", 1), "auth_email": c.get("auth_email"), **p,
                   "url": f"https://www.youtube.com/channel/{c['external_id']}"})
    fb = []
    for pg in db.all("""SELECT p.*, c.title client, g.number pool, a.name fb_name FROM fb_pages p JOIN fb_accounts a ON a.id=p.fb_account_id
                        LEFT JOIN channels c ON c.id=p.channel_id LEFT JOIN ip_groups g ON g.id=a.group_id
                        WHERE p.workspace_id=? ORDER BY p.name""", (ws_id,)):
        p = posts("destination=? AND status='published'", (f"facebook:{pg['page_id']}",))
        fb.append({"id": pg["id"], "name": pg["name"], "picture": pg["picture"], "pool": pg["pool"], "client": pg["client"],
                   "authorized_by": pg.get("fb_name"),
                   "client_id": pg["channel_id"], "category": pg["category"], **p, "url": f"https://www.facebook.com/{pg['page_id']}"})
    ig = []
    for d in db.all("""SELECT d.*, c.title client, g.number pool FROM ig_dest d LEFT JOIN channels c ON c.id=d.channel_id
                       LEFT JOIN ip_groups g ON g.id=d.group_id WHERE d.workspace_id=? ORDER BY d.username""", (ws_id,)):
        p = posts("destination=? AND status='published'", (f"instagram:{d['id']}",))
        ig.append({"id": d["id"], "name": "@" + (d["username"] or d["ig_user_id"]), "picture": d["picture"], "pool": d["pool"],
                   "client": d["client"], "client_id": d["channel_id"], "status": d["status"], **p,
                   "url": f"https://www.instagram.com/{d['username']}/" if d["username"] else None})
    return {"youtube": yt, "facebook": fb, "instagram": ig}


def account_deep(ws_id, kind, aid, tz_name):
    """Everything about one account: growth, every post with its numbers, best hours, sources, reliability."""
    tz = _tz(tz_name)
    now = time.time()
    if kind == "youtube":
        c = db.one("SELECT * FROM channels WHERE id=? AND workspace_id=?", (aid, ws_id))
        if not c:
            return None
        dest = f"youtube:{c['external_id']}"
        head = {"name": c["title"], "picture": c["thumbnail"], "handle": c["handle"], "subscribers": c["subscribers"], "auth_email": c.get("auth_email"),
                "total_views": c["total_views"], "videos": c.get("video_count"), "client_id": c["id"],
                "url": f"https://www.youtube.com/channel/{c['external_id']}", "stats_at": c["stats_at"]}
        pts = db.all("SELECT day, views, subscribers FROM channel_points WHERE channel_id=? ORDER BY day DESC LIMIT 91", (c["id"],))[::-1]
        growth = [{"day": pts[i]["day"], "views": max(0, (pts[i]["views"] or 0) - (pts[i - 1]["views"] or 0)),
                   "subs": (pts[i]["subscribers"] or 0) - (pts[i - 1]["subscribers"] or 0)} for i in range(1, len(pts))]
        ch_id = c["id"]
    elif kind == "facebook":
        p = db.one("SELECT * FROM fb_pages WHERE id=? AND workspace_id=?", (aid, ws_id))
        if not p:
            return None
        dest = f"facebook:{p['page_id']}"
        head = {"name": p["name"], "picture": p["picture"], "category": p["category"], "client_id": p["channel_id"],
                "url": f"https://www.facebook.com/{p['page_id']}"}
        growth, ch_id = [], p["channel_id"]
    elif kind == "instagram":
        d = db.one("SELECT * FROM ig_dest WHERE id=? AND workspace_id=?", (aid, ws_id))
        if not d:
            return None
        dest = f"instagram:{d['id']}"
        head = {"name": "@" + (d["username"] or d["ig_user_id"]), "picture": d["picture"], "client_id": d["channel_id"],
                "url": f"https://www.instagram.com/{d['username']}/" if d["username"] else None, "status": d["status"]}
        growth, ch_id = [], d["channel_id"]
    else:
        return None
    ups = db.all("""SELECT u.*, m.origin origin FROM uploads u LEFT JOIN media m ON m.id=u.media_id
                    WHERE u.workspace_id=? AND u.destination=? ORDER BY u.reserved_at DESC LIMIT 400""", (ws_id, dest))
    pub = [u for u in ups if u["status"] == "published"]
    first24 = _first_day_views(pub)
    with_views = [u for u in pub if u["views"] is not None]
    # posting per day (last 30 days, the account owner's time)
    daymap = defaultdict(lambda: {"published": 0, "failed": 0})
    for u in ups:
        t = u["published_at"] or u["reserved_at"]
        if not t or t < now - 30 * 86400:
            continue
        k = dt.datetime.fromtimestamp(t, tz).date().isoformat()
        daymap[k]["published" if u["status"] == "published" else "failed" if u["status"] in ("failed", "uncertain") else "published"] += 1
    days = [(dt.datetime.now(tz).date() - dt.timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
    cadence = [{"day": d, **daymap[d]} for d in days]
    cells = defaultdict(list)
    for u in pub:
        v = first24.get(u["id"], u["views"])
        if v is None or not u["published_at"]:
            continue
        t = dt.datetime.fromtimestamp(u["published_at"], tz)
        cells[(t.weekday(), t.hour)].append(v)
    heat = [[round(sum(cells[(d, h)]) / len(cells[(d, h)])) if cells[(d, h)] else None for h in range(24)] for d in range(7)]
    hours = defaultdict(list)
    for (d, h), vs in cells.items():
        hours[h] += vs
    best_hours = sorted(((h, round(sum(v) / len(v))) for h, v in hours.items() if v), key=lambda x: -x[1])[:3]
    origins = defaultdict(int)
    for u in pub:
        origins[u.get("origin") or ("encore" if u.get("encore_of") else "drive")] += 1
    total = len(ups)
    ok = len(pub)
    posts = [{"id": u["id"], "title": u["title"], "status": u["status"], "at": u["published_at"] or u["reserved_at"],
              "views": u["views"], "likes": u["likes"], "comments": u["comments"], "first_day": first24.get(u["id"]),
              "video_id": u["external_video_id"], "origin": u.get("origin"), "encore": bool(u.get("encore_of"))} for u in ups[:200]]
    summary = {"posts": ok, "failed": sum(1 for u in ups if u["status"] in ("failed", "uncertain")),
               "success_rate": round(100 * ok / total) if total else None,
               "views_on_posts": sum(u["views"] or 0 for u in with_views),
               "avg_views": round(sum(u["views"] for u in with_views) / len(with_views)) if with_views else None,
               "avg_first_day": round(sum(first24.values()) / len(first24)) if first24 else None,
               "likes": sum(u["likes"] or 0 for u in pub), "comments": sum(u["comments"] or 0 for u in pub),
               "views_30d": sum(g["views"] for g in growth[-30:]) if growth else None,
               "subs_30d": sum(g["subs"] for g in growth[-30:]) if growth else None,
               "best_hours": [{"hour": h, "avg": v} for h, v in best_hours]}
    top = sorted(with_views, key=lambda u: -(u["views"] or 0))[:5]
    return {"kind": kind, "id": aid, "head": head, "summary": summary, "growth": growth[-90:], "cadence": cadence, "heat": heat,
            "origins": dict(origins), "posts": posts, "top": [{"title": u["title"], "views": u["views"], "video_id": u["external_video_id"]} for u in top],
            "has_views": kind == "youtube", "channel_id": ch_id}

