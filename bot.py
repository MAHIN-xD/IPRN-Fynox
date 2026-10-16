import time
import re
import os
import random
import threading
import sqlite3
import datetime
import requests
import telebot
from telebot import types
from http.server import HTTPServer, BaseHTTPRequestHandler

# ----------------- Configuration -----------------
BOT_TOKEN = "8676287522:AAGWr_LjYRGp-N4edxGj067pgWxS-O3u_KE"
IPRN_TOKEN = "241803|c4n8xh1N1Oy8dhbfIxBhCLl1EeMSQ36X76Cuj3lI4b40f61d"
BASE_URL = "https://api.iprn.pro/api/stock/public"

CHECKER_API_URL = "http://api.agbots.site:8080/check/"
CHECKER_AUTH = "user8354"
CHECKER_API_KEY = "SIGUzg7Xf7euGs8B"

HEADERS = {
    "Authorization": f"Bearer {IPRN_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json"
}

CHECKER_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": CHECKER_AUTH,
    "auth": CHECKER_AUTH,
    "api-key": CHECKER_API_KEY,
    "x-api-key": CHECKER_API_KEY
}

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")
user_sessions = {}

# ----------------- Clean Chinese Names -----------------
CHINESE_NAMES = [
    "李子轩", "王若曦", "陈思源", "张梓涵", "刘雨泽", "杨晨", "黄子涵",
    "赵雨辰", "周俊杰", "吴雨轩", "张伟", "王芳", "李娜", "刘敏", "陈静"
]

# ----------------- Database Setup -----------------
def init_db():
    conn = sqlite3.connect("bot_data.db")
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS stats (user_id INTEGER PRIMARY KEY, count INTEGER DEFAULT 0)")
    c.execute("CREATE TABLE IF NOT EXISTS used_numbers (phone TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

init_db()

def mark_done(user_id, phone):
    conn = sqlite3.connect("bot_data.db")
    c = conn.cursor()
    c.execute("INSERT INTO stats (user_id, count) VALUES (?, 1) ON CONFLICT(user_id) DO UPDATE SET count = count + 1", (user_id,))
    clean_p = re.sub(r'\D', '', str(phone))
    c.execute("INSERT OR IGNORE INTO used_numbers (phone) VALUES (?)", (clean_p,))
    conn.commit()
    conn.close()

def is_used(phone):
    clean_p = re.sub(r'\D', '', str(phone))
    conn = sqlite3.connect("bot_data.db")
    c = conn.cursor()
    c.execute("SELECT phone FROM used_numbers WHERE phone = ?", (clean_p,))
    r = c.fetchone()
    conn.close()
    return r is not None

# ----------------- Fast OTP Extractor -----------------
def get_code(text):
    if not text:
        return None
    # 5 or 6 digit Telegram code
    m = re.search(r'(?:code|kod|código|verification|login)[\s:]*([0-9]{4,8})', text, re.IGNORECASE)
    if m:
        return m.group(1)
    digits = re.findall(r'\b\d{5,6}\b', text)
    if digits:
        return digits[0]
    return None

# ----------------- TG Checker -----------------
def is_fresh(phone):
    cleaned = phone if str(phone).startswith("+") else f"+{phone}"
    try:
        r = requests.post(CHECKER_API_URL, json={"numbers": [cleaned]}, headers=CHECKER_HEADERS, timeout=5)
        if r.status_code == 200:
            res = r.json().get(cleaned, {})
            st = str(res.get("status", "")).lower()
            reg = res.get("registered", None)
            if "ban" in st or "flood" in st or "locked" in st or st in ["occupied", "registered", "taken", "used"] or reg is True:
                return False
            return True
    except Exception:
        pass
    return True

# ----------------- IPRN Functions -----------------
def api_balance():
    try:
        r = requests.get(f"{BASE_URL}/balance", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            d = r.json()
            return f"{d.get('amount')} {d.get('currency')}"
    except Exception:
        pass
    return "Error"

def reserve_number():
    try:
        r = requests.get(f"{BASE_URL}/activation-products", headers=HEADERS, timeout=5)
        if r.status_code != 200:
            return None, "Error"
        prods = r.json().get("data", [])
        prod = next((p for p in prods if p.get("available", 0) > 0), None)
        if not prod:
            return None, "No Stock"

        dest = prod.get("destination")
        terms = prod.get("payment_terms")
        url = f"{BASE_URL}/activations?destination={requests.utils.quote(dest)}&payment_terms={requests.utils.quote(terms)}"
        r = requests.post(url, headers=HEADERS, timeout=6)
        if r.status_code == 201:
            d = r.json()
            return d.get("data", d), "OK"
    except Exception:
        pass
    return None, "Error"

def cancel_act(act_id):
    if act_id:
        try:
            requests.delete(f"{BASE_URL}/activations/{act_id}?activation_id={act_id}", headers=HEADERS, timeout=5)
        except Exception:
            pass

def fetch_sms(act_id, phone):
    clean_p = re.sub(r'\D', '', str(phone))

    # 1. Check Panel/EDR
    try:
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        r = requests.get(f"{BASE_URL}/edr?page=1&perPage=15&day={today}", headers=HEADERS, timeout=4)
        if r.status_code == 200:
            for item in r.json().get("data", []):
                b_num = re.sub(r'\D', '', str(item.get("b_number", "")))
                if b_num and (clean_p.endswith(b_num) or b_num.endswith(clean_p)):
                    return item.get("message")
    except Exception:
        pass

    # 2. Check Activation endpoint
    try:
        r = requests.get(f"{BASE_URL}/activations/{act_id}?activation_id={act_id}", headers=HEADERS, timeout=4)
        if r.status_code == 200:
            data = r.json().get("data", {})
            msgs = data.get("messages", [])
            if msgs and len(msgs) > 0:
                return msgs[0].get("message")
            if data.get("status") == "received" and data.get("message"):
                return data.get("message")
    except Exception:
        pass

    return None

# ----------------- UI Builders -----------------
def inline_menu(act_id):
    m = types.InlineKeyboardMarkup()
    m.row(
        types.InlineKeyboardButton("🔄 Check Status", callback_data=f"chk_{act_id}"),
        types.InlineKeyboardButton("🔁 Change", callback_data=f"chg_{act_id}"),
        types.InlineKeyboardButton("❌ Cancel", callback_data=f"cnc_{act_id}")
    )
    return m

def main_kb():
    k = types.ReplyKeyboardMarkup(resize_keyboard=True)
    k.row(types.KeyboardButton("📱 Get Number"), types.KeyboardButton("💰 Balance"))
    return k

# ----------------- Polling Worker -----------------
def poll_sms(chat_id, user_id, act_id, phone, msg_id, name):
    start = time.time()
    while time.time() - start < 300: # 5 mins
        sess = user_sessions.get(user_id)
        if not sess or sess.get("act_id") != act_id or not sess.get("active"):
            break

        msg = fetch_sms(act_id, phone)
        if msg:
            code = get_code(msg) or "See Text Below"
            mark_done(user_id, phone)

            out = (
                f"✅ *OTP RECEIVED!*\n\n"
                f"📱 Number: `+{phone}`\n"
                f"🔑 Code: `{code}`\n"
                f"👤 Name: `{name}`\n\n"
                f"📩 *Message:*\n`{msg}`"
            )
            try:
                bot.edit_message_text(out, chat_id, msg_id)
            except Exception:
                bot.send_message(chat_id, out)

            user_sessions.pop(user_id, None)
            break

        time.sleep(2)

# ----------------- Core Flow -----------------
def process_number(chat_id, user_id, status_msg_id):
    for _ in range(6):
        sess = user_sessions.get(user_id)
        if not sess or not sess.get("active"):
            return

        data, status = reserve_number()
        if status == "No Stock":
            bot.edit_message_text("⚠️ *No numbers available in stock.*", chat_id, status_msg_id)
            user_sessions.pop(user_id, None)
            return
        elif not data or status != "OK":
            time.sleep(1)
            continue

        act_id = data.get("id")
        phone = data.get("phone")

        if is_used(phone):
            cancel_act(act_id)
            continue

        if not is_fresh(phone):
            cancel_act(act_id)
            continue

        # Fresh Number Found!
        name = random.choice(CHINESE_NAMES)
        sess["act_id"] = act_id
        sess["phone"] = phone
        sess["name"] = name

        text = (
            f"📱 Number: `+{phone}`\n"
            f"👤 Name: `{name}`\n\n"
            f"⏳ *Status:* Waiting for SMS..."
        )
        bot.edit_message_text(text, chat_id, status_msg_id, reply_markup=inline_menu(act_id))

        t = threading.Thread(target=poll_sms, args=(chat_id, user_id, act_id, phone, status_msg_id, name))
        t.daemon = True
        t.start()
        return

    bot.edit_message_text("❌ *Failed to get fresh number. Please try again.*", chat_id, status_msg_id)
    user_sessions.pop(user_id, None)

def get_new_number_cmd(message):
    uid = message.from_user.id
    cid = message.chat.id

    old = user_sessions.get(uid)
    if old:
        old["active"] = False
        cancel_act(old.get("act_id"))
        user_sessions.pop(uid, None)

    msg = bot.send_message(cid, "⏳ *Getting fresh number...*")
    user_sessions[uid] = {"act_id": None, "phone": None, "active": True}

    t = threading.Thread(target=process_number, args=(cid, uid, msg.message_id))
    t.daemon = True
    t.start()

# ----------------- Handlers -----------------
@bot.message_handler(commands=['start'])
def start_h(m):
    bot.send_message(m.chat.id, "🤖 *Bot Ready.* Press button below:", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "📱 Get Number")
def get_h(m):
    get_new_number_cmd(m)

@bot.message_handler(func=lambda m: m.text == "💰 Balance")
def bal_h(m):
    bot.send_message(m.chat.id, f"💰 Balance: *{api_balance()}*")

@bot.callback_query_handler(func=lambda c: True)
def cb_h(c):
    uid = c.from_user.id
    cid = c.message.chat.id
    data = c.data

    if data.startswith("cnc_"):
        act_id = data.split("_")[1]
        sess = user_sessions.get(uid)
        if sess and sess.get("act_id") == act_id:
            sess["active"] = False
            user_sessions.pop(uid, None)
        cancel_act(act_id)
        bot.edit_message_text("❌ *Cancelled.*", cid, c.message.message_id)

    elif data.startswith("chg_"):
        bot.answer_callback_query(c.id, "Changing...")
        get_new_number_cmd(c.message)

    elif data.startswith("chk_"):
        act_id = data.split("_")[1]
        sess = user_sessions.get(uid)
        phone = sess.get("phone", "") if sess else ""
        name = sess.get("name", "李子轩") if sess else "李子轩"

        msg = fetch_sms(act_id, phone)
        if msg:
            code = get_code(msg) or "See Text Below"
            mark_done(uid, phone)
            if sess:
                sess["active"] = False
                user_sessions.pop(uid, None)
            out = (
                f"✅ *OTP RECEIVED!*\n\n"
                f"📱 Number: `+{phone}`\n"
                f"🔑 Code: `{code}`\n"
                f"👤 Name: `{name}`\n\n"
                f"📩 *Message:*\n`{msg}`"
            )
            bot.edit_message_text(out, cid, c.message.message_id)
            bot.answer_callback_query(c.id, "Received!")
        else:
            bot.answer_callback_query(c.id, "Still waiting for SMS...", show_alert=False)

# ----------------- HTTP Health Server -----------------
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        return

if __name__ == "__main__":
    t = threading.Thread(target=lambda: HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8080))), H).serve_forever(), daemon=True)
    t.start()
    bot.infinity_polling()
