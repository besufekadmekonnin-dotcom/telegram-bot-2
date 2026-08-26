
import asyncio
import logging
import os
import secrets
import sqlite3
from pathlib import Path
from threading import Thread

import requests
from flask import Flask, abort, Response
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

DB_PATH = os.getenv("DB_PATH", "bot.db")
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
PORT = int(os.getenv("PORT", "8080"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Set it before starting the bot.")
if not BASE_URL:
    raise RuntimeError("BASE_URL is missing. Set it to your public HTTPS URL.")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

app = Flask(__name__)

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        joined_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT UNIQUE NOT NULL,
        file_id TEXT NOT NULL,
        file_name TEXT,
        mime_type TEXT,
        size INTEGER,
        owner_id INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        downloads INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT UNIQUE NOT NULL,
        owner_id INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        downloads INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS batch_files (
        batch_id INTEGER NOT NULL,
        file_id INTEGER NOT NULL,
        PRIMARY KEY (batch_id, file_id)
    );
    """)
    conn.commit()
    conn.close()

def save_user(user):
    if not user:
        return
    conn = db()
    conn.execute("""
        INSERT INTO users(user_id, username, first_name)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name
    """, (user.id, user.username or "", user.first_name or ""))
    conn.commit()
    conn.close()

def is_admin(user_id):
    return ADMIN_ID != 0 and user_id == ADMIN_ID

def save_file(file_id, file_name, mime_type, size, owner_id):
    token = secrets.token_urlsafe(18)
    conn = db()
    conn.execute("""
        INSERT INTO files(token, file_id, file_name, mime_type, size, owner_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (token, file_id, file_name or "file", mime_type or "", size or 0, owner_id))
    conn.commit()
    conn.close()
    return token

def get_file_by_token(token):
    conn = db()
    row = conn.execute("SELECT * FROM files WHERE token=?", (token,)).fetchone()
    conn.close()
    return row

def create_batch(owner_id, file_ids):
    token = secrets.token_urlsafe(18)
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT INTO batches(token, owner_id) VALUES (?, ?)", (token, owner_id))
    batch_id = cur.lastrowid
    cur.executemany(
        "INSERT INTO batch_files(batch_id, file_id) VALUES (?, ?)",
        [(batch_id, x) for x in file_ids],
    )
    conn.commit()
    conn.close()
    return token

def get_batch(token):
    conn = db()
    batch = conn.execute("SELECT * FROM batches WHERE token=?", (token,)).fetchone()
    if not batch:
        conn.close()
        return None, []
    rows = conn.execute("""
        SELECT f.* FROM files f
        JOIN batch_files bf ON bf.file_id=f.id
        WHERE bf.batch_id=?
        ORDER BY f.id
    """, (batch["id"],)).fetchall()
    conn.close()
    return batch, rows

def tg_file_url(file_id):
    r = requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
        params={"file_id": file_id},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data)
    path = data["result"]["file_path"]
    return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"

def stream_telegram_file(row):
    url = tg_file_url(row["file_id"])
    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()

    def generate():
        for chunk in r.iter_content(chunk_size=1024 * 256):
            if chunk:
                yield chunk

    mime = row["mime_type"] or "application/octet-stream"
    name = row["file_name"] or "file"
    headers = {
        "Content-Disposition": f'attachment; filename="{name.replace(chr(34), "")}"'
    }
    return Response(generate(), content_type=mime, headers=headers)

@app.get("/f/<token>")
def download_file(token):
    row = get_file_by_token(token)
    if not row:
        abort(404)
    conn = db()
    conn.execute("UPDATE files SET downloads=downloads+1 WHERE id=?", (row["id"],))
    conn.commit()
    conn.close()
    try:
        return stream_telegram_file(row)
    except Exception as e:
        log.exception("Download failed: %s", e)
        abort(502)

@app.get("/b/<token>")
def download_batch(token):
    batch, rows = get_batch(token)
    if not batch or not rows:
        abort(404)

    # A batch link opens a simple HTML page listing every file.
    items = []
    for row in rows:
        link = f"{BASE_URL}/f/{row['token']}"
        name = (row["file_name"] or "file").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        items.append(f'<li><a href="{link}">{name}</a></li>')

    conn = db()
    conn.execute("UPDATE batches SET downloads=downloads+1 WHERE id=?", (batch["id"],))
    conn.commit()
    conn.close()

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>File Store</title></head>
<body style="font-family:Arial;max-width:700px;margin:40px auto;padding:20px">
<h2>📁 File Store</h2><p>Files: {len(rows)}</p><ul>{''.join(items)}</ul>
</body></html>"""
    return Response(html, content_type="text/html; charset=utf-8")

def file_info_from_message(message):
    if message.document:
        f = message.document
        return f.file_id, f.file_name or "document", f.mime_type or "application/octet-stream", f.file_size or 0
    if message.video:
        f = message.video
        return f.file_id, "video.mp4", f.mime_type or "video/mp4", f.file_size or 0
    if message.audio:
        f = message.audio
        return f.file_id, message.audio.file_name or "audio", f.mime_type or "audio/mpeg", f.file_size or 0
    if message.voice:
        f = message.voice
        return f.file_id, "voice.ogg", f.mime_type or "audio/ogg", f.file_size or 0
    if message.photo:
        f = message.photo[-1]
        return f.file_id, "photo.jpg", "image/jpeg", f.file_size or 0
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    await update.message.reply_text(
        "👋 Welcome to File Store Bot!\n\n"
        "📁 Send me a file, video, photo or audio and I will save it.\n"
        "🔗 Reply to the file with /genlink to get a download link.\n"
        "📦 Use /batchlink to collect multiple files, then /done.\n\n"
        "Commands:\n"
        "/id - Show your Telegram ID\n"
        "/genlink - Generate a file link\n"
        "/batchlink - Start a batch\n"
        "/done - Finish a batch\n"
        "/users - Admin: show users\n"
        "/stats - Admin: show statistics\n"
        "/broadcast - Admin: broadcast a message"
    )

async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    await update.message.reply_text(f"🆔 Your Telegram ID: `{update.effective_user.id}`", parse_mode="Markdown")

async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    info = file_info_from_message(update.message)
    if not info:
        return

    file_id, name, mime, size = info
    token = save_file(file_id, name, mime, size, update.effective_user.id)

    if context.user_data.get("batch_mode"):
        context.user_data.setdefault("batch_file_ids", []).append(
            db_file_id_by_token(token)
        )
        await update.message.reply_text(
            f"✅ Added to batch: {name}\n📦 Files in batch: {len(context.user_data['batch_file_ids'])}"
        )
    else:
        await update.message.reply_text(
            f"✅ Saved: {name}\n\n"
            f"Reply to this message with /genlink to create the link."
        )

def db_file_id_by_token(token):
    row = get_file_by_token(token)
    return row["id"]

async def genlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    msg = update.message.reply_to_message
    if not msg:
        await update.message.reply_text("⚠️ Reply to a file message with /genlink.")
        return

    info = file_info_from_message(msg)
    if not info:
        await update.message.reply_text("⚠️ The replied message does not contain a supported file.")
        return

    # Find an existing record owned by this user, or create one.
    conn = db()
    row = conn.execute(
        "SELECT * FROM files WHERE file_id=? AND owner_id=? ORDER BY id DESC LIMIT 1",
        (info[0], update.effective_user.id),
    ).fetchone()
    conn.close()

    if not row:
        token = save_file(info[0], info[1], info[2], info[3], update.effective_user.id)
    else:
        token = row["token"]

    await update.message.reply_text(f"🔗 File link:\n{BASE_URL}/f/{token}")

async def batchlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    context.user_data["batch_mode"] = True
    context.user_data["batch_file_ids"] = []
    await update.message.reply_text(
        "📦 Batch mode started.\nSend the files one by one.\nWhen finished, send /done."
    )

async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("batch_mode"):
        await update.message.reply_text("ℹ️ No active batch.")
        return

    ids = context.user_data.get("batch_file_ids", [])
    if not ids:
        context.user_data.clear()
        await update.message.reply_text("⚠️ No files were added.")
        return

    token = create_batch(update.effective_user.id, ids)
    context.user_data.clear()
    await update.message.reply_text(
        f"📦 Batch created: {len(ids)} files\n\n🔗 {BASE_URL}/b/{token}"
    )

async def users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return
    conn = db()
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    await update.message.reply_text(f"👥 Total users: {count}")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return
    conn = db()
    users_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    files_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    downloads = conn.execute("SELECT COALESCE(SUM(downloads),0) FROM files").fetchone()[0]
    batches = conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
    conn.close()
    await update.message.reply_text(
        f"📊 Statistics\n\n"
        f"👥 Users: {users_count}\n"
        f"📁 Files: {files_count}\n"
        f"📦 Batches: {batches}\n"
        f"⬇️ Downloads: {downloads}"
    )

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return

    text = " ".join(context.args).strip()
    if not text and update.message.reply_to_message:
        text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""

    if not text:
        await update.message.reply_text(
            "Usage:\n/broadcast Your message\n\n"
            "Or reply to a message with /broadcast."
        )
        return

    conn = db()
    rows = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()

    sent = 0
    for row in rows:
        try:
            await context.bot.send_message(row["user_id"], text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass

    await update.message.reply_text(f"📢 Broadcast finished. Sent: {sent}/{len(rows)}")

def run_web():
    app.run(host="0.0.0.0", port=PORT, threaded=True)

def main():
    init_db()

    web_thread = Thread(target=run_web, daemon=True)
    web_thread.start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("id", show_id))
    application.add_handler(CommandHandler("genlink", genlink))
    application.add_handler(CommandHandler("batchlink", batchlink))
    application.add_handler(CommandHandler("done", done))
    application.add_handler(CommandHandler("users", users))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("broadcast", broadcast))

    supported = (
        filters.Document.ALL
        | filters.VIDEO
        | filters.AUDIO
        | filters.VOICE
        | filters.PHOTO
    )
    application.add_handler(MessageHandler(supported, receive_file))

    log.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
