#!/usr/bin/env python3
"""
Multi-theatre watcher for District + BookMyShow that PHONES you.

What it detects (driven by watch.json):
  * OPEN  - a watched theatre starts listing shows for the target date
            (District: only sessions matching language + format; BMS: venue link
            for that exact date appears).
  * SEATS - on District, a watched show for the date gets >= seats_needed seats
            back (e.g. a sold-out PVR Infinity Vision 3D show re-releases seats).

On any new event it sends a Telegram message and places a phone call:
  * CALLMEBOT_TG_USER  -> free Telegram voice call via callmebot.com (@username)
  * TWILIO_*           -> real phone call via Twilio
Both are optional; configure whichever you want (or both).

Only public pages are read (the showtimes page each site serves to any
visitor). No login, no private APIs.

Run once:            python watcher.py
Run continuously:    python watcher.py --loop 120     (seconds between checks)
Print what it sees:  python watcher.py --dry-run
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent


def load_dotenv(path=ROOT / ".env"):
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if val.startswith('"'):
                end_quote = val.find('"', 1)
                val = val[1:end_quote] if end_quote != -1 else val.strip('"')
            elif val.startswith("'"):
                end_quote = val.find("'", 1)
                val = val[1:end_quote] if end_quote != -1 else val.strip("'")
            else:
                val = val.split("#", 1)[0].strip()
            if key and key not in os.environ:
                os.environ[key] = val


load_dotenv()
CONFIG_PATH = Path(os.environ.get("WATCH_CONFIG", ROOT / "watch.json"))
STATE_PATH = Path(os.environ.get("WATCH_STATE", ROOT / "watch_state.json"))

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}


# --------------------------------------------------------------------------- #
# fetching
# --------------------------------------------------------------------------- #
def fetch(url, warmup=None, try_direct_first=False):
    """GET a page, through ScraperAPI / PROXY_URL when configured (needed on
    non-India IPs such as GitHub runners).

    try_direct_first: attempt a plain request first and only spend a
    ScraperAPI credit if that is blocked (District often isn't geo-blocked)."""
    key = os.environ.get("SCRAPERAPI_KEY")
    if key and try_direct_first:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.ok and "__NEXT_DATA__" in r.text:
                return r.text
            print(f"[fetch] direct got {r.status_code}, falling back to ScraperAPI")
        except requests.RequestException as e:
            print(f"[fetch] direct failed ({e}), falling back to ScraperAPI")
    if key:
        api = "https://api.scraperapi.com/?" + urllib.parse.urlencode(
            {"api_key": key, "country_code": "in", "url": url}
        )
        r = requests.get(api, timeout=90)
        r.raise_for_status()
        return r.text

    proxy = os.environ.get("PROXY_URL")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    s = requests.Session()
    s.headers.update(HEADERS)
    if warmup:
        try:
            s.get(warmup, timeout=30, proxies=proxies)
        except requests.RequestException:
            pass
    r = s.get(url, timeout=30, proxies=proxies)
    r.raise_for_status()
    return r.text


def get_target_dates(cfg):
    dates = cfg.get("dates") or cfg.get("date")
    if not dates:
        return []
    if isinstance(dates, str):
        return [dates]
    return [str(d) for d in dates]


def extract_time_hhmm(s):
    iso = s.get("showTimeISO") or s.get("showTimeLocal") or ""
    if len(iso) >= 16 and "T" in iso:
        return iso.split("T")[1][:5]
    txt = s.get("showTimeText") or ""
    m_time = re.match(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", txt, re.I)
    if m_time:
        hr = int(m_time.group(1))
        mn = m_time.group(2)
        ampm = (m_time.group(3) or "").upper()
        if ampm == "PM" and hr < 12:
            hr += 12
        elif ampm == "AM" and hr == 12:
            hr = 0
        return f"{hr:02d}:{mn}"
    return ""


# --------------------------------------------------------------------------- #
# District
# --------------------------------------------------------------------------- #
def district_sessions(cfg, target_date):
    """Return list of dicts: one per show on the target date, all cinemas."""
    url = cfg["district_url"].format(date=target_date)
    html = fetch(url, try_direct_first=True)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("District: __NEXT_DATA__ not found (page layout changed?)")
    data = json.loads(m.group(1))

    # movieSessions is keyed "<movieCode><date>"; pick the entry for our date.
    ms = (
        data.get("props", {}).get("pageProps", {}).get("data", {})
        .get("serverState", {}).get("movieSessions", {})
    )
    out = []
    for entry in ms.values():
        # District falls back to the nearest open date -- ignore that.
        if entry.get("searchDate") != target_date:
            continue
        pd = entry.get("pageData", {}) or {}
        for cinema in (pd.get("nearbyCinemas") or []) + (pd.get("farCinemas") or []):
            name = (cinema.get("cinemaInfo") or {}).get("name") or ""
            for s in cinema.get("sessions") or []:
                if s.get("showDate") and s["showDate"] != target_date:
                    continue
                out.append({
                    "site": "district",
                    "date": target_date,
                    "cinema_id": cinema.get("id"),
                    "cinema": name,
                    "sid": s.get("sid"),
                    "time": extract_time_hhmm(s),
                    "time_text": s.get("showTimeText") or "",
                    "fmt": s.get("scrnFmt") or "",
                    "lang": s.get("lang") or "",
                    "avail": int(s.get("avail") or 0),
                    "total": int(s.get("total") or 0),
                    "status": s.get("seatStatus") or "",
                    "areas": [
                        {
                            "label": (a.get("label") or "").strip(),
                            "avail": int(a.get("sAvail") or 0),
                            "total": int(a.get("sTotal") or 0),
                            "price": float(a.get("price") or 0),
                        }
                        for a in s.get("areas") or []
                    ],
                })
    return out


# --------------------------------------------------------------------------- #
# BookMyShow
# --------------------------------------------------------------------------- #
def bms_venues(cfg, target_date):
    """Return {venue_code: slug} for venues that list the exact target date."""
    ymd = target_date.replace("-", "")
    url = cfg["bms_url"].format(yyyymmdd=ymd, date=target_date)
    html = fetch(url, warmup="https://in.bookmyshow.com/")
    venues = {}
    for slug, code in re.findall(
        r"/cinemas/[a-z-]+/([a-z0-9-]+)/buytickets/([A-Z0-9]+)/" + ymd, html
    ):
        venues[code] = slug
    return venues


# --------------------------------------------------------------------------- #
# matching
# --------------------------------------------------------------------------- #
def group_for(cfg, cinema_name, site="district"):
    """First group whose name regex matches. Groups with "bms": false are
    District-only (e.g. a format-based catch-all; BMS pages carry no format)."""
    for g in cfg["groups"]:
        if site == "bms" and g.get("bms") is False:
            continue
        if re.search(g["match"], cinema_name, re.I):
            return g
    return None


def session_ok(cfg, g, s):
    if cfg.get("language") and cfg["language"].lower() not in s["lang"].lower():
        return False
    for f in cfg.get("format_must_include", []):
        if f.lower() not in s["fmt"].lower():
            return False
    if g.get("format_match") and not re.search(g["format_match"], s["fmt"], re.I):
        return False
    # Show timing filter: e.g. 09:00 to 16:00
    stime = s.get("time")
    if stime:
        after = cfg.get("show_time_after")
        before = cfg.get("show_time_before")
        if after and stime < after:
            return False
        if before and stime > before:
            return False
    return True


def is_front_row_area(area, all_areas, cfg):
    """Return True if the area is a front-row (neck-strain / undesirable) section."""
    if not cfg.get("exclude_front_rows", True):
        return False

    label = (area.get("label") or "").strip().lower()
    exclude_patterns = cfg.get(
        "exclude_areas",
        ["budget", "normal", "second class", "2nd class", "lower", "silver", "front"]
    )
    for pat in exclude_patterns:
        if re.search(r"\b" + re.escape(pat) + r"\b", label, re.I):
            return True

    # Price-based check: if multiple price tiers exist, the lowest tier
    # in Chennai theatres is statutory front-row (< ₹150 vs ₹200+)
    prices = [a.get("price", 0) for a in all_areas if a.get("price", 0) > 0]
    if len(set(prices)) > 1:
        min_price = min(prices)
        max_price = max(prices)
        if area.get("price") == min_price and max_price >= min_price * 1.4:
            return True

    return False


def get_qualifying_areas(areas, need, cfg):
    """Return list of non-front areas that have at least `need` seats available."""
    qualifying = []
    for a in areas:
        if not is_front_row_area(a, areas, cfg) and a["avail"] >= need:
            qualifying.append(a)
    return qualifying


# --------------------------------------------------------------------------- #
# notifications
# --------------------------------------------------------------------------- #
def send_telegram(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        print("[telegram] not configured")
        return
    r = requests.post(
        f"https://api.telegram.org/bot{tok}/sendMessage",
        json={"chat_id": chat, "text": text, "disable_web_page_preview": True},
        timeout=30,
    )
    print(f"[telegram] {r.status_code}")


def twilio_call(spoken):
    """Real phone call via Twilio. Returns True if Twilio accepted the call."""
    sid, auth = os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN")
    frm, to = os.environ.get("TWILIO_FROM"), os.environ.get("CALL_TO")
    if not (sid and auth and frm and to):
        print("[twilio] not configured")
        return False
    say = spoken.replace("&", "and").replace("<", "").replace(">", "")
    twiml = (
        f'<Response><Say voice="alice" language="en-IN">{say}</Say>'
        f'<Pause length="1"/><Say voice="alice" language="en-IN">{say}</Say></Response>'
    )
    try:
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json",
            data={"To": to, "From": frm, "Twiml": twiml},
            auth=(sid, auth),
            timeout=30,
        )
        print(f"[twilio] {r.status_code} {'call queued' if r.ok else r.text[:200]}")
        return r.ok
    except requests.RequestException as e:
        print(f"[twilio] failed: {e}")
        return False


def callmebot_call(spoken):
    """Best-effort Telegram voice call via CallMeBot (free tier often skips
    calls with 'FLOOD'). Returns True if CallMeBot says the call started."""
    user = os.environ.get("CALLMEBOT_TG_USER")
    if not user:
        return False
    try:
        r = requests.get(
            "https://api.callmebot.com/start.php",
            params={"user": user, "text": spoken[:250], "lang": "en-IN-Standard-A", "rpt": 3},
            timeout=120,
        )
        body = re.sub(r"<script.*?</script>", "", r.text, flags=re.DOTALL | re.IGNORECASE)
        body = re.sub(r"<style.*?</style>", "", body, flags=re.DOTALL | re.IGNORECASE)
        body = re.sub(r"<[^>]+>", " ", body)
        is_error = "not authorized" in body.lower() or "error" in body.lower() or "flood" in body.lower()
        if is_error:
            clean_msg = " ".join(body.split())[:160]
            print(f"[callmebot] {clean_msg}")
            return False
        print(f"[callmebot] {r.status_code} call started")
        return r.ok
    except requests.RequestException as e:
        print(f"[callmebot] failed: {e}")
        return False


def place_call(spoken):
    """Twilio phone call first (reliable), then a Telegram call as an extra."""
    ok_twilio = twilio_call(spoken)
    ok_tg = callmebot_call(spoken)
    if not (ok_twilio or ok_tg):
        print("[call] no call provider configured / all failed")
    return ok_twilio or ok_tg


# --------------------------------------------------------------------------- #
# main check
# --------------------------------------------------------------------------- #
def load_json(p, default):
    try:
        return json.loads(Path(p).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def check_once(cfg, dry_run=False):
    target_dates = get_target_dates(cfg)
    if not target_dates:
        print("[error] no date or dates configured in watch.json")
        return

    state = load_json(STATE_PATH, {})
    first_run = not state.get("initialized")
    seen_open = set(state.get("open", []))   # "district:<cinema_id>:<date>" / "bms:<code>:<date>"
    prev_avail = state.get("avail", {})      # "district:<cid>:<date>:<sid>" -> int (good seats only)
    need = int(cfg.get("seats_needed", 3))
    cutoff = cfg.get("seat_alert_shows_before")  # "HH:MM" or None

    events = []      # (priority, call_summary, tg_msg)
    snapshot = []    # human-readable view of what matched
    new_avail = dict(prev_avail)

    # ---- District ----
    for target_date in target_dates:
        try:
            sessions = district_sessions(cfg, target_date)
        except Exception as e:  # keep going with BMS
            print(f"[district] fetch/parse failed for {target_date}: {e}")
            sessions = None

        if sessions is not None:
            by_cinema = {}
            for s in sessions:
                g = group_for(cfg, s["cinema"])
                if not g or not session_ok(cfg, g, s):
                    continue
                by_cinema.setdefault((s["cinema_id"], s["cinema"], g["name"]), []).append(s)

            for (cid, cname, gname), ss in by_cinema.items():
                g = next(x for x in cfg["groups"] if x["name"] == gname)
                ss.sort(key=lambda x: x["time"])
                shows = ", ".join(f'{x["time_text"]} ({x["avail"]} left)' for x in ss)
                snapshot.append(f"[District] {cname} ({target_date}): {shows}")

                okey = f"district:{cid}:{target_date}"
                if g.get("alert_on_open") and okey not in seen_open:
                    d_url = cfg["district_url"].format(date=target_date)
                    call_summary = f"OPENED on District: {cname} for {target_date}"
                    tg_msg = (
                        f"🎟️ [District] Booking OPEN!\n\n"
                        f"🏢 Theatre: {cname}\n"
                        f"🎬 Movie: {cfg['movie']}\n"
                        f"📅 Date: {target_date}\n"
                        f"🎭 Format: {ss[0]['fmt']}\n"
                        f"⏰ Shows: {shows}\n\n"
                        f"🔗 Book on District:\n{d_url}"
                    )
                    events.append((0, call_summary, tg_msg))
                seen_open.add(okey)

                for x in ss:
                    qualifying = get_qualifying_areas(x["areas"], need, cfg)
                    good_avail = sum(a["avail"] for a in qualifying)
                    k = f"district:{cid}:{target_date}:{x['sid']}"
                    new_avail[k] = good_avail

                    if not g.get("alert_on_seats"):
                        continue
                    if cutoff and x["time"] > cutoff:
                        continue
                    before = prev_avail.get(k)
                    # Fire when the show goes from "not enough" to "enough", or when
                    # a batch of seats is released (jump of >= need in non-front sections).
                    if good_avail >= need and (
                        before is None and not first_run and okey in state.get("open", [])
                        or (before is not None and before < need)
                        or (before is not None and good_avail - before >= need)
                    ):
                        d_url = cfg["district_url"].format(date=target_date)
                        call_summary = f"Seats available on District: {cname} {x['time_text']}"
                        area_lines = "\n".join(
                            f"  • {a['label']}: {a['avail']} seats available (₹{a['price']:.2f})"
                            for a in qualifying
                        )
                        tg_msg = (
                            f"🚨 [District] SEATS AVAILABLE ({need}+ Seats Together)\n\n"
                            f"🏢 Theatre: {cname}\n"
                            f"🎬 Movie: {cfg['movie']}\n"
                            f"📅 Date: {target_date}\n"
                            f"⏰ Show: {x['time_text']} ({x['fmt']})\n\n"
                            f"💺 Available Section(s):\n"
                            f"{area_lines}\n"
                            f"🚫 Front rows excluded\n\n"
                            f"🔗 Book on District:\n{d_url}"
                        )
                        events.append((1, call_summary, tg_msg))

    prev_avail = new_avail

    # ---- BookMyShow ----
    # BMS needs ScraperAPI from GitHub (credits), so throttle it separately.
    bms_every = int(cfg.get("bms_every_minutes", 0)) * 60
    last_bms = state.get("last_bms", 0)
    should_check_bms = True
    if not cfg.get("bms_url"):
        should_check_bms = False
    elif bms_every and time.time() - last_bms < bms_every - 30:
        print(f"[bms] skipped (checked {int(time.time() - last_bms)}s ago)")
        should_check_bms = False

    if should_check_bms:
        bms_success = False
        for target_date in target_dates:
            try:
                venues = bms_venues(cfg, target_date)
                bms_success = True
            except Exception as e:
                print(f"[bms] fetch failed for {target_date}: {e}")
                venues = None

            if venues is not None:
                for code, slug in venues.items():
                    g = group_for(cfg, slug.replace("-", " "), site="bms")
                    if not g:
                        continue
                    snapshot.append(f"[BMS] {slug} ({code}) is listing {target_date}")
                    okey = f"bms:{code}:{target_date}"
                    if g.get("alert_on_open") and okey not in seen_open:
                        b_url = cfg["bms_url"].format(yyyymmdd=target_date.replace("-", ""), date=target_date)
                        theatre_title = slug.replace("-", " ").title()
                        call_summary = f"OPENED on BookMyShow: {theatre_title} for {target_date}"
                        tg_msg = (
                            f"🎟️ [BookMyShow] Booking OPEN!\n\n"
                            f"🏢 Theatre: {theatre_title} ({code})\n"
                            f"🎬 Movie: {cfg['movie']}\n"
                            f"📅 Date: {target_date}\n\n"
                            f"🎉 Shows are now listed on BookMyShow for this date!\n\n"
                            f"🔗 Book on BookMyShow:\n{b_url}"
                        )
                        events.append((0, call_summary, tg_msg))
                    seen_open.add(okey)
        if bms_success:
            last_bms = int(time.time())

    # ---- report ----
    print("\n".join(snapshot) or "(no watched theatre is listing the dates yet)")

    new_state = {
        "initialized": True,
        "open": sorted(seen_open),
        "avail": prev_avail,
        "last_call": state.get("last_call", 0),
        "last_bms": last_bms,
        "checked_at": int(time.time()),
    }

    if dry_run:
        print(f"\n[dry-run] {len(events)} event(s) generated:")
        for p, summary, tg_msg in events:
            print(f"\n--- [Event priority {p}] {summary} ---\n{tg_msg}")
        return

    dates_label = ", ".join(target_dates)
    if first_run:
        # Baseline: don't phone you about things that were already true.
        msg = (f"👀 Watcher armed for {cfg['movie']} on {dates_label}.\n"
               "Already listed right now:\n" + ("\n".join(snapshot) or "nothing yet"))
        send_telegram(msg)
        events = []

    if events:
        events.sort(key=lambda e: e[0])
        # Send individual, dedicated message for each theatre/event
        for _, _, tg_msg in events:
            send_telegram(tg_msg)
            time.sleep(0.35)

        cooldown = int(cfg.get("call_cooldown_minutes", 10)) * 60
        if time.time() - new_state["last_call"] >= cooldown or any(p == 0 for p, _, _ in events):
            top_summary = events[0][1]
            spoken = f"Alert. {cfg['movie']} tickets. {top_summary}. Open Telegram and book now."
            if place_call(spoken):
                new_state["last_call"] = int(time.time())

    STATE_PATH.write_text(json.dumps(new_state, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0, help="seconds between checks (0 = run once)")
    ap.add_argument("--dry-run", action="store_true", help="print matches, no alerts, no state")
    ap.add_argument("--test-call", action="store_true", help="just place a test call and exit")
    args = ap.parse_args()

    cfg = load_json(CONFIG_PATH, None)
    if not cfg:
        sys.exit(f"missing/invalid {CONFIG_PATH}")

    if args.test_call:
        send_telegram("✅ Test alert from Movie-Alert watcher.")
        place_call("This is a test call from your movie ticket watcher.")
        return

    while True:
        print(time.strftime("\n== %Y-%m-%d %H:%M:%S =="))
        try:
            check_once(cfg, dry_run=args.dry_run)
        except Exception as e:
            print(f"[error] {e}")
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
