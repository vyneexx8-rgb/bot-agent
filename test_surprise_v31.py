"""Checks menu and exact user-supplied .gp art."""
import unittest
from feature_utils import COMMANDS, COMMAND_INFO, big_surprise

class SurpriseTests(unittest.TestCase):
    def test_removed_gosu(self):
        self.assertNotIn('gosu', COMMAND_INFO)
        self.assertIn('gp', COMMAND_INFO)
        self.assertNotIn('gosu', [c for section in COMMANDS.values() for c, _ in section])

    def test_art_unmodified(self):
        from pathlib import Path
        art = big_surprise()
        self.assertEqual(len(art.splitlines()), 16)
        self.assertTrue(art.startswith('_____________________________#####____'))
        self.assertIn('_________прошлым летом!!________##________', art)
        self.assertTrue(art.endswith('_______###__________###______#########__'))
        self.assertTrue(all(set(line) <= set('#_ !Язнаючто тыделалпрошлымлетом') for line in art.splitlines()))

if __name__ == '__main__':
    unittest.main()
