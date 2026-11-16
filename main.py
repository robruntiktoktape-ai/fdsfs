import os
import sqlite3
import secrets
import string
from datetime import datetime, timedelta
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

import telebot
from telebot import types

# === НАСТРОЙКИ (твои данные) ===
BOT_TOKEN = os.environ.get("BOT_TOKEN", "9200973387:AAHMW82BjFLOFoK2K3DgZjYEtQDzvr1iWJo")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "7922416281"))
API_SECRET = os.environ.get("API_SECRET", secrets.token_hex(32))

DB_PATH = "keys.db"

# === FASTAPI ===
app = FastAPI(title="Luno Cheats Auth")

class CheckKeyRequest(BaseModel):
    key: str
    hwid: str

class CheckKeyResponse(BaseModel):
    valid: bool
    message: str

# === БАЗА ДАННЫХ ===
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS keys (
                key TEXT PRIMARY KEY,
                duration_days INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                activated INTEGER DEFAULT 0,
                hwid TEXT,
                activated_at TEXT,
                banned INTEGER DEFAULT 0
            )
        """)
        conn.commit()

# === API ENDPOINTS ===
@app.post("/api/check_key", response_model=CheckKeyResponse)
def check_key(req: CheckKeyRequest):
    key = req.key.upper().strip()
    hwid = req.hwid.strip()

    if len(key) != 16 or not all(c in string.ascii_uppercase + string.digits for c in key):
        raise HTTPException(status_code=400, detail="invalid key format")

    with get_db() as conn:
        row = conn.execute("SELECT * FROM keys WHERE key = ?", (key,)).fetchone()

        if not row:
            raise HTTPException(status_code=403, detail="ключ не найден")

        if row["banned"] == 1:
            raise HTTPException(status_code=403, detail="ключ заблокирован")

        if row["activated"] == 1:
            if row["hwid"] != hwid:
                raise HTTPException(status_code=403, detail="ключ уже активирован на другом устройстве")
            
            activated_at = datetime.fromisoformat(row["activated_at"])
            expires_at = activated_at + timedelta(days=row["duration_days"])
            if datetime.utcnow() > expires_at:
                raise HTTPException(status_code=403, detail="срок действия ключа истёк")
            
            return CheckKeyResponse(valid=True, message="добро пожаловать обратно")

        # Первая активация
        now = datetime.utcnow().isoformat()
        conn.execute(
            "UPDATE keys SET activated = 1, hwid = ?, activated_at = ? WHERE key = ?",
            (hwid, now, key)
        )
        conn.commit()

        return CheckKeyResponse(valid=True, message=f"активация успешна, срок {row['duration_days']} дней")

@app.get("/health")
def health():
    return {"status": "ok"}

# === TELEGRAM BOT ===
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

def is_admin(message):
    return message.from_user.id == ADMIN_ID

@bot.message_handler(commands=["start"])
def cmd_start(message):
    if not is_admin(message):
        return
    bot.reply_to(message, "Бот управления ключами luno_cheats\n\n"
        "/addkey <дней> — создать ключ (пример: /addkey 30)\n"
        "/revoke <ключ> — удалить ключ\n"
        "/ban <ключ> — заблокировать ключ\n"
        "/list — список последних 30 ключей\n"
        "/stats — статистика")

@bot.message_handler(commands=["addkey"])
def cmd_addkey(message):
    if not is_admin(message):
        return
    args = message.text.split()
    if len(args) != 2:
        bot.reply_to(message, "Использование: /addkey <дней>")
        return
    try:
        days = int(args[1])
    except ValueError:
        bot.reply_to(message, "дни должны быть числом")
        return

    key = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(16))
    now = datetime.utcnow().isoformat()

    with get_db() as conn:
        conn.execute(
            "INSERT INTO keys (key, duration_days, created_at) VALUES (?, ?, ?)",
            (key, days, now)
        )
        conn.commit()

    bot.reply_to(message, f"✓ Ключ создан:\n`{key}`\nСрок: {days} дней")

@bot.message_handler(commands=["revoke"])
def cmd_revoke(message):
    if not is_admin(message):
        return
    args = message.text.split()
    if len(args) != 2:
        bot.reply_to(message, "Использование: /revoke <ключ>")
        return
    key = args[1].upper()

    with get_db() as conn:
        conn.execute("DELETE FROM keys WHERE key = ?", (key,))
        conn.commit()

    bot.reply_to(message, f"✓ Ключ `{key}` отозван")

@bot.message_handler(commands=["ban"])
def cmd_ban(message):
    if not is_admin(message):
        return
    args = message.text.split()
    if len(args) != 2:
        bot.reply_to(message, "Использование: /ban <ключ>")
        return
    key = args[1].upper()

    with get_db() as conn:
        conn.execute("UPDATE keys SET banned = 1 WHERE key = ?", (key,))
        conn.commit()

    bot.reply_to(message, f"✓ Ключ `{key}` заблокирован")

@bot.message_handler(commands=["list"])
def cmd_list(message):
    if not is_admin(message):
        return
    with get_db() as conn:
        rows = conn.execute(
            "SELECT key, duration_days, activated, hwid, banned FROM keys ORDER BY created_at DESC LIMIT 30"
        ).fetchall()

    if not rows:
        bot.reply_to(message, "Ключей нет")
        return

    msg = "Последние 30 ключей:\n\n"
    for row in rows:
        status = "🔴 забанен" if row["banned"] else ("🟢 активен" if row["activated"] else "⚪ свободен")
        hwid_short = row["hwid"][:8] + "..." if row["hwid"] else "-"
        msg += f"`{row['key']}` | {row['duration_days']}д | {status} | {hwid_short}\n"

    bot.reply_to(message, msg)

@bot.message_handler(commands=["stats"])
def cmd_stats(message):
    if not is_admin(message):
        return
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM keys").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM keys WHERE activated = 1").fetchone()[0]
        banned = conn.execute("SELECT COUNT(*) FROM keys WHERE banned = 1").fetchone()[0]
        free = total - active - banned

    bot.reply_to(message, f"📊 Статистика:\n\n"
        f"Всего: {total}\n"
        f"Активных: {active}\n"
        f"Свободных: {free}\n"
        f"Забанено: {banned}")

# === ЗАПУСК ===
def run_bot():
    print("Telegram bot started...")
    bot.infinity_polling()

if __name__ == "__main__":
    init_db()
    print("=== Luno Cheats Auth Server ===")
    print(f"Admin ID: {ADMIN_ID}")
    
    import threading
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    port = int(os.environ.get("PORT", 8080))
    print(f"Starting FastAPI on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)