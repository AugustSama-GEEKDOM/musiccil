"""封面获取、调色板提取，以及「唱片 + 旋转像素封面」的终端绘制。

绘制用半block（▀）做正方形像素：1 个字符 = 1 像素宽 × 半个字符高，
上下两个像素用前景/背景色拼成，这样圆是真正的圆，像素画也够细腻。
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

RGB = Tuple[int, int, int]

UPPER_HALF = "▀"

#: 封面占唱片半径的比例。像素画要够大才看得清，所以标签放得比较满；
#: 再大就会把黑胶凹槽挤没了。``art_size_for`` 与 ``Vinyl`` 必须用同一个值，
#: 否则旋转采样会落到封面网格外面、在中心糊出一圈杂色。
#: 实测 0.60 偏小、0.72 会把封面裁掉且黑胶边缘太细，0.68 最平衡。
LABEL_RATIO = 0.68

#: 像素画保留的颜色数。太少发灰、太多又会把渐变噪声带进来，
#: 18~24 色在「能认出封面」和「色块干净」之间最平衡。
ART_COLORS = 22


# ----------------------------------------------------------------- 图片工具
def default_cache_dir() -> str:
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        root = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(root, "musiccil", "covers")


def fetch_bytes(url: str, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (musiccil)", "Accept": "image/*,*/*"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


class CoverCache:
    """封面本地缓存：先查磁盘，未命中再下载。``offline`` 时只用缓存。"""

    def __init__(self, directory: Optional[str] = None, offline: bool = False):
        self.dir = directory or default_cache_dir()
        self.offline = offline

    def _path(self, key: str) -> str:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
        return os.path.join(self.dir, digest + ".img")

    def get(self, key: str, url: str = "") -> Optional[bytes]:
        path = self._path(key)
        if os.path.exists(path):
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
                if data:
                    return data
            except OSError:
                pass
        if not url or self.offline:
            return None
        try:
            data = fetch_bytes(url)
        except (urllib.error.URLError, OSError, ValueError):
            return None
        if data:
            try:
                os.makedirs(self.dir, exist_ok=True)
                tmp = path + ".tmp"
                with open(tmp, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, path)
            except OSError:
                pass
        return data or None


def open_cover(data: bytes):
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    img.load()
    return img.convert("RGB")


def palette(img, max_colors: int = 48, sample: int = 96) -> List[Tuple[RGB, int]]:
    """把封面量化成 (颜色, 像素数) 列表，用于挑主色。"""
    from PIL import Image

    thumb = img.copy()
    thumb.thumbnail((sample, sample), Image.LANCZOS)
    quant = thumb.quantize(colors=max_colors, method=Image.MEDIANCUT)
    pal = quant.getpalette() or []
    counts = quant.getcolors(maxcolors=1 << 20) or []
    out: List[Tuple[RGB, int]] = []
    for count, index in counts:
        base = index * 3
        if base + 2 >= len(pal):
            continue
        out.append(((pal[base], pal[base + 1], pal[base + 2]), count))
    out.sort(key=lambda item: -item[1])
    return out


def to_pixels(
    img,
    width: int,
    height: int,
    enhance: bool = True,
    colors: int = ART_COLORS,
) -> List[List[RGB]]:
    """把封面缩放成 width×height 的像素网格（居中裁成正方形）。

    缩到几十像素之后，清晰度取决于两件事：

    * **用 BOX 而不是 LANCZOS**：LANCZOS 是带负瓣的重采样，在这么小的格子上会
      产生振铃和糊边；BOX 就是区域平均，边缘更硬、色块更整，正是像素画要的。
    * **量化到有限调色板且关闭抖动**：渐变被压成有限的干净色块，辨识度反而更高。
      一定不能开 dither，否则会给像素画撒上噪点。
    """
    from PIL import Image, ImageEnhance

    if width <= 0 or height <= 0:
        return []
    side = min(img.size)
    left = (img.width - side) // 2
    top = (img.height - side) // 2
    square = img.crop((left, top, left + side, top + side))
    small = square.resize((width, height), Image.BOX)
    if enhance:
        # 让缩到十几个像素后仍有辨识度
        small = ImageEnhance.Color(small).enhance(1.5)
        small = ImageEnhance.Contrast(small).enhance(1.15)
        if colors:
            small = small.quantize(
                colors=colors, method=Image.MEDIANCUT, dither=Image.Dither.NONE
            ).convert("RGB")
    px = small.load()
    return [[px[x, y] for x in range(width)] for y in range(height)]


def rotate_grid(grid: List[List[RGB]], degrees: float, fill: RGB) -> List[List[RGB]]:
    """最近邻旋转像素网格（保留像素画的硬边）。"""
    if not grid:
        return grid
    h = len(grid)
    w = len(grid[0])
    if abs(degrees % 360.0) < 0.35:
        return [row[:] for row in grid]
    rad = math.radians(degrees)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    out: List[List[RGB]] = []
    for y in range(h):
        row: List[RGB] = []
        for x in range(w):
            sx = cos_a * (x - cx) - sin_a * (y - cy) + cx
            sy = sin_a * (x - cx) + cos_a * (y - cy) + cy
            ix, iy = int(round(sx)), int(round(sy))
            if 0 <= ix < w and 0 <= iy < h:
                row.append(grid[iy][ix])
            else:
                row.append(fill)
        out.append(row)
    return out


def blend_grids(a: List[List[RGB]], b: List[List[RGB]], t: float) -> List[List[RGB]]:
    """两张像素画之间插值（换曲时封面过渡用）。

    ``t`` 为 0 得到 ``a``、为 1 得到 ``b``。尺寸不一致时按两者中较小的取，
    所以换曲前后封面边长不同也不会越界。
    """
    if t <= 0.0:
        return a
    if t >= 1.0:
        return b
    # 端点判断要放在"空网格"之前：否则目标封面为空（无封面）时，
    # t 走到 1 也会一直返回旧封面，过渡永远结束不了。
    if not a:
        return b
    if not b:
        return a
    rows = min(len(a), len(b))
    out: List[List[RGB]] = []
    for y in range(rows):
        ra, rb = a[y], b[y]
        cols = min(len(ra), len(rb))
        row: List[RGB] = []
        for x in range(cols):
            ca, cb = ra[x], rb[x]
            row.append(
                (
                    int(round(ca[0] + (cb[0] - ca[0]) * t)),
                    int(round(ca[1] + (cb[1] - ca[1]) * t)),
                    int(round(ca[2] + (cb[2] - ca[2]) * t)),
                )
            )
        out.append(row)
    return out


# ------------------------------------------------------------------ 唱片绘制
class Vinyl:
    """按字符网格生成唱片像素。

    网格坐标即像素坐标：横向 1 像素 = 1 个字符，纵向 2 像素 = 1 行。
    """

    def __init__(
        self,
        px_w: int = 28,
        px_h: int = 28,
        cover: Optional[List[List[RGB]]] = None,
        accent: RGB = (235, 65, 70),
        theme=None,
        #: 角度档位缓存数。``rotate_grid`` 单次约 0.16 ms，整帧约 0.7 ms
        #: （30fps 预算的 2%），所以直接按当前角度精确旋转，不做量化——
        #: 一旦量化成档位，慢速时每帧 0.5° 的步进会被档位吃掉，
        #: 封面有一半的帧原地不动，看起来就是一卡一卡。
        #: 这个值只用于限制缓存规模。
        angles: int = 360,
    ):
        self.px_w = px_w
        self.px_h = px_h
        self.theme = theme
        self.cx = (px_w - 1) / 2.0
        self.cy = (px_h - 1) / 2.0
        self.radius = min(self.cx, self.cy) + 0.4
        self.hole_r = max(1.0, self.radius * 0.115)
        self.label_r = self.radius * LABEL_RATIO
        self.angles = max(1, angles)
        self.accent = accent
        self._base_art = cover
        self._cache: Dict[int, List[List[RGB]]] = {}

    # -- 像素颜色 ------------------------------------------------------
    def _vinyl_color(self, dist: float, ang: float) -> RGB:
        t = (dist - self.label_r) / max(0.001, self.radius - self.label_r)
        t = max(0.0, min(1.0, t))
        groove = 0.5 + 0.5 * math.sin(dist * math.pi * 1.55)
        # 一圈只有一个高光，转速才和中心的封面一致；用 sin(2*ang) 会变成两个对称
        # 高光，看上去像在以两倍速转，和封面不同步。
        sheen = 0.5 + 0.5 * math.sin(ang + dist * 0.22)
        if self.theme is not None:
            dark, light = self.theme.vinyl_dark, self.theme.vinyl_light
        else:
            dark, light = (18, 20, 24), (58, 62, 70)
        k = 0.30 * t + 0.42 * groove * (0.35 + 0.65 * t) + 0.34 * sheen * (0.20 + 0.80 * t)
        k = max(0.0, min(1.0, k))
        return (
            int(dark[0] + (light[0] - dark[0]) * k),
            int(dark[1] + (light[1] - dark[1]) * k),
            int(dark[2] + (light[2] - dark[2]) * k),
        )

    def _label_color(self, x: int, y: int, dist: float, angle: float) -> RGB:
        if self._base_art is None:
            # 没有封面时用主题色画一个同心圆标签
            band = int(dist * 1.1) % 2
            base = self.accent
            if band:
                base = tuple(int(c * 0.72) for c in self.accent)
            return base  # type: ignore[return-value]
        art = self._art_grid(angle)
        ah = len(art)
        aw = len(art[0]) if ah else 0
        if ah == 0 or aw == 0:
            return self.accent
        ox = (self.px_w - aw) / 2.0
        oy = (self.px_h - ah) / 2.0
        # 必须用 floor 而不是 round：居中偏移量常常正好是 ±0.5，而 Python 的
        # 银行家舍入会把相邻两列映到同一个封面像素，等于把每个像素横向拉宽成
        # 2 格，看起来就是"几个字符一个大色块"。
        ax = int(math.floor(x - ox))
        ay = int(math.floor(y - oy))
        if 0 <= ax < aw and 0 <= ay < ah:
            return art[ay][ax]
        return self.accent

    def _art_grid(self, angle: float) -> List[List[RGB]]:
        if self._base_art is None:
            return []
        # 按真实角度取整到 0.5° 缓存：既保证连续推进，又不用每帧重算同一个角度。
        quant = 720 if self.angles >= 360 else self.angles * 2
        bucket = int(round((angle % 360.0) / (360.0 / quant))) % quant
        grid = self._cache.get(bucket)
        if grid is None:
            grid = rotate_grid(self._base_art, bucket * (360.0 / quant), self.accent)
            self._cache[bucket] = grid
        return grid

    # -- 主绘制 --------------------------------------------------------
    def pixel(self, x: int, y: int, angle: float, playing: bool) -> RGB:
        dx = x - self.cx
        dy = y - self.cy
        dist = math.hypot(dx, dy)
        if dist > self.radius:
            return self.theme.bg_main if self.theme else (21, 25, 38)
        if dist <= self.hole_r:
            return self.theme.bg_main if self.theme else (21, 25, 38)
        if dist <= self.label_r:
            return self._label_color(x, y, dist, angle)
        return self._vinyl_color(dist, math.atan2(dy, dx) + angle)

    def _stylus(self, playing: bool) -> List[Tuple[int, int]]:
        """唱针：从右上方伸向唱片，播放时落在唱片上。"""
        top = int(self.cy - self.radius - 1.2)
        start_x = int(self.cx + self.radius * 0.78)
        end_x = int(self.cx + self.label_r * 0.92) if playing else int(self.cx + self.radius * 0.80)
        start_y = max(0, top)
        end_y = int(self.cy - self.label_r * 0.42) if playing else int(top + 2)
        pts: List[Tuple[int, int]] = []
        steps = max(1, end_y - start_y)
        for i in range(steps + 1):
            t = i / steps
            x = int(round(start_x + (end_x - start_x) * t))
            y = start_y + i
            pts.append((x, y))
        return pts

    def buffer(
        self, angle: float, playing: bool = True, stylus: bool = True
    ) -> List[List[Optional[RGB]]]:
        """None 表示该像素透明（直接露终端底色）。"""
        transparent = self._transparent_mask()
        buf: List[List[Optional[RGB]]] = [
            [None if transparent[y][x] else self.pixel(x, y, angle, playing) for x in range(self.px_w)]
            for y in range(self.px_h)
        ]
        if stylus:
            col = self.theme.stylus if self.theme else (200, 210, 225)
            for x, y in self._stylus(playing):
                if 0 <= x < self.px_w and 0 <= y < self.px_h:
                    buf[y][x] = col
        return buf

    def _transparent_mask(self) -> List[List[bool]]:
        mask = getattr(self, "_mask", None)
        if mask is None:
            mask = [
                [math.hypot(x - self.cx, y - self.cy) > self.radius
                 or math.hypot(x - self.cx, y - self.cy) <= self.hole_r
                 for x in range(self.px_w)]
                for y in range(self.px_h)
            ]
            self._mask = mask
        return mask

    def rows(
        self, angle: float, playing: bool = True, stylus: bool = True, base: Optional[RGB] = None
    ) -> List[List[Tuple[str, Optional[RGB], Optional[RGB]]]]:
        """转成「半block」单元格行：每格 (▀, 上色, 下色)。"""
        buf = self.buffer(angle, playing, stylus)
        base = base or (self.theme.bg_main if self.theme else (21, 25, 38))
        rows: List[List[Tuple[str, RGB, RGB]]] = []
        for row in range(0, self.px_h, 2):
            top = buf[row]
            bottom = buf[row + 1] if row + 1 < self.px_h else [None] * self.px_w
            cells = []
            for x in range(self.px_w):
                t, b = top[x], bottom[x]
                if t is None and b is None:
                    cells.append((" ", None, None))
                elif t is None:
                    cells.append(("▄", b, None))
                elif b is None:
                    cells.append((UPPER_HALF, t, None))
                elif t == b:
                    cells.append(("█", t, None))
                else:
                    cells.append((UPPER_HALF, t, b))
            rows.append(cells)
        return rows


def art_size_for(radius_px: float, ring: float = LABEL_RATIO) -> int:
    """封面像素边长：略大于标签直径，圆形裁切掉四角，视觉上铺满中心。

    取 1.02 倍是为了让旋转时圆形区域永远落在网格内部（见 LABEL_RATIO 注释），
    同时保证边长为奇数，使网格有唯一中心像素、唱片不歪。
    """
    side = int(radius_px * ring * 2.0 * 1.02)
    side = max(9, side)
    return side if side % 2 else side + 1
