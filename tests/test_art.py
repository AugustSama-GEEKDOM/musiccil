"""唱片区：像素网格、旋转、1 字符 1 像素映射、外圈转速同步。"""

import unittest

from musiccil.art import LABEL_RATIO, Vinyl, art_size_for, rotate_grid, to_pixels


class FakeImage:
    """最小可用的假封面：不依赖 Pillow，只验证尺寸与取值逻辑。"""

    width = 40
    height = 40
    size = (40, 40)

    def crop(self, box):
        return self

    def resize(self, size, method=None):
        class Small:
            def load(self):
                class Px:
                    def __getitem__(_, key):
                        x, y = key
                        return (x * 8 % 256, y * 8 % 256, 128)

                return Px()

            def save(self):
                pass

        return Small()


class ToPixelsTest(unittest.TestCase):
    def test_grid_shape_is_exact(self):
        grid = to_pixels(FakeImage(), 13, 9, enhance=False)
        self.assertEqual(len(grid), 9)
        for row in grid:
            self.assertEqual(len(row), 13)
            for cell in row:
                self.assertEqual(len(cell), 3)

    def test_zero_size_is_safe(self):
        self.assertEqual(to_pixels(FakeImage(), 0, 5, enhance=False), [])

    def test_quantize_kwargs_disable_dither(self):
        """量化必须关抖动，否则像素画会被撒上噪点。"""
        from PIL import Image

        source = Image.new("RGB", (64, 64))
        for y in range(64):
            for x in range(64):
                source.putpixel((x, y), (x * 4 % 256, y * 4 % 256, 128))

        captured = {}
        original = Image.Image.quantize

        def spy(self, *args, **kwargs):
            captured.update(kwargs)
            return original(self, *args, **kwargs)

        Image.Image.quantize = spy
        try:
            to_pixels(source, 19, 19, enhance=True)
        finally:
            Image.Image.quantize = original

        self.assertEqual(captured.get("dither"), Image.Dither.NONE)
        self.assertTrue(captured.get("colors"))

    def test_resample_is_box_for_hard_pixel_edges(self):
        """LANCZOS 的负瓣会把小尺寸像素画糊掉，必须用 BOX。"""
        from PIL import Image

        captured = {}
        source = Image.new("RGB", (32, 32), (10, 20, 30))
        original = Image.Image.resize

        def spy(self, size, resample=None, *a, **k):
            captured["resample"] = resample
            return original(self, size, resample, *a, **k)

        Image.Image.resize = spy
        try:
            to_pixels(source, 17, 17, enhance=False)
        finally:
            Image.Image.resize = original

        self.assertEqual(captured.get("resample"), Image.BOX)


class RotateTest(unittest.TestCase):
    GRID = [[(x, y, 0) for x in range(9)] for y in range(9)]

    def test_identity_at_zero(self):
        self.assertEqual(rotate_grid(self.GRID, 0.0, (0, 0, 0)), self.GRID)

    def test_size_preserved(self):
        for deg in (0, 37, 90, 180, 359.9):
            out = rotate_grid(self.GRID, deg, (1, 2, 3))
            self.assertEqual(len(out), 9)
            for row in out:
                self.assertEqual(len(row), 9)

    def test_out_of_bounds_uses_fill(self):
        out = rotate_grid(self.GRID, 45, (9, 9, 9))
        self.assertIn((9, 9, 9), [c for row in out for c in row])

    def test_empty_grid(self):
        self.assertEqual(rotate_grid([], 30, (0, 0, 0)), [])


class VinylTest(unittest.TestCase):
    def test_grid_dimensions_match_request(self):
        buf = Vinyl(px_w=24, px_h=24).buffer(0.0, playing=True, stylus=False)
        self.assertEqual(len(buf), 24)
        for row in buf:
            self.assertEqual(len(row), 24)

    def test_rows_are_half_height(self):
        self.assertEqual(len(Vinyl(px_w=24, px_h=24).rows(0.0)), 12)

    def test_center_hole_is_transparent(self):
        buf = Vinyl(px_w=24, px_h=24).buffer(0.0, playing=False, stylus=False)
        self.assertIsNone(buf[12][12])

    def test_rotation_only_reuses_cached_frames(self):
        grid = [[(x, y, 0) for x in range(9)] for y in range(9)]
        vinyl = Vinyl(px_w=24, px_h=24, cover=grid, angles=12)
        self.assertIs(vinyl._art_grid(0.0), vinyl._art_grid(1.0))
        self.assertLess(len(vinyl._cache), 13)

    def test_stylus_present_and_inside(self):
        vinyl = Vinyl(px_w=24, px_h=24)
        pts = vinyl._stylus(playing=True)
        self.assertTrue(pts)
        for x, y in pts:
            self.assertTrue(0 <= x < 24)
            self.assertTrue(0 <= y < 24)

    def test_art_size_fits_inside_vinyl(self):
        for cells in (12, 16, 24, 32, 40):
            side = art_size_for(cells / 2.0)
            self.assertLessEqual(side, cells)
            self.assertEqual(side % 2, 1, "边长必须是奇数才有唯一中心像素")

    def test_label_ratio_matches_art_size(self):
        # art_size_for 与 Vinyl.label_r 必须同比例，否则旋转采样会落到封面网格外
        for cells in (20, 32, 52):
            vinyl = Vinyl(px_w=cells, px_h=cells)
            self.assertAlmostEqual(vinyl.label_r / vinyl.radius, LABEL_RATIO, places=6)
