"""Google Drive: folder inventory, the per-channel video queue, moves and downloads."""
from .google import authed
from .net import Permanent, QuotaExceeded, Transient

API = "https://www.googleapis.com/drive/v3"
STAGE_FOLDERS = ("Processing", "Done", "Failed")
FOLDER = "application/vnd.google-apps.folder"


def _check(r, what):
    if r.status_code in (403, 429) and ("rateLimit" in r.text or "quota" in r.text.lower()):
        raise QuotaExceeded("Google Drive rate limit reached", r.text[:300])
    if r.status_code == 404:
        raise Permanent(f"Drive {what} not found or no longer shared", r.text[:300])
    if r.status_code >= 500 or r.status_code == 429:
        raise Transient(f"Google Drive is busy ({what})", r.text[:300])
    if r.status_code >= 400:
        raise Permanent(f"Google Drive refused {what}", r.text[:300])
    return r.json() if r.content else {}


def _list(session, box, q, fields, order=None, limit=1000):
    out, token = [], None
    while True:
        params = {"q": q, "fields": f"nextPageToken, files({fields})", "pageSize": 200,
                  "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if order:
            params["orderBy"] = order
        if token:
            params["pageToken"] = token
        data = _check(authed(session, box, "GET", API + "/files", params=params), "list")
        out.extend(data.get("files", []))
        token = data.get("nextPageToken")
        if not token or len(out) >= limit:
            return out[:limit]


def whoami(session, box):
    data = _check(authed(session, box, "GET", API + "/about", params={"fields": "user(emailAddress)"}), "account")
    return data.get("user", {}).get("emailAddress")


def folder_info(session, box, folder_id):
    return _check(authed(session, box, "GET", f"{API}/files/{folder_id}",
                         params={"fields": "id,name,mimeType,trashed", "supportsAllDrives": "true"}), "folder")


def subfolders(session, box, root_id, max_depth=3, max_count=500):
    """Every folder under the main folder (breadth first), skipping stage folders."""
    found, frontier = [], [(root_id, "", 0)]
    while frontier and len(found) < max_count:
        parent, path, depth = frontier.pop(0)
        kids = _list(session, box, f"'{parent}' in parents and mimeType='{FOLDER}' and trashed=false",
                     "id,name", order="name")
        for k in kids:
            if k["name"] in STAGE_FOLDERS or k["name"].startswith("_"):
                continue
            p = f"{path}/{k['name']}" if path else k["name"]
            found.append({"id": k["id"], "name": k["name"], "path": p})
            if depth + 1 < max_depth:
                frontier.append((k["id"], p, depth + 1))
    return found


def videos(session, box, folder_id, limit=200):
    """Videos directly inside the folder, oldest first."""
    return _list(session, box,
                 f"'{folder_id}' in parents and trashed=false and mimeType contains 'video/'",
                 "id,name,size,mimeType,createdTime", order="createdTime", limit=limit)


def child_folder(session, box, parent_id, name):
    hits = _list(session, box, f"'{parent_id}' in parents and name='{name}' and mimeType='{FOLDER}' and trashed=false",
                 "id,name")
    if hits:
        return hits[0]["id"]
    r = authed(session, box, "POST", API + "/files", params={"supportsAllDrives": "true"},
               json={"name": name, "mimeType": FOLDER, "parents": [parent_id]})
    return _check(r, "folder create")["id"]


def move(session, box, file_id, from_id, to_id):
    r = authed(session, box, "PATCH", f"{API}/files/{file_id}",
               params={"addParents": to_id, "removeParents": from_id, "supportsAllDrives": "true", "fields": "id"})
    _check(r, "move")


def download(session, box, file_id, path, chunk=1 << 20, on_progress=None):
    r = authed(session, box, "GET", f"{API}/files/{file_id}", params={"alt": "media", "supportsAllDrives": "true"},
               stream=True, timeout=(20, 300))
    if r.status_code >= 400:
        _check(r, "download")
    size = 0
    with open(path, "wb") as f:
        for part in r.iter_content(chunk):
            f.write(part)
            size += len(part)
            if on_progress:
                on_progress(size)
    if size == 0:
        raise Permanent("The Drive file is empty")
    return size
