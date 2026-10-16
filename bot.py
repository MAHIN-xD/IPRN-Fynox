import time
import re
import os
import random
import threading
import sqlite3
import requests
import telebot
from telebot import types
from http.server import HTTPServer, BaseHTTPRequestHandler

# ----------------- Configuration -----------------
BOT_TOKEN = "8676287522:AAGWr_LjYRGp-N4edxGj067pgWxS-O3u_KE"
IPRN_TOKEN = "241803|c4n8xh1N1Oy8dhbfIxBhCLl1EeMSQ36X76Cuj3lI4b40f61d"
BASE_URL = "https://api.iprn.pro/api/stock/public"

# Checker API Configuration
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

# Session state: {user_id: {"activation_id": ..., "phone": ..., "thread_active": bool}}
user_sessions = {}

# ----------------- Chinese Names Generator -----------------
CHINESE_SURNAMES = ["王", "李", "张", "刘", "陈", "杨", "黄", "赵", "周", "吴", "徐", "孙", "马", "朱", "胡", "郭", "何", "高", "林", "郑"]
CHINESE_GIVEN_NAMES = ["伟", "芳", "娜", "敏", "静", "秀英", "丽", "强", "磊", "军", "洋", "勇", "艳", "杰", "娟", "涛", "明", "超", "浩", "宇", "俊", "欣", "晨", "睿"]

CHINESE_BIOS = [
    "保持热爱，奔赴山海。",
    "生活明朗，万物可爱。",
    "顺其自然，随遇而安。",
    "知足常乐，珍惜眼前。",
    "热爱可抵岁月漫长。",
    "愿所有努力都不被辜负。"
]

def generate_chinese_identity():
    surname = random.choice(CHINESE_SURNAMES)
    given_name = random.choice(CHINESE_GIVEN_NAMES)
    bio = random.choice(CHINESE_BIOS)
    return surname, given_name, bio

# ----------------- Database Setup -----------------
def init_db():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            user_id INTEGER PRIMARY KEY,
            otp_count INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS used_numbers (
            phone TEXT PRIMARY KEY
        )
    """)
    conn.commit()
    conn.close()

init_db()

def increment_otp_count(user_id):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO stats (user_id, otp_count) VALUES (?, 1)
        ON CONFLICT(user_id) DO UPDATE SET otp_count = otp_count + 1
    """, (user_id,))
    conn.commit()
    conn.close()

def get_otp_count(user_id):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT otp_count FROM stats WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0

def mark_number_used(phone):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO used_numbers (phone) VALUES (?)", (phone,))
    conn.commit()
    conn.close()

# ----------------- OTP Extractor -----------------
def extract_otp(text):
    if not text:
        return "N/A"
    
    m = re.search(r'(?:code|kod|código|verification|login)[\s:]*([0-9\-\s]{4,8})', text, re.IGNORECASE)
    if m:
        cleaned = re.sub(r'\D', '', m.group(1))
        if 4 <= len(cleaned) <= 8:
            return cleaned

    numbers = re.findall(r'\b\d{4,8}\b', text)
    if numbers:
        return numbers[0]

    return "Check Text"

# ----------------- Telegram Checker Logic -----------------
FRESH_KEYWORDS = {"unoccupied", "unregistered", "not_registered", "free", "fresh", "available", "false", "0", 0, False}

def check_number_tg_status(phone: str) -> str:
    cleaned = phone if str(phone).startswith("+") else f"+{phone}"
    payload = {"numbers": [cleaned]}
    try:
        r = requests.post(CHECKER_API_URL, json=payload, headers=CHECKER_HEADERS, timeout=6)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                num_data = data.get(cleaned, {})
                status = str(num_data.get("status", "")).lower()
                registered = num_data.get("registered", None)

                if status in FRESH_KEYWORDS or registered is False:
                    return "fresh"
                elif "ban" in status:
                    return "banned"
                elif status in ["occupied", "registered", "taken", "used", "true", "1", 1] or registered is True:
                    return "occupied"
                elif status in ["flood", "locked", "lock", "wait", "restricted", "2fa"]:
                    return "locked"
    except Exception as e:
        print(f"Checker Error: {e}")
    return "unknown"

# ----------------- IPRN API Helpers -----------------
def get_balance():
    try:
        r = requests.get(f"{BASE_URL}/balance", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            data = r.json()
            return f"{data.get('amount')} {data.get('currency')}"
    except Exception:
        pass
    return "Error"

def get_first_available_product():
    try:
        r = requests.get(f"{BASE_URL}/activation-products", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            products = r.json().get("data", [])
            for p in products:
                if p.get("available", 0) > 0:
                    return p.get("destination"), p.get("payment_terms")
    except Exception:
        pass
    return None, None

def reserve_number():
    dest, terms = get_first_available_product()
    if not dest or not terms:
        return None, "No stock"
    
    url = f"{BASE_URL}/activations?destination={requests.utils.quote(dest)}&payment_terms={requests.utils.quote(terms)}"
    try:
        r = requests.post(url, headers=HEADERS, timeout=5)
        if r.status_code == 201:
            data = r.json()
            return data.get("data", data), "OK"
        elif r.status_code == 422:
            return None, "No stock"
    except Exception:
        pass
    return None, "Error"

def cancel_activation(act_id):
    if not act_id:
        return
    try:
        requests.delete(f"{BASE_URL}/activations/{act_id}", headers=HEADERS, timeout=5)
    except Exception:
        pass

def check_activation_sms(act_id):
    try:
        r = requests.get(f"{BASE_URL}/activations/{act_id}", headers=HEADERS, timeout=6)
        if r.status_code == 200:
            data = r.json()
            return data.get("data", data)
    except Exception:
        pass
    return None

# ----------------- Format Success Message -----------------
def build_success_message(phone, otp_code, raw_msg):
    surname, given_name, bio = generate_chinese_identity()
    return (
        f"✅ *SMS Received!*\n\n"
        f"📱 Number: `+{phone}`\n"
        f"🔑 Code: `{otp_code}`\n\n"
        f"🇨🇳 *Telegram Anti-Freeze Setup:*\n"
        f"• First Name: `{given_name}`\n"
        f"• Last Name: `{surname}`\n"
        f"• Full Name: `{surname}{given_name}`\n"
        f"• Bio: `{bio}`\n\n"
        f"📩 Message:\n`{raw_msg}`"
    )

# ----------------- Keyboards -----------------
def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(types.KeyboardButton("📱 Get Number"))
    markup.row(types.KeyboardButton("💰 Balance"), types.KeyboardButton("📊 My Stats"))
    return markup

def action_inline_keyboard(act_id):
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("🔄 Refresh / Check OTP", callback_data=f"refresh_{act_id}")
    )
    markup.row(
        types.InlineKeyboardButton("🔁 Change Number", callback_data=f"change_{act_id}"),
        types.InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{act_id}")
    )
    return markup

# ----------------- Worker Function -----------------
def sms_polling_worker(chat_id, user_id, act_id, phone, msg_id):
    start_time = time.time()
    while time.time() - start_time < 600:
        session = user_sessions.get(user_id)
        if not session or session.get("activation_id") != act_id or not session.get("thread_active"):
            break

        data = check_activation_sms(act_id)
        if data:
            status = str(data.get("status", "")).lower()
            messages = data.get("messages", [])

            if messages or status == "received":
                raw_msg = ""
                if isinstance(messages, list) and len(messages) > 0:
                    raw_msg = messages[0].get("message", "")
                elif isinstance(messages, dict):
                    raw_msg = messages.get("message", "")

                otp_code = extract_otp(raw_msg)
                
                increment_otp_count(user_id)
                mark_number_used(phone)

                text = build_success_message(phone, otp_code, raw_msg)
                try:
                    # আগের নম্বরের মেসেজটা সরাসরি এডিট করে ওটিপি ও চাইনিজ প্রোফাইল বানিয়ে দেবে
                    bot.edit_message_text(text, chat_id, msg_id)
                except Exception:
                    bot.send_message(chat_id, text)

                user_sessions.pop(user_id, None)
                break

            if status in ["cancelled", "expired"]:
                break

        time.sleep(2)

# ----------------- Core Flow -----------------
def fetch_fresh_number_loop(chat_id, user_id, status_msg_id):
    max_retries = 6

    for attempt in range(1, max_retries + 1):
        session = user_sessions.get(user_id)
        if not session or not session.get("thread_active"):
            return

        try:
            bot.edit_message_text(f"⏳ Reserving number... (Attempt {attempt})", chat_id, status_msg_id)
        except Exception:
            pass

        data, status = reserve_number()
        if status == "No stock":
            bot.edit_message_text("⚠️ *No numbers available in stock right now.*", chat_id, status_msg_id)
            user_sessions.pop(user_id, None)
            return
        elif status == "Error" or not data:
            bot.edit_message_text("❌ *Failed to get number from server.*", chat_id, status_msg_id)
            user_sessions.pop(user_id, None)
            return

        act_id = data.get("id")
        phone = data.get("phone")
        dest = data.get("destination", "N/A")

        try:
            bot.edit_message_text(f"🔍 Checking TG status for `+{phone}`...", chat_id, status_msg_id)
        except Exception:
            pass

        tg_status = check_number_tg_status(phone)

        if tg_status in ["fresh", "unknown"]:
            session["activation_id"] = act_id
            session["phone"] = phone

            text = (
                f"📱 *New Number Reserved*\n\n"
                f"Country: *{dest}*\n"
                f"TG Status: *Fresh / Available* ✅\n"
                f"Number: `+{phone}` (tap to copy)\n\n"
                f"⏳ _Waiting for SMS..._"
            )
            bot.edit_message_text(text, chat_id, status_msg_id, reply_markup=action_inline_keyboard(act_id))

            t = threading.Thread(target=sms_polling_worker, args=(chat_id, user_id, act_id, phone, status_msg_id))
            t.daemon = True
            t.start()
            return
        else:
            cancel_activation(act_id)
            time.sleep(1)

    bot.edit_message_text("⚠️ *Could not find a fresh number right now. Try again later.*", chat_id, status_msg_id)
    user_sessions.pop(user_id, None)

def handle_get_or_change(message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    old_session = user_sessions.get(user_id)
    if old_session:
        old_session["thread_active"] = False
        cancel_activation(old_session.get("activation_id"))
        user_sessions.pop(user_id, None)

    wait_msg = bot.send_message(chat_id, "⏳ Contacting server...")

    user_sessions[user_id] = {
        "activation_id": None,
        "phone": None,
        "thread_active": True
    }

    t = threading.Thread(target=fetch_fresh_number_loop, args=(chat_id, user_id, wait_msg.message_id))
    t.daemon = True
    t.start()

# ----------------- Message Handlers -----------------
@bot.message_handler(commands=['start'])
def start_cmd(message):
    bot.send_message(
        message.chat.id, 
        "🤖 *IPRN TG Bot Ready.*\nSelect an option below:", 
        reply_markup=main_menu()
    )

@bot.message_handler(func=lambda m: m.text == "📱 Get Number")
def btn_get_number(message):
    handle_get_or_change(message)

@bot.message_handler(func=lambda m: m.text == "💰 Balance")
def btn_balance(message):
    bal = get_balance()
    bot.send_message(message.chat.id, f"💰 Balance: *{bal}*")

@bot.message_handler(func=lambda m: m.text == "📊 My Stats")
def btn_stats(message):
    count = get_otp_count(message.from_user.id)
    bot.send_message(message.chat.id, f"📊 Total OTPs Received: *{count}*")

# ----------------- Inline Callbacks -----------------
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    data = call.data

    if data.startswith("cancel_"):
        act_id = data.split("_")[1]
        session = user_sessions.get(user_id)
        if session and session.get("activation_id") == act_id:
            session["thread_active"] = False
            user_sessions.pop(user_id, None)
        
        cancel_activation(act_id)
        bot.edit_message_text("❌ *Number cancelled and returned to pool.*", chat_id, call.message.message_id)

    elif data.startswith("change_"):
        bot.answer_callback_query(call.id, "Changing number...")
        handle_get_or_change(call.message)

    elif data.startswith("refresh_"):
        act_id = data.split("_")[1]
        session = user_sessions.get(user_id)
        
        res = check_activation_sms(act_id)
        if res:
            messages = res.get("messages", [])
            status = str(res.get("status", "")).lower()

            if messages or status == "received":
                raw_msg = ""
                if isinstance(messages, list) and len(messages) > 0:
                    raw_msg = messages[0].get("message", "")
                elif isinstance(messages, dict):
                    raw_msg = messages.get("message", "")

                phone = res.get("phone", session.get("phone", "N/A") if session else "N/A")
                otp_code = extract_otp(raw_msg)

                if session:
                    session["thread_active"] = False
                    user_sessions.pop(user_id, None)

                increment_otp_count(user_id)
                mark_number_used(phone)

                text = build_success_message(phone, otp_code, raw_msg)
                try:
                    bot.edit_message_text(text, chat_id, call.message.message_id)
                except Exception:
                    bot.send_message(chat_id, text)

                bot.answer_callback_query(call.id, "SMS Received!")
                return
            else:
                bot.answer_callback_query(call.id, "No SMS yet, still waiting...", show_alert=False)
        else:
            bot.answer_callback_query(call.id, "Unable to check status right now.", show_alert=False)

# ----------------- Render Dummy HTTP Server -----------------
class SimpleHealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

    def log_message(self, format, *args):
        return

def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHealthHandler)
    server.serve_forever()

if __name__ == "__main__":
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()

    print("Bot is running...")
    bot.infinity_polling()
