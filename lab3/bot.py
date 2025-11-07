#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram-bot: Справочник музеев Санкт-Петербурга
+ Погода (Open-Meteo)
+ Маршрут (Яндекс.Карты)
+ План (погода + маршрут)
+ Обратная связь (/feedback) -> data/feedback.json
+ Статистика (SQLite) + /stats (для администратора)
"""

import json
import logging
import os
import random
import time
import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus
from datetime import datetime, timedelta

from dotenv import load_dotenv
load_dotenv()

import requests
from rapidfuzz import fuzz, process

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    ConversationHandler,
    filters,
)

# ---------------------------- ЛОГИРОВАНИЕ ----------------------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("spb-museums-bot")

# ---------------------------- КОНСТАНТЫ ----------------------------
DATA_DIR = Path(__file__).parent / "data"
MUSEUMS_PATH = DATA_DIR / "museums.json"
DEFAULT_CITY = "Санкт-Петербург"

OPEN_METEO_GEOCODE = (
    "https://geocoding-api.open-meteo.com/v1/search?name={name}&count=1&language=ru&format=json"
)
OPEN_METEO_FORECAST = (
    "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,weather_code&timezone={tz}"
)

WEATHER_CODE_MAP = {
    0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "изморозь", 51: "лёгкая морось", 53: "умеренная морось",
    55: "сильная морось", 61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
    66: "ледяной дождь", 67: "сильный ледяной дождь", 71: "небольшой снег",
    73: "снег", 75: "сильный снег", 77: "снежные зёрна", 80: "ливневые дожди",
    81: "сильные ливни", 82: "очень сильные ливни", 85: "снегопад", 86: "сильный снегопад",
    95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом",
}

# ---------------------------- УТИЛИТЫ ----------------------------
def escape_md(text: str) -> str:
    return (text or "").replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")

def load_museums() -> List[Dict[str, Any]]:
    if not MUSEUMS_PATH.exists():
        raise FileNotFoundError(f"Не найден файл с данными: {MUSEUMS_PATH}")
    with MUSEUMS_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Формат museums.json некорректен: ожидается список объектов")
    return data

def list_museums_text(museums: List[Dict[str, Any]]) -> str:
    lines = [f"{m['id']}. {m['name']}" for m in museums]
    return "📜 *Список музеев:*\n" + "\n".join(lines)

def format_museum_card(m: Dict[str, Any]) -> str:
    name = escape_md(m.get("name", "—"))
    hours = escape_md(m.get("hours", "—"))
    address = escape_md(m.get("address", "—"))
    tickets = escape_md(m.get("tickets", "—"))
    site = m.get("site", "") or "—"
    lines = [
        f"🖼 *{name}*",
        f"⏰ {hours}",
        f"📍 {address}",
        f"🎟 {tickets}",
        f"🌐 {site}",
    ]
    return "\n".join(lines)

def build_yandex_route_link(address: str) -> str:
    return f"https://yandex.ru/maps/?rtext=~{quote_plus(address)}"

def normalize(s: str) -> str:
    return " ".join((s or "").strip().lower().split())

def find_museum_by_id(museums: List[Dict[str, Any]], mid: int) -> Optional[Dict[str, Any]]:
    for m in museums:
        if int(m.get("id")) == mid:
            return m
    return None

def fuzzy_find_museums(museums: List[Dict[str, Any]], query: str, limit: int = 5) -> List[Tuple[Dict[str, Any], int]]:
    choices = {m["name"]: m for m in museums}
    results = process.extract(query, choices.keys(), scorer=fuzz.WRatio, limit=limit)
    return [(choices[name], int(score)) for name, score, _ in results if score >= 50]

# ---------------------------- HTTP СЕССИЯ С RETRY ----------------------------
class Http:
    def __init__(self, timeout: float = 7.0, retries: int = 2):
        self.s = requests.Session()
        self.timeout = timeout
        self.retries = retries

    def get(self, url: str, **kwargs) -> requests.Response:
        last_exc = None
        for attempt in range(self.retries + 1):
            try:
                return self.s.get(url, timeout=self.timeout, **kwargs)
            except requests.RequestException as e:
                last_exc = e
                if attempt < self.retries:
                    time.sleep(0.6 * (attempt + 1))
                else:
                    raise last_exc

http = Http()

# ---------------------------- ПОГОДА ----------------------------
@lru_cache(maxsize=64)
def geocode_city(name: str) -> Optional[Tuple[float, float, str, str]]:
    url = OPEN_METEO_GEOCODE.format(name=quote_plus(name))
    r = http.get(url); r.raise_for_status()
    data = r.json()
    results = data.get("results") or []
    if not results:
        return None
    top = results[0]
    lat = float(top["latitude"]); lon = float(top["longitude"])
    tz = top.get("timezone") or "Europe/Moscow"
    resolved = top.get("name") or name
    return lat, lon, tz, resolved

@lru_cache(maxsize=128)
def get_weather(lat: float, lon: float, tz: str) -> Optional[Dict[str, Any]]:
    url = OPEN_METEO_FORECAST.format(lat=lat, lon=lon, tz=quote_plus(tz))
    r = http.get(url); r.raise_for_status()
    data = r.json()
    current = data.get("current") or {}
    temp = current.get("temperature_2m")
    code = current.get("weather_code")
    desc = WEATHER_CODE_MAP.get(code, "погода неизвестна")
    return {"temperature": temp, "code": code, "description": desc}

def render_weather_block(city_display: str, weather: Dict[str, Any]) -> str:
    t = weather.get("temperature"); d = weather.get("description", "—")
    if t is None:
        return f"🌦 Погода в {escape_md(city_display)}: данные недоступны"
    sign = "+" if t >= 0 else "−"
    t_abs = abs(float(t))
    return f"🌦 *Погода в {escape_md(city_display)}:*\nСейчас: {sign}{t_abs:.1f} °C, {escape_md(d)}"

# ---------------------------- FEEDBACK (форма) ----------------------------
FEEDBACK_LIKE, FEEDBACK_DISLIKE, FEEDBACK_IMPROVE, FEEDBACK_USE = range(4)
FEEDBACK_FILE = DATA_DIR / "feedback.json"

def save_feedback_entry(user_id: int, answers: Dict[str, str]):
    """Сохраняет отзыв в JSON."""
    entry = {
        "user_id": user_id,
        "timestamp": datetime.now().isoformat(),
        "answers": answers,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if FEEDBACK_FILE.exists():
        try:
            with FEEDBACK_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                data = []
        except Exception:
            data = []
    else:
        data = []
    data.append(entry)
    with FEEDBACK_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("Отзыв сохранён: user=%s, total=%s", user_id, len(data))

# ---------------------------- STATS / SQLITE ----------------------------
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except ValueError:
    ADMIN_ID = 0

DB_PATH = DATA_DIR / "bot.db"

def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                first_seen TIMESTAMP,
                last_seen  TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      TIMESTAMP,
                user_id INTEGER,
                type    TEXT,
                command TEXT,
                payload TEXT
            )
        """)
        conn.commit()

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def touch_user(user) -> None:
    if not user:
        return
    now = datetime.utcnow().isoformat()
    with db() as conn:
        c = conn.cursor()
        c.execute("SELECT user_id FROM users WHERE user_id=?", (user.id,))
        if c.fetchone():
            c.execute("UPDATE users SET last_seen=? WHERE user_id=?", (now, user.id))
        else:
            c.execute(
                "INSERT INTO users (user_id, username, first_seen, last_seen) VALUES (?,?,?,?)",
                (user.id, user.username or "", now, now)
            )

def log_event(user_id: int, etype: str, command: str = "", payload: str = "") -> None:
    with db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO events (ts, user_id, type, command, payload) VALUES (?,?,?,?,?)",
            (datetime.utcnow().isoformat(), user_id, etype, command, payload)
        )

def track(command_name: str):
    def deco(func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            try:
                user = update.effective_user
                if user:
                    touch_user(user)
                    payload = " ".join(context.args) if getattr(context, "args", None) else ""
                    log_event(user.id, "command", command_name, payload)
            except Exception as e:
                logger.warning("Не удалось записать статистику: %s", e)
            return await func(update, context, *args, **kwargs)
        return wrapper
    return deco

# ---------------------------- ХЭНДЛЕРЫ КОМАНД ----------------------------
@track("start")
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Привет! Я бот-справочник музеев Санкт-Петербурга.\n\n"
        "Команды:\n"
        "• /list — список музеев\n"
        "• /find <запрос> — поиск по названию\n"
        "• /museum <id|название> — карточка музея\n"
        "• /random — случайный музей\n"
        "• /weather [город] — погода (по умолчанию СПб)\n"
        "• /route <id|название> — маршрут в Яндекс.Картах\n"
        "• /plan <id|название> — погода + маршрут\n"
        "• /feedback — оставить отзыв (1 минута)\n"
        "• /stats — статистика (админ)\n"
        "• /ping — проверка\n"
        "\nПодсказка: можно просто написать часть названия (например, «Эрмитаж») — я предложу варианты."
    )
    await update.message.reply_text(text)

@track("help")
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)

@track("ping")
async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("pong 🏓")

@track("list")
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        museums = load_museums()
        await update.message.reply_text(list_museums_text(museums), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.exception("Ошибка /list: %s", e)
        await update.message.reply_text("❗ Не удалось загрузить список музеев.")

@track("find")
async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("Использование: `/find <запрос>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        museums = load_museums()
        qn = normalize(query)
        substr = [m for m in museums if qn in normalize(m["name"])]
        if substr:
            lines = [f"{m['id']}. {m['name']}" for m in substr[:20]]
        else:
            matches = fuzzy_find_museums(museums, query, limit=10)
            lines = [f"{m['id']}. {m['name']} ({score}%)" for m, score in matches] or ["Ничего не найдено."]
        await update.message.reply_text("🔎 Результаты поиска:\n" + "\n".join(lines))
    except Exception as e:
        logger.exception("Ошибка /find: %s", e)
        await update.message.reply_text("❗ Ошибка при поиске. Попробуйте ещё раз.")

@track("museum")
async def cmd_museum(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    arg = " ".join(context.args).strip()
    if not arg:
        await update.message.reply_text("Использование: `/museum <id|название>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        museums = load_museums()
        museum = None
        if arg.isdigit():
            museum = find_museum_by_id(museums, int(arg))
        if museum is None:
            matches = fuzzy_find_museums(museums, arg, limit=1)
            museum = matches[0][0] if matches else None
        if not museum:
            await update.message.reply_text("Не нашёл такой музей. Попробуйте /find <часть названия>.")
            return
        await update.message.reply_text(format_museum_card(museum), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.exception("Ошибка /museum: %s", e)
        await update.message.reply_text("❗ Не удалось показать карточку музея.")

@track("random")
async def cmd_random(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        museums = load_museums()
        museum = random.choice(museums)
        await update.message.reply_text("🎲 Случайный выбор:\n" + format_museum_card(museum), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.exception("Ошибка /random: %s", e)
        await update.message.reply_text("❗ Не получилось выбрать музей.")

@track("weather")
async def cmd_weather(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    city_arg = " ".join(context.args).strip()
    city_q = city_arg or DEFAULT_CITY
    try:
        geo = geocode_city(city_q)
        if not geo:
            await update.message.reply_text(f"Не нашёл город “{city_q}”. Попробуйте уточнить, например: “Санкт-Петербург”.")
            return
        lat, lon, tz, city_name = geo
        w = get_weather(lat, lon, tz)
        if not w:
            await update.message.reply_text("Не удалось получить погоду. Попробуйте позже.")
            return
        await update.message.reply_text(render_weather_block(city_name, w), parse_mode=ParseMode.MARKDOWN)
    except requests.RequestException:
        await update.message.reply_text("⚠️ Не удалось получить данные. Проверьте интернет и попробуйте ещё раз.")
    except Exception as e:
        logger.exception("Ошибка /weather: %s", e)
        await update.message.reply_text("❗ Произошла ошибка при обработке погоды.")

@track("route")
async def cmd_route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    arg = " ".join(context.args).strip()
    if not arg:
        await update.message.reply_text("Использование: `/route <id|название>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        museums = load_museums()
        museum = None
        if arg.isdigit():
            museum = find_museum_by_id(museums, int(arg))
        if museum is None:
            matches = fuzzy_find_museums(museums, arg, limit=1)
            museum = matches[0][0] if matches else None
        if not museum:
            suggestions = fuzzy_find_museums(museums, arg, limit=5)
            if suggestions:
                sugg_lines = [f"{m['id']}. {m['name']}" for m, _ in suggestions]
                await update.message.reply_text("Не нашёл точного совпадения. Возможно, вы имели в виду:\n" + "\n".join(sugg_lines))
            else:
                await update.message.reply_text("Не нашёл такой музей. Попробуйте /find <часть названия>.")
            return
        addr = museum.get("address") or ""
        if not addr:
            await update.message.reply_text("У музея нет адреса в данных.")
            return
        link = build_yandex_route_link(addr)
        text = (
            f"🗺 *Маршрут до «{escape_md(museum['name'])}»:*\n"
            f"Адрес: {escape_md(addr)}\n"
            f"Открыть в Яндекс.Картах → {link}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.exception("Ошибка /route: %s", e)
        await update.message.reply_text("❗ Не удалось построить маршрут.")

@track("plan")
async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    arg = " ".join(context.args).strip()
    if not arg:
        await update.message.reply_text("Использование: `/plan <id|название>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        museums = load_museums()
        museum = None
        if arg.isdigit():
            museum = find_museum_by_id(museums, int(arg))
        if museum is None:
            matches = fuzzy_find_museums(museums, arg, limit=1)
            museum = matches[0][0] if matches else None
        if not museum:
            await update.message.reply_text("Не нашёл такой музей. Попробуйте /find <часть названия>.")
            return

        geo = geocode_city(DEFAULT_CITY)
        weather_text = "Погода недоступна."
        if geo:
            lat, lon, tz, city_name = geo
            w = get_weather(lat, lon, tz)
            if w:
                weather_text = render_weather_block(city_name, w)

        addr = museum.get("address") or ""
        link = build_yandex_route_link(addr) if addr else "—"
        text = (
            f"{weather_text}\n\n"
            f"🗺 *Маршрут до «{escape_md(museum['name'])}»:*\n"
            f"Адрес: {escape_md(addr) if addr else '—'}\n"
            f"Открыть в Яндекс.Картах → {link}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.exception("Ошибка /plan: %s", e)
        await update.message.reply_text("❗ Не удалось составить план визита.")

# ----- FEEDBACK: диалог -----
@track("feedback")
async def feedback_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    intro_text = (
        "📝 *Нам важно ваше мнение!*\n\n"
        "Пожалуйста, ответьте на несколько коротких вопросов — это займёт не больше минуты.\n"
        "_В любой момент можно выйти командой /cancel._\n\n"
        "Первый вопрос:\n"
        "😊 Что вам понравилось в боте?"
    )
    await update.message.reply_text(intro_text, parse_mode=ParseMode.MARKDOWN)
    context.user_data["feedback"] = {}
    return FEEDBACK_LIKE

async def feedback_like(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["feedback"]["like"] = update.message.text
    await update.message.reply_text("🙃 А что не понравилось?")
    return FEEDBACK_DISLIKE

async def feedback_dislike(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["feedback"]["dislike"] = update.message.text
    await update.message.reply_text("💡 Что, на ваш взгляд, можно улучшить?")
    return FEEDBACK_IMPROVE

async def feedback_improve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["feedback"]["improve"] = update.message.text
    await update.message.reply_text("🧐 Хотели бы использовать такой бот в будущем?")
    return FEEDBACK_USE

async def feedback_use(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["feedback"]["use"] = update.message.text
    user_id = update.message.from_user.id
    save_feedback_entry(user_id, context.user_data["feedback"])
    thank_you_text = (
        "💌 *Спасибо за обратную связь!*\n\n"
        "Ваши ответы сохранены и помогут сделать бота лучше 🌿\n"
        "Если захотите — можно написать разработчику: @yourusername"
    )
    await update.message.reply_text(thank_you_text, parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

async def feedback_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Обратная связь отменена.")
    return ConversationHandler.END

# ----- STATS: админ-команда -----
@track("stats")
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        await update.message.reply_text("⛔️ Команда только для администратора.")
        return

    now = datetime.utcnow()
    dt7 = now - timedelta(days=7)
    dt30 = now - timedelta(days=30)

    with db() as conn:
        c = conn.cursor()
        total_users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_7  = c.execute("SELECT COUNT(*) FROM users WHERE last_seen>=?", (dt7.isoformat(),)).fetchone()[0]
        active_30 = c.execute("SELECT COUNT(*) FROM users WHERE last_seen>=?", (dt30.isoformat(),)).fetchone()[0]
        total_events_30 = c.execute("SELECT COUNT(*) FROM events WHERE ts>=?", (dt30.isoformat(),)).fetchone()[0]

        rows_cmd = c.execute("""
            SELECT command, COUNT(*) AS cnt
            FROM events
            WHERE type='command' AND ts>=?
            GROUP BY command
            ORDER BY cnt DESC
            LIMIT 15
        """, (dt30.isoformat(),)).fetchall()

        rows_museums = c.execute("""
            SELECT payload, COUNT(*) AS cnt
            FROM events
            WHERE type='command' AND command IN ('museum','route','plan') AND ts>=?
            GROUP BY payload
            ORDER BY cnt DESC
            LIMIT 5
        """, (dt30.isoformat(),)).fetchall()

        feedback_cnt = c.execute("""
            SELECT COUNT(*) FROM events
            WHERE type='command' AND command='feedback' AND ts>=?
        """, (dt30.isoformat(),)).fetchone()[0]

    if total_users == 0 and total_events_30 == 0:
        await update.message.reply_text(
            "📊 Пока нет данных для статистики. Попробуйте выполнить несколько команд и снова /stats."
        )
        return

    lines = [
        "📊 *Статистика бота (30 дней)*",
        f"👥 Пользователи: всего {total_users} · активны 7д: {active_7} · 30д: {active_30}",
        f"⚙️ Событий (30д): {total_events_30}",
        f"📝 Отзывов (30д): {feedback_cnt}",
        "",
        "🔝 Команды:",
    ]
    for cmd, cnt in rows_cmd:
        lines.append(f"• /{cmd} — {cnt}")
    if rows_museums:
        lines.append("")
        lines.append("🏛 Топ запросов к музеям:")
        for p, cnt in rows_museums:
            p_disp = p if p else "(без аргумента)"
            if len(p_disp) > 40:
                p_disp = p_disp[:37] + "…"
            lines.append(f"• {p_disp} — {cnt}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

# ----- Фолбэк: произвольный текст -----
async def handle_text_guess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if not text:
        return
    try:
        user = update.effective_user
        if user:
            try:
                touch_user(user)
                log_event(user.id, "message", "", text[:200])
            except Exception as e:
                logger.warning("Не удалось записать статистику сообщения: %s", e)

        museums = load_museums()
        matches = fuzzy_find_museums(museums, text, limit=5)
        if not matches:
            return
        lines = [f"{m['id']}. {m['name']}" for m, _ in matches]
        reply = (
            "Похоже, вы ищете музей. Подходят:\n" + "\n".join(lines) +
            "\n\nНапример:\n/museum <id>  •  /route <id>  •  /plan <id>"
        )
        await update.message.reply_text(reply)
    except Exception as e:
        logger.exception("Ошибка handle_text_guess: %s", e)

# ----- Глобальный обработчик ошибок -----
async def on_error(update, context):
    logger.exception("Exception while handling update: %s", context.error)
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text("⚠️ Произошла ошибка. Мы уже чиним.")
    except Exception:
        pass

# ---------------------------- MAIN ----------------------------
def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("Не задан BOT_TOKEN (экспортируйте переменную окружения). Пример:\n  export BOT_TOKEN=123:ABC")
        raise SystemExit(1)

    init_db()
    # гарантируем наличие data/ и пустого feedback.json (необязательно, но удобно)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fb = DATA_DIR / "feedback.json"
    if not fb.exists():
        fb.write_text("[]", encoding="utf-8")

    application: Application = ApplicationBuilder().token(token).build()

    # FEEDBACK ConversationHandler (ставим РАНЬШЕ общего текстового)
    feedback_handler = ConversationHandler(
        entry_points=[CommandHandler("feedback", feedback_start)],
        states={
            FEEDBACK_LIKE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, feedback_like)],
            FEEDBACK_DISLIKE: [MessageHandler(filters.TEXT & ~filters.COMMAND, feedback_dislike)],
            FEEDBACK_IMPROVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, feedback_improve)],
            FEEDBACK_USE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, feedback_use)],
        },
        fallbacks=[CommandHandler("cancel", feedback_cancel)],
        allow_reentry=True,
        per_message=False,
    )
    application.add_handler(feedback_handler, group=0)

    # Команды
    application.add_handler(CommandHandler("start",  cmd_start),  group=0)
    application.add_handler(CommandHandler("help",   cmd_help),   group=0)
    application.add_handler(CommandHandler("ping",   cmd_ping),   group=0)
    application.add_handler(CommandHandler("list",   cmd_list),   group=0)
    application.add_handler(CommandHandler("find",   cmd_find),   group=0)
    application.add_handler(CommandHandler("museum", cmd_museum), group=0)
    application.add_handler(CommandHandler("random", cmd_random), group=0)
    application.add_handler(CommandHandler("weather",cmd_weather),group=0)
    application.add_handler(CommandHandler("route",  cmd_route),  group=0)
    application.add_handler(CommandHandler("plan",   cmd_plan),   group=0)
    application.add_handler(CommandHandler("stats",  cmd_stats),  group=0)

    # Общий текстовый — в другой группе, чтобы не перехватывал ответы формы
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_guess), group=1)

    application.add_error_handler(on_error)

    logger.info("Бот запущен. Ожидаю обновления…")
    application.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
