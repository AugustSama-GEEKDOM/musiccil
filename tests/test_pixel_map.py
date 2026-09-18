"""一个字符必须刚好对应一个封面像素，且内外圈转速一致。"""

import math
import unittest

from musiccil.art import Vinyl, art_size_for


def cover(side):
    return [[(x * 7 % 256, y * 11 % 256, 100) for x in range(side)] for y in range(side)]


class OneCharPerPixelTest(unittest.TestCase):
    """这里曾经用 ``int(round(x - ox))`` 做映射。

    居中偏移量常常正好是 ±0.5，Python 的银行家舍入会把相邻两列映到同一个
    封面像素，等于把每个像素横向拉宽成 2 格（实测 38 列只用到 19 个像素），
    看起来就是「几个字符一个大色块」。必须用 ``math.floor``。
    """

    def test_floor_uses_every_art_column_but_round_does_not(self):
        for cells, side in ((52, 37), (40, 27), (32, 23), (28, 19)):
            ox = (cells - side) / 2.0
            floored = [int(math.floor(x - ox)) for x in range(cells)]
            rounded = [int(round(x - ox)) for x in range(cells)]
            inside_f = [c for c in floored if 0 <= c < side]
            inside_r = [c for c in rounded if 0 <= c < side]
            self.assertEqual(
                len(inside_f), len(set(inside_f)),
                "cells=%d side=%d floor 映射出现重复列" % (cells, side),
            )
            self.assertLess(
                len(set(inside_r)), len(set(inside_f)),
                "cells=%d side=%d 应该能观察到 round 的列折叠" % (cells, side),
            )

    def test_labels_neighbour_cells_are_distinct(self):
        side = 19
        vinyl = Vinyl(px_w=40, px_h=40, cover=cover(side))
        ox = (40 - side) / 2.0
        row = int(round(vinyl.cy))
        colors = [
            vinyl._label_color(x, row, 0.0, 0.0)
            for x in range(40)
            if 0 <= int(math.floor(x - ox)) < side
        ]
        self.assertGreater(
            len(set(colors)), len(colors) // 2,
            "标题像素被成对复制，1:1 映射失效",
        )

    def test_art_side_follows_label_ratio(self):
        for cells in (20, 32, 52):
            side = art_size_for(cells / 2.0)
            if side % 2 == 0:
                side += 1
            self.assertLessEqual(side, cells, "封面网格不该超过唱片像素尺寸")
            self.assertEqual(side % 2, 1)


class RotationSyncTest(unittest.TestCase):
    """黑胶外圈高光必须一圈一次，才不会看起来比封面快一倍。"""

    CELLS = 52

    def _outer_points(self, vinyl, n=6):
        r = vinyl.radius
        return [
            (x, y)
            for y in range(self.CELLS)
            for x in range(self.CELLS)
            if r * 0.86 <= math.hypot(x - vinyl.cx, y - vinyl.cy) <= r * 0.94
        ][:n]

    @staticmethod
    def _lum(vinyl, x, y, ang):
        c = vinyl.pixel(x, y, float(ang), True)
        return (c[0] * 3 + c[1] * 6 + c[2]) / 10.0

    def test_outer_sheen_period_is_360_not_180(self):
        vinyl = Vinyl(px_w=self.CELLS, px_h=self.CELLS)
        pts = self._outer_points(vinyl)
        self.assertTrue(pts, "没有取到外圈像素")
        for x, y in pts:
            first = [self._lum(vinyl, x, y, a) for a in range(0, 180, 30)]
            second = [self._lum(vinyl, x, y, a) for a in range(180, 360, 30)]
            identical = all(abs(a - b) < 3 for a, b in zip(first, second))
            self.assertFalse(
                identical,
                "外圈高光呈 180° 周期，说明又用回了 sin(2*ang)，会比封面快一倍",
            )

    def test_sheen_actually_varies_with_angle(self):
        vinyl = Vinyl(px_w=self.CELLS, px_h=self.CELLS)
        x, y = self._outer_points(vinyl, 1)[0]
        vals = [self._lum(vinyl, x, y, a) for a in range(0, 360, 15)]
        self.assertGreater(max(vals) - min(vals), 2.0, "外圈高光几乎不随角度变化")


class SpinRateTest(unittest.TestCase):
    """转速与角度分辨率：外圈转太快/跳帧是两个不同的毛病。"""

    def test_spin_is_calm_enough(self):
        from musiccil.app import SPIN_PER_SEC

        seconds_per_turn = 360.0 / SPIN_PER_SEC
        # 曾经是 55°/s（6.5 秒一圈，太急）→ 25°/s → 15°/s（24 秒一圈）
        self.assertLessEqual(SPIN_PER_SEC, 20.0, "转速又被调快了")
        self.assertGreaterEqual(seconds_per_turn, 18.0, "一圈太快，会显得外圈在抢拍")

    def test_app_accepts_spin_override(self):
        from musiccil.app import App, SPIN_PER_SEC
        from musiccil.api import Track

        tracks = [Track(id="1", name="歌", artist="A")]
        self.assertEqual(App(tracks, no_audio=True, offline=True).spin, SPIN_PER_SEC)
        self.assertEqual(App(tracks, no_audio=True, offline=True, spin=8).spin, 8.0)
        # 0/None 视为没给，仍然用默认转速，避免传 0 时唱片卡死
        self.assertEqual(App(tracks, no_audio=True, offline=True, spin=0).spin, SPIN_PER_SEC)

    def test_angle_frames_are_dense_enough(self):
        vinyl = Vinyl(px_w=28, px_h=28)
        # 每圈帧数太少时封面会一跳一跳；转速越慢这个顿挫越明显
        self.assertGreaterEqual(vinyl.angles, 360, "旋转角度分辨率太低，会出现跳帧")
        # 档位本身要细到看不出台阶：在二十来像素的封面上 1° 大约只移动 0.2 像素
        per_frame = 360.0 / vinyl.angles
        self.assertLessEqual(per_frame, 1.5, "角度档位太粗，会看出台阶")

    def test_spin_is_still_continuous_at_30fps(self):
        """慢速下每帧都必须真的推进，不能被角度档位吃掉而原地踏步。"""
        from musiccil.app import SPIN_PER_SEC

        grid = [[(x * 7 % 256, y * 11 % 256, 100) for x in range(20)] for y in range(20)]
        vinyl = Vinyl(px_w=28, px_h=28, cover=grid)

        angle = 0.0
        dt = 1 / 30.0
        frames = []
        for _ in range(90):                        # 3 秒
            angle = (angle + SPIN_PER_SEC * dt) % 360.0
            frames.append(id(vinyl._art_grid(angle)))

        stalls = sum(1 for a, b in zip(frames, frames[1:]) if a == b)
        self.assertEqual(stalls, 0, "封面有帧原地不动，慢速下会显得一顿一顿")
        self.assertEqual(len(set(frames)), len(frames), "封面帧没有随角度连续更新")


class ThemeFadeTest(unittest.TestCase):
    """换曲时配色要渐变过去，不能一步跳变。"""

    def _app(self):
        from musiccil.app import App
        from musiccil.api import Track

        return App([Track(id="1", name="歌", artist="A")], no_audio=True, offline=True)

    def test_switch_starts_a_fade_instead_of_jumping(self):
        from musiccil.app import THEME_FADE
        from musiccil.theme import preset

        app = self._app()
        app.scene.theme = preset("海盐蓝")
        app._auto_theme = preset("网易红")
        app._apply_theme()

        # 刚切过去时屏幕上的还是旧配色，只是记录了一个过渡目标
        self.assertEqual(app.scene.theme, preset("海盐蓝"))
        self.assertEqual(app._fade_to, preset("网易红"))
        self.assertLess(app._fade_progress, 1.0)

        # 过渡中途必须落在两个端点之间，而不是任一端点
        app._advance_theme_fade(THEME_FADE / 2)
        mid = app.scene.theme.accent
        self.assertNotIn(mid, (preset("海盐蓝").accent, preset("网易红").accent))

        # 走完过渡后精确等于目标主题
        app._advance_theme_fade(THEME_FADE)
        self.assertEqual(app.scene.theme, preset("网易红"))
        self.assertEqual(app._fade_progress, 1.0)

    def test_fade_finishes_in_reasonable_time(self):
        from musiccil.app import THEME_FADE

        self.assertGreaterEqual(THEME_FADE, 0.3, "过渡太快，看不出渐变")
        self.assertLessEqual(THEME_FADE, 2.0, "过渡太慢，换歌会显得迟钝")

    def test_repeated_frames_after_finish_do_not_keep_working(self):
        from musiccil.app import THEME_FADE
        from musiccil.theme import preset

        app = self._app()
        app.scene.theme = preset("海盐蓝")
        app._auto_theme = preset("网易红")
        app._apply_theme()
        app._advance_theme_fade(THEME_FADE * 2)
        settled = app.scene.theme
        app._advance_theme_fade(THEME_FADE)
        self.assertEqual(app.scene.theme, settled)

    def test_snap_theme_finishes_immediately(self):
        from musiccil.theme import preset

        app = self._app()
        app.scene.theme = preset("海盐蓝")
        app._auto_theme = preset("网易红")
        app._apply_theme()
        app.snap_theme()
        self.assertEqual(app.scene.theme, preset("网易红"))
        self.assertEqual(app._fade_progress, 1.0)

    def test_fade_seconds_are_configurable(self):
        from musiccil.app import App, THEME_FADE
        from musiccil.api import Track

        tracks = [Track(id="1", name="歌", artist="A")]
        self.assertEqual(App(tracks, no_audio=True, offline=True).theme_fade, THEME_FADE)
        self.assertEqual(App(tracks, no_audio=True, offline=True, theme_fade=2.5).theme_fade, 2.5)
        # 0 是合法值：表示不做过渡
        self.assertEqual(App(tracks, no_audio=True, offline=True, theme_fade=0).theme_fade, 0.0)
        self.assertEqual(App(tracks, no_audio=True, offline=True, theme_fade=-3).theme_fade, 0.0)

    def test_zero_fade_switches_immediately(self):
        from musiccil.theme import preset

        app = self._app()
        app.theme_fade = 0.0
        app.scene.theme = preset("海盐蓝")
        app._auto_theme = preset("网易红")
        app._apply_theme()
        app._advance_theme_fade(0.016)
        self.assertEqual(app.scene.theme, preset("网易红"))

    def test_snap_theme_reads_current_target_not_stale_field(self):
        """回归：目标没变时 _apply_theme 会提前返回，snap 不能直接用旧的 _fade_to。"""
        from musiccil.theme import preset

        app = self._app()
        app._auto_theme = preset("青柠")
        app._apply_theme()
        # 目标已经生效、过渡也走完了，此时 _fade_to 才第一次被写成青柠
        app.snap_theme()
        app._auto_theme = preset("霓虹紫")
        app._apply_theme()
        app.snap_theme()
        self.assertEqual(app.scene.theme, preset("霓虹紫"))

    def test_fade_hue_moves_monotonically_between_ends(self):
        """蓝→红必须沿色相绕过去，而不是掉进灰紫。"""
        import colorsys

        from musiccil.theme import preset

        app = self._app()
        app._auto_theme = preset("海盐蓝")
        app.snap_theme()
        start_hue = colorsys.rgb_to_hsv(*(c / 255.0 for c in app.scene.theme.accent))[0] * 360

        app._auto_theme = preset("网易红")
        app._apply_theme()
        hues = []
        for _ in range(27):
            app._advance_theme_fade(1 / 30.0)
            hues.append(colorsys.rgb_to_hsv(*(c / 255.0 for c in app.scene.theme.accent))[0] * 360)

        end_hue = colorsys.rgb_to_hsv(*(c / 255.0 for c in app.scene.theme.accent))[0] * 360
        # 起点约 214°、终点约 358°：中间每一步都该落在这条弧上，不出现反向跳
        self.assertLessEqual(abs(hues[-1] - end_hue), 2.0)
        self.assertAlmostEqual(end_hue, 358.0, delta=6.0)
        self.assertAlmostEqual(start_hue, 214.0, delta=6.0)
        for a, b in zip(hues, hues[1:]):
            self.assertLessEqual(a - b, 1.0, "色相出现回跳")

    def test_apply_theme_is_noop_when_target_unchanged(self):
        from musiccil.theme import preset

        app = self._app()
        app.scene.theme = preset("网易红")
        app._auto_theme = preset("网易红")
        app._fade_progress = 1.0
        app._apply_theme()
        self.assertEqual(app._fade_progress, 1.0, "目标没变就不该重新开始过渡")
        self.assertEqual(app.scene.theme, preset("网易红"))


class AudioFadeTest(unittest.TestCase):
    """切歌/快进快退时的音频淡入淡出。"""

    def _app(self, **kw):
        from musiccil.app import App
        from musiccil.api import Track
        from musiccil.ui import Renderer

        kw.setdefault("no_audio", True)
        kw.setdefault("offline", True)
        tracks = [Track(id=str(i), name="曲%d" % i, artist="A",
                        url="http://x/%d.mp3" % i) for i in range(4)]
        app = App(tracks, renderer=Renderer(), **kw)
        app.renderer.resize(50, 42)
        app.player = _FakeMpv()
        return app

    def test_default_and_override(self):
        from musiccil.app import AUDIO_FADE, App
        from musiccil.api import Track

        tracks = [Track(id="1", name="歌", artist="A")]
        self.assertEqual(App(tracks, no_audio=True, offline=True).audio_fade, AUDIO_FADE)
        self.assertEqual(App(tracks, no_audio=True, offline=True, audio_fade=0.5).audio_fade, 0.5)
        # 0 合法：表示不做过渡
        self.assertEqual(App(tracks, no_audio=True, offline=True, audio_fade=0).audio_fade, 0.0)
        self.assertEqual(App(tracks, no_audio=True, offline=True, audio_fade=-1).audio_fade, 0.0)

    def test_switch_fades_out_then_swaps_then_fades_in(self):
        app = self._app()
        app.player.gain = 0.0
        app.next_track()
        # 先静音，但还没换
        self.assertEqual(app.player.gain, app.player.SILENT_GAIN)
        self.assertEqual(app.player.loaded, [])
        self.assertEqual(app._audio_fade_state, "out")

        # 推进到切换点之后
        for _ in range(10):
            app._advance_audio_fade(1 / 30.0)
        self.assertEqual(app.player.loaded, ["http://x/1.mp3"])
        # 最终增益回 0、状态归位
        for _ in range(20):
            app._advance_audio_fade(1 / 30.0)
        self.assertEqual(app.player.gain, 0.0)
        self.assertEqual(app._audio_fade_state, "idle")

    def test_seek_also_fades(self):
        app = self._app()
        app.with_audio_fade(lambda: app.player.seek(5))
        self.assertEqual(app.player.gain, app.player.SILENT_GAIN)
        for _ in range(20):
            app._advance_audio_fade(1 / 30.0)
        self.assertEqual(app.player.seeked, [5])

    def test_consecutive_next_walks_two_tracks(self):
        """连按下一首必须走两首，不能因为延迟加载而只走一首。"""
        app = self._app()
        app.next_track()
        app._advance_audio_fade(0.05)
        app.next_track()
        for _ in range(20):
            app._advance_audio_fade(1 / 30.0)
        self.assertEqual(app.index, 2)
        self.assertEqual(app.player.loaded, ["http://x/2.mp3"], "应只加载最终那一首")

    def test_no_fade_switches_immediately(self):
        app = self._app(audio_fade=0)
        app.next_track()
        self.assertEqual(app.player.loaded, ["http://x/1.mp3"])
        self.assertEqual(app._audio_fade_state, "idle")

    def test_fade_disabled_without_player(self):
        app = self._app(audio_fade=0)
        app.player = None
        app.next_track()          # 不应抛异常
        self.assertEqual(app.index, 1)


class _FakeMpv:
    """只记录调用的 mpv 替身。"""

    SILENT_GAIN = -60.0
    position = 0.0

    def __init__(self):
        self.gain = 0.0
        self.loaded = []
        self.seeked = []
        self.paused = False

    def set_gain(self, g):
        self.gain = g

    def load(self, url, mode="replace"):
        self.loaded.append(url)

    def seek(self, seconds, mode="relative"):
        self.seeked.append(seconds)

    def seek_absolute(self, seconds):
        self.seeked.append(("abs", seconds))

    def quit(self):
        pass


class MediaSessionTest(unittest.TestCase):
    """系统媒体通知：开关、降级、以及按键回调映射。"""

    def _app(self, **kw):
        from musiccil.app import App
        from musiccil.api import Track

        kw.setdefault("no_audio", True)
        kw.setdefault("offline", True)
        return App([Track(id="1", name="歌", artist="A")], **kw)

    def test_can_be_disabled(self):
        self.assertIsNone(self._app(media_controls=False).media)

    def test_missing_winsdk_degrades_silently(self):
        from unittest import mock

        from musiccil import media

        with mock.patch.object(media, "available", return_value=False):
            session = media.MediaSession()
        self.assertFalse(session.enabled)
        # 降级后这些调用必须是安全的空操作
        session.update(None, True)
        session.pump()
        session.clear()
        session.close()

    def test_button_names_map_to_actions(self):
        from musiccil import media

        self.assertEqual(media.BUTTON_ACTIONS["PLAY"], "play")
        self.assertEqual(media.BUTTON_ACTIONS["PAUSE"], "pause")
        self.assertEqual(media.BUTTON_ACTIONS["NEXT"], "next")
        self.assertEqual(media.BUTTON_ACTIONS["PREVIOUS"], "previous")
        self.assertNotIn("RECORD", media.BUTTON_ACTIONS)

    def test_media_actions_drive_the_player(self):
        from unittest import mock

        app = self._app()
        app.player = mock.Mock()
        app.player.paused = True
        app._on_media_action("play")
        app.player.toggle_pause.assert_called_once()
        # 已经在播时再按播放不该重复切换
        app.player.reset_mock()
        app.player.paused = False
        app._on_media_action("play")
        app.player.toggle_pause.assert_not_called()

    def test_stop_action_requests_quit(self):
        app = self._app()
        app.player = None
        app._on_media_action("stop")
        self.assertTrue(app._want_quit)

    def test_sync_skips_duplicate_updates(self):
        """同一曲目同一秒内不该反复戳系统接口。"""
        from unittest import mock

        app = self._app()
        app.media = mock.Mock()
        app.media.enabled = True
        app._sync_media_session()
        first = app.media.update.call_count
        app._sync_media_session()
        self.assertEqual(app.media.update.call_count, first)

    def test_sync_refreshes_when_track_changes(self):
        from unittest import mock

        app = self._app()
        app.media = mock.Mock()
        app.media.enabled = True
        app._sync_media_session()
        before = app.media.update.call_count
        app.index = 0
        app._media_key = None
        app._sync_media_session()
        self.assertGreater(app.media.update.call_count, before)


class CoverFadeTest(unittest.TestCase):
    """换曲时封面要交叉淡入，而不是直接换图。"""

    def test_blend_grids_endpoints_and_middle(self):
        from musiccil.art import blend_grids

        a = [[(0, 0, 0)]]
        b = [[(200, 100, 50)]]
        self.assertEqual(blend_grids(a, b, 0.0), a)
        self.assertEqual(blend_grids(a, b, 1.0), b)
        self.assertEqual(blend_grids(a, b, 0.5), [[(100, 50, 25)]])

    def test_blend_grids_handles_empty_sides(self):
        from musiccil.art import blend_grids

        a = [[(10, 10, 10)]]
        b = [[(20, 20, 20)]]
        # 目标为空（新曲没有封面）时，走到 1.0 必须真的给出"空"，否则过渡结束不了
        self.assertEqual(blend_grids(a, None, 1.0), None)
        self.assertEqual(blend_grids(None, b, 0.0), None)
        self.assertEqual(blend_grids(None, b, 1.0), b)

    def test_blend_grids_tolerates_size_mismatch(self):
        from musiccil.art import blend_grids

        a = [[(0, 0, 0), (0, 0, 0)], [(0, 0, 0), (0, 0, 0)]]
        b = [[(100, 100, 100)]]
        out = blend_grids(a, b, 0.5)
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0]), 1)

    def test_cover_fade_progress_reaches_one(self):
        from musiccil.app import THEME_FADE

        app = self._app()
        app._cover_fade_progress = 0.0
        app._cover_fade_elapsed = 0.0
        seen = []
        for _ in range(40):
            seen.append(app._advance_cover_fade(THEME_FADE / 30.0))
        self.assertEqual(app._cover_fade_progress, 1.0)
        self.assertEqual(seen[-1], 1.0)
        # 中间必须真的存在"半透明"状态，否则就是直接换图
        self.assertTrue(any(0.0 < v < 1.0 for v in seen))

    def test_cover_fade_disabled_when_theme_fade_zero(self):
        app = self._app(theme_fade=0)
        app._cover_fade_progress = 0.0
        self.assertEqual(app._advance_cover_fade(0.016), 1.0)
        self.assertEqual(app._cover_fade_progress, 1.0)

    def _app(self, **kw):
        from musiccil.app import App
        from musiccil.api import Track

        kw.setdefault("no_audio", True)
        kw.setdefault("offline", True)
        return App([Track(id="1", name="歌", artist="A")], **kw)

    def test_vinyl_accepts_cover_fade_parameter(self):
        import inspect

        from musiccil.ui import Renderer

        sig = inspect.signature(Renderer.vinyl)
        self.assertIn("cover_fade", sig.parameters)


if __name__ == "__main__":
    unittest.main()
