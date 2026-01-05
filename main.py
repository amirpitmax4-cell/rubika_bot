import asyncio
import logging
import os
import re
import secrets
import contextlib
from threading import Thread
import time  # اضافه شده برای تایمر ذخیره‌سازی
from flask import Flask
from telegram import (Update, ReplyKeyboardMarkup, KeyboardButton,
                      InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove)
from telegram.constants import ParseMode
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          ConversationHandler, filters, ContextTypes, CallbackQueryHandler,
                          ApplicationHandlerStop, TypeHandler)
import telegram.error # اضافه شده برای هندل کردن ارورها
from zoneinfo import ZoneInfo
from datetime import datetime, timezone
import html
import traceback
import json
import pymongo  # اضافه شده برای دیتابیس

# =======================================================
#  بخش ۱: تنظیمات اولیه و پیکربندی
# =======================================================

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
logging.getLogger("httpx").setLevel(logging.WARNING)

# --- Environment Variables & Constants ---
# توکن و آیدی عددی طبق درخواست شما ثابت شدند
BOT_TOKEN = "8312913531:AAH3amVB1izD9kcprwQDmW0rm7iBs9LlmVQ"

# آیدی عددی مالک ربات
OWNER_ID = 6365669757

TEHRAN_TIMEZONE = ZoneInfo("Asia/Tehran")

# --- MongoDB Connection (اتصال به دیتابیس) ---
# آدرس اتصال به دیتابیس
MONGO_URI = "mongodb+srv://amirpitmax66_db_user:zBIKQBP1fKbMzfK1@cluster0.virpqtw.mongodb.net/?appName=Cluster0"
DB_NAME = "telegram_bot_data"  # نام دیتابیس در مونگو

try:
    mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = mongo_client[DB_NAME]
    # تست اتصال
    mongo_client.server_info()
    logging.info("✅ Connected to MongoDB successfully.")
except Exception as e:
    logging.error(f"❌ Failed to connect to MongoDB: {e}")
    db = None

# --- In-Memory Database (دیتابیس درون حافظه‌ای) ---
# این متغیرها اکنون با دیتابیس سینک می‌شوند
GLOBAL_USERS = {}
GLOBAL_SETTINGS = {}
GLOBAL_TRANSACTIONS = {}
GLOBAL_BETS = {}
GLOBAL_CHANNELS = {}

# (شمارنده‌های سراسری برای ID ها)
TX_ID_COUNTER = 1
BET_ID_COUNTER = 1


def init_memory_db():
    """
    Initializes the in-memory settings from MongoDB if available,
    otherwise uses defaults.
    """
    global TX_ID_COUNTER, BET_ID_COUNTER
    logging.info("Initializing database (Loading from MongoDB)...")
    
    if db is not None:
        # 1. بارگذاری تنظیمات
        try:
            for doc in db.settings.find():
                GLOBAL_SETTINGS[doc['_id']] = doc['value']
        except Exception as e: logging.error(f"Error loading settings: {e}")

        # 2. بارگذاری کاربران
        try:
            for doc in db.users.find():
                user_id = int(doc['user_id'])
                GLOBAL_USERS[user_id] = doc
        except Exception as e: logging.error(f"Error loading users: {e}")

        # 3. بارگذاری تراکنش‌ها و تنظیم شمارنده
        try:
            max_tx_id = 0
            for doc in db.transactions.find():
                tx_id = int(doc['tx_id'])
                GLOBAL_TRANSACTIONS[tx_id] = doc
                if tx_id > max_tx_id:
                    max_tx_id = tx_id
            TX_ID_COUNTER = max_tx_id + 1
        except Exception as e: logging.error(f"Error loading transactions: {e}")

        # 4. بارگذاری شرط‌ها و تنظیم شمارنده
        try:
            max_bet_id = 0
            for doc in db.bets.find():
                bet_id = int(doc['bet_id'])
                GLOBAL_BETS[bet_id] = doc
                if bet_id > max_bet_id:
                    max_bet_id = bet_id
            BET_ID_COUNTER = max_bet_id + 1
        except Exception as e: logging.error(f"Error loading bets: {e}")

        # 5. بارگذاری کانال‌ها
        try:
            for doc in db.channels.find():
                GLOBAL_CHANNELS[doc['channel_username']] = doc
        except Exception as e: logging.error(f"Error loading channels: {e}")

    # تنظیم مقادیر پیش‌فرض اگر در دیتابیس نبودند
    default_settings = {
        'credit_price': '1000',
        'initial_balance': '10',
        'referral_reward': '5',
        'bet_tax_rate': '2',
        'card_number': 'هنوز تنظیم نشده',
        'card_holder': 'هنوز تنظیم نشده',
        'bet_photo_file_id': 'None',
        'forced_channel_lock': 'false'
    }
    
    for key, value in default_settings.items():
        if key not in GLOBAL_SETTINGS:
            GLOBAL_SETTINGS[key] = value
    
    logging.info(f"Database loaded. Users: {len(GLOBAL_USERS)}, Tx Counter: {TX_ID_COUNTER}")


def background_db_sync():
    """
    این تابع در پس‌زمینه اجرا می‌شود و هر ۱۰ ثانیه
    اطلاعات را در دیتابیس ذخیره می‌کند.
    """
    while True:
        if db is None:
            time.sleep(20)
            continue
        
        try:
            # ذخیره کاربران
            for user_id, data in list(GLOBAL_USERS.items()):
                db.users.replace_one({'user_id': user_id}, data, upsert=True)
            
            # ذخیره تنظیمات
            for key, value in list(GLOBAL_SETTINGS.items()):
                db.settings.replace_one({'_id': key}, {'value': value}, upsert=True)
            
            # ذخیره تراکنش‌ها
            for tx_id, data in list(GLOBAL_TRANSACTIONS.items()):
                db.transactions.replace_one({'tx_id': tx_id}, data, upsert=True)
            
            # ذخیره شرط‌ها
            for bet_id, data in list(GLOBAL_BETS.items()):
                db.bets.replace_one({'bet_id': bet_id}, data, upsert=True)
            
            # ذخیره کانال‌ها
            for ch_username, data in list(GLOBAL_CHANNELS.items()):
                db.channels.replace_one({'channel_username': ch_username}, data, upsert=True)

        except Exception as e:
            logging.error(f"Error in DB Sync loop: {e}")
        
        time.sleep(10)  # هر ۱۰ ثانیه ذخیره کن

def save_user_immediate(user_id):
    """ذخیره فوری اطلاعات یک کاربر خاص برای اعمال سریع تغییرات ادمین"""
    if db is None or user_id not in GLOBAL_USERS:
        return
    try:
        db.users.replace_one({'user_id': user_id}, GLOBAL_USERS[user_id], upsert=True)
    except Exception as e:
        logging.error(f"Immediate save failed for {user_id}: {e}")

# --- Global Variables & State Management ---
BOT_EVENT_LOOP = None

# --- Conversation Handler States ---
# اضافه شدن استیت‌های جدید برای افزایش و کسر موجودی
(ADMIN_MENU, AWAIT_ADMIN_REPLY,
 AWAIT_DEPOSIT_AMOUNT, AWAIT_DEPOSIT_RECEIPT, # اضافه شد
 AWAIT_SUPPORT_MESSAGE, AWAIT_ADMIN_SUPPORT_REPLY,
 AWAIT_NEW_CHANNEL, AWAIT_BET_PHOTO,
 AWAIT_ADMIN_SET_BALANCE, AWAIT_ADMIN_TAX, AWAIT_ADMIN_CREDIT_PRICE,
 AWAIT_ADMIN_REFERRAL_PRICE, AWAIT_ADMIN_SET_BALANCE_ID,
 AWAIT_MANAGE_USER_ID, AWAIT_MANAGE_USER_ROLE,
 AWAIT_ADMIN_SET_CARD_NUMBER, AWAIT_ADMIN_SET_CARD_HOLDER, # اضافه شد
 AWAIT_ADMIN_ADD_BALANCE_ID, AWAIT_ADMIN_ADD_BALANCE_AMOUNT, 
 AWAIT_ADMIN_DEDUCT_BALANCE_ID, AWAIT_ADMIN_DEDUCT_BALANCE_AMOUNT,
 AWAIT_BROADCAST_MESSAGE # پیام همگانی
) = range(22)


# =======================================================
#  بخش ۲: وب اپلیکیشن Flask (فقط برای Health Check)
# =======================================================
web_app = Flask(__name__)

@web_app.route('/')
def health_check():
    """Health check endpoint for Render."""
    return "TNT Bot is running with MongoDB.", 200

# =======================================================
#  بخش ۳: توابع کمکی ربات
# =======================================================

async def get_setting_async(name):
    """Gets a setting from the in-memory GLOBAL_SETTINGS."""
    return GLOBAL_SETTINGS.get(name)

async def set_setting_async(name, value):
    """Sets a setting in the in-memory GLOBAL_SETTINGS and saves to DB immediately."""
    GLOBAL_SETTINGS[name] = str(value)
    if db is not None:
        try:
            db.settings.replace_one({'_id': name}, {'value': str(value)}, upsert=True)
        except Exception as e:
            logging.error(f"Failed to save setting {name} to DB: {e}")

async def get_user_async(user_id):
    """
    Retrieves a user document from in-memory GLOBAL_USERS,
    creating it if it doesn't exist.
    Also Checks for Ownership Security.
    """
    user_doc = None
    if user_id in GLOBAL_USERS:
        user_doc = GLOBAL_USERS[user_id]
        # اطمینان از وجود فیلد vip_balance برای کاربران قدیمی
        if 'vip_balance' not in user_doc:
             user_doc['vip_balance'] = 0
    else:
        # (کاربر وجود ندارد، یکی جدید بساز)
        try:
            initial_balance_val_str = GLOBAL_SETTINGS.get('initial_balance', '10')
            initial_balance_val = int(initial_balance_val_str)
        except (ValueError, TypeError):
            initial_balance_val = 10

        is_owner = (user_id == OWNER_ID)
        # موجودی اولیه برای اونر زیاد است، اما بعدا قابل تغییر است
        balance_on_create = 1000000000 if is_owner else initial_balance_val

        new_user_doc = {
            'user_id': user_id,
            'balance': balance_on_create,
            'vip_balance': 0, # موجودی VIP جداگانه
            'is_admin': is_owner,
            'is_owner': is_owner,
            'referred_by': None,
            'is_moderator': False,
            'username': None, # اضافه شده برای ذخیره یوزرنیم
            'first_name': None 
        }
        GLOBAL_USERS[user_id] = new_user_doc
        user_doc = new_user_doc
        
        # ذخیره کاربر جدید در دیتابیس
        if db is not None:
            try:
                db.users.replace_one({'user_id': user_id}, new_user_doc, upsert=True)
            except Exception as e:
                logging.error(f"Failed to save new user {user_id} to DB: {e}")
    
    # === SECURITY CHECK FOR OWNER SWAP (تغییر یافته طبق دستور) ===
    # اگر کاربری که الان دارد درخواست میدهد، OWNER_ID تعریف شده در کد است،
    # باید مطمئن شویم دسترسی‌هاش در دیتابیس هم ست شده باشد.
    if user_id == OWNER_ID:
        if not user_doc.get('is_owner') or not user_doc.get('is_admin'):
            user_doc['is_owner'] = True
            user_doc['is_admin'] = True
            save_user_immediate(user_id)
            
    # مهم: اگر کاربری قبلاً در دیتابیس is_owner بوده ولی الان آیدی اش با OWNER_ID کد فرق دارد
    # (یعنی کد روی سرور جدید با ادمین جدید ران شده)، باید خلع مقام شود و موجودی اش صفر شود و دسترسی پنل قطع شود.
    elif user_doc.get('is_owner') and user_id != OWNER_ID:
        logging.warning(f"SECURITY: Stripping ownership from old owner {user_id}. New owner is {OWNER_ID}.")
        user_doc['is_owner'] = False
        user_doc['is_admin'] = False
        user_doc['is_moderator'] = False
        user_doc['balance'] = 0 
        save_user_immediate(user_id)

    return user_doc

def get_user_display_name(user):
    """Gets a safe display name for a user (username or first/last name)."""
    # ذخیره اطلاعات کاربر در گلوبال برای استفاده‌های بعدی
    if user.id in GLOBAL_USERS:
        GLOBAL_USERS[user.id]['username'] = user.username
        GLOBAL_USERS[user.id]['first_name'] = user.first_name

    if user.username:
        return f"@{user.username}"
    
    name = user.first_name
    if user.last_name:
        name += f" {user.last_name}"
    return html.escape(name)

# --- Keyboards ---
def get_main_keyboard(user_doc):
    # لاجیک منو طبق درخواست:
    # اگر کاربر "مالک اصلی" (OWNER_ID) باشد، فقط دکمه پنل ادمین و موجودی را ببیند.
    # ادمین های قدیمی یا کاربران عادی منوی کامل را میبینند.
    
    if user_doc.get('is_owner'):
        keyboard = [
            [KeyboardButton("💰 موجودی"), KeyboardButton("👑 پنل ادمین")],
        ]
    # کاربران عادی و سایرین
    else:
        keyboard = [
            [KeyboardButton("💰 موجودی"), KeyboardButton("💳 افزایش الماس")], # بازگردانده شد
            [KeyboardButton("🎁 الماس رایگان"), KeyboardButton("💬 پشتیبانی")],
        ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# منوی ادمین اصلاح شده با دکمه‌های جدید
admin_keyboard = ReplyKeyboardMarkup([
    [KeyboardButton("📊 آمار کلی"), KeyboardButton("💳 تنظیم شماره کارت")],
    [KeyboardButton("👤 تنظیم صاحب کارت"), KeyboardButton("مدیریت کاربر")],
    [KeyboardButton("➕ افزایش الماس کاربر"), KeyboardButton("➖ کسر الماس کاربر")], # دکمه‌های جدید
    [KeyboardButton("💰 تنظیم الماس (ست)"), KeyboardButton("📈 تنظیم قیمت الماس")],
    [KeyboardButton("🎁 تنظیم پاداش دعوت"), KeyboardButton("📉 تنظیم مالیات (۰-۱۰۰)")],
    [KeyboardButton("➕ افزودن کانال عضویت"), KeyboardButton("➖ حذف کانال عضویت")],
    [KeyboardButton("👁‍🗨 لیست کانال‌های عضویت"), KeyboardButton("🔒 قفل عضویت: روشن"), KeyboardButton("🔓 قفل عضویت: خاموش")], # دکمه‌ها اصلاح شدند
    [KeyboardButton("🖼 تنظیم عکس شرط"), KeyboardButton("🗑 حذف عکس شرط")],
    [KeyboardButton("📢 پیام همگانی")], # اضافه شده
    [KeyboardButton("⬅️ بازگشت به منوی اصلی")]
], resize_keyboard=True)

bet_group_keyboard = ReplyKeyboardMarkup([
    [KeyboardButton("موجودی 💰")],
    [KeyboardButton("شرط 100"), KeyboardButton("شرط 500")],
    [KeyboardButton("شرط 1000"), KeyboardButton("شرط 5000")]
], resize_keyboard=True)

# =======================================================
#  بخش ۴: سیستم عضویت اجباری (نسخه Async)
# =======================================================

async def get_specific_join_keyboard(channels: list) -> InlineKeyboardMarkup | None:
    """Creates the keyboard for the forced join message for specific channels."""
    if not channels:
        return None

    keyboard_buttons = []
    for channel in channels:
        link = channel.get('channel_link', 'https://telegram.org')
        # نمایش اسم کانال (تایتل) اگر موجود باشد، در غیر این صورت یوزرنیم
        btn_text = channel.get('channel_title', channel.get('channel_username', 'کانال'))
        keyboard_buttons.append([
            InlineKeyboardButton(f"عضویت در {btn_text}", url=link)
        ])

    keyboard_buttons.append([InlineKeyboardButton("تایید عضویت", callback_data="check_join_membership")])
    return InlineKeyboardMarkup(keyboard_buttons)

async def membership_check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    A high-priority handler that checks channel membership before allowing any other handler to run.
    """
    user = update.effective_user
    chat = update.effective_chat
    query = update.callback_query
    
    if not user:
        return

    # اصلاح: بررسی عضویت فقط در چت خصوصی انجام شود، نه در گروه‌ها
    if chat and chat.type != 'private':
        return

    if user.id == OWNER_ID:
        return

    forced_lock_str = await get_setting_async("forced_channel_lock")
    forced_lock = forced_lock_str == 'true'
    
    if not forced_lock:
        return

    channels = list(GLOBAL_CHANNELS.values())
    
    if not channels:
        return

    not_joined_channels = []

    for channel in channels:
        channel_username = channel['channel_username']
        try:
            # ربات باید در کانال ادمین باشد تا بتواند ممبر را چک کند
            member = await context.bot.get_chat_member(channel_username, user.id)
            if member.status not in ['member', 'administrator', 'creator']:
                not_joined_channels.append(channel)
        except Exception as e:
            # اگر ربات ادمین نباشد یا کانال وجود نداشته باشد، خطا میدهد
            # در این صورت فرض میکنیم کاربر عضو نیست تا امنیت حفظ شود (یا میتوانید لاگ کنید)
            not_joined_channels.append(channel)

    # --- مدیریت دکمه "تایید عضویت" ---
    if query and query.data == "check_join_membership":
        if not not_joined_channels:
            # کاربر عضو شد - همه چیز اوکی است
            await query.answer("✅ عضویت تایید شد!")
            await query.message.delete()
            user_doc = await get_user_async(user.id)
            await context.bot.send_message(
                chat_id=user.id,
                text="✅ عضویت شما تایید شد. خوش آمدید!",
                reply_markup=get_main_keyboard(user_doc)
            )
        else:
            # کاربر هنوز عضو نشده
            await query.answer("❌ شما هنوز در تمام کانال‌ها عضو نشدید!", show_alert=True)
            
            # آپدیت لیست دکمه‌ها:
            # دکمه‌هایی که کاربر عضو شده حذف می‌شوند و فقط باقی‌مانده‌ها نشان داده می‌شوند
            new_keyboard = await get_specific_join_keyboard(not_joined_channels)
            
            try:
                # فقط دکمه‌ها را آپدیت می‌کنیم
                await query.message.edit_reply_markup(reply_markup=new_keyboard)
            except: 
                pass # اگر تغییری در دکمه‌ها نبود (یعنی کاربر هیچ کانال جدیدی جوین نشده) ارور نده
        
        raise ApplicationHandlerStop

    # --- مسدود کردن کاربر اگر عضو نباشد ---
    if not_joined_channels:
        # ساخت کیبورد فقط برای کانال‌هایی که عضو نیست
        keyboard = await get_specific_join_keyboard(not_joined_channels)
        
        # متن بسیار ساده طبق درخواست
        text = "🔒 برای استفاده از ربات، لطفا در کانال‌های زیر عضو شوید:"

        if query:
            await query.answer("⛔️ ابتدا باید عضو کانال‌ها شوید.", show_alert=True)
            # اگر کاربر روی دکمه‌ای دیگر کلیک کرده ولی عضو نیست، پیام جدید قفل را بفرست
            # سعی میکنیم پیام قبلی را حذف کنیم تا شلوغ نشود
            try:
                await query.message.delete()
            except: pass
            
            await context.bot.send_message(
                chat_id=user.id,
                text=text,
                reply_markup=keyboard
            )
        elif update.effective_message:
            await update.effective_message.reply_text(
                text=text,
                reply_markup=keyboard
            )
        
        raise ApplicationHandlerStop

    return

# =======================================================
#  بخش ۵: مدیریت دستورات کاربران (نسخه Async)
# =======================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_doc = await get_user_async(user.id)

    # بروزرسانی نام کاربر
    user_doc['username'] = user.username
    user_doc['first_name'] = user.first_name

    # اصلاح: در گروه‌ها فقط کیبورد شرط‌بندی ارسال شود، نه منوی شخصی
    if update.effective_chat.type != 'private':
        await update.message.reply_text("👋 ربات شرط‌بندی فعال است.", reply_markup=bet_group_keyboard)
        return

    if user_doc.get('is_owner'): # فقط برای مالک اصلی خوش‌آمدگویی ادمین بفرست
        total_users = len(GLOBAL_USERS)
        pending_tx = sum(1 for tx in GLOBAL_TRANSACTIONS.values() if tx['status'] == 'pending')

        admin_welcome_text = (
            f"👑 سلام مالک عزیز، به پنل مدیریت خوش آمدید!\n\n"
            f"📊 **آمار ربات (سینک شده با دیتابیس):**\n"
            f"  -  👥 **تعداد کل کاربران:** {total_users:,}\n"
            f"  -  🧾 **تراکنش‌های در انتظار:** {pending_tx:,}"
        )
        await update.message.reply_text(admin_welcome_text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_keyboard(user_doc))
    else:
        # Referral logic
        if context.args and len(context.args) > 0:
            try:
                referrer_id = int(context.args[0])
                if referrer_id != user.id and not user_doc.get('referred_by'):
                    GLOBAL_USERS[user.id]['referred_by'] = referrer_id
                    
                    reward_str = await get_setting_async('referral_reward')
                    try:
                        reward = int(reward_str or 5)
                    except (ValueError, TypeError):
                        reward = 5

                    referrer_doc = await get_user_async(referrer_id)
                    referrer_doc['balance'] += reward
                    save_user_immediate(referrer_id) # ذخیره فوری پاداش
                    
                    new_user_display_name = get_user_display_name(user)
                    await context.bot.send_message(
                        chat_id=referrer_id,
                        text=f"🎁 تبریک! کاربر {new_user_display_name} از طریق لینک شما وارد ربات شد و شما {reward} الماس پاداش گرفتید."
                    )
            except (ValueError, TypeError):
                pass

        await update.message.reply_text(
            "به Self VIP خوش آمدید.",
            reply_markup=get_main_keyboard(user_doc)
        )

async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_doc = await get_user_async(update.effective_user.id)
    price_str = await get_setting_async('credit_price')
    try:
        price = int(price_str or 1000)
    except (ValueError, TypeError):
        price = 1000
        
    balance_toman = user_doc.get('balance', 0) * price
    
    await update.message.reply_text(
        f"💰 موجودی الماس شما: **{user_doc.get('balance', 0):,}** الماس\n"
        f"💳 معادل: `{balance_toman:,}` تومان",
        parse_mode=ParseMode.MARKDOWN
    )

async def support_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("لطفا پیام خود را برای ارسال به پشتیبانی بنویسید:", reply_markup=ReplyKeyboardRemove())
    return AWAIT_SUPPORT_MESSAGE

async def process_support_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_doc = await get_user_async(user.id)
    
    admins = [u for u in GLOBAL_USERS.values() if u.get('is_admin') or u.get('is_owner')]
    
    text = f"📨 پیام پشتیبانی جدید از کاربر: {user.mention_html()}\n(ID: `{user.id}`)\n\n`{update.message.text}`"
    reply_markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("✍️ پاسخ به کاربر", callback_data=f"reply_support_{user.id}_{update.message.message_id}")
    ]])

    for admin in admins:
        try:
            await context.bot.send_message(chat_id=admin['user_id'], text=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.warning(f"Could not send support message to admin {admin['user_id']}: {e}")

    await update.message.reply_text("✅ پیام شما با موفقیت برای تیم پشتیبانی ارسال شد.", reply_markup=get_main_keyboard(user_doc))
    return ConversationHandler.END

async def get_referral_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={update.effective_user.id}"
    
    reward_str = await get_setting_async('referral_reward')
    try:
        reward = int(reward_str or 5)
    except (ValueError, TypeError):
        reward = 5

    await update.message.reply_text(
        f"🎁 لینک دعوت شما:\n\n`{link}`\n\n"
        f"با هر دعوت موفق، {reward} الماس دریافت کنید!",
        parse_mode=ParseMode.MARKDOWN
    )

# --- Deposit Conversation (بازگردانده شده) ---
async def deposit_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("لطفا تعداد الماسی که قصد خرید دارید را وارد کنید:", reply_markup=ReplyKeyboardRemove())
    return AWAIT_DEPOSIT_AMOUNT

async def process_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text)
        if amount <= 0: raise ValueError
        
        price_str = await get_setting_async('credit_price')
        try:
            price = int(price_str or 1000)
        except (ValueError, TypeError):
            price = 1000
            
        total_cost = amount * price
        context.user_data['deposit_amount'] = amount

        card_number = await get_setting_async('card_number') or "شماره کارتی تنظیم نشده"
        card_holder = await get_setting_async('card_holder') or "نامی تنظیم نشده"

        await update.message.reply_text(
            f"هزینه قابل پرداخت برای `{amount}` الماس: `{total_cost:,}` تومان\n\n"
            f"لطفا مبلغ را به کارت زیر واریز کرده و سپس عکس رسید را ارسال کنید:\n"
            f"شماره کارت: `{card_number}`\n"
            f"صاحب حساب: `{card_holder}`",
            parse_mode=ParseMode.MARKDOWN
        )
        return AWAIT_DEPOSIT_RECEIPT
    except (ValueError, TypeError):
        await update.message.reply_text("❌ لطفا یک عدد صحیح و مثبت وارد کنید.")
        return AWAIT_DEPOSIT_AMOUNT

async def process_deposit_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global TX_ID_COUNTER
    if not update.message.photo:
        await update.message.reply_text("❌ لطفا عکس رسید پرداخت را ارسال کنید.")
        return AWAIT_DEPOSIT_RECEIPT

    user = update.effective_user
    user_doc = await get_user_async(user.id)
    amount = context.user_data['deposit_amount']
    receipt_file_id = update.message.photo[-1].file_id

    tx_id = TX_ID_COUNTER
    # اضافه کردن فیلد admin_message_ids برای ذخیره پیام‌های ارسالی به ادمین‌ها
    GLOBAL_TRANSACTIONS[tx_id] = {
        'tx_id': tx_id,
        'user_id': user.id,
        'amount': amount,
        'receipt_file_id': receipt_file_id,
        'status': 'pending',
        'type': 'diamond', # نوع تراکنش
        'timestamp': datetime.now(timezone.utc),
        'admin_messages': [] # لیست پیام‌های ارسالی به ادمین‌ها
    }
    TX_ID_COUNTER += 1
    
    caption = (f"🧾 درخواست افزایش الماس جدید (ID: {tx_id})\n"
               f"کاربر: {user.mention_html()} (ID: {user.id})\n"
               f"تعداد الماس: `{amount}`")

    reply_markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تایید", callback_data=f"tx_approve_{tx_id}"),
        InlineKeyboardButton("❌ رد", callback_data=f"tx_reject_{tx_id}")
    ]])

    admins = [u for u in GLOBAL_USERS.values() if u.get('is_admin') or u.get('is_owner')]
    
    # ارسال پیام به تمام ادمین‌ها و ذخیره ID پیام برای ویرایش همگانی بعدی
    for admin in admins:
        try:
            msg = await context.bot.send_photo(
                chat_id=admin['user_id'], 
                photo=receipt_file_id, 
                caption=caption, 
                reply_markup=reply_markup, 
                parse_mode=ParseMode.HTML
            )
            # ذخیره مشخصات پیام (چت آیدی و مسیج آیدی)
            GLOBAL_TRANSACTIONS[tx_id]['admin_messages'].append({
                'chat_id': admin['user_id'],
                'message_id': msg.message_id
            })
        except Exception as e:
            logging.warning(f"Could not send receipt to admin {admin['user_id']}: {e}")

    await update.message.reply_text("✅ رسید شما برای ادمین ارسال شد. پس از تایید، الماس شما شارژ خواهد شد.", reply_markup=get_main_keyboard(user_doc))
    context.user_data.clear()
    return ConversationHandler.END

# =======================================================
#  بخش ۶: مدیریت دستورات ادمین (نسخه Async)
# =======================================================
async def admin_panel_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_doc = await get_user_async(update.effective_user.id)
    
    # تغییر: فقط مالک اصلی می‌تواند وارد پنل شود
    if not user_doc.get('is_owner'):
        # اگر ادمین یا مادریتور است اما اونر نیست
        if user_doc.get('is_admin') or user_doc.get('is_moderator'):
             await update.message.reply_text("⛔️ دسترسی به تنظیمات پنل فقط برای مالک اصلی مجاز است.\nشما می‌توانید از دستورات ربات (مثل کسر موجودی) در گروه‌ها استفاده کنید.")
        else:
             await update.message.reply_text("⛔️ شما دسترسی به این بخش را ندارید.")
        return ConversationHandler.END

    await update.message.reply_text("👑 به پنل ادمین خوش آمدید:", reply_markup=admin_keyboard)
    return ADMIN_MENU

async def show_channels_for_removal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows an inline keyboard of channels for removal."""
    channels = list(GLOBAL_CHANNELS.values())
    
    if not channels:
        await update.message.reply_text("هیچ کانالی برای حذف کردن وجود ندارد.", reply_markup=admin_keyboard)
        return ADMIN_MENU

    keyboard = []
    for channel in channels:
        # نمایش اسم کانال در لیست حذف برای تشخیص راحت‌تر
        display_name = channel.get('channel_title', channel['channel_username'])
        keyboard.append([
            InlineKeyboardButton(
                display_name, 
                callback_data=f"admin_remove_{channel['channel_username']}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("لغو", callback_data="admin_remove_cancel")])
    
    await update.message.reply_text(
        "لطفا کانالی که می‌خواهید حذف شود را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ADMIN_MENU

async def process_admin_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text
    context.user_data['admin_choice'] = choice

    prompts = {
        "💳 تنظیم شماره کارت": "لطفا شماره کارت جدید را وارد کنید:",
        "👤 تنظیم صاحب کارت": "لطفا نام صاحب حساب جدید را وارد کنید:",
        "💰 تنظیم الماس (ست)": "ابتدا آیدی عددی کاربر را وارد کنید:",
        "➕ افزایش الماس کاربر": "ابتدا آیدی عددی کاربر را برای افزایش الماس وارد کنید:",
        "➖ کسر الماس کاربر": "ابتدا آیدی عددی کاربر را برای کسر الماس وارد کنید:",
        "📈 تنظیم قیمت الماس": "قیمت جدید هر الماس به تومان را وارد کنید:",
        "🎁 تنظیم پاداش دعوت": "پاداش هر دعوت موفق به الماس را وارد کنید:",
        "📉 تنظیم مالیات (۰-۱۰۰)": "درصد مالیات (بین ۰ تا ۱۰۰) را وارد کنید:",
        "➕ افزودن کانال عضویت": "یوزرنیم کانال/گروه با @ (مثل @channel) یا لینک کامل (مثل https://t.me/channel) را ارسال کنید:",
        "🖼 تنظیم عکس شرط": "لطفا عکس مورد نظر برای شرط را ارسال کنید."
    }

    if choice in prompts:
        await update.message.reply_text(prompts[choice], reply_markup=ReplyKeyboardRemove())
        if choice == "➕ افزودن کانال عضویت":
            return AWAIT_NEW_CHANNEL
        elif choice == "🖼 تنظیم عکس شرط":
            return AWAIT_BET_PHOTO
        elif choice == "💰 تنظیم الماس (ست)":
            return AWAIT_ADMIN_SET_BALANCE_ID
        elif choice == "➕ افزایش الماس کاربر":
            return AWAIT_ADMIN_ADD_BALANCE_ID
        elif choice == "➖ کسر الماس کاربر":
            return AWAIT_ADMIN_DEDUCT_BALANCE_ID
        elif choice == "📉 تنظیم مالیات (۰-۱۰۰)":
            return AWAIT_ADMIN_TAX
        elif choice == "📈 تنظیم قیمت الماس":
            return AWAIT_ADMIN_CREDIT_PRICE
        elif choice == "🎁 تنظیم پاداش دعوت":
            return AWAIT_ADMIN_REFERRAL_PRICE
        elif choice == "💳 تنظیم شماره کارت":
            return AWAIT_ADMIN_SET_CARD_NUMBER
        elif choice == "👤 تنظیم صاحب کارت":
            return AWAIT_ADMIN_SET_CARD_HOLDER
        else:
            return AWAIT_ADMIN_REPLY
    
    elif choice == "📢 پیام همگانی":
        await update.message.reply_text("لطفا پیام خود را ارسال کنید (متن، عکس، فایل و...). این پیام برای تمام کاربران ارسال خواهد شد:", reply_markup=ReplyKeyboardRemove())
        return AWAIT_BROADCAST_MESSAGE

    elif choice == "➖ حذف کانال عضویت":
        return await show_channels_for_removal(update, context)
            
    elif choice == "مدیریت کاربر":
        await update.message.reply_text("آیدی عددی کاربر مورد نظر را وارد کنید:", reply_markup=ReplyKeyboardRemove())
        return AWAIT_MANAGE_USER_ID

    elif choice == "🔒 قفل عضویت: روشن":
        await set_setting_async('forced_channel_lock', 'true')
        await update.message.reply_text("✅ قفل عضویت اجباری فعال شد.", reply_markup=admin_keyboard)
        return ADMIN_MENU

    elif choice == "🔓 قفل عضویت: خاموش":
        await set_setting_async('forced_channel_lock', 'false')
        await update.message.reply_text("❌ قفل عضویت اجباری غیرفعال شد.", reply_markup=admin_keyboard)
        return ADMIN_MENU

    elif choice == "👁‍🗨 لیست کانال‌های عضویت":
        channels = list(GLOBAL_CHANNELS.values())
        if not channels:
            await update.message.reply_text("هیچ کانالی برای عضویت اجباری تنظیم نشده است.", reply_markup=admin_keyboard)
            return ADMIN_MENU

        message = "لیست کانال‌های عضویت اجباری:\n\n"
        for i, channel in enumerate(channels, 1):
            title = channel.get('channel_title', 'بدون نام')
            username = channel['channel_username']
            message += f"{i}. {title} ({username})\n"

        await update.message.reply_text(message, reply_markup=admin_keyboard)
        return ADMIN_MENU
    
    elif choice == "📊 آمار کلی":
        total_users = len(GLOBAL_USERS)
        pending_tx = sum(1 for tx in GLOBAL_TRANSACTIONS.values() if tx['status'] == 'pending')
        total_balance = sum(u.get('balance', 0) for u in GLOBAL_USERS.values())

        admin_welcome_text = (
            f"📊 **آمار ربات (سینک شده با دیتابیس):**\n"
            f"  -  👥 **تعداد کل کاربران:** {total_users:,}\n"
            f"  -  💰 **مجموع الماس کاربران:** {total_balance:,}\n"
            f"  -  🧾 **تراکنش‌های در انتظار:** {pending_tx:,}"
        )
        await update.message.reply_text(admin_welcome_text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard)
        return ADMIN_MENU

    elif choice == "🗑 حذف عکس شرط":
        await set_setting_async('bet_photo_file_id', 'None')
        await update.message.reply_text("✅ عکس شرط با موفقیت حذف شد.", reply_markup=admin_keyboard)
        return ADMIN_MENU

    elif choice == "⬅️ بازگشت به منوی اصلی":
        user_doc = await get_user_async(update.effective_user.id)
        await update.message.reply_text("بازگشت به منوی اصلی...", reply_markup=get_main_keyboard(user_doc))
        return ConversationHandler.END

async def process_admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a broadcast message to all users."""
    user = update.effective_user
    user_doc = await get_user_async(user.id)
    
    if not user_doc.get('is_owner'):
        await update.message.reply_text("⛔️ دسترسی غیرمجاز.", reply_markup=admin_keyboard)
        return ADMIN_MENU

    message = update.message
    users_ids = list(GLOBAL_USERS.keys())
    
    status_msg = await update.message.reply_text(f"⏳ در حال ارسال پیام به {len(users_ids) - 1} کاربر (به جز ادمین)...\nلطفا صبر کنید.")
    
    count = 0
    
    # برای جلوگیری از بلاک شدن توسط تلگرام به خاطر سرعت زیاد، یک تاخیر کوچک میگذاریم
    for uid in users_ids:
        # ارسال نکردن پیام به خود ادمین
        if uid == user.id:
            continue

        try:
            await context.bot.copy_message(chat_id=uid, from_chat_id=update.effective_chat.id, message_id=message.message_id)
        except Exception:
            pass # طبق دستور، خطاها نادیده گرفته می‌شوند تا همیشه "موفق" باشد و لوپ نشکند
        
        count += 1
        # هر 30 پیام یک استراحت خیلی کوتاه برای مدیریت ریت لیمیت
        if count % 30 == 0:
            await asyncio.sleep(0.5)
            
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=status_msg.message_id,
        text=f"✅ پیام همگانی با موفقیت برای تمام کاربران ارسال شد."
    )
    
    await update.message.reply_text("بازگشت به منوی مدیریت.", reply_markup=admin_keyboard)
    return ADMIN_MENU

async def process_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles simple text replies for admin settings."""
    last_choice = context.user_data.get('admin_choice')
    reply = update.message.text.strip()
    await update.message.reply_text("✅ عملیات انجام شد.", reply_markup=admin_keyboard)
    context.user_data.pop('admin_choice', None)
    return ADMIN_MENU

async def process_admin_set_card_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets the new card number."""
    try:
        card_number = update.message.text.strip()
        if not card_number:
            raise ValueError("شماره کارت نمی‌تواند خالی باشد")
        
        await set_setting_async('card_number', card_number)
        await update.message.reply_text(f"✅ شماره کارت با موفقیت به `{card_number}` تنظیم شد.", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard)
    except ValueError as e:
        await update.message.reply_text(f"❌ ورودی نامعتبر است. لطفا شماره کارت را دوباره وارد کنید.\n({e})")
        return AWAIT_ADMIN_SET_CARD_NUMBER
    
    context.user_data.clear()
    return ADMIN_MENU

async def process_admin_set_card_holder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets the new card holder name."""
    try:
        card_holder = update.message.text.strip()
        if not card_holder:
            raise ValueError("نام صاحب کارت نمی‌تواند خالی باشد")
        
        await set_setting_async('card_holder', card_holder)
        await update.message.reply_text(f"✅ نام صاحب حساب با موفقیت به `{card_holder}` تنظیم شد.", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard)
    except ValueError as e:
        await update.message.reply_text(f"❌ ورودی نامعتبر است. لطفا نام صاحب حساب را دوباره وارد کنید.\n({e})")
        return AWAIT_ADMIN_SET_CARD_HOLDER
    
    context.user_data.clear()
    return ADMIN_MENU

# --- New Admin Conversation Handlers ---

async def process_manage_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gets the user ID for managing roles."""
    try:
        target_user_id = int(update.message.text.strip())
        context.user_data['target_user_id_manage'] = target_user_id
        
        await get_user_async(target_user_id)
        
        role_keyboard = ReplyKeyboardMarkup([
            [KeyboardButton("ادمین"), KeyboardButton("مادریتور")],
            [KeyboardButton("کاربر عادی"), KeyboardButton("لغو")]
        ], resize_keyboard=True)
        
        await update.message.reply_text(f"لطفا نقش جدید را برای کاربر `{target_user_id}` انتخاب کنید:",
                                        parse_mode=ParseMode.MARKDOWN,
                                        reply_markup=role_keyboard)
        return AWAIT_MANAGE_USER_ROLE
    except ValueError:
        await update.message.reply_text("❌ آیدی عددی نامعتبر است. لطفا دوباره تلاش کنید.", reply_markup=admin_keyboard)
        context.user_data.clear()
        return ADMIN_MENU

async def process_manage_user_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets the new role for the user."""
    try:
        role = update.message.text.strip()
        target_user_id = context.user_data.pop('target_user_id_manage', None)
        admin_doc = await get_user_async(update.effective_user.id)

        if role == "لغو":
            await update.message.reply_text("عملیات لغو شد.", reply_markup=admin_keyboard)
            context.user_data.clear()
            return ADMIN_MENU

        if not target_user_id:
            await update.message.reply_text("❌ خطای داخلی. لطفا دوباره از پنل ادمین شروع کنید.", reply_markup=admin_keyboard)
            return ADMIN_MENU
            
        if not admin_doc.get('is_owner'):
            await update.message.reply_text("⛔️ فقط مالک اصلی ربات می‌تواند نقش‌ها را تغییر دهد.", reply_markup=admin_keyboard)
            return ADMIN_MENU
            
        if target_user_id == OWNER_ID:
            await update.message.reply_text("❌ شما نمی‌توانید نقش مالک اصلی را تغییر دهید.", reply_markup=admin_keyboard)
            return ADMIN_MENU

        target_user_doc = await get_user_async(target_user_id)

        message = ""

        if role == "ادمین":
            # تغییر: ادمین‌های جدید دسترسی به پنل نخواهند داشت (طبق درخواست)
            # ما آنها را به عنوان is_admin=True علامت می‌زنیم تا بتوانند از دستورات استفاده کنند
            # اما در ورودی پنل آنها را بلاک می‌کنیم.
            target_user_doc['is_admin'] = True
            target_user_doc['is_moderator'] = False
            message = f"✅ کاربر `{target_user_id}` به **ادمین** ارتقا یافت.\n(دسترسی: دستورات مدیریتی، بدون پنل تنظیمات)"
        
        elif role == "مادریتور":
            target_user_doc['is_admin'] = False
            target_user_doc['is_moderator'] = True
            message = f"✅ کاربر `{target_user_id}` به **مادریتور** ارتقا یافت.\n(دسترسی: فقط دستورات کسر/افزایش)"
            
        elif role == "کاربر عادی":
            target_user_doc['is_admin'] = False
            target_user_doc['is_moderator'] = False
            message = f"✅ کاربر `{target_user_id}` به **کاربر عادی** تنزل یافت."
            
        else:
            await update.message.reply_text("❌ نقش انتخاب شده نامعتبر است.", reply_markup=admin_keyboard)
            return ADMIN_MENU

        save_user_immediate(target_user_id) # ذخیره فوری
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard)
        
    except Exception as e:
        logging.error(f"Error managing user role: {e}")
        await update.message.reply_text("❌ خطایی در تغییر نقش رخ داد.", reply_markup=admin_keyboard)
    
    context.user_data.clear()
    return ADMIN_MENU


# --- توابع مربوط به تنظیم و افزایش موجودی ---

async def process_admin_set_balance_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        target_user_id = int(update.message.text.strip())
        context.user_data['target_user_id_balance'] = target_user_id
        await get_user_async(target_user_id)
        await update.message.reply_text(f"مقدار الماس **جدید (قطعی)** را برای کاربر `{target_user_id}` وارد کنید:", parse_mode=ParseMode.MARKDOWN)
        return AWAIT_ADMIN_SET_BALANCE
    except ValueError:
        await update.message.reply_text("❌ آیدی عددی نامعتبر است.", reply_markup=admin_keyboard)
        return ADMIN_MENU

async def process_admin_set_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        new_balance = int(update.message.text.strip())
        target_user_id = context.user_data.pop('target_user_id_balance', None)

        if target_user_id is None:
             return ADMIN_MENU

        target_user_doc = await get_user_async(target_user_id)
        target_user_doc['balance'] = new_balance
        save_user_immediate(target_user_id) # ذخیره فوری
        
        await update.message.reply_text(f"✅ الماس کاربر `{target_user_id}` روی {new_balance:,} تنظیم شد.", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard)
    except ValueError:
        await update.message.reply_text("❌ مقدار نامعتبر است. لطفا عدد وارد کنید.")
        return AWAIT_ADMIN_SET_BALANCE
    return ADMIN_MENU

# --- هندلرهای جدید برای افزایش موجودی (Add Balance) ---
async def process_admin_add_balance_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        target_user_id = int(update.message.text.strip())
        context.user_data['target_user_id_add'] = target_user_id
        await get_user_async(target_user_id)
        await update.message.reply_text(f"چه تعداد الماس می‌خواهید به کاربر `{target_user_id}` **اضافه** کنید؟", parse_mode=ParseMode.MARKDOWN)
        return AWAIT_ADMIN_ADD_BALANCE_AMOUNT
    except ValueError:
        await update.message.reply_text("❌ آیدی نامعتبر.", reply_markup=admin_keyboard)
        return ADMIN_MENU

async def process_admin_add_balance_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text.strip())
        target_user_id = context.user_data.pop('target_user_id_add', None)
        
        if amount <= 0:
            await update.message.reply_text("مقدار باید مثبت باشد.")
            return AWAIT_ADMIN_ADD_BALANCE_AMOUNT

        target_user_doc = await get_user_async(target_user_id)
        target_user_doc['balance'] += amount
        save_user_immediate(target_user_id)
        
        await update.message.reply_text(f"✅ {amount:,} الماس به کاربر `{target_user_id}` اضافه شد.\nموجودی جدید: {target_user_doc['balance']:,}", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard)
    except ValueError:
        await update.message.reply_text("❌ عدد نامعتبر.")
        return AWAIT_ADMIN_ADD_BALANCE_AMOUNT
    return ADMIN_MENU

# --- هندلرهای جدید برای کسر موجودی (Deduct Balance) ---
async def process_admin_deduct_balance_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        target_user_id = int(update.message.text.strip())
        context.user_data['target_user_id_deduct'] = target_user_id
        await get_user_async(target_user_id)
        await update.message.reply_text(f"چه تعداد الماس می‌خواهید از کاربر `{target_user_id}` **کم** کنید؟", parse_mode=ParseMode.MARKDOWN)
        return AWAIT_ADMIN_DEDUCT_BALANCE_AMOUNT
    except ValueError:
        await update.message.reply_text("❌ آیدی نامعتبر.", reply_markup=admin_keyboard)
        return ADMIN_MENU

async def process_admin_deduct_balance_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text.strip())
        target_user_id = context.user_data.pop('target_user_id_deduct', None)
        
        if amount <= 0:
            await update.message.reply_text("مقدار باید مثبت باشد.")
            return AWAIT_ADMIN_DEDUCT_BALANCE_AMOUNT

        target_user_doc = await get_user_async(target_user_id)
        target_user_doc['balance'] -= amount
        save_user_immediate(target_user_id)
        
        await update.message.reply_text(f"✅ {amount:,} الماس از کاربر `{target_user_id}` کسر شد.\nموجودی جدید: {target_user_doc['balance']:,}", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard)
    except ValueError:
        await update.message.reply_text("❌ عدد نامعتبر.")
        return AWAIT_ADMIN_DEDUCT_BALANCE_AMOUNT
    return ADMIN_MENU


async def process_admin_tax(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets the new tax rate."""
    try:
        tax_rate = int(update.message.text.strip())
        if not 0 <= tax_rate <= 100:
            raise ValueError("Tax rate must be between 0 and 100")
        
        await set_setting_async('bet_tax_rate', str(tax_rate))
        await update.message.reply_text(f"✅ مالیات شرط‌بندی با موفقیت روی {tax_rate}% تنظیم شد.", reply_markup=admin_keyboard)
    except ValueError:
        await update.message.reply_text("❌ درصد نامعتبر است. لطفا یک عدد بین ۰ تا ۱۰۰ وارد کنید.")
        return AWAIT_ADMIN_TAX
    return ADMIN_MENU

async def process_admin_credit_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets the new credit price."""
    try:
        price = int(update.message.text.strip())
        if price <= 0:
            raise ValueError("Price must be positive")
        
        await set_setting_async('credit_price', str(price))
        await update.message.reply_text(f"✅ قیمت هر الماس با موفقیت روی {price:,} تومان تنظیم شد.", reply_markup=admin_keyboard)
    except ValueError:
        await update.message.reply_text("❌ قیمت نامعتبر است. لطفا یک عدد مثبت وارد کنید.")
        return AWAIT_ADMIN_CREDIT_PRICE
    return ADMIN_MENU

async def process_admin_referral_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets the new referral reward."""
    try:
        reward = int(update.message.text.strip())
        if reward < 0:
            raise ValueError("Reward cannot be negative")
        
        await set_setting_async('referral_reward', str(reward))
        await update.message.reply_text(f"✅ پاداش دعوت (رفرال) با موفقیت روی {reward:,} الماس تنظیم شد.", reply_markup=admin_keyboard)
    except ValueError:
        await update.message.reply_text("❌ پاداش نامعتبر است. لطفا یک عدد وارد کنید.")
        return AWAIT_ADMIN_REFERRAL_PRICE
    return ADMIN_MENU

# --- End of New Admin Handlers ---

async def process_new_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = update.message.text.strip()
    channel_username = None
    channel_link = None

    if reply.startswith('@'):
        channel_username = reply
        channel_link = f"https://t.me/{reply[1:]}"
    elif "t.me/" in reply:
        try:
            username = reply.split("t.me/")[-1].split('/')[0]
            if not username: raise ValueError("Invalid link")
            channel_username = f"@{username}"
            channel_link = f"https://t.me/{username}"
        except Exception as e:
            logging.warning(f"Could not parse channel link: {reply} - Error: {e}")
            await update.message.reply_text("❌ لینک نامعتبر است. لطفا یوزرنیم با @ یا لینک کامل t.me را ارسال کنید.", reply_markup=admin_keyboard)
            return AWAIT_NEW_CHANNEL
    else:
        await update.message.reply_text("❌ ورودی نامعتبر است. لطفا یوزرنیم با @ (مثل @channel) یا لینک کامل (مثل https://t.me/channel) ارسال کنید.", reply_markup=admin_keyboard)
        return AWAIT_NEW_CHANNEL

    # --- اصلاح: هندل کردن ارور عدم دسترسی به کانال ---
    channel_title = channel_username # پیش فرض
    try:
        chat = await context.bot.get_chat(channel_username)
        channel_title = chat.title # دریافت اسم کانال برای نمایش در دکمه
        
        # سعی می‌کنیم وضعیت ادمین بودن خودمون رو چک کنیم
        member = await chat.get_member(context.bot.id)
        if member.status not in ['administrator', 'creator']:
             await update.message.reply_text(f"⚠️ **هشدار:** ربات در کانال {channel_username} ادمین نیست. لطفا ربات را در کانال ادمین کنید تا قفل عضویت کار کند.", parse_mode=ParseMode.MARKDOWN)
    except telegram.error.BadRequest as e:
        # اگر ربات دسترسی دیدن ممبرها رو نداشته باشه یا کانال پرایوت باشه و ربات توش نباشه
        logging.warning(f"Cannot access member list of {channel_username}: {e}")
        await update.message.reply_text(
            f"⚠️ **هشدار دسترسی:** ربات نتوانست اطلاعات {channel_username} را دریافت کند.\n"
            f"اگر کانال خصوصی است یا ربات عضو نیست، ابتدا ربات را در آن **ادمین** کنید.\n"
            f"کانال اضافه شد، اما ممکن است اسم آن در دکمه درست نمایش داده نشود یا چک کردن ممبر کار نکند.",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ **هشدار:** خطا در بررسی کانال: {e}\n", parse_mode=ParseMode.MARKDOWN)

    GLOBAL_CHANNELS[channel_username] = {
        'channel_username': channel_username,
        'channel_link': channel_link,
        'channel_title': channel_title # ذخیره اسم کانال
    }

    await update.message.reply_text(f"✅ کانال {channel_title} ({channel_username}) با موفقیت اضافه شد.", reply_markup=admin_keyboard)
    context.user_data.clear()
    return ADMIN_MENU

async def process_bet_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ لطفا یک عکس ارسال کنید.", reply_markup=admin_keyboard)
        return AWAIT_BET_PHOTO

    file_id = update.message.photo[-1].file_id
    await set_setting_async('bet_photo_file_id', file_id)
    await update.message.reply_text("✅ عکس شرط با موفقیت تنظیم شد.", reply_markup=admin_keyboard)
    context.user_data.clear()
    return ADMIN_MENU

async def admin_support_reply_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split('_')
    target_user_id = int(data[2])
    context.user_data['reply_to_user'] = target_user_id
    await query.message.reply_text(f"لطفا پاسخ خود را برای کاربر با آیدی {target_user_id} بنویسید:", reply_markup=ReplyKeyboardRemove())
    return AWAIT_ADMIN_SUPPORT_REPLY

async def process_admin_support_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user
    target_user_id = context.user_data.get('reply_to_user')
    if not target_user_id: return ConversationHandler.END

    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text=f"✉️ پاسخ پشتیبانی:\n\n{update.message.text}"
        )
        await update.message.reply_text("✅ پاسخ شما برای کاربر ارسال شد.", reply_markup=admin_keyboard)
    except Exception as e:
        await update.message.reply_text(f"❌ ارسال پیام به کاربر ناموفق بود: {e}", reply_markup=admin_keyboard)

    context.user_data.clear()
    return ADMIN_MENU

# =======================================================
#  بخش ۷: مدیریت Callback Query و پیام‌های عمومی (نسخه Async)
# =======================================================
async def cancel_bet_job(context: ContextTypes.DEFAULT_TYPE):
    """Job to cancel a bet if it's not joined within the time limit."""
    job = context.job
    bet_id = job.data['bet_id']
    chat_id = job.data['chat_id']
    message_id = job.data['message_id']
    
    if bet_id in GLOBAL_BETS and GLOBAL_BETS[bet_id]['status'] == 'pending':
        deleted_bet = GLOBAL_BETS.pop(bet_id)
        
        logging.info(f"Bet {bet_id} expired and was cancelled.")
        try:
            await context.bot.edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=f"⏰ شرط‌بندی روی تعداد {deleted_bet['amount']} الماس منقضی شد.",
                reply_markup=None
            )
        except Exception:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"⏰ شرط‌بندی روی تعداد {deleted_bet['amount']} الماس منقضی شد.",
                    reply_markup=None
                )
            except: pass

async def handle_channel_removal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the admin's choice of channel to remove."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "admin_remove_cancel":
        await query.edit_message_text("عملیات لغو شد.")
        return

    channel_username = data.replace("admin_remove_", "")
    
    if channel_username in GLOBAL_CHANNELS:
        del GLOBAL_CHANNELS[channel_username]
        
        # --- بخش اصلاح شده: حذف فوری از دیتابیس ---
        if db is not None:
            try:
                db.channels.delete_one({'channel_username': channel_username})
                logging.info(f"Deleted channel {channel_username} from DB.")
            except Exception as e:
                logging.error(f"Failed to delete channel {channel_username} from DB: {e}")
        # ----------------------------------------

        logging.info(f"Admin {query.from_user.id} removed channel {channel_username}")
        await query.edit_message_text(f"✅ کانال {channel_username} با موفقیت هم از حافظه و هم از دیتابیس حذف شد.")
    else:
        # حتی اگر در حافظه نبود، تلاش کن از دیتابیس پاک کنی تا دیگر برنگردد
        if db is not None:
            try:
                db.channels.delete_one({'channel_username': channel_username})
            except: pass
            
        await query.edit_message_text(f"❌ کانال {channel_username} یافت نشد (اما اگر در دیتابیس بود، پاک شد).")


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles general callback queries."""
    query = update.callback_query
    
    if query.data.startswith("admin_remove_"):
        await handle_channel_removal_callback(update, context)
        return
    
    # تایید کال‌بک در ابتدای کار برای جلوگیری از چرخش لودینگ تلگرام
    try:
        await query.answer()
    except:
        pass

    user_id = query.from_user.id
    data = query.data.split('_')
    action = data[0]

    # --- هندل کردن تایید/رد تراکنش های مختلف ---
    if action == "tx":
        tx_id = int(data[2])
        try:
            tx = GLOBAL_TRANSACTIONS.get(tx_id)
            
            if not tx:
                try:
                    await query.edit_message_caption(caption=query.message.caption_html + "\n\n(تراکنش یافت نشد)", parse_mode=ParseMode.HTML)
                except: pass
                return

            if tx.get('status') != 'pending':
                await query.answer("این درخواست قبلا پردازش شده است.", show_alert=True)
                return

            admin_name = get_user_display_name(query.from_user)
            tx_type = tx.get('type', 'diamond') # default to diamond for old tx
            
            decision = data[1] # approve or reject
            
            if decision == "approve":
                user_doc = await get_user_async(tx['user_id'])
                
                if tx_type == 'diamond':
                    user_doc['balance'] += tx['amount']
                    msg_text = f"✅ پرداخت شما برای {tx['amount']} الماس تایید و حساب شما شارژ شد."
                    
                tx['status'] = 'approved'
                save_user_immediate(tx['user_id'])
                
                status_text = f"\n\n<b>✅ تایید شد توسط {admin_name}.</b>"
                try:
                    await context.bot.send_message(tx['user_id'], msg_text)
                except Exception as e:
                    logging.warning(f"Could not send success msg to user {tx['user_id']}: {e}")

            elif decision == "reject":
                tx['status'] = 'rejected'
                user_doc = await get_user_async(tx['user_id'])
                
                msg_text = f"❌ درخواست شما (ID: {tx_id}) توسط مدیریت رد شد."

                status_text = f"\n\n<b>❌ رد شد توسط {admin_name}.</b>"
                try:
                    await context.bot.send_message(tx['user_id'], msg_text)
                except Exception as e:
                    logging.warning(f"Could not send reject msg to user {tx['user_id']}: {e}")
            
            # --- Sync Admin Messages ---
            admin_messages = tx.get('admin_messages', [])
            if not admin_messages:
                try:
                    if query.message.caption:
                        await query.edit_message_caption(caption=query.message.caption_html + status_text, parse_mode=ParseMode.HTML, reply_markup=None)
                    else:
                        await query.edit_message_text(text=query.message.text_html + status_text, parse_mode=ParseMode.HTML, reply_markup=None)
                except: pass
            else:
                for msg_info in admin_messages:
                    try:
                        # تشخیص عکس دار بودن یا متن خالی
                        try:
                            await context.bot.edit_message_caption(
                                chat_id=msg_info['chat_id'],
                                message_id=msg_info['message_id'],
                                caption=query.message.caption_html.split('\n\n<b>')[0] + status_text,
                                parse_mode=ParseMode.HTML,
                                reply_markup=None
                            )
                        except:
                            await context.bot.edit_message_text(
                                chat_id=msg_info['chat_id'],
                                message_id=msg_info['message_id'],
                                text=query.message.text_html.split('\n\n<b>')[0] + status_text,
                                parse_mode=ParseMode.HTML,
                                reply_markup=None
                            )
                    except Exception as e:
                        logging.warning(f"Failed to sync edit for admin {msg_info['chat_id']}: {e}")

        except Exception as e:
            logging.error(f"Error processing transaction callback: {e}")

    elif action == "bet":
        bet_id = int(data[2])
        bet = GLOBAL_BETS.get(bet_id)
        user = query.from_user

        if not bet:
            try:
                await query.edit_message_text("این شرط دیگر فعال نیست.")
            except: pass
            return

        if data[1] == "cancel":
            if user.id != bet['proposer_id']:
                await query.answer("شما شروع کننده این شرط نیستید.", show_alert=True)
                return
            if bet.get('status') != 'pending':
                await query.answer("این شرط دیگر برای لغو در دسترس نیست.", show_alert=True)
                return

            if context.job_queue:
                current_jobs = context.job_queue.get_jobs_by_name(f"bet_timeout_{bet_id}")
                for job in current_jobs:
                    job.schedule_removal()
            
            GLOBAL_BETS.pop(bet_id, None)
            
            await query.answer("✅ شرط با موفقیت لغو شد.", show_alert=False)
            try:
                await query.edit_message_caption(caption=f"❌ شرط توسط {bet['proposer_username']} لغو شد.", reply_markup=None)
            except Exception:
                try:
                    await query.edit_message_text(f"❌ شرط توسط {bet['proposer_username']} لغو شد.", reply_markup=None)
                except: pass
            return

        if data[1] == "join":
            if user.id == bet['proposer_id']:
                await query.answer("شما نمی‌توانید به شرط خودتان بپیوندید.", show_alert=True)
                return
            
            if bet.get('status') != 'pending':
                await query.answer("متاسفانه کس دیگری زودتر به این شرط پیوست.", show_alert=True)
                return
            
            opponent_display_name = get_user_display_name(user)
            bet['status'] = 'active'
            bet['opponent_id'] = user.id
            bet['opponent_username'] = opponent_display_name
            
            joiner_doc = await get_user_async(user.id)
            if joiner_doc['balance'] < bet['amount']:
                bet['status'] = 'pending'
                bet['opponent_id'] = None
                bet['opponent_username'] = None
                await query.answer("موجودی شما برای پیوستن به این شرط کافی نیست.", show_alert=True)
                return

            if context.job_queue:
                current_jobs = context.job_queue.get_jobs_by_name(f"bet_timeout_{bet_id}")
                for job in current_jobs:
                    job.schedule_removal()

            await query.answer("✅ شما به شرط پیوستید! در حال انتخاب برنده...", show_alert=False)
            try:
                await query.edit_message_caption(caption="🎲 در حال انتخاب برنده...", reply_markup=None)
            except:
                try: await query.edit_message_text("🎲 در حال انتخاب برنده...", reply_markup=None)
                except: pass

            await asyncio.sleep(1)

            amount = bet['amount']
            proposer_doc = await get_user_async(bet['proposer_id'])
            proposer_doc['balance'] -= amount
            joiner_doc['balance'] -= amount

            proposer_id = bet['proposer_id']
            opponent_id = user.id
            winner_id = secrets.choice([proposer_id, opponent_id])

            total_pot = amount * 2
            tax_rate_str = await get_setting_async('bet_tax_rate')
            try:
                tax_rate = int(tax_rate_str or 0)
            except (ValueError, TypeError):
                tax_rate = 0
            
            tax = round(total_pot * (tax_rate / 100))
            prize = total_pot - tax

            winner_doc = await get_user_async(winner_id)
            winner_doc['balance'] += prize
            
            if tax > 0 and bet['proposer_id'] != OWNER_ID and user.id != OWNER_ID:
                owner_doc = await get_user_async(OWNER_ID)
                owner_doc['balance'] += tax

            # ذخیره فوری تغییرات برای اطمینان از صحت موجودی
            save_user_immediate(proposer_id)
            save_user_immediate(opponent_id)

            if winner_id == proposer_id:
                winner_display_name = bet['proposer_username']
                loser_display_name = opponent_display_name
            else:
                winner_display_name = opponent_display_name
                loser_display_name = bet['proposer_username']

            GLOBAL_BETS.pop(bet_id, None)

            # --- تغییر: متن چپ چین شده برای برنده/بازنده (استایل TNT) ---
            result_text = (
                f"♦️ — <b>TNT Virtual Services</b> — ♦️\n\n"
                f"🏆 <b>WINNER:</b> {winner_display_name}\n"
                f"❌ <b>LOSER:</b> {loser_display_name}\n\n"
                f"💰 Prize: {prize:,} 💎\n"
                f"📉 Tax: {tax:,} 💎\n\n"
                f"♦️ — @{context.bot.username} — ♦️"
            )

            try:
                await query.edit_message_caption(caption=result_text, reply_markup=None, parse_mode=ParseMode.HTML)
            except Exception:
                try: await query.edit_message_text(text=result_text, reply_markup=None, parse_mode=ParseMode.HTML)
                except Exception as e: logging.error(f"Failed to edit bet message {bet_id}: {e}")

async def group_balance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles 'موجودی' in groups."""
    if not update.message: return

    sender = update.effective_user
    target_user = sender
    reply_to_message = update.message.reply_to_message

    if reply_to_message and reply_to_message.from_user:
        sender_doc = await get_user_async(sender.id)
        if sender_doc.get('is_admin') or sender_doc.get('is_moderator') or sender_doc.get('is_owner'):
            target_user = reply_to_message.from_user

    target_user_doc = await get_user_async(target_user.id)
    price_str = await get_setting_async('credit_price')
    try:
        price = int(price_str or 1000)
    except (ValueError, TypeError):
        price = 1000
    toman_value = target_user_doc['balance'] * price

    target_display_name = get_user_display_name(target_user)
    text = (
        f"👤 کاربر: {target_display_name}\n"
        f"💰 موجودی الماس: {target_user_doc['balance']:,}\n"
        f"💳 معادل تخمینی: {toman_value:,.0f} تومان"
    )
    await update.message.reply_text(text)

async def transfer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles credit transfers in groups (reply with 'انتقال 100')."""
    if not update.message or not update.message.reply_to_message or not update.message.reply_to_message.from_user:
        return

    sender = update.effective_user
    receiver = update.message.reply_to_message.from_user

    try:
        match = re.search(r'(\d+)', update.message.text)
        if not match:
            return
        
        amount = int(match.group(1))
        
        if amount <= 0:
            await update.message.reply_text("تعداد الماس انتقال باید مثبت باشد.")
            return
    except (ValueError, TypeError):
        await update.message.reply_text("خطا در خواندن تعداد.")
        return 

    try:
        sender_doc = await get_user_async(sender.id)

        if sender.id == receiver.id:
            await update.message.reply_text("انتقال به خود امکان‌پذیر نیست.")
            return

        if sender_doc['balance'] < amount:
            await update.message.reply_text("موجودی الماس شما کافی نیست.")
            return

        receiver_doc = await get_user_async(receiver.id)

        sender_doc['balance'] -= amount
        receiver_doc['balance'] += amount
        save_user_immediate(sender.id)
        save_user_immediate(receiver.id)

        sender_display_name = get_user_display_name(sender)
        receiver_display_name = get_user_display_name(receiver)

        text = (
            f"✅ انتقال موفق ✅\n\n"
            f"👤 از: {sender_display_name}\n"
            f"👥 به: {receiver_display_name}\n"
            f"💰 تعداد: {amount:,} الماس"
        )
        await update.message.reply_text(text)
    except Exception as e:
        logging.error(f"Error during transfer: {e}")
        await update.message.reply_text("خطایی در هنگام انتقال رخ داد.")

async def start_bet_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts a bet with inline buttons."""
    global BET_ID_COUNTER
    if not update.message: return
    
    proposer = update.effective_user

    match = re.search(r'(\d+)', update.message.text)
    if not match: return
    try:
        amount = int(match.group(1))
        if amount <= 0: return
    except (ValueError, TypeError):
        return

    proposer_doc = await get_user_async(proposer.id)
    if proposer_doc['balance'] < amount:
        await update.message.reply_text("موجودی شما برای این شرط کافی نیست.")
        return
        
    bet_id = BET_ID_COUNTER
    proposer_display_name = get_user_display_name(proposer)
    GLOBAL_BETS[bet_id] = {
        'bet_id': bet_id,
        'proposer_id': proposer.id,
        'proposer_username': proposer_display_name,
        'amount': amount,
        'chat_id': update.effective_chat.id,
        'status': 'pending',
        'created_at': datetime.now(timezone.utc)
    }
    BET_ID_COUNTER += 1
        
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ پیوستن", callback_data=f"bet_join_{bet_id}"),
            InlineKeyboardButton("❌ لغو شرط", callback_data=f"bet_cancel_{bet_id}")
        ]
    ])

    proposer_mention = proposer_display_name
    # تغییر نام فوتر
    text = (
        f"♦️ — شرط جدید (ID: {bet_id}) — ♦️\n"
        f"| 💰 | تعداد الماس : {amount:,}\n"
        f"| 👤 | سازنده : {proposer_mention}\n"
        f"♦️ — خدمات مجازی TNT — ♦️"
    )

    sent_message = None
    photo_id = await get_setting_async('bet_photo_file_id')

    try:
        if photo_id and photo_id != 'None':
            sent_message = await update.message.reply_photo(photo=photo_id, caption=text, reply_markup=keyboard)
        else:
            sent_message = await update.message.reply_text(text, reply_markup=keyboard)
    except Exception as e:
        logging.error(f"Failed to send bet message: {e}")
        if photo_id and photo_id != 'None':
            try: sent_message = await update.message.reply_text(text, reply_markup=keyboard)
            except: return
        else: return
    
    if not sent_message: return

    if context.job_queue:
        context.job_queue.run_once(
            cancel_bet_job,
            120,
            data={
                'bet_id': bet_id,
                'chat_id': update.effective_chat.id,
                'message_id': sent_message.message_id
            },
            name=f"bet_timeout_{bet_id}"
        )

async def deduct_balance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles admin 'کسر' command."""
    if not update.message or not update.message.reply_to_message:
        return

    admin_user = update.effective_user
    admin_doc = await get_user_async(admin_user.id)
    if not (admin_doc.get('is_admin') or admin_doc.get('is_moderator') or admin_doc.get('is_owner')):
        return

    target_user = update.message.reply_to_message.from_user
    if target_user.id == admin_user.id:
        await update.message.reply_text("شما نمی‌توانید از خودتان الماس کسر کنید.")
        return
    if target_user.id == OWNER_ID:
        await update.message.reply_text("شما نمی‌توانید از مالک اصلی الماس کسر کنید.")
        return

    match = re.search(r'(\d+)', update.message.text)
    if not match:
        await update.message.reply_text("لطفا مقدار عددی برای کسر را مشخص کنید. مثال: کسر 500")
        return

    try:
        amount_to_deduct = int(match.group(1))
        if amount_to_deduct <= 0:
            await update.message.reply_text("مقدار کسر باید یک عدد مثبت باشد.")
            return
    except (ValueError, TypeError):
        await update.message.reply_text("مقدار وارد شده نامعتبر است.")
        return

    target_doc = await get_user_async(target_user.id)
    target_display_name = get_user_display_name(target_user)
    if target_doc.get('balance', 0) < amount_to_deduct:
        await update.message.reply_text(f"کاربر {target_display_name} موجودی کافی برای کسر {amount_to_deduct:,} الماس را ندارد.")
        return

    target_doc['balance'] -= amount_to_deduct
    save_user_immediate(target_user.id)
    
    admin_display_name = get_user_display_name(admin_user)
    tehran_time = datetime.now(TEHRAN_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')
    receipt_text = (
        f"❌ {amount_to_deduct:,} الماس از {target_display_name} کسر شد.\n"
        f"🧾 رسید کسر:\n"
        f"📤 ادمین/مادریتور: {admin_display_name}\n"
        f"📥 کاربر: {target_display_name}\n"
        f"💰 تعداد: {amount_to_deduct:,}\n"
        f"⏰ {tehran_time}"
    )
    await update.message.reply_text(receipt_text)

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_doc = await get_user_async(update.effective_user.id)
    await update.message.reply_text("عملیات لغو شد.", reply_markup=get_main_keyboard(user_doc))
    context.user_data.clear()
    return ConversationHandler.END

async def show_bet_keyboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the quick bet reply keyboard in groups."""
    await update.message.reply_text("منوی شرط:", reply_markup=bet_group_keyboard)

# =======================================================
#  بخش ۸: تابع اصلی و اجرای ربات
# =======================================================
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    logging.info(f"Starting minimal Flask health check server on 0.0.0.0:{port}")
    try:
        web_app.run(host='0.0.0.0', port=port)
    except Exception as e:
        logging.error(f"Failed to start Flask health check server: {e}")

async def post_init(application: Application):
    """Actions to run after the bot is initialized."""
    global BOT_EVENT_LOOP
    BOT_EVENT_LOOP = asyncio.get_running_loop()
    
    init_memory_db()
    logging.info("Database synchronized.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log the error."""
    logging.error("Exception while handling an update:", exc_info=context.error)

    if "Conflict: terminated by other getUpdates request" in str(context.error):
        logging.warning("Conflict error detected. Ensure only one bot instance is running.")
        return

    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    if isinstance(update, Update):
        update_str = json.dumps(update.to_dict(), indent=2, ensure_ascii=False)
    else:
        update_str = str(update)

    message = (
        f"An exception was raised while handling an update\n"
        f"<pre>update = {html.escape(update_str)}</pre>\n\n"
        f"<pre>context.chat_data = {html.escape(str(context.chat_data))}</pre>\n\n"
        f"<pre>context.user_data = {html.escape(str(context.user_data))}</pre>\n\n"
        f"<pre>{html.escape(tb_string)}</pre>"
    )

    if len(message) > 4096:
        message = message[:4090] + "...</pre>"
        
    try:
        await context.bot.send_message(chat_id=OWNER_ID, text=message, parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.error(f"Failed to send error log to owner: {e}")

if __name__ == "__main__":
    if not BOT_TOKEN:
        # اگر توکن ست نشده بود، اینجا می توانید دستی وارد کنید یا ارور دهید
        logging.warning("BOT_TOKEN environment variable is not set. Using default (unsafe).")

    logging.info("Starting Flask app in a background thread...")
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # --- شروع ترد ذخیره‌سازی خودکار دیتابیس ---
    logging.info("Starting DB Sync thread...")
    db_thread = Thread(target=background_db_sync, daemon=True)
    db_thread.start()

    # --- Conversation Handlers ---
    admin_conv_states = {
        ADMIN_MENU: [
            MessageHandler(filters.Regex("^(💳 تنظیم شماره کارت|👤 تنظیم صاحب کارت|مدیریت کاربر)$"), process_admin_choice), # بازگردانده شد
            MessageHandler(filters.Regex("^(➕ افزودن کانال عضویت|➖ حذف کانال عضویت|🖼 تنظیم عکس شرط)$"), process_admin_choice),
            MessageHandler(filters.Regex(r"^(💰 تنظیم الماس \(ست\)|➕ افزایش الماس کاربر|➖ کسر الماس کاربر|📈 تنظیم قیمت الماس|🎁 تنظیم پاداش دعوت|📉 تنظیم مالیات \(۰-۱۰۰\))$"), process_admin_choice),
            MessageHandler(filters.Regex("^(👁‍🗨 لیست کانال‌های عضویت|📊 آمار کلی|🗑 حذف عکس شرط)$"), process_admin_choice),
            MessageHandler(filters.Regex("^(🔒 قفل عضویت: روشن|🔓 قفل عضویت: خاموش)$"), process_admin_choice), # تغییر هندلر
            MessageHandler(filters.Regex("^(📢 پیام همگانی)$"), process_admin_choice), # اضافه شده
            MessageHandler(filters.Regex("^⬅️ بازگشت به منوی اصلی$"), process_admin_choice),
        ],
        AWAIT_ADMIN_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_reply)],
        AWAIT_ADMIN_SET_CARD_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_set_card_number)], # اضافه شد
        AWAIT_ADMIN_SET_CARD_HOLDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_set_card_holder)], # اضافه شد
        AWAIT_NEW_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_new_channel)],
        AWAIT_BET_PHOTO: [MessageHandler(filters.PHOTO, process_bet_photo)],
        AWAIT_ADMIN_SET_BALANCE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_set_balance_id)],
        AWAIT_ADMIN_SET_BALANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_set_balance)],
        
        # هندلرهای جدید
        AWAIT_ADMIN_ADD_BALANCE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_add_balance_id)],
        AWAIT_ADMIN_ADD_BALANCE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_add_balance_amount)],
        AWAIT_ADMIN_DEDUCT_BALANCE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_deduct_balance_id)],
        AWAIT_ADMIN_DEDUCT_BALANCE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_deduct_balance_amount)],

        AWAIT_ADMIN_TAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_tax)],
        AWAIT_ADMIN_CREDIT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_credit_price)],
        AWAIT_ADMIN_REFERRAL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_referral_price)],
        AWAIT_MANAGE_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_manage_user_id)],
        AWAIT_MANAGE_USER_ROLE: [
            MessageHandler(filters.Regex("^(ادمین|مادریتور|کاربر عادی|لغو)$"), process_manage_user_role)
        ],
        AWAIT_BROADCAST_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, process_admin_broadcast)], # هندلر جدید
    }

    # تغییر مهم: اضافه شدن allow_reentry=True برای حل مشکل گیر کردن پنل
    admin_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^👑 پنل ادمین$") & filters.ChatType.PRIVATE, admin_panel_entry)],
        states=admin_conv_states,
        fallbacks=[CommandHandler('cancel', cancel_conversation)],
        conversation_timeout=600,
        allow_reentry=True  # <--- این خط مشکل گیر کردن دکمه پنل رو حل میکنه
    )

    deposit_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💳 افزایش الماس$") & filters.ChatType.PRIVATE, deposit_entry)],
        states={
            AWAIT_DEPOSIT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_deposit_amount)],
            AWAIT_DEPOSIT_RECEIPT: [MessageHandler(filters.PHOTO, process_deposit_receipt)]
        },
        fallbacks=[CommandHandler('cancel', cancel_conversation)],
        conversation_timeout=300,
        allow_reentry=True
    )

    support_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💬 پشتیبانی$") & filters.ChatType.PRIVATE, support_entry)],
        states={ AWAIT_SUPPORT_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_support_message)] },
        fallbacks=[CommandHandler('cancel', cancel_conversation)],
        conversation_timeout=300,
        allow_reentry=True
    )

    admin_reply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_support_reply_entry, pattern="^reply_support_")],
        states={
            AWAIT_ADMIN_SUPPORT_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_support_reply)]
        },
        fallbacks=[CommandHandler('cancel', cancel_conversation)],
        per_message=False,
        conversation_timeout=300
    )
    
    from telegram.request import HTTPXRequest
    request = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=10,
        write_timeout=10,
        connect_timeout=10,
        pool_timeout=10
    )
    
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .post_init(post_init)
        .build()
    )

    application.add_handler(TypeHandler(Update, membership_check_handler), group=-1)
    application.add_error_handler(error_handler)

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.Regex("^💰 موجودی$") & filters.ChatType.PRIVATE, show_balance))
    application.add_handler(MessageHandler(filters.Regex("^🎁 الماس رایگان$") & filters.ChatType.PRIVATE, get_referral_link))
    
    application.add_handler(admin_conv)
    application.add_handler(deposit_conv)
    application.add_handler(support_conv)
    application.add_handler(admin_reply_conv)

    # Group Handlers
    application.add_handler(MessageHandler(filters.Regex(r'^(شرط|بت)$') & filters.ChatType.GROUPS, show_bet_keyboard_handler))
    application.add_handler(MessageHandler(filters.Regex(r'^(شرطبندی|شرط) \d+$') & filters.ChatType.GROUPS, start_bet_handler))
    application.add_handler(MessageHandler(filters.Regex(r'^(انتقال|transfer)\s+(\d+)$') & filters.REPLY & filters.ChatType.GROUPS, transfer_handler))
    application.add_handler(MessageHandler(filters.Regex(r'^موجودی$') & filters.ChatType.GROUPS, group_balance_handler))
    application.add_handler(MessageHandler(filters.Regex(r'^(کسر اعتبار|کسر) \d+$') & filters.REPLY & filters.ChatType.GROUPS, deduct_balance_handler))
    application.add_handler(MessageHandler(filters.Regex(r'^موجودی 💰$') & filters.ChatType.GROUPS, group_balance_handler))

    application.add_handler(CallbackQueryHandler(callback_query_handler))

    logging.info("Starting Telegram Bot (Polling)...")
    try:
        application.run_polling(
            allowed_updates=Update.ALL_TYPES, 
            drop_pending_updates=True
        )
    except KeyboardInterrupt:
        logging.info("Bot stopped by user")
    except Exception as e:
        logging.error(f"Fatal error in bot: {e}")
        raise
