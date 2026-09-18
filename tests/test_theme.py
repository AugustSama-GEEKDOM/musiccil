import colorsys
import unittest

from musiccil.theme import (
    DEFAULT_SCENE,
    SCENES,
    blend,
    hsl,
    luminance,
    pick_accent,
    preset,
    theme_from_accent,
    theme_from_cover,
)


class AccentTest(unittest.TestCase):
    def test_picks_saturated_over_gray(self):
        colors = [((40, 40, 40), 5000), ((220, 30, 40), 500)]
        self.assertEqual(pick_accent(colors), (220, 30, 40))

    def test_ignores_dark_and_desaturated(self):
        self.assertIsNone(pick_accent([((10, 10, 12), 900), ((200, 200, 202), 100)]))
        self.assertIsNone(pick_accent([]))

    def test_size_matters(self):
        big = [((200, 60, 60), 10000), ((60, 60, 200), 500)]
        self.assertEqual(pick_accent(big), (200, 60, 60))

    def test_fallback_when_no_vivid_color(self):
        self.assertEqual(theme_from_cover("x", [((30, 30, 30), 100)]), preset(DEFAULT_SCENE))


class ThemeTest(unittest.TestCase):
    def test_presets_are_distinct(self):
        accents = {name: preset(name).accent for name in SCENES}
        self.assertEqual(len(set(accents.values())), len(accents))

    def test_derived_theme_is_readable(self):
        for name in SCENES:
            theme = preset(name)
            self.assertLess(luminance(theme.bg_main), 0.20, name)
            self.assertGreater(luminance(theme.accent), 0.30, name)
            for rgb in theme.as_dict().values():
                self.assertEqual(len(rgb), 3)
                for channel in rgb:
                    self.assertTrue(0 <= channel <= 255, name)

    def test_header_is_visible_against_background(self):
        for name in SCENES:
            theme = preset(name)
            self.assertGreater(luminance(theme.bg_header), luminance(theme.bg_main), name)

    def test_theme_follows_cover_hue(self):
        red = theme_from_accent("r", (220, 40, 40))
        blue = theme_from_accent("b", (40, 80, 220))
        self.assertGreater(red.accent[0], red.accent[2])
        self.assertGreater(blue.accent[2], blue.accent[0])

    def test_hsl_bounds(self):
        self.assertEqual(hsl(0, 0, 0), (0, 0, 0))
        self.assertEqual(hsl(0, 0, 1), (255, 255, 255))
        self.assertEqual(hsl(999, 5, -3), (0, 0, 0))


class BlendTest(unittest.TestCase):
    """换曲时的配色过渡：靠这个函数把两套主题插值出来。"""

    def test_endpoints_are_exact(self):
        a, b = preset("海盐蓝"), preset("网易红")
        self.assertEqual(blend(a, b, 0.0), a)
        self.assertEqual(blend(a, b, 1.0), b)
        self.assertEqual(blend(a, b, -1.0), a)
        self.assertEqual(blend(a, b, 2.0), b)

    def test_every_field_stays_a_valid_rgb(self):
        a, b = preset("海盐蓝"), preset("青柠")
        for step in range(21):
            mid = blend(a, b, step / 20.0)
            for key, rgb in mid.as_dict().items():
                self.assertEqual(len(rgb), 3, key)
                for channel in rgb:
                    self.assertTrue(0 <= channel <= 255, (key, rgb))

    def test_vivid_colors_rotate_through_hue_not_gray(self):
        # 蓝→红若按 RGB 直插，中点是灰紫色、彩度会掉一半；按色相旋转则保持鲜艳
        a, b = preset("海盐蓝"), preset("网易红")
        mid = blend(a, b, 0.5).accent
        sat = colorsys.rgb_to_hsv(*(c / 255.0 for c in mid))[1]
        self.assertGreater(sat, 0.6, "过渡中段掉了饱和度，看起来会发灰")

    def test_dark_background_moves_monotonically(self):
        # 暗底走 RGB 直插，避免中途泛出怪彩色
        a, b = preset("海盐蓝"), preset("网易红")
        keys = ("bg_main", "bg_panel", "vinyl_dark")
        for key in keys:
            start = a.__dict__[key][0]
            end = b.__dict__[key][0]
            values = [blend(a, b, i / 10.0).__dict__[key][0] for i in range(11)]
            low, high = min(start, end), max(start, end)
            for v in values:
                self.assertTrue(low - 1 <= v <= high + 1, (key, v))

    def test_same_theme_is_returned_unchanged(self):
        a = preset("琥珀橙")
        self.assertEqual(blend(a, a, 0.5), a)


if __name__ == "__main__":
    unittest.main()
