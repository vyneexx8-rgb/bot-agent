"""Telegram Business automation bot (aiogram 3). Run: python main.py

Uses official Business/Secretary mode, NOT a userbot or session login.
"""
from __future__ import annotations
import asyncio
import logging
import os
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import (
    BusinessConnection, BusinessMessagesDeleted, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup, Message,
)
from dotenv import load_dotenv

import features
from ai_styles import as_telegram_text, rewrite
from db import Store
from utils import content, human_duration, parse_duration, safe_text

load_dotenv()
TOKEN = os.environ.get("BOT_TOKEN", "")
DB_PATH = os.environ.get("DB_PATH", "autobot.sqlite3")
store = Store(DB_PATH)
mute_lock = asyncio.Lock()
styling_inflight: set[tuple[str, int, int]] = set()
router = Router()
log = logging.getLogger("autobot")


def unmute_markup(mute_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔓 Анмут", callback_data=f"unmute:{mute_id}")
    ]])


def unsticker_markup(mute_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔓 Снять мут стикеров", callback_data=f"unmutes:{mute_id}")
    ]])


def responder_markup(settings) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("Выключить" if settings["enabled"] else "Включить"), callback_data="ar:toggle")],
        [InlineKeyboardButton(text="Режим: всегда / AFK", callback_data="ar:mode")],
        [InlineKeyboardButton(text="AFK: вкл / выкл", callback_data="ar:afk")],
    ])


def responder_panel(settings) -> str:
    mode = "всегда" if settings["mode"] == "always" else "только в AFK (вручную)"
    return ("<b>Автоответчик</b>\n"
            f"Статус: {'✅ включён' if settings['enabled'] else '❌ выключен'}\n"
            f"Интервал: {human_duration(settings['interval_s'])}\n"
            f"Режим: {mode}\n"
            f"AFK: {'включён' if settings['afk'] else 'выключен'}\n\n"
            f"Текст: {safe_text(settings['response'], 900)}\n\n"
            "Команды: /text новый текст, /interval 1ч, /afk on|off")


def help_text() -> str:
    return ("<b>Команды Business-бота</b>\n\n"
            "<b>В личном чате с ботом:</b>\n"
            "/autoresponder — панель автоответчика\n"
            "/text текст — изменить автоответ\n"
            "/interval 1ч — интервал ответов\n"
            "/afk on|off — вручную включить AFK\n"
            "/mute CHAT_ID 1ч — мут на время\n"
            "/mutes CHAT_ID 2 часа — мут только стикеров\n"
            "/unmutes CHAT_ID — снять мут стикеров\n"
            "/muteslist — список мутов стикеров\n"
            "/unmute CHAT_ID — снять мут\n"
            "/antimute CHAT_ID — то же самое\n"
            "/mutelist — список мутов и кнопки\n\n"
            "<b>В подключённом Business-чате (если Telegram передаёт исходящие сообщения):</b>\n"
            ".mute 1ч, .mutes 2 hours, .unmutes, .unmute, .antimute, .autoresponder\n"
            "Игры: .chk, .ttt, .duel. Анимации: .love, .sparkle.\n"
            "Образ профиля: .clone, восстановление: .restore.\n"
            "Остальные команды: .help (меню с кнопками).\n"
            "Режим оформления: .kawai / .kawaii / .bold / .italic / .monospace / .underline / .tsundere / .yandere\n"
            "Повторная команда стиля его выключает. /style CHAT_ID kawaii — запасной способ.\n"
            "Если исходящие команды не видны: /run CHAT_ID .help\n\n"
            "Логи удаления и редактирования приходят владельцу бота в личные сообщения. "
            "Мут работает только для управляемого Business-чата. "
            "После ручного снятия или истечения срока бот заменяет первоначальное "
            "сообщение о муте на сообщение об анмуте (новое не отправляет).")


async def send_owner(bot: Bot, conn, text: str, **kwargs):
    try:
        return await bot.send_message(conn["owner_chat_id"], text, parse_mode="HTML", **kwargs)
    except TelegramAPIError as exc:
        log.warning("Не получилось отправить владельцу: %s", exc)
        return None


async def get_conn(bot: Bot, conn_id: str):
    conn = store.connection(conn_id)
    if conn:
        return conn
    try:
        remote = await bot.get_business_connection(business_connection_id=conn_id)
        store.save_connection(remote)
        return store.connection(conn_id)
    except TelegramAPIError:
        return None


def ended_mute_notice(reason: str, owner_name: str = "Пользователь") -> str:
    if reason == "expired":
        return ("🔓 <b>Срок мута истёк.</b>\n"
                "Мут с собеседника снят автоматически.\n"
                "Теперь сообщения не удаляются.")
    if reason == "replaced":
        return ("🔓 <b>Предыдущий мут завершён.</b>\n"
                "Установлен новый срок мута.")
    return (f"🔓 <b>{safe_text(owner_name, 100)} снял(а) мут с собеседника.</b>\n"
            "Теперь сообщения не удаляются.")


async def update_original_notice(bot: Bot, mute, owner_name: str = "Пользователь") -> bool:
    """Edit the original shared message, NEVER send a second message in that chat."""
    if not mute["notice_msg_id"]:
        # Initial notification wasn't delivered, so there is nothing to replace.
        store.mark_notice_updated(mute["id"])
        return False
    try:
        await bot.edit_message_text(
            chat_id=mute["chat_id"],
            message_id=mute["notice_msg_id"],
            business_connection_id=mute["conn_id"],
            text=ended_mute_notice(mute["end_reason"], owner_name),
            parse_mode="HTML",
            reply_markup=None,
        )
        store.mark_notice_updated(mute["id"])
        return True
    except TelegramAPIError as exc:
        # No new shared message; retry this exact edit after a short delay.
        store.defer_notice_update(mute["id"], 30)
        log.warning("Failed editing original mute notice (%s): %s", mute["id"], exc)
        return False


async def finish_mute(bot: Bot, mute_id: int, reason: str,
                      owner_name: str = "Пользователь") -> tuple[bool, bool]:
    """End mute immediately, edit the SAME notice, persist retries on failure."""
    async with mute_lock:
        mute = store.mute_by_id(mute_id)
        if not mute or mute["end_reason"] is not None:
            return False, False
        # If it already expired, always announce the expiry, even when the
        # user clicks 'unmute' at the exact time the background job wakes up.
        if mute["expires"] <= int(time.time()):
            reason = "expired"
        mute = store.end_mute(mute_id, reason)
        delivered = await update_original_notice(bot, mute, owner_name)
        return True, delivered


async def unmute_chat(bot: Bot, conn_id: str, chat_id: int,
                      owner_name: str = "Пользователь") -> tuple[bool, bool]:
    mute = store.mute_any(conn_id, chat_id)
    if not mute or mute["end_reason"] is not None:
        return False, False
    return await finish_mute(bot, mute["id"], "manual", owner_name)


async def set_mute(bot: Bot, conn, chat_id: int, duration: int, reply_to_owner: bool = False):
    if not conn["can_delete_all"]:
        raise ValueError("Нужно разрешение «Удалять все сообщения» для Business-бота.")
    if chat_id == conn["owner_id"]:
        raise ValueError("Нельзя установить мут самому себе.")
    async with mute_lock:
        previous = store.mute_any(conn["id"], chat_id)
        if previous and previous["end_reason"] is None:
            old = store.end_mute(previous["id"], "replaced")
            await update_original_notice(bot, old)
        mute_id = store.set_mute(conn["id"], chat_id, conn["owner_id"], int(time.time()) + duration)
        notice = ("🔇 <b>Пользователь установил вам мут.</b>\n"
                  f"Срок: <b>{human_duration(duration)}</b>\n"
                  "Новые сообщения будут удаляться до окончания срока.")
        try:
            sent = await bot.send_message(
                chat_id=chat_id, business_connection_id=conn["id"],
                text=notice, parse_mode="HTML", reply_markup=unmute_markup(mute_id))
            store.set_mute_notice(conn["id"], chat_id, sent.message_id)
            delivered = True
        except TelegramAPIError as exc:
            log.warning("Mute notice not delivered in chat %s: %s", chat_id, exc)
            delivered = False
    if reply_to_owner and not delivered:
        await send_owner(bot, conn,
                         "Мут установлен, но уведомление собеседнику не отправлено. "
                         "Проверь права ответа и ограничение 24 часов Telegram.")
    return mute_id, delivered


async def process_expired_mutes(bot: Bot):
    """Handle expirations and retry failed edits, including after bot restarts."""
    pending = store.needs_notice_update()
    for mute in pending:
        if mute["end_reason"] is None:
            await finish_mute(bot, mute["id"], "expired")
        else:
            async with mute_lock:
                latest = store.mute_by_id(mute["id"])
                if latest and latest["end_reason"] and not latest["notice_updated"]:
                    await update_original_notice(bot, latest)


def ended_sticker_notice(reason: str, owner_name: str = "Пользователь") -> str:
    if reason == "expired":
        return ("🔓 <b>Срок мута стикеров истёк.</b>\n"
                "Теперь собеседник снова может отправлять стикеры.")
    if reason == "replaced":
        return "🔓 <b>Прежний мут стикеров заменён новым.</b>"
    return (f"🔓 <b>{safe_text(owner_name, 100)} снял(а) мут стикеров.</b>\n"
            "Теперь собеседник снова может отправлять стикеры.")


async def update_sticker_notice(bot: Bot, mute, owner_name: str = "Пользователь") -> bool:
    """Replace the original shared sticker-mute message, not a new message."""
    if not mute["notice_msg_id"]:
        store.mark_sticker_notice_updated(mute["id"])
        return False
    try:
        await bot.edit_message_text(
            chat_id=mute["chat_id"], message_id=mute["notice_msg_id"],
            business_connection_id=mute["conn_id"],
            text=ended_sticker_notice(mute["end_reason"], owner_name),
            parse_mode="HTML", reply_markup=None)
        store.mark_sticker_notice_updated(mute["id"])
        return True
    except TelegramAPIError as exc:
        store.defer_sticker_notice_update(mute["id"], 30)
        log.warning("Could not edit sticker mute notice %s: %s", mute["id"], exc)
        return False


async def finish_sticker_mute(bot: Bot, mute_id: int, reason: str,
                              owner_name: str = "Пользователь") -> tuple[bool, bool]:
    async with mute_lock:
        mute = store.sticker_mute_by_id(mute_id)
        if not mute or mute["end_reason"] is not None:
            return False, False
        if mute["expires"] <= int(time.time()):
            reason = "expired"
        mute = store.end_sticker_mute(mute_id, reason)
        delivered = await update_sticker_notice(bot, mute, owner_name)
        return True, delivered


async def unsticker_chat(bot: Bot, conn_id: str, chat_id: int,
                         owner_name: str = "Пользователь") -> tuple[bool, bool]:
    mute = store.sticker_mute_any(conn_id, chat_id)
    if not mute or mute["end_reason"] is not None:
        return False, False
    return await finish_sticker_mute(bot, mute["id"], "manual", owner_name)


async def set_sticker_mute(bot: Bot, conn, chat_id: int, duration: int) -> tuple[int, bool]:
    if not conn["can_delete_all"]:
        raise ValueError("Для удаления стикеров разреши Business-боту «Удалять все сообщения».")
    if chat_id == conn["owner_id"]:
        raise ValueError("Нельзя замутить собственные стикеры этим способом.")
    async with mute_lock:
        prior = store.sticker_mute_any(conn["id"], chat_id)
        if prior and prior["end_reason"] is None:
            old = store.end_sticker_mute(prior["id"], "replaced")
            await update_sticker_notice(bot, old)
        mute_id = store.set_sticker_mute(
            conn["id"], chat_id, conn["owner_id"], int(time.time()) + duration)
        notice = ("🧩 <b>Пользователь запретил вам отправлять стикеры.</b>\n"
                  f"Срок: <b>{human_duration(duration)}</b>\n"
                  "Текст и другие сообщения не блокируются.")
        delivered = False
        try:
            sent = await bot.send_message(
                chat_id=chat_id, business_connection_id=conn["id"],
                text=notice, parse_mode="HTML", reply_markup=unsticker_markup(mute_id))
            store.set_sticker_mute_notice(mute_id, sent.message_id)
            delivered = True
        except TelegramAPIError as exc:
            log.warning("Sticker mute notice was not delivered in %s: %s", chat_id, exc)
    return mute_id, delivered


async def process_expired_sticker_mutes(bot: Bot):
    """Finalize expirations and retry updating notices after outages."""
    for mute in store.pending_sticker_notices():
        if mute["end_reason"] is None:
            await finish_sticker_mute(bot, mute["id"], "expired")
        else:
            async with mute_lock:
                current = store.sticker_mute_by_id(mute["id"])
                if current and current["end_reason"] and not current["notice_updated"]:
                    await update_sticker_notice(bot, current)


async def style_outgoing(bot: Bot, conn, msg: Message) -> None:
    """Edit ONLY the connected owner's outgoing text, in its original message.

    Never intercept or alter received text, service notices, or dot commands.
    """
    if not msg.text or msg.sender_business_bot or msg.text.startswith('.'):
        return
    if not conn['can_reply']:
        return
    chat_id, msg_id, conn_id = msg.chat.id, msg.message_id, conn['id']
    mode = store.style(conn_id, chat_id)
    if not mode or store.already_styled(conn_id, chat_id, msg_id):
        return
    ident = (conn_id, chat_id, msg_id)
    if ident in styling_inflight:
        return
    styling_inflight.add(ident)
    try:
        source = msg.text.strip()
        if len(source) > 1600:
            return  # Don't silently truncate an existing message.
        revised = await rewrite(mode, source)
        out, parse_mode = as_telegram_text(mode, revised)
        # Mark attempts to prevent repeated Groq calls from duplicate updates.
        store.mark_styled(conn_id, chat_id, msg_id)
        if out == source and not parse_mode:
            return
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                business_connection_id=conn_id, text=out,
                **({'parse_mode': parse_mode} if parse_mode else {}),
            )
        except TelegramAPIError as exc:
            # Not all Telegram clients/configurations allow editing outgoing
            # Business-user messages. Keep the original; never send duplicates.
            log.warning('Outgoing style edit rejected in chat %s: %s', chat_id, exc)
    finally:
        styling_inflight.discard(ident)


@router.business_connection()
async def business_connected(conn: BusinessConnection, bot: Bot):
    store.save_connection(conn)
    if conn.is_enabled:
        await send_owner(bot, store.connection(conn.id),
                         "✅ Business-бот подключён. Открой /help для управления.\n"
                         "Для мута разреши боту удаление всех сообщений в управляемых чатах.")


@router.business_message()
async def on_business_message(message: Message, bot: Bot):
    conn_id = message.business_connection_id
    if not conn_id or message.chat.type != "private":
        return
    conn = await get_conn(bot, conn_id)
    if not conn or not conn["enabled"]:
        return

    # Bot-authored business messages may appear as sent by the connected owner.
    # They must NEVER be auto-restyled or parsed as owner commands.
    if message.sender_business_bot:
        return

    # Commands from the account owner, ONLY if Telegram delivers these outbound messages.
    is_owner = bool(message.from_user and message.from_user.id == conn["owner_id"])
    if is_owner:
        command = (message.text or "").strip()
        if command.startswith("."):
            action, *args = command.split(maxsplit=1)
            action = action.lower()
            if action == ".mute":
                try:
                    seconds = parse_duration(args[0] if args else "")
                    await set_mute(bot, conn, message.chat.id, seconds)
                except ValueError as exc:
                    await send_owner(bot, conn, safe_text(str(exc)))
            elif action == ".mutes":
                try:
                    seconds = parse_duration(args[0] if args else "")
                    await set_sticker_mute(bot, conn, message.chat.id, seconds)
                except ValueError as exc:
                    await send_owner(bot, conn, safe_text(str(exc)))
            elif action == ".unmutes":
                name = message.from_user.full_name if message.from_user else "Пользователь"
                removed, delivered = await unsticker_chat(bot, conn_id, message.chat.id, name)
                await send_owner(bot, conn,
                                 "🔓 Мут стикеров снят." if removed else "Активного мута стикеров нет.")
            elif action in (".antimute", ".unmute"):
                name = message.from_user.full_name if message.from_user else "Пользователь"
                removed, delivered = await unmute_chat(bot, conn_id, message.chat.id, name)
                text = ("🔓 Мут снят. " +
                        ("Исходное сообщение обновлено." if delivered else
                         "Исходное сообщение пока не удалось изменить.")) if removed else "Активного мута нет."
                await send_owner(bot, conn, text)
            elif action == ".autoresponder":
                st = store.settings(conn_id)
                await send_owner(bot, conn, responder_panel(st), reply_markup=responder_markup(st))
            else:
                await features.process(bot, conn, message.chat.id, command, message,
                                       store, owner_sender=message.from_user)
        else:
            await style_outgoing(bot, conn, message)
        return

    mute = store.mute(conn_id, message.chat.id)
    if mute:
        # Do not cache messages we delete ourselves; avoids mirroring those as logs.
        if conn["can_delete_all"]:
            try:
                await bot.delete_business_messages(
                    business_connection_id=conn_id, message_ids=[message.message_id])
            except TelegramAPIError as exc:
                log.warning("Failed deleting muted message in %s: %s", message.chat.id, exc)
        return

    if message.sticker is not None and store.sticker_mute(conn_id, message.chat.id):
        # Sticker mute is independent of normal mute and doesn't affect text.
        try:
            await bot.delete_business_messages(
                business_connection_id=conn_id, message_ids=[message.message_id])
        except TelegramAPIError as exc:
            log.warning("Could not delete sticker in chat %s: %s", message.chat.id, exc)
        # Do not record the message in deletion logs as if the sender deleted it.
        return

    kind, body = content(message)
    store.save_message(conn_id, message, kind, body)

    st = store.settings(conn_id)
    if not st or not st["enabled"] or not conn["can_reply"]:
        return
    if st["mode"] == "afk" and not st["afk"]:
        return
    if time.time() - store.last_reply(conn_id, message.chat.id) < st["interval_s"]:
        return
    try:
        await bot.send_message(chat_id=message.chat.id, text=st["response"],
                               business_connection_id=conn_id)
        store.mark_reply(conn_id, message.chat.id)
    except TelegramAPIError as exc:
        log.warning("Auto reply failed: %s", exc)


@router.edited_business_message()
async def on_edited_business_message(message: Message, bot: Bot):
    conn_id = message.business_connection_id
    if not conn_id or message.chat.type != "private":
        return
    conn = await get_conn(bot, conn_id)
    if not conn or not conn["enabled"]:
        return
    if not message.from_user or message.from_user.id == conn["owner_id"]:
        return
    kind, body = content(message)
    old = store.message(conn_id, message.chat.id, message.message_id)
    if not old:
        # Nothing to compare if bot never received the original.
        store.save_message(conn_id, message, kind, body)
        return
    if old["body"] == body and old["kind"] == kind:
        return
    store.save_message(conn_id, message, kind, body)
    who = f"@{safe_text(old['username'])}" if old["username"] else "без username"
    await send_owner(bot, conn,
                     f"✏️ <b>{safe_text(old['name'])}</b> ({who}) "
                     f"[<code>{old['from_id']}</code>] отредактировал сообщение.\n\n"
                     f"<b>Было:</b>\n<blockquote>{safe_text(old['body'])}</blockquote>\n"
                     f"<b>Стало:</b>\n<blockquote>{safe_text(body)}</blockquote>")


@router.deleted_business_messages()
async def on_deleted_business_messages(event: BusinessMessagesDeleted, bot: Bot):
    conn = await get_conn(bot, event.business_connection_id)
    if not conn or not conn["enabled"]:
        return
    for msg_id in event.message_ids[:100]:
        cached = store.message(conn["id"], event.chat.id, msg_id)
        store.delete_cached_message(conn["id"], event.chat.id, msg_id)
        if not cached or cached["from_id"] == conn["owner_id"]:
            continue
        who = f"@{safe_text(cached['username'])}" if cached["username"] else "без username"
        await send_owner(bot, conn,
                         "🗑 <b>Это сообщение было удалено</b>\n"
                         f"От: <b>{safe_text(cached['name'])}</b> ({who}) "
                         f"[<code>{cached['from_id']}</code>]\n"
                         f"Тип: {safe_text(cached['kind'])}\n\n"
                         f"<blockquote>{safe_text(cached['body'])}</blockquote>")


@router.message(Command("start", "help"))
async def start(message: Message):
    # This is a normal bot PM. It never requests a login code or session.
    await message.answer(help_text(), parse_mode="HTML", reply_markup=features.main_menu())


@router.message(Command("run"))
async def owner_run(message: Message, bot: Bot):
    """Fallback if Telegram does not deliver owner's outgoing Business messages.

    Syntax: /run CHAT_ID .command args
    Only the connected owner can use it. Never accept a connection ID from chat.
    """
    conn = store.for_owner(message.from_user.id) if message.from_user else None
    if not conn:
        await message.answer("Сначала подключи своего Business-бота.")
        return
    pieces = (message.text or "").split(maxsplit=2)
    if len(pieces) != 3:
        await message.answer("Пример: /run 123456789 .love\n"
                             "Фото- и видео-командам нужен ответ на вложение в этом чате с ботом.")
        return
    try:
        target = int(pieces[1])
    except ValueError:
        await message.answer("Укажи числовой ID чата.")
        return
    if target == conn["owner_id"]:
        await message.answer("Нужен ID собеседника в разрешённом Business-чате.")
        return
    line = pieces[2]
    if line.startswith(".mute "):
        try:
            seconds = parse_duration(line.partition(" ")[2])
            _, delivered = await set_mute(bot, conn, target, seconds)
            await message.answer("Мут установлен. " +
                                 ("Уведомление доставлено." if delivered else "Уведомление не доставлено."))
        except ValueError as exc:
            await message.answer(str(exc))
        return
    if line.startswith(".mutes "):
        try:
            seconds = parse_duration(line.partition(" ")[2])
            _, delivered = await set_sticker_mute(bot, conn, target, seconds)
            await message.answer("Мут стикеров установлен. " +
                                 ("Уведомление отправлено." if delivered else "Уведомление не доставлено."))
        except ValueError as exc:
            await message.answer(str(exc))
        return
    if line == ".unmutes":
        removed, delivered = await unsticker_chat(bot, conn["id"], target, message.from_user.full_name)
        await message.answer(
            ("Мут стикеров снят. " + ("Уведомление обновлено." if delivered else
             "Не удалось обновить уведомление.")) if removed else "Активного мута стикеров нет.")
        return
    if line in (".antimute", ".unmute"):
        removed, delivered = await unmute_chat(bot, conn["id"], target, message.from_user.full_name)
        if removed:
            await message.answer("Мут снят. " + ("Уведомление изменено." if delivered else
                                 "Уведомление не получилось изменить."))
        else:
            await message.answer("Активного мута нет.")
        return
    if not line.startswith("."):
        await message.answer("Команда должна начинаться с точки, например .help")
        return
    handled = await features.process(bot, conn, target, line, message,
                                     store, owner_sender=message.from_user)
    await message.answer("Команда отправлена (если Telegram разрешил действие)." if handled
                         else "Неизвестная команда. Используй .help")


@router.callback_query(F.data.startswith(("help:", "cmd:", "gm:")))
async def feature_buttons(query: CallbackQuery, bot: Bot):
    if await features.game_callback(query, bot):
        return
    await features.help_callback(query, bot)


@router.message(Command('style'))
async def owner_style(message: Message):
    """Fallback command if the owner's outgoing Business updates aren't delivered."""
    if not message.from_user:
        return
    conn = store.for_owner(message.from_user.id)
    if not conn:
        await message.answer('Сначала подключи Business-бота.')
        return
    pieces = (message.text or '').split(maxsplit=2)
    if len(pieces) < 2:
        await message.answer('Пример: /style 123456789 kawaii\n/style 123456789 off')
        return
    try:
        chat_id = int(pieces[1])
    except ValueError:
        await message.answer('Нужен числовой ID собеседника.')
        return
    if chat_id == conn['owner_id']:
        await message.answer('Укажи ID разрешённого чата с собеседником.')
        return
    from ai_styles import normalized_mode, is_valid_mode, mode_notice
    if len(pieces) == 2 or pieces[2].lower() == 'status':
        await message.answer('Стиль чата: ' + (store.style(conn['id'], chat_id) or 'выключен'))
        return
    mode = normalized_mode(pieces[2])
    if not is_valid_mode(mode) and mode != '':
        await message.answer('Неизвестный стиль. Пример: kawaii, tsundere, bold, off.')
        return
    current = store.style(conn['id'], chat_id)
    mode = '' if mode and current == mode else mode
    store.set_style(conn['id'], chat_id, mode)
    await message.answer(mode_notice(mode))


@router.message(Command("autoresponder", "ar"))
async def show_autoresponder(message: Message):
    if not message.from_user:
        return
    conn = store.for_owner(message.from_user.id)
    if not conn:
        await message.answer("Сначала подключи бота через Telegram Business / Автоматизацию чатов.")
        return
    st = store.settings(conn["id"])
    await message.answer(responder_panel(st), parse_mode="HTML", reply_markup=responder_markup(st))


@router.callback_query(F.data.startswith("ar:"))
async def responder_callback(query: CallbackQuery):
    conn = store.for_owner(query.from_user.id)
    if not conn:
        await query.answer("Настройки доступны только владельцу.", show_alert=True)
        return
    state = store.settings(conn["id"])
    action = query.data.removeprefix("ar:")
    if action == "toggle":
        store.update_settings(conn["id"], enabled=int(not state["enabled"]))
    elif action == "mode":
        store.update_settings(conn["id"], mode="always" if state["mode"] == "afk" else "afk")
    elif action == "afk":
        store.update_settings(conn["id"], afk=int(not state["afk"]))
    else:
        await query.answer("Неизвестная команда")
        return
    st = store.settings(conn["id"])
    if query.message:
        try:
            await query.message.edit_text(responder_panel(st), parse_mode="HTML", reply_markup=responder_markup(st))
        except TelegramAPIError:
            pass
    await query.answer("Сохранено")


@router.message(Command("text"))
async def set_text(message: Message):
    conn = store.for_owner(message.from_user.id) if message.from_user else None
    if not conn:
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.answer("Пример: /text Сейчас не могу ответить.")
        return
    if len(parts[1]) > 2000:
        await message.answer("Максимальная длина текста: 2000 символов.")
        return
    store.update_settings(conn["id"], response=parts[1])
    await message.answer("✅ Текст сохранён.")


@router.message(Command("interval"))
async def set_interval(message: Message):
    conn = store.for_owner(message.from_user.id) if message.from_user else None
    if not conn:
        return
    parts = (message.text or "").split(maxsplit=1)
    try:
        interval = parse_duration(parts[1] if len(parts) == 2 else "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    store.update_settings(conn["id"], interval_s=interval)
    await message.answer(f"✅ Интервал: {human_duration(interval)}")


@router.message(Command("afk"))
async def set_afk(message: Message):
    conn = store.for_owner(message.from_user.id) if message.from_user else None
    if not conn:
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or parts[1].lower() not in ("on", "off"):
        await message.answer("Пример: /afk on или /afk off")
        return
    store.update_settings(conn["id"], afk=int(parts[1].lower() == "on"))
    await message.answer("✅ AFK обновлён.")


@router.message(Command("mute"))
async def manual_mute(message: Message, bot: Bot):
    conn = store.for_owner(message.from_user.id) if message.from_user else None
    if not conn:
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Пример: /mute 123456789 1ч (ID собеседника).\n"
                             "Кнопка «🔓 Анмут» будет под сообщением в ОБЩЕЙ переписке, у обоих.")
        return
    try:
        chat_id = int(parts[1])
        seconds = parse_duration(parts[2])
        _, delivered = await set_mute(bot, conn, chat_id, seconds)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await message.answer(f"🔇 Мут установлен: {human_duration(seconds)}. "
                         + ("Уведомление отправлено." if delivered else "Уведомление не доставлено."))


@router.message(Command("mutes"))
async def manual_sticker_mute(message: Message, bot: Bot):
    conn = store.for_owner(message.from_user.id) if message.from_user else None
    if not conn:
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3:
        await message.answer("Пример: /mutes 123456789 2 часа или /mutes 123456789 30min")
        return
    try:
        target = int(parts[1])
        seconds = parse_duration(parts[2])
        _, delivered = await set_sticker_mute(bot, conn, target, seconds)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await message.answer(f"🧩 Мут стикеров на {human_duration(seconds)} установлен. " +
                         ("Уведомление доставлено." if delivered else "Уведомление не доставлено."))


@router.message(Command("unmutes"))
async def manual_unsticker(message: Message, bot: Bot):
    conn = store.for_owner(message.from_user.id) if message.from_user else None
    if not conn:
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Пример: /unmutes 123456789")
        return
    try:
        target = int(parts[1])
    except ValueError:
        await message.answer("Укажи числовой ID чата.")
        return
    removed, delivered = await unsticker_chat(bot, conn["id"], target, message.from_user.full_name)
    if not removed:
        await message.answer("Активного мута стикеров нет.")
    else:
        await message.answer("🔓 Мут стикеров снят. " +
                             ("Сообщение изменено." if delivered else "Не удалось изменить уведомление."))


@router.message(Command("muteslist"))
async def sticker_mute_list(message: Message):
    if not message.from_user:
        return
    rows = store.sticker_mutes_by_owner(message.from_user.id)
    if not rows:
        await message.answer("Активных мутов стикеров нет.")
        return
    for row in rows:
        left = max(0, row["expires"] - int(time.time()))
        await message.answer(f"🧩 Стикеры отключены в чате <code>{row['chat_id']}</code>\n"
                             f"Осталось: {human_duration(left)}", parse_mode="HTML",
                             reply_markup=unsticker_markup(row["id"]))


@router.message(Command("unmute", "antimute"))
async def manual_unmute(message: Message, bot: Bot):
    conn = store.for_owner(message.from_user.id) if message.from_user else None
    if not conn:
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Пример: /antimute 123456789")
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await message.answer("Укажи числовой ID чата.")
        return
    removed, delivered = await unmute_chat(bot, conn["id"], chat_id, message.from_user.full_name)
    if not removed:
        await message.answer("Активного мута нет.")
    elif delivered:
        await message.answer("🔓 Мут снят. Исходное сообщение изменено в общей переписке.")
    else:
        await message.answer("🔓 Мут снят, но редактирование исходного сообщения не удалось. Бот повторит попытку.")


@router.message(Command("mutelist"))
async def mute_list(message: Message):
    if not message.from_user:
        return
    rows = store.mutes_by_owner(message.from_user.id)
    if not rows:
        await message.answer("Активных мутов нет.")
        return
    for row in rows:
        seconds = max(0, row["expires"] - int(time.time()))
        await message.answer(f"🔇 Чат: <code>{row['chat_id']}</code>\n"
                             f"Осталось: {human_duration(seconds)}",
                             parse_mode="HTML", reply_markup=unmute_markup(row["id"]))


@router.callback_query(F.data.startswith("unmute:"))
async def unmute_callback(query: CallbackQuery, bot: Bot):
    try:
        mute_id = int(query.data.removeprefix("unmute:"))
    except ValueError:
        await query.answer("Неверная команда")
        return
    row = store.mute_by_id(mute_id)
    if not row:
        await query.answer("Мут уже снят или истёк.", show_alert=True)
        return
    # Critical: stranger who sees the button cannot unmute themself.
    if query.from_user.id != row["owner_id"]:
        await query.answer("Только владелец аккаунта может снять мут.", show_alert=True)
        return
    # Ignore buttons from a previous mute and don't treat an expired mute as active.
    if row["end_reason"]:
        await query.answer("Мут уже снят или истёк.", show_alert=True)
        return
    if row["expires"] <= int(time.time()):
        await finish_mute(bot, mute_id, "expired")
        await query.answer("Срок мута уже истёк.", show_alert=True)
        return
    removed, delivered = await finish_mute(bot, mute_id, "manual", query.from_user.full_name)
    await query.answer(
        "🔓 Исходное сообщение обновлено." if delivered else
        "Мут снят, но Telegram пока не позволил обновить сообщение.",
        show_alert=not delivered)


@router.callback_query(F.data.startswith("unmutes:"))
async def unstickers_callback(query: CallbackQuery, bot: Bot):
    try:
        mute_id = int(query.data.removeprefix("unmutes:"))
    except (ValueError, AttributeError):
        await query.answer("Неверная команда", show_alert=True)
        return
    row = store.sticker_mute_by_id(mute_id)
    if not row or row["end_reason"] is not None:
        await query.answer("Мут стикеров уже снят или истёк.", show_alert=True)
        return
    if query.from_user.id != row["owner_id"]:
        await query.answer("Только тот, кто поставил мут, может его снять.", show_alert=True)
        return
    if row["expires"] <= int(time.time()):
        await finish_sticker_mute(bot, mute_id, "expired")
        await query.answer("Срок уже закончился.", show_alert=True)
        return
    removed, delivered = await finish_sticker_mute(bot, mute_id, "manual", query.from_user.full_name)
    await query.answer("Мут стикеров снят." if delivered else
                       "Мут снят, но Telegram отклонил обновление уведомления.",
                       show_alert=not delivered)


async def periodic_cleanup():
    while True:
        await asyncio.sleep(6 * 3600)
        store.cleanup()


async def periodic_mute_expirations(bot: Bot):
    while True:
        try:
            await process_expired_mutes(bot)
            await process_expired_sticker_mutes(bot)
        except Exception:
            log.exception("Ошибка проверки окончания мутов")
        await asyncio.sleep(1)


async def periodic_clock(bot: Bot):
    while True:
        try:
            await features.clock_tick(bot, store)
        except Exception:
            log.exception("Ошибка обновления времени в имени")
        await asyncio.sleep(60)


async def main():
    if not TOKEN:
        raise SystemExit("Добавь BOT_TOKEN в файл .env")
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=TOKEN)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    store.cleanup()
    cleaner = asyncio.create_task(periodic_cleanup())
    expirer = asyncio.create_task(periodic_mute_expirations(bot))
    clock = asyncio.create_task(periodic_clock(bot))
    try:
        await dispatcher.start_polling(bot, allowed_updates=[
            "message", "callback_query", "business_connection", "business_message",
            "edited_business_message", "deleted_business_messages",
        ])
    finally:
        cleaner.cancel()
        expirer.cancel()
        clock.cancel()
        for task in list(features.RUNNING_TASKS):
            task.cancel()
        await asyncio.gather(cleaner, expirer, clock, *list(features.RUNNING_TASKS),
                             return_exceptions=True)
        store.db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
