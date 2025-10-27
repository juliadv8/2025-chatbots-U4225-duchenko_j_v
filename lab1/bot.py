#!/usr/bin/env python3
"""
Telegram bot: Saint Petersburg Museums directory
Library: python-telegram-bot (v21+, asyncio)
Author: ChatGPT
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from rapidfuzz import process, fuzz

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

# --------------------- Configuration & Logging ---------------------
load_dotenv()  # loads .env if present

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "").strip()  # optional

# Paths
BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "data" / "museums.json"

# Logging config
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.DEBUG,
)
logger = logging.getLogger("spb-museums-bot")


# --------------------- Data Model ---------------------
@dataclass
class Museum:
    id: str
    name: str
    hours: str
    address: str
    tickets: str
    url: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Dict) -> "Museum":
        return cls(
            id=str(d.get("id", "")),
            name=d.get("name", ""),
            hours=d.get("hours", ""),
            address=d.get("address", ""),
            tickets=d.get("tickets", ""),
            url=d.get("url"),
        )

    def to_markdown(self) -> str:
        parts = [
            f"*{escape_md(self.name)}*",
            f"🕒 *График:* {escape_md(self.hours)}",
            f"📍 *Адрес:* {escape_md(self.address)}",
            f"🎟️ *Билеты:* {escape_md(self.tickets)}",
        ]
        if self.url:
            parts.append(f"🌐 *Сайт:* {escape_md(self.url)}")
        return "\n".join(parts)


def escape_md(text: str) -> str:
    """Escape characters for Telegram MarkdownV2."""
    # Minimal escaping for *_[]()~`>#+-=|{}.! per MarkdownV2 rules
    if text is None:
        return ""
    esc = ""
    for ch in text:
        if ch in r"_*[]()~`>#+-=|{}.!":
            esc += "\\" + ch
        else:
            esc += ch
    return esc


class MuseumStore:
    """JSON-backed store with simple in-memory cache + fuzzy search."""
    def __init__(self, data_path: Path):
        self.data_path = data_path
        self._items: Dict[str, Museum] = {}

    def load(self) -> None:
        if not self.data_path.exists():
            logger.warning("Data file not found. Creating default dataset at %s", self.data_path)
            self._bootstrap_defaults()
        with self.data_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        self._items = {str(d["id"]): Museum.from_dict(d) for d in data}

    def save(self) -> None:
        data = [vars(m) for m in self._items.values()]
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        with self.data_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def all(self) -> List[Museum]:
        return list(self._items.values())

    def get_by_id(self, mid: str) -> Optional[Museum]:
        return self._items.get(str(mid))

    def find(self, query: str, limit: int = 5) -> List[Museum]:
        if not query:
            return []
        names_map = {m.name: m for m in self._items.values()}
        # RapidFuzz returns list of tuples (choice, score, index)
        matches = process.extract(
            query,
            names_map.keys(),
            scorer=fuzz.WRatio,
            limit=limit,
        )
        # Filter weak matches
        result = []
        for name, score, _ in matches:
            if score >= 60:  # threshold
                result.append(names_map[name])
        # Also add substring matches (case-insensitive) not already included
        q = query.lower()
        for m in self._items.values():
            if q in m.name.lower() and m not in result:
                result.append(m)
        return result[:limit]

    def _bootstrap_defaults(self) -> None:
        default_data = [
            {
                "id": "1",
                "name": "Государственный Эрмитаж",
                "hours": "Вт, Чт, Сб, Вс 10:30–18:00; Ср, Пт 10:30–21:00; Пн — выходной",
                "address": "Дворцовая пл., 2",
                "tickets": "От 500 ₽ (льготы доступны); бесплатно для детей до 18",
                "url": "https://www.hermitagemuseum.org/",
            },
            {
                "id": "2",
                "name": "Русский музей (Михайловский дворец)",
                "hours": "Пн, Ср, Пт, Сб, Вс 10:00–18:00; Чт 13:00–21:00; Вторник — выходной",
                "address": "Инженерная ул., 4",
                "tickets": "От 500 ₽; комбинированные билеты доступны",
                "url": "https://rusmuseum.ru/",
            },
            {
                "id": "3",
                "name": "Музей антропологии и этнографии (Кунсткамера)",
                "hours": "Ср–Вс 11:00–19:00; Пн, Вт — выходной",
                "address": "Университетская наб., 3",
                "tickets": "Взрослый 400–500 ₽; льготы",
                "url": "https://www.kunstkamera.ru/",
            },
            {
                "id": "4",
                "name": "Музей Фаберже",
                "hours": "Ежедневно 10:00–21:00",
                "address": "Наб. реки Фонтанки, 21",
                "tickets": "Взрослый от 650 ₽; аудиогид отдельно",
                "url": "https://fabergemuseum.ru/",
            },
            {
                "id": "5",
                "name": "Эрарта Музей Современного Искусства",
                "hours": "Пн–Вс 10:00–22:00 (Ср — выходной)",
                "address": "29-я линия В.О., 2",
                "tickets": "Взрослый от 1200 ₽; льготы",
                "url": "https://www.erarta.com/",
            },
            {
                "id": "6",
                "name": "Музей истории Санкт-Петербурга (Петропавловская крепость)",
                "hours": "Ежедневно 10:00–18:00; Ср до 17:00 (разделы музея могут иметь свой режим)",
                "address": "Петропавловская крепость, 3",
                "tickets": "Комплексные билеты от 750 ₽",
                "url": "https://spbmuseum.ru/",
            },
            {
                "id": "7",
                "name": "Центральный военно-морской музей",
                "hours": "Ср–Вс 11:00–19:00; Пн, Вт — выходной",
                "address": "Площадь Труда, 5",
                "tickets": "Взрослый 500 ₽; льготы",
                "url": "https://navalmuseum.ru/",
            },
        ]
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        with self.data_path.open("w", encoding="utf-8") as f:
            json.dump(default_data, f, ensure_ascii=False, indent=2)
        logger.info("Default dataset created with %d items", len(default_data))


store = MuseumStore(DATA_PATH)


# --------------------- Bot Behavior ---------------------
HELP_TEXT = (
    "Я — справочник по музеям Санкт-Петербурга.\n\n"
    "Доступные команды:\n"
    "/start — приветствие и краткая справка\n"
    "/help — помощь и список команд\n"
    "/list — показать все музеи (id и названия)\n"
    "/find <запрос> — поиск по названию (фаззи + подстрока)\n"
    "/museum <id|название> — подробная карточка музея\n"
    "/random — случайный музей\n"
    "/ping — проверка доступности\n"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! 👋\n" + HELP_TEXT,
        disable_web_page_preview=True,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, disable_web_page_preview=True)


def format_list(items: List[Museum]) -> str:
    lines = []
    for m in items:
        lines.append(f"{m.id}. {m.name}")
    return "Список музеев:\n" + "\n".join(lines) if lines else "Пока список пуст."


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = store.all()
    text = format_list(items)
    await update.message.reply_text(text)


async def find_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text("Укажите запрос: например, `/find эрмитаж`", parse_mode=ParseMode.MARKDOWN_V2)
        return
    matches = store.find(query, limit=7)
    if not matches:
        await update.message.reply_text("Ничего не нашёл. Попробуйте иначе сформулировать запрос.")
        return
    text = "Нашёл:\n" + "\n".join(f"{m.id}. {m.name}" for m in matches)
    await update.message.reply_text(text)


def resolve_museum(arg: str) -> Optional[Museum]:
    if not arg:
        return None
    # Try by id first
    m = store.get_by_id(arg)
    if m:
        return m
    # Fuzzy by name
    matches = store.find(arg, limit=1)
    return matches[0] if matches else None


async def museum_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    arg = " ".join(context.args) if context.args else ""
    if not arg:
        await update.message.reply_text("Укажите id или название: `/museum 1` или `/museum эрмитаж`", parse_mode=ParseMode.MARKDOWN_V2)
        return
    m = resolve_museum(arg)
    if not m:
        await update.message.reply_text("Музей не найден. Проверьте ввод или используйте /list и /find.")
        return
    await update.message.reply_text(m.to_markdown(), parse_mode=ParseMode.MARKDOWN_V2, disable_web_page_preview=True)


async def random_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    import random
    items = store.all()
    if not items:
        await update.message.reply_text("Справочник пуст.")
        return
    m = random.choice(items)
    await update.message.reply_text(m.to_markdown(), parse_mode=ParseMode.MARKDOWN_V2, disable_web_page_preview=True)


async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("pong ✅")


async def fallback_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Graceful answer for free-form text: try to search by name."""
    text = (update.message.text or "").strip()
    if not text:
        return
    # Heuristic: if user typed a short phrase, attempt search
    matches = store.find(text, limit=3)
    if matches:
        ans = "Возможно, вы искали:\n" + "\n".join(f"{m.id}. {m.name}" for m in matches)
        ans += "\n\nИспользуйте `/museum <id>` для подробностей."
        await update.message.reply_text(ans)
    else:
        await update.message.reply_text("Я понимаю команды /list, /find, /museum и др. Напишите /help для справки.")


# --------------------- Error handling ---------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)
    # Notify user if possible
    try:
        if isinstance(update, Update) and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Ой! Что-то пошло не так 😿 Попробуйте позже или команду /help.",
            )
    except Exception:
        pass
    # Optionally notify admin
    if ADMIN_USER_ID:
        try:
            await context.bot.send_message(
                chat_id=int(ADMIN_USER_ID),
                text=f"⚠️ Bot error: {context.error}",
            )
        except Exception:
            pass


# --------------------- App bootstrap ---------------------

async def _post_init(app: Application) -> None:
    logger.info("Bot started")


def ensure_data_loaded() -> None:
    try:
        store.load()
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in %s: %s. Recreating with defaults.", DATA_PATH, e)
        # Backup broken file and recreate
        try:
            broken = DATA_PATH.with_suffix(".broken.json")
            DATA_PATH.replace(broken)
            logger.warning("Backed up invalid file to %s", broken)
        except Exception:
            logger.exception("Failed to backup invalid data file")
        store._bootstrap_defaults()
        store.load()


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. See .env.example")

    ensure_data_loaded()

    application: Application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("list", list_cmd))
    application.add_handler(CommandHandler("find", find_cmd))
    application.add_handler(CommandHandler("museum", museum_cmd))
    application.add_handler(CommandHandler("random", random_cmd))
    application.add_handler(CommandHandler("ping", ping_cmd))

    # Fallback text handler (simple search)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_text))

    # Errors
    application.add_error_handler(error_handler)

    # Start
    application.run_polling(stop_signals=None)  # handle signals internally


if __name__ == "__main__":
    main()
