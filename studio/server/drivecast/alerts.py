"""Owner alerts: one list for the panel, the phone notification and the fleet hub.

Checked every 5 minutes. Each alert has a stable key, so the phone notifies only
once per problem and clears it when the problem is gone.
"""
import json
import time

from . import db, errors


def _a(code, key, level="error", **ctx):
    i = errors.info(code, **ctx)
    return {"key": key, "code": code, "level": level, "title": i["title"], "message": i["message"], "fix": i.get("fix")}


def collect():
    from . import fleet, store
    out = []
    now = time.time()
    if db.meta_get("superseded_by"):
        out.append(_a("PHONE_REPLACED", "replaced"))
    d = db.jload(db.meta_get("device_info"), {}) or {}
    if d.get("battery") is not None and d["battery"] <= 20 and not d.get("charging"):
        out.append(_a("BATTERY_LOW", "battery", level=d["battery"]))
    if (d.get("temp") or 0) >= 43:
        out.append(_a("PHONE_HOT", "hot", "warn", temp=round(d["temp"])))
    if db.meta_get("backup_test_error"):
        out.append(_a("BACKUP_TEST_FAILED", "backup", why=db.meta_get("backup_test_error")))
    owner = db.one("SELECT workspace_id FROM users WHERE role='owner' ORDER BY id LIMIT 1")
    if owner:
        lim = store.limits_for_workspace(owner["workspace_id"])
        used = store.uploads_today(owner["workspace_id"], now)
        if lim["daily_cap"] and used >= max(1, lim["daily_cap"] - 1) and db.one("SELECT 1 FROM channels WHERE youtube_on=1 AND platform='youtube' AND state='live'"):
            out.append(_a("QUOTA_NEAR", "quota", "warn", used=used, cap=lim["daily_cap"]))
    if fleet.role() == "hub":
        for dv in fleet.devices():
            mins = int((now - (dv["last_seen"] or now)) / 60)
            if not dv["online"]:
                out.append(_a("PHONE_OFFLINE", "off:" + dv["host"], host=dv["host"], mins=mins))
            elif dv["max_users"] and dv["users"] >= 0.85 * dv["max_users"]:
                out.append(_a("PHONE_FULL", "full:" + dv["host"], "warn", host=dv["host"], users=dv["users"], max=dv["max_users"]))
    return out


def tick():
    al = collect()
    old = {a["key"] for a in db.jload(db.meta_get("alerts_json"), []) or []}
    for a in al:
        if a["key"] not in old:
            db.event(a["level"], "alert", f"{a['title']}. {a['message']}", None, None, a["code"])
    db.meta_set("alerts_json", json.dumps(al))
    db.meta_set("alerts_at", str(time.time()))
    return al
