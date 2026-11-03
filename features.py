"""Optional user-requested Business chat commands. No logins, scraping or gambling.

This is a normal Secretary/Business bot: commands work from outgoing business_message
updates IF Telegram delivers those updates. /run CHAT_ID .command is the fallback.
"""
from __future__ import annotations
import asyncio
import html
import io
import logging
import os
import random
import secrets
import shutil
import tempfile
import httpx
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from feature_utils import COMMANDS, COMMAND_INFO, FORMAT_MODES, HEART_ROWS, heart_frame, STAR_ROWS, STAR_COLORS, star_frame, mini_summary, public_profile, big_surprise, stylize
from ai_styles import as_telegram_text, is_valid_mode, mode_notice, normalized_mode, rewrite
import checkers_engine as ce
from profile_ops import costume, restore

log = logging.getLogger('features')
GAMES: dict[str, dict] = {}
RUNNING_TASKS: set[asyncio.Task] = set()


def track_task(coro):
    task = asyncio.create_task(coro)
    RUNNING_TASKS.add(task)
    task.add_done_callback(RUNNING_TASKS.discard)


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=category, callback_data=f'help:{i}')]
        for i, category in enumerate(COMMANDS)
    ])


def category_menu(i: int) -> InlineKeyboardMarkup:
    keys = list(COMMANDS)
    if i not in range(len(keys)):
        return main_menu()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f'.{command}', callback_data=f'cmd:{command}')]
        for command, _ in COMMANDS[keys[i]]
    ] + [[InlineKeyboardButton(text='◀️ Категории', callback_data='help:root')]])


def category_text(i: int) -> str:
    keys = list(COMMANDS)
    if i not in range(len(keys)):
        return 'Навигация по командам бота. Выбери категорию.'
    key = keys[i]
    return f'{key}\n' + '\n'.join(f'• .{name} — {description}' for name, description in COMMANDS[key])


async def say(bot: Bot, conn, chat_id: int, text: str, **kwargs):
    try:
        return await bot.send_message(chat_id=chat_id, business_connection_id=conn['id'], text=text, **kwargs)
    except TelegramAPIError as exc:
        log.warning('Business send to %s failed: %s', chat_id, exc)
        return None


async def send_media(bot: Bot, method_name: str, conn, chat_id: int, **kwargs):
    try:
        return await getattr(bot, method_name)(chat_id=chat_id, business_connection_id=conn['id'], **kwargs)
    except (TelegramAPIError, AttributeError) as exc:
        log.warning('%s for %s failed: %s', method_name, chat_id, exc)
        return None


def target_msg(message: Message | None) -> Message | None:
    return getattr(message, 'reply_to_message', None) if message else None


def target_user(message: Message | None):
    replied = target_msg(message)
    return replied.from_user if replied and replied.from_user else (message.chat if message else None)


def metadata_for_file(message: Message | None) -> str:
    replied = target_msg(message)
    if not replied:
        return 'Ответь командой .check на сообщение с документом, фото, видео или аудио.'
    f = replied.document or replied.video or replied.audio or replied.voice or (replied.photo[-1] if replied.photo else None)
    if not f:
        return 'У сообщения нет поддерживаемого файла.'
    name = getattr(f, 'file_name', '') or '(без имени)'
    mime = getattr(f, 'mime_type', None) or '(неизвестен)'
    size = getattr(f, 'file_size', None)
    kind = 'Документ' if replied.document else 'Медиа'
    risk = ''
    if name.lower().endswith(('.exe', '.apk', '.bat', '.cmd', '.js', '.scr')):
        risk = '\nВнимание: расширение файла может исполнять код. Не открывай непроверенные файлы.'
    return (f'{kind}: {name[:120]}\nТип (заявленный): {mime[:80]}\n'
            f'Размер: {size / 1024 / 1024:.2f} МиБ' if size is not None else
            f'{kind}: {name[:120]}\nТип (заявленный): {mime[:80]}\nРазмер неизвестен') + risk + '\nЭто сведения о файле, НЕ проверка на вирусы.'


async def get_file_bytes(bot: Bot, file_id: str, size: int | None = None) -> bytes:
    if size and size > 15_000_000:
        raise ValueError('Файл слишком большой (максимум 15 МБ).')
    meta = await bot.get_file(file_id)
    if not meta.file_path:
        raise ValueError('Telegram не предоставил путь к файлу.')
    out = io.BytesIO()
    await bot.download_file(meta.file_path, destination=out)
    data = out.getvalue()
    if len(data) > 15_000_000:
        raise ValueError('Файл слишком большой.')
    return data


def photo_file(message: Message | None):
    t = target_msg(message)
    return t.photo[-1] if t and t.photo else None


async def photo_transform(bot: Bot, conn, chat_id: int, message: Message | None, mode: str) -> str:
    photo = photo_file(message)
    if photo is None:
        return f'Ответь .{mode} на фотографию.'
    try:
        from PIL import Image, ImageOps
        data = await get_file_bytes(bot, photo.file_id, photo.file_size)
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image).convert('RGB')
        image.thumbnail((1800, 1800))
        if mode == 'lq':
            image.thumbnail((280, 280))
            out = io.BytesIO()
            image.save(out, format='JPEG', quality=14, optimize=True)
            await send_media(bot, 'send_photo', conn, chat_id,
                             photo=BufferedInputFile(out.getvalue(), filename='low_quality.jpg'),
                             caption='📷 Фото с пониженным качеством')
        elif mode == 'gif':
            image.thumbnail((550, 550))
            out = io.BytesIO()
            image.save(out, format='GIF', duration=800, loop=0)
            await send_media(bot, 'send_document', conn, chat_id,
                             document=BufferedInputFile(out.getvalue(), filename='photo.gif'),
                             caption='GIF из фотографии')
        else:  # story: three ordered portrait-style sections
            w, h = image.size
            if w < 3:
                return 'Изображение слишком маленькое.'
            for i in range(3):
                left, right = w * i // 3, w * (i + 1) // 3
                part = image.crop((left, 0, right, h))
                out = io.BytesIO()
                part.save(out, format='JPEG', quality=86)
                await send_media(bot, 'send_photo', conn, chat_id,
                                 photo=BufferedInputFile(out.getvalue(), filename=f'story_{i+1}.jpg'),
                                 caption=f'Часть {i+1}/3')
        return ''
    except (ValueError, OSError, TelegramAPIError) as exc:
        return f'Не удалось обработать изображение: {exc}'


async def ffmpeg_transform(bot: Bot, conn, chat_id: int, message: Message | None, mode: str) -> str:
    replied = target_msg(message)
    media = replied.voice if mode == 'fv' and replied else (replied.video or replied.animation if replied else None)
    if not media:
        return 'Ответь .fv на голосовое сообщение.' if mode == 'fv' else 'Ответь .gif на видео или фото.'
    if not shutil.which('ffmpeg'):
        return 'Для обработки медиа установи FFmpeg на сервере.'
    try:
        data = await get_file_bytes(bot, media.file_id, media.file_size)
        with tempfile.TemporaryDirectory() as temp:
            inp = Path(temp) / 'input'
            out = Path(temp) / ('louder.ogg' if mode == 'fv' else 'converted.gif')
            inp.write_bytes(data)
            if mode == 'fv':
                cmd = ['ffmpeg','-v','error','-y','-i',str(inp),'-filter:a','volume=1.7',
                       '-c:a','libopus','-b:a','48k','-t','60',str(out)]
            else:
                cmd = ['ffmpeg','-v','error','-y','-i',str(inp),'-t','8','-vf','fps=7,scale=360:-1:flags=lanczos',str(out)]
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL,
                                                       stderr=asyncio.subprocess.PIPE)
            try:
                _, err = await asyncio.wait_for(proc.communicate(), timeout=28)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return 'Слишком долгая обработка медиа.'
            if proc.returncode or not out.exists() or out.stat().st_size > 12_000_000:
                return 'Не удалось конвертировать файл: ' + err.decode(errors='replace')[:250]
            output = BufferedInputFile(out.read_bytes(), filename=out.name)
            if mode == 'fv':
                await send_media(bot, 'send_voice', conn, chat_id, voice=output, caption='Усиленное голосовое')
            else:
                await send_media(bot, 'send_document', conn, chat_id, document=output, caption='Готовый GIF')
        return ''
    except (ValueError, OSError, TelegramAPIError) as exc:
        return f'Ошибка обработки: {exc}'


async def animated_heart(bot: Bot, conn, chat_id: int):
    sent = await say(bot, conn, chat_id, '♡')
    if not sent:
        return
    for i in range(1, len(HEART_ROWS) + 1):
        await asyncio.sleep(1)
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=sent.message_id,
                                        business_connection_id=conn['id'],
                                        text='<pre>' + heart_frame(i, i) + '</pre>',
                                        parse_mode='HTML')
        except TelegramAPIError as exc:
            log.info('Heart animation stopped: %s', exc)
            break


async def animated_star(bot: Bot, conn, chat_id: int):
    """Draw a full multi-line rainbow star, then animate sparkling colours."""
    sent = await say(bot, conn, chat_id, '<pre>' + star_frame(3) + '</pre>', parse_mode='HTML')
    if not sent:
        return
    # Grow by two rows per second, then keep the completed picture shimmering.
    growth = list(range(5, len(STAR_ROWS) + 1, 2))
    if growth[-1] != len(STAR_ROWS):
        growth.append(len(STAR_ROWS))
    for i, row_count in enumerate(growth):
        await asyncio.sleep(1)
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=sent.message_id,
                                        business_connection_id=conn['id'],
                                        text='<pre>' + star_frame(row_count, i) + '</pre>',
                                        parse_mode='HTML')
        except TelegramAPIError as exc:
            log.info('Star animation stopped: %s', exc)
            return
    for phase in range(len(STAR_COLORS)):
        await asyncio.sleep(1)
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=sent.message_id,
                                        business_connection_id=conn['id'],
                                        text='<pre>' + star_frame(len(STAR_ROWS), phase + 4) + '</pre>',
                                        parse_mode='HTML')
        except TelegramAPIError as exc:
            log.info('Star sparkle stopped: %s', exc)
            break


async def random_neko(bot: Bot, conn, chat_id: int):
    """Fetch a random family-friendly anime catgirl from the public SFW endpoint."""
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            resp = await client.get('https://nekos.best/api/v2/neko',
                                    headers={'User-Agent': 'BusinessAutobot/3.0 (Telegram assistant)'})
            resp.raise_for_status()
            results = resp.json().get('results', [])
            url = results[0]['url'] if results else ''
            if not url.startswith('https://nekos.best/api/v2/neko/') or not url.endswith('.png'):
                raise ValueError('Unexpected picture URL')
        if not await send_media(bot, 'send_photo', conn, chat_id, photo=url, caption='🐱 Неко-тян'):
            await say(bot, conn, chat_id, 'Не удалось загрузить иллюстрацию.')
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        await say(bot, conn, chat_id, 'Сервис иллюстраций временно недоступен.')


async def simulated_typing(bot: Bot, conn, chat_id: int, text: str):
    for _ in range(2):
        try:
            await bot.send_chat_action(chat_id=chat_id, business_connection_id=conn['id'], action='typing')
        except TelegramAPIError:
            return
        await asyncio.sleep(3)
    await say(bot, conn, chat_id, text or 'Сообщение набрано.')


async def update_name(bot: Bot, conn, first_name: str, last_name: str = '') -> str:
    first_name = first_name.strip()[:64]
    if not first_name:
        return 'Имя не должно быть пустым.'
    try:
        remote = await bot.get_business_connection(business_connection_id=conn['id'])
        if not remote.rights or not getattr(remote.rights, 'can_edit_name', False):
            return 'Нужно разрешение «Изменять имя» в настройках Business-бота.'
        await bot.set_business_account_name(business_connection_id=conn['id'],
                                            first_name=first_name, last_name=last_name[:64])
    except TelegramAPIError as exc:
        return f'Telegram не разрешил изменить имя: {exc}'
    return f'Имя обновлено: {first_name}'


def player_label(state: dict, uid: int) -> str:
    return state.get('names', {}).get(uid) or ('Владелец' if uid == state['owner'] else 'Собеседник')


def new_game(mode: str, conn, chat_id: int, player_name='Владелец', other_name='Собеседник') -> tuple[str, dict]:
    for key in list(GAMES):
        if time.time() - GAMES[key]['created'] > 3600:
            del GAMES[key]
    if len(GAMES) > 150:
        GAMES.pop(next(iter(GAMES)))
    token = secrets.token_hex(4)
    board = ce.new_board() if mode == 'chk' else [''] * 9 if mode == 'ttt' else []
    state = {'kind': mode, 'board': board, 'conn_id': conn['id'], 'chat_id': chat_id,
             'owner': conn['owner_id'], 'created': time.time(),
             'turn': 'w' if mode == 'chk' else 'X' if mode == 'ttt' else conn['owner_id'],
             'selected': None, 'chain': None, 'combo': 0, 'message_id': None,
             'score': {'X':0,'O':0,'draw':0}, 'finished': False,
             'names': {conn['owner_id']: player_name[:32], chat_id: other_name[:32]},
             'winning_cells': ()}
    if mode == 'duel':
        state.update(phase='invite', hp={conn['owner_id']:3, chat_id:3}, logs=[], winner=None)
    GAMES[token] = state
    return token, state


def reset_game(state: dict) -> None:
    kind = state['kind']
    state['board'] = ce.new_board() if kind == 'chk' else [''] * 9 if kind == 'ttt' else []
    state['turn'] = 'w' if kind == 'chk' else 'X' if kind == 'ttt' else state['owner']
    state['selected'] = None
    state['chain'] = None
    state['combo'] = 0
    state['finished'] = False
    state['winning_cells'] = ()
    if kind == 'duel':
        state['phase'] = 'invite'
        state['hp'] = {state['owner']: 3, state['chat_id']: 3}
        state['logs'] = []
        state['winner'] = None


def game_screen(state: dict, hint: str = '') -> str:
    kind = state['kind']
    if kind == 'ttt':
        score = state['score']
        top = '🏆 ПАРТИЯ ЗАВЕРШЕНА' if state['finished'] else '✦ КРЕСТИКИ-НОЛИКИ ✦'
        turn = '❌ Владелец' if state['turn'] == 'X' else '⭕ Собеседник'
        return (f'{top}\n\n❌ Владелец  ·  ⭕ Собеседник\n'
                f'Счёт: {score["X"]} : {score["O"]}    Ничьи: {score["draw"]}\n'
                f'━━━━━━━━━━\n{hint or ("Ход: " + turn)}')
    if kind == 'chk':
        who = '⚪ Белые' if state['turn'] == 'w' else '⚫ Чёрные'
        selected = state.get('selected')
        count_w = sum(p.lower() == 'w' for p in state['board'])
        count_b = sum(p.lower() == 'b' for p in state['board'])
        if state['finished']:
            notice = 'Партия завершена.'
        elif state.get('chain') is not None:
            notice = 'Продолжай комбо: нажми зелёную клетку.'
        elif selected is not None:
            notice = 'Теперь нажми 🟩 для хода или взятия.'
        else:
            notice = 'Нажми свою шашку, потом 🟩 для хода.'
        return (f'Мини шашки\n\n'
                f'⚪ Белые: {count_w}  |  ⚫ Чёрные: {count_b}\n'
                f'Сейчас ходят: {who}\n'
                f'━━━━━━━━━━━━\n'
                f'{hint or notice}\n\n'
                '⚪⚫ — шашки  |  ♕♛ — дамки\n'
                '🟩 — куда ходить  |  🟨 — выбрана\n'
                'Правило «фук»: можно не бить, но тогда снимается шашка, которая могла бить.')
    if kind == 'duel':
        owner, other = state['owner'], state['chat_id']
        first = player_label(state, owner)
        second = player_label(state, other)
        top = '✨ МАГИЧЕСКАЯ ДУЭЛЬ ✨\n━━━━━━━━━━━━'
        if state['phase'] == 'invite':
            return f'{top}\n\n{first} приглашает {second} на дуэль!\n\nПринять вызов?'
        if state['phase'] == 'declined':
            return f'{top}\n\n{second} обоссал штаны, пока читал предложение о дуэли.\nВызов отклонён.'
        if state['phase'] == 'resigned':
            return f'{top}\n\n{state.get("quit_name", "Игрок")} опетушился и сдался в этой дуэли.\n🏆 Победитель: {player_label(state, state["winner"])}'
        winner = f'🏆 Победил {player_label(state, state["winner"])}!' if state['finished'] else f'Ход: {player_label(state, state["turn"])}'
        history = '\n'.join(state.get('logs', [])[-3:])
        return (f'{top}\n\n{first}: {"💙" * state["hp"][owner]}\n'
                f'{second}: {"💜" * state["hp"][other]}\n'
                f'━━━━━━━━━━━━\n{winner}\n\n{history or "Выбери магическое действие."}')
    return ''


def game_keyboard(game_id: str, state: dict) -> InlineKeyboardMarkup:
    kind = state['kind']
    if kind == 'duel':
        if state['phase'] == 'invite':
            rows = [[InlineKeyboardButton(text='✅ Принять', callback_data=f'gm:{game_id}:a'),
                     InlineKeyboardButton(text='❌ Отказаться', callback_data=f'gm:{game_id}:d')]]
        elif not state['finished']:
            rows = [[InlineKeyboardButton(text='✨ Магический залп', callback_data=f'gm:{game_id}:s')],
                    [InlineKeyboardButton(text='🏳️ Сдаться', callback_data=f'gm:{game_id}:q')]]
        else:
            rows = []
        rows.append([InlineKeyboardButton(text='🔄 Новый вызов', callback_data=f'gm:{game_id}:r')])
        return InlineKeyboardMarkup(inline_keyboard=rows)
    n = 8 if kind == 'chk' else 3
    board = state['board']
    choices = {}
    if kind == 'chk' and not state['finished']:
        choices = ce.available(board, state['turn'], state.get('chain'))
    selected = state.get('selected')
    targets = {to for to, _ in choices.get(selected, [])} if selected is not None else set()
    rows = []
    for y in range(n):
        row = []
        for x in range(n):
            idx = y*n + x
            val = board[idx]
            if kind == 'ttt':
                icon = {'X':'❌', 'O':'⭕', '':'▫️'}[val]
                if idx in state.get('winning_cells', ()): icon = '🏆'
            else:
                icon = {'w':'⚪','b':'⚫','W':'♕','B':'♛','':'▪️' if (x+y)%2 else '▫️'}[val]
                if idx in targets: icon = '🟩'
                elif idx == selected: icon = '🟨'
                # Keep the actual piece visible instead of hiding it behind a marker.
            row.append(InlineKeyboardButton(text=icon, callback_data=f'gm:{game_id}:{idx}'))
        rows.append(row)
    if kind == 'chk':
        rows.append([InlineKeyboardButton(text='📖 Правила', callback_data=f'gm:{game_id}:i')])
    else:
        rows.append([InlineKeyboardButton(text='📖 Правила', callback_data=f'gm:{game_id}:i'),
                     InlineKeyboardButton(text='🔄 Заново', callback_data=f'gm:{game_id}:r')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def play_move(state: dict, idx: int, player_id: int) -> tuple[bool, str]:
    if state['kind'] == 'chk':
        return ce.move(state, idx, player_id)
    board, turn = state['board'], state['turn']
    expected = state['owner'] if turn == 'X' else state['chat_id']
    if player_id != expected:
        return False, 'Сейчас ход другого игрока.'
    if board[idx]:
        return False, 'Клетка занята.'
    board[idx] = turn
    combos = ((0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6))
    for line in combos:
        if all(board[p] == turn for p in line):
            state['finished'] = True
            state['winning_cells'] = line
            state['score'][turn] += 1
            return True, f'🏆 Победили {"крестики" if turn == "X" else "нолики"}!'
    if all(board):
        state['finished'] = True
        state['score']['draw'] += 1
        return True, '🤝 Ничья!'
    state['turn'] = 'O' if turn == 'X' else 'X'
    return True, ('❌ Крестики' if state['turn'] == 'X' else '⭕ Нолики') + ' ходят следующими.'


def duel_action(state, action, user_id, username=None) -> tuple[bool, str]:
    """Fantasy arcade duel only. Random magic effects, no real weapons or stakes."""
    owner, opponent = state['owner'], state['chat_id']
    if username:
        state['names'][user_id] = username[:32]
    if action == 'r':
        if user_id != owner:
            return False, 'Новый вызов отправляет инициатор.'
        reset_game(state)
        return True, 'Новый вызов.'
    phase = state['phase']
    if phase == 'invite':
        if user_id != opponent:
            return False, 'Принимать или отклонять может только приглашённый игрок.'
        if action == 'd':
            state['phase'] = 'declined'
            state['finished'] = True
            return True, 'Приглашение отклонено.'
        if action == 'a':
            state['phase'] = 'playing'
            state['turn'] = owner
            return True, 'Дуэль началась!'
        return False, 'Подтверди участие.'
    if state['finished']:
        return False, 'Дуэль закончилась.'
    if action == 'q':
        state['phase'] = 'resigned'
        state['finished'] = True
        state['quit_name'] = player_label(state, user_id)
        state['winner'] = opponent if user_id == owner else owner
        return True, 'Игрок сдался.'
    if action != 's':
        return False, 'Неизвестное действие.'
    if user_id != state['turn']:
        return False, 'Подожди своего хода.'
    other = opponent if user_id == owner else owner
    if random.random() < 0.55:
        state['hp'][other] -= 1
        result = f'✨ {player_label(state, user_id)} попал магией!'
    else:
        result = f'🌫 {player_label(state, user_id)} промахнулся!'
    state['logs'].append(result)
    if state['hp'][other] <= 0:
        state['finished'] = True
        state['phase'] = 'won'
        state['winner'] = user_id
        return True, 'Игра завершена.'
    state['turn'] = other
    return True, result


async def game_callback(query: CallbackQuery, bot: Bot) -> bool:
    parts = (query.data or '').split(':')
    if len(parts) != 3 or parts[0] != 'gm':
        return False
    _, game_id, action = parts
    state = GAMES.get(game_id)
    if not state:
        await query.answer('Партия недоступна после перезапуска.', show_alert=True)
        return True
    uid = query.from_user.id
    if uid not in (state['owner'], state['chat_id']):
        await query.answer('Игра предназначена для участников чата.', show_alert=True)
        return True
    if action == 'i':
        await query.answer('Мини шашки: нажми свою шашку, затем зелёную клетку. Простая шашка ходит вперёд, бьёт в обе стороны. Бить необязательно, но если пропустить возможное взятие, одну шашку снимают («фук»). Дамки ходят по диагонали в любую сторону, комбо нужно завершить.\nКрестики-нолики: собери три в ряд.', show_alert=True)
        return True
    import copy
    before = copy.deepcopy(state)
    if state['kind'] == 'duel':
        label = ('@' + query.from_user.username) if query.from_user.username else query.from_user.full_name
        changed, info = duel_action(state, action, uid, label)
    elif action == 'r' and state['kind'] == 'chk':
        changed, info = False, 'В «Мини шашках» нет кнопки перезапуска. Для новой партии используй .chk.'
    elif action == 'r':
        if uid != state['owner'] and not state.get('finished'):
            changed, info = False, 'Начать заново может инициатор игры.'
        else:
            reset_game(state)
            changed, info = True, '🔄 Новая партия.'
    elif state.get('finished'):
        changed, info = False, 'Партия завершена.'
    elif action.isdigit() and 0 <= int(action) < len(state['board']):
        changed, info = play_move(state, int(action), uid)
    else:
        changed, info = False, 'Неверное действие.'
    if not changed:
        await query.answer(info[:180], show_alert=True)
        return True
    try:
        await bot.edit_message_text(chat_id=state['chat_id'], message_id=state['message_id'],
                                    business_connection_id=state['conn_id'],
                                    text=game_screen(state, info if state['kind'] != 'duel' else ''),
                                    reply_markup=game_keyboard(game_id, state))
    except TelegramAPIError as exc:
        state.update(before)
        log.warning('Game edit error: %s', exc)
        await query.answer('Не удалось обновить игровое сообщение.', show_alert=True)
        return True
    await query.answer(info[:180])
    return True


async def help_callback(query: CallbackQuery, bot: Bot) -> bool:
    data = query.data or ''
    if not (data.startswith('help:') or data.startswith('cmd:')):
        return False
    if data.startswith('cmd:'):
        command = data[4:]
        text = f'.{command} — {COMMAND_INFO.get(command, "Неизвестная команда")}\n\nНапиши эту команду в управляемом Business-чате.'
        keyboard = main_menu()
    else:
        i = data.split(':',1)[1]
        text, keyboard = ('Выбери категорию.', main_menu()) if i == 'root' else (category_text(int(i)) if i.isdigit() else 'Выбери категорию.', category_menu(int(i)) if i.isdigit() else main_menu())
    try:
        if query.message:
            # The Bot API accepts edits of ordinary and business bot messages.
            business_id = getattr(query.message, 'business_connection_id', None)
            kwargs = {'business_connection_id': business_id} if business_id else {}
            await bot.edit_message_text(chat_id=query.message.chat.id,
                                        message_id=query.message.message_id, text=text,
                                        reply_markup=keyboard, **kwargs)
    except TelegramAPIError:
        pass
    await query.answer()
    return True


async def process(bot: Bot, conn, chat_id: int, command_text: str,
                  message: Message | None, store, owner_sender=None) -> bool:
    """Return True if command was understood. Outbound messages are sent only via this connection."""
    line = command_text.strip()
    if not line.startswith('.'):
        return False
    name, _, args = line[1:].partition(' ')
    name = name.lower()
    args = args.strip()
    if name not in COMMAND_INFO and name != 'kawai':
        return False
    if not conn['can_reply'] and name not in ('status','time','gifts'):
        log.warning('Business connection has no reply permission; command %s cannot send', name)
        return True
    if name == 'help':
        await say(bot, conn, chat_id, 'Навигация по командам бота. Выбери категорию.', reply_markup=main_menu())
    elif name in FORMAT_MODES or name == 'kawai':
        mode = normalized_mode(name)
        if args.lower() in ('off', 'выкл', '0'):
            store.set_style(conn['id'], chat_id, '')
            await say(bot, conn, chat_id, mode_notice(''))
        elif args.lower() in ('on', 'вкл', '1', ''):
            new_mode = '' if store.style(conn['id'], chat_id) == mode else mode
            store.set_style(conn['id'], chat_id, new_mode)
            await say(bot, conn, chat_id, mode_notice(new_mode))
        else:
            # One-off conversion; does NOT switch the persistent mode.
            result = await rewrite(mode, args)
            formatted, parse = as_telegram_text(mode, result)
            await say(bot, conn, chat_id, formatted,
                      **({'parse_mode': parse} if parse else {}))
    elif name in ('style', 'styleoff'):
        if name == 'styleoff' or (args and normalized_mode(args) == ''):
            store.set_style(conn['id'], chat_id, '')
            await say(bot, conn, chat_id, mode_notice(''))
        elif args.lower() == 'status' or not args:
            current = store.style(conn['id'], chat_id)
            await say(bot, conn, chat_id, f'✦ Стиль: {current or "выключен"}\nДля отключения: .styleoff')
        else:
            mode = normalized_mode(args)
            if is_valid_mode(mode):
                new_mode = '' if store.style(conn['id'], chat_id) == mode else mode
                store.set_style(conn['id'], chat_id, new_mode)
                await say(bot, conn, chat_id, mode_notice(new_mode))
            else:
                await say(bot, conn, chat_id, 'Неизвестный стиль. Выбери его в .help.')
    elif name == 'love':
        track_task(animated_heart(bot, conn, chat_id))
    elif name == 'sparkle':
        track_task(animated_star(bot, conn, chat_id))
    elif name == 'type':
        track_task(simulated_typing(bot, conn, chat_id, args[:500]))
    elif name == 'check':
        await say(bot, conn, chat_id, metadata_for_file(message))
    elif name == 'troll':
        await say(bot, conn, chat_id, random.choice([
            'У этого диалога явно включён режим неожиданностей.',
            'Проверка на серьёзность не пройдена. Повторить?',
            'Даже чайник сказал бы: «Интересный сюжет».',
        ]))
    elif name == 'zaebu':
        await say(bot, conn, chat_id, random.choice([
            '🫶 Всё будет нормально, даже если день странный.',
            '🌷 Сегодня ты заслуживаешь паузу.',
            '✨ Официально: чат стал на одно доброе сообщение лучше.',
        ]))
    elif name == 'flip':
        await say(bot, conn, chat_id, '🪙 ' + random.choice(['Орёл!', 'Решка!']))
    elif name == 'dice':
        await send_media(bot, 'send_dice', conn, chat_id, emoji='🎲')
    elif name in ('ttt','chk','duel'):
        who = target_user(message)
        participant = ('@' + who.username if getattr(who, 'username', None) else
                       (getattr(who, 'full_name', None) or getattr(who, 'first_name', None) or 'Собеседник'))
        owner_name = ('@' + owner_sender.username if owner_sender and owner_sender.username else
                      owner_sender.first_name if owner_sender else 'Владелец')
        token, state = new_game(name, conn, chat_id, owner_name, participant)
        sent = await say(bot, conn, chat_id, game_screen(state),
                         reply_markup=game_keyboard(token, state))
        if sent:
            state['message_id'] = sent.message_id
        else:
            GAMES.pop(token, None)
    elif name in ('info','dox','doxp'):
        who = target_user(message)
        desc = public_profile(who)
        if name == 'doxp':
            try:
                await bot.send_message(conn['owner_chat_id'], desc)
            except TelegramAPIError:
                await say(bot, conn, chat_id, 'Не удалось написать владельцу. Сначала отправь /start боту.')
        else:
            await say(bot, conn, chat_id, desc)
    elif name == 'clone':
        who = target_user(message)
        response = await costume(bot, conn, chat_id, store, who)
        await say(bot, conn, chat_id, response)
    elif name == 'restore':
        response = await restore(bot, conn, store)
        await say(bot, conn, chat_id, response)
    elif name == 'short':
        messages = store.recent_messages(conn['id'], chat_id, count=8)
        await say(bot, conn, chat_id, mini_summary([r['body'] for r in messages]))
    elif name == 'nk':
        await random_neko(bot, conn, chat_id)
    elif name == 'gp':
        await say(bot, conn, chat_id, '<pre>' + html.escape(big_surprise()) + '</pre>', parse_mode='HTML')
    elif name in ('lq','story'):
        response = await photo_transform(bot, conn, chat_id, message, name)
        if response: await say(bot, conn, chat_id, response)
    elif name == 'gif':
        if photo_file(message):
            response = await photo_transform(bot, conn, chat_id, message, name)
        else:
            response = await ffmpeg_transform(bot, conn, chat_id, message, name)
        if response: await say(bot, conn, chat_id, response)
    elif name == 'fv':
        response = await ffmpeg_transform(bot, conn, chat_id, message, name)
        if response: await say(bot, conn, chat_id, response)
    elif name == 'afk':
        store.update_settings(conn['id'], afk=int(args.lower() not in ('off','выкл','0')))
        await say(bot, conn, chat_id, 'AFK ' + ('выключен.' if args.lower() in ('off','выкл','0') else 'включён.'))
    elif name == 'autoresponder':
        st = store.settings(conn['id'])
        if args.lower() in ('on','off'):
            store.update_settings(conn['id'], enabled=int(args.lower() == 'on'))
        if args and args.lower() not in ('on','off'):
            store.update_settings(conn['id'], response=args[:2000])
        st = store.settings(conn['id'])
        await say(bot, conn, chat_id, f"Автоответчик: {'включён' if st['enabled'] else 'выключен'}.\nТекст: {st['response'][:1000]}\nНастройки: /autoresponder в личке с ботом.")
    elif name == 'gifts':
        try:
            gifts = await bot.get_business_account_gifts(business_connection_id=conn['id'])
            count = getattr(gifts, 'total_count', None)
            await say(bot, conn, chat_id, f'Подарков: {count if count is not None else "данные недоступны"}.')
        except (TelegramAPIError, AttributeError) as exc:
            await say(bot, conn, chat_id, f'Для просмотра подарков нужно отдельное право в настройках подключения: {str(exc)[:160]}')
    elif name == 'status':
        if not args:
            await say(bot, conn, chat_id, 'Пример: .status Учусь')
        else:
            store.disable_clock(conn['id'])
            response = await update_name(bot, conn, args[:64])
            await say(bot, conn, chat_id, response)
    elif name == 'time':
        if args.lower() in ('off','выкл'):
            store.disable_clock(conn['id'])
            await say(bot, conn, chat_id, 'Отображение времени отключено. Текущее имя останется до ручного изменения.')
        else:
            base = (args or (owner_sender.first_name if owner_sender else 'Пользователь'))[:40]
            now = datetime.now(ZoneInfo(os.getenv('TIMEZONE', 'Europe/Kyiv'))).strftime('%H:%M')
            response = await update_name(bot, conn, f'{base} | {now}')
            if response.startswith('Имя обновлено'):
                store.enable_clock(conn['id'], base)
            await say(bot, conn, chat_id, response)
    elif name in ('mute','mutes','unmutes','unmute','antimute'):
        return False  # delegated to main.py because it owns the mute state machine
    else:
        return False
    return True


async def clock_tick(bot: Bot, store):
    """At most once each five minutes per connected user; requires edit-name right."""
    now = int(time.time())
    for row in store.clock_due(now):
        conn = store.connection(row['conn_id'])
        if not conn or not conn['enabled']:
            continue
        try:
            response = await update_name(bot, conn, f"{row['base_name']} | {datetime.now(ZoneInfo(os.getenv('TIMEZONE', 'Europe/Kyiv'))).strftime('%H:%M')}")
            if not response.startswith('Имя обновлено'):
                log.info('Clock failed for %s: %s', row['conn_id'], response)
        except Exception:
            log.exception('Clock update failed')
        finally:
            store.mark_clock(row['conn_id'], now)
