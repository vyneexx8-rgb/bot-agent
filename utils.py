"""Small, dependency-free helpers for the Business bot."""
from __future__ import annotations
import html
import re

MAX_MUTE_SECONDS = 30 * 24 * 60 * 60


DURATION_UNITS = {
    # English
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    # Русский
    "с": 1, "сек": 1, "секунда": 1, "секунду": 1, "секунды": 1, "секунд": 1,
    "м": 60, "мин": 60, "минута": 60, "минуту": 60,
    "минуты": 60, "минут": 60,
    "ч": 3600, "час": 3600, "часа": 3600, "часов": 3600,
    "д": 86400, "день": 86400, "дня": 86400, "дней": 86400,
    "сутки": 86400, "суток": 86400,
}


def parse_duration(raw: str) -> int:
    """Parse Russian/English time, e.g. 1ч, 2 hours, 1h30m, 2 часа 15 мин.

    Requires an explicit unit for EVERY number; never silently accepts months.
    Duration is capped at 30 days, independently of which mute command uses it.
    """
    source = raw.strip().casefold()
    if not source:
        raise ValueError("Укажи время: 30м, 2ч, 1д, 30min, 2hours, 1day.")
    total = 0
    position = 0
    count = 0
    while position < len(source):
        match = re.match(r"\s*(\d+)\s*([a-zа-яё]+)", source[position:])
        if not match:
            raise ValueError("Формат времени: 30м, 2 часа, 1h30m, 2 hours, 1 day.")
        unit = match.group(2)
        if unit not in DURATION_UNITS:
            raise ValueError("Неизвестная единица времени. Используй секунды, минуты, часы или дни (RU/EN).")
        total += int(match.group(1)) * DURATION_UNITS[unit]
        count += 1
        position += match.end()
        if count > 8:
            raise ValueError("Слишком много частей в записи времени.")
        if source[position:].isspace():
            position = len(source)
    if not (1 <= total <= MAX_MUTE_SECONDS):
        raise ValueError("Длительность мута: от 1 секунды до 30 дней.")
    return total


def human_duration(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400} д."
    if seconds % 3600 == 0:
        return f"{seconds // 3600} ч."
    if seconds % 60 == 0:
        return f"{seconds // 60} мин."
    return f"{seconds} сек."


def safe_text(value: str | None, limit: int = 1600) -> str:
    if value is None or value == "":
        value = "(нет текста)"
    if len(value) > limit:
        value = value[:limit] + "…"
    return html.escape(value)


def content(msg) -> tuple[str, str]:
    """Return (human-readable type, cached text or caption). No media stored."""
    if msg.text is not None:
        return ("текст", msg.text)
    for kind, attr in (
        ("фото", "photo"), ("видео", "video"), ("голосовое", "voice"),
        ("документ", "document"), ("анимация", "animation"),
        ("стикер", "sticker"), ("аудио", "audio"),
    ):
        if getattr(msg, attr, None):
            return (kind, msg.caption or f"[{kind}]")
    return ("медиа/сообщение", msg.caption or "[без текста]")
