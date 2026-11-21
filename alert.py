import os, re, json, time, requests
from playwright.sync_api import sync_playwright
from twilio.rest import Client

try:
    from dotenv import load_dotenv
    load_dotenv()          # only matters when running locally
except ImportError:
    pass

LANGUAGES = {
    "english": {"code": "ET00526306", "et_codes": "*"},
    "tamil":   {"code": "ET00526312", "et_codes": "ET00526312"},
}
DATES = ["20261217", "20261218", "20261219", "20261220"]

THEATRES = {
    "GK Cinemas Porur": [
        "GK Cinemas RGB",
        "GK Cinemas RGB + Laser SRL 4D",
        "GK Cinemas RGB + Laser SRL 4D: Porur",
    ],
    "INOX National": ["INOX National", "INOX: National", "National, Arcot Road"],
    "AGS Maduravoyal": ["AGS Cinemas: Maduravoyal", "AGS Cinemas Maduravoyal"],
    "AGS Villivakkam": ["AGS Cinemas: Villivakkam", "AGS Cinemas Villivakkam"],
    "SB Cinemas": [
        "SB Cinemas",
        "SB Cinemas (Sri Bhagavathi)",
        "SB Cinemas (Sri Bhagavathi) 4K Atmos",
    ],
}

STATE_FILE = "alerted.json"

TG_TOKEN = os.environ["TG_TOKEN"]
TG_CHAT_ID = os.environ["TG_CHAT_ID"]
TW_SID = os.environ["TW_SID"]
TW_TOKEN = os.environ["TW_TOKEN"]
TW_NUMBER = os.environ["TW_NUMBER"]
MY_PHONE = os.environ["MY_PHONE"]

twilio = Client(TW_SID, TW_TOKEN)


def normalize(s):
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return " ".join(s.split())


NORMALIZED = {n: [normalize(a) for a in al] for n, al in THEATRES.items()}


def load_state():
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(sorted(state), f, indent=2)


def build_url(lang, date):
    cfg = LANGUAGES[lang]
    code = cfg["code"]
    return (
        f"https://in.bookmyshow.com/movies/chennai/avengers-doomsday/buytickets/{code}/{date}"
        f"?etCodes={cfg['et_codes']}&language={lang}&refEventCode={code}"
    )


def send_telegram(msg):
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data={"chat_id": TG_CHAT_ID, "text": msg},
        timeout=15,
    )
    r.raise_for_status()


def send_call(spoken):
    twilio.calls.create(
        twiml=f"<Response><Say>{spoken}</Say></Response>",
        from_=TW_NUMBER,
        to=MY_PHONE,
    )


def alert(msg, spoken):
    for name, fn in [("telegram", lambda: send_telegram(msg)),
                     ("call", lambda: send_call(spoken))]:
        try:
            fn()
        except Exception as e:
            print(f"{name} failed:", e)


def check(page, lang, date, state):
    url = build_url(lang, date)
    page.goto(url, timeout=60000)
    page.wait_for_timeout(5000)
    text = f" {normalize(page.inner_text('body'))} "

    for canonical, aliases in NORMALIZED.items():
        key = f"{lang}|{date}|{canonical}"
        if key in state:
            continue
        if any(f" {a} " in text for a in aliases):
            state.add(key)
            alert(
                f"[{lang.upper()}] Shows OPEN at {canonical} on {date[6:]} Dec! "
                f"Book 3 seats, not front rows: {url}",
                f"Avengers Doomsday {lang} shows are open at {canonical} "
                f"on December {date[6:]}. Book 3 seats, not front rows.",
            )


def main():
    send_telegram("Test: GitHub Actions is working.")
    state = load_state()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for lang in LANGUAGES:
            for d in DATES:
                try:
                    check(page, lang, d, state)
                except Exception as e:
                    print(f"error ({lang}, {d}):", e)
        browser.close()
    save_state(state)


if __name__ == "__main__":
    main()
