"""Simple SQLite storage. Only store message text needed for deletion/editing logs."""
from __future__ import annotations
import sqlite3
import time
from pathlib import Path

class Store:
    def __init__(self, path: str = "autobot.sqlite3"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS connections (
          id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL,
          owner_chat_id INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
          can_reply INTEGER NOT NULL DEFAULT 0,
          can_delete_all INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settings (
          conn_id TEXT PRIMARY KEY,
          enabled INTEGER NOT NULL DEFAULT 0,
          response TEXT NOT NULL DEFAULT 'Сейчас не могу ответить.',
          interval_s INTEGER NOT NULL DEFAULT 3600,
          mode TEXT NOT NULL DEFAULT 'afk',
          afk INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS messages (
          conn_id TEXT NOT NULL, chat_id INTEGER NOT NULL, msg_id INTEGER NOT NULL,
          from_id INTEGER NOT NULL, name TEXT NOT NULL, username TEXT NOT NULL,
          kind TEXT NOT NULL, body TEXT NOT NULL, created INTEGER NOT NULL,
          PRIMARY KEY(conn_id,chat_id,msg_id)
        );
        CREATE TABLE IF NOT EXISTS mutes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          conn_id TEXT NOT NULL, chat_id INTEGER NOT NULL,
          owner_id INTEGER NOT NULL, expires INTEGER NOT NULL,
          notice_msg_id INTEGER,
          ended_at INTEGER,
          end_reason TEXT,
          notice_updated INTEGER NOT NULL DEFAULT 0,
          retry_at INTEGER NOT NULL DEFAULT 0,
          UNIQUE(conn_id,chat_id)
        );
        CREATE TABLE IF NOT EXISTS sticker_mutes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          conn_id TEXT NOT NULL, chat_id INTEGER NOT NULL,
          owner_id INTEGER NOT NULL, expires INTEGER NOT NULL,
          notice_msg_id INTEGER,
          ended_at INTEGER, end_reason TEXT,
          notice_updated INTEGER NOT NULL DEFAULT 0,
          retry_at INTEGER NOT NULL DEFAULT 0,
          UNIQUE(conn_id, chat_id)
        );
        CREATE TABLE IF NOT EXISTS autoreplies (
          conn_id TEXT NOT NULL, chat_id INTEGER NOT NULL, last_sent INTEGER NOT NULL,
          PRIMARY KEY(conn_id,chat_id)
        );
        CREATE TABLE IF NOT EXISTS style_modes (
          conn_id TEXT NOT NULL, chat_id INTEGER NOT NULL,
          mode TEXT NOT NULL DEFAULT '', updated_at INTEGER NOT NULL,
          PRIMARY KEY(conn_id, chat_id)
        );
        CREATE TABLE IF NOT EXISTS styled_messages (
          conn_id TEXT NOT NULL, chat_id INTEGER NOT NULL,
          msg_id INTEGER NOT NULL, created INTEGER NOT NULL,
          PRIMARY KEY(conn_id, chat_id, msg_id)
        );
        CREATE TABLE IF NOT EXISTS name_clocks (
          conn_id TEXT PRIMARY KEY,
          base_name TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 0,
          last_tick INTEGER NOT NULL DEFAULT 0
        );
        """)
        self.db.execute("""CREATE TABLE IF NOT EXISTS profile_backup (
            conn_id TEXT PRIMARY KEY, first_name TEXT NOT NULL,
            last_name TEXT NOT NULL, photo BLOB, copied INTEGER NOT NULL DEFAULT 0,
            created INTEGER NOT NULL
        )""")
        # Safe schema migration for archives created with earlier versions.
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(mutes)")}
        for column, definition in (
            ("notice_msg_id", "INTEGER"),
            ("ended_at", "INTEGER"),
            ("end_reason", "TEXT"),
            ("notice_updated", "INTEGER NOT NULL DEFAULT 0"),
            ("retry_at", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in columns:
                self.db.execute(f"ALTER TABLE mutes ADD COLUMN {column} {definition}")
        self.db.commit()

    def save_connection(self, conn) -> None:
        rights = conn.rights
        self.db.execute("""INSERT INTO connections
          (id,owner_id,owner_chat_id,enabled,can_reply,can_delete_all)
          VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
          owner_id=excluded.owner_id,owner_chat_id=excluded.owner_chat_id,
          enabled=excluded.enabled,can_reply=excluded.can_reply,
          can_delete_all=excluded.can_delete_all""",
          (conn.id, conn.user.id, conn.user_chat_id, int(conn.is_enabled),
           int(bool(rights and rights.can_reply)),
           int(bool(rights and rights.can_delete_all_messages))))
        self.db.execute("INSERT OR IGNORE INTO settings(conn_id) VALUES(?)", (conn.id,))
        self.db.commit()

    def connection(self, conn_id: str):
        return self.db.execute("SELECT * FROM connections WHERE id=?", (conn_id,)).fetchone()

    def for_owner(self, owner_id: int):
        return self.db.execute("SELECT * FROM connections WHERE owner_id=? AND enabled=1 ORDER BY rowid DESC LIMIT 1", (owner_id,)).fetchone()

    def settings(self, conn_id: str):
        return self.db.execute("SELECT * FROM settings WHERE conn_id=?", (conn_id,)).fetchone()

    def update_settings(self, conn_id: str, **kwargs) -> None:
        allowed = {"enabled", "response", "interval_s", "mode", "afk"}
        if not kwargs or not set(kwargs).issubset(allowed):
            raise ValueError("Invalid setting")
        cols = ",".join(f"{key}=?" for key in kwargs)
        self.db.execute(f"UPDATE settings SET {cols} WHERE conn_id=?", (*kwargs.values(), conn_id))
        self.db.commit()

    def save_message(self, conn_id, msg, kind, body):
        user = msg.from_user
        name = user.full_name if user else "Неизвестный"
        username = user.username if user and user.username else ""
        self.db.execute("""INSERT INTO messages
          (conn_id,chat_id,msg_id,from_id,name,username,kind,body,created)
          VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(conn_id,chat_id,msg_id)
          DO UPDATE SET kind=excluded.kind,body=excluded.body""",
          (conn_id, msg.chat.id, msg.message_id, user.id if user else 0,
           name, username, kind, body[:4000], int(time.time())))
        self.db.commit()

    def message(self, conn_id, chat_id, msg_id):
        return self.db.execute("SELECT * FROM messages WHERE conn_id=? AND chat_id=? AND msg_id=?",
                               (conn_id, chat_id, msg_id)).fetchone()

    def delete_cached_message(self, conn_id, chat_id, msg_id):
        self.db.execute("DELETE FROM messages WHERE conn_id=? AND chat_id=? AND msg_id=?",
                        (conn_id, chat_id, msg_id))
        self.db.commit()

    def set_mute(self, conn_id, chat_id, owner_id, expires) -> int:
        # Create a fresh id for each new mute: an old inline button must NOT
        # be able to cancel a newer mute in the same chat.
        self.db.execute("DELETE FROM mutes WHERE conn_id=? AND chat_id=?", (conn_id, chat_id))
        self.db.execute("""INSERT INTO mutes(conn_id,chat_id,owner_id,expires)
          VALUES(?,?,?,?)""", (conn_id, chat_id, owner_id, expires))
        self.db.commit()
        return self.db.execute("SELECT id FROM mutes WHERE conn_id=? AND chat_id=?",
                               (conn_id, chat_id)).fetchone()[0]

    def set_mute_notice(self, conn_id, chat_id, notice_msg_id):
        self.db.execute(
            "UPDATE mutes SET notice_msg_id=? WHERE conn_id=? AND chat_id=?",
            (notice_msg_id, conn_id, chat_id))
        self.db.commit()

    def mute(self, conn_id, chat_id):
        """Return active mute only; expired mutes remain for notice editing."""
        return self.db.execute("""SELECT * FROM mutes
          WHERE conn_id=? AND chat_id=? AND end_reason IS NULL AND expires>?
          """, (conn_id, chat_id, int(time.time()))).fetchone()

    def mute_any(self, conn_id, chat_id):
        return self.db.execute("SELECT * FROM mutes WHERE conn_id=? AND chat_id=?",
                               (conn_id, chat_id)).fetchone()

    def mute_by_id(self, mute_id):
        return self.db.execute("SELECT * FROM mutes WHERE id=?", (mute_id,)).fetchone()

    def end_mute(self, mute_id: int, reason: str):
        """Immediately stop the mute without throwing away its original message ID."""
        if reason not in ("manual", "expired", "replaced"):
            raise ValueError("Unknown mute ending reason")
        now = int(time.time())
        self.db.execute("""UPDATE mutes SET end_reason=?, ended_at=?
          WHERE id=? AND end_reason IS NULL""", (reason, now, mute_id))
        self.db.commit()
        return self.mute_by_id(mute_id)

    def mark_notice_updated(self, mute_id: int):
        self.db.execute("UPDATE mutes SET notice_updated=1 WHERE id=?", (mute_id,))
        self.db.commit()

    def defer_notice_update(self, mute_id: int, seconds: int = 30):
        self.db.execute("UPDATE mutes SET retry_at=? WHERE id=?",
                        (int(time.time()) + seconds, mute_id))
        self.db.commit()

    def needs_notice_update(self, now: int | None = None):
        now = int(time.time()) if now is None else now
        return self.db.execute("""SELECT * FROM mutes
          WHERE notice_updated=0 AND retry_at<=?
            AND (end_reason IS NOT NULL OR expires<=?)
          ORDER BY expires LIMIT 100""", (now, now)).fetchall()

    def unmute(self, conn_id, chat_id):
        """Legacy helper. Prefer end_mute for status updates in the shared chat."""
        self.db.execute("DELETE FROM mutes WHERE conn_id=? AND chat_id=?", (conn_id, chat_id))
        self.db.commit()

    def mutes_by_owner(self, owner_id):
        return self.db.execute("""SELECT * FROM mutes
          WHERE owner_id=? AND end_reason IS NULL AND expires>?
          ORDER BY expires LIMIT 30""", (owner_id, int(time.time()))).fetchall()

    # Sticker mutes have an independent lifecycle. They never modify regular mutes.
    def set_sticker_mute(self, conn_id, chat_id, owner_id, expires) -> int:
        self.db.execute("DELETE FROM sticker_mutes WHERE conn_id=? AND chat_id=?", (conn_id, chat_id))
        cursor = self.db.execute("""INSERT INTO sticker_mutes
          (conn_id,chat_id,owner_id,expires) VALUES(?,?,?,?)""",
          (conn_id,chat_id,owner_id,expires))
        self.db.commit()
        return cursor.lastrowid

    def set_sticker_mute_notice(self, mute_id, notice_msg_id):
        self.db.execute("UPDATE sticker_mutes SET notice_msg_id=? WHERE id=?",
                        (notice_msg_id,mute_id))
        self.db.commit()

    def sticker_mute(self, conn_id, chat_id):
        return self.db.execute("""SELECT * FROM sticker_mutes WHERE
          conn_id=? AND chat_id=? AND end_reason IS NULL AND expires>?""",
          (conn_id,chat_id,int(time.time()))).fetchone()

    def sticker_mute_any(self, conn_id, chat_id):
        return self.db.execute("SELECT * FROM sticker_mutes WHERE conn_id=? AND chat_id=?",
                               (conn_id,chat_id)).fetchone()

    def sticker_mute_by_id(self, mute_id):
        return self.db.execute("SELECT * FROM sticker_mutes WHERE id=?", (mute_id,)).fetchone()

    def end_sticker_mute(self, mute_id, reason):
        if reason not in ('manual','expired','replaced'):
            raise ValueError('Unknown sticker mute end reason')
        self.db.execute("""UPDATE sticker_mutes SET end_reason=?,ended_at=?
          WHERE id=? AND end_reason IS NULL""", (reason,int(time.time()),mute_id))
        self.db.commit()
        return self.sticker_mute_by_id(mute_id)

    def mark_sticker_notice_updated(self, mute_id):
        self.db.execute("UPDATE sticker_mutes SET notice_updated=1 WHERE id=?", (mute_id,))
        self.db.commit()

    def defer_sticker_notice_update(self, mute_id, seconds=30):
        self.db.execute("UPDATE sticker_mutes SET retry_at=? WHERE id=?",
                        (int(time.time())+seconds,mute_id))
        self.db.commit()

    def pending_sticker_notices(self, now=None):
        now = int(time.time()) if now is None else now
        return self.db.execute("""SELECT * FROM sticker_mutes WHERE
          notice_updated=0 AND retry_at<=? AND
          (end_reason IS NOT NULL OR expires<=?)
          ORDER BY expires LIMIT 100""", (now,now)).fetchall()

    def sticker_mutes_by_owner(self, owner_id):
        return self.db.execute("""SELECT * FROM sticker_mutes WHERE
          owner_id=? AND end_reason IS NULL AND expires>?
          ORDER BY expires LIMIT 30""", (owner_id,int(time.time()))).fetchall()

    def last_reply(self, conn_id, chat_id):
        row = self.db.execute("SELECT last_sent FROM autoreplies WHERE conn_id=? AND chat_id=?",
                              (conn_id, chat_id)).fetchone()
        return row[0] if row else 0

    def mark_reply(self, conn_id, chat_id):
        self.db.execute("""INSERT INTO autoreplies(conn_id,chat_id,last_sent) VALUES(?,?,?)
          ON CONFLICT(conn_id,chat_id) DO UPDATE SET last_sent=excluded.last_sent""",
                        (conn_id, chat_id, int(time.time())))
        self.db.commit()


    def recent_messages(self, conn_id: str, chat_id: int, count: int = 8):
        return list(reversed(self.db.execute("""SELECT body FROM messages
          WHERE conn_id=? AND chat_id=? ORDER BY created DESC, msg_id DESC LIMIT ?""",
          (conn_id, chat_id, count)).fetchall()))

    def enable_clock(self, conn_id: str, base_name: str):
        self.db.execute("""INSERT INTO name_clocks(conn_id,base_name,active,last_tick)
          VALUES(?,?,1,?) ON CONFLICT(conn_id) DO UPDATE SET
          base_name=excluded.base_name,active=1,last_tick=excluded.last_tick""",
          (conn_id, base_name[:40], int(time.time())))
        self.db.commit()

    def disable_clock(self, conn_id: str):
        self.db.execute("UPDATE name_clocks SET active=0 WHERE conn_id=?", (conn_id,))
        self.db.commit()

    def clock_due(self, now: int):
        return self.db.execute("""SELECT * FROM name_clocks
          WHERE active=1 AND last_tick<=? LIMIT 20""", (now-300,)).fetchall()

    def mark_clock(self, conn_id: str, now: int):
        self.db.execute("UPDATE name_clocks SET last_tick=? WHERE conn_id=?", (now, conn_id))
        self.db.commit()

    def set_style(self, conn_id: str, chat_id: int, mode: str) -> None:
        """A separate mode for each business chat. Empty mode disables styling."""
        self.db.execute("""INSERT INTO style_modes(conn_id,chat_id,mode,updated_at)
          VALUES(?,?,?,?) ON CONFLICT(conn_id,chat_id) DO UPDATE SET
          mode=excluded.mode,updated_at=excluded.updated_at""",
          (conn_id, chat_id, mode, int(time.time())))
        self.db.commit()

    def style(self, conn_id: str, chat_id: int) -> str:
        row = self.db.execute("SELECT mode FROM style_modes WHERE conn_id=? AND chat_id=?",
                              (conn_id, chat_id)).fetchone()
        return row['mode'] if row else ''

    def mark_styled(self, conn_id: str, chat_id: int, msg_id: int) -> None:
        self.db.execute("""INSERT OR IGNORE INTO styled_messages
          (conn_id,chat_id,msg_id,created) VALUES(?,?,?,?)""",
          (conn_id, chat_id, msg_id, int(time.time())))
        self.db.commit()

    def already_styled(self, conn_id: str, chat_id: int, msg_id: int) -> bool:
        return self.db.execute("""SELECT 1 FROM styled_messages WHERE
          conn_id=? AND chat_id=? AND msg_id=?""",
          (conn_id,chat_id,msg_id)).fetchone() is not None

    def get_profile_backup(self, conn_id):
        return self.db.execute("SELECT * FROM profile_backup WHERE conn_id=?", (conn_id,)).fetchone()

    def save_profile_backup(self, conn_id, first_name, last_name, photo):
        """Backup is one-time until restored; never overwrite the original snapshot."""
        self.db.execute("""INSERT OR IGNORE INTO profile_backup
            (conn_id,first_name,last_name,photo,created) VALUES(?,?,?,?,?)""",
            (conn_id, first_name[:64], (last_name or '')[:64], photo, int(time.time())))
        self.db.commit()

    def drop_profile_backup(self, conn_id):
        self.db.execute("DELETE FROM profile_backup WHERE conn_id=?", (conn_id,))
        self.db.commit()

    def cleanup(self, retention_days: int = 7):
        cutoff = int(time.time()) - retention_days * 86400
        self.db.execute("DELETE FROM messages WHERE created < ?", (cutoff,))
        self.db.execute("DELETE FROM styled_messages WHERE created < ?", (cutoff,))
        # Keep expired mutes until we edit the original notice in the shared chat.
        # Completed mute records are retained briefly so old buttons cannot
        # trigger another action; old or undeliverable ones are pruned later.
        self.db.execute("""DELETE FROM mutes WHERE ended_at IS NOT NULL
          AND ended_at < ?""", (cutoff,))
        self.db.execute("""DELETE FROM sticker_mutes WHERE ended_at IS NOT NULL
          AND ended_at < ?""", (cutoff,))
        self.db.commit()
