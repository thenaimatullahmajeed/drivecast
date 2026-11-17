"""Error catalogue. Every external failure is turned into one stable code with a
plain message, the button that fixes it, and the scope it is allowed to stop.

Scope rules (bulkhead):
  post     → only this video is affected
  channel  → only this channel waits
  source   → only this channel's Instagram source waits
  ig       → every Instagram source using this group's Instagram account waits
  group    → every channel on this IP group waits (never falls back to the phone IP)
  library  → the workspace's Drive connection (all Drive work in the workspace waits)
  server   → the phone itself (ffmpeg missing, disk full)
"""

CATALOG = {
    # --- proxy ---
    "PROXY_DEAD": ("group", "Proxy not responding", "Group {group} does not answer. Its channels are held — nothing leaves from the phone’s own IP.", "replace_proxy"),
    "PROXY_SLOW": ("group", "Proxy is slow", "Group {group} answers slowly ({ms} ms). Uploads may fail. Consider replacing it.", "replace_proxy"),
    "PROXY_IP_CHANGED": ("group", "Proxy IP changed", "Group {group} now leaves from a different IP. Confirm it before posting continues.", "confirm_ip"),
    # --- Instagram ---
    "IG_NO_ACCOUNT": ("ig", "No Instagram account on this group", "Group {group} has no Instagram cookies yet. Add them to start pulling posts.", "add_cookies"),
    "IG_COOKIES_EXPIRED": ("ig", "Instagram sign-in expired", "The Instagram account on Group {group} is signed out. Paste fresh cookies.", "replace_cookies"),
    "IG_CHALLENGE": ("ig", "Instagram wants a check", "Instagram asked the account on Group {group} to confirm it’s you. Open Instagram in a browser on that account, complete the check, then paste fresh cookies.", "replace_cookies"),
    "IG_RATE_LIMIT": ("ig", "Instagram asked us to slow down", "Group {group}’s Instagram account is resting until {until}. Nothing to do — it resumes by itself.", None),
    "IG_FEEDBACK": ("ig", "Instagram blocked actions for a while", "Instagram paused the account on Group {group} (“feedback required”). It rests for some hours; if it repeats, use another test account.", "replace_cookies"),
    "IG_HOME_REDIRECT": ("source", "Instagram hid this profile", "Instagram sent us to its home page for @{handle}. Open the profile in a browser; if it opens, wait a few hours.", "check_source"),
    "IG_PROFILE_NOT_FOUND": ("source", "Instagram profile not found", "@{handle} does not exist or was renamed. Update the handle.", "edit_source"),
    "IG_PROFILE_PRIVATE": ("source", "Instagram profile is private", "@{handle} is private. Follow it from the group’s test account, or ask the client to make it public.", "edit_source"),
    "IG_MEDIA_GONE": ("post", "Post removed from Instagram", "This post was deleted or hidden on Instagram before we could download it.", None),
    "IG_UNKNOWN": ("source", "Instagram returned an unexpected answer", "Instagram answered in an unexpected way for @{handle}. It will retry later.", None),
    # --- media ---
    "LOW_QUALITY": ("post", "Skipped: low quality", "Below 720p — not posted, so a channel never gets a blurry video.", None),
    "NO_AUDIO": ("post", "Skipped: no sound", "This video has no audio track.", None),
    "TOO_SHORT": ("post", "Skipped: too short", "Shorter than 3 seconds.", None),
    "CLEAN_FAILED": ("post", "Could not clean the file", "Removing hidden data from this file failed, so it was not posted (privacy first).", None),
    "FFMPEG_MISSING": ("server", "Video tools missing", "The video tool inside the app could not start. Reinstall the latest APK.", "update_app"),
    "DISK_FULL": ("server", "Phone storage is full", "Free up space on the phone. Downloads are paused.", None),
    # --- Google Drive ---
    "DRIVE_NOT_CONNECTED": ("library", "Google Drive not connected", "Connect your Drive in Google Drive settings so files have a home.", "connect_drive"),
    "DRIVE_AUTH_REVOKED": ("library", "Google Drive access ended", "Reconnect your Google Drive.", "connect_drive"),
    "DRIVE_QUOTA": ("library", "Google Drive is busy", "Google Drive asked us to slow down. It retries by itself.", None),
    "DRIVE_FULL": ("library", "Google Drive is full", "Your Drive storage is full. Free space or upgrade.", None),
    "DRIVE_FOLDER_GONE": ("channel", "Drive folder missing", "This channel’s Drive folder was moved or deleted. Choose it again.", "edit_source"),
    # --- Facebook ---
    "FB_NO_APP": ("library", "Facebook app not set", "Add your Meta app's App ID and secret in Settings before connecting Facebook.", "fb_app"),
    "FB_NO_ACCOUNT": ("fb", "No Facebook connected on this group", "Group {group} has no Facebook account yet. Connect one to post to its pages.", "connect_facebook"),
    "FB_TOKEN_EXPIRED": ("fb", "Facebook sign-in expired", "The Facebook connection on Group {group} ended. Reconnect it (one Allow).", "reconnect_facebook"),
    "FB_PERMISSION": ("channel", "Facebook permission missing", "Facebook refused: {perm}. Reconnect and accept all the page permissions, and make sure you are an admin of this page.", "reconnect_facebook"),
    "FB_RATE_LIMIT": ("fb", "Facebook asked us to slow down", "Group {group}'s Facebook is resting briefly. It resumes by itself.", None),
    "FB_BLOCKED": ("fb", "Facebook paused this action", "Facebook temporarily blocked posting for policy reasons. It will retry later; if it repeats, check the page in Facebook.", None),
    "FB_BUSY": ("post", "Facebook is busy", "Facebook did not answer. It will retry.", None),
    "FB_NO_PAGE": ("channel", "No Facebook page linked", "Choose which page this channel posts to.", "edit_source"),
    "FB_REJECTED": ("post", "Facebook refused this post", "Facebook refused the post: {why}", None),
    # --- YouTube ---
    "YT_AUTH_REVOKED": ("channel", "YouTube access ended", "Reconnect this channel — Google access was removed.", "reconnect_channel"),
    "YT_QUOTA": ("channel", "YouTube daily limit reached", "Posting resumes after midnight Pacific.", None),
    "YT_UNCERTAIN": ("post", "Upload result unknown", "The connection dropped mid-upload. Check YouTube Studio — it is never retried by itself.", "resolve_upload"),
    "YT_REJECTED": ("post", "YouTube refused this video", "YouTube refused the file. It was moved to Failed.", None),
}


def info(code, **ctx):
    scope, title, msg, fix = CATALOG.get(code, ("post", "Problem", code, None))
    safe = {k: ("" if v is None else v) for k, v in ctx.items()}
    try:
        msg = msg.format_map(_Default(safe))
    except Exception:
        pass
    return {"code": code, "scope": scope, "title": title, "message": msg, "fix": fix}


class _Default(dict):
    def __missing__(self, key):
        return "?"


class CodedError(Exception):
    """A failure that already knows its catalogue code."""

    def __init__(self, code, detail=None, **ctx):
        self.code, self.detail, self.ctx = code, (detail or "")[:2000], ctx
        super().__init__(f"{code}: {self.detail[:200]}")

    @property
    def scope(self):
        return CATALOG.get(self.code, ("post",))[0]

    def info(self, **extra):
        return info(self.code, **{**self.ctx, **extra})
