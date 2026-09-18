"""mpv 控制层测试：全部静音运行，不会发出声音。"""

import unittest

from musiccil.player import Mpv, find_mpv

HAVE_MPV = find_mpv() is not None


@unittest.skipUnless(HAVE_MPV, "未安装 mpv")
class MpvTest(unittest.TestCase):
    def setUp(self):
        self.mpv = Mpv(volume=0, muted=True)

    def tearDown(self):
        self.mpv.quit()

    def test_starts_idle_and_connected(self):
        self.assertTrue(self.mpv.idle)
        self.assertTrue(self.mpv.muted)
        self.assertEqual(self.mpv.volume, 0)

    def test_volume_clamped(self):
        self.assertEqual(self.mpv.set_volume(150), 100)
        self.assertEqual(self.mpv.set_volume(-20), 0)
        self.assertEqual(self.mpv.adjust_volume(30), 30)

    def test_pause_toggle(self):
        self.mpv.set_paused(False)
        self.assertTrue(self.mpv.toggle_pause())
        self.assertFalse(self.mpv.toggle_pause())

    def test_bad_url_reports_error_and_does_not_hang(self):
        self.mpv.load("https://example.invalid/never.mp3")
        for _ in range(60):
            self.mpv.poll()
            if self.mpv.last_file_error():
                break
            import time

            time.sleep(0.1)
        self.assertIsNotNone(self.mpv.last_file_error())
        self.assertTrue(self.mpv.idle)

    def test_missing_source_raises(self):
        with self.assertRaises(Exception):
            Mpv(mpv_path="/definitely/not/mpv")


if __name__ == "__main__":
    unittest.main()
