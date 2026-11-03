"""Dependency-free tests for the new feature command helpers."""
import types
import unittest
from feature_utils import COMMANDS, COMMAND_INFO, FORMAT_MODES, heart_frame, mini_summary, public_profile, stylize
from db import Store
import tempfile
from pathlib import Path
import time

class FeatureTests(unittest.TestCase):
    def test_every_command_listed(self):
        names = [name for group in COMMANDS.values() for name, _ in group]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), len(COMMAND_INFO))
        self.assertGreaterEqual(len(names), 37)

    def test_formatting(self):
        self.assertEqual(stylize('bold', '<Привет>'), ('<b>&lt;Привет&gt;</b>', 'HTML'))
        self.assertEqual(stylize('monospace', 'abc'), ('<code>abc</code>', 'HTML'))
        for mode in FORMAT_MODES:
            with self.subTest(mode=mode):
                result, parse = stylize(mode, 'Тест')
                self.assertTrue(result)
                self.assertIn(parse, (None, 'HTML'))

    def test_heart_progressive(self):
        self.assertEqual(heart_frame(0), '♡')
        self.assertEqual(len(heart_frame(8).splitlines()), 8)
        self.assertTrue(len(heart_frame(8)) > len(heart_frame(1)))
        self.assertNotEqual(heart_frame(8, 1), heart_frame(8, 2))

    def test_profile_data_public_only(self):
        p = public_profile(types.SimpleNamespace(full_name='Тест', username='someone', id=10))
        self.assertIn('Telegram ID: 10', p)
        self.assertIn('@someone', p)
        self.assertNotIn('Телефон: +', p)

    def test_summary_no_hallucination(self):
        self.assertIn('молоко', mini_summary(['купить молоко']))
        self.assertIn('Недостаточно', mini_summary([]))

    def test_clock_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Store(str(Path(tmp) / 'test.db'))
            s.enable_clock('b1', 'Имя')
            self.assertEqual(s.clock_due(int(time.time())), [])
            later = int(time.time()) + 302
            self.assertEqual(len(s.clock_due(later)), 1)
            s.mark_clock('b1', later)
            self.assertEqual(s.clock_due(later), [])
            s.disable_clock('b1')
            self.assertEqual(s.clock_due(later + 10000), [])
            s.db.close()

if __name__ == '__main__':
    unittest.main()
