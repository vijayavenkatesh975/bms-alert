# bms-alert

Get a **phone call** (plus a Telegram message) the moment tickets open — or sold-out
seats come back — for a movie at the theatres you care about, on **District** and
**BookMyShow**. Runs on **GitHub Actions**; nothing to keep running on your machine.

Currently configured for **Avengers: Doomsday · 17, 18, 19, 20 Dec 2026 · Chennai · English 3D · 1 seat**:

| Priority | Theatres | Calls when |
|---|---|---|
| 1 | GK Cinemas 4K 3D, Porur | opens · seats come back |
| 2 | Any PVR / INOX **Infinity Vision 3D** | opens · sold-out early show releases ≥ 2 seats |
| 3 | AGS Maduravoyal | opens · seats come back |
| Backup | SB Cinemas, Vels | opens |

## How it works

```
GitHub Actions (cron */20, each run loops 25 min) ──every 60 s──▶ watcher.py
                                                       │
     District public showtimes page (__NEXT_DATA__) ◀──┤  per show: format, language,
     BookMyShow buytickets page (venue links)       ◀──┘  seats available / total
                                                       │
                       new event? ──▶ Telegram message
                                      + Twilio phone call (primary)
                                      + CallMeBot Telegram call (best-effort extra)
```

- **OPEN** – a watched theatre starts listing the target date (District: only shows
  matching `language` + `format_must_include`; BMS: a venue link for that exact date).
- **SEATS** – a watched District show (starting before `seat_alert_shows_before`) goes
  from fewer than `seats_needed` seats to enough, or releases a batch of seats.
- The **first run** only records what's already listed and sends a Telegram summary;
  calls start from the next run. State lives in `watch_state.json` (committed by the
  workflow). Calls are rate-limited by `call_cooldown_minutes` (except new openings).
- Only public pages are read — no login, no private APIs.

## Setup

### 1. Telegram bot (messages)
Message **@BotFather** → `/newbot` → copy the token. Send the bot any message, open
`https://api.telegram.org/bot<TOKEN>/getUpdates`, copy `chat.id`.

### 2. Phone call — pick one or both
| Provider | Secrets | Cost |
|---|---|---|
| **CallMeBot** – Telegram voice call (extra) | `CALLMEBOT_TG_USER` = `@yourhandle` | Free but unreliable (often skips with "FLOOD"). Authorize by sending /start to @CallMeBot_txtbot; set Telegram Privacy → Calls → Everybody. |
| **Twilio** – real phone call (primary) | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`, `CALL_TO` (`+91…`) | Trial calls only verified numbers and plays a trial notice first; paid ≈ $0.05–0.07/min to India. |

### 3. ScraperAPI (BookMyShow from GitHub)
BMS blocks non-India / datacenter IPs. Add `SCRAPERAPI_KEY` (scraperapi.com) or a
`PROXY_URL` India proxy. District is fetched directly first and only falls back to
ScraperAPI if blocked, to save credits.

### 4. Repo secrets
**Settings → Secrets and variables → Actions**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`SCRAPERAPI_KEY`, plus the call secrets from step 2.

### 5. Trigger it every few minutes (cron-job.org)
GitHub's own `schedule` is unreliable, so trigger externally. Create a fine-grained
PAT scoped to this repo with **Actions: Read and write**, then a cron-job.org job:

| Field | Value |
|---|---|
| URL | `https://api.github.com/repos/<you>/bms-alert/actions/workflows/booking-watch.yml/dispatches` |
| Method / Schedule | `POST`, every 5–10 min |
| Headers | `Accept: application/vnd.github+json`, `Authorization: Bearer <PAT>`, `X-GitHub-Api-Version: 2022-11-28` |
| Body | `{"ref":"main"}` |

## Configure — `watch.json`

```jsonc
{
  "movie": "Avengers: Doomsday",
  "date": "2026-12-17",
  "seats_needed": 2,
  "language": "English",
  "format_must_include": ["3D"],
  "district_url": "https://www.district.in/movies/<slug>-in-chennai-MV<id>?fromdate={date}",
  "bms_url": "https://in.bookmyshow.com/movies/chennai/<slug>/buytickets/ET<id>/{yyyymmdd}",
  "groups": [
    { "name": "GK Porur", "match": "\\bgk\\b.*porur", "alert_on_open": true, "alert_on_seats": true },
    { "name": "PVR IV 3D", "match": "\\bpvr\\b|\\binox\\b", "format_match": "infinity vision.*3d",
      "alert_on_open": true, "alert_on_seats": true }
  ],
  "seat_alert_shows_before": "14:30",
  "call_cooldown_minutes": 10
}
```

`match` is a case-insensitive regex tested against the District cinema name and the
BMS venue slug (hyphens → spaces). After changing the target, delete `watch_state.json`.

## Run locally

```bash
pip install -r requirements.txt
python watcher.py --dry-run                   # what matches right now (no alerts)
python watcher.py --test-call                 # test Telegram + call setup
caffeinate -i python watcher.py --loop 120    # check every 2 min from your Mac (India IP, no ScraperAPI needed)
```

## Files

| File | Purpose |
|---|---|
| `watcher.py` | Multi-theatre District + BMS watcher with phone-call alerts |
| `watch.json` | Movie / date / theatres / seat preferences |
| `watch_state.json` | Auto-managed: what's already open, last seat counts, last call |
| `poller.py`, `config.json`, `state.json` | Original single-theatre BMS poller (still runs, non-blocking) |
| `.github/workflows/booking-watch.yml` | The runner (dispatched by cron-job.org) |
