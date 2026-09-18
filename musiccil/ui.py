"""界面渲染：整屏布局、唱片区、歌词页、播放列表页。

核心约束：**输出行数永远等于终端行数、每行宽度永远等于面板宽度**，
否则终端会滚动或错位。所有留白都由 ``_pad`` 动态分配。
"""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from . import term
from .art import LABEL_RATIO, Vinyl
from .lrc import LyricLine, index_at
from .theme import Theme, preset

RGB = Tuple[int, int, int]
Cell = Tuple[str, Optional[RGB], Optional[RGB]]

MODES = ("顺序", "随机", "单曲")


@dataclass
class Scene:
    """一次渲染需要的全部状态。"""

    theme: Theme = field(default_factory=lambda: preset("网易红"))
    title: str = "未播放"
    artist: str = "—"
    album: str = ""
    source: str = ""
    duration: float = 0.0
    position: float = 0.0
    playing: bool = False
    favorite: bool = False
    mode: int = 0
    volume: int = 80
    muted: bool = False
    lyrics: List[LyricLine] = field(default_factory=list)
    queue: List[Tuple[str, str]] = field(default_factory=list)
    queue_index: int = 0
    view: str = "player"
    notice: str = ""
    notice_until: float = 0.0
    angle: float = 0.0


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds or 0))
    return "%d:%02d" % divmod(seconds, 60)


class Renderer:
    """定宽定高渲染器。"""

    def __init__(self, cols: int = 100, rows: int = 34):
        self.height = max(12, rows - 1)
        self._cells = 0
        self.width = 0
        self.margin = 0
        self.width_override = 0      # >0 时强制面板宽度（用于 --width）
        self._vinyl: Optional[Vinyl] = None
        self._vinyl_key = None
        #: 换曲时封面像素画的交叉淡入：_cover_grid 是当前曲目，_cover_prev 是上一首
        self._cover_grid = None
        self._cover_prev = None
        self._cover_fade_cache = {}
        #: 新曲没封面时，淡出用的收尾色（跟随当前主题强调色）
        self._fade_fill: RGB = (0, 0, 0)
        self._cover_norm = None
        self._cover_norm_key = None
        self.resize(cols, rows)

    # ------------------------------------------------------------ 尺寸计算
    def resize(self, cols: int, rows: int) -> None:
        self.height = max(12, rows - 1)
        limit = self.width_override if self.width_override else 78
        self.width = max(30, min(cols - 1, limit))
        self.cols = cols
        self.margin = max(0, (cols - self.width) // 2)
        # 固定开销：header 4 + 上边框 1 + 上下各 1 空行 + 标签 1 + 标题 3
        #           + 空行 1 + 下边框 1 + 进度 1 + 空行 1 + 音量 1 + 空行 1 + 按键 2
        overhead = 19
        budget = max(5, self.height - overhead)      # 唱片区可用行数
        # 唱片不能占满屏：早期版本唱片吃掉整屏约 57% 的高度，很不协调。
        # 现在限制在唱片区高度的 70%、面板宽度的 70%，实测约占整屏 35%。
        cell_rows = max(7, min(int(budget * 0.70), int(self.width * 0.70) // 2))
        cells = cell_rows * 2
        cells = max(14, min(cells, self.width - 2))
        if cells % 2:
            cells += 1
        self._cells = cells
        self.invalidate()

    @property
    def cells(self) -> int:
        return self._cells

    # 兼容旧接口：外部直接赋值 width/height 时也重建
    @property
    def vinyl_rows(self) -> int:
        return self._cells // 2

    def invalidate(self) -> None:
        self._vinyl = None
        self._vinyl_key = None

    # ------------------------------------------------------------ 唱片对象
    def vinyl(self, theme: Theme, cover_img, accent: RGB, key, cover_fade: float = 1.0) -> Vinyl:
        """准备唱片对象。

        ``cover_fade`` 是封面交叉淡入的进度：0 显示上一张封面、1 显示当前曲目的。
        换曲时封面像素画若直接替换，中间会"啪"地换成另一张图；
        在两首的像素画之间插值，视觉上就是从旧封面渐变过去的。
        """
        # 没封面时收尾色用强调色：淡出之后留下的是主题色标签圆，不是一块突兀的黑
        self._fade_fill = accent
        if self._vinyl is None or self._vinyl_key != key:
            from .art import art_size_for, to_pixels

            grid = None
            if cover_img is not None:
                # 上限必须跟着 LABEL_RATIO 走：标签圆是 0.72R，用 0.60R 的老上限
                # 会把封面缩得过小，白白浪费像素
                ceiling = int(self._cells / 2.0 * LABEL_RATIO * 2.0 * 1.02) + 2
                side = min(art_size_for(self._cells / 2.0), ceiling)
                side = max(9, side)
                if side % 2 == 0:
                    side = side + 1 if side + 1 <= ceiling else side - 1
                grid = to_pixels(cover_img, side, side)
            # 换曲：把当前网格留作过渡起点，新网格作为终点
            self._cover_prev = self._cover_grid
            self._cover_grid = grid
            self._vinyl = Vinyl(
                px_w=self._cells, px_h=self._cells, cover=grid, accent=accent, theme=theme
            )
            self._vinyl_key = key
        else:
            if self._vinyl.theme != theme or self._vinyl.accent != accent:
                self._vinyl.theme = theme
                self._vinyl.accent = accent
                self._vinyl._cache.clear()
        self._apply_cover_grid(self._grid_for_fade(cover_fade))
        return self._vinyl

    def _grid_for_fade(self, progress: float):
        """按过渡进度给出封面网格：起点与终点之间插值。

        进度量化到有限档位并缓存结果——每帧都生成一张新网格的话，
        ``Vinyl`` 的旋转缓存每帧全失效，等于白白重算一遍旋转。
        """
        from .art import blend_grids

        if progress >= 1.0:
            return self._cover_grid
        if self._cover_prev is None:
            return self._cover_grid
        if progress <= 0.0 or self._cover_prev == self._cover_grid:
            return self._cover_prev
        # 新曲没封面时，让它淡出成一块主题色，而不是"啪"地消失。
        # blend_grids 在 t=1 时会返回 None，所以终点要先换成一个实心网格。
        target = self._cover_grid
        if target is None:
            target = self._solid_grid(self._cover_prev, self._fade_fill)
        step = 24                                   # 0.9 秒里 24 档，肉眼看不出台阶
        bucket = max(1, min(step - 1, int(round(progress * step))))
        cached = self._cover_fade_cache or {}
        key = (id(self._cover_prev), id(target), bucket)
        grid = cached.get(key)
        if grid is None:
            cached.clear()                          # 只留当前这首曲子的过渡帧
            grid = blend_grids(self._cover_prev, target, bucket / step)
            cached[key] = grid
        self._cover_fade_cache = cached
        return grid

    def _solid_grid(self, like, color: RGB):
        """照某张网格的尺寸造一张纯色网格（用于淡出到主题色）。"""
        return [[color for _ in row] for row in like]

    def _apply_cover_grid(self, grid) -> None:
        """把网格换成当前过渡帧；变了才清旋转缓存，避免每帧白算。"""
        # Vinyl 内部用的是 _base_art（旋转采样的源图）
        if getattr(self._vinyl, "_base_art", None) is grid:
            return
        self._vinyl._base_art = grid
        self._vinyl._cache.clear()

    # ------------------------------------------------------------ 行构造器
    def _line(self, cells, bg=None, align="left", width=None) -> str:
        t = self._theme
        return term.render_line(cells, width or self.width, bg or t.bg_main, align)

    def _blank(self, count=1) -> List[str]:
        return [self._line([(" ", None)]) for _ in range(max(0, count))]

    def _header(self) -> List[str]:
        t = self._theme
        return [
            term.render_line([(" ", None)], self.width, t.bg_header, "center"),
            term.render_line(
                [("◈", t.accent, t.bg_header),
                 ("   终 端 唱 片 机   ", t.text, t.bg_header),
                 ("◈", t.accent, t.bg_header)],
                self.width, t.bg_header, "center",
            ),
            term.render_line(
                [("Terminal Player v2.0 · mpv", t.muted, t.bg_header)],
                self.width, t.bg_header, "center",
            ),
            term.render_line([(" ", None)], self.width, t.bg_header, "center"),
        ]

    def _rule(self, top: bool) -> str:
        t = self._theme
        left, right = ("┌", "┐") if top else ("└", "┘")
        inner = max(0, self.width - 4)
        text = left + "─" + " ╌" * (inner // 2)
        if term.str_width(text) < self.width - 1:
            text += " "
        text += right
        return self._line([(text, t.muted)], align="center")

    def _tracking(self) -> List[str]:
        t = self._theme
        s = self._scene
        heart = "♥" if s.favorite else "♡"
        name = term.truncate(s.title or "未播放", self.width - 8)
        rows = [
            self._line(
                [("▶  ", t.accent), (name, t.accent), ("  ", None), (heart, t.accent if s.favorite else t.muted)],
                align="center",
            ),
            self._line([(term.truncate(s.artist or "—", self.width - 4), t.text)], align="center"),
        ]
        album = term.truncate(s.album, self.width - 6) if s.album else ""
        rows.append(
            self._line([("《" + album + "》", t.muted)], align="center") if album
            else self._line([("", t.muted)], align="center")
        )
        return rows

    def _lyric_rows(self, count: int) -> List[str]:
        t = self._theme
        s = self._scene
        lines = s.lyrics
        if not lines:
            msg = "这首歌没有歌词" if s.title != "未播放" else "还没有开始播放"
            pad = max(0, count // 2 - 1)
            return self._blank(pad) + [
                self._line([("·  " + msg + "  ·", t.muted)], align="center")
            ] + self._blank(max(0, count - pad - 1))
        cur = index_at(lines, s.position)
        span = max(3, count)
        start = max(0, min(cur - span // 2, max(0, len(lines) - span)))
        window = lines[start : start + span]
        out: List[str] = []
        for offset, (_stamp, texts) in enumerate(window):
            idx = start + offset
            if idx == cur:
                color, prefix = t.accent, "▶ "
            elif abs(idx - cur) == 1:
                color, prefix = t.text, "   "
            else:
                color, prefix = t.muted, "   "
            body = term.truncate(" / ".join(texts), self.width - 7)
            out.append(self._line([(prefix, color), (body, color)], align="center"))
        return out

    def _list_rows(self, count: int) -> List[str]:
        t = self._theme
        s = self._scene
        if not s.queue:
            return self._lyric_rows(count)
        total = len(s.queue)
        span = max(1, count)
        start = max(0, min(s.queue_index - span // 2, max(0, total - span)))
        artist_w = max(8, min(18, self.width // 4))
        out: List[str] = []
        for i in range(start, min(total, start + span)):
            name, artist = s.queue[i]
            active = i == s.queue_index
            if active:
                color = t.accent
            elif abs(i - s.queue_index) <= 2:
                color = t.text
            else:
                color = t.muted
            label = term.fit(term.truncate("%2d. %s" % (i + 1, name), self.width - artist_w - 6),
                             self.width - artist_w - 6)
            out.append(
                self._line(
                    [
                        ("▶ " if active else "  ", t.accent if active else t.muted),
                        (label, color),
                        ("  " + term.truncate(artist, artist_w - 2), t.muted),
                    ],
                    align="center",
                )
            )
        return out

    def _progress(self) -> str:
        t, s = self._theme, self._scene
        bar = max(16, min(42, self.width - 22))
        ratio = 0.0 if not s.duration else max(0.0, min(1.0, s.position / s.duration))
        filled = int(bar * ratio)
        now = fmt_time(s.position)
        total = fmt_time(s.duration) if s.duration else "--:--"
        return self._line(
            [("━" * filled, t.accent), ("─" * (bar - filled), t.prog_track),
             ("  %s/%s" % (now, total), t.muted)],
            align="center",
        )

    def _volume(self) -> str:
        t, s = self._theme, self._scene
        bar = max(12, min(28, self.width - 26))
        level = 0 if s.muted else s.volume
        filled = int(bar * level / 100.0)
        cells: List[Cell] = [
            ("VOL  ", t.muted),
            ("▏" * filled, t.accent if not s.muted else t.muted),
            ("▏" * (bar - filled), t.prog_track),
            ("  %d%%" % level, t.text),
        ]
        if s.muted:
            cells.append(("  静音", t.accent))
        return self._line(cells, align="center")

    def _chips(self, items: Sequence[Tuple[str, str]]) -> List[str]:
        t = self._theme
        rows: List[str] = []
        current: List[Cell] = []
        used = 0
        gap = 2
        for key, label in items:
            key_cell = (" " + key + " ", t.bg_header, t.btn_bg)
            label_cell = (" " + label + " ", t.text)
            w = term.str_width(key_cell[0]) + term.str_width(label_cell[0])
            if current and used + gap + w > self.width:
                rows.append(self._line(current, align="center"))
                current, used = [], 0
            if current:
                current.append((" " * gap, None))
                used += gap
            current.append(key_cell)
            current.append(label_cell)
            used += w
        if current:
            rows.append(self._line(current, align="center"))
        return rows

    def _controls(self) -> List[str]:
        s = self._scene
        first = [
            ("SPC", "暂停" if s.playing else "播放"),
            ("←/→", "快退/快进"),
            ("↑/↓", "音量"),
            ("N", "下一首"),
            ("P", "上一首"),
        ]
        second = [
            ("H", "红心" if not s.favorite else "已红心"),
            ("S", MODES[s.mode % len(MODES)]),
            ("L", "歌词"),
            ("T", "列表"),
            ("R", "主题"),
            ("Q", "退出"),
        ]
        return self._chips(first + second)

    # -------------------------------------------------------------- 整屏
    def render(self, scene: Scene) -> str:
        self._scene = scene
        self._theme = scene.theme
        t = scene.theme

        fixed: List[str] = []
        fixed += self._header()
        fixed.append(self._rule(top=True))
        fixed += self._blank(1)
        if time.time() < scene.notice_until and scene.notice:
            fixed.append(self._line([("●  ", t.accent), (term.truncate(scene.notice, self.width - 6), t.accent)], align="center"))
        else:
            label = {"lyrics": "歌词", "list": "播放列表"}.get(scene.view, "正在播放")
            fixed.append(
                self._line(
                    [(label + "  ", t.accent), ("·  " + (scene.source or "本地"), t.muted)],
                    align="center",
                )
            )
        fixed += self._blank(1)
        fixed += self._tracking()
        fixed += self._blank(1)

        tail: List[str] = []
        tail.append(self._progress())
        tail.append(self._blank(1)[0])
        tail.append(self._volume())
        tail += self._blank(1)
        tail += self._controls()
        tail.append(self._rule(top=False))

        middle = self.height - len(fixed) - len(tail)
        middle = max(4, middle)
        if scene.view == "lyrics":
            body = self._lyric_rows(middle)
        elif scene.view == "list":
            body = self._list_rows(middle)
        else:
            body = self._vinyl_rows(middle)

        if len(body) > middle:
            body = body[:middle]
        elif len(body) < middle:
            extra = middle - len(body)
            body = self._blank(extra // 2) + body + self._blank(extra - extra // 2)

        lines = fixed + body + tail
        if len(lines) != self.height:
            # 保险：严格对齐高度，避免终端滚动
            if len(lines) < self.height:
                lines = lines + self._blank(self.height - len(lines))
            else:
                lines = lines[: self.height]
        left = self.margin
        right = max(0, getattr(self, "cols", self.width + left) - left - self.width)
        if left or right:
            prefix = term.bg(t.bg_main) + " " * left
            suffix = term.bg(t.bg_main) + " " * right
            lines = [prefix + line + suffix for line in lines]
        return "\n".join(lines)

    def _vinyl_rows(self, count: int) -> List[str]:
        if self._vinyl is None:
            return self._blank(count)
        s = self._scene
        rows = self._vinyl.rows(s.angle, playing=s.playing, base=s.theme.bg_main)
        out = [self._line(row, align="center") for row in rows]
        if len(out) > count:
            # 行数受限时居中裁掉上下两端
            drop = len(out) - count
            top = drop // 2
            out = out[top : top + count]
        return out
