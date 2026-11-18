"""AI captions with Groq — optional, never in the way.

What it does for a post (each part only when its switch is on for the route):
  * listens to the video (Whisper) so the text matches what is actually said
  * writes a YouTube title + description, a Facebook caption and an Instagram caption + hashtags
  * keeps captions different from the channel's recent posts
  * writes in the route's language and the client's brand voice
  * a check before posting: abuse, false claims, someone else's brand → the post is held
  * uploads subtitles to YouTube (when the channel allowed it)
  * a short weekly summary per client

Keys: each IP group may carry the client's own Groq key; otherwise the owner's key from
Settings. No key, a refused key, the daily limit or any API problem → the post goes out
with its original text. Only the check can hold a post, and only when it is sure.
"""
import json
import logging
import os
import re
import subprocess
import time

from . import db, security, store

log = logging.getLogger("drivecast.ai")

API = "https://api.groq.com/openai/v1"
AUTO = "auto"  # pick the best model Groq offers right now (models come and go)
PREFER = ("openai/gpt-oss-120b", "gpt-oss-120b", "70b", "qwen", "gpt-oss-20b", "llama", "")
NOT_CHAT = ("whisper", "guard", "tts", "playai", "orpheus", "distil", "compound", "embed")
FALLBACK_MODEL = "openai/gpt-oss-120b"
TEXT_PER_DAY = 900        # Groq free plan allows about 1,000 text requests a day per account
AUDIO_PER_DAY = 27000     # seconds; free plan allows 28,800
MAX_AUDIO_S = 600         # first 10 minutes are plenty for captions and Shorts subtitles
LANGUAGES = ["auto", "English", "Urdu", "Hindi", "Arabic", "Spanish", "Portuguese", "French", "Turkish",
             "Indonesian", "Bengali", "Japanese", "German"]
ISO = {"english": "en", "urdu": "ur", "hindi": "hi", "arabic": "ar", "spanish": "es", "portuguese": "pt",
       "french": "fr", "turkish": "tr", "indonesian": "id", "bengali": "bn", "japanese": "ja", "german": "de",
       "punjabi": "pa", "persian": "fa", "russian": "ru", "italian": "it", "korean": "ko", "chinese": "zh"}
FORCE_SSL = "https://www.googleapis.com/auth/youtube.force-ssl"
DEFAULTS = {"captions": False, "subtitles": False, "check": False, "language": "auto", "voice": ""}


class AiError(Exception):
    pass


# ---------------------------------------------------------------- settings and keys
def settings(ch):
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in db.jload(ch.get("ai_json"), {}).items() if k in DEFAULTS})
    return cfg


def normalize(b, old=None):
    cfg = dict(old or DEFAULTS)
    for k in ("captions", "subtitles", "check"):
        if k in b:
            cfg[k] = bool(b[k])
    if "language" in b:
        cfg["language"] = b["language"] if b["language"] in LANGUAGES else "auto"
    if "voice" in b:
        cfg["voice"] = re.sub(r"\s+", " ", str(b["voice"] or "")).strip()[:300]
    return cfg


def key_for(ch=None, ws=None, g=None):
    """(key, scope, where) — the group's own key first, then the owner's. None when there is no key."""
    if ch is not None:
        g = store.group(ch.get("group_id"))
        ws = store.workspace(ch["workspace_id"])
    if g and g.get("groq_key_enc"):
        return security.unseal(g["groq_key_enc"]), f"g{g['id']}", f"Group {g['number']}"
    if ws and ws.get("groq_key_enc"):
        return security.unseal(ws["groq_key_enc"]), f"ws{ws['id']}", "your Settings"
    return None


def known():
    """The model list Groq gave us last time (refreshed when a key is saved or a model disappears)."""
    return db.jload(db.meta_get("ai_models"), {}) or {}


def best(chat):
    for p in PREFER:
        for i in chat:
            if p in i.lower():
                return i
    return None


def model(ws):
    chosen = (ws or {}).get("ai_model")
    chat = known().get("chat") or []
    if chosen and chosen != AUTO and (not chat or chosen in chat):
        return chosen
    return best(chat) or FALLBACK_MODEL


def whisper():
    stt = known().get("stt") or []
    for p in ("whisper-large-v3-turbo", "whisper-large-v3"):
        if p in stt:
            return p
    return stt[0] if stt else "whisper-large-v3-turbo"


def fetch_models(key):
    """Ask Groq which models this key can use today."""
    try:
        code, _h, text = _get(key, "/models")
    except Exception as e:
        raise AiError(f"Could not reach Groq: {e}")
    if code == 401:
        raise AiError("Groq did not accept this key")
    if code >= 400:
        raise AiError(f"Groq answered {code}: {text[:160]}")
    ids = [m.get("id") for m in (json.loads(text).get("data") or []) if m.get("id") and m.get("active", True) is not False]
    chat = sorted(i for i in ids if not any(x in i.lower() for x in NOT_CHAT))
    stt = sorted(i for i in ids if "whisper" in i.lower())
    if chat:
        db.meta_set("ai_models", json.dumps({"chat": chat, "stt": stt, "at": time.time()}))
    return chat, stt


class ModelGone(AiError):
    pass


def _day():
    return time.strftime("%Y-%m-%d", time.gmtime())  # Groq's daily limits reset on UTC days


def usage(scope):
    r = db.one("""SELECT COALESCE(SUM(CASE WHEN kind!='audio' THEN 1 ELSE 0 END),0) text,
                         COALESCE(SUM(audio_s),0) audio, COALESCE(SUM(tokens),0) tokens,
                         COALESCE(SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END),0) failed
                  FROM ai_log WHERE scope=? AND day=? AND kind NOT IN ('flag')""", (scope, _day()))
    blocked = float(db.meta_get("ai_block:" + scope) or 0)
    return {"text": r["text"], "text_limit": TEXT_PER_DAY, "audio_s": round(r["audio"]), "audio_limit": AUDIO_PER_DAY,
            "tokens": r["tokens"], "failed": r["failed"], "resting_until": blocked if blocked > time.time() else None,
            "key_refused": db.meta_get("ai_bad:" + scope) == "1"}


def _log(scope, ch, kind, ok=True, tokens=0, audio_s=0, file_key=None, note=None):
    db.run("""INSERT INTO ai_log(at,day,scope,workspace_id,channel_id,kind,tokens,audio_s,ok,file_key,note)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
           (time.time(), _day(), scope, ch and ch["workspace_id"], ch and ch["id"], kind, int(tokens or 0),
            float(audio_s or 0), 1 if ok else 0, file_key, (note or "")[:300] or None))


# ---------------------------------------------------------------- HTTP (tests replace _post)
def _session():
    from .net import session_for
    return session_for(None)  # no client identity involved: the phone's own internet is fine


def _post(key, path, json_body=None, files=None, data=None, timeout=60):
    s = _session()
    r = s.post(API + path, headers={"Authorization": "Bearer " + key}, json=json_body, files=files, data=data, timeout=timeout)
    return r.status_code, dict(r.headers), r.text


def _get(key, path, timeout=30):
    s = _session()
    r = s.get(API + path, headers={"Authorization": "Bearer " + key}, timeout=timeout)
    return r.status_code, dict(r.headers), r.text


def _call(key, scope, path, ch=None, **kw):
    if float(db.meta_get("ai_block:" + scope) or 0) > time.time():
        raise AiError("resting")
    try:
        code, headers, text = _post(key, path, **kw)
    except Exception as e:
        raise AiError(f"network: {e}")
    if code == 401:
        if db.meta_get("ai_bad:" + scope) != "1":
            db.meta_set("ai_bad:" + scope, "1")
            ws = ch and ch["workspace_id"]
            db.event("error", "ai", "Groq did not accept the AI key. Posts go out with their normal text until you paste a new key.",
                     ws, None, "AI_KEY_BAD")
        raise AiError("key refused")
    if code == 429:
        wait = 0
        for h in ("retry-after", "Retry-After"):
            try:
                wait = float(headers.get(h) or 0)
            except ValueError:
                wait = 0
        db.meta_set("ai_block:" + scope, str(time.time() + max(60, min(wait or 1800, 6 * 3600))))
        raise AiError("rate limited")
    if code in (400, 404) and ("model_not_found" in text or "decommissioned" in text or "does not exist" in text):
        try:
            fetch_models(key)  # Groq retired the model: learn today's list, the caller retries once
        except AiError:
            pass
        raise ModelGone("model retired")
    if code >= 400:
        raise AiError(f"HTTP {code}: {text[:200]}")
    db.meta_set("ai_bad:" + scope, "0")
    return json.loads(text)


def chat(key, scope, ws, messages, ch=None, max_tokens=900, as_json=True):
    def ask():
        body = {"model": model(ws), "messages": messages, "temperature": 0.8, "max_tokens": max_tokens}
        if as_json:
            body["response_format"] = {"type": "json_object"}
        return _call(key, scope, "/chat/completions", ch=ch, json_body=body)
    try:
        out = ask()
    except ModelGone:
        out = ask()
    msg = out["choices"][0]["message"]["content"] or ""
    tokens = (out.get("usage") or {}).get("total_tokens") or 0
    return (json.loads(_json_part(msg)) if as_json else msg.strip()), tokens


def _json_part(s):
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-z]*\s*|\s*```$", "", s)
    i, j = s.find("{"), s.rfind("}")
    return s[i:j + 1] if i >= 0 and j > i else s


# ---------------------------------------------------------------- listening (Whisper)
def extract_audio(video, out_dir):
    from . import cleaner
    exe = cleaner.ffmpeg_path()
    if not exe:
        raise AiError("ffmpeg missing")
    out = os.path.join(out_dir, "voice.flac")
    p = subprocess.run([exe, "-y", "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", "16000", "-t", str(MAX_AUDIO_S),
                        "-c:a", "flac", out], capture_output=True, timeout=180)
    if p.returncode != 0 or not os.path.isfile(out) or os.path.getsize(out) < 2000:
        raise AiError("no usable audio")
    return out


def transcribe(key, scope, audio, ch=None):
    def ask():
        with open(audio, "rb") as fh:
            return _call(key, scope, "/audio/transcriptions", ch=ch, files={"file": ("voice.flac", fh, "audio/flac")},
                         data={"model": whisper(), "response_format": "verbose_json"}, timeout=120)
    try:
        out = ask()
    except ModelGone:
        out = ask()
    segs = [{"start": float(s.get("start") or 0), "end": float(s.get("end") or 0), "text": (s.get("text") or "").strip()}
            for s in out.get("segments") or [] if (s.get("text") or "").strip()]
    return {"text": (out.get("text") or "").strip(), "language": (out.get("language") or "").lower(),
            "duration": float(out.get("duration") or 0), "segments": segs}


def srt(segments):
    def ts(t):
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    lines = []
    for i, s in enumerate(segments, 1):
        end = s["end"] if s["end"] > s["start"] else s["start"] + 2
        lines += [str(i), f"{ts(s['start'])} --> {ts(end)}", s["text"], ""]
    return "\n".join(lines)


def lang_code(name):
    name = (name or "").lower().strip()
    if len(name) == 2:
        return name
    return ISO.get(name)


# ---------------------------------------------------------------- writing
def _clean(s, limit):
    s = re.sub(r"[<>]", "", str(s or "")).replace("\r", "")
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s[:limit].strip()


def _tags(tags):
    out = []
    for t in tags or []:
        t = re.sub(r"[^\w]", "", str(t).lstrip("#"), flags=re.UNICODE)
        if t and t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return ["#" + t for t in out[:15]]  # Instagram allows 30; 15 reads as human, not spam


def sanitize(raw, base_title, base_desc, used):
    yt = raw.get("youtube") or {}
    title = _clean(yt.get("title"), 100) or base_title
    if title.lower() in {u.lower() for u in used}:
        title = base_title
    desc = _clean(yt.get("description"), 4800) or base_desc
    fb = _clean((raw.get("facebook") or {}).get("caption"), 3000) or desc
    ig = raw.get("instagram") or {}
    tags = " ".join(_tags(ig.get("hashtags")))
    igc = _clean(ig.get("caption"), 2100 - len(tags)) or _clean(base_desc, 1800)
    igc = (igc + ("\n\n" + tags if tags else "")).strip()
    chk = raw.get("check") or {}
    return {"youtube": (title, desc), "facebook": (title, fb), "instagram": (title, igc),
            "flag": None if chk.get("ok", True) else (_clean(chk.get("reason"), 200) or "the text looked unsafe")}


PROMPT = """You write social media text for one client's posts. Reply with JSON only.
Client channel: {channel}
Write in: {language}
Brand voice: {voice}
Platforms to write for: {kinds}
Recent titles on this channel (do not repeat or closely copy them): {recent}

The post:
- Original title: {title}
- Original caption: {caption}
- What is said in the video (may be empty): {transcript}

Rules:
- Base every claim on the post itself. Never invent facts, numbers, names, prices or links.
- YouTube title: max 90 characters, natural, no clickbait in capitals, no emojis spam, no angle brackets.
- YouTube description: 2–4 short lines then 3–5 hashtags.
- Facebook caption: 1–3 friendly lines.
- Instagram caption: 1–3 lines; hashtags go in the separate list (8–15, relevant, no spaces).
- Keep the client's own @mentions only if they are in the original caption.
{check_rule}
JSON shape:
{{"youtube": {{"title": "", "description": ""}}, "facebook": {{"caption": ""}},
  "instagram": {{"caption": "", "hashtags": []}}, "check": {{"ok": true, "reason": ""}}}}"""

CHECK_RULE = ("- Also check the post. Set check.ok=false ONLY if the original title/caption/speech clearly contains: "
              "hate or abuse, sexual content, a dangerous or clearly false health/money claim, or uses another company's "
              "brand as if the client owned it. Otherwise check.ok=true. Give a short reason when false.")


def prepare(ch, f, m, base_title, base_desc, kinds, video=None, tmp=None, file_key=None, photo=False):
    """Everything AI for one post. Returns a pack or None. Never raises except the check's hold."""
    cfg = settings(ch)
    if not (cfg["captions"] or cfg["subtitles"] or cfg["check"]):
        return None
    k = key_for(ch)
    if not k:
        return None
    key, scope, _where = k
    ws = store.workspace(ch["workspace_id"])
    pack = {"scope": scope, "texts": {}, "srt": None, "lang": None, "flag": None}
    use = usage(scope)
    heard = None
    if video and not photo and (cfg["captions"] or cfg["subtitles"]) and use["audio_s"] < AUDIO_PER_DAY and tmp:
        try:
            heard = transcribe(key, scope, extract_audio(video, tmp), ch)
            _log(scope, ch, "audio", audio_s=heard["duration"], file_key=file_key)
            if cfg["subtitles"] and heard["segments"]:
                pack["srt"] = srt(heard["segments"])
                pack["lang"] = lang_code(cfg["language"]) if cfg["language"] != "auto" else lang_code(heard["language"])
        except AiError as e:
            if str(e) not in ("no usable audio",):
                _log(scope, ch, "audio", ok=False, file_key=file_key, note=str(e))
        except Exception as e:  # never in the way
            log.warning("transcribe failed: %s", e)
    if not (cfg["captions"] or cfg["check"]) or use["text"] >= TEXT_PER_DAY:
        return pack
    if cfg["check"] and approved(ch, file_key):
        cfg["check"] = False  # you moved it back after a hold: that is your OK
        if not cfg["captions"]:
            return pack
    used = [r["title"] for r in db.all("SELECT title FROM uploads WHERE channel_id=? AND status='published' AND title IS NOT NULL "
                                       "ORDER BY id DESC LIMIT 12", (ch["id"],))]
    caption = (m or {}).get("caption") or ""
    prompt = PROMPT.format(channel=ch["title"], language=cfg["language"] if cfg["language"] != "auto" else
                           "the same language as the post (English if unclear)",
                           voice=cfg["voice"] or "clear, warm and natural", kinds=", ".join(kinds) or "youtube",
                           recent="; ".join(used) or "none", title=base_title, caption=caption[:1500] or "none",
                           transcript=(heard or {}).get("text", "")[:2500] or "none",
                           check_rule=CHECK_RULE if cfg["check"] else "- check.ok is always true.")
    try:
        raw, tokens = chat(key, scope, ws, [{"role": "user", "content": prompt}], ch=ch)
        _log(scope, ch, "text", tokens=tokens, file_key=file_key)
    except Exception as e:
        _log(scope, ch, "text", ok=False, file_key=file_key, note=str(e))
        return pack
    s = sanitize(raw, base_title, base_desc, used)
    if cfg["captions"]:
        pack["texts"] = {k2: s[k2] for k2 in ("youtube", "facebook", "instagram")}
    if cfg["check"] and s["flag"]:
        _log(scope, ch, "flag", file_key=file_key, note=s["flag"])
        from .errors import CodedError
        raise CodedError("AI_HOLD", s["flag"], why=s["flag"])
    return pack


def approved(ch, file_key):
    return bool(file_key and db.one("SELECT 1 FROM ai_log WHERE channel_id=? AND kind='flag' AND file_key=?", (ch["id"], file_key)))


def text_for(pack, kind, title, desc):
    if pack and pack.get("texts", {}).get(kind):
        return pack["texts"][kind]
    return title, desc


def can_subtitle(ch):
    return FORCE_SSL in (ch.get("yt_scope") or "")


def after_youtube(pack, ch, session, video_id):
    """Subtitles for a YouTube post that just went live. Problems only leave a note."""
    if not pack or not pack.get("srt") or not video_id:
        return False
    if not can_subtitle(ch):
        return False
    from .google import authed
    try:
        box = store.channel_box(ch)
        boundary = "dc" + security.token(12)
        meta = {"snippet": {"videoId": video_id, "language": pack.get("lang") or "en", "name": "", "isDraft": False}}
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
                f"--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n{pack['srt']}\r\n--{boundary}--\r\n").encode()
        r = authed(session, box, "POST", "https://www.googleapis.com/upload/youtube/v3/captions",
                   params={"part": "snippet", "uploadType": "multipart"},
                   headers={"Content-Type": f"multipart/related; boundary={boundary}"}, data=body, timeout=(20, 120))
        if r.status_code >= 300:
            raise AiError(f"HTTP {r.status_code}: {r.text[:160]}")
        _log(pack["scope"], ch, "subs", note=video_id)
        return True
    except Exception as e:
        _log(pack["scope"], ch, "subs", ok=False, note=str(e))
        db.event("warn", "ai", f"{ch['title']}: posted, but the subtitles could not be added ({e}).", ch["workspace_id"], ch["id"])
        return False


# ---------------------------------------------------------------- weekly summary
def _week():
    return time.strftime("%G-W%V", time.gmtime())


def report_facts(ch, since):
    rows = db.all("""SELECT destination, status, COUNT(*) n FROM uploads WHERE channel_id=? AND reserved_at>=?
                     GROUP BY destination, status""", (ch["id"], since))
    per = {}
    for r in rows:
        d = (r["destination"] or "").split(":")[0] or "youtube"
        per.setdefault(d, {})[r["status"]] = r["n"]
    errs = [r["message"] for r in db.all("""SELECT message FROM events WHERE channel_id=? AND at>=? AND level IN ('error','warn')
                                            ORDER BY id DESC LIMIT 5""", (ch["id"], since))]
    top = db.all("""SELECT title, destination FROM uploads WHERE channel_id=? AND status='published' AND published_at>=?
                    ORDER BY id DESC LIMIT 5""", (ch["id"], since))
    return {"client": ch["title"], "subscribers": ch.get("subscribers"), "views_total": ch.get("total_views"),
            "posts_by_platform": per, "recent_posts": [t["title"] for t in top], "problems": errs}


def weekly(force_channel=None):
    """Once a week per client with AI on: a short summary in its language."""
    week = _week()
    q = "SELECT * FROM channels WHERE ai_json IS NOT NULL" + (" AND id=?" if force_channel else "")
    made = 0
    for ch in db.all(q, (force_channel,) if force_channel else ()):
        cfg = settings(ch)
        if not (cfg["captions"] or cfg["subtitles"] or cfg["check"]) and not force_channel:
            continue
        if not force_channel and db.one("SELECT 1 FROM ai_reports WHERE channel_id=? AND week=?", (ch["id"], week)):
            continue
        k = key_for(ch)
        if not k:
            continue
        key, scope, _ = k
        if usage(scope)["text"] >= TEXT_PER_DAY:
            continue
        facts = report_facts(ch, time.time() - 7 * 86400)
        lang = cfg["language"] if cfg["language"] != "auto" else "English"
        try:
            text, tokens = chat(key, scope, store.workspace(ch["workspace_id"]), [{"role": "user", "content":
                                f"Write a short weekly report (4–6 plain sentences, no headings, no invented numbers) in {lang} "
                                f"for the owner of this social media client, from these facts only:\n{json.dumps(facts)}"}],
                                ch=ch, max_tokens=400, as_json=False)
            _log(scope, ch, "report", tokens=tokens)
        except Exception as e:
            _log(scope, ch, "report", ok=False, note=str(e))
            continue
        db.run("INSERT OR REPLACE INTO ai_reports(channel_id,week,text,created_at) VALUES(?,?,?,?)",
               (ch["id"], week, text[:3000], time.time()))
        made += 1
    return made


def latest_report(ch_id):
    return db.one("SELECT week, text, created_at FROM ai_reports WHERE channel_id=? ORDER BY created_at DESC LIMIT 1", (ch_id,))


def test_key(key, ws):
    """Proves the key works and learns which models it may use. Returns the model that will write."""
    chat, _stt = fetch_models(key)
    if not chat:
        raise AiError("Groq lists no text models for this key")
    use = model(ws)
    code, _h, text = _post(key, "/chat/completions", json_body={
        "model": use, "messages": [{"role": "user", "content": "Reply with OK"}], "max_tokens": 5}, timeout=30)
    if code == 401:
        raise AiError("Groq did not accept this key")
    if code == 429:
        return use  # the key works, it is only at its limit right now
    if code >= 400:
        raise AiError(f"Groq answered {code}: {text[:160]}")
    return use


def service():
    try:
        weekly()
    except Exception as e:
        log.warning("weekly AI reports: %s", e)
