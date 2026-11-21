import os, re, json, requests
from xml.sax.saxutils import escape
from playwright.sync_api import sync_playwright
from twilio.rest import Client

E = os.environ
TG_TOKEN, TG_CHAT_ID = E["TG_TOKEN"], E["TG_CHAT_ID"]
TW_SID, TW_TOKEN = E["TW_SID"], E["TW_TOKEN"]
TW_NUMBER, MY_PHONE = E["TW_NUMBER"], E["MY_PHONE"]

DATES = ["20261217", "20261218", "20261219", "20261220"]
URL = "https://in.bookmyshow.com/movies/chennai/avengers-doomsday/buytickets/{code}/{date}{query}"
SITES = [
    {"lang": "English", "code": "ET00526306",
     "query": "?etCodes=*&language=english&refEventCode=ET00526306"},
    {"lang": "Tamil", "code": "ET00526312",
     "query": "?etCodes=ET00526312&language=tamil&refEventCode=ET00526312"},
]
THEATRES = {
    "GK Cinemas Porur": ["GK Cinemas RGB", "GK Cinemas RGB + Laser SRL 4D",
                         "GK Cinemas RGB + Laser SRL 4D: Porur", "GK Cinemas", "GKCP"],
    "INOX National": ["INOX National", "INOX: National", "National, Arcot Road"],
    "AGS Maduravoyal": ["AGS Cinemas: Maduravoyal", "AGS Cinemas Maduravoyal"],
    "AGS Villivakkam": ["AGS Cinemas: Villivakkam", "AGS Cinemas Villivakkam"],
    "SB Cinemas": ["SB Cinemas", "SB Cinemas (Sri Bhagavathi)",
                   "SB Cinemas (Sri Bhagavathi) 4K Atmos"],
}

twilio = Client(TW_SID, TW_TOKEN)
STATE_FILE = "alerted.json"

def load_state():
    try:
        with open(STATE_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()

def norm(s):
    return " " + re.sub(r"[^a-z0-9]+", " ", s.lower()).strip() + " "

def telegram(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      data={"chat_id": TG_CHAT_ID, "text": msg}, timeout=30)
    except Exception as e:
        print("telegram failed:", e)

def call(speech):
    try:
        twilio.calls.create(
            twiml=f"<Response><Say>{escape(speech)}</Say></Response>",
            from_=TW_NUMBER, to=MY_PHONE)
    except Exception as e:
        print("call failed:", e)

state = load_state()
if E.get("SEND_TEST") == "true":
    telegram("GitHub test: alert system is working.")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    for date in DATES:
        for site in SITES:
            try:
                link = URL.format(code=site["code"], date=date, query=site["query"])
                page.goto(link, timeout=60000)
                page.wait_for_timeout(5000)
                raw = page.inner_text("body")
                if "you have been blocked" in raw.lower():
                    print(date, site["lang"], "BLOCKED by BookMyShow")
                    continue
                print(date, site["lang"], "page loaded OK")
                text = norm(raw)
                for name, aliases in THEATRES.items():
                    key = f"{date}|{site['lang']}|{name}"
                    if key not in state and any(norm(a) in text for a in aliases):
                        state.add(key)
                        telegram(f"SHOWS OPEN: {name} ({site['lang']}) on {date[6:]} Dec. Book 3 seats, not front rows.\n{link}")
                        call(f"Avengers Doomsday bookings are open at {name}, {site['lang']}, on {date[6:]} December. Check Telegram.")
            except Exception as e:
                print(date, site["lang"], "error:", e)
    browser.close()

with open(STATE_FILE, "w") as f:
    json.dump(sorted(state), f)
