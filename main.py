import os
import sqlite3
import secrets
import string
from datetime import datetime, timedelta
from contextlib import contextmanager

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
import asyncio

# === НАСТРОЙКИ (всё вшито) ===
BOT_TOKEN = "9200973387:AAHMW82BjFLOFoK2K3DgZjYEtQDzvr1iWJo"
ADMIN_ID = 7922416281
DB_PATH = "keys.db"

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === БАЗА ДАННЫХ ===
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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

# === ЛОГИКА ПРОВЕРКИ КЛЮЧА ===
def check_key_logic(key: str, hwid: str):
    key = key.upper().strip()
    hwid = hwid.strip()
    
    if len(key) != 16 or not all(c in string.ascii_uppercase + string.digits for c in key):
        return False, "invalid_key_format"

    with get_db() as conn:
        row = conn.execute("SELECT * FROM keys WHERE key = ?", (key,)).fetchone()
        
        if not row:
            return False, "key_not_found"
        
        if row["banned"] == 1:
            return False, "key_banned"
        
        if row["activated"] == 1:
            if row["hwid"] != hwid:
                return False, "hwid_mismatch"
            
            activated_at = datetime.fromisoformat(row["activated_at"])
            expires_at = activated_at + timedelta(days=row["duration_days"])
            if datetime.utcnow() > expires_at:
                return False, "key_expired"
            
            return True, "welcome_back"
        
        # Первая активация
        now = datetime.utcnow().isoformat()
        conn.execute(
            "UPDATE keys SET activated = 1, hwid = ?, activated_at = ? WHERE key = ?",
            (hwid, now, key)
        )
        conn.commit()
        return True, f"activated_{row['duration_days']}_days"

# === ОБРАБОТЧИКИ ДЛЯ ТЕБЯ (АДМИНА) ===
@dp.message(Command("start"))
async def cmd_start(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer(
        "Бот управления luno_cheats\n\n"
        "/add <дней> — создать ключ\n"
        "/ban <ключ> — заблокировать\n"
        "/delete <ключ> — удалить\n"
        "/list — список ключей"
    )

@dp.message(Command("add"))
async def cmd_add(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) != 2:
        await message.answer("Использование: /add <дней>")
        return
    try:
        days = int(args[1])
    except ValueError:
        await message.answer("дни должны быть числом")
        return
    
    key = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(16))
    now = datetime.utcnow().isoformat()
    
    with get_db() as conn:
        conn.execute("INSERT INTO keys (key, duration_days, created_at) VALUES (?, ?, ?)", (key, days, now))
        conn.commit()
    
    await message.answer(f"✓ Ключ создан:\n`{key}`\nСрок: {days} дней", parse_mode="Markdown")

@dp.message(Command("ban"))
async def cmd_ban(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) != 2:
        await message.answer("Использование: /ban <ключ>")
        return
    key = args[1].upper()
    with get_db() as conn:
        conn.execute("UPDATE keys SET banned = 1 WHERE key = ?", (key,))
        conn.commit()
    await message.answer(f"✓ Ключ `{key}` заблокирован", parse_mode="Markdown")

@dp.message(Command("delete"))
async def cmd_delete(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) != 2:
        await message.answer("Использование: /delete <ключ>")
        return
    key = args[1].upper()
    with get_db() as conn:
        conn.execute("DELETE FROM keys WHERE key = ?", (key,))
        conn.commit()
    await message.answer(f"✓ Ключ `{key}` удалён", parse_mode="Markdown")

@dp.message(Command("list"))
async def cmd_list(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    with get_db() as conn:
        rows = conn.execute("SELECT key, duration_days, activated, hwid, banned FROM keys ORDER BY created_at DESC LIMIT 20").fetchall()
    if not rows:
        await message.answer("Ключей нет")
        return
    msg = "Последние 20 ключей:\n\n"
    for r in rows:
        st = "🔴" if r["banned"] else ("🟢" if r["activated"] else "⚪")
        msg += f"`{r['key']}` | {r['duration_days']}д | {st}\n"
    await message.answer(msg, parse_mode="Markdown")

# === ОБРАБОТЧИК ЗАПРОСОВ ОТ ЛАУНЧЕРА ===
# Лаунчер шлёт сообщение с текстом: CHECK_KEY|<KEY>|<HWID>
# Бот отвечает: OK|<MESSAGE> или ERR|<REASON>
@dp.message()
async def handle_launcher_request(message: Message):
    text = message.text.strip()
    if not text.startswith("CHECK_KEY|"):
        return
    
    parts = text.split("|")
    if len(parts) != 3:
        return
    
    _, key, hwid = parts
    valid, result_code = check_key_logic(key, hwid)
    
    response_map = {
        "welcome_back": "OK|добро пожаловать обратно",
        "activated_30_days": "OK|активация успешна, срок 30 дней",
        "activated_7_days": "OK|активация успешна, срок 7 дней",
        "activated_1_days": "OK|активация успешна, срок 1 дней",
        "key_not_found": "ERR|ключ не найден",
        "key_banned": "ERR|ключ заблокирован",
        "hwid_mismatch": "ERR|ключ уже активирован на другом устройстве",
        "key_expired": "ERR|срок действия истёк",
        "invalid_key_format": "ERR|неверный формат ключа"
    }
    
    # Форматируем сообщение об активации с правильным количеством дней
    if result_code.startswith("activated_"):
        days = result_code.replace("activated_", "").replace("_days", "")
        resp_text = f"OK|активация успешна, срок {days} дней"
    else:
        resp_text = response_map.get(result_code, "ERR|unknown_error")
    
    await bot.send_message(chat_id=message.chat.id, text=resp_text)

async def main():
    init_db()
    print("Бот запущен. Жду запросы...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())