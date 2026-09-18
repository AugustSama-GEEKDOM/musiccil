#!/usr/bin/env python3
"""在独立终端窗口里启动 musiccil 播放器。

主流程本来就是直接跑 ``musiccil`` 自动开窗（见 SKILL.md）。这个脚本留给两种情况：

* 想换一个特定的终端模拟器 / 自己控制窗口启动方式；
* 需要核查开窗逻辑本身。

它会用和播放器内置相同的机制（Windows: CREATE_NEW_CONSOLE）拉起窗口，
并刻意不重定向子进程输出——否则新控制台里拿不到 tty，界面画不出来。
"""

from __future__ import annotations

import os
import subprocess
import sys

def _default_project_dir() -> str:
    """定位项目根目录。

    优先用环境变量 MUSICCIL_HOME；否则从本脚本位置往上找含 musiccil 包的那一层
    （技能在仓库里是 skills/musiccil/scripts/，所以往上三级）。
    这样技能被复制到 ~/.codex/skills 之后仍然能用，不依赖任何绝对路径。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        if os.path.isfile(os.path.join(here, "musiccil", "__init__.py")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return ""


PROJECT_DIR = os.environ.get("MUSICCIL_HOME") or _default_project_dir()


def _bootstrap() -> None:
    """直接 import 项目里的 window 模块；不在项目目录也能用。"""
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__)
        return 2
    _bootstrap()
    try:
        from musiccil import window
    except ImportError:
        print("找不到 musiccil 包，请确认项目路径：%s" % PROJECT_DIR, file=sys.stderr)
        return 2

    # 纯输出型调用不需要窗口，就地跑并把结果交回调用方
    if {"--search", "--json-out", "--render-once"} & set(args):
        return subprocess.call(
            [sys.executable, "-m", "musiccil"] + args, cwd=PROJECT_DIR
        )

    size_text = None
    argv = list(args)
    if "--window-size" in argv:
        i = argv.index("--window-size")
        if i + 1 < len(argv):
            size_text = argv[i + 1]
        del argv[i : i + 2]
    pid = window.spawn(argv, cwd=PROJECT_DIR, size=window.parse_size(size_text))
    if pid is None:
        print(
            "无法打开独立终端窗口。请在终端里直接运行：\n  musiccil %s\n"
            % " ".join(args),
            file=sys.stderr,
        )
        return 2
    cols, rows = window.target_size(size_text)
    print("已在独立窗口打开播放器（pid %d，%d×%d 竖屏）。" % (pid, cols, rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
