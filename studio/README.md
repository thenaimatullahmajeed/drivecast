# DriveCast Studio

Google Drive folders → YouTube, run from an Android phone, managed from any browser.

- `server/` — Python server (panel, API, scheduler, workers). Runs on the phone through Chaquopy, or on a computer: `cd server && python -m drivecast --data ./data`.
- `server/drivecast/web/` — the web panel (no build step) and the phone screen.
- `tunnel/` — Tailscale Funnel helper that gives the phone a fixed public https link.
- `android/` — the Android app: foreground service, boot start, setup checklist.
- `.github/workflows/android.yml` — tests, builds the tunnel with the NDK, builds and publishes the APK.

## Safety rules built in
- Every channel uploads only through its IP group's SOCKS5 proxy (DNS through proxy). Wrong or missing IP → the post is held. Never falls back to the server's own IP.
- Direct publish at a natural time inside the audience's peak hours. Never pre-scheduled on YouTube.
- One reservation per (Drive file, destination). An upload that ends without a clear answer is marked *uncertain* and never retried automatically.
- Each channel, IP group and account fails on its own; services restart themselves.

## Tests
`cd server && python -m unittest discover -s tests`
