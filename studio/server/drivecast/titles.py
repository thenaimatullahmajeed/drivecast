"""Titles. Rule: never empty, never "Untitled", never a camera/file name.

Order: Instagram caption → a real file name → the channel's own title bank
(rotated, no repeats) → a neutral title built from the channel name.
"""
import hashlib
import os
import re
import unicodedata

BAD = re.compile(r"^(untitled|new video|video|img|vid|mov|dsc|pxl|screen ?recording|snapchat|whatsapp video|"
                 r"inshot|capcut|recording|copy of|final|edit|clip|reel)[\s_\-\d().]*$", re.I)
CAMERA = re.compile(r"^(img|vid|mov|dsc|dscn|pxl|gopr|mvi|wp|signal|video|vid-|photo)[_\-]?\d", re.I)
HASHTAG = re.compile(r"(?<!\w)#[\w؀-ۿ]+", re.U)
MENTION = re.compile(r"(?<!\w)@[\w.]+", re.U)
URL = re.compile(r"https?://\S+")
MAX_TITLE = 95


def _strip_emoji(s):
    return "".join(ch for ch in s if unicodedata.category(ch) not in ("So", "Cs", "Co", "Sk") and ch not in "‍️")


def tidy(s):
    s = URL.sub("", s or "")
    s = _strip_emoji(s)
    s = re.sub(r"[_]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -–—|•·.,:;!?\"'")
    return s.strip()


def usable(s):
    s = (s or "").strip()
    if len(s) < 3 or BAD.match(s) or CAMERA.match(s):
        return False
    if re.fullmatch(r"[\d\s_\-().]+", s):  # only numbers / dates
        return False
    letters = sum(ch.isalpha() for ch in s)
    return letters >= 3


def hashtags(text):
    return list(dict.fromkeys(h for h in HASHTAG.findall(text or "")))[:15]


def from_caption(caption):
    """First meaningful line of a caption, hashtags/mentions/emoji removed."""
    for line in (caption or "").splitlines():
        t = tidy(MENTION.sub("", HASHTAG.sub("", line)))
        if usable(t):
            return _cut(t)
    return None


def from_filename(name):
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    stem = re.sub(r"\s*\(\d+\)$", "", stem)            # "name (1)"
    stem = re.sub(r"\s*[-_]?(copy|final|v\d+)$", "", stem, flags=re.I)
    t = tidy(stem)
    return _cut(t) if usable(t) else None


def _cut(t):
    if len(t) <= MAX_TITLE:
        return t
    cut = t[:MAX_TITLE].rsplit(" ", 1)[0]
    return cut.rstrip(",;:-") or t[:MAX_TITLE]


def fallback(channel_title, bank, used, seed):
    """A natural title from the channel's bank, never repeating one already used."""
    pool = [b.strip() for b in (bank or []) if usable(b.strip())]
    for b in sorted(pool, key=lambda x: hashlib.md5(f"{seed}{x}".encode()).hexdigest()):
        if b not in used:
            return _cut(b)
    base = tidy(channel_title or "") or "New"
    variants = ["moments", "today", "new clip", "worth watching", "a closer look", "this one", "behind the scenes", "highlights"]
    for i in range(len(variants)):
        v = variants[(int(hashlib.md5(str(seed).encode()).hexdigest(), 16) + i) % len(variants)]
        t = f"{base} — {v}"
        if t not in used:
            return _cut(t)
    return _cut(f"{base} — {seed}")


def choose(name=None, caption=None, channel_title=None, bank=None, used=(), seed=""):
    return from_caption(caption) or from_filename(name) or fallback(channel_title, bank, set(used), seed)


def encore_title(title, n):
    """Re-post titles change a little so they are not identical, without misleading."""
    t = title or ""
    endings = [" (revisited)", " — watch again", " (fan favourite)"]
    return _cut(t + endings[(n - 1) % len(endings)])


def description(caption, title, is_short=True):
    tags = hashtags(caption)
    body = (caption or "").strip()
    if not body:
        body = title or ""
    parts = [body]
    if is_short and "#shorts" not in body.lower():
        parts.append("#Shorts")
    if tags and not any(t in body for t in tags):
        parts.append(" ".join(tags))
    return "\n\n".join(p for p in parts if p)[:4900]
