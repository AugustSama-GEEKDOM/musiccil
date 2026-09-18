"""把播放器放进一个独立的终端窗口运行。

为什么需要它：播放器是交互式 TUI，需要真正的终端（tty）和键盘。当 musiccil 被
agent、脚本或管道调用时，标准输出往往是管道，直接跑只会"在后台一闪而过"看不见。

Windows 上的关键事实：CREATE_NEW_CONSOLE 会让子进程接到一个**全新的控制台**，
于是它的 stdin/stdout/stderr 都是真正的 tty，界面才能正常绘制。但前提是
**不要**给 Popen 传 stdout=/stderr= 重定向——一旦重定向，子进程拿到的是管道，
isatty() 为 False，界面就画不出来。
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from typing import Dict, List, Optional, Sequence

#: 让子进程知道自己已经在窗口里，避免无限递归开窗
MARKER_ENV = "MUSICCIL_IN_WINDOW"
#: 父进程写好、交给窗口播放的临时歌单，子进程读完即删
TEMP_PLAYLIST_ENV = "MUSICCIL_TEMP_PLAYLIST"
#: 新建窗口时希望使用的字符网格 "列,行"；子进程据此调整自己的窗口
SIZE_ENV = "MUSICCIL_WINDOW_SIZE"
#: 仅在「没有 Windows Terminal、只能用裸控制台」时置位：子进程需要自己调窗口尺寸
APPLY_SIZE_ENV = "MUSICCIL_APPLY_SIZE"

TITLE = "musiccil"

#: 默认窗口尺寸：竖屏。比早期版本收敛了一圈（56×48 → 50×42），配合界面里收紧的
#: 唱片占比，整屏不再有大半是唱片；同时窗口没有缩得太狠，免得封面像素不够用。
#: 50×42 实测约 510×840 像素。
DEFAULT_COLS = 50
DEFAULT_ROWS = 42

#: 终端单元格的高宽比约为 2（1 列宽 ≈ 2 倍行高的 1/1，即字高约为字宽的两倍）。
#: 判断"竖屏"必须按像素算，不能直接比较列数和行数——56 列 × 48 行实际是 571×960，
#: 是竖的。
CELL_ASPECT = 2.0


def is_portrait(cols: int, rows: int) -> bool:
    """按像素比例判断是否竖屏。"""
    return rows * CELL_ASPECT > cols


def in_window() -> bool:
    return os.environ.get(MARKER_ENV) == "1"


def parse_size(text: Optional[str]):
    """解析 "列x行" / "列,行" 之类的尺寸写法；非法返回 None。"""
    if not text:
        return None
    parts = text.lower().replace("x", ",").replace(" ", ",").split(",")
    parts = [p for p in parts if p]
    if len(parts) != 2:
        return None
    try:
        cols, rows = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if cols < 20 or rows < 12:
        return None
    return cols, rows


def target_size(explicit: Optional[str] = None):
    """窗口尺寸的优先级：命令行 > 环境变量 > 默认竖屏。"""
    return parse_size(explicit) or parse_size(os.environ.get(SIZE_ENV)) or (
        DEFAULT_COLS,
        DEFAULT_ROWS,
    )


def console_is_visible() -> bool:
    """当前控制台窗口是否真的显示在用户眼前。

    只看 ``isatty()`` 是不够的：在 agent 的 PTY 里 stdout 也是 tty，但那个控制台
    窗口可能是隐藏的、也不在前台——直接跑就变成"在后台运行"，正是要避免的情况。
    """
    if os.name != "nt":
        return True
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        hwnd = k32.GetConsoleWindow()
        if not hwnd:
            return False
        return bool(user32.IsWindowVisible(hwnd)) and user32.GetForegroundWindow() == hwnd
    except Exception:
        return True


def should_open_window(explicit: bool = False, disabled: bool = False) -> bool:
    """判断是否需要另开一个窗口来给用户看。"""
    if disabled or in_window():
        return False
    if explicit:
        return True
    if not sys.stdout.isatty():
        return True
    return not console_is_visible()


def apply_console_size(size=None) -> bool:
    """把当前控制台窗口调成指定的字符网格（仅在裸控制台窗口里有效）。

    顺序很重要：先把窗口缩到最小，再扩缓冲区，最后把窗口撑到目标大小。
    反过来做会因为窗口装不下缓冲区而失败。Windows Terminal 下这些调用只改
    conpty 网格、不会让窗口变竖，所以只在 ``MUSICCIL_APPLY_SIZE`` 置位时才走这里。
    """
    if os.name != "nt":
        return False
    cols, rows = size or target_size()
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return False

    class COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    try:
        k32 = ctypes.windll.kernel32
        handle = k32.GetStdHandle(-11)
        k32.SetConsoleScreenBufferSize.argtypes = [wintypes.HANDLE, COORD]
        k32.SetConsoleScreenBufferSize.restype = wintypes.BOOL
        k32.SetConsoleWindowInfo.argtypes = [
            wintypes.HANDLE, wintypes.BOOL, ctypes.POINTER(wintypes.SMALL_RECT)
        ]
        k32.SetConsoleWindowInfo.restype = wintypes.BOOL

        def rect(l, t, r, b):
            return wintypes.SMALL_RECT(l, t, r, b)

        k32.SetConsoleWindowInfo(handle, True, ctypes.byref(rect(0, 0, 1, 1)))
        if not k32.SetConsoleScreenBufferSize(handle, COORD(cols, rows)):
            return False
        return bool(
            k32.SetConsoleWindowInfo(handle, True, ctypes.byref(rect(0, 0, cols - 1, rows - 1)))
        )
    except Exception:
        return False


def _child_argv(extra_args: Sequence[str]) -> List[str]:
    """优先用已安装的 musiccil 命令，否则退回解释器 + -m。"""
    for name in ("musiccil", "musiccil.exe"):
        found = shutil.which(name)
        if found:
            return [found] + list(extra_args)
    return [sys.executable, "-m", "musiccil"] + list(extra_args)


def _base_env() -> Dict[str, str]:
    env = dict(os.environ)
    env[MARKER_ENV] = "1"
    return env


def _spawn_windows_terminal(
    argv: List[str],
    cwd: Optional[str],
    env: Dict[str, str],
    size,
) -> Optional[int]:
    """用 Windows Terminal 开一个能精确指定字符网格、且真的是竖屏的窗口。

    直接给子进程 CREATE_NEW_CONSOLE 只能改 conpty 的网格，Windows Terminal 的
    窗口尺寸不会跟着变成竖屏；``wt --size 列,行`` 才是让窗口本身变竖的正路。
    """
    wt = shutil.which("wt.exe") or shutil.which("wt")
    if not wt:
        return None
    cols, rows = size
    command = " ".join(subprocess.list2cmdline([a]) for a in argv)
    full = "cd /d \"%s\" && %s" % (cwd or os.getcwd(), command) if os.name == "nt" else command
    try:
        proc = subprocess.Popen(
            [wt, "-w", "-1", "--size", "%d,%d" % (cols, rows), "cmd", "/c", full],
            cwd=cwd,
            env=env,
            close_fds=True,
        )
    except OSError:
        return None
    return proc.pid


def spawn(
    extra_args: Sequence[str],
    cwd: Optional[str] = None,
    temp_playlist: Optional[str] = None,
    size=None,
) -> Optional[int]:
    """在新窗口里启动播放器；成功返回 pid，无法开窗返回 None。

    调用方**不要**重定向子进程输出，否则窗口里拿不到 tty。
    """
    argv = _child_argv(extra_args)
    env = _base_env()
    if temp_playlist:
        env[TEMP_PLAYLIST_ENV] = temp_playlist
    cols, rows = size or target_size()
    env[SIZE_ENV] = "%d,%d" % (cols, rows)

    if os.name == "nt":
        pid = _spawn_windows_terminal(argv, cwd, env, (cols, rows))
        if pid is not None:
            return pid
        # 没有 Windows Terminal 就用普通控制台，尺寸由子进程自己调（见 apply_console_size）
        env[APPLY_SIZE_ENV] = "1"
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010),
            close_fds=True,
        )
        return proc.pid

    if sys.platform == "darwin":
        inner = "cd %s && exec %s" % (
            shlex.quote(cwd or os.getcwd()),
            " ".join(shlex.quote(a) for a in argv),
        )
        script = 'tell application "Terminal" to do script "%s"' % inner.replace('"', '\\"')
        return subprocess.Popen(["osascript", "-e", script]).pid

    shell_cmd = "cd %s && exec %s" % (
        shlex.quote(cwd or os.getcwd()),
        " ".join(shlex.quote(a) for a in argv),
    )
    for candidate in (
        ("x-terminal-emulator", "-e"),
        ("gnome-terminal", "--"),
        ("konsole", "-e"),
        ("xfce4-terminal", "-e"),
        ("xterm", "-e"),
    ):
        if not shutil.which(candidate[0]):
            continue
        proc = subprocess.Popen(list(candidate) + ["sh", "-c", shell_cmd], cwd=cwd, env=env)
        return proc.pid
    return None
