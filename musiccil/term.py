"""终端基础设施：ANSI 真彩色、显示宽度计算、定宽行渲染、控制台初始化。"""

from __future__ import annotations

import os
import sys
import unicodedata

RESET = "\x1b[0m"
FG_DEFAULT = "\x1b[39m"

_WIDE = frozenset(("F", "W"))
_fg_cache: dict = {}
_bg_cache: dict = {}
_width_cache: dict = {}


def fg(c) -> str:
    s = _fg_cache.get(c)
    if s is None:
        s = _fg_cache[c] = f"\x1b[38;2;{c[0]};{c[1]};{c[2]}m"
    return s


def bg(c) -> str:
    s = _bg_cache.get(c)
    if s is None:
        s = _bg_cache[c] = f"\x1b[48;2;{c[0]};{c[1]};{c[2]}m"
    return s


def char_width(ch: str) -> int:
    w = _width_cache.get(ch)
    if w is None:
        if unicodedata.combining(ch) or ch < " ":
            w = 0
        else:
            w = 2 if unicodedata.east_asian_width(ch) in _WIDE else 1
        _width_cache[ch] = w
    return w


def str_width(s: str) -> int:
    """终端实际显示宽度（中文/全角算 2 列）。"""
    if s.isascii():
        return len(s)
    return sum(char_width(c) for c in s)


def truncate(s: str, width: int, ellipsis: str = "…") -> str:
    """按显示宽度截断，超长时补省略号。"""
    if width <= 0:
        return ""
    if str_width(s) <= width:
        return s
    ew = str_width(ellipsis)
    if ew > width:
        return ""
    out = []
    used = 0
    for ch in s:
        w = char_width(ch)
        if used + w > width - ew:
            break
        out.append(ch)
        used += w
    return "".join(out) + ellipsis


def _fit_cells(cells, width):
    """内容超宽时丢弃尾部单元格，必要时截断最后一格。"""
    out = []
    used = 0
    for cell in cells:
        text = cell[0]
        w = str_width(text)
        if used + w <= width:
            out.append(cell)
            used += w
            continue
        room = width - used
        if room > 0:
            cut = truncate(text, room, "")
            if cut:
                out.append((cut,) + tuple(cell[1:]))
                used += str_width(cut)
        break
    return out, used


def render_line(cells, width: int, bg_default, align: str = "left") -> str:
    """把 (text, fg|None, bg|None) 单元格序列渲染为恰好 width 列的一行。"""
    cells = [c if isinstance(c, tuple) else (c,) for c in cells]
    total = sum(str_width(c[0]) for c in cells)
    if total > width:
        cells, total = _fit_cells(cells, width)
    pad = max(0, width - total)
    if align == "center":
        left = pad // 2
    elif align == "right":
        left = pad
    else:
        left = 0
    out = [bg(bg_default)]
    if left:
        out.append(" " * left)
    for cell in cells:
        text = cell[0]
        f = cell[1] if len(cell) > 1 else None
        b = cell[2] if len(cell) > 2 and cell[2] is not None else bg_default
        out.append(bg(b))
        out.append(FG_DEFAULT if f is None else fg(f))
        out.append(text)
    right = pad - left
    if right:
        out.append(bg(bg_default))
        out.append(" " * right)
    out.append(RESET)
    return "".join(out)


def fit(s: str, width: int, align: str = "left") -> str:
    """纯文本定宽（用于测试与调试输出）。"""
    s = truncate(s, width)
    pad = width - str_width(s)
    if pad <= 0:
        return s
    if align == "center":
        left = pad // 2
        return " " * left + s + " " * (pad - left)
    if align == "right":
        return " " * pad + s
    return s + " " * pad


def term_size(fallback=(100, 30)):
    try:
        size = os.get_terminal_size()
        return size.columns, size.lines
    except OSError:
        pass
    try:
        import shutil

        size = shutil.get_terminal_size(fallback)
        return size.columns, size.lines
    except Exception:
        return fallback


def init_console() -> None:
    """开启 Windows VT 处理并统一 UTF-8 输出，保证真彩色转义与中文正常。"""
    if os.name == "nt":
        try:
            import ctypes

            k = ctypes.windll.kernel32
            for handle_id in (-11, -12):
                h = k.GetStdHandle(handle_id)
                mode = ctypes.c_uint32()
                if k.GetConsoleMode(h, ctypes.byref(mode)):
                    # ENABLE_PROCESSED_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING
                    k.SetConsoleMode(h, mode.value | 0x0001 | 0x0004)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def set_title(text: str) -> None:
    # 标题只是装饰，不值得为它崩：输出被重定向到 GBK 管道、或测试里换成
    # 别的流时，「♪」这类字符会编码失败。这时安静跳过即可。
    try:
        sys.stdout.write(f"\x1b]0;{text}\x07")
        sys.stdout.flush()
    except (UnicodeEncodeError, ValueError, OSError):
        pass


def enter_screen(bg_rgb) -> None:
    """切到备用屏幕缓冲区并统一终端底色。"""
    sys.stdout.write(
        "\x1b[?1049h"          # 备用屏幕
        "\x1b[2J\x1b[H"
        "\x1b[?25l"            # 隐藏光标
    )
    if bg_rgb:
        sys.stdout.write(f"\x1b]11;rgb:{bg_rgb[0]:02x}/{bg_rgb[1]:02x}/{bg_rgb[2]:02x}\x1b\\")
    sys.stdout.flush()


def exit_screen() -> None:
    sys.stdout.write(
        "\x1b[0m"
        "\x1b]110\x1b\\\x1b]111\x1b\\"
        "\x1b[?25h"            # 恢复光标
        "\x1b[?1049l"          # 回到主屏幕
    )
    sys.stdout.flush()


def set_term_bg(bg_rgb) -> None:
    """同步终端底色（OSC 11），非交互输出时跳过以免污染管道。"""
    if bg_rgb and sys.stdout.isatty():
        sys.stdout.write(f"\x1b]11;rgb:{bg_rgb[0]:02x}/{bg_rgb[1]:02x}/{bg_rgb[2]:02x}\x1b\\")
