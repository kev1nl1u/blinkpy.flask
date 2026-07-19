# Serialized Blink Operation Queue & Scheduled Downloads

All Blink operations run through one serialized priority queue. Arm/disarm get top
priority; downloads move off the critical path (on-demand or a daily scheduler).
Result: fast, reliable arming and no concurrent-request rate limiting.

## Problem

Opening the app to arm/disarm stalled for minutes or failed. Three compounding causes:

```mermaid
flowchart TD
    A[Open app] --> B[fetchVideos auto-starts SSE download]
    B --> C[Many Flask threads call run_sync concurrently]
    C --> D[Concurrent calls hit one serial sync module]
    D --> E[429 Too Many Requests + arm queues behind downloads]
    E --> F[Arm stalls / fails]
```

Verified against `blinkpy`:

| Operation | Path | Cost |
| --- | --- | --- |
| `update_local_storage_manifest` | `sync_module.py:425` | 2 round-trips, backs off only if busy. **Cheap.** |
| `prepare_download` | `sync_module.py:705` → `api.py:712` | Polls `MAX_RETRY=120` × `1s` = **up to 120 s/clip** (device→cloud upload). |
| `request_system_arm` | `api.py:222` | **Same** `wait_for_command`, same network. |

`arm` and `prepare_download` share one command channel on one serial device, and
there is **no cancel endpoint** in `blinkpy`. Stopping the poll doesn't free the
device — it keeps uploading. So the physical floor on arm latency is **one clip's
upload time**; nothing can beat that except not running downloads while arming.

## Architecture

One worker on the existing background loop drains an `asyncio.PriorityQueue`. Every
Blink call goes through it, so two never overlap.

```mermaid
flowchart LR
    subgraph Flask threads
        ARM[arm / disarm]
        ST[status / manifest]
        TAP[tap remote clip]
        SCH[scheduler job]
    end
    ARM -->|submit p0| Q
    ST  -->|submit p1| Q
    TAP -->|submit p2| Q
    SCH -->|submit p3| Q
    Q[["PriorityQueue<br/>p0 arm · p1 status<br/>p2 boost · p3 bulk"]] -->|pop lowest| W
    W([single worker<br/>one op at a time]) --> BP[blinkpy] --> DEV[(sync module<br/>serial HW)]
    W -.resolve.-> F[caller Future]
```

Queue entries are `(priority, sequence, key)` — the monotonic `sequence` gives FIFO
within a priority without comparing coroutines.

**Arm latency guarantee** — arm enqueued mid-download finishes only the in-flight clip,
then jumps the queue:

```mermaid
sequenceDiagram
    participant U as User
    participant Q as Queue
    participant W as Worker
    Note over W: downloading clip 3/10 (p3)
    U->>Q: arm (p0)
    Note over Q: p0 < p3 → front
    W->>W: finish clip 3 (atomic)
    W->>U: arm runs next ✓
    Note over W: clips 4–10 resume after
```

## Priorities

| P | Operation | Why |
| --- | --- | --- |
| 0 | arm / disarm | User-facing, must never wait behind downloads |
| 1 | manifest / status / network / refresh | Cheap reads, keep UI fresh |
| 2 | boosted clip (user tapped) | Wanted now, but never before arm |
| 3 | bulk clip (scheduler) | Background, lowest |

## Components

| File | Role |
| --- | --- |
| `app/services/blink/queue.py` | `BlinkQueue`: priority heap + worker; `submit` / `submit_nowait` / `reprioritize`; dedup by key. Knows nothing of clips/arming. |
| `app/services/blink/__init__.py` | Exposes `service.submit/…`; `start_from_credentials`. |
| `app/services/blink/downloads.py` | `download_one_clip` + file/metadata helpers (was duplicated 3×). |
| `app/services/blink/bulk.py` | Scheduler job: manifest (p1) + per-clip (p3, keyed). |
| `app/services/settings.py` | `Settings` load/validate/save (`HH:MM` + `zoneinfo`). |
| `app/services/scheduler.py` | `DownloadScheduler` (APScheduler, reschedulable). |
| `app/api/endpoints.py` | Routes Blink ops via queue; `+/settings`, `+/clip/boost`, `+/local/remote`. |
| `app.js` + `dashboard.html` | Clip states, tap-to-boost, settings modal. |

`BlinkQueue` is domain-agnostic; `download_one_clip` is queue-agnostic — each tested alone.

## Flows

```mermaid
flowchart TD
    subgraph Entry
        I[init] --> S[status p1] --> L[local clips from disk] --> R[manifest p1 → remote placeholders]
    end
    subgraph Tap remote
        T[POST /clip/boost] --> RP{in queue?}
        RP -->|yes| RR[reprioritize → p2]
        RP -->|no| EN[submit p2]
        RR --> DL[download] --> PLAY[play from disk]
        EN --> DL
    end
    subgraph Scheduler
        CR[cron HH:MM tz] --> MAN[manifest p1] --> PC[new clips p3]
    end
```

Clip lifecycle in the UI:

```mermaid
stateDiagram-v2
    [*] --> remote: in manifest, not on disk
    [*] --> local: already downloaded
    remote --> downloading: user taps (boost p2)
    downloading --> local: file lands on disk
    downloading --> remote: error / timeout
    local --> [*]: play
```

## Edge cases

| Case | Handling |
| --- | --- |
| Worker job raises | Caught per-job; exception → caller Future; worker survives. `CancelledError` cancels the Future and re-raises. |
| `prepare_download` fails | blinkpy retries; then Future errors, clip stays `remote`, partial file removed. |
| 429 | Prevented by serialization; if seen, backoff + 1 retry. |
| Scheduler fires, Blink offline | Job tries `start_from_credentials`; needs 2FA → skip + log. |
| Invalid config | Rejected pre-save (`HH:MM` + `zoneinfo`) → `400`, scheduler unchanged. |
| Duplicate clip | `key = "module:clip_id"` → enqueued once (tap + bulk dedup). |
| Server restart | Queue is in-memory; interrupted downloads resume next scheduler/tap. |

## Motion-driven downloads (armed-gated manifest poll)

Goal: download new clips near-real-time instead of only on the daily schedule.

Blink offers **no push/webhook** for new recordings. The official app receives FCM
push, but blinkpy exposes no client for it — `notification_flags` only configures
which notifications the *cloud* sends to the *mobile app*. Everything in blinkpy is
poll-based.

This deployment records to **local storage** (sync-module USB), not cloud, so the
cheap cloud signals (`/videos/count`, `/media/changed`) never move. Detecting a new
local clip requires building the local-storage **manifest** (`manifest/request` →
`wait_for_command` → GET), which wakes the sync module.

We also empirically ruled out the cheap `homescreen` endpoint as a sentinel: a motion
clip recorded at `08:45:18Z` produced **no** homescreen field change — `updated_at`
only bumps on config/arm events, and `last_hb` is a 60 s heartbeat. So the manifest
build is the only reliable signal.

To bound that cost, `run_motion_poll` (`app/services/blink/motion.py`) runs on an
interval (`motion_download.interval_minutes`, default 2) and per sync module:

1. cheap armed check (one `get_network_info` request, priority 1);
2. **disarmed → skip** (no recordings possible — this is the whole cost saving);
3. armed → build the manifest, enqueue clips newer than an in-memory high-water mark
   at boosted priority (2), keyed `module:clip_id` (dedups with tap-to-boost).

The first poll after a restart seeds the mark without re-downloading history (that
backlog is the daily bulk download's job). The interval job is reconfigurable at
runtime via the same settings POST + `DownloadScheduler.apply`.

## Out of scope (YAGNI)

Tee streaming (boost replaces it) · cancelling in-flight downloads (device work isn't
abortable) · persisting the queue · a separate "download on entry" toggle (scheduler replaces it).
