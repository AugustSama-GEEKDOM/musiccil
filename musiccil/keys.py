"""跨平台非阻塞按键读取。"""

from __future__ import annotations

import os
import sys

if os.name == "nt":
    import msvcrt

    _SPECIAL = {
        b"H": "UP",
        b"P": "DOWN",
        b"K": "LEFT",
        b"M": "RIGHT",
    }

    def get_key():
        if not msvcrt.kbhit():
            return None
        ch = msvcrt.getch()
        if ch in (b"\x00", b"\xe0"):
            return _SPECIAL.get(msvcrt.getch())
        if ch == b" ":
            return "SPACE"
        if ch in (b"\r", b"\n"):
            return "ENTER"
        if ch == b"\x1b":
            return "ESC"
        try:
            return ch.decode("utf-8").upper()
        except UnicodeDecodeError:
            return None

else:
    import select
    import termios
    import tty

    def get_key():
        if not select.select([sys.stdin], [], [], 0)[0]:
            return None
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            seq = sys.stdin.read(2)
            return {"[A": "UP", "[B": "DOWN", "[D": "LEFT", "[C": "RIGHT"}.get(seq)
        if ch == " ":
            return "SPACE"
        if ch in ("\r", "\n"):
            return "ENTER"
        if ch == "\x03":
            raise KeyboardInterrupt
        return ch.upper() if ch.isprintable() else None

def raw_mode():
    """进入/退出原始模式，返回一个清理回调。"""
    if os.name == "nt":
        return lambda: None
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)

    def restore():
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    return restore
