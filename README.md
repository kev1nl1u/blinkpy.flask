# Blinkpy Flask

A private, self-hosted PWA security dashboard for Blink camera systems powered by [blinkpy](https://github.com/fronzbot/blinkpy). Runs on your local network behind Google OAuth with a single-email whitelist — only one person can ever log in.

## Why build this? (The Local Storage Problem)

Blink's zero-subscription local storage is great in theory, but streaming video directly from the Sync Module's USB drive is painfully slow. The hardware bottlenecks result in timeouts, constant buffering, and a frustrating user experience when you just want to check your cameras quickly.

**This project bypasses the Sync Module for playback.** By fetching and caching clips to your server's disk, the PWA serves your footage instantly via HTTP range requests. You get the privacy and cost-savings of local storage, combined with the lightning-fast, zero-buffering playback of a premium cloud service.

## Features

- **Google OAuth 2.0**: full Authorization Code Flow with id_token signature verification; single-email whitelist enforced server-side
- **Blink integration**: arm / disarm all sync modules, live armed-state indicator, automatic 2FA handling
- **Local video library**: downloads clips from Blink's local storage to disk; streams them in-browser with HTTP range support (seek / scrub)
- **Automated downloads**: serialized priority queue + a daily bulk download and a motion-driven poller that fetches new clips near-real-time while armed (see [Downloads & Automation](#downloads--automation))
- **Command queue view**: live panel showing the running job, pending jobs, and scheduled jobs' next run
- **Progressive Web App**: installable on iOS / Android
- **i18n**: English, Italian, Chinese; language persisted in session + cookie, auto-detected from `Accept-Language`
- **Dark / light theme**: saved in `localStorage`, applied before first paint (no flash)
- **Pull-to-refresh**: swipe down on the video gallery to resync Blink state
- **Server reset**: single action that wipes credentials, all sessions, and downloaded videos
- **Security headers**: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, HSTS in production

## Possible improvements
- **Multi-module support**: currently arming / disarming toggle all sync modules at once; could add per-module controls with multiple status indicators
- **Camera live view**: Currently blocked by strict API protections.
  - **The challenge**: Blink transitioned to tighter OAuth 2.0 flows and aggressive client-integrity checks. Legacy endpoints for live view now typically return "An app update is required".
  - **Current blockers**: Reverse-engineering the latest APK reveals the required `APP-BUILD` and `User-Agent` headers. However, forcefully injecting these Android-specific headers into the `blinkpy` session triggers immediate server blocks. This is likely due to the API detecting conflicting client signatures (e.g., mixing injected Android headers with the library's underlying session footprint).


## Architecture

| Layer | Technology |
|---|---|
| Backend | Flask 3 + Flask-Session (filesystem-backed) |
| Blink API | blinkpy + aiohttp on a dedicated background asyncio loop |
| Auth | Google OAuth 2.0 Authorization Code Flow |
| Frontend | Alpine.js 3 (reactive state) + Tailwind CSS via CDN |
| Templates | Jinja2 with `{% extends %}` / `{% include %}` partials |
| PWA | Service Worker — network-first navigation, cache-first static assets |

## Project Structure

```
blink-web/
├── app/
│   ├── api/
│   │   └── endpoints.py        # All /api/* routes
│   ├── locales/                # i18n JSON files (en, it, zh)
│   ├── services/
│   │   ├── scheduler.py        # APScheduler: daily bulk + motion interval jobs
│   │   ├── settings.py         # settings.json load/save (schedule + motion)
│   │   └── blink/
│   │       ├── __init__.py     # BlinkService + background asyncio loop
│   │       ├── queue.py        # Serialized single-worker priority queue
│   │       ├── downloads.py    # download_one_clip + clip metadata sidecars
│   │       ├── bulk.py         # Daily bulk download job
│   │       └── motion.py       # Motion-driven poll job (armed-gated)
│   ├── static/
│   │   ├── css/app.css         # Theme variables + component styles
│   │   ├── js/app.js           # Alpine.js blinkApp() component
│   │   └── sw.js               # Service Worker
│   ├── templates/
│   │   ├── base.html           # HTML shell, Tailwind config, CDN scripts
│   │   ├── index.html          # Extends base, includes all partials
│   │   └── partials/           # One file per screen / component
│   ├── auth.py                 # GoogleOAuth2Manager + login_required decorator
│   ├── auth_routes.py          # /login, /oauth2callback, /logout
│   ├── i18n.py                 # JSON translation loader with in-memory cache
│   ├── routes.py               # /, /videos/<path>, /sw.js
│   └── __init__.py             # App factory, Jinja globals, security headers
├── config.py                   # Flask config (DevelopmentConfig / ProductionConfig)
├── run.py                      # Entry point (LiveReload in dev, threaded in prod)
├── requirements.txt
└── .env.example
```

## Setup

### 1. Google OAuth credentials

1. Open [Google Cloud Console](https://console.cloud.google.com/) → **APIs & Services** → **Credentials**
2. Create an **OAuth 2.0 Client ID** (application type: **Web application**)
3. Under **Authorized redirect URIs** add `http://localhost:5000/oauth2callback`
4. Copy the **Client ID** and **Client Secret**

### 2. Environment variables

```bash
cp .env.example .env
```

Fill in `.env`:

```env
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
ALLOWED_EMAIL=your-email@gmail.com
OAUTH2_REDIRECT_URI=http://localhost:5000/oauth2callback

# Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
SECRET_KEY=your-random-secret-key

FLASK_DEBUG=true   # set to false (or omit) in production
```

### 3. Install dependencies

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Run

**Development** (LiveReload, localhost only):
```bash
FLASK_DEBUG=true python run.py
```

**Production** (all interfaces, threaded):
```bash
python run.py
```

> In production, place behind a reverse proxy (nginx / Caddy) with TLS and update `OAUTH2_REDIRECT_URI` to your public HTTPS URL.

---

## Authentication Flow

```
Browser → GET /login
  → Server generates authorization URL + cryptographically random CSRF state token
  → State token saved in session
  → Redirect to accounts.google.com

Google → GET /oauth2callback?code=…&state=…
  1. State token verified against session value (CSRF protection)
  2. Authorization code exchanged for id_token via Google token endpoint
  3. id_token signature verified with Google's public keys
  4. Email claim extracted and checked against ALLOWED_EMAIL (whitelist)
  5. Permanent server-side session created (30 days)
  → Redirect to /
```

## Blink Connection Flow

Blink credentials are stored separately from Google OAuth — you authenticate with Google first, then configure Blink:

1. After Google login, the setup screen prompts for Blink email + password
2. `POST /api/credentials` initializes `BlinkService` with the provided credentials
3. If Blink requires 2FA: server returns `awaiting_2fa: true`, UI shows OTP input
4. User submits OTP → `POST /api/credentials/2fa` completes login
5. On success: tokens saved to `credentials.json` (permissions 600); service is marked started
6. On next server start: `credentials.json` is loaded automatically — no re-login required

The Blink client runs on a persistent background asyncio event loop (separate thread) so the aiohttp `ClientSession` stays alive for the lifetime of the Flask process.

---

## Downloads & Automation

### Serialized priority queue

Every Blink operation is funneled through a single-worker asyncio **priority queue** (`app/services/blink/queue.py`) so no two Blink calls ever overlap (the API rejects concurrent access). Lower priority number runs first; a monotonic counter breaks ties (FIFO within a priority). Entries with the same key are de-duplicated, so the same clip can't be enqueued twice.

| Priority | Operation |
|---|---|
| `0` | arm / disarm |
| `1` | manifest / status / refresh |
| `2` | boosted clip (motion or manual tap) |
| `3` | bulk clip (daily download) |

A queued download can be **boosted** to priority 2 by tapping a remote clip in the gallery (`POST /api/blink/local/clip/boost`), pulling it to the front.

The **Command queue** panel (logo menu → *Coda comandi*) shows the running job, pending jobs in run order, and the next run time of both scheduled jobs. It polls `GET /api/queue` every 2s while open.

### Scheduled bulk download

A cron job (`DownloadScheduler`, APScheduler) runs once a day at a configured time. It refreshes each sync module's local-storage manifest and enqueues **every** clip at priority 3 (dedup skips clips already on disk). This is the backlog/catch-up path. Configured under *Download schedulato* (enabled, time, timezone).

### Motion-driven download

Blink has **no push/webhook** for new recordings, and the cheap `homescreen` endpoint does **not** reflect new local-storage clips — only building the local-storage manifest does, and that build wakes the sync module (expensive). So the motion poller (`app/services/blink/motion.py`) is cost-bounded:

1. An interval job fires every *N* minutes (default 2, configurable under *Download da movimento*).
2. For each sync module: a cheap **armed check** (one network-status request). Disarmed modules are skipped — no motion means no recording, so the expensive manifest build is avoided.
3. If armed: build the manifest and compare against an in-memory high-water mark (`_last_seen`, newest `created_at` seen per module).
   - **First poll after a restart** only seeds the mark — it does **not** re-download history (that's the bulk job's role).
   - Later polls enqueue only clips newer than the mark, at priority 2.

Because `_last_seen` is in-memory, it resets on process restart (re-seeds without re-downloading). Latency to disk ≈ the poll interval.

Both jobs attempt to start Blink from saved `credentials.json` if the service isn't connected; if 2FA is required or credentials are missing, the run is skipped. Settings live in `settings.json`; `POST /api/settings` persists them and reconfigures the scheduler at runtime.

---

## API Reference

All endpoints require a valid Google-authenticated session. Returns `401` JSON if unauthenticated.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/status` | Blink connection state + current armed status |
| `POST` | `/api/blink/refresh` | Force-refresh Blink network info and arm state |
| `POST` | `/api/credentials` | Submit Blink email + password |
| `POST` | `/api/credentials/2fa` | Submit 2FA OTP code |
| `POST` | `/api/blink/arm` | Arm all sync modules (optional body: `{"module": "name"}`) |
| `POST` | `/api/blink/disarm` | Disarm all sync modules |
| `GET` | `/api/local-videos` | List locally downloaded video clips (newest 50) |
| `GET` | `/api/blink/local/remote` | List clips in the sync-module manifest (downloaded or not) |
| `POST` | `/api/blink/local/clip/boost` | Boost a remote clip to priority 2 and enqueue its download |
| `GET` | `/api/queue` | Command queue snapshot: running + pending jobs, scheduled jobs' next run |
| `GET` | `/api/settings` | Read scheduled + motion download settings |
| `POST` | `/api/settings` | Update settings; reconfigures the scheduler at runtime |
| `POST` | `/api/admin/reset` | Wipe credentials, sessions, and all downloaded videos |
| `POST` | `/api/language/<lang>` | Set UI language — `en`, `it`, or `zh` |
| `GET` | `/videos/<path>` | Stream a downloaded MP4 with HTTP range support |

---

## Security

| Concern | Implementation |
|---|---|
| **Single-user access** | `ALLOWED_EMAIL` checked after id_token signature verification — not just session |
| **CSRF** | Random state token per OAuth flow, verified before code exchange |
| **Session storage** | Server-side filesystem (`flask_session/`); browser only receives an opaque session ID |
| **Session cookie** | `HttpOnly`, `SameSite=Lax`, `Secure=True` in production |
| **credentials.json** | File permissions set to `0o600` on every write |
| **Video path traversal** | Resolved path validated to stay within `local_clips/` before serving |
| **HTTP headers** | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, HSTS (production only) |
| **SECRET_KEY** | Random 32-byte value generated at startup if not set; warning printed — always set it in `.env` for production |

---

## PWA

The Service Worker (`sw.js`) uses two caches:

- **`blink-shell-v1`** — caches `/` at install time; serves as offline fallback for navigation
- **`blink-static-v1`** — cache-first for `/static/*` and CDN assets

API calls (`/api/*`) and video streams (`/videos/*`) are never cached.

**Install on iOS:** Share → Add to Home Screen  
**Install on Android:** browser menu → Install app

---

## Production Deployment

Example with Caddy:

```
# /etc/caddy/Caddyfile
blink.example.com {
    reverse_proxy localhost:5000
}
```

Update `.env` for production:

```env
FLASK_DEBUG=false
OAUTH2_REDIRECT_URI=https://blink.example.com/oauth2callback
SESSION_COOKIE_SECURE=true
SECRET_KEY=<long random value>
```

Update the redirect URI in Google Cloud Console to match.

---

## License

This project depends on third-party libraries with their own licenses.

- `blinkpy` is licensed under the MIT License.

When redistributing this project, include and preserve all required third-party license notices.
