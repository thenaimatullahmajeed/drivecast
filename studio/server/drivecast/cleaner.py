"""Media cleaning: remove every hidden detail (location, phone, time, source app,
encoder strings) without re-encoding, then add DriveCast's own truthful tag.

Video: ffmpeg stream copy into a brand-new MP4. Container + stream metadata,
chapters and H.264/HEVC SEI user data are dropped; the display rotation is kept.
Photo: JPEG metadata segments removed losslessly (Pillow is used only when the
photo must be turned upright first).
"""
import os
import re
import shutil
import subprocess

from . import config
from .errors import CodedError

TAG = "DriveCast Studio"
_SEI = {"h264": "6", "hevc": "39|40"}


def ffmpeg_path():
    p = getattr(config, "FFMPEG", None) or os.environ.get("DC_FFMPEG")
    if p and os.path.exists(p):
        return p
    return shutil.which("ffmpeg")


_works = {}


def available():
    """True when the bundled ffmpeg actually runs on this phone (checked once per binary)."""
    exe = ffmpeg_path()
    if not exe:
        return False
    if exe not in _works:
        try:
            p = subprocess.run([exe, "-hide_banner", "-version"], capture_output=True, timeout=20)
            _works[exe] = p.returncode == 0
        except Exception:
            _works[exe] = False
    return _works[exe]


def _run(args, timeout=900):
    exe = ffmpeg_path()
    if not exe:
        raise CodedError("FFMPEG_MISSING", "ffmpeg binary not found")
    try:
        p = subprocess.run([exe, "-hide_banner", "-nostdin"] + args, capture_output=True, timeout=timeout)
    except OSError as e:
        raise CodedError("FFMPEG_MISSING", str(e))
    except subprocess.TimeoutExpired:
        raise CodedError("CLEAN_FAILED", "ffmpeg timed out")
    return p.returncode, p.stderr.decode("utf-8", "replace")


def probe(path):
    """width, height, duration, has_audio, vcodec — parsed from ffmpeg's own report."""
    _, err = _run(["-i", path], timeout=60)
    out = {"width": None, "height": None, "duration": None, "has_audio": False, "vcodec": None, "rotation": 0}
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        out["duration"] = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    for line in err.splitlines():
        if "Video:" in line and out["vcodec"] is None:
            mc = re.search(r"Video: (\w+)", line)
            out["vcodec"] = mc[1] if mc else None
            ms = re.search(r", (\d{2,5})x(\d{2,5})", line)
            if ms:
                out["width"], out["height"] = int(ms[1]), int(ms[2])
        if "Audio:" in line:
            out["has_audio"] = True
    mr = re.search(r"rotation of (-?\d+(?:\.\d+)?)", err) or re.search(r"rotate\s*:\s*(-?\d+)", err)
    if mr:
        out["rotation"] = int(float(mr[1])) % 360
    if out["width"] and out["rotation"] in (90, 270):
        out["width"], out["height"] = out["height"], out["width"]
    return out


def quality_gate(info, min_short_side=720, min_seconds=3):
    """Returns a skip code or None."""
    w, h = info.get("width"), info.get("height")
    if w and h and min(w, h) < min_short_side:
        return "LOW_QUALITY"
    if not info.get("has_audio"):
        return "NO_AUDIO"
    if info.get("duration") is not None and info["duration"] < min_seconds:
        return "TOO_SHORT"
    return None


def clean_video(src, dst, ref, version, vcodec=None):
    vcodec = vcodec or probe(src).get("vcodec")
    args = ["-y", "-i", src, "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
            "-map_metadata", "-1", "-map_metadata:s:v", "-1", "-map_metadata:s:a", "-1", "-map_chapters", "-1",
            "-dn", "-sn", "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact"]
    if vcodec in _SEI:
        args += ["-bsf:v", f"filter_units=remove_types={_SEI[vcodec]}"]
    args += ["-metadata", f"encoder={TAG} {version}", "-metadata", f"comment=Processed by {TAG} {version} · {ref}",
             "-movflags", "+faststart", "-f", "mp4", dst]
    code, err = _run(args)
    if code != 0 or not os.path.exists(dst) or os.path.getsize(dst) < 1024:
        if vcodec in _SEI:  # some streams reject the SEI filter; retry without it rather than fail the post
            args2 = [a for a in args if not a.startswith("filter_units") and a != "-bsf:v"]
            code, err = _run(args2)
        if code != 0 or not os.path.exists(dst) or os.path.getsize(dst) < 1024:
            raise CodedError("CLEAN_FAILED", err[-1500:])
    return dst


def merge(video, audio, dst):
    code, err = _run(["-y", "-i", video, "-i", audio, "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-f", "mp4", dst])
    if code != 0 or not os.path.exists(dst):
        raise CodedError("CLEAN_FAILED", "merge: " + err[-1200:])
    return dst


# ---------------------------------------------------------------- photos
_DROP = {0xE1, 0xED, 0xEC, 0xFE, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xEB, 0xEF}  # EXIF/XMP, IPTC, COM, misc APPn


def _orientation(data):
    i = data.find(b"Exif\x00\x00")
    if i < 0:
        return 1
    t = data[i + 6:]
    le = t[:2] == b"II"
    rd = (lambda b: int.from_bytes(b, "little" if le else "big"))
    try:
        off = rd(t[4:8])
        n = rd(t[off:off + 2])
        for k in range(n):
            e = off + 2 + 12 * k
            if rd(t[e:e + 2]) == 0x0112:
                return rd(t[e + 8:e + 10])
    except Exception:
        pass
    return 1


def clean_jpeg(src, dst, ref, version):
    with open(src, "rb") as f:
        data = f.read()
    if data[:2] != b"\xff\xd8":
        return clean_other_image(src, dst, ref, version)
    orient = _orientation(data)
    out = bytearray(b"\xff\xd8")
    i = 2
    while i < len(data) - 1:
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker == 0xDA:  # start of scan: copy the rest unchanged
            out += data[i:]
            break
        seg_len = int.from_bytes(data[i + 2:i + 4], "big")
        seg = data[i:i + 2 + seg_len]
        if marker not in _DROP:
            out += seg
        i += 2 + seg_len
    note = f"{TAG} {version} · {ref}".encode()
    head = b"\xff\xfe" + (len(note) + 2).to_bytes(2, "big") + note
    if orient not in (0, 1) and orient <= 8:
        # keep ONLY the "which way is up" flag, so the photo is not shown sideways; nothing else survives
        tiff = (b"II*\x00" + (8).to_bytes(4, "little") + (1).to_bytes(2, "little") + (0x0112).to_bytes(2, "little")
                + (3).to_bytes(2, "little") + (1).to_bytes(4, "little") + orient.to_bytes(2, "little") + b"\x00\x00"
                + (0).to_bytes(4, "little"))
        payload = b"Exif\x00\x00" + tiff
        head = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload + head
    out = out[:2] + head + out[2:]
    with open(dst, "wb") as f:
        f.write(out)
    return dst


_PNG_KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"pHYs", b"sBIT", b"bKGD"}


def clean_other_image(src, dst, ref, version):
    """PNG: drop every text/EXIF/time chunk losslessly. Other formats are refused (privacy first)."""
    import struct
    import zlib
    with open(src, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise CodedError("CLEAN_FAILED", "unsupported photo format — use JPG or PNG")
    out, i = bytearray(data[:8]), 8
    while i + 8 <= len(data):
        n = struct.unpack(">I", data[i:i + 4])[0]
        kind = data[i + 4:i + 8]
        chunk = data[i:i + 12 + n]
        if kind == b"IEND":
            note = b"Software\x00" + f"{TAG} {version} \u00b7 {ref}".encode("latin-1", "replace")
            out += struct.pack(">I", len(note)) + b"tEXt" + note + struct.pack(">I", zlib.crc32(b"tEXt" + note) & 0xFFFFFFFF)
        if kind in _PNG_KEEP:
            out += chunk
        i += 12 + n
    with open(dst, "wb") as f:
        f.write(out)
    return dst
