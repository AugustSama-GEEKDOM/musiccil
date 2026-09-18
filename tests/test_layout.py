"""布局不变式：输出必须严格等于终端尺寸，否则真实终端会滚动/错位。"""

import re
import unittest

from musiccil import term
from musiccil.ui import MODES, Renderer, Scene
from musiccil.theme import SCENES, preset

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(line: str) -> str:
    return ANSI.sub("", line)


def sample_scene(theme=None, **kwargs) -> Scene:
    scene = Scene(theme=theme or preset("网易红"))
    scene.title = kwargs.get("title", "加州梦游")
    scene.artist = kwargs.get("artist", "闫泽欢 / 于梓贝")
    scene.album = kwargs.get("album", "加州梦游")
    scene.source = kwargs.get("source", "网易云")
    scene.duration = kwargs.get("duration", 266.0)
    scene.position = kwargs.get("position", 97.0)
    scene.playing = kwargs.get("playing", True)
    scene.volume = kwargs.get("volume", 72)
    scene.muted = kwargs.get("muted", False)
    scene.favorite = kwargs.get("favorite", True)
    scene.mode = kwargs.get("mode", 0)
    scene.view = kwargs.get("view", "player")
    scene.queue = kwargs.get("queue", [(f"曲目 {i + 1}", "歌手") for i in range(40)])
    scene.queue_index = kwargs.get("queue_index", 5)
    scene.notice = kwargs.get("notice", "")
    scene.notice_until = kwargs.get("notice_until", 0.0)
    return scene


class LayoutTest(unittest.TestCase):
    SIZES = [(80, 24), (60, 20), (100, 30), (120, 40), (200, 60), (54, 18), (44, 14)]
    VIEWS = ("player", "lyrics", "list")

    def test_lines_match_terminal_size(self):
        for cols, rows in self.SIZES:
            renderer = Renderer(cols, rows)
            for view in self.VIEWS:
                scenes = [
                    sample_scene(view=view),
                    sample_scene(view=view, title="A Very Long English Title That Overflows", artist=""),
                    sample_scene(view=view, title="未播放", album="", source="",
                                 notice="正在拉取封面…", notice_until=9e18),
                    sample_scene(view=view, duration=0, position=0),
                ]
                for scene in scenes:
                    lines = renderer.render(scene).split("\n")
                    self.assertEqual(len(lines), renderer.height, f"{cols}x{rows} {view} 行数不符")
                    for line in lines:
                        self.assertEqual(
                            term.str_width(plain(line)), cols,
                            f"{cols}x{rows} {view} 行宽不符: {plain(line)!r}",
                        )

    def test_wide_characters_do_not_break_width(self):
        renderer = Renderer(90, 30)
        scene = sample_scene(title="日本語のタイトル・한국어・中文标题", artist="Максим / 张惠妹")
        for line in renderer.render(scene).split("\n"):
            self.assertEqual(term.str_width(plain(line)), 90)

    def test_every_theme_keeps_layout(self):
        renderer = Renderer(96, 32)
        for name in SCENES:
            for view in self.VIEWS:
                lines = renderer.render(sample_scene(theme=preset(name), view=view)).split("\n")
                self.assertEqual(len(lines), renderer.height, name)
                for line in lines:
                    self.assertEqual(term.str_width(plain(line)), 96, name)

    def test_all_play_modes_render(self):
        renderer = Renderer(96, 32)
        for mode in range(len(MODES)):
            self.assertEqual(
                len(renderer.render(sample_scene(mode=mode)).split("\n")), renderer.height
            )

    def test_resize_is_consistent(self):
        renderer = Renderer(80, 24)
        for cols, rows in [(120, 40), (60, 20), (100, 30)]:
            renderer.resize(cols, rows)
            lines = renderer.render(sample_scene(view="lyrics")).split("\n")
            self.assertEqual(len(lines), renderer.height)
            for line in lines:
                self.assertEqual(term.str_width(plain(line)), cols)

    def test_width_override(self):
        renderer = Renderer(120, 30)
        renderer.width_override = 50
        renderer.resize(120, 30)
        self.assertEqual(renderer.width, 50)
        for line in renderer.render(sample_scene()).split("\n"):
            self.assertEqual(term.str_width(plain(line)), 120)

    def test_lyric_view_follows_position(self):
        from musiccil.lrc import parse

        renderer = Renderer(90, 30)
        scene = sample_scene(view="lyrics")
        scene.lyrics = parse("\n".join(f"[00:{i:02d}.00]第 {i} 句歌词" for i in range(40)))
        scene.position = 20.0
        frame = plain(renderer.render(scene))
        self.assertIn("第 20 句歌词", frame)
        self.assertNotIn("第 0 句歌词", frame)

    def test_list_view_shows_queue_window(self):
        renderer = Renderer(90, 30)
        scene = sample_scene(view="list")
        scene.queue = [(f"第{i}首", "歌手") for i in range(100)]
        scene.queue_index = 50
        frame = plain(renderer.render(scene))
        self.assertIn("第50首", frame)
        self.assertNotIn("第0首", frame)


class TruncateTest(unittest.TestCase):
    def test_truncate_counts_display_width(self):
        # 中文每字 2 列，5 列最多容纳 2 个字
        self.assertEqual(term.str_width(term.truncate("中文字符串", 5)), 5)
        self.assertEqual(term.str_width(term.truncate("中文字符串", 6)), 5)
        self.assertEqual(term.str_width(term.truncate("中文字符串", 7)), 7)
        self.assertEqual(term.str_width(term.truncate("abcdefghij", 5)), 5)
        self.assertEqual(term.truncate("abc", 10), "abc")
        self.assertEqual(term.truncate("anything", 0), "")

    def test_fit_pads_to_exact_width(self):
        for text in ("a", "中文", "mixed 混排 string", ""):
            self.assertEqual(term.str_width(term.fit(text, 20)), 20)

    def test_render_line_never_exceeds_width(self):
        for text in ("短", "中等长度中文文本内容", "x" * 200):
            line = term.render_line([(text, (255, 255, 255))], 20, (0, 0, 0))
            self.assertEqual(term.str_width(plain(line)), 20)


if __name__ == "__main__":
    unittest.main()
