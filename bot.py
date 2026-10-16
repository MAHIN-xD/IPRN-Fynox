import time
import re
import os
import random
import threading
import sqlite3
import datetime
import requests
import pandas as pd
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

# Tumar Telegram ID boshao
ADMIN_IDS = [7266067201]

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

# ----------------- Extended Chinese Names -----------------
CHINESE_NAMES = [
    "李子轩", "王若曦", "陈思源", "张梓涵", "刘雨泽", "杨晨", "黄子涵", "赵雨辰", "周俊杰", "吴雨轩",
    "张伟", "王芳", "李娜", "刘敏", "陈静", "杨秀英", "黄丽", "赵强", "周磊", "吴军",
    "徐洋", "孙勇", "马艳", "朱杰", "胡娟", "郭涛", "何明", "高超", "林浩", "郑宇",
    "谢天宇", "罗浩然", "宋嘉豪", "唐晓峰", "许志强", "韩建华", "邓文博", "冯伟伦", "曹宇轩", "彭明辉",
    "曾祥龙", "萧敬腾", "田思博", "董建国", "袁志远", "潘子轩", "于海洋", "蒋伟峰", "蔡文德", "余天赐",
    "杜少卿", "叶俊熙", "苏明玉", "魏子安", "程飞龙", "吕秀才", "丁春秋", "沈万三", "任盈盈", "姚明哲"
]

# ----------------- Database Setup -----------------
def init_db():
    conn = sqlite3.connect("bot_data.db")
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS stats (user_id INTEGER PRIMARY KEY, count INTEGER DEFAULT 0)")
    c.execute("CREATE TABLE IF NOT EXISTS used_numbers (phone TEXT PRIMARY KEY)")
    c.execute("""
        CREATE TABLE IF NOT EXISTS file_numbers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE,
            is_used INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_user_otp_count(user_id):
    conn = sqlite3.connect("bot_data.db")
    c = conn.cursor()
    c.execute("SELECT count FROM stats WHERE user_id = ?", (user_id,))
    r = c.fetchone()
    conn.close()
    return r[0] if r else 0

def increment_user_otp(user_id, phone):
    clean_p = re.sub(r'\D', '', str(phone))
    conn = sqlite3.connect("bot_data.db")
    c = conn.cursor()
    c.execute("INSERT INTO stats (user_id, count) VALUES (?, 1) ON CONFLICT(user_id) DO UPDATE SET count = count + 1", (user_id,))
    c.execute("INSERT OR IGNORE INTO used_numbers (phone) VALUES (?)", (clean_p,))
    c.execute("UPDATE file_numbers SET is_used = 1 WHERE phone = ?", (clean_p,))
    conn.commit()
    conn.close()

def is_phone_used(phone):
    clean_p = re.sub(r'\D', '', str(phone))
    conn = sqlite3.connect("bot_data.db")
    c = conn.cursor()
    c.execute("SELECT phone FROM used_numbers WHERE phone = ?", (clean_p,))
    r = c.fetchone()
    conn.close()
    return r is not None

def get_next_available_file_number():
    conn = sqlite3.connect("bot_data.db")
    c = conn.cursor()
    c.execute("SELECT id, phone FROM file_numbers WHERE is_used = 0 ORDER BY id ASC")
    rows = c.fetchall()
    
    for row_id, phone in rows:
        clean_p = re.sub(r'\D', '', str(phone))
        # Jodi already use hoye thake, skip and mark used
        if is_phone_used(clean_p):
            c.execute("UPDATE file_numbers SET is_used = 1 WHERE id = ?", (row_id,))
            conn.commit()
            continue
        
        # Lock number
        c.execute("UPDATE file_numbers SET is_used = 1 WHERE id = ?", (row_id,))
        c.execute("INSERT OR IGNORE INTO used_numbers (phone) VALUES (?)", (clean_p,))
        conn.commit()
        conn.close()
        return clean_p
        
    conn.close()
    return None

# ----------------- Fast OTP Extractor -----------------
def get_code(text):
    if not text:
        return None
    m = re.search(r'(?:code|kod|código|verification|login)[\s:]*([0-9]{4,8})', text, re.IGNORECASE)
    if m:
        return m.group(1)
    digits = re.findall(r'\b\d{5,6}\b', text)
    if digits:
        return digits[0]
    return None

# ----------------- TG Checker -----------------
def check_tg(phone):
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
    if act_id and act_id != "none":
        try:
            requests.delete(f"{BASE_URL}/activations/{act_id}?activation_id={act_id}", headers=HEADERS, timeout=5)
        except Exception:
            pass

def fetch_sms(act_id, phone):
    clean_p = re.sub(r'\D', '', str(phone))

    # 1. Check Panel/EDR
    try:
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        r = requests.get(f"{BASE_URL}/edr?page=1&perPage=25&day={today}", headers=HEADERS, timeout=4)
        if r.status_code == 200:
            for item in r.json().get("data", []):
                b_num = re.sub(r'\D', '', str(item.get("b_number", "")))
                if b_num and (clean_p.endswith(b_num) or b_num.endswith(clean_p)):
                    return item.get("message")
    except Exception:
        pass

    # 2. Check Activation endpoint
    if act_id and act_id != "none":
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
def poll_sms(chat_id, user_id, act_id, phone, msg_id, name, my_otp_count):
    start = time.time()
    while time.time() - start < 300: # 5 minutes timeout
        sess = user_sessions.get(user_id)
        if not sess or sess.get("phone") != phone or not sess.get("active"):
            break

        msg = fetch_sms(act_id, phone)
        if msg:
            code = get_code(msg) or "See Text Below"
            increment_user_otp(user_id, phone)

            out = (
                f"✅ *OTP RECEIVED!*\n\n"
                f"📱 Number: `+{phone}`\n"
                f"🔑 Code: `{code}`\n"
                f"👤 Name: `{name}`\n"
                f"📊 *Your Total OTPs:* `{my_otp_count + 1}`\n"
                f"⚡ Status: *Active / Verified* ✅\n\n"
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
    my_otp_count = get_user_otp_count(user_id)

    # 1. Prothome file-er queue check korbe
    while True:
        file_phone = get_next_available_file_number()
        if not file_phone:
            break
        
        # TG checker run
        if check_tg(file_phone):
            name = random.choice(CHINESE_NAMES)
            sess = user_sessions.get(user_id)
            if sess:
                sess["act_id"] = "none"
                sess["phone"] = file_phone
                sess["name"] = name
                sess["my_otp"] = my_otp_count

            text = (
                f"📊 *Your Total OTPs:* `{my_otp_count}`\n\n"
                f"📱 Number: `+{file_phone}` (tap to copy)\n"
                f"👤 Name: `{name}` (tap to copy)\n"
                f"⚡ TG Status: *Fresh / Unregistered* ✅\n\n"
                f"⏳ *Status:* Waiting for SMS..."
            )
            bot.edit_message_text(text, chat_id, status_msg_id, reply_markup=inline_menu("none"))
            t = threading.Thread(target=poll_sms, args=(chat_id, user_id, "none", file_phone, status_msg_id, name, my_otp_count))
            t.daemon = True
            t.start()
            return

    # 2. File-e number na thakle IPRN API theke nibe
    for attempt in range(1, 7):
        sess = user_sessions.get(user_id)
        if not sess or not sess.get("active"):
            return

        bot.edit_message_text(f"⏳ *Reserving number... (Attempt {attempt})*", chat_id, status_msg_id)

        data, status = reserve_number()
        if status == "No Stock":
            bot.edit_message_text("⚠️ *No numbers available in stock right now.*", chat_id, status_msg_id)
            user_sessions.pop(user_id, None)
            return
        elif not data or status != "OK":
            time.sleep(1)
            continue

        act_id = data.get("id")
        phone = data.get("phone")

        if is_phone_used(phone):
            cancel_act(act_id)
            continue

        bot.edit_message_text(f"🔍 *Checking TG status for `+{phone}`...*", chat_id, status_msg_id)

        if not check_tg(phone):
            cancel_act(act_id)
            time.sleep(1)
            continue

        # Fresh Found!
        clean_p = re.sub(r'\D', '', str(phone))
        conn = sqlite3.connect("bot_data.db")
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO used_numbers (phone) VALUES (?)", (clean_p,))
        conn.commit()
        conn.close()

        name = random.choice(CHINESE_NAMES)
        sess["act_id"] = act_id
        sess["phone"] = phone
        sess["name"] = name
        sess["my_otp"] = my_otp_count

        text = (
            f"📊 *Your Total OTPs:* `{my_otp_count}`\n\n"
            f"📱 Number: `+{phone}` (tap to copy)\n"
            f"👤 Name: `{name}` (tap to copy)\n"
            f"⚡ TG Status: *Fresh / Unregistered* ✅\n\n"
            f"⏳ *Status:* Waiting for SMS..."
        )
        bot.edit_message_text(text, chat_id, status_msg_id, reply_markup=inline_menu(act_id))

        t = threading.Thread(target=poll_sms, args=(chat_id, user_id, act_id, phone, status_msg_id, name, my_otp_count))
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

    msg = bot.send_message(cid, "⏳ *Contacting server...*")
    user_sessions[uid] = {"act_id": None, "phone": None, "active": True}

    t = threading.Thread(target=process_number, args=(cid, uid, msg.message_id))
    t.daemon = True
    t.start()

# ----------------- Admin Panel & File Upload -----------------
@bot.message_handler(commands=['admin'])
def admin_cmd(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ *You are not authorized.*")
        return
    
    conn = sqlite3.connect("bot_data.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM file_numbers WHERE is_used = 0")
    remaining = c.fetchone()[0]
    conn.close()

    bot.send_message(
        message.chat.id, 
        f"👑 *Admin Panel*\n\n"
        f"📦 *Unused Numbers in Queue:* `{remaining}`\n\n"
        f"👉 Upload `.txt`, `.csv`, or `.xlsx` file containing phone numbers."
    )

@bot.message_handler(content_types=['document'])
def handle_file_upload(message):
    if message.from_user.id not in ADMIN_IDS:
        return

    doc = message.document
    file_name = doc.file_name.lower()

    if not (file_name.endswith('.txt') or file_name.endswith('.csv') or file_name.endswith('.xlsx')):
        bot.reply_to(message, "⚠️ Only `.txt`, `.csv`, or `.xlsx` files are allowed.")
        return

    msg = bot.reply_to(message, "⏳ *Processing file...*")
    file_info = bot.get_file(doc.file_id)
    downloaded = bot.download_file(file_info.file_path)

    numbers = []
    try:
        if file_name.endswith('.txt'):
            lines = downloaded.decode('utf-8', errors='ignore').splitlines()
            for line in lines:
                nums = re.findall(r'\b\d{10,15}\b', line)
                numbers.extend(nums)
        elif file_name.endswith('.csv'):
            with open("temp.csv", "wb") as f:
                f.write(downloaded)
            df = pd.read_csv("temp.csv")
            for col in df.columns:
                nums = df[col].astype(str).str.extractall(r'(\d{10,15})')[0].tolist()
                numbers.extend(nums)
            os.remove("temp.csv")
        elif file_name.endswith('.xlsx'):
            with open("temp.xlsx", "wb") as f:
                f.write(downloaded)
            df = pd.read_excel("temp.xlsx")
            for col in df.columns:
                nums = df[col].astype(str).str.extractall(r'(\d{10,15})')[0].tolist()
                numbers.extend(nums)
            os.remove("temp.xlsx")
    except Exception as e:
        bot.edit_message_text(f"❌ *Failed to read file:* `{e}`", message.chat.id, msg.message_id)
        return

    added = 0
    conn = sqlite3.connect("bot_data.db")
    c = conn.cursor()
    for num in set(numbers):
        clean = re.sub(r'\D', '', str(num))
        # Jodi age thekei used na hoy, tobei pool-e dhukbe
        if not is_phone_used(clean):
            try:
                c.execute("INSERT INTO file_numbers (phone, is_used) VALUES (?, 0)", (clean,))
                added += 1
            except Exception:
                pass
    conn.commit()
    conn.close()

    bot.edit_message_text(f"✅ *Upload Complete!*\n\n📥 Total Extracted: `{len(numbers)}`\n🆕 Added to Queue: `{added}`", message.chat.id, msg.message_id)

# ----------------- Message Handlers -----------------
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
        if sess and sess.get("phone"):
            sess["active"] = False
            user_sessions.pop(uid, None)
        if act_id != "none":
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
        my_otp_count = sess.get("my_otp", 0) if sess else 0

        msg = fetch_sms(act_id if act_id != "none" else None, phone)
        if msg:
            code = get_code(msg) or "See Text Below"
            increment_user_otp(uid, phone)
            if sess:
                sess["active"] = False
                user_sessions.pop(uid, None)
            out = (
                f"✅ *OTP RECEIVED!*\n\n"
                f"📱 Number: `+{phone}`\n"
                f"🔑 Code: `{code}`\n"
                f"👤 Name: `{name}`\n"
                f"📊 *Your Total OTPs:* `{my_otp_count + 1}`\n"
                f"⚡ Status: *Active / Verified* ✅\n\n"
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
