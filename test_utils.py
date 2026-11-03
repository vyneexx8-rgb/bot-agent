import unittest
from utils import parse_duration, human_duration, safe_text

class UtilsTests(unittest.TestCase):
    def test_cyrillic_and_latin_time(self):
        self.assertEqual(parse_duration('1ч'), 3600)
        self.assertEqual(parse_duration('30m'), 1800)
        self.assertEqual(parse_duration('2д'), 172800)
        self.assertEqual(parse_duration('3 часа'), 10800)
        self.assertEqual(parse_duration('1 день'), 86400)
        self.assertEqual(parse_duration('2 hours'), 7200)
        self.assertEqual(parse_duration('15min'), 900)
        self.assertEqual(parse_duration('2 минуты 30 секунд'), 150)
        self.assertEqual(parse_duration('1h30m'), 5400)
        self.assertEqual(parse_duration('5ч 5м'), 18300)
    def test_invalid(self):
        for x in ['0m', '500д', 'bad', '5months', '2h garbage', '3', '2м 5']:
            with self.assertRaises(ValueError): parse_duration(x)
    def test_human(self):
        self.assertEqual(human_duration(3600), '1 ч.')
    def test_html(self):
        self.assertEqual(safe_text('<test>'), '&lt;test&gt;')

if __name__ == '__main__': unittest.main()
