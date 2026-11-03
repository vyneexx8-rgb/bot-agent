"""Pure helpers for optional Telegram Business commands."""
import random
import re
import html

COMMANDS = {
    'Сообщения': [
        ('check', 'Сведения о файле (не антивирус)'), ('help', 'Кнопочное меню'),
        ('love', 'Анимированное разноцветное сердце'), ('sparkle', 'Анимация сияющей звезды'), ('mute', 'Мут собеседника'),
        ('mutes', 'Мут только стикеров: .mutes 2ч'),
        ('unmutes', 'Снять мут стикеров'),
        ('unmute', 'Снять свой мут'), ('antimute', 'Снять свой мут'),
        ('troll', 'Безобидная шутка'), ('type', 'Показать набор текста'),
        ('zaebu', 'Случайная добрая фраза'),
    ],
    'Форматирование': [
        ('bold', 'Режим жирного текста'), ('italic', 'Режим курсива'),
        ('monospace', 'Моноширинный режим'), ('underline', 'Режим подчёркивания'),
        ('kawaii', 'Милый режим (.kawai тоже работает)'),
        ('tsundere', 'Стиль tsundere'), ('yandere', 'Стиль yandere'),
        ('styleoff', 'Отключить активный стиль'), ('style', 'Текущий стиль или его выключение'),
    ],
    'Игры': [
        ('dice', 'Бросить кубик'),
        ('duel', 'Аркадная магическая дуэль на двоих'), ('flip', 'Монетка'),
        ('chk', 'Мини шашки'), ('ttt', 'Крестики-нолики'),
    ],
    'Обработка': [
        ('clone', 'Зеркальный образ с меткой 🎭 и бэкапом'), ('restore', 'Восстановить имя и фото из бэкапа'), ('dox', 'Доступная информация о пользователе'),
        ('doxp', 'Те же данные в личку владельцу'), ('fv', 'Сделать голосовое громче'),
        ('afk', 'Режим AFK'), ('autoresponder', 'Автоответчик'),
        ('gifts', 'Количество подарков, если есть право'),
        ('info', 'Доступная информация о пользователе'),
        ('nk', 'Случайная иллюстрация неко-тян'), ('short', 'Краткая выжимка доступных сообщений'),
        ('status', 'Поменять своё имя (требуется разрешение)'),
        ('story', 'Разделить фотографию на 3 части'),
        ('time', 'Добавить время в имя (раз в 5 минут)'),
        ('gif', 'Перевести медиа в GIF'),
    ],
    'Остальное': [
        ('gp', 'Большой сюрприз'),
        ('lq', 'Ухудшить качество фото'),
    ],
}

COMMAND_INFO = {command: description for items in COMMANDS.values() for command, description in items}
FORMAT_MODES = ('bold', 'italic', 'monospace', 'underline', 'kawaii', 'tsundere', 'yandere')


def stylize(mode: str, value: str) -> tuple[str, str | None]:
    """Returns (formatted text, HTML parse mode or None)."""
    text = value.strip()[:1800]
    if not text:
        raise ValueError(f'Пример: .{mode} Привет')
    escaped = html.escape(text)
    if mode in ('bold', 'italic', 'monospace', 'underline'):
        tag = {'bold': 'b', 'italic': 'i', 'monospace': 'code', 'underline': 'u'}[mode]
        return (f'<{tag}>{escaped}</{tag}>', 'HTML')
    if mode == 'kawaii':
        return (f'ฅ^•ﻌ•^ฅ  {text}  ✿', None)
    if mode == 'tsundere':
        return (f'Хм... {text}. И не думай, что я специально стараюсь!', None)
    if mode == 'yandere':
        return (f'✦ {text} ✦  Это звучит очень драматично.', None)
    return (text, None)


HEART_ROWS = [
    '  ██   ██  ',
    ' ████ ████ ',
    '███████████',
    '███████████',
    ' █████████ ',
    '  ███████  ',
    '   █████   ',
    '    ███    ',
    '     █     ',
]
HEART_COLORS = ['❤️', '🧡', '💛', '💚', '💙', '💜', '🩷']


def heart_frame(rows: int, offset: int = 0) -> str:
    """Symmetrical 11-cell heart, padded with fullwidth blanks for emoji width."""
    output = []
    for y, row in enumerate(HEART_ROWS[:max(0, min(rows, len(HEART_ROWS)))]):
        output.append(''.join(HEART_COLORS[(idx + y + offset) % len(HEART_COLORS)]
                              if char == '█' else '\u3000' for idx, char in enumerate(row)))
    return '\n'.join(output) or '♡' 


# 11 columns fit comfortably in most Telegram mobile chat bubbles.
# X marks a coloured cell; spaces are filled with full-width blanks.
# The outer edges form a complete five-pointed star, not a one-line effect.
STAR_ROWS = [
    '     X     ',
    '     X     ',
    '    XXX    ',
    '    XXX    ',
    'XXXXXXXXXXX',
    ' XXXXXXXXX ',
    '  XXXXXXX  ',
    '   XXXXX   ',
    '   XXXXX   ',
    '  XX   XX  ',
    ' XX     XX ',
    ' X       X ',
]
STAR_COLORS = ('💜', '💙', '🩵', '💚', '💛', '🧡', '🩷')
STAR_GLOW_POINTS = ((0, 0), (0, 10), (2, 1), (2, 9),
                    (8, 0), (8, 10), (11, 0), (11, 10))


def star_frame(rows: int, phase: int = 0) -> str:
    """Big, colourful, symmetric five-point star growing top-to-bottom.

    Once complete it sparkles: several pixels change colour and tiny glints
    twinkle around it.  Each frame is returned as plain text (for <pre>).
    No extra messages are needed; the same Telegram message is edited.
    """
    shown = max(0, min(rows, len(STAR_ROWS)))
    if not shown:
        return '✦'
    finished = shown == len(STAR_ROWS)
    output = []
    for y, mask in enumerate(STAR_ROWS[:shown]):
        cells = []
        for x, mark in enumerate(mask):
            if mark == 'X':
                # Symmetric rainbow shading. When complete, the colours pulse.
                distance = abs(x - 5) + abs(y - 5) // 2
                colour = STAR_COLORS[(distance + phase) % len(STAR_COLORS)]
                if finished and (x + 2 * y + phase * 3) % 19 == 0:
                    colour = '✨'
                cells.append(colour)
            elif finished and (y, x) in STAR_GLOW_POINTS and (x + y + phase) % 3 == 0:
                cells.append('✨')
            else:
                cells.append('　')
        output.append(''.join(cells))
    return '\n'.join(output)


def public_profile(user) -> str:
    if user is None:
        return 'Нет доступных сведений.'
    name = getattr(user, 'full_name', None) or 'Неизвестно'
    username = getattr(user, 'username', None)
    uid = getattr(user, 'id', None)
    return (f'Имя: {name}\nЮзернейм: {"@" + username if username else "не задан"}\n'
            f'Telegram ID: {uid if uid is not None else "неизвестен"}\n'
            '')


def mini_summary(messages: list[str]) -> str:
    clean = [re.sub(r'\s+', ' ', t).strip() for t in messages if t and t.strip()]
    if not clean:
        return 'Недостаточно сообщений для пересказа.'
    excerpts = [s[:170] + ('…' if len(s) > 170 else '') for s in clean[-6:]]
    return 'Краткая выжимка из доступных сообщений:\n' + '\n'.join(f'• {s}' for s in excerpts)


# Моноширинный рисунок для команды .gp, без случайного текста.
BIG_SURPRISE = '_____________________________#####____\n_________ Я знаю _____________#####____\n___________что ты делал _______####______\n_________прошлым летом!!________##________\n_____________________________######_____\n____________________________#######_____\n____####__________________#########___\n___######________________###_######___\n#########_______________###__######__\n_#######_______________###___######___\n_______#################____######__\n_______##########################__\n________################____#####____\n_________##___________##_____####______\n_________##___________##_____#########___\n_______###__________###______#########__'


def big_surprise() -> str:
    """Return the user-supplied ASCII image unchanged."""
    return BIG_SURPRISE
