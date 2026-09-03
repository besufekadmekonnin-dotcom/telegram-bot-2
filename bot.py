import os
import asyncio
import secrets
import sqlite3
import re
import traceback
import inspect

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import RetryAfter


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    "8778987128:AAG_IjRbwNafV59bKK_pRjvXnvXAVR5ES5M"
).strip()

ADMIN_IDS = {
    6004785454,
    8760235013,
}

STORAGE_CHAT_ID = int(
    os.getenv(
        "STORAGE_CHAT_ID",
        "-1004429842064"
    )
)

DB = "bot.db"

# Telegram fire message effect
FIRE_EFFECT_ID = "5104841245755180586"

# Channel shown in the /start message
CHANNEL_USERNAME = "@B16_NETFLIX"


# ============================================================
# DATABASE
# ============================================================

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def setup_db():
    con = db()

    con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS links (
            token TEXT PRIMARY KEY,
            link_type TEXT DEFAULT 'single',
            chat_id TEXT,
            message_id INTEGER,
            first_link TEXT,
            last_link TEXT
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS batch_items (
            token TEXT,
            chat_id TEXT,
            message_id INTEGER,
            position INTEGER,
            PRIMARY KEY (token, position)
        )
    """)

    columns = [
        row[1]
        for row in con.execute(
            "PRAGMA table_info(links)"
        ).fetchall()
    ]

    if "link_type" not in columns:
        con.execute(
            "ALTER TABLE links ADD COLUMN link_type TEXT DEFAULT 'single'"
        )

    if "chat_id" not in columns:
        con.execute(
            "ALTER TABLE links ADD COLUMN chat_id TEXT"
        )

    if "message_id" not in columns:
        con.execute(
            "ALTER TABLE links ADD COLUMN message_id INTEGER"
        )

    if "first_link" not in columns:
        con.execute(
            "ALTER TABLE links ADD COLUMN first_link TEXT"
        )

    if "last_link" not in columns:
        con.execute(
            "ALTER TABLE links ADD COLUMN last_link TEXT"
        )

    con.commit()
    con.close()


# ============================================================
# MAIN MENU
# ============================================================

def main_menu():
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("🎬 Generate file link"),
                KeyboardButton("📦 Generate batch link"),
            ],
            [
                KeyboardButton("👥 Show users"),
                KeyboardButton("📊 Bot statistics"),
            ],
            [
                KeyboardButton("📢 Broadcast"),
                KeyboardButton("🆔 My Telegram ID"),
            ],
        ],
        resize_keyboard=True
    )


# ============================================================
# START BUTTONS
# ============================================================

def colored_button(text, callback_data, style):
    # Works with both older and newer python-telegram-bot versions.
    # New versions accept style directly; older versions forward it through api_kwargs.
    try:
        params = inspect.signature(InlineKeyboardButton).parameters
        if "style" in params:
            return InlineKeyboardButton(
                text,
                callback_data=callback_data,
                style=style
            )
        return InlineKeyboardButton(
            text,
            callback_data=callback_data,
            api_kwargs={"style": style}
        )
    except Exception:
        return InlineKeyboardButton(
            text,
            callback_data=callback_data
        )


def start_buttons():
    return InlineKeyboardMarkup(
        [
            [
                colored_button(
                    "😊 About Me",
                    "about",
                    "primary"
                ),
                colored_button(
                    "Close 🔒",
                    "close",
                    "danger"
                ),
            ]
        ]
    )


def about_buttons():
    return InlineKeyboardMarkup(
        [
            [
                colored_button(
                    "⬅️ BACK",
                    "back_start",
                    "primary"
                ),
                colored_button(
                    "Close 🔒",
                    "close",
                    "danger"
                ),
            ]
        ]
    )


# ============================================================
# ADMIN
# ============================================================

def admin_only(update):
    return (
        update.effective_user
        and update.effective_user.id in ADMIN_IDS
    )


# ============================================================
# USER
# ============================================================

def save_user(user_id):
    con = db()

    con.execute(
        """
        INSERT OR IGNORE INTO users(user_id)
        VALUES(?)
        """,
        (user_id,)
    )

    con.commit()
    con.close()


def get_user_name(user):
    if not user:
        return "User"

    first = (user.first_name or "").strip()

    if first:
        return first

    if user.username:
        return "@" + user.username

    return "User"


# ============================================================
# TOKEN
# ============================================================

def make_token():
    return secrets.token_urlsafe(18)


# ============================================================
# BOT LINK
# ============================================================

async def make_link(context, token):
    bot = await context.bot.get_me()

    return (
        f"https://t.me/"
        f"{bot.username}"
        f"?start={token}"
    )


# ============================================================
# MESSAGE LINK PARSER
# ============================================================

def extract_message_link(text):
    if not text:
        return None

    text = text.strip()

    match = re.search(
        r"https?://t\.me/c/(\d+)/(\d+)",
        text
    )

    if match:
        return {
            "chat_id": "-100" + match.group(1),
            "message_id": int(match.group(2)),
            "type": "private"
        }

    match = re.search(
        r"https?://t\.me/([A-Za-z0-9_]+)/(\d+)",
        text
    )

    if match:
        return {
            "chat_id": "@" + match.group(1),
            "message_id": int(match.group(2)),
            "type": "public"
        }

    return None


# ============================================================
# SAFE COPY MESSAGE
# ============================================================

async def safe_copy_message(
    bot,
    chat_id,
    from_chat_id,
    message_id,
    max_retries=30
):
    retries = 0

    while True:
        try:
            return await bot.copy_message(
                chat_id=chat_id,
                from_chat_id=from_chat_id,
                message_id=message_id
            )

        except RetryAfter as e:
            retries += 1

            if retries > max_retries:
                raise

            await asyncio.sleep(
                max(int(e.retry_after), 1)
            )


# ============================================================
# FAST BATCH COPY
# ============================================================

async def copy_range_fast(
    bot,
    source_chat_id,
    storage_chat_id,
    low,
    high,
    token,
    con
):
    position = 1
    copied = 0
    failed = 0

    for group_start in range(low, high + 1, 100):

        group_end = min(
            group_start + 99,
            high
        )

        message_ids = list(
            range(group_start, group_end + 1)
        )

        group_done = False
        group_retries = 0

        while not group_done:

            try:

                stored_messages = await bot.copy_messages(
                    chat_id=storage_chat_id,
                    from_chat_id=source_chat_id,
                    message_ids=message_ids
                )

                for stored in stored_messages:

                    if stored is None:
                        continue

                    con.execute(
                        """
                        INSERT INTO batch_items
                        (
                            token,
                            chat_id,
                            message_id,
                            position
                        )
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            token,
                            str(storage_chat_id),
                            stored.message_id,
                            position
                        )
                    )

                    position += 1
                    copied += 1

                con.commit()
                group_done = True

            except RetryAfter as e:

                group_retries += 1

                if group_retries > 30:
                    break

                await asyncio.sleep(
                    max(int(e.retry_after), 1)
                )

            except Exception as e:

                print(
                    f"GROUP COPY ERROR "
                    f"{group_start}-{group_end}:",
                    repr(e)
                )

                break

        if group_done:
            continue

        # ====================================================
        # FALLBACK
        # ====================================================

        for message_id in message_ids:

            retries = 0

            while True:

                try:

                    stored = await bot.copy_message(
                        chat_id=storage_chat_id,
                        from_chat_id=source_chat_id,
                        message_id=message_id
                    )

                    con.execute(
                        """
                        INSERT INTO batch_items
                        (
                            token,
                            chat_id,
                            message_id,
                            position
                        )
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            token,
                            str(storage_chat_id),
                            stored.message_id,
                            position
                        )
                    )

                    position += 1
                    copied += 1

                    if copied % 10 == 0:
                        con.commit()

                    break

                except RetryAfter as e:

                    await asyncio.sleep(
                        max(int(e.retry_after), 1)
                    )

                except Exception as e:

                    retries += 1

                    print(
                        f"MESSAGE {message_id} "
                        f"ERROR {retries}/5:",
                        repr(e)
                    )

                    if retries >= 5:
                        failed += 1
                        break

                    await asyncio.sleep(
                        min(retries * 2, 10)
                    )

    con.commit()

    return copied, failed


# ============================================================
# START TEXT
# ============================================================

def start_text(name):
    return (
        f"<b>Hello {name},</b>\n\n"
        "<b>I can store private files in Specified Channel "
        "and other users can access it from special link.</b>\n\n"
        f"▌ <b>Check -</b> {CHANNEL_USERNAME}"
    )


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    user = update.effective_user

    if not user:
        return

    save_user(user.id)

    name = get_user_name(user)

    # ========================================================
    # NORMAL /START
    # ========================================================

    if not context.args:

        try:

            await update.message.reply_text(
                start_text(name),
                parse_mode="HTML",
                reply_markup=start_buttons(),
                message_effect_id=FIRE_EFFECT_ID
            )

        except Exception as e:

            print(
                "START EFFECT ERROR:",
                repr(e)
            )

            await update.message.reply_text(
                start_text(name),
                parse_mode="HTML",
                reply_markup=start_buttons()
            )

        return

    # ========================================================
    # LINK /START
    # ========================================================

    token = context.args[0].strip()

    con = db()

    link = con.execute(
        """
        SELECT *
        FROM links
        WHERE token=?
        """,
        (token,)
    ).fetchone()

    if not link:

        con.close()

        await update.message.reply_text(
            f"<b>Hello {name},</b>\n\n"
            "❌ Link not found.",
            parse_mode="HTML",
            reply_markup=start_buttons()
        )

        return

    # ========================================================
    # SINGLE
    # ========================================================

    if link["link_type"] == "single":

        chat_id = link["chat_id"]
        message_id = link["message_id"]

        con.close()

        try:

            await safe_copy_message(
                context.bot,
                update.effective_chat.id,
                chat_id,
                message_id
            )

        except Exception as e:

            print(
                "SINGLE SEND ERROR:",
                repr(e)
            )

            await update.message.reply_text(
                "❌ File could not be sent."
            )

        return

    # ========================================================
    # BATCH
    # ========================================================

    items = con.execute(
        """
        SELECT *
        FROM batch_items
        WHERE token=?
        ORDER BY position ASC
        """,
        (token,)
    ).fetchall()

    con.close()

    if not items:

        await update.message.reply_text(
            "❌ Batch is empty.",
            reply_markup=start_buttons()
        )

        return

    await update.message.reply_text(
        f"<b>Hello {name},</b>\n\n"
        "📦 <b>Batch found!</b>\n\n"
        f"📁 Messages: {len(items)}\n"
        "⏳ Sending...",
        parse_mode="HTML"
    )

    sent = 0
    failed = 0

    message_ids = [
        int(item["message_id"])
        for item in items
    ]

    for group_start in range(
        0,
        len(message_ids),
        100
    ):

        group_ids = message_ids[
            group_start:group_start + 100
        ]

        try:

            copied_messages = await context.bot.copy_messages(
                chat_id=update.effective_chat.id,
                from_chat_id=STORAGE_CHAT_ID,
                message_ids=group_ids
            )

            sent += len(
                [
                    x for x in copied_messages
                    if x is not None
                ]
            )

        except RetryAfter as e:

            await asyncio.sleep(
                max(int(e.retry_after), 1)
            )

            try:

                copied_messages = await context.bot.copy_messages(
                    chat_id=update.effective_chat.id,
                    from_chat_id=STORAGE_CHAT_ID,
                    message_ids=group_ids
                )

                sent += len(
                    [
                        x for x in copied_messages
                        if x is not None
                    ]
                )

            except Exception as e2:

                print(
                    "GROUP RETRY ERROR:",
                    repr(e2)
                )

                for message_id in group_ids:

                    try:

                        await safe_copy_message(
                            context.bot,
                            update.effective_chat.id,
                            STORAGE_CHAT_ID,
                            message_id
                        )

                        sent += 1

                    except Exception as e3:

                        print(
                            "INDIVIDUAL SEND ERROR:",
                            repr(e3)
                        )

                        failed += 1

        except Exception as e:

            print(
                "GROUP SEND ERROR:",
                repr(e)
            )

            for message_id in group_ids:

                try:

                    await safe_copy_message(
                        context.bot,
                        update.effective_chat.id,
                        STORAGE_CHAT_ID,
                        message_id
                    )

                    sent += 1

                except Exception as e2:

                    print(
                        "INDIVIDUAL SEND ERROR:",
                        repr(e2)
                    )

                    failed += 1

    await update.message.reply_text(
        "✅ <b>Batch completed!</b>\n\n"
        f"✅ Sent: {sent}\n"
        f"❌ Failed: {failed}",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


# ============================================================
# CALLBACK BUTTONS
# ============================================================

async def button_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    user = update.effective_user
    name = get_user_name(user)

    # ========================================================
    # ABOUT ME
    # ========================================================

    if query.data == "about":

        await query.edit_message_text(
            f"<b>Hello {name},</b>\n\n"
            "<b>I can store private files in Specified Channel "
            "and other users can access it from special link.</b>\n\n"
            "○ Creator: BC\n"
            "○ Language: Python\n"
            "○ Library: python-telegram-bot\n"
            f"○ Channel - {CHANNEL_USERNAME}",
            parse_mode="HTML",
            reply_markup=about_buttons()
        )

        return

    # ========================================================
    # BACK
    # ========================================================

    if query.data == "back_start":

        try:

            await query.edit_message_text(
                start_text(name),
                parse_mode="HTML",
                reply_markup=start_buttons()
            )

        except Exception as e:

            print(
                "BACK ERROR:",
                repr(e)
            )

        return

    # ========================================================
    # CLOSE
    # ========================================================

    if query.data == "close":

        try:

            await query.delete_message()

        except Exception as e:

            print(
                "CLOSE ERROR:",
                repr(e)
            )

        return


# ============================================================
# SINGLE LINK BUTTON
# ============================================================

async def file_link_button(
    update,
    context
):
    if not admin_only(update):

        await update.message.reply_text(
            "⛔ ADMIN ONLY"
        )

        return

    context.user_data.clear()
    context.user_data["mode"] = "single"

    await update.message.reply_text(
        "🎬 Send or forward the file now."
    )


# ============================================================
# BATCH BUTTON
# ============================================================

async def batch_button(
    update,
    context
):
    if not admin_only(update):

        await update.message.reply_text(
            "⛔ ADMIN ONLY"
        )

        return

    context.user_data.clear()
    context.user_data["mode"] = "batch_first"

    await update.message.reply_text(
        "📦 BATCH MODE\n\n"
        "Send the FIRST Telegram message link."
    )


# ============================================================
# CREATE SINGLE LINK
# ============================================================

async def create_single_link(
    message,
    context
):
    try:

        stored = await safe_copy_message(
            context.bot,
            STORAGE_CHAT_ID,
            message.chat_id,
            message.message_id
        )

        storage_chat_id = str(
            STORAGE_CHAT_ID
        )

        storage_message_id = stored.message_id

    except Exception as e:

        print(
            "SINGLE STORAGE ERROR:",
            repr(e)
        )

        await message.reply_text(
            "❌ Could not save the file to storage."
        )

        return

    key = make_token()

    con = db()

    try:

        con.execute(
            """
            INSERT INTO links
            (
                token,
                link_type,
                chat_id,
                message_id
            )
            VALUES (?, 'single', ?, ?)
            """,
            (
                key,
                storage_chat_id,
                storage_message_id
            )
        )

        con.commit()

    except Exception as e:

        print(
            "SINGLE DATABASE ERROR:",
            repr(e)
        )

        con.rollback()
        con.close()

        await message.reply_text(
            "❌ Could not create the link."
        )

        return

    con.close()

    link = await make_link(
        context,
        key
    )

    context.user_data.clear()

    await message.reply_text(
        "✅ FILE LINK CREATED\n\n"
        f"🔗 {link}",
        reply_markup=main_menu()
    )


# ============================================================
# RECEIVE MEDIA
# ============================================================

async def receive_media(
    update,
    context
):
    message = update.message

    if not message:
        return

    if not admin_only(update):

        await message.reply_text(
            "⛔ ADMIN ONLY"
        )

        return

    save_user(
        update.effective_user.id
    )

    mode = context.user_data.get("mode")

    if mode == "single":

        await create_single_link(
            message,
            context
        )

        return

    await message.reply_text(
        "ℹ️ Choose an option from the menu.",
        reply_markup=main_menu()
    )


# ============================================================
# FIRST LINK
# ============================================================

async def process_first_link(
    update,
    context,
    text
):
    parsed = extract_message_link(text)

    if not parsed:

        await update.message.reply_text(
            "❌ Invalid Telegram message link.\n\n"
            "Example:\n"
            "https://t.me/bekimo/92149"
        )

        return

    context.user_data["first_link"] = text.strip()
    context.user_data["first_parsed"] = parsed
    context.user_data["mode"] = "batch_last"

    await update.message.reply_text(
        "✅ FIRST LINK SAVED.\n\n"
        "Now send the LAST Telegram message link."
    )


# ============================================================
# LAST LINK
# ============================================================

async def process_last_link(
    update,
    context,
    text
):
    parsed = extract_message_link(text)

    if not parsed:

        await update.message.reply_text(
            "❌ Invalid Telegram message link."
        )

        return

    first = context.user_data.get("first_parsed")
    first_link = context.user_data.get("first_link")

    if not first or not first_link:

        context.user_data.clear()

        await update.message.reply_text(
            "❌ First link is missing."
        )

        return

    if str(first["chat_id"]) != str(
        parsed["chat_id"]
    ):

        await update.message.reply_text(
            "❌ FIRST and LAST links must "
            "belong to the same channel."
        )

        return

    first_id = int(first["message_id"])
    last_id = int(parsed["message_id"])

    low = min(first_id, last_id)
    high = max(first_id, last_id)

    source_chat_id = str(first["chat_id"])

    total = high - low + 1

    key = make_token()

    con = db()

    try:

        con.execute(
            """
            INSERT INTO links
            (
                token,
                link_type,
                chat_id,
                message_id,
                first_link,
                last_link
            )
            VALUES (?, 'batch', ?, ?, ?, ?)
            """,
            (
                key,
                str(STORAGE_CHAT_ID),
                0,
                first_link,
                text.strip()
            )
        )

        con.commit()

    except Exception as e:

        print(
            "BATCH DATABASE ERROR:",
            repr(e)
        )

        con.rollback()
        con.close()

        await update.message.reply_text(
            "❌ Could not create batch."
        )

        return

    await update.message.reply_text(
        "⚡ Processing batch...\n\n"
        f"📁 Messages: {total}\n"
        "⏳ Please wait..."
    )

    try:

        copied, failed = await copy_range_fast(
            bot=context.bot,
            source_chat_id=source_chat_id,
            storage_chat_id=STORAGE_CHAT_ID,
            low=low,
            high=high,
            token=key,
            con=con
        )

    except Exception as e:

        print(
            "BATCH CREATION ERROR:",
            repr(e)
        )

        traceback.print_exc()

        con.close()

        await update.message.reply_text(
            "❌ Batch creation failed."
        )

        return

    con.close()

    bot_link = await make_link(
        context,
        key
    )

    context.user_data.clear()

    await update.message.reply_text(
        "✅ BATCH LINK CREATED!\n\n"
        f"▶️ First: {low}\n"
        f"▶️ Last: {high}\n"
        f"📁 Stored: {copied}\n"
        f"❌ Failed: {failed}\n\n"
        f"🔗 {bot_link}",
        reply_markup=main_menu()
    )


# ============================================================
# SHOW USERS
# ============================================================

async def show_users(
    update,
    context
):
    if not admin_only(update):

        await update.message.reply_text(
            "⛔ ADMIN ONLY"
        )

        return

    con = db()

    count = con.execute(
        """
        SELECT COUNT(*)
        FROM users
        """
    ).fetchone()[0]

    con.close()

    await update.message.reply_text(
        f"👥 TOTAL USERS: {count}"
    )


# ============================================================
# STATISTICS
# ============================================================

async def stats(
    update,
    context
):
    if not admin_only(update):

        await update.message.reply_text(
            "⛔ ADMIN ONLY"
        )

        return

    con = db()

    users = con.execute(
        """
        SELECT COUNT(*)
        FROM users
        """
    ).fetchone()[0]

    links = con.execute(
        """
        SELECT COUNT(*)
        FROM links
        """
    ).fetchone()[0]

    batches = con.execute(
        """
        SELECT COUNT(*)
        FROM links
        WHERE link_type='batch'
        """
    ).fetchone()[0]

    batch_messages = con.execute(
        """
        SELECT COUNT(*)
        FROM batch_items
        """
    ).fetchone()[0]

    con.close()

    await update.message.reply_text(
        "📊 BOT STATISTICS\n\n"
        f"👥 Users: {users}\n"
        f"🔗 Links: {links}\n"
        f"📦 Batches: {batches}\n"
        f"📁 Batch messages: {batch_messages}"
    )


# ============================================================
# MY ID
# ============================================================

async def my_id(
    update,
    context
):
    await update.message.reply_text(
        "🆔 Your Telegram ID:\n"
        f"{update.effective_user.id}"
    )


# ============================================================
# BROADCAST
# ============================================================

async def broadcast(
    update,
    context
):
    if not admin_only(update):

        await update.message.reply_text(
            "⛔ ADMIN ONLY"
        )

        return

    context.user_data["mode"] = "broadcast"

    await update.message.reply_text(
        "📢 Send the broadcast message."
    )


# ============================================================
# BROADCAST MESSAGE
# ============================================================

async def broadcast_message(
    update,
    context
):
    if not admin_only(update):
        return

    con = db()

    users = con.execute(
        """
        SELECT user_id
        FROM users
        """
    ).fetchall()

    con.close()

    sent = 0

    for row in users:

        try:

            await safe_copy_message(
                context.bot,
                row["user_id"],
                update.effective_chat.id,
                update.message.message_id
            )

            sent += 1

        except Exception as e:

            print(
                "BROADCAST ERROR:",
                repr(e)
            )

        await asyncio.sleep(0.05)

    context.user_data.clear()

    await update.message.reply_text(
        "✅ Broadcast finished.\n\n"
        f"📨 Sent: {sent}",
        reply_markup=main_menu()
    )


# ============================================================
# TEXT HANDLER
# ============================================================

async def text_handler(
    update,
    context
):
    if not update.message:
        return

    text = update.message.text or ""

    if text == "🎬 Generate file link":

        await file_link_button(
            update,
            context
        )

        return

    if text == "📦 Generate batch link":

        await batch_button(
            update,
            context
        )

        return

    if text == "👥 Show users":

        await show_users(
            update,
            context
        )

        return

    if text == "📊 Bot statistics":

        await stats(
            update,
            context
        )

        return

    if text == "📢 Broadcast":

        await broadcast(
            update,
            context
        )

        return

    if text == "🆔 My Telegram ID":

        await my_id(
            update,
            context
        )

        return

    mode = context.user_data.get("mode")

    if mode == "batch_first":

        if admin_only(update):

            await process_first_link(
                update,
                context,
                text
            )

        return

    if mode == "batch_last":

        if admin_only(update):

            await process_last_link(
                update,
                context,
                text
            )

        return

    if mode == "broadcast":

        await broadcast_message(
            update,
            context
        )

        return


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update,
    context
):
    print(
        "BOT ERROR:",
        repr(context.error)
    )

    traceback.print_exc()


# ============================================================
# MAIN
# ============================================================

async def main():

    setup_db()

    if (
        not BOT_TOKEN
        or BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE"
    ):

        raise RuntimeError(
            "Please set BOT_TOKEN."
        )

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    # ========================================================
    # COMMANDS
    # ========================================================

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "batch",
            batch_button
        )
    )

    app.add_handler(
        CommandHandler(
            "genlink",
            file_link_button
        )
    )

    # ========================================================
    # CALLBACK BUTTONS
    # ========================================================

    app.add_handler(
        CallbackQueryHandler(
            button_callback
        )
    )

    # ========================================================
    # MEDIA
    # ========================================================

    app.add_handler(
        MessageHandler(
            (
                filters.VIDEO
                | filters.AUDIO
                | filters.Document.ALL
                | filters.PHOTO
                | filters.Sticker.ALL
            ),
            receive_media
        )
    )

    # ========================================================
    # TEXT
    # ========================================================

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    # ========================================================
    # ERROR
    # ========================================================

    app.add_error_handler(
        error_handler
    )

    print("==============================")
    print("       BOT IS RUNNING")
    print("==============================")

    await app.initialize()
    await app.start()

    if app.updater is None:
        raise RuntimeError(
            "Telegram updater is unavailable."
        )

    await app.updater.start_polling()

    try:

        while True:
            await asyncio.sleep(3600)

    finally:

        await app.updater.stop()
        await app.stop()
        await app.shutdown()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        print("Bot stopped.")
