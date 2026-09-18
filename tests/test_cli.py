"""命令行路由测试：只覆盖不联网的分支（曲目来源解析与退出码）。"""

import json
import os
import tempfile
import unittest
from unittest import mock

from musiccil import cli


def write(path, payload):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)


class ArgumentTest(unittest.TestCase):
    def setUp(self):
        self.parser = cli.build_parser()

    def test_playlist_flag_maps_to_playlist_id(self):
        args = self.parser.parse_args(["--list", "149553"])
        self.assertEqual(args.playlist_id, "149553")
        self.assertFalse(hasattr(args, "list"))

    def test_toplist_flag_maps_to_toplist_id(self):
        args = self.parser.parse_args(["--toplist", "3778678"])
        self.assertEqual(args.toplist_id, "3778678")

    def test_defaults(self):
        args = self.parser.parse_args(["加州梦游"])
        self.assertEqual(args.platform, "wy")
        self.assertEqual(args.limit, 10)
        self.assertEqual(args.volume, 80)
        self.assertFalse(args.search)
        self.assertEqual(args.query, ["加州梦游"])

    def test_multiword_query_joins(self):
        args = self.parser.parse_args(["深夜", "爵士"])
        self.assertEqual(" ".join(args.query), "深夜 爵士")


class SourceResolutionTest(unittest.TestCase):
    """这些分支不触发网络：本地文件/目录/m3u/JSON/直链。"""

    def setUp(self):
        self.api = type("FakeApi", (), {"search": lambda *a, **k: []})()
        self.parser = cli.build_parser()

    def test_local_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "歌.mp3")
            with open(path, "wb") as fh:
                fh.write(b"\x00" * 32)
            args = self.parser.parse_args([path])
            tracks = cli.collect_from_arguments(args, self.api)
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0].name, "歌")

    def test_directory_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("a.mp3", "b.flac", "c.txt"):
                with open(os.path.join(tmp, name), "wb") as fh:
                    fh.write(b"\x00" * 16)
            args = self.parser.parse_args([tmp])
            self.assertEqual(len(cli.collect_from_arguments(args, self.api)), 2)

    def test_m3u(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.mp3"), "wb") as fh:
                fh.write(b"\x00" * 16)
            m3u = os.path.join(tmp, "l.m3u")
            with open(m3u, "w", encoding="utf-8") as fh:
                fh.write("a.mp3\n")
            args = self.parser.parse_args([m3u])
            self.assertEqual(len(cli.collect_from_arguments(args, self.api)), 1)

    def test_json_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.json")
            write(path, [{"id": "1", "type": "wy", "name": "x"}])
            args = self.parser.parse_args([path])
            tracks = cli.collect_from_arguments(args, self.api)
            self.assertEqual(tracks[0].id, "1")

    def test_inline_json_query(self):
        args = self.parser.parse_args(['[{"id":"7","type":"qq"}]'])
        tracks = cli.collect_from_arguments(args, self.api)
        self.assertEqual(tracks[0].type, "qq")

    def test_platform_directive(self):
        args = self.parser.parse_args(["wy:1357375695"])
        tracks = cli.collect_from_arguments(args, self.api)
        self.assertEqual(tracks[0].id, "1357375695")

    def test_url_query(self):
        args = self.parser.parse_args(["https://example.com/a.mp3"])
        tracks = cli.collect_from_arguments(args, self.api)
        self.assertEqual(tracks[0].source, "网络直链")

    def test_empty_query_returns_nothing(self):
        args = self.parser.parse_args([])
        self.assertEqual(cli.collect_from_arguments(args, self.api), [])


class JsonFileFlagTest(unittest.TestCase):
    def test_file_flag_accepts_repeated_values(self):
        parser = cli.build_parser()
        args = parser.parse_args(["--file", "a.json", "--file", "b.json"])
        self.assertEqual(args.json_file, ["a.json", "b.json"])

    def test_stdin_flag(self):
        self.assertTrue(cli.build_parser().parse_args(["--stdin-json"]).stdin_json)


class FastPathFlagTest(unittest.TestCase):
    def test_book_parses_path_and_artist(self):
        args = cli.build_parser().parse_args(
            ["稻香", "--artist", "周杰伦", "--book", "p.json", "--no-play"]
        )
        self.assertEqual(args.book, "p.json")
        self.assertEqual(args.artist, "周杰伦")
        self.assertTrue(args.no_play)
        self.assertEqual(args.book_count, 1)

    def test_spin_defaults_to_none_and_accepts_float(self):
        args = cli.build_parser().parse_args(["稻香"])
        self.assertIsNone(args.spin)
        self.assertEqual(cli.build_parser().parse_args(["--spin", "8.5"]).spin, 8.5)

    def test_brief_and_compact_are_off_by_default(self):
        args = cli.build_parser().parse_args(["稻香"])
        self.assertFalse(args.brief)
        self.assertFalse(args.compact_json)

    def test_slim_drops_empty_fields_and_extra(self):
        full = {
            "id": "1", "type": "wy", "name": "歌", "artist": "A", "album": "B",
            "duration": 0.0, "url": "", "pic": "", "lrc": "", "source": "",
            "extra": {"pic_id": 5},
        }
        self.assertEqual(cli._slim(full), {"id": "1", "type": "wy", "name": "歌", "artist": "A", "album": "B"})

    def test_book_without_query_is_rejected(self):
        args = cli.build_parser().parse_args(["--book", "p.json"])
        self.assertEqual(cli._book(args, object()), 2)

    def test_mix_parses_path_and_limits(self):
        args = cli.build_parser().parse_args(
            ["深夜 city pop", "--mix", "p.json", "--mix-count", "6", "--per-artist", "1"]
        )
        self.assertEqual(args.mix, "p.json")
        self.assertEqual(args.mix_count, 6)
        self.assertEqual(args.per_artist, 1)

    def test_mix_without_query_is_rejected(self):
        args = cli.build_parser().parse_args(["--mix", "p.json"])
        self.assertEqual(cli._mix(args, object()), 2)


class ChildFlagForwardingTest(unittest.TestCase):
    """--book/--mix 开窗时要把影响播放的开关透传给窗口里的子进程。"""

    def _child_args(self, argv):
        from musiccil import window
        from musiccil.api import Track

        args = cli.build_parser().parse_args(argv)
        tracks = [Track(id="1", name="歌", artist="A", url="http://x/a.mp3")]
        captured = {}

        def fake_spawn(child_args, **kwargs):
            captured["argv"] = child_args
            # 真开窗时临时歌单由子进程读完即删；这里没有子进程，得自己清掉，
            # 否则每跑一次测试就在 TEMP 里留一个 musiccil-*.json
            leftover = kwargs.get("temp_playlist")
            if leftover and os.path.exists(leftover):
                os.remove(leftover)
            return 4321

        with mock.patch.object(window, "spawn", side_effect=fake_spawn):
            self.assertEqual(cli._launch_window(args, tracks), 0)
        return captured["argv"]

    def test_spin_is_forwarded_to_the_window(self):
        argv = self._child_args(["--window", "--spin", "9.5", "--volume", "55"])
        self.assertIn("--spin", argv)
        self.assertEqual(argv[argv.index("--spin") + 1], "9.5")
        self.assertEqual(argv[argv.index("--volume") + 1], "55")

    def test_spin_is_absent_by_default(self):
        self.assertNotIn("--spin", self._child_args(["--window"]))


class PathExpansionTest(unittest.TestCase):
    """路径展开：agent 照文档抄 %TEMP% 时 PowerShell 不会展开，得由我们兜住。"""

    def test_expand_path_handles_windows_env_var(self):
        with mock.patch.dict(os.environ, {"MUSICCIL_TEST_TMP": r"C:\tmp\mc-test"}, clear=False):
            self.assertEqual(
                cli.expand_path(r"%MUSICCIL_TEST_TMP%\song.json"),
                os.path.abspath(r"C:\tmp\mc-test\song.json"),
            )

    def test_expand_path_handles_tilde_and_relative(self):
        self.assertTrue(os.path.isabs(cli.expand_path("~")))
        self.assertEqual(cli.expand_path("a.json"), os.path.abspath("a.json"))

    def test_write_playlist_creates_missing_parent_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "nested", "deep", "song.json")
            path = cli._write_playlist(target, [])
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(path, os.path.abspath(target))


class FavoritesTest(unittest.TestCase):
    def test_reads_ids_ignoring_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "fav.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# 注释\n1357375695\n\n001I6gzS3LufWy\n")
            self.assertEqual(cli._read_favorites(path), {"1357375695", "001I6gzS3LufWy"})

    def test_missing_file_is_empty(self):
        self.assertEqual(cli._read_favorites("definitely-missing-42.txt"), set())
        self.assertEqual(cli._read_favorites(None), set())


if __name__ == "__main__":
    unittest.main()
