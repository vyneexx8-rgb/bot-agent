"""Pure tests for the multi-line animated star (no Telegram network needed)."""
import unittest
from feature_utils import STAR_ROWS, STAR_COLORS, star_frame

class StarAnimationTests(unittest.TestCase):
    def test_star_is_big_and_symmetric(self):
        self.assertGreaterEqual(len(STAR_ROWS), 10)
        self.assertEqual({len(row) for row in STAR_ROWS}, {11})
        for row in STAR_ROWS:
            self.assertEqual(row, row[::-1])
        self.assertTrue(all('X' in row for row in STAR_ROWS))
        self.assertGreater(STAR_ROWS[4].count('X'), STAR_ROWS[0].count('X'))
        self.assertLess(STAR_ROWS[-1].count('X'), STAR_ROWS[4].count('X'))

    def test_growth_top_to_bottom(self):
        self.assertEqual(star_frame(0), '✦')
        self.assertEqual(len(star_frame(3).splitlines()), 3)
        self.assertEqual(len(star_frame(8).splitlines()), 8)
        self.assertEqual(len(star_frame(500).splitlines()), len(STAR_ROWS))
        self.assertNotEqual(star_frame(5), star_frame(8))

    def test_colours_and_shimmer(self):
        full = star_frame(len(STAR_ROWS), 0)
        self.assertTrue(any(colour in full for colour in STAR_COLORS))
        self.assertIn('✨', full)
        self.assertNotEqual(full, star_frame(len(STAR_ROWS), 1))
        self.assertGreater(len(set(star_frame(len(STAR_ROWS), phase) for phase in range(6))), 4)
        self.assertEqual(len(full.splitlines()), len(STAR_ROWS))

if __name__ == '__main__':
    unittest.main()
