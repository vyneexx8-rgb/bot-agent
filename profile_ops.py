"""Business profile costume with a visible 🎭 mark and a restorable backup.

Works only on the connected account whose owner issued the command. Never
changes or fetches hidden account information.
"""
import io
import logging
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, InputProfilePhotoStatic

log = logging.getLogger('profile_ops')


async def avatar_jpeg(bot, user_id):
    photos = await bot.get_user_profile_photos(user_id=user_id, limit=1)
    if not photos.photos:
        return None
    best = photos.photos[0][-1]
    meta = await bot.get_file(best.file_id)
    if not meta.file_path:
        raise ValueError('Аватар недоступен для скачивания.')
    sink = io.BytesIO()
    await bot.download_file(meta.file_path, destination=sink)
    data = sink.getvalue()
    if len(data) > 8_000_000:
        raise ValueError('Аватар слишком большой для копирования.')
    # The Bot API delivers ordinary profile photos as JPEG.
    return data


async def set_avatar(bot, conn_id, data):
    if data is None:
        return await bot.remove_business_account_profile_photo(business_connection_id=conn_id)
    upload = BufferedInputFile(data, filename='profile.jpg')
    return await bot.set_business_account_profile_photo(
        business_connection_id=conn_id, photo=InputProfilePhotoStatic(photo=upload))


async def costume(bot, conn, chat_id, store, target=None):
    """Copy publicly visible name and avatar, tagging the new name as a costume."""
    try:
        remote = await bot.get_business_connection(business_connection_id=conn['id'])
        rights = remote.rights
        if not rights or not rights.can_edit_name or not rights.can_edit_profile_photo:
            return 'Разреши боту менять имя и аватар в настройках подключения.'
        who = target if target and target.id == chat_id else await bot.get_chat(chat_id)
        if not getattr(who, 'first_name', None):
            return 'Не удалось получить открытое имя собеседника.'
        other_photo = await avatar_jpeg(bot, chat_id)
        if other_photo is None:
            return 'У собеседника нет доступного аватара. Профиль не изменён.'
        previous = store.get_profile_backup(conn['id'])
        if not previous:
            original = await avatar_jpeg(bot, conn['owner_id'])
            store.save_profile_backup(conn['id'], remote.user.first_name,
                                      remote.user.last_name or '', original)
        # Explicit label avoids presenting oneself as the actual other person.
        label = '🎭 ' + who.first_name
        await bot.set_business_account_name(
            business_connection_id=conn['id'],
            first_name=label[:64], last_name=(getattr(who, 'last_name', None) or '')[:64])
        await set_avatar(bot, conn['id'], other_photo)
        store.disable_clock(conn['id'])
        return '🎭 Образ установлен. Имя и аватар изменены. Для возврата: .restore'
    except (TelegramAPIError, ValueError, AttributeError) as exc:
        log.warning('Costume could not be set: %s', type(exc).__name__)
        return 'Не удалось сменить профиль. Проверь доступ к фото, разрешения и ограничения Telegram.'


async def restore(bot, conn, store):
    old = store.get_profile_backup(conn['id'])
    if not old:
        return 'Резервной копии пока нет. Используй .clone перед изменениями.'
    try:
        await bot.set_business_account_name(
            business_connection_id=conn['id'],
            first_name=old['first_name'], last_name=old['last_name'])
        await set_avatar(bot, conn['id'], old['photo'])
        store.drop_profile_backup(conn['id'])
        return '✅ Имя и аватар восстановлены из резервной копии.'
    except TelegramAPIError as exc:
        log.warning('Restore failed: %s', type(exc).__name__)
        return 'Telegram не разрешил восстановить профиль. Копия сохранена, попробуй позже.'
