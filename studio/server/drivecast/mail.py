"""Outgoing email (password reset links) through the owner's own mailbox — e.g. Gmail with an app password.

Optional: without it, a forgotten password is reset with a link the owner makes on the Users page.
"""
import smtplib
import ssl
from email.message import EmailMessage

from . import db, security

PRESETS = {"gmail": ("smtp.gmail.com", 465), "outlook": ("smtp.office365.com", 587), "zoho": ("smtp.zoho.com", 465)}


def config():
    return db.jload(db.meta_get("smtp_json"), {}) or {}


def ready():
    c = config()
    return bool(c.get("host") and c.get("user") and c.get("pass_enc"))


def view():
    c = config()
    return {"host": c.get("host") or "", "port": c.get("port") or 465, "user": c.get("user") or "",
            "from_name": c.get("from_name") or "DriveCast Studio", "ready": ready()}


def save(b):
    c = config()
    for k in ("host", "user", "from_name"):
        if k in b:
            c[k] = str(b[k] or "").strip()[:200]
    if "port" in b:
        c["port"] = int(b["port"] or 465)
    if b.get("password"):
        c["pass_enc"] = security.seal(str(b["password"]).replace(" ", ""))  # Google shows app passwords with spaces
    if b.get("clear"):
        c = {}
    db.meta_set("smtp_json", __import__("json").dumps(c))
    return view()


def send(to, subject, text, html=None):
    c = config()
    if not ready():
        raise RuntimeError("Email is not set up")
    msg = EmailMessage()
    msg["From"] = f'{c.get("from_name") or "DriveCast Studio"} <{c["user"]}>'
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    port = int(c.get("port") or 465)
    ctx = ssl.create_default_context()
    pw = security.unseal(c["pass_enc"])
    if port == 465:
        with smtplib.SMTP_SSL(c["host"], port, context=ctx, timeout=25) as s:
            s.login(c["user"], pw)
            s.send_message(msg)
    else:
        with smtplib.SMTP(c["host"], port, timeout=25) as s:
            s.starttls(context=ctx)
            s.login(c["user"], pw)
            s.send_message(msg)


def reset_email(name, link):
    text = (f"Hi {name},\n\nSomeone asked to reset the password of your DriveCast Studio account.\n"
            f"Open this link within 30 minutes to choose a new password:\n\n{link}\n\n"
            "If it was not you, ignore this email — your password stays the same.\n\nDriveCast Studio")
    html = (f"<div style='font-family:system-ui,sans-serif;max-width:480px;margin:auto;padding:24px;color:#0B0E14'>"
            f"<h2 style='margin:0 0 12px'>Reset your password</h2><p>Hi {name},</p>"
            f"<p>Someone asked to reset the password of your DriveCast Studio account. The link works for 30 minutes.</p>"
            f"<p><a href='{link}' style='display:inline-block;background:#0B0E14;color:#fff;padding:12px 18px;border-radius:10px;"
            f"text-decoration:none;font-weight:600'>Choose a new password</a></p>"
            f"<p style='color:#667085;font-size:13px'>If it was not you, ignore this email — your password stays the same.</p></div>")
    return text, html
