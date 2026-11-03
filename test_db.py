"""DB tests for mute lifecycle, expiry and preserving notice IDs."""
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from db import Store


class DatabaseMuteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Store(str(Path(self.tmp.name) / "test.sqlite3"))

    def tearDown(self):
        self.db.db.close()
        self.tmp.cleanup()

    def test_old_inline_buttons_cannot_unmute_new_mute(self):
        end = int(time.time()) + 3600
        old_id = self.db.set_mute("connection", 111, 222, end)
        new_id = self.db.set_mute("connection", 111, 222, end)
        self.assertNotEqual(old_id, new_id)
        self.assertIsNone(self.db.mute_by_id(old_id))
        self.assertEqual(self.db.mute_by_id(new_id)["owner_id"], 222)
        self.assertIsNotNone(self.db.mute("connection", 111))

    def test_expired_mute_is_inactive_but_waits_for_edit(self):
        now = int(time.time())
        mute_id = self.db.set_mute("con", 111, 222, now - 1)
        self.db.set_mute_notice("con", 111, 456)
        self.assertIsNone(self.db.mute("con", 111))
        pending = self.db.needs_notice_update(now)
        self.assertEqual([row["id"] for row in pending], [mute_id])
        ended = self.db.end_mute(mute_id, "expired")
        self.assertEqual(ended["notice_msg_id"], 456)
        self.assertEqual(ended["end_reason"], "expired")
        self.db.cleanup()
        self.assertEqual(self.db.mute_by_id(mute_id)["notice_msg_id"], 456)

    def test_manually_ended_mute_is_not_active_and_edit_is_queued(self):
        now = int(time.time())
        mute_id = self.db.set_mute("con", 111, 222, now + 100)
        self.db.set_mute_notice("con", 111, 55)
        self.db.end_mute(mute_id, "manual")
        self.assertIsNone(self.db.mute("con", 111))
        self.assertEqual(self.db.mutes_by_owner(222), [])
        self.assertEqual(self.db.needs_notice_update(now)[0]["id"], mute_id)
        self.db.mark_notice_updated(mute_id)
        self.assertEqual(self.db.needs_notice_update(now), [])

    def test_retry_failed_edit_without_reactivating_mute(self):
        now = int(time.time())
        with patch("db.time.time", return_value=now):
            mute_id = self.db.set_mute("con", 111, 222, now + 100)
            self.db.end_mute(mute_id, "manual")
            self.db.defer_notice_update(mute_id, 30)
            self.assertEqual(self.db.needs_notice_update(now), [])
            self.assertEqual(len(self.db.needs_notice_update(now + 30)), 1)
            self.assertIsNone(self.db.mute("con", 111))

    def test_migrate_legacy_mutes_schema(self):
        self.db.db.close()
        path = Path(self.tmp.name) / "test.sqlite3"
        conn = sqlite3.connect(path)
        conn.execute("DROP TABLE mutes")
        conn.execute("""CREATE TABLE mutes (
          id INTEGER PRIMARY KEY AUTOINCREMENT, conn_id TEXT NOT NULL,
          chat_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
          expires INTEGER NOT NULL, notice_msg_id INTEGER,
          UNIQUE(conn_id,chat_id))""")
        conn.execute("INSERT INTO mutes(conn_id, chat_id, owner_id, expires, notice_msg_id) VALUES(?,?,?,?,?)",
                     ("con", 111, 222, int(time.time()) + 50, 444))
        conn.commit()
        conn.close()
        self.db = Store(str(path))
        self.assertEqual(self.db.mute("con", 111)["notice_msg_id"], 444)
        self.assertIsNone(self.db.mute("con", 111)["end_reason"])


if __name__ == "__main__":
    unittest.main()
