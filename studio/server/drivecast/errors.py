"""Error catalogue. Every external failure is turned into one stable code with a
plain message, the button that fixes it, and the scope it is allowed to stop.

Scope rules (bulkhead):
  post     → only this video is affected
  channel  → only this channel waits
  source   → only this channel's Instagram source waits
  ig       → every Instagram source using this pool's Instagram account waits
  group    → every channel on this IP group waits (never falls back to the phone IP)
  library  → the workspace's Drive connection (all Drive work in the workspace waits)
  server   → the phone itself (ffmpeg missing, disk full)
"""

CATALOG = {
    # --- proxy ---
    "PROXY_DEAD": ("group", "Proxy not responding", "Pool {group} does not answer. Its channels are held — nothing leaves from the phone’s own IP.", "replace_proxy"),
    "PROXY_SLOW": ("group", "Proxy is slow", "Pool {group} answers slowly ({ms} ms). Uploads may fail. Consider replacing it.", "replace_proxy"),
    "PROXY_IP_CHANGED": ("group", "Proxy IP changed", "Pool {group} now leaves from a different IP. Confirm it before posting continues.", "confirm_ip"),
    # --- Instagram ---
    "IG_NO_ACCOUNT": ("ig", "No Instagram account on this pool", "Pool {group} has no Instagram cookies yet. Add them to start pulling posts.", "add_cookies"),
    "IG_COOKIES_EXPIRED": ("ig", "Instagram sign-in expired", "The Instagram account on Pool {group} is signed out. Paste fresh cookies.", "replace_cookies"),
    "IG_CHALLENGE": ("ig", "Instagram wants a check", "Instagram asked the account on Pool {group} to confirm it’s you. Open Instagram in a browser on that account, complete the check, then paste fresh cookies.", "replace_cookies"),
    "IG_RATE_LIMIT": ("ig", "Instagram asked us to slow down", "Pool {group}’s Instagram account is resting until {until}. Nothing to do — it resumes by itself.", None),
    "IG_FEEDBACK": ("ig", "Instagram blocked actions for a while", "Instagram paused the account on Pool {group} (“feedback required”). It rests for some hours; if it repeats, use another test account.", "replace_cookies"),
    "IG_HOME_REDIRECT": ("source", "Instagram hid this profile", "Instagram sent us to its home page for @{handle}. Open the profile in a browser; if it opens, wait a few hours.", "check_source"),
    "IG_PROFILE_NOT_FOUND": ("source", "Instagram profile not found", "@{handle} does not exist or was renamed. Update the handle.", "edit_source"),
    "IG_PROFILE_PRIVATE": ("source", "Instagram profile is private", "@{handle} is private. Follow it from the pool’s test account, or ask the client to make it public.", "edit_source"),
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
    "FB_NO_ACCOUNT": ("fb", "No Facebook connected on this pool", "Pool {group} has no Facebook account yet. Connect one to post to its pages.", "connect_facebook"),
    "FB_TOKEN_EXPIRED": ("fb", "Facebook sign-in expired", "The Facebook connection on Pool {group} ended. Reconnect it (one Allow).", "reconnect_facebook"),
    "FB_PERMISSION": ("channel", "Facebook permission missing", "Facebook refused: {perm}. Reconnect and accept all the page permissions, and make sure you are an admin of this page.", "reconnect_facebook"),
    "FB_RATE_LIMIT": ("fb", "Facebook asked us to slow down", "Pool {group}'s Facebook is resting briefly. It resumes by itself.", None),
    "FB_BLOCKED": ("fb", "Facebook paused this action", "Facebook temporarily blocked posting for policy reasons. It will retry later; if it repeats, check the page in Facebook.", None),
    "FB_BUSY": ("post", "Facebook is busy", "Facebook did not answer. It will retry.", None),
    "FB_NO_PAGE": ("channel", "No Facebook page linked", "Choose which page this channel posts to.", "edit_source"),
    "FB_REJECTED": ("post", "Facebook refused this post", "Facebook refused the post: {why}", None),
    # --- YouTube / TikTok as a SOURCE (reading a client's own public channel) ---
    "VID_NOT_FOUND": ("source", "Profile link not found", "{site} could not find {handle}. Check the link.", "edit_source"),
    "VID_LOGIN_NEEDED": ("source", "{site} wants a signed-in browser", "{site} would not list {handle} without sign-in. Add cookies from a test account on this source, or paste video links by hand.", "source_cookies"),
    "VID_BOT_CHECK": ("source", "{site} asked for a bot check", "{site} wants to confirm we are not a bot. It retries in a few hours; switch reading to the phone's internet, or add cookies, if it repeats.", "source_cookies"),
    "VID_RATE_LIMIT": ("source", "{site} asked us to slow down", "{site} is resting this source for a while. It resumes by itself.", None),
    "VID_GONE": ("post", "Video no longer available", "This video was removed, made private or is region-locked.", None),
    "VID_LIVE": ("post", "Skipped: live stream", "Live streams and premieres are skipped until they become normal videos.", None),
    "YTDLP_MISSING": ("server", "Video downloader missing", "The downloader inside the app could not start. Press Update downloader on the Server page.", "update_downloader"),
    "YTDLP_OLD": ("server", "Video downloader needs an update", "{site} changed something. Press Update downloader on the Server page.", "update_downloader"),
    # --- Instagram as a DESTINATION (official Content Publishing API) ---
    "IGD_TOKEN_EXPIRED": ("channel", "Instagram posting access ended", "Reconnect the Instagram destination of this client (one Allow).", "connect_ig_dest"),
    "IGD_NOT_PRO": ("channel", "Instagram account is not Professional", "Publishing needs an Instagram Business or Creator account. Switch it in the Instagram app (Settings › Account type).", "connect_ig_dest"),
    "IGD_LIMIT": ("channel", "Instagram daily post limit reached", "Instagram allows a limited number of API posts per 24 hours. Posting resumes by itself.", None),
    "IGD_MEDIA": ("post", "Instagram refused this file", "Instagram could not use this file: {why}", None),
    "IGD_NO_PUBLIC_URL": ("server", "No public link for Instagram", "Instagram fetches the file from your panel's public link. Turn on the public link (Server page) first.", None),
    "IGD_NO_APP": ("library", "Instagram app not set", "Add your Meta app's Instagram App ID and secret in Settings, or connect Instagram through the Facebook page instead.", "ig_app"),
    # --- phone / fleet health ---
    "PHONE_REPLACED": ("server", "This phone was replaced", "A backup was restored on another phone, which now runs this address. Posting is stopped here so nothing posts twice.", None),
    "PHONE_OFFLINE": ("server", "Phone {host} is offline", "{host} has not checked in for {mins} minutes. Its clients are on hold until it is back (plug it in, check Wi-Fi), or restore its backup on a spare phone.", None),
    "PHONE_FULL": ("server", "Phone {host} is nearly full", "{host} holds {users} of {max} accounts. Add a phone before it fills up — new sign-ups go to phones with room.", None),
    "PHONE_HOT": ("server", "Phone is hot", "The phone is at {temp}°C. Take it out of its case, out of the sun, or add a small fan.", None),
    "BATTERY_LOW": ("server", "Phone battery is low", "Battery at {level}% and not charging. Plug the phone in — the server stops when it dies.", None),
    "QUOTA_NEAR": ("library", "YouTube daily limit almost used", "{used} of {cap} uploads used today. The rest wait until midnight Pacific.", None),
    "BACKUP_TEST_FAILED": ("server", "Backup test failed", "The latest backup in Drive could not be opened: {why}. Make a new backup now (Server page).", None),
    "FB_GRAPH_OLD": ("library", "Meta API version retired", "Meta no longer accepts API version {ver}. Open Settings → Meta app and set a newer version (e.g. the one Meta's changelog lists as current).", "fb_app"),
    # --- AI (Groq) ---
    "YT_REMOVED": ("channel", "YouTube rejected a post", "YouTube rejected or removed a post. Open Videos to see which one and why.", None),
    "VID_DUPLICATE": ("post", "Same video already posted", "This video was already posted on this channel, so it was skipped.", None),
    "AI_HOLD": ("post", "Held by the AI check", "The AI check stopped this post: {why}. It is in Failed — move it back into the client's folder to post it anyway.", None),
    "AI_KEY_BAD": ("library", "AI key not accepted", "Groq refused the AI key. Posts go out with their normal text until you paste a new key (Settings or Pool → Apps).", "ai_key"),
    # --- YouTube ---
    "YT_AUTH_REVOKED": ("channel", "YouTube access ended", "Reconnect this channel — Google access was removed.", "reconnect_channel"),
    "YT_QUOTA": ("channel", "YouTube daily limit reached", "Posting resumes after midnight Pacific.", None),
    "YT_UNCERTAIN": ("post", "Upload result unknown", "The connection dropped mid-upload. Check YouTube Studio — it is never retried by itself.", "resolve_upload"),
    "YT_REJECTED": ("post", "YouTube refused this video", "YouTube refused the file. It was moved to Failed.", None),
}


def is_timeout(e):
    t = str(e)
    return any(k in t for k in ("ConnectTimeout", "timed out", "Read timed out", "SOCKSHTTPSConnectionPool", "Max retries exceeded",
                                "ProxyError", "Connection reset", "RemoteDisconnected"))


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
