"""Verify expiry and manual unmute edit the same notice rather than posting anew."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

try:
    import main
except ImportError:
    main = None

@unittest.skipIf(main is None, 'Install requirements first')
class MuteNoticeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_store = main.store
        main.store = main.Store(str(Path(self.temp.name) / 'test.sqlite3'))
        self.bot = AsyncMock()
        self.bot.edit_message_text.return_value = True

    async def asyncTearDown(self):
        main.store.db.close()
        main.store = self.old_store
        self.temp.cleanup()

    def create_mute(self, duration):
        mute_id = main.store.set_mute('business-1', 101, 202, int(time.time()) + duration)
        main.store.set_mute_notice('business-1', 101, 303)
        return mute_id

    async def test_manual_edits_original(self):
        mute_id = self.create_mute(200)
        result = await main.finish_mute(self.bot, mute_id, 'manual', 'Автор')
        self.assertEqual(result, (True, True))
        self.bot.edit_message_text.assert_awaited_once()
        kwargs = self.bot.edit_message_text.await_args.kwargs
        self.assertEqual(kwargs['chat_id'], 101)
        self.assertEqual(kwargs['message_id'], 303)
        self.assertEqual(kwargs['business_connection_id'], 'business-1')
        self.assertIsNone(kwargs['reply_markup'])
        self.assertIn('снял(а) мут', kwargs['text'])
        self.bot.send_message.assert_not_awaited()

    async def test_expiry_edits_original(self):
        self.create_mute(-1)
        await main.process_expired_mutes(self.bot)
        kwargs = self.bot.edit_message_text.await_args.kwargs
        self.assertEqual(kwargs['message_id'], 303)
        self.assertIn('Срок мута истёк', kwargs['text'])
        self.assertIsNone(kwargs['reply_markup'])
        self.bot.send_message.assert_not_awaited()
