# rubika_bot.py (نسخه نهایی برای استقرار در Render)

# --- بخش ۱: وارد کردن کتابخانه‌ها ---
import json
import os
import time
import logging
from datetime import datetime, timedelta
from threading import Thread
import requests
from sambanova import SambaNova, SambaNovaError
from flask import Flask

# --- بخش ۲: تنظیمات اولیه و خواندن متغیرهای محیطی ---
# این مقادیر از بخش Environment Variables در Render خوانده می‌شوند
RUBIKA_BOT_TOKEN = "EHEAE0JKCJHAIGIFAUMEJWIYALFDMGVTZSPFODGWHJZBWDJNVVXJUCXQAABAVEFA"
SAMBA_API_KEY = "5aa637f3-7ba6-4422-a8db-926dd29f84ef"
ADMIN_GUID = "b0Dnp03e31983616e7a50e88eca6795b"
CARD_NUMBER = "6219-8619-5635-3857"
CARD_HOLDER = "احسان حسین زاده"

# نام فایل پایگاه داده
# در Render، سیستم فایل موقتی است. برای پایداری داده‌ها، بهتر است از سرویس دیتابیس Render استفاده شود.
# اما برای شروع و سادگی، از فایل JSON استفاده می‌کنیم.
DB_FILE = "bot_database.json"

# تنظیمات لاگ‌گیری
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# متغیرهای وضعیت
user_states = {}
selected_models = {}
update_offset = 0

# مدل‌های هوش مصنوعی
VISION_MODELS = ["Llama-4-Maverick-17B-128E-Instruct"]
TEXT_MODELS = ["DeepSeek-V3.1", "gpt-oss-120b", "Qwen3-32B"]
AI_MODELS = VISION_MODELS + TEXT_MODELS

# --- بخش ۳: کلاس ارتباط با API روبیکا ---
class RubikaBot:
    """یک کلاس ساده برای مدیریت درخواست‌ها به API پیام‌رسان روبیکا."""
    def __init__(self, token):
        self.token = token
        self.base_url = f"https://messenger.iranlms.ir/v1/bots/{self.token}/"

    def _send_request(self, method, data):
        url = self.base_url + method
        try:
            response = requests.post(url, json=data, timeout=20)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"Error connecting to Rubika API: {e}")
            return None

    def get_updates(self, offset=0):
        data = {"limit": 50, "timeout": 0, "offset": offset}
        return self._send_request("getUpdates", data)

    def send_message(self, chat_id, text, inline_keyboard=None, message_id=None):
        data = {"chat_id": chat_id, "text": text}
        if message_id:
            data["reply_to_message_id"] = message_id
        if inline_keyboard:
            data["reply_markup"] = {"inline_keyboard": inline_keyboard}
        return self._send_request("sendMessage", data)

# --- بخش ۴: توابع مدیریت پایگاه داده (JSON) ---
def setup_database():
    """پایگاه داده را با ساختار اولیه ایجاد یا به‌روزرسانی می‌کند."""
    if not os.path.exists(DB_FILE):
        initial_data = {
            "users": {},
            "plans": {
                "برنزی": {"price": 10000, "duration_days": 30, "models": ["DeepSeek-V3.1"]},
                "نقره‌ای": {"price": 25000, "duration_days": 30, "models": ["DeepSeek-V3.1", "gpt-oss-120b"]},
                "طلایی": {"price": 50000, "duration_days": 90, "models": AI_MODELS}
            },
            "pending_payments": {},
            "settings": {
                "forced_join_enabled": False,
                "forced_join_channels": []
            }
        }
        write_data(initial_data)
    else:
        try:
            data = read_data()
            if "settings" not in data:
                data["settings"] = {"forced_join_enabled": False, "forced_join_channels": []}
                write_data(data)
        except json.JSONDecodeError:
             os.remove(DB_FILE)
             setup_database()


def read_data():
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def write_data(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def get_user(user_guid):
    return read_data()["users"].get(str(user_guid))

def activate_user_plan(user_guid, plan_name):
    db = read_data()
    plan_info = db["plans"][plan_name]
    duration = plan_info["duration_days"]
    expiry_date = datetime.now() + timedelta(days=duration)
    db["users"][str(user_guid)] = {
        "plan": plan_name,
        "expiry_date": expiry_date.strftime("%Y-%m-%d %H:%M:%S")
    }
    if str(user_guid) in db["pending_payments"]:
        del db["pending_payments"][str(user_guid)]
    write_data(db)

def is_user_plan_active(user_guid):
    user = get_user(user_guid)
    if not user: return False
    try:
        expiry_date = datetime.strptime(user["expiry_date"], "%Y-%m-%d %H:%M:%S")
        return expiry_date > datetime.now()
    except (ValueError, TypeError):
        return False

def get_settings():
    return read_data()["settings"]

def toggle_forced_join():
    db = read_data()
    db["settings"]["forced_join_enabled"] = not db["settings"]["forced_join_enabled"]
    write_data(db)
    return db["settings"]["forced_join_enabled"]

def add_channel(channel_id):
    db = read_data()
    if channel_id not in db["settings"]["forced_join_channels"]:
        db["settings"]["forced_join_channels"].append(channel_id)
        write_data(db)
        return True
    return False

def remove_channel(channel_id):
    db = read_data()
    if channel_id in db["settings"]["forced_join_channels"]:
        db["settings"]["forced_join_channels"].remove(channel_id)
        write_data(db)
        return True
    return False


# --- بخش ۵: راه‌اندازی کلاینت‌ها ---
bot = RubikaBot(RUBIKA_BOT_TOKEN)
samba_client = SambaNova(api_key=SAMBA_API_KEY) if SAMBA_API_KEY else None


# --- بخش ۶: توابع کمکی ربات ---
def is_admin(user_guid):
    return user_guid == ADMIN_GUID

def check_membership_and_proceed(user_guid, chat_id, callback_data_on_success):
    """بررسی می‌کند که آیا کاربر باید عضو شود یا خیر."""
    settings = get_settings()
    if not settings["forced_join_enabled"] or not settings["forced_join_channels"]:
        handle_callback_query({"author_object_guid": user_guid, "message": {"chat_id": chat_id}, "data": callback_data_on_success})
        return

    channels_list = "\n".join([f"@{ch}" if not ch.startswith(("c0", "g0")) else ch for ch in settings["forced_join_channels"]])
    
    join_keyboard = [[{"text": "عضو شدم ✅", "callback_data": f"joined_{callback_data_on_success}"}]]
    bot.send_message(
        chat_id,
        f"برای استفاده از ربات، لطفاً ابتدا در کانال(های) زیر عضو شوید:\n\n{channels_list}\n\nسپس روی دکمه 'عضو شدم' کلیک کنید.",
        inline_keyboard=join_keyboard
    )


# --- بخش ۷: کیبوردهای ربات ---
def main_menu_keyboard():
    return [[{"text": "انتخاب مدل هوش مصنوعی 🤖", "callback_data": "select_model"}],
            [{"text": "خرید یا تمدید اشتراک 💳", "callback_data": "buy_plan"}],
            [{"text": "حساب کاربری من 👤", "callback_data": "my_account"}]]

def admin_panel_keyboard():
    return [[{"text": "پرداخت‌های در انتظار ⏳", "callback_data": "admin_pending"}],
            [{"text": "تنظیمات عضویت اجباری 📢", "callback_data": "admin_forced_join"}]]


# --- بخش ۸: منطق اصلی و پردازشگرها ---
def process_update(update):
    """هر آپدیت جدید را به پردازشگر مناسب ارسال می‌کند."""
    try:
        if "message" in update:
            handle_message(update["message"])
        elif "callback_query" in update:
            handle_callback_query(update["callback_query"])
    except Exception as e:
        logging.error(f"Error processing update: {update}\nError: {e}")

def handle_message(message):
    user_guid = message["author_object_guid"]
    chat_id = message["chat_id"]
    text = message.get("text")

    if is_admin(user_guid):
        current_state = user_states.get(user_guid)
        if current_state == "awaiting_channel_to_add":
            if add_channel(text.strip()):
                bot.send_message(chat_id, f"✅ کانال/گروه `{text}` با موفقیت اضافه شد.", inline_keyboard=[[{"text": "بازگشت", "callback_data": "admin_forced_join"}]])
            else:
                bot.send_message(chat_id, f"⚠️ کانال `{text}` از قبل وجود دارد.", inline_keyboard=[[{"text": "بازگشت", "callback_data": "admin_forced_join"}]])
            if user_guid in user_states: del user_states[user_guid]
            return
        
        if current_state == "awaiting_channel_to_remove":
            if remove_channel(text.strip()):
                bot.send_message(chat_id, f"✅ کانال/گروه `{text}` با موفقیت حذف شد.", inline_keyboard=[[{"text": "بازگشت", "callback_data": "admin_forced_join"}]])
            else:
                bot.send_message(chat_id, f"⚠️ کانال `{text}` یافت نشد.", inline_keyboard=[[{"text": "بازگشت", "callback_data": "admin_forced_join"}]])
            if user_guid in user_states: del user_states[user_guid]
            return

    if text == "/start":
        check_membership_and_proceed(user_guid, chat_id, "show_main_menu")
    elif text == "/admin" and is_admin(user_guid):
        bot.send_message(chat_id, "پنل مدیریت برای شما فعال است.", inline_keyboard=admin_panel_keyboard())
    
    # Placeholder for AI text processing logic
    # You need to check for user plan, selected model, etc.

def handle_callback_query(callback):
    user_guid = callback["author_object_guid"]
    chat_id = callback["message"]["chat_id"]
    data = callback["data"]
    
    if data.startswith("joined_"):
        original_callback = data.replace("joined_", "", 1)
        bot.send_message(chat_id, "از عضویت شما سپاسگزاریم! اکنون می‌توانید از ربات استفاده کنید.")
        handle_callback_query({"author_object_guid": user_guid, "message": {"chat_id": chat_id}, "data": original_callback})
        return

    if data == "show_main_menu":
        bot.send_message(chat_id, "سلام! به ربات هوش مصنوعی خوش آمدید. لطفاً یک گزینه را انتخاب کنید:", inline_keyboard=main_menu_keyboard())

    elif data == "buy_plan":
        plans = read_data()["plans"]
        keyboard = []
        for name, info in plans.items():
            keyboard.append([{"text": f"{name} - {info['price']} تومان", "callback_data": f"plan_{name}"}])
        keyboard.append([{"text": "➡️ بازگشت", "callback_data": "show_main_menu"}])
        bot.send_message(chat_id, "لطفاً یکی از پلن‌های زیر را برای خرید انتخاب کنید:", inline_keyboard=keyboard)
    
    elif data.startswith("plan_"):
        plan_name = data.replace("plan_", "")
        plan_info = read_data()["plans"][plan_name]
        message_text = (
            f"شما پلن **{plan_name}** را انتخاب کردید.\n\n"
            f"**قیمت:** {plan_info['price']} تومان\n"
            f"**مدت زمان:** {plan_info['duration_days']} روز\n"
            f"**مدل‌های در دسترس:** {', '.join(plan_info['models'])}\n\n"
            f"لطفاً مبلغ را به شماره کارت زیر واریز کرده و سپس **عکس واضح از رسید** را برای ربات ارسال کنید:\n"
            f"`{CARD_NUMBER}`\n"
            f"**به نام:** {CARD_HOLDER}"
        )
        bot.send_message(chat_id, message_text)
        user_states[user_guid] = {"state": "awaiting_receipt", "plan": plan_name}

    elif is_admin(user_guid):
        if data == "admin_forced_join":
            settings = get_settings()
            status_text = "فعال ✅" if settings["forced_join_enabled"] else "غیرفعال ❌"
            toggle_btn_text = "غیرفعال کردن" if settings["forced_join_enabled"] else "فعال کردن"
            
            keyboard = [
                [{"text": f"وضعیت فعلی: {status_text}", "callback_data": "no_action"}],
                [{"text": toggle_btn_text, "callback_data": "toggle_forced_join"}],
                [{"text": "افزودن کانال", "callback_data": "add_channel"}, {"text": "حذف کانال", "callback_data": "remove_channel"}],
                [{"text": "مشاهده لیست کانال‌ها", "callback_data": "list_channels"}],
                [{"text": "➡️ بازگشت به پنل ادمین", "callback_data": "back_to_admin_panel"}],
            ]
            bot.send_message(chat_id, "تنظیمات عضویت اجباری:", inline_keyboard=keyboard)
        
        elif data == "toggle_forced_join":
            is_enabled = toggle_forced_join()
            status = "فعال" if is_enabled else "غیرفعال"
            bot.send_message(chat_id, f"عضویت اجباری با موفقیت {status} شد.")
            handle_callback_query({"author_object_guid": user_guid, "message": {"chat_id": chat_id}, "data": "admin_forced_join"})

        elif data == "add_channel":
            user_states[user_guid] = "awaiting_channel_to_add"
            bot.send_message(chat_id, "لطفاً شناسه (ID) کانال یا گروه مورد نظر را ارسال کنید (مثال: `c0abcdef` یا `MyChannelID`).")

        elif data == "remove_channel":
            user_states[user_guid] = "awaiting_channel_to_remove"
            bot.send_message(chat_id, "لطفاً شناسه (ID) کانال یا گروهی که می‌خواهید حذف کنید را ارسال کنید.")

        elif data == "list_channels":
            channels = get_settings()["forced_join_channels"]
            msg = "هیچ کانالی در لیست وجود ندارد." if not channels else f"لیست کانال‌های عضویت اجباری:\n" + "\n".join(f"- `{ch}`" for ch in channels)
            bot.send_message(chat_id, msg, inline_keyboard=[[{"text": "بازگشت", "callback_data": "admin_forced_join"}]])

        elif data == "back_to_admin_panel":
             bot.send_message(chat_id, "پنل مدیریت:", inline_keyboard=admin_panel_keyboard())


# --- بخش ۹: حلقه اصلی اجرای ربات ---
def main_bot_loop():
    """حلقه اصلی که به طور مداوم آپدیت‌ها را از روبیکا دریافت می‌کند."""
    global update_offset
    logging.info("Bot logic is starting...")
    while True:
        try:
            if not RUBIKA_BOT_TOKEN or not samba_client:
                logging.warning("One or more critical environment variables are not set. Bot logic is paused.")
                time.sleep(60)
                continue

            updates = bot.get_updates(offset=update_offset)
            if updates and updates.get("data", {}).get("updates"):
                for update in updates["data"]["updates"]:
                    update_id = update["update_id"]
                    update_offset = update_id + 1
                    Thread(target=process_update, args=(update,)).start()
        except Exception as e:
            logging.error(f"An error occurred in the main bot loop: {e}")
            time.sleep(15)
        time.sleep(1)

# --- بخش ۱۰: وب سرور Flask برای آنلاین نگه داشتن ربات ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive and running!"

def run_web_server():
    # وب سرور روی پورتی که Render اختصاص می‌دهد اجرا می‌شود.
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- بخش ۱۱: اجرای نهایی ---
if __name__ == '__main__':
    setup_database()
    
    # ربات را در یک نخ جداگانه اجرا کن
    bot_thread = Thread(target=main_bot_loop)
    bot_thread.start()
    
    # وب سرور را در نخ اصلی اجرا کن
    logging.info("Starting web server to keep bot alive...")
    run_web_server()
