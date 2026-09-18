"""主题：从封面提取主色，并派生整套界面配色。"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

RGB = Tuple[int, int, int]


def hsl(h: float, s: float, l: float) -> RGB:
    r, g, b = colorsys.hls_to_rgb(
        (h % 360.0) / 360.0,
        max(0.0, min(1.0, l)),
        max(0.0, min(1.0, s)),
    )
    return (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


def lerp(a: RGB, b: RGB, t: float) -> RGB:
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    return (
        int(round(a[0] + (b[0] - a[0]) * t)),
        int(round(a[1] + (b[1] - a[1]) * t)),
        int(round(a[2] + (b[2] - a[2]) * t)),
    )


def luminance(c: RGB) -> float:
    return (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255.0


def _vivid(c: RGB) -> bool:
    """够鲜艳才值得按色相旋转；暗色/灰色走 RGB 直插更稳。"""
    _, s, v = colorsys.rgb_to_hsv(*(x / 255.0 for x in c))
    return s >= 0.45 and v >= 0.25


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    """混合两个颜色。

    鲜艳色（强调色、顶栏）按 HSV **最短色相弧**旋转，换曲时是颜色"转"过去；
    低饱和的背景/文字按 RGB 逐通道插值，避免暗底在中途泛出怪彩色。
    """
    if a == b:
        return a
    if _vivid(a) and _vivid(b):
        ah, asat, av = colorsys.rgb_to_hsv(*(x / 255.0 for x in a))
        bh, bsat, bv = colorsys.rgb_to_hsv(*(x / 255.0 for x in b))
        dh = (bh - ah + 0.5) % 1.0 - 0.5      # 走最短的那半圈，别绕远路
        r, g, bl = colorsys.hsv_to_rgb(
            (ah + dh * t) % 1.0,
            asat + (bsat - asat) * t,
            av + (bv - av) * t,
        )
        return (int(round(r * 255)), int(round(g * 255)), int(round(bl * 255)))
    return lerp(a, b, t)


@dataclass(frozen=True)
class Theme:
    name: str
    bg_main: RGB
    bg_panel: RGB
    bg_header: RGB
    accent: RGB
    accent_dim: RGB
    muted: RGB
    text: RGB
    btn_bg: RGB
    stylus: RGB
    vinyl_dark: RGB
    vinyl_light: RGB
    prog_track: RGB

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "name"}


def theme_from_accent(name: str, accent: RGB) -> Theme:
    r, g, b = (c / 255.0 for c in accent)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    H = h * 360.0
    s = min(1.0, max(s, 0.42))
    return Theme(
        name=name,
        bg_main=hsl(H, min(s * 0.42, 0.30), 0.105),
        bg_panel=hsl(H, min(s * 0.45, 0.32), 0.135),
        bg_header=hsl(H, min(max(s * 0.95, 0.50), 0.85), 0.30),
        accent=hsl(H, min(max(s, 0.55), 0.92), 0.585),
        accent_dim=hsl(H, min(max(s, 0.50), 0.90), 0.375),
        muted=hsl(H, min(max(s * 0.25, 0.14), 0.30), 0.42),
        text=hsl(H, 0.20, 0.76),
        btn_bg=hsl(H, 0.28, 0.16),
        stylus=hsl(H, 0.08, 0.78),
        vinyl_dark=hsl(H, 0.16, 0.075),
        vinyl_light=hsl(H, 0.20, 0.215),
        prog_track=hsl(H, 0.22, 0.175),
    )


#: 场景预设：C 键循环，也可用 --scene 指定
SCENES = {
    "网易红": (235, 65, 70),
    "海盐蓝": (72, 148, 245),
    "青柠": (52, 196, 168),
    "琥珀橙": (245, 152, 60),
    "霓虹紫": (150, 112, 240),
    "蜜桃粉": (240, 118, 170),
}

DEFAULT_SCENE = "网易红"


def preset(name: str) -> Theme:
    accent = SCENES.get(name, SCENES[DEFAULT_SCENE])
    return theme_from_accent(name, accent)


def pick_accent(colors: Iterable[Tuple[RGB, int]]) -> Optional[RGB]:
    """从 (颜色, 数量) 列表里挑一个「最有代表性的鲜艳色」。

    评分偏向高饱和、中高明度、占比大的颜色；灰/黑/白会被跳过，
    跳过之后仍无候选则返回 None（调用方回退到默认场景）。
    """
    colors = [(tuple(int(x) for x in c), int(n)) for c, n in colors if n > 0]
    if not colors:
        return None
    total = float(sum(n for _, n in colors)) or 1.0
    best = None
    best_score = 0.0
    for rgb, n in colors:
        h, s, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))
        if v < 0.12 or s < 0.15:
            continue
        share = n / total
        edge = 1.0 - abs(v - 0.62) / 0.62  # 太暗太亮都降权
        score = (s ** 0.7) * (0.45 + 0.55 * max(0.0, edge)) * (0.55 + 0.45 * share)
        if score > best_score:
            best_score = score
            best = rgb
    return best


def theme_from_cover(name: str, colors) -> Theme:
    accent = pick_accent(colors)
    if accent is None:
        return preset(DEFAULT_SCENE)
    return theme_from_accent(name, accent)


def blend(a: Theme, b: Theme, t: float) -> Theme:
    """两个主题之间插值：``t`` 为 0 取 ``a``、为 1 取 ``b``。

    换曲时如果直接把新主题赋给界面，整屏颜色会"啪"地跳一下。按时间喂 0→1
    的进度调用本函数，配色就是平滑过渡过去的。
    """
    if t <= 0.0:
        return a
    if t >= 1.0:
        return b
    data = {
        key: _mix(value, b.__dict__[key], t)
        for key, value in a.__dict__.items()
        if key != "name"
    }
    return Theme(name=b.name if t >= 0.5 else a.name, **data)
