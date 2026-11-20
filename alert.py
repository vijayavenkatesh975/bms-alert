import os, json, smtplib, requests
from email.message import EmailMessage
from playwright.sync_api import sync_playwright
from twilio.rest import Client

BASE_URL = "https://in.bookmyshow.com/movies/chennai/avengers-doomsday/buytickets/ET00526306/"
QUERY = "?etCodes=*&language=english&refEventCode=ET00526306"
DATES = ["20261217", "20261218", "20261219", "20261220"]
THEATRES = [
    "GK Cinemas RGB",
    "INOX National",
    "AGS Cinemas: Maduravoyal",
    "AGS Cinemas: Villivakkam",
    "SB Cinemas",
]

E = os.environ
TG_TOKEN, TG_CHAT_ID = E["TG_TOKEN"], E["TG_CHAT_ID"]
GMAIL, GMAIL_APP_PASSWORD = E["GMAIL"], E["GMAIL_APP_PASSWORD"]
TW_SID, TW_TOKEN = E["TW_SID"], E["TW_TOKEN"]
TW_NUMBER, MY_PHONE, TW_WHATSAPP = E["TW_NUMBER"], E["MY_PHONE"], E["TW_WHATSAPP"]

twilio = Client(TW_SID, TW_TOKEN)
STATE_FILE = "alerted.json"

def load_state():
    try:
        with open(STATE_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(sorted(s), f)

def send_email(msg):
    m = EmailMessage()
    m["Subject"] = "BMS ALERT: Avengers Doomsday"
    m["From"] = m["To"] = GMAIL
    m.set_content(msg)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL, GMAIL_APP_PASSWORD)
        s.send_message(m)

def alert(msg):
    for name, fn in [
        ("telegram", lambda: requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT_ID, "text": msg})),
        ("email", lambda: send_email(msg)),
        ("sms", lambda: twilio.messages.create(body=msg, from_=TW_NUMBER, to=MY_PHONE)),
        ("whatsapp", lambda: twilio.messages.create(
            body=msg, from_=TW_WHATSAPP, to=f"whatsapp:{MY_PHONE}")),
        ("call", lambda: twilio.calls.create(
            twiml=f"<Response><Say>{msg}</Say></Response>",
            from_=TW_NUMBER, to=MY_PHONE)),
    ]:
        try:
            fn()
        except Exception as e:
            print(f"{name} failed:", e)

state = load_state()

if E.get("SEND_TEST") == "true":
    alert("GitHub test: alert system is working.")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    for date in DATES:
        try:
            page.goto(BASE_URL + date + QUERY, timeout=60000)
            page.wait_for_timeout(5000)
            text = page.inner_text("body").lower()
            print(date, "| page text starts:", text[:150].replace("\n", " "))
            for t in THEATRES:
                key = f"{date}|{t}"
                if t.lower() in text and key not in state:
                    state.add(key)
                    alert(f"Shows OPEN at {t} on {date[6:]} Dec! Book 3 seats, not front rows: {BASE_URL + date + QUERY}")
        except Exception as e:
            print(date, "error:", e)
    browser.close()

save_state(state)
