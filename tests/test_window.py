"""独立窗口启动的测试。

不真的开窗（会打扰用户），而是验证关键契约：子进程不能被重定向输出，
否则新控制台里拿不到 tty、界面画不出来。
"""

import os
import unittest
from unittest import mock

from musiccil import window


class ChildArgvTest(unittest.TestCase):
    def test_prefers_installed_command(self):
        with mock.patch.object(window.shutil, "which", side_effect=lambda n: "/usr/bin/" + n if n == "musiccil" else None):
            argv = window._child_argv(["--file", "x.json"])
        self.assertEqual(argv, ["/usr/bin/musiccil", "--file", "x.json"])

    def test_falls_back_to_interpreter(self):
        with mock.patch.object(window.shutil, "which", return_value=None):
            argv = window._child_argv(["--version"])
        self.assertEqual(argv[1:], ["-m", "musiccil", "--version"])
        self.assertTrue(argv[0])

    def test_args_are_passed_through_untouched(self):
        with mock.patch.object(window.shutil, "which", return_value=None):
            argv = window._child_argv(["深夜 爵士", "--limit", "10"])
        self.assertIn("深夜 爵士", argv)


class MarkerTest(unittest.TestCase):
    def test_in_window_reads_env(self):
        with mock.patch.dict(os.environ, {window.MARKER_ENV: "1"}, clear=False):
            self.assertTrue(window.in_window())
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(window.in_window())

    def test_base_env_marks_child(self):
        env = window._base_env()
        self.assertEqual(env[window.MARKER_ENV], "1")


class SpawnTest(unittest.TestCase):
    """spawn 不能重定向子进程输出，否则窗口里没有 tty。"""

    def _capture_popen(self):
        captured = {}

        class FakeProc:
            pid = 4321

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return FakeProc()

        return captured, fake_popen

    def test_windows_creates_new_console_without_output_redirection(self):
        captured, fake_popen = self._capture_popen()
        with mock.patch.object(window.os, "name", "nt"), \
             mock.patch.object(window.subprocess, "Popen", fake_popen), \
             mock.patch.object(window.shutil, "which", return_value=None):
            pid = window.spawn(["--file", "p.json"], cwd="/tmp")
        self.assertEqual(pid, 4321)
        kwargs = captured["kwargs"]
        for forbidden in ("stdout", "stderr", "stdin"):
            self.assertNotIn(forbidden, kwargs, "重定向 %s 会让窗口里拿不到 tty" % forbidden)
        self.assertTrue(kwargs["creationflags"] & 0x00000010, "缺少 CREATE_NEW_CONSOLE")
        self.assertEqual(kwargs["env"][window.MARKER_ENV], "1")

    def test_temp_playlist_is_exported(self):
        captured, fake_popen = self._capture_popen()
        with mock.patch.object(window.os, "name", "nt"), \
             mock.patch.object(window.subprocess, "Popen", fake_popen), \
             mock.patch.object(window.shutil, "which", return_value=None):
            window.spawn(["--file", "/tmp/x.json"], temp_playlist="/tmp/x.json")
        self.assertEqual(captured["kwargs"]["env"][window.TEMP_PLAYLIST_ENV], "/tmp/x.json")

    def test_posix_returns_none_without_terminal(self):
        with mock.patch.object(window.os, "name", "posix"), \
             mock.patch.object(window.sys, "platform", "linux"), \
             mock.patch.object(window.shutil, "which", return_value=None):
            self.assertIsNone(window.spawn(["--file", "p.json"]))


class DecideTest(unittest.TestCase):
    """决定"要不要开窗"的规则：必须避免在后台不可见地运行。"""

    def _decide(self, *, isatty, visible, explicit=False, disabled=False, marked=False):
        env = {window.MARKER_ENV: "1"} if marked else {}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(window.sys.stdout, "isatty", return_value=isatty), \
             mock.patch.object(window, "console_is_visible", return_value=visible):
            return window.should_open_window(explicit=explicit, disabled=disabled)

    def test_pipe_opens_window(self):
        self.assertTrue(self._decide(isatty=False, visible=False))

    def test_visible_terminal_runs_in_place(self):
        self.assertFalse(self._decide(isatty=True, visible=True))

    def test_invisible_pty_still_opens_window(self):
        # agent 的 PTY：isatty 为真但窗口不可见 —— 这是最容易漏的情况
        self.assertTrue(self._decide(isatty=True, visible=False))

    def test_explicit_flag_forces_window(self):
        self.assertTrue(self._decide(isatty=True, visible=True, explicit=True))

    def test_disabled_flag_wins(self):
        self.assertFalse(self._decide(isatty=True, visible=False, disabled=True))
        self.assertFalse(self._decide(isatty=False, visible=False, disabled=True))

    def test_no_recursion_inside_window(self):
        self.assertFalse(self._decide(isatty=False, visible=False, marked=True))
        self.assertFalse(self._decide(isatty=False, visible=False, explicit=True, marked=True))

    def test_console_probe_is_safe_off_windows(self):
        with mock.patch.object(window.os, "name", "posix"):
            self.assertTrue(window.console_is_visible())


class SizeTest(unittest.TestCase):
    def test_parse_accepts_common_spellings(self):
        for text in ("56x48", "56X48", "56,48", "56 48", " 56 x 48 "):
            self.assertEqual(window.parse_size(text), (56, 48), text)

    def test_parse_rejects_garbage(self):
        for text in (None, "", "56", "56x48x60", "abc", "10x10", "56x", "x48"):
            self.assertIsNone(window.parse_size(text), text)

    def test_default_is_portrait(self):
        cols, rows = window.target_size()
        # 单元格高宽比约 1:2，所以按像素判竖屏而不是按字符数比较
        self.assertTrue(window.is_portrait(cols, rows), "默认窗口必须是竖屏")
        self.assertGreater(rows * window.CELL_ASPECT, cols)
        self.assertEqual((cols, rows), (window.DEFAULT_COLS, window.DEFAULT_ROWS))

    def test_portrait_helper_matches_measured_window(self):
        # 实测：56×48 的窗口是 571×960 像素，即竖屏
        self.assertTrue(window.is_portrait(56, 48))
        self.assertFalse(window.is_portrait(120, 30))

    def test_explicit_beats_env(self):
        with mock.patch.dict(os.environ, {window.SIZE_ENV: "64,56"}, clear=False):
            self.assertEqual(window.target_size("70x60"), (70, 60))
            self.assertEqual(window.target_size(), (64, 56))
            self.assertEqual(window.target_size("bad"), (64, 56))

    def test_default_when_nothing_set(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                window.target_size(), (window.DEFAULT_COLS, window.DEFAULT_ROWS)
            )

    def test_size_is_exported_to_child(self):
        captured = {}

        class FakeProc:
            pid = 99

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return FakeProc()

        with mock.patch.object(window.os, "name", "nt"), \
             mock.patch.object(window, "_spawn_windows_terminal", return_value=None), \
             mock.patch.object(window.subprocess, "Popen", fake_popen), \
             mock.patch.object(window.shutil, "which", return_value=None):
            window.spawn(["--file", "p.json"], size=(60, 50))
        self.assertEqual(captured["kwargs"]["env"][window.SIZE_ENV], "60,50")
        # 裸控制台回退路径必须让子进程自己调尺寸
        self.assertEqual(captured["kwargs"]["env"].get(window.APPLY_SIZE_ENV), "1")


class WindowsTerminalTest(unittest.TestCase):
    """有 wt.exe 时必须走能真正变竖屏的路径。"""

    def test_prefers_windows_terminal(self):
        captured = {}

        class FakeProc:
            pid = 7

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv
            return FakeProc()

        with mock.patch.object(window.os, "name", "nt"), \
             mock.patch.object(window.shutil, "which",
                               side_effect=lambda n: r"C:\wt.exe" if n.startswith("wt") else None), \
             mock.patch.object(window.subprocess, "Popen", fake_popen):
            pid = window.spawn(["--file", "p.json"], size=(56, 48))
        self.assertEqual(pid, 7)
        argv = captured["argv"]
        self.assertIn("--size", argv)
        self.assertIn("56,48", argv)
        # 走 wt 时不需要子进程再调尺寸
        self.assertNotIn(window.APPLY_SIZE_ENV, captured.get("env", {}))

    def test_falls_back_when_wt_missing(self):
        class FakeProc:
            pid = 8

        with mock.patch.object(window.os, "name", "nt"), \
             mock.patch.object(window.shutil, "which", return_value=None), \
             mock.patch.object(window.subprocess, "Popen", return_value=FakeProc()):
            self.assertEqual(window.spawn(["--file", "p.json"]), 8)


if __name__ == "__main__":
    unittest.main()
