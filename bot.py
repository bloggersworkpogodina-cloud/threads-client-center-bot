import asyncio
import logging
import os
import secrets
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

import db

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")
DAILY_SEND_HOUR = int(os.getenv("DAILY_SEND_HOUR", "9"))
RESULTS_POLL_HOUR = int(os.getenv("RESULTS_POLL_HOUR", "19"))
WORK_GROUP_ID = int(os.getenv("WORK_GROUP_ID", "0"))
TZ = ZoneInfo(TIMEZONE)

logging.basicConfig(level=logging.INFO)
router = Router()


def admin_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Внести аналитику"), KeyboardButton(text="📝 Ветки")],
            [KeyboardButton(text="👥 Клиенты"), KeyboardButton(text="📈 Отчёты")],
        ],
        resize_keyboard=True,
    )


def client_menu():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📅 Ветки на сегодня")]],
        resize_keyboard=True,
    )


def clients_keyboard(clients, prefix):
    rows = [
        [InlineKeyboardButton(text=c["name"], callback_data=f"{prefix}:{c['id']}")]
        for c in clients
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def yes_no_keyboard(prefix):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data=f"{prefix}:yes"),
                InlineKeyboardButton(text="Нет", callback_data=f"{prefix}:no"),
            ]
        ]
    )



async def ensure_client_topic(bot: Bot, client_id: int):
    client = await db.get_client(client_id)
    if not client or not WORK_GROUP_ID:
        return None
    if client["topic_id"]:
        return client["topic_id"]

    topic = await bot.create_forum_topic(
        chat_id=WORK_GROUP_ID,
        name=client["name"][:128],
    )
    await db.set_client_topic(client_id, topic.message_thread_id)
    await bot.send_message(
        WORK_GROUP_ID,
        f"<b>Карточка клиента создана</b>\n"
        f"Threads: @{client['threads_username'] or '—'}\n"
        f"Telegram: {client['telegram_link'] or '—'}",
        message_thread_id=topic.message_thread_id,
    )
    return topic.message_thread_id


async def send_to_client_topic(bot: Bot, client_id: int, text: str):
    if not WORK_GROUP_ID:
        return
    topic_id = await ensure_client_topic(bot, client_id)
    if topic_id:
        await bot.send_message(
            WORK_GROUP_ID,
            text,
            message_thread_id=topic_id,
        )


class AddClient(StatesGroup):
    name = State()
    threads = State()
    tg_link = State()


class AddPosts(StatesGroup):
    client = State()
    post_date = State()
    payload = State()


class AnalyticsFlow(StatesGroup):
    client = State()
    analytics_date = State()
    slot = State()
    views = State()
    likes = State()
    comments = State()
    reposts = State()
    followers = State()


class ResultFlow(StatesGroup):
    transitions = State()
    inquiries = State()
    sales = State()
    revenue = State()


async def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if await is_admin(message.from_user.id):
        await state.clear()
        await message.answer("Админ-центр Threads готов.", reply_markup=admin_menu())
        return

    client = await db.get_client_by_tg(message.from_user.id)
    if client:
        await message.answer(
            "<b>Добро пожаловать в личный кабинет 👋</b>\n\n"
            "Здесь проходит наша работа с контентом в Threads.\n\n"
            "Каждый день вы будете получать готовые ветки для публикации.\n\n"
            "Раз в 2 дня бот попросит вас коротко отметить результаты продвижения: переходы в Telegram, обращения и продажи.\n\n"
            "Это поможет нам отслеживать не только охваты, но и реальный результат работы.\n\n"
            "<b>Личный кабинет подключён ✅</b>",
            reply_markup=client_menu(),
        )
        return

    if len(args) == 2 and args[1].startswith("invite_"):
        code = args[1].replace("invite_", "", 1)
        bound = await db.bind_client(code, message.from_user.id)
        if bound:
            client = await db.get_client_by_tg(message.from_user.id)
            await message.answer(
                "<b>Добро пожаловать в личный кабинет 👋</b>\n\n"
                "Здесь проходит наша работа с контентом в Threads.\n\n"
                "Каждый день вы будете получать готовые ветки для публикации.\n\n"
                "Раз в 2 дня бот попросит вас коротко отметить результаты продвижения: переходы в Telegram, обращения и продажи.\n\n"
                "Это поможет нам отслеживать не только охваты, но и реальный результат работы.\n\n"
                "<b>Личный кабинет подключён ✅</b>",
                reply_markup=client_menu(),
            )
            return

    await message.answer("Эта ссылка недействительна или уже использована.")



@router.message(Command("chatid"))
async def chatid(message: Message):
    if message.chat.type in {"group", "supergroup"}:
        await message.answer(f"Chat ID: <code>{message.chat.id}</code>")
    else:
        await message.answer("Эту команду нужно отправить в рабочей группе.")


@router.message(Command("menu"))
async def menu(message: Message):
    if await is_admin(message.from_user.id):
        await message.answer("Главное меню", reply_markup=admin_menu())
    else:
        client = await db.get_client_by_tg(message.from_user.id)
        if client:
            await message.answer("Меню клиента", reply_markup=client_menu())


@router.message(F.text == "👥 Клиенты")
async def clients(message: Message):
    if not await is_admin(message.from_user.id):
        return
    rows = await db.list_clients()
    buttons = [
        [InlineKeyboardButton(text="➕ Добавить клиента", callback_data="client_add")]
    ]
    for c in rows:
        buttons.append([
            InlineKeyboardButton(
                text=c["name"],
                callback_data=f"client_card:{c['id']}"
            )
        ])
    await message.answer(
        "Клиенты:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("client_open:"))
async def open_client_card_fixed(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    client_id = int(callback.data.split(":")[1])
    client = await db.get_client(client_id)
    if not client:
        await callback.answer("Клиент не найден", show_alert=True)
        return
    text = (
        f"<b>{client['name']}</b>\n"
        f"Threads: @{client['threads_username'] or '—'}\n"
        f"Telegram: {client['telegram_link'] or '—'}\n"
        f"Статус: {'подключён' if client['telegram_user_id'] else 'не подключён'}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💬 Создать тему", callback_data=f"client_topic:{client_id}")
    ]])
    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data.startswith("client_card:"))
async def client_card(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    client_id = int(callback.data.split(":")[1])
    c = await db.get_client(client_id)
    if not c:
        await callback.answer("Клиент не найден", show_alert=True)
        return
    status = "подключён" if c["telegram_id"] else "не подключён"
    text = (
        f"<b>{c['name']}</b>\n"
        f"Threads: @{c['threads_username'] or '—'}\n"
        f"Telegram: {c['telegram_link'] or '—'}\n"
        f"Статус: {status}"
    )
    topic_text = "💬 Тема уже создана" if c["topic_id"] else "💬 Создать тему"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=topic_text, callback_data=f"client_topic:{client_id}")
    ]])
    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "client_add")
async def client_add(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.set_state(AddClient.name)
    await callback.message.answer("Введите имя клиента:")
    await callback.answer()


@router.message(AddClient.name)
async def client_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AddClient.threads)
    await message.answer("Введите username Threads без @ или отправьте «-»:")


@router.message(AddClient.threads)
async def client_threads(message: Message, state: FSMContext):
    value = None if message.text.strip() == "-" else message.text.strip().lstrip("@")
    await state.update_data(threads=value)
    await state.set_state(AddClient.tg_link)
    await message.answer("Введите ссылку на Telegram клиента или отправьте «-»:")


@router.message(AddClient.tg_link)
async def client_tg_link(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    link = None if message.text.strip() == "-" else message.text.strip()
    code = secrets.token_urlsafe(8)
    client_id = await db.add_client(data["name"], code, data.get("threads"), link)
    if WORK_GROUP_ID:
        await ensure_client_topic(bot, client_id)
    me = await bot.get_me()
    invite = f"https://t.me/{me.username}?start=invite_{code}"
    await state.clear()
    await message.answer(
        f"Клиент добавлен.\n\nСсылка для подключения:\n{invite}",
        reply_markup=admin_menu(),
    )


@router.callback_query(F.data.startswith("client_view:"))
async def client_view(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    client_id = int(callback.data.split(":")[1])
    c = await db.get_client(client_id)
    status = "подключён" if c["telegram_id"] else "не подключён"
    text = (
        f"<b>{c['name']}</b>\n"
        f"Threads: @{c['threads_username'] or '—'}\n"
        f"Telegram: {c['telegram_link'] or '—'}\n"
        f"Статус: {status}"
    )
    await callback.message.answer(text)
    await callback.answer()


@router.callback_query(F.data.startswith("client_topic:"))
async def create_existing_client_topic(callback: CallbackQuery, bot: Bot):
    if not await is_admin(callback.from_user.id):
        return
    client_id = int(callback.data.split(":")[1])
    client = await db.get_client(client_id)
    if not client:
        await callback.answer("Клиент не найден", show_alert=True)
        return
    if client["topic_id"]:
        await callback.answer("Тема уже создана ✅", show_alert=True)
        return
    if not WORK_GROUP_ID:
        await callback.answer("Не указан WORK_GROUP_ID", show_alert=True)
        return
    try:
        await ensure_client_topic(bot, client_id)
        await callback.message.answer(f"Тема для клиента <b>{client['name']}</b> создана ✅")
        await callback.answer("Готово ✅")
    except Exception:
        logging.exception("Topic creation error")
        await callback.answer("Ошибка создания темы. Проверьте права бота.", show_alert=True)


@router.message(F.text == "📝 Ветки")
async def posts_menu(message: Message):
    if not await is_admin(message.from_user.id):
        return
    rows = await db.list_clients()
    if not rows:
        await message.answer("Сначала добавьте клиента.")
        return
    await message.answer(
        "Выберите клиента:",
        reply_markup=clients_keyboard(rows, "posts_client"),
    )


@router.callback_query(F.data.startswith("posts_client:"))
async def posts_client(callback: CallbackQuery, state: FSMContext):
    client_id = int(callback.data.split(":")[1])
    await state.update_data(client_id=client_id)
    await state.set_state(AddPosts.post_date)
    await callback.message.answer(
        "Введите дату в формате ДД.ММ.ГГГГ или отправьте «сегодня»:"
    )
    await callback.answer()


def parse_date(text: str) -> str:
    if text.strip().lower() == "сегодня":
        return date.today().isoformat()
    return datetime.strptime(text.strip(), "%d.%m.%Y").date().isoformat()


@router.message(AddPosts.post_date)
async def posts_date(message: Message, state: FSMContext):
    try:
        post_date = parse_date(message.text)
    except ValueError:
        await message.answer("Не поняла дату. Формат: 13.07.2026")
        return
    await state.update_data(post_date=post_date)
    await state.set_state(AddPosts.payload)
    await message.answer(
        "Пришлите все ветки одним сообщением.\n\n"
        "Пример:\n"
        "09:00\nТекст первой ветки\n\n"
        "12:00\nТекст второй ветки\n\n"
        "15:00\nТекст третьей ветки\n\n"
        "18:00\nТекст четвёртой ветки"
    )


def split_posts(payload: str):
    import re
    matches = list(re.finditer(r"(?m)^(?:([01]\d|2[0-3]):[0-5]\d)\s*$", payload))
    result = []
    for i, match in enumerate(matches):
        slot = match.group(0).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(payload)
        body = payload[start:end].strip()
        if body:
            result.append((slot, body))
    return result


@router.message(AddPosts.payload)
async def posts_payload(message: Message, state: FSMContext):
    items = split_posts(message.text)
    if not items:
        await message.answer("Не нашла времена. Каждое время должно быть на отдельной строке.")
        return
    data = await state.get_data()
    for slot, body in items:
        await db.save_post(data["client_id"], data["post_date"], slot, body)
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="📤 Отправить клиенту сейчас",
                callback_data=f"send_posts:{data['client_id']}:{data['post_date']}"
            )]
        ]
    )
    await message.answer(f"Сохранено веток: {len(items)}", reply_markup=kb)


async def send_posts_to_client(bot: Bot, client_id: int, post_date: str):
    client = await db.get_client(client_id)
    if not client or not client["telegram_id"]:
        return False, "Клиент ещё не подключён к боту."
    posts = await db.get_posts(client_id, post_date)
    if not posts:
        return False, "На эту дату веток нет."
    human_date = datetime.fromisoformat(post_date).strftime("%d.%m.%Y")
    await bot.send_message(client["telegram_id"], f"<b>Ветки на {human_date}</b>")
    for p in posts:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text="✅ Опубликовано",
                    callback_data=f"published:{p['id']}"
                )
            ]]
        )
        await bot.send_message(
            client["telegram_id"],
            f"<b>{p['slot']}</b>\n\n{p['body']}",
            reply_markup=kb,
        )
    await db.mark_posts_sent(client_id, post_date)
    await send_to_client_topic(
        bot,
        client_id,
        f"📤 <b>Ветки на {human_date} отправлены клиенту</b>\n"
        f"Количество: {len(posts)}",
    )
    return True, "Ветки отправлены."


@router.callback_query(F.data.startswith("send_posts:"))
async def send_posts(callback: CallbackQuery, bot: Bot):
    _, client_id, post_date = callback.data.split(":")
    ok, text = await send_posts_to_client(bot, int(client_id), post_date)
    await callback.message.answer(text)
    await callback.answer()


@router.callback_query(F.data.startswith("published:"))
async def published(callback: CallbackQuery, bot: Bot):
    post_id = int(callback.data.split(":")[1])
    await db.mark_published(post_id)
    client = await db.get_client_by_tg(callback.from_user.id)
    if client:
        await send_to_client_topic(
            bot,
            client["id"],
            "✅ Клиент отметил ветку как опубликованную.",
        )
    await callback.answer("Отметила как опубликованную ✅")


@router.message(F.text == "📅 Ветки на сегодня")
async def client_today_posts(message: Message, bot: Bot):
    client = await db.get_client_by_tg(message.from_user.id)
    if not client:
        return
    ok, text = await send_posts_to_client(bot, client["id"], date.today().isoformat())
    if not ok:
        await message.answer(text)


@router.message(F.text == "📊 Внести аналитику")
async def analytics_start(message: Message):
    if not await is_admin(message.from_user.id):
        return
    rows = await db.list_clients()
    if not rows:
        await message.answer("Сначала добавьте клиента.")
        return
    await message.answer("Выберите клиента:", reply_markup=clients_keyboard(rows, "analytics_client"))


@router.callback_query(F.data.startswith("analytics_client:"))
async def analytics_client(callback: CallbackQuery, state: FSMContext):
    client_id = int(callback.data.split(":")[1])
    await state.update_data(client_id=client_id)
    await state.set_state(AnalyticsFlow.analytics_date)
    await callback.message.answer("Введите дату или «сегодня»:")
    await callback.answer()


@router.message(AnalyticsFlow.analytics_date)
async def analytics_date(message: Message, state: FSMContext):
    try:
        d = parse_date(message.text)
    except ValueError:
        await message.answer("Формат даты: 13.07.2026")
        return
    data = await state.get_data()
    posts = await db.get_posts(data["client_id"], d)
    if not posts:
        await message.answer("На эту дату у клиента нет сохранённых веток.")
        await state.clear()
        return
    await state.update_data(
        analytics_date=d,
        post_ids=[p["id"] for p in posts],
        slots=[p["slot"] for p in posts],
        post_index=0,
        current_values={}
    )
    await ask_next_slot(message, state, posts[0])


async def ask_next_slot(message: Message, state: FSMContext, post):
    await state.set_state(AnalyticsFlow.views)
    preview = post["body"][:120] + ("…" if len(post["body"]) > 120 else "")
    await message.answer(
        f"<b>Ветка {post['slot']}</b>\n{preview}\n\n"
        "Введите просмотры или «-»:"
    )


def parse_optional_int(text: str):
    if text.strip() == "-":
        return None
    return int(text.strip())


@router.message(AnalyticsFlow.views)
async def analytics_views(message: Message, state: FSMContext):
    try:
        value = parse_optional_int(message.text)
    except ValueError:
        await message.answer("Введите число или «-».")
        return
    await state.update_data(current_values={"views": value})
    await state.set_state(AnalyticsFlow.likes)
    await message.answer("Лайки или «-»:")


@router.message(AnalyticsFlow.likes)
async def analytics_likes(message: Message, state: FSMContext):
    try:
        value = parse_optional_int(message.text)
    except ValueError:
        await message.answer("Введите число или «-».")
        return
    data = await state.get_data()
    values = data["current_values"]
    values["likes"] = value
    await state.update_data(current_values=values)
    await state.set_state(AnalyticsFlow.comments)
    await message.answer("Комментарии или «-»:")


@router.message(AnalyticsFlow.comments)
async def analytics_comments(message: Message, state: FSMContext):
    try:
        value = parse_optional_int(message.text)
    except ValueError:
        await message.answer("Введите число или «-».")
        return
    data = await state.get_data()
    values = data["current_values"]
    values["comments"] = value
    await state.update_data(current_values=values)
    await state.set_state(AnalyticsFlow.reposts)
    await message.answer("Репосты или «-»:")


@router.message(AnalyticsFlow.reposts)
async def analytics_reposts(message: Message, state: FSMContext):
    try:
        value = parse_optional_int(message.text)
    except ValueError:
        await message.answer("Введите число или «-».")
        return
    data = await state.get_data()
    values = data["current_values"]
    values["reposts"] = value
    await state.update_data(current_values=values)
    await state.set_state(AnalyticsFlow.followers)
    await message.answer("Изменение подписчиков за день или «-»:")


@router.message(AnalyticsFlow.followers)
async def analytics_followers(message: Message, state: FSMContext):
    try:
        followers = parse_optional_int(message.text)
    except ValueError:
        await message.answer("Введите число, например 12 или -3, либо «-».")
        return

    data = await state.get_data()
    idx = data["post_index"]
    slot = data["slots"][idx]
    values = data["current_values"]

    await db.save_analytics(
        data["client_id"],
        data["analytics_date"],
        slot,
        values.get("views"),
        values.get("likes"),
        values.get("comments"),
        values.get("reposts"),
        followers if idx == 0 else None,
    )

    next_idx = idx + 1
    if next_idx >= len(data["slots"]):
        await state.clear()
        await message.answer("Аналитика сохранена ✅", reply_markup=admin_menu())
        return

    posts = await db.get_posts(data["client_id"], data["analytics_date"])
    await state.update_data(post_index=next_idx, current_values={})
    await ask_next_slot(message, state, posts[next_idx])


@router.message(F.text == "📈 Отчёты")
async def reports(message: Message):
    if not await is_admin(message.from_user.id):
        return
    rows = await db.list_clients()
    if not rows:
        await message.answer("Клиентов пока нет.")
        return
    await message.answer("Выберите клиента:", reply_markup=clients_keyboard(rows, "report_client"))


@router.callback_query(F.data.startswith("report_client:"))
async def report_client(callback: CallbackQuery):
    client_id = int(callback.data.split(":")[1])
    today = date.today()
    start = today - timedelta(days=6)
    total_views = total_likes = total_comments = total_reposts = followers = 0
    best = None
    for i in range(7):
        d = (start + timedelta(days=i)).isoformat()
        rows = await db.get_day_analytics(client_id, d)
        for r in rows:
            total_views += r["views"] or 0
            total_likes += r["likes"] or 0
            total_comments += r["comments"] or 0
            total_reposts += r["reposts"] or 0
            followers += r["followers_delta"] or 0
            if r["views"] is not None and (best is None or r["views"] > best["views"]):
                best = {"date": d, "slot": r["slot"], "views": r["views"]}

    c = await db.get_client(client_id)
    text = (
        f"<b>Отчёт: {c['name']}</b>\n"
        f"Период: {start.strftime('%d.%m')}–{today.strftime('%d.%m')}\n\n"
        f"Просмотры: {total_views}\n"
        f"Лайки: {total_likes}\n"
        f"Комментарии: {total_comments}\n"
        f"Репосты: {total_reposts}\n"
        f"Подписчики: {followers:+d}\n"
    )
    if best:
        text += f"\nЛучшая ветка: {best['date']} в {best['slot']} — {best['views']} просмотров"
    await callback.message.answer(text)
    await callback.answer()


async def send_today_posts_job(bot: Bot):
    today = date.today().isoformat()
    for c in await db.list_clients():
        if c["telegram_id"]:
            posts = await db.get_posts(c["id"], today)
            if posts and not all(p["sent_at"] for p in posts):
                await send_posts_to_client(bot, c["id"], today)


async def send_results_poll_job(bot: Bot):
    # Опрос раз в 2 дня: по чётности порядкового номера дня.
    today = date.today()
    if today.toordinal() % 2 != 0:
        return
    for c in await db.list_clients():
        if not c["telegram_id"]:
            continue
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Были переходы", callback_data="result_start:yes"),
                    InlineKeyboardButton(text="Пока ничего", callback_data="result_start:no"),
                ]
            ]
        )
        await bot.send_message(
            c["telegram_id"],
            "За последние 2 дня Threads принёс какой-то результат?",
            reply_markup=kb,
        )


@router.callback_query(F.data.startswith("result_start:"))
async def result_start(callback: CallbackQuery, state: FSMContext):
    client = await db.get_client_by_tg(callback.from_user.id)
    if not client:
        return
    answer = callback.data.split(":")[1]
    if answer == "no":
        await db.save_result_poll(
            client["id"],
            date.today().isoformat(),
            answer="nothing",
        )
        await callback.message.answer("Спасибо, отметила.")
        await callback.answer()
        return

    await state.update_data(client_id=client["id"], period_end=date.today().isoformat())
    await state.set_state(ResultFlow.transitions)
    await callback.message.answer("Сколько примерно было переходов в Telegram? Можно написать 0.")
    await callback.answer()


@router.message(ResultFlow.transitions)
async def result_transitions(message: Message, state: FSMContext):
    try:
        value = int(message.text.strip())
    except ValueError:
        await message.answer("Введите число.")
        return
    await state.update_data(transitions=value)
    await state.set_state(ResultFlow.inquiries)
    await message.answer("Сколько было обращений?")


@router.message(ResultFlow.inquiries)
async def result_inquiries(message: Message, state: FSMContext):
    try:
        value = int(message.text.strip())
    except ValueError:
        await message.answer("Введите число.")
        return
    await state.update_data(inquiries=value)
    await state.set_state(ResultFlow.sales)
    await message.answer("Сколько было продаж?")


@router.message(ResultFlow.sales)
async def result_sales(message: Message, state: FSMContext):
    try:
        value = int(message.text.strip())
    except ValueError:
        await message.answer("Введите число.")
        return
    await state.update_data(sales=value)
    await state.set_state(ResultFlow.revenue)
    await message.answer("Сумма продаж? Можно написать 0.")


@router.message(ResultFlow.revenue)
async def result_revenue(message: Message, state: FSMContext):
    try:
        value = float(message.text.strip().replace(",", "."))
    except ValueError:
        await message.answer("Введите число.")
        return
    data = await state.get_data()
    await db.save_result_poll(
        data["client_id"],
        data["period_end"],
        tg_transitions=data["transitions"],
        inquiries=data["inquiries"],
        sales=data["sales"],
        revenue=value,
        answer="result",
    )
    await state.clear()
    await message.answer("Спасибо, всё сохранила ✅")



@router.message(
    F.chat.type == "private",
    ~F.text.in_({"/start", "/menu"}),
)
async def client_message_to_topic(message: Message, bot: Bot, state: FSMContext):
    # Не перехватываем сообщения во время активного сценария FSM.
    if await state.get_state():
        return

    client = await db.get_client_by_tg(message.from_user.id)
    if not client or not WORK_GROUP_ID:
        return

    topic_id = await ensure_client_topic(bot, client["id"])
    if not topic_id:
        return

    header = await bot.send_message(
        WORK_GROUP_ID,
        "<b>Сообщение от клиента</b>",
        message_thread_id=topic_id,
    )

    copied = await bot.copy_message(
        chat_id=WORK_GROUP_ID,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
        message_thread_id=topic_id,
    )
    await db.save_message_link(
        client["id"],
        client_message_id=message.message_id,
        group_message_id=copied.message_id,
    )
    await message.answer("Сообщение передано ✅")


@router.message(F.chat.id == WORK_GROUP_ID)
async def topic_reply_to_client(message: Message, bot: Bot):
    if not message.message_thread_id:
        return
    if message.from_user and message.from_user.is_bot:
        return

    client = await db.get_client_by_topic(message.message_thread_id)
    if not client or not client["telegram_id"]:
        return

    # Системные команды в группе клиенту не отправляем.
    if message.text and message.text.startswith("/"):
        return

    try:
        await bot.copy_message(
            chat_id=client["telegram_id"],
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        await message.reply("Отправлено клиенту ✅")
    except Exception as exc:
        logging.exception("Не удалось отправить сообщение клиенту: %s", exc)
        await message.reply("Не удалось отправить сообщение клиенту.")


async def main():
    await db.init_db()
    bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(
        send_today_posts_job,
        "cron",
        hour=DAILY_SEND_HOUR,
        minute=0,
        args=[bot],
        id="daily_posts",
        replace_existing=True,
    )
    scheduler.add_job(
        send_results_poll_job,
        "cron",
        hour=RESULTS_POLL_HOUR,
        minute=0,
        args=[bot],
        id="results_poll",
        replace_existing=True,
    )
    scheduler.start()

    await bot.set_my_commands([
        BotCommand(command="start", description="Запустить"),
        BotCommand(command="menu", description="Открыть меню"),
        BotCommand(command="chatid", description="Показать ID группы"),
    ])

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
