"""Offline style, persistence, and heart checks; never contacts Groq."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_styles import STYLE_PROMPTS, as_telegram_text, mode_notice, normalized_mode, protect_result, rewrite
from db import Store
from feature_utils import HEART_ROWS, FORMAT_MODES, heart_frame


class AIStyleTests(unittest.TestCase):
    def test_prompts_all_modes(self):
        self.assertEqual(set(STYLE_PROMPTS), set(FORMAT_MODES))
        self.assertTrue(all(len(prompt) > 20 for prompt in STYLE_PROMPTS.values()))

    def test_alias_and_notices(self):
        self.assertEqual(normalized_mode('kawai'), 'kawaii')
        self.assertEqual(normalized_mode('.kawaii'), 'kawaii')
        self.assertEqual(normalized_mode('off'), '')
        self.assertIn('Милый режим включён', mode_notice('kawaii'))

    def test_safe_markup(self):
        text, parse = as_telegram_text('bold', '<script>&')
        self.assertEqual(text, '<b>&lt;script&gt;&amp;</b>')
        self.assertEqual(parse, 'HTML')

    def test_empty_oversize_model_output(self):
        self.assertEqual(protect_result('', 'привет'), 'привет')
        self.assertEqual(protect_result('X'*5000, 'привет'), 'привет')

    def test_no_key_local_fallback(self):
        with patch.dict(os.environ, {'GROQ_API_KEY': ''}):
            self.assertEqual(asyncio.run(rewrite('bold', 'привет')), 'привет')
            self.assertIn('привет', asyncio.run(rewrite('kawaii', 'привет')))

    def test_db_mode_is_per_chat_and_persistent(self):
        with tempfile.TemporaryDirectory() as t:
            path = str(Path(t) / 'db.sqlite3')
            db = Store(path)
            db.set_style('conn', 111, 'kawaii')
            db.set_style('conn', 222, 'bold')
            db.mark_styled('conn', 111, 90)
            db.db.close()
            again = Store(path)
            self.assertEqual(again.style('conn', 111), 'kawaii')
            self.assertEqual(again.style('conn', 222), 'bold')
            self.assertTrue(again.already_styled('conn', 111, 90))
            self.assertFalse(again.already_styled('conn', 222, 90))
            again.set_style('conn', 111, '')
            self.assertEqual(again.style('conn', 111), '')
            again.db.close()

    def test_heart_is_symmetric_bitmap(self):
        self.assertEqual({len(row) for row in HEART_ROWS}, {11})
        self.assertEqual(len(HEART_ROWS), 9)
        for row in HEART_ROWS:
            self.assertEqual(row, row[::-1])
        self.assertIn('██   ██', HEART_ROWS[0])
        self.assertEqual(HEART_ROWS[-1].count('█'), 1)
        self.assertEqual(len(heart_frame(9).splitlines()), 9)

if __name__ == '__main__':
    unittest.main()
