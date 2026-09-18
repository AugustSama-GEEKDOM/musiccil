import unittest

from musiccil.lrc import index_at, parse


class ParseTest(unittest.TestCase):
    def test_basic_timestamps(self):
        lines = parse("[00:01.50]第一句\n[00:12.00]第二句")
        self.assertEqual([t for t, _ in lines], [1.5, 12.0])
        self.assertEqual(lines[0][1], ["第一句"])

    def test_multiple_stamps_on_one_line(self):
        lines = parse("[00:05.00][01:05.00]副歌")
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0][1], ["副歌"])
        self.assertEqual(lines[1][0], 65.0)

    def test_translation_lines_merge(self):
        lines = parse("[00:05.00]Hello\n[00:05.00]你好")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0][1], ["Hello", "你好"])

    def test_offset_tag(self):
        lines = parse("[offset:+500]\n[00:10.00]歌词")
        self.assertAlmostEqual(lines[0][0], 9.5, places=3)

    def test_metadata_tags_ignored(self):
        lines = parse("[ti:标题]\n[ar:歌手]\n[00:01.00]正文")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0][1], ["正文"])

    def test_garbage_input(self):
        self.assertEqual(parse(""), [])
        self.assertEqual(parse("没有时间戳的纯文本"), [])
        self.assertEqual(parse("[00:01.00]"), [])

    def test_unsorted_input_is_ordered(self):
        lines = parse("[00:30.00]后\n[00:10.00]前")
        self.assertEqual([t for t, _ in lines], [10.0, 30.0])

    def test_two_digit_hundredths(self):
        lines = parse("[00:03.35]词")
        self.assertAlmostEqual(lines[0][0], 3.35, places=3)


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.lines = parse("[00:00.00]a\n[00:10.00]b\n[00:20.00]c")

    def test_before_first_line(self):
        self.assertEqual(index_at(parse("[00:10.00]a"), 5.0), -1)

    def test_exact_and_between(self):
        self.assertEqual(index_at(self.lines, 0.0), 0)
        self.assertEqual(index_at(self.lines, 9.99), 0)
        self.assertEqual(index_at(self.lines, 10.0), 1)
        self.assertEqual(index_at(self.lines, 15.0), 1)
        self.assertEqual(index_at(self.lines, 999.0), 2)

    def test_empty(self):
        self.assertEqual(index_at([], 10.0), -1)


if __name__ == "__main__":
    unittest.main()
