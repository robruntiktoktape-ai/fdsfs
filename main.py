import os
import sqlite3
import secrets
import string
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
import asyncio
from aiohttp import web

# === НАСТРОЙКИ (всё вшито) ===
BOT_TOKEN = "9200973387:AAHMW82BjFLOFoK2K3DgZjYEtQDzvr1iWJo"
ADMIN_ID = 7922416281
DB_PATH = "keys.db"

# Секрет между этим процессом и main.go (лоадером). В бинарнике
# лоадера его рано или поздно достанут через strings — это нормально,
# он не защищает от реверса, только от случайных сторонних запросов.
CHECK_KEY = "da26dea86e37683154836d1c719334d6f549f715055747281d576f47da6bc294"

# Отдельный, более чувствительный секрет для создания/бана/удаления
# ключей через curl (дублирует то, что и так можно через Telegram).
ADMIN_KEY = "dcdf3c4e129dcbec049ea4cc19912b52c70ea9975a8271381fbfc77310f12b9a"

API_HOST = "0.0.0.0"
API_PORT = int(os.environ.get("PORT", 8765))

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


# === ОБЩАЯ ЛОГИКА (используется и ботом, и HTTP API) ===
def check_key_logic(key: str, hwid: str):
    key = key.upper().strip()
    hwid = hwid.strip()

    if len(key) != 16 or not all(c in string.ascii_uppercase + string.digits for c in key):
        return False, "invalid_key_format"
    if not hwid:
        return False, "invalid_hwid"

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

        now = datetime.utcnow().isoformat()
        conn.execute(
            "UPDATE keys SET activated = 1, hwid = ?, activated_at = ? WHERE key = ?",
            (hwid, now, key)
        )
        conn.commit()
        return True, f"activated_{row['duration_days']}_days"


def format_response(valid: bool, result_code: str):
    if result_code.startswith("activated_"):
        days = result_code.replace("activated_", "").replace("_days", "")
        return valid, f"активация успешна, срок {days} дней", result_code
    messages = {
        "welcome_back": "добро пожаловать обратно",
        "key_not_found": "ключ не найден",
        "key_banned": "ключ заблокирован",
        "hwid_mismatch": "ключ уже активирован на другом устройстве",
        "key_expired": "срок действия истёк",
        "invalid_key_format": "неверный формат ключа",
        "invalid_hwid": "неверный HWID",
    }
    return valid, messages.get(result_code, "неизвестная ошибка"), result_code


def generate_key() -> str:
    return "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(16))


def create_key(days: int) -> str:
    key = generate_key()
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO keys (key, duration_days, created_at) VALUES (?, ?, ?)",
            (key, days, now)
        )
        conn.commit()
    return key


def ban_key(key: str) -> bool:
    with get_db() as conn:
        cur = conn.execute("UPDATE keys SET banned = 1 WHERE key = ?", (key.upper().strip(),))
        conn.commit()
        return cur.rowcount > 0


def delete_key(key: str) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM keys WHERE key = ?", (key.upper().strip(),))
        conn.commit()
        return cur.rowcount > 0


def list_keys(limit: int = 20):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT key, duration_days, activated, hwid, banned, created_at "
            "FROM keys ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# === TELEGRAM-КОМАНДЫ ДЛЯ АДМИНА ===
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
    if days <= 0:
        await message.answer("дни должны быть положительным числом")
        return

    key = create_key(days)
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
    if not ban_key(key):
        await message.answer(f"Ключ `{key}` не найден", parse_mode="Markdown")
        return
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
    if not delete_key(key):
        await message.answer(f"Ключ `{key}` не найден", parse_mode="Markdown")
        return
    await message.answer(f"✓ Ключ `{key}` удалён", parse_mode="Markdown")


@dp.message(Command("list"))
async def cmd_list(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    rows = list_keys(20)
    if not rows:
        await message.answer("Ключей нет")
        return
    msg = "Последние 20 ключей:\n\n"
    for r in rows:
        st = "🔴" if r["banned"] else ("🟢" if r["activated"] else "⚪")
        msg += f"`{r['key']}` | {r['duration_days']}д | {st}\n"
    await message.answer(msg, parse_mode="Markdown")


# === HTTP API ДЛЯ ЛОАДЕРА (main.go) ===
async def handle_check(request: web.Request):
    if request.headers.get("X-API-Key") != CHECK_KEY:
        return web.json_response({"ok": False, "reason": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "reason": "bad_json"}, status=400)

    key = str(data.get("key", ""))
    hwid = str(data.get("hwid", ""))
    if not key or not hwid:
        return web.json_response({"ok": False, "reason": "missing_fields"}, status=400)

    valid, result_code = check_key_logic(key, hwid)
    ok, message, reason = format_response(valid, result_code)
    return web.json_response({"ok": ok, "message": message, "reason": reason})


# === HTTP API ДЛЯ АДМИНА (альтернатива Telegram-командам) ===
def require_admin(request: web.Request) -> bool:
    return request.headers.get("X-Admin-Key") == ADMIN_KEY


async def handle_create(request: web.Request):
    if not require_admin(request):
        return web.json_response({"ok": False, "reason": "unauthorized"}, status=401)
    try:
        data = await request.json()
        days = int(data.get("days", 0))
    except Exception:
        return web.json_response({"ok": False, "reason": "bad_json"}, status=400)
    if days <= 0:
        return web.json_response({"ok": False, "reason": "invalid_days"}, status=400)

    key = create_key(days)
    return web.json_response({"ok": True, "key": key, "duration_days": days})


async def handle_ban(request: web.Request):
    if not require_admin(request):
        return web.json_response({"ok": False, "reason": "unauthorized"}, status=401)
    key = str((await request.json()).get("key", ""))
    if not ban_key(key):
        return web.json_response({"ok": False, "reason": "key_not_found"}, status=404)
    return web.json_response({"ok": True})


async def handle_delete(request: web.Request):
    if not require_admin(request):
        return web.json_response({"ok": False, "reason": "unauthorized"}, status=401)
    key = str((await request.json()).get("key", ""))
    if not delete_key(key):
        return web.json_response({"ok": False, "reason": "key_not_found"}, status=404)
    return web.json_response({"ok": True})


async def handle_list(request: web.Request):
    if not require_admin(request):
        return web.json_response({"ok": False, "reason": "unauthorized"}, status=401)
    return web.json_response({"ok": True, "keys": list_keys(50)})


async def run_http_api():
    app = web.Application()
    app.router.add_post("/api/check", handle_check)
    app.router.add_post("/api/admin/create", handle_create)
    app.router.add_post("/api/admin/ban", handle_ban)
    app.router.add_post("/api/admin/delete", handle_delete)
    app.router.add_get("/api/admin/list", handle_list)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, API_HOST, API_PORT)
    await site.start()
    print(f"HTTP API слушает на {API_HOST}:{API_PORT}")


async def main():
    init_db()
    await run_http_api()
    print("Telegram-бот запущен. Жду команды и запросы от лоадера...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
