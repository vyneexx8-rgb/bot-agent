"""Independent sticker mutes, English/Russian times and notice lifecycle."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from db import Store
from utils import parse_duration

try:
    import main
except ImportError:
    main = None


class StickerMuteDBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.tmp.name) / 'db.sqlite3'))

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_separate_from_normal_mute_and_expires(self):
        now = int(time.time())
        regular = self.store.set_mute('conn', 17, 3, now+300)
        sticker = self.store.set_sticker_mute('conn', 17, 3, now+5)
        self.assertNotEqual(self.store.mute_by_id(regular), None)
        self.assertIsNotNone(self.store.sticker_mute_by_id(sticker))
        self.assertEqual(self.store.mute('conn', 17)['id'], regular)
        self.assertEqual(self.store.sticker_mute('conn', 17)['id'], sticker)
        self.store.end_sticker_mute(sticker, 'manual')
        self.assertIsNone(self.store.sticker_mute('conn', 17))
        self.assertIsNotNone(self.store.mute('conn', 17))

    def test_expired_not_active_and_waiting_to_edit(self):
        now = int(time.time())
        mid = self.store.set_sticker_mute('conn', 17, 3, now-1)
        self.store.set_sticker_mute_notice(mid, 900)
        self.assertIsNone(self.store.sticker_mute('conn', 17))
        self.assertEqual([row['id'] for row in self.store.pending_sticker_notices(now)], [mid])
        self.store.end_sticker_mute(mid,'expired')
        self.assertEqual(self.store.sticker_mute_by_id(mid)['notice_msg_id'], 900)
        self.store.mark_sticker_notice_updated(mid)
        self.assertEqual(self.store.pending_sticker_notices(now), [])

    def test_old_button_cannot_affect_new_sticker_mute(self):
        now=int(time.time())
        old=self.store.set_sticker_mute('c',1,2,now+100)
        new=self.store.set_sticker_mute('c',1,2,now+200)
        self.assertNotEqual(old,new)
        self.assertIsNone(self.store.sticker_mute_by_id(old))
        self.assertIsNotNone(self.store.sticker_mute_by_id(new))

    def test_time_aliases(self):
        self.assertEqual(parse_duration('1 час 30 минут'), 5400)
        self.assertEqual(parse_duration('1hour30minutes'), 5400)
        self.assertEqual(parse_duration('2 days'), 172800)
        self.assertEqual(parse_duration('1сутки'), 86400)


@unittest.skipIf(main is None, 'Requires aiogram, install requirements')
class StickerMuteAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_store = main.store
        main.store = Store(str(Path(self.tmp.name) / 'db.sqlite3'))
        self.bot = AsyncMock()
        self.bot.send_message.return_value.message_id = 501

    async def asyncTearDown(self):
        main.store.db.close()
        main.store = self.old_store
        self.tmp.cleanup()

    async def test_notice_and_manual_unmute_in_place(self):
        conn={'id':'conn','owner_id':200,'can_delete_all':1}
        mute_id, delivered = await main.set_sticker_mute(self.bot, conn, 12, 60)
        self.assertTrue(delivered)
        self.assertEqual(main.store.sticker_mute_by_id(mute_id)['notice_msg_id'], 501)
        keyboard = self.bot.send_message.await_args.kwargs['reply_markup']
        self.assertEqual(keyboard.inline_keyboard[0][0].callback_data, f'unmutes:{mute_id}')
        removed, updated=await main.finish_sticker_mute(self.bot, mute_id,'manual','Автор')
        self.assertEqual((removed,updated),(True,True))
        kwargs=self.bot.edit_message_text.await_args.kwargs
        self.assertEqual(kwargs['message_id'], 501)
        self.assertIn('снял(а) мут стикеров', kwargs['text'])
        self.assertIsNone(kwargs['reply_markup'])
        self.assertEqual(self.bot.send_message.await_count,1)

    async def test_expiry_edits_same_message(self):
        mid=main.store.set_sticker_mute('conn',12,200,int(time.time())-5)
        main.store.set_sticker_mute_notice(mid,502)
        await main.process_expired_sticker_mutes(self.bot)
        kwargs=self.bot.edit_message_text.await_args.kwargs
        self.assertEqual(kwargs['message_id'],502)
        self.assertIn('истёк',kwargs['text'])
        self.bot.send_message.assert_not_awaited()
