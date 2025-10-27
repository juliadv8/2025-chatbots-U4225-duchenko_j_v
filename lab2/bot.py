#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram-bot: Справочник музеев Санкт-Петербурга + погода (Open-Meteo) + маршрут (Яндекс.Карты)

Требования: python-telegram-bot v21+, requests, rapidfuzz
Структура проекта:
.
├─ bot.py
├─ requirements.txt
├─ README.md
└─ data/
   └─ museums.json
"""

import json
import logging
import os
import random
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

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

# Простая таблица расшифровки кодов погоды Open-Meteo
WEATHER_CODE_MAP = {
    0: "ясно",
    1: "в основном ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь",
    51: "лёгкая морось",
    53: "умеренная морось",
    55: "сильная морось",
    61: "небольшой дождь",
    63: "дождь",
    65: "сильный дождь",
    66: "ледяной дождь",
    67: "сильный ледяной дождь",
    71: "небольшой снег",
    73: "снег",
    75: "сильный снег",
    77: "снежные зёрна",
    80: "ливневые дожди",
    81: "сильные ливни",
    82: "очень сильные ливни",
    85: "снегопад",
    86: "сильный снегопад",
    95: "гроза",
    96: "гроза с градом",
    99: "сильная гроза с градом",
}

# ---------------------------- УТИЛИТЫ ----------------------------

def escape_md(text: str) -> str:
    """Экранирует спецсимволы для MarkdownV2/Markdown (минимально для обычного Markdown)."""
    # Мы используем обычный Markdown (ParseMode.MARKDOWN), тут достаточно не переусердствовать
    return text.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")

def load_museums() -> List[Dict[str, Any]]:
    """Читает json со списком музеев."""
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
    # Формат: https://yandex.ru/maps/?rtext=~<URL-encoded address>
    return f"https://yandex.ru/maps/?rtext=~{quote_plus(address)}"

def normalize(s: str) -> str:
    return " ".join((s or "").strip().lower().split())

def find_museum_by_id(museums: List[Dict[str, Any]], mid: int) -> Optional[Dict[str, Any]]:
    for m in museums:
        if int(m.get("id")) == mid:
            return m
    return None

def fuzzy_find_museums(museums: List[Dict[str, Any]], query: str, limit: int = 5) -> List[Tuple[Dict[str, Any], int]]:
    """Возвращает до limit лучших совпадений по названию (скор + объект)."""
    choices = {m["name"]: m for m in museums}
    results = process.extract(query, choices.keys(), scorer=fuzz.WRatio, limit=limit)
    # results -> [(name, score, idx)], нам нужен объект и score
    return [ (choices[name], int(score)) for name, score, _ in results if score >= 50 ]

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
    """
    Возвращает (lat, lon, tz, resolved_name) или None.
    Кэшируется lru_cache.
    """
    url = OPEN_METEO_GEOCODE.format(name=quote_plus(name))
    r = http.get(url)
    r.raise_for_status()
    data = r.json()
    results = data.get("results") or []
    if not results:
        return None
    top = results[0]
    lat = float(top["latitude"])
    lon = float(top["longitude"])
    tz = top.get("timezone") or "Europe/Moscow"
    resolved = top.get("name") or name
    return lat, lon, tz, resolved

@lru_cache(maxsize=128)
def get_weather(lat: float, lon: float, tz: str) -> Optional[Dict[str, Any]]:
    """
    Возвращает словарь с текущей температурой и описанием погоды.
    Кэшируется lru_cache.
    """
    url = OPEN_METEO_FORECAST.format(lat=lat, lon=lon, tz=quote_plus(tz))
    r = http.get(url)
    r.raise_for_status()
    data = r.json()
    current = data.get("current") or {}
    temp = current.get("temperature_2m")
    code = current.get("weather_code")
    desc = WEATHER_CODE_MAP.get(code, "погода неизвестна")
    return {"temperature": temp, "code": code, "description": desc}

def render_weather_block(city_display: str, weather: Dict[str, Any]) -> str:
    t = weather.get("temperature")
    d = weather.get("description", "—")
    if t is None:
        return f"🌦 Погода в {escape_md(city_display)}: данные недоступны"
    sign = "+" if t >= 0 else "−"
    t_abs = abs(float(t))
    return (
        f"🌦 *Погода в {escape_md(city_display)}:*\n"
        f"Сейчас: {sign}{t_abs:.1f} °C, {escape_md(d)}"
    )

# ---------------------------- ХЭНДЛЕРЫ ----------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Привет! Я бот-справочник музеев Санкт-Петербурга.\n\n"
        "Доступные команды:\n"
        "• /help — справка\n"
        "• /list — список всех музеев\n"
        "• /find <запрос> — поиск по названию\n"
        "• /museum <id|название> — карточка музея\n"
        "• /random — случайный музей\n"
        "• /weather [город] — погода (по умолчанию СПб)\n"
        "• /route <id|название> — маршрут в Яндекс.Картах\n"
        "• /plan <id|название> — погода + маршрут\n"
        "• /ping — проверка\n"
    )
    await update.message.reply_text(text)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("pong 🏓")

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        museums = load_museums()
        await update.message.reply_text(
            list_museums_text(museums), parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.exception("Ошибка /list: %s", e)
        await update.message.reply_text("❗ Не удалось загрузить список музеев.")

async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("Использование: `/find <запрос>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        museums = load_museums()
        # быстрый подстрочный фильтр
        qn = normalize(query)
        substr = [m for m in museums if qn in normalize(m["name"])]
        if substr:
            lines = [f"{m['id']}. {m['name']}" for m in substr[:20]]
        else:
            # fuzzy
            matches = fuzzy_find_museums(museums, query, limit=10)
            lines = [f"{m['id']}. {m['name']} ({score}%)" for m, score in matches] or ["Ничего не найдено."]
        await update.message.reply_text("🔎 Результаты поиска:\n" + "\n".join(lines))
    except Exception as e:
        logger.exception("Ошибка /find: %s", e)
        await update.message.reply_text("❗ Ошибка при поиске. Попробуйте ещё раз.")

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
            # fuzzy по названию
            matches = fuzzy_find_museums(museums, arg, limit=1)
            museum = matches[0][0] if matches else None
        if not museum:
            await update.message.reply_text("Не нашёл такой музей. Попробуйте /find <часть названия>.")
            return
        await update.message.reply_text(
            format_museum_card(museum), parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.exception("Ошибка /museum: %s", e)
        await update.message.reply_text("❗ Не удалось показать карточку музея.")

async def cmd_random(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        museums = load_museums()
        museum = random.choice(museums)
        await update.message.reply_text(
            "🎲 Случайный выбор:\n" + format_museum_card(museum),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.exception("Ошибка /random: %s", e)
        await update.message.reply_text("❗ Не получилось выбрать музей.")

async def cmd_weather(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    city_arg = " ".join(context.args).strip()
    city_q = city_arg or DEFAULT_CITY
    try:
        geo = geocode_city(city_q)
        if not geo:
            await update.message.reply_text(
                f"Не нашёл город “{city_q}”. Попробуйте уточнить, например: “Санкт-Петербург”."
            )
            return
        lat, lon, tz, city_name = geo
        w = get_weather(lat, lon, tz)
        if not w:
            await update.message.reply_text("Не удалось получить погоду. Попробуйте позже.")
            return
        await update.message.reply_text(
            render_weather_block(city_name, w), parse_mode=ParseMode.MARKDOWN
        )
    except requests.RequestException:
        await update.message.reply_text(
            "⚠️ Не удалось получить данные. Проверьте интернет и попробуйте ещё раз."
        )
    except Exception as e:
        logger.exception("Ошибка /weather: %s", e)
        await update.message.reply_text("❗ Произошла ошибка при обработке погоды.")

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
            # Предложим варианты
            suggestions = fuzzy_find_museums(museums, arg, limit=5)
            if suggestions:
                sugg_lines = [f"{m['id']}. {m['name']}" for m, _ in suggestions]
                await update.message.reply_text(
                    "Не нашёл точного совпадения. Возможно, вы имели в виду:\n" + "\n".join(sugg_lines)
                )
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

async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    arg = " ".join(context.args).strip()
    if not arg:
        await update.message.reply_text("Использование: `/plan <id|название>`", parse_mode=ParseMode.MARKDOWN)
        return
    # Логика: найдём музей, достанем адрес, получим погоду для СПб (или можно было бы парсить город из аргумента)
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

        # Погода по СПб по умолчанию
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

async def handle_text_guess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """На произвольный текст попытаемся угадать музей и предложить команды."""
    text = (update.message.text or "").strip()
    if not text:
        return
    try:
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

# ---------------------------- MAIN ----------------------------

def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("Не задан BOT_TOKEN (экспортируйте переменную окружения). Пример:\n  export BOT_TOKEN=123:ABC")
        raise SystemExit(1)

    application: Application = ApplicationBuilder().token(token).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("ping", cmd_ping))
    application.add_handler(CommandHandler("list", cmd_list))
    application.add_handler(CommandHandler("find", cmd_find))
    application.add_handler(CommandHandler("museum", cmd_museum))
    application.add_handler(CommandHandler("random", cmd_random))
    application.add_handler(CommandHandler("weather", cmd_weather))
    application.add_handler(CommandHandler("route", cmd_route))
    application.add_handler(CommandHandler("plan", cmd_plan))

    # Любой текст без команды — попробуем угадать
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_guess))

    logger.info("Бот запущен. Ожидаю обновления…")
    application.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
