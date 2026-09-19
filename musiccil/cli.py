"""命令行入口：解析曲目来源、执行搜索、启动播放器或输出 JSON。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import List, Optional

from . import __version__, term, window
from .api import (
    ApiError,
    KEY_HELP,
    KEY_FILE,
    MusicApi,
    Track,
    _artist_set,
    check_key,
    normalize_platform,
    pick_mix,
    api_site_url,
    resolve_key,
    save_key,
    song_key,
)
from .local import (
    AUDIO_EXT,
    is_url,
    looks_like_json,
    read_json_source,
    read_m3u,
    scan_directory,
    track_from_directive,
    track_from_file,
    track_from_url,
)

EPILOG = """\
示例:
  musiccil "加州梦游"                   # 搜索网易云并播放
  musiccil "周杰伦" -p qq -l 8          # 指定平台与数量
  musiccil --list 149553                # 播放网易云歌单
  musiccil --toplist 3778678            # 播放排行榜
  musiccil --user 123456 --pick 0       # 播放某用户收藏的第一个歌单
  musiccil qq:001I6gzS3LufWy            # 用 平台:ID 直接播放
  musiccil D:\\Music\\song.flac           # 播放本地文件
  musiccil D:\\Music                    # 播放整个目录
  musiccil --search "晚安" --json-out    # 只搜索，输出 JSON 给上层调用
  musiccil --playlist-out p.json "晚安"  # 搜索并生成可供播放器直接用的歌单文件
  musiccil --file p.json                # 播放 JSON 歌单（上层推荐结果）
  musiccil --mix p.json "深夜 city pop"  # 一条命令完成推荐歌单并开窗播放
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="musiccil",
        description="终端唱片机播放器：封面主题色 + 旋转像素封面 + 真实播放（mpv 后端）",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("query", nargs="*", help="搜索关键词 / 文件路径 / 目录 / m3u / 平台:ID / 音频直链")
    p.add_argument("-p", "--platform", default="wy", help="平台：wy/qq/kw/kg/mg/qi（默认 wy）")
    p.add_argument("-l", "--limit", type=int, default=10, help="搜索数量（默认 10）")
    p.add_argument("--page", type=int, default=1, help="搜索页码（默认 1）")
    p.add_argument("--list", dest="playlist_id", help="歌单 ID")
    p.add_argument("--toplist", dest="toplist_id", help="排行榜 ID")
    p.add_argument("--user", dest="uid", help="用户 UID，取其歌单")
    p.add_argument("--pick", type=int, default=0, help="配合 --user，选择第几个歌单（从 0 开始）")
    p.add_argument("--file", dest="json_file", action="append", default=[],
                   help="JSON 曲目文件/字符串，可重复（Agent 推荐结果可直接喂进来）")
    p.add_argument("--stdin-json", action="store_true", help="从标准输入读取 JSON 曲目")
    p.add_argument("--search", action="store_true", help="只搜索不播放")
    p.add_argument("--json-out", action="store_true", help="把结果输出为 JSON（隐含 --search）")
    p.add_argument("--playlist-out", metavar="PATH", help="把结果写成 JSON 歌单文件")
    p.add_argument("--no-resolve", action="store_true", help="搜索时不预取音频/封面/歌词（更快）")
    p.add_argument("--brief", action="store_true",
                   help="只取筛选必需的低带宽字段（关键词搜索出的 id/type/name/artist/album），"
                        "完全跳过逐首详情请求；配合 --json-out 可把输出体积压到十分之一以下")
    p.add_argument("--compact-json", action="store_true",
                   help="--json-out 时省略空字段与 extra，进一步压缩输出体积")
    p.add_argument("--book", metavar="PATH",
                   help="一步到位：搜索 → 自动挑最匹配的一首 → 解析音频/封面/歌词 → "
                        "写成歌单文件并直接开窗播放。省掉中间的多次工具往返")
    p.add_argument("--mix", metavar="PATH",
                   help="一步到位（推荐歌单）：一次搜索 → 自动去重/去live版/限同歌手数量 → "
                        "并发解析音频/封面/歌词 → 写歌单并开窗播放。一条命令顶掉整条推荐流程")
    p.add_argument("--mix-count", type=int, default=8,
                   help="配合 --mix：最终歌单收录几首（默认 8）")
    p.add_argument("--seed", default="",
                   help="配合 --mix：先放这首指定歌曲（排第一），其余按关键词推荐。"
                        "用于「我想听 X，再推荐几首」这类请求")
    p.add_argument("--per-artist", type=int, default=2,
                   help="配合 --mix：同一位歌手最多几首（默认 2，0 为不限制）")
    p.add_argument("--artist", default="", help="配合 --book，指定歌手以提高匹配准确度")
    p.add_argument("--book-count", type=int, default=1,
                   help="配合 --book：取前 N 首（默认 1，即只播最匹配的那首）")
    p.add_argument("--no-play", action="store_true",
                   help="配合 --book/--mix：只生成歌单文件，不开窗播放")
    p.add_argument("--no-audio", action="store_true", help="只渲染界面不发声（调试/截图）")
    p.add_argument("--offline", action="store_true", help="只用本地缓存的封面，不联网")
    p.add_argument("--volume", type=int, default=80, help="初始音量 0-100（默认 80）")
    p.add_argument("--favorites", help="红心歌曲 ID 列表文件（每行一个）")
    p.add_argument("--key", help="接口密钥，默认取环境变量 MUSICCIL_API_KEY")
    p.add_argument("--setup", action="store_true",
                   help="配置接口密钥：提示你去 musiccil API 调用站免费领一把，粘贴后校验并保存到本地，"
                        "以后所有命令都自动用它")
    p.add_argument("--no-setup", action="store_true",
                   help="没有密钥时不要弹出配置引导，直接按错误退出（脚本/CI 用）")
    p.add_argument("--width", type=int, default=0, help="强制界面宽度（列），0 为自动")
    p.add_argument("--render-once", action="store_true", help="只渲染一帧到标准输出后退出")
    p.add_argument("--window", action="store_true",
                   help="在独立终端窗口里打开播放器（默认：输出不是终端时自动开窗）")
    p.add_argument("--no-window", action="store_true",
                   help="强制在当前终端里运行，不要另开窗口")
    p.add_argument("--window-size", metavar="COLSxROWS", default=None,
                   help="独立窗口的字符尺寸，默认竖屏 70x56")
    p.add_argument("--spin", type=float, default=None, metavar="DEG_PER_SEC",
                   help="唱片转速（度/秒），默认 15（约 24 秒一圈）；调大转更快")
    p.add_argument("--theme-fade", type=float, default=None, metavar="SECONDS",
                   help="换曲时主题配色的过渡时长（秒），默认 0.9；0 为不做过渡直接换色")
    p.add_argument("--audio-fade", type=float, default=None, metavar="SECONDS",
                   help="切歌/快进快退时的音频淡入淡出时长（秒），默认 0.22；0 为直接切换")
    p.add_argument("--no-media-controls", action="store_true",
                   help="不注册系统媒体通知（Windows 任务栏/媒体键的控制与信息显示）")
    p.add_argument("--version", action="version", version="musiccil " + __version__)
    return p


def _read_favorites(path: Optional[str]) -> set:
    if not path:
        return set()
    try:
        with open(expand_path(path), "r", encoding="utf-8") as fh:
            return {line.strip() for line in fh if line.strip() and not line.startswith("#")}
    except OSError:
        return set()


def dump_json(payload) -> None:
    term.init_console()
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _slim(track: dict) -> dict:
    """去掉空字段与 extra，只留真正有内容的部分，便于 agent 少读点东西。"""
    out = {}
    for key, value in track.items():
        if key == "extra" or value in (None, "", 0, 0.0, [], {}):
            continue
        out[key] = value
    return out


def dump_tracks(tracks: List[Track], compact: bool = False) -> None:
    payload = [t.to_dict() for t in tracks]
    if compact:
        payload = [_slim(t) for t in payload]
    if compact and payload and all(len(t) <= 7 for t in payload):
        # 筛选结果每首一行就够，比缩进 JSON 好读也好接管道
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _book(args, api: MusicApi) -> int:
    """一步到位：搜索 → 挑选 → 解析资源 → 写歌单 → （默认）开窗播放。

    设计目的是把 agent 的往返次数从"搜一次、读结果、写文件、再解析、再播放"
    压缩成一次调用；接口侧只需 2 次请求（search + info）。
    """
    query = " ".join(args.query).strip()
    if not query:
        sys.stderr.write("--book 需要提供歌名，例如：musiccil --book p.json \"歌名\" --artist 歌手\n")
        return 2

    t0 = time.perf_counter()
    picked: List[Track] = []
    if args.artist:
        # 带歌手时：指定平台找不到原唱就自动换平台（周杰伦等在网易云常缺席）
        first = api.best_across_platforms(
            query, args.artist, args.platform, candidates=max(8, args.book_count * 4)
        )
        if first is not None:
            picked.append(first)
    if not picked:
        try:
            hits = api.search(query, args.platform, page=args.page, limit=max(args.book_count, 1))
        except ApiError as exc:
            sys.stderr.write("搜索失败：%s\n" % exc)
            return 2
        if not hits:
            sys.stderr.write("没有搜到「%s」\n" % query)
            return 1
        picked = hits[: max(1, args.book_count)]

    try:
        resolved = api.resolve(picked)
    except ApiError as exc:
        sys.stderr.write("解析失败：%s\n" % exc)
        return 2

    playable = [t for t in resolved if t.url]
    if not playable:
        sys.stderr.write("这首取不到音频直链（多为版权限制），换 wy/kg 平台或换一首试试\n")
        return 1

    path = _write_playlist(args.book, playable)
    first = playable[0]
    elapsed = time.perf_counter() - t0
    sys.stderr.write(
        "已选中：%s — %s（%s，%.1fs）\n已写歌单：%s\n"
        % (first.name, first.display_artist, first.platform, elapsed, path)
    )
    if args.no_play:
        return 0

    return _open_window(args, path, len(playable))


def _needs_api(args) -> bool:
    """这条命令会不会真的联网。

    播本地文件/目录/直链完全不需要接口密钥，本地用户不该被一屏配置提示拦住；
    反过来只要可能联网就返回 True，宁可多问一句也别让流程跑到一半才报错。
    """
    if args.book or args.mix or args.search or args.json_out or args.playlist_out:
        return True
    if args.playlist_id or args.toplist_id or args.uid:
        return True
    if args.json_file or args.stdin_json:
        # 歌单文件里的曲目仍要逐首取音频/封面/歌词，除非显式跳过
        return not (args.no_resolve or args.brief)
    query = " ".join(args.query).strip()
    if not query:
        return False
    # 本地路径、直链、内联 JSON 都不经过接口
    if is_url(query) or os.path.exists(query) or looks_like_json(query):
        return False
    return True


def _prompt_key(site: str) -> str:
    """问用户要密钥；取消或 EOF 时返回空串。"""
    sys.stdout.write(
        "musiccil 联网取歌需要一把接口密钥（按次计配额）。\n"
        "免费领取：注册 %s，登录后在页面里复制密钥。\n"
        "把密钥粘贴到这里再回车：\n"
        "> " % site
    )
    sys.stdout.flush()
    try:
        return input().strip()
    except (EOFError, KeyboardInterrupt):
        sys.stdout.write("\n已取消。\n")
        return ""


def _save_verified_key(value: str) -> int:
    """校验并保存密钥。校验不过的密钥不落盘，免得后面每首歌都白报一次错。"""
    value = (value or "").strip()
    if not value:
        sys.stderr.write("没有拿到密钥。\n" + KEY_HELP + "\n")
        return 2
    info = check_key(value)
    if not info.ok:
        sys.stderr.write(
            "密钥不可用：%s\n"
            "请到 %s 重新复制一个（注意别把换行、引号一起粘进来）。\n"
            "也可以让 Agent 代你保存：musiccil --setup --key <密钥>\n"
            % (info.message, api_site_url())
        )
        return 2
    path = save_key(value)
    if info.known:
        sys.stdout.write("密钥有效，已保存到 %s\n" % path)
        if info.email:
            sys.stdout.write("账号：%s\n" % info.email)
        sys.stdout.write("剩余配额：%s 次（每天签到还会再补）\n" % info.quota)
    else:
        # 探测不到站点时按「未知」处理：照样保存，别把用户拦在门外
        sys.stdout.write("已保存到 %s\n提示：%s\n" % (path, info.message))
    sys.stdout.write('以后直接运行 musiccil "歌名" 就行，不用再带密钥。\n')
    return 0


def setup_key(args) -> int:
    """--setup：配置密钥。

    带 --key 时是非交互的（Agent 可以直接代用户保存），不带时走交互提示；
    两条路都会先校验再落盘。
    """
    value = (args.key or "").strip()
    if not value:
        if not sys.stdin.isatty():
            sys.stderr.write(KEY_HELP + "\n")
            sys.stderr.write(
                "当前环境不是交互终端。让 Agent 执行：musiccil --setup --key <密钥>\n"
            )
            return 2
        value = _prompt_key(api_site_url())
    return _save_verified_key(value)


def ensure_key(args, api: MusicApi) -> Optional[int]:
    """第一次使用时的密钥引导；返回 None 表示可以继续，否则是退出码。"""
    if api.key:
        return None
    if not _needs_api(args):
        # 播本地文件/直链不需要接口，没密钥也照常放行
        return None
    if args.no_setup:
        sys.stderr.write(KEY_HELP + "\n")
        return 2
    if sys.stdin.isatty():
        sys.stdout.write("第一次使用，先花十秒配好接口密钥。\n")
        value = _prompt_key(api_site_url())
        if value:
            code = _save_verified_key(value)
            if code:
                return code
            api.key = value
            return None
    sys.stderr.write(KEY_HELP + "\n")
    if not sys.stdin.isatty():
        sys.stderr.write(
            "当前环境不是交互终端。让 Agent 执行：musiccil --setup --key <密钥>\n"
        )
    return 2

def expand_path(path: str) -> str:
    """把用户/agent 写的路径展开成绝对路径。

    ``%TEMP%`` 这种 Windows 变量在 PowerShell 里不会自动展开（那是 cmd 的语法），
    agent 照文档抄下来就会拿到一个字面含 ``%TEMP%`` 的目录而报
    ``FileNotFoundError``；``~`` 同理。所以两种写法都在这里统一处理。
    """
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def _write_playlist(path: str, tracks: List[Track]) -> str:
    path = expand_path(path)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([t.to_dict() for t in tracks], fh, ensure_ascii=False, indent=2)
    return path


def _mix(args, api: MusicApi) -> int:
    """一步到位的推荐歌单：搜（必要时翻页）→ 自动挑 → 并发解析 → 写歌单 → 开窗播放。

    存在的理由和 ``--book`` 一样：命令本身一两秒就能跑完，慢的是 agent 把
    "搜索、读候选、写 JSON、再播放" 拆成好几轮工具调用。用户实测一次推荐要
    五分钟，几乎全花在这些往返上，所以把整条链路压进一条命令。
    """
    query = " ".join(args.query).strip()
    if not query:
        sys.stderr.write('--mix 需要提供风格描述，例如：musiccil --mix p.json "深夜 city pop"\n')
        return 2

    t0 = time.perf_counter()
    want = max(1, args.mix_count)
    # --seed：先定位用户点名的那一首，它会排在歌单第一位。
    # 用 --artist 一起指定时走跨平台匹配，能避开翻唱。
    seed: Optional[Track] = None
    if args.seed:
        if args.artist:
            seed = api.best_across_platforms(
                args.seed, args.artist, args.platform, candidates=8
            )
        if seed is None:
            try:
                seed_hits = api.search(args.seed, args.platform, limit=5)
            except ApiError as exc:
                sys.stderr.write("搜索种子歌曲失败：%s\n" % exc)
                return 2
            if args.artist:
                want_artist = set(_artist_set(args.artist))
                seed = next(
                    (t for t in seed_hits if want_artist and want_artist & set(_artist_set(t.artist))),
                    None,
                )
            seed = seed or (seed_hits[0] if seed_hits else None)
        if seed is None:
            sys.stderr.write("没有找到种子歌曲「%s」，将只按关键词推荐\n" % args.seed)

    exclude = {song_key(seed)} if seed is not None else None
    # 种子占掉一个位置，补推荐时少要一首
    want_rest = max(0, want - (1 if seed is not None else 0))
    # 多搜一些候选，足够挑掉 live/remix 与同歌手重复后仍能凑满 want 首。
    # 有些关键词前几条会被同一张专辑的重复条目占满（见 pick_mix 的注释），
    # 所以挑不满时再自动翻一页，别让歌单缩水。
    hits: List[Track] = []
    picked: List[Track] = []
    if want_rest:
        for page, fetch in (
            (args.page, max(want_rest * 2, want_rest + 6)),
            (args.page + 1, want_rest * 3),
        ):
            try:
                page_hits = api.search(query, args.platform, page=page, limit=fetch)
            except ApiError as exc:
                sys.stderr.write("搜索失败：%s\n" % exc)
                return 2
            seen = {"%s:%s" % (t.type, t.id) for t in hits}
            hits.extend(t for t in page_hits if "%s:%s" % (t.type, t.id) not in seen)
            picked = pick_mix(
                hits,
                count=want_rest,
                per_artist=max(0, args.per_artist),
                exclude=exclude,
            )
            if len(picked) >= want_rest or not page_hits:
                break
        if not hits and seed is None:
            sys.stderr.write("没有搜到「%s」\n" % query)
            return 1
        if not picked and seed is None:
            picked = hits[:want_rest]

    # 种子歌曲永远排第一位
    picked = ([seed] if seed is not None else []) + picked

    try:
        resolved = api.resolve(picked)
    except ApiError as exc:
        sys.stderr.write("解析失败：%s\n" % exc)
        return 2
    playable = [t for t in resolved if t.url]
    if not playable:
        sys.stderr.write("这些候选都取不到音频直链（多为版权限制），换个关键词或平台再试\n")
        return 1

    path = _write_playlist(args.mix, playable)
    elapsed = time.perf_counter() - t0
    sys.stderr.write(
        "已生成歌单：%d 首（%.1fs）→ %s\n%s\n"
        % (
            len(playable),
            elapsed,
            path,
            "\n".join(
                "  %d. %s — %s" % (i + 1, t.name, t.display_artist)
                for i, t in enumerate(playable)
            ),
        )
    )
    if args.no_play:
        return 0
    return _open_window(args, path, len(playable), temp_playlist=None)


def _open_window(args, path: str, count: int, temp_playlist=None) -> int:
    """把已落盘的歌单交给一个独立终端窗口播放。"""
    child_args = ["--file", path]
    if args.no_audio:
        child_args.append("--no-audio")
    if args.offline:
        child_args.append("--offline")
    if args.key:
        child_args += ["--key", args.key]
    if args.volume != 80:
        child_args += ["--volume", str(args.volume)]
    if args.favorites:
        child_args += ["--favorites", args.favorites]
    if args.width:
        child_args += ["--width", str(args.width)]
    if args.spin:
        child_args += ["--spin", str(args.spin)]
    if args.theme_fade is not None:
        child_args += ["--theme-fade", str(args.theme_fade)]
    if args.audio_fade is not None:
        child_args += ["--audio-fade", str(args.audio_fade)]
    if args.no_media_controls:
        child_args.append("--no-media-controls")

    pid = window.spawn(
        child_args,
        cwd=os.getcwd(),
        temp_playlist=temp_playlist,
        size=window.parse_size(args.window_size),
    )
    if pid is None:
        sys.stderr.write("无法打开独立终端窗口。请在终端里直接运行：\n  musiccil --file %s\n" % path)
        return 2
    cols, rows = window.target_size(args.window_size)
    sys.stderr.write(
        "已在独立窗口打开播放器（pid %d，共 %d 首，%d×%d 竖屏）。\n"
        "窗口里的按键：SPC 播放/暂停  ←/→ 快退快进  ↑/↓ 音量  N/P 上下首  "
        "H 红心  S 模式  L 歌词  T 列表  R 主题  Q 退出\n" % (pid, count, cols, rows)
    )
    return 0


def _launch_window(args, tracks: List[Track]) -> int:
    """把已解析好的曲目写成临时歌单，交给一个独立终端窗口去播放。

    这样窗口里的子进程不必重新联网解析，既省接口配额，也保证播放的就是
    用户刚看到的这批歌。
    """
    import tempfile

    handle, path = tempfile.mkstemp(prefix="musiccil-", suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        json.dump([t.to_dict() for t in tracks], fh, ensure_ascii=False)

    child_args = ["--file", path]
    if args.no_audio:
        child_args.append("--no-audio")
    if args.offline:
        child_args.append("--offline")
    if args.key:
        child_args += ["--key", args.key]
    if args.volume != 80:
        child_args += ["--volume", str(args.volume)]
    if args.favorites:
        child_args += ["--favorites", args.favorites]
    if args.width:
        child_args += ["--width", str(args.width)]
    if args.spin:
        child_args += ["--spin", str(args.spin)]
    if args.theme_fade is not None:
        child_args += ["--theme-fade", str(args.theme_fade)]
    if args.audio_fade is not None:
        child_args += ["--audio-fade", str(args.audio_fade)]
    if args.no_media_controls:
        child_args.append("--no-media-controls")

    size = window.parse_size(args.window_size)
    pid = window.spawn(child_args, cwd=os.getcwd(), temp_playlist=path, size=size)
    if pid is None:
        sys.stderr.write(
            "无法打开独立终端窗口。请在终端里直接运行：\n"
            "  musiccil --file %s\n" % path
        )
        return 2
    cols, rows = window.target_size(args.window_size)
    sys.stderr.write(
        "已在独立窗口打开播放器（pid %d，共 %d 首，%d×%d 竖屏）。\n"
        "窗口里的按键：SPC 播放/暂停  ←/→ 快退快进  ↑/↓ 音量  N/P 上下首  "
        "H 红心  S 模式  L 歌词  T 列表  R 主题  Q 退出\n"
        % (pid, len(tracks), cols, rows)
    )
    return 0


def collect_from_arguments(args, api: MusicApi) -> List[Track]:
    """按优先级把命令行里的各种来源解析成一组 Track。"""
    tracks: List[Track] = []
    query = " ".join(args.query).strip()

    if args.playlist_id:
        payload = api.playlist(args.playlist_id, args.platform)
        tracks = api.tracks_from_list(payload, normalize_platform(args.platform) or args.platform)
        if not tracks:
            raise ApiError("歌单里没有取到歌曲")
        return tracks

    if args.toplist_id:
        payload = api.toplist(args.toplist_id, args.platform)
        tracks = api.tracks_from_list(payload, normalize_platform(args.platform) or args.platform)
        if not tracks:
            raise ApiError("排行榜里没有取到歌曲")
        return tracks

    if args.uid:
        playlists = api.user_playlists(args.uid, args.platform)
        if not playlists:
            raise ApiError("没有取到这个用户的歌单")
        pick = max(0, min(args.pick, len(playlists) - 1))
        chosen = playlists[pick]
        list_id = str(chosen.get("id") or "")
        payload = api.playlist(list_id, args.platform)
        tracks = api.tracks_from_list(payload, normalize_platform(args.platform) or args.platform)
        if not tracks:
            raise ApiError("歌单里没有取到歌曲：%s" % chosen.get("name"))
        return tracks

    if not query:
        return []

    if looks_like_json(query):
        return read_json_source(query)

    directive = track_from_directive(query, args.platform)
    if directive.id:
        return [directive]

    if os.path.exists(query):
        if os.path.isdir(query):
            return scan_directory(query)
        ext = os.path.splitext(query)[1].lower()
        if ext in AUDIO_EXT:
            return [track_from_file(query)]
        if ext in (".m3u", ".m3u8"):
            return read_m3u(query)
        if ext in (".json", ".txt"):
            try:
                return read_json_source(query)
            except (ValueError, OSError):
                return []
        return [track_from_file(query)]

    if is_url(query):
        return [track_from_url(query)]

    return api.search(query, args.platform, args.page, args.limit)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    # --setup：配置密钥。放在最前面，不连网、不解析曲目，纯粹是配置动作。
    if getattr(args, "setup", False):
        return setup_key(args)

    term.init_console()
    api = MusicApi(key=args.key)
    tracks: List[Track] = []
    error: Optional[str] = None

    # 第一次用（本地没有密钥）时引导一次；拿到就不打断后续流程
    blocked = ensure_key(args, api)
    if blocked is not None:
        return blocked

    # --book：一条命令走完"搜→挑→解析→落盘→播放"，避免多次工具往返
    if args.mix:
        return _mix(args, api)

    if args.book:
        return _book(args, api)

    # 由 _launch_window 启动时，临时歌单放在环境变量里，用完全局即可删除，
    # 免得在用户临时目录里留下垃圾文件
    temp_playlist = os.environ.get(window.TEMP_PLAYLIST_ENV)
    if temp_playlist and os.path.exists(temp_playlist):
        try:
            tracks.extend(read_json_source(temp_playlist))
        except (ValueError, OSError) as exc:
            error = "无法读取临时歌单：%s" % exc
        finally:
            try:
                os.remove(temp_playlist)
            except OSError:
                pass

    try:
        for raw in args.json_file:
            try:
                tracks.extend(read_json_source(raw))
            except (ValueError, OSError) as exc:
                error = "无法解析 --file：%s" % exc
        if args.stdin_json:
            try:
                tracks.extend(read_json_source(sys.stdin.read()))
            except ValueError as exc:
                sys.stderr.write("标准输入 JSON 解析失败：%s\n" % exc)
        if not tracks:
            tracks = collect_from_arguments(args, api)
    except ApiError as exc:
        error = str(exc)
    except KeyboardInterrupt:
        return 130

    # 补全音频直链 / 封面 / 歌词
    if tracks and not args.no_resolve and not args.brief:
        need = [t for t in tracks if t.type and t.id and not t.url]
        if need:
            if args.search or args.json_out or args.playlist_out or not sys.stdout.isatty():
                sys.stderr.write("正在获取 %d 首歌曲的音频/封面/歌词…\n" % len(need))
            tracks = api.resolve(tracks)

    # 平台返回的曲目可能因版权拿不到直链；这些无法播放，直接剔除并告知
    # （--brief/--no-resolve 下本来就没取 url，不能按 url 过滤，否则会全删）
    if tracks and not args.no_resolve and not args.brief:
        playable = [t for t in tracks if t.url or not t.type]
        dropped = len(tracks) - len(playable)
        if dropped:
            sys.stderr.write(
                "跳过 %d 首没有音频直链的曲目（多为版权限制，换 wy/kg 平台命中率更高）\n" % dropped
            )
        if playable:
            tracks = playable
        elif tracks:
            sys.stderr.write("警告：这些曲目都取不到音频直链，界面会打开但没有声音\n")

    if args.playlist_out:
        with open(expand_path(args.playlist_out), "w", encoding="utf-8") as fh:
            json.dump([t.to_dict() for t in tracks], fh, ensure_ascii=False, indent=2)
        sys.stderr.write("已写入 %d 首到 %s\n" % (len(tracks), args.playlist_out))

    if args.search or args.json_out:
        dump_tracks(tracks, compact=args.compact_json)
        if error:
            sys.stderr.write("提示：%s\n" % error)
        return 0 if tracks else 1

    if args.playlist_out:
        # 写成歌单即完成请求：不再顺手打开界面，避免管道/脚本里弹出 TUI
        if error:
            sys.stderr.write("提示：%s\n" % error)
        return 0 if tracks else 1

    if error and not tracks:
        sys.stderr.write("错误：%s\n" % error)
        return 2
    if error:
        sys.stderr.write("提示：%s\n" % error)

    if not tracks:
        sys.stderr.write("没有可播放的曲目。用 --search 先看看搜索有没有结果。\n")
        return 2

    if not args.render_once:
        # 需要的情况下另开窗口：输出不是终端，或虽然连着 tty 但窗口不可见（agent 的 PTY）
        if window.should_open_window(explicit=args.window, disabled=args.no_window):
            return _launch_window(args, tracks)

    if not args.render_once and not sys.stdout.isatty():
        # 走到这里说明用户用 --no-window 强制就地运行，但输出并不是终端，
        # 界面画不出来，明确拦住而不是丢一堆转义码到管道里
        sys.stderr.write(
            "--no-window 需要真实的终端输出；当前输出是管道。\n"
            "去掉 --no-window 让它自动开窗，或加 --search --json-out 只取数据。\n"
        )
        return 2

    from .app import App
    from .ui import Renderer

    renderer = Renderer()
    if args.width:
        renderer.width_override = args.width
    if args.render_once:
        renderer.resize(*term.term_size())
        app = App(tracks, renderer=renderer, no_audio=True, offline=args.offline, volume=args.volume,
                  favorites=_read_favorites(args.favorites),
                  # 只出一帧的截图/调试场景不需要往系统通知栏发东西
                  media_controls=False)
        if tracks:
            app.load_current(notice=False)
        app.snap_theme()          # 只出一帧，没有后续帧推进渐变，直接定到目标配色
        app.scene.playing = False
        app.renderer.vinyl(app.scene.theme, app._cover_img if app._art_ready else None,
                           app.scene.theme.accent, (app._theme_key, app.renderer.cells))
        sys.stdout.write(app.renderer.render(app.scene) + "\n")
        return 0

    try:
        app = App(
            tracks,
            renderer=renderer,
            no_audio=args.no_audio,
            offline=args.offline,
            volume=args.volume,
            favorites=_read_favorites(args.favorites),
            spin=args.spin,
            theme_fade=args.theme_fade,
            audio_fade=args.audio_fade,
            media_controls=not args.no_media_controls,
        )
    except Exception as exc:
        sys.stderr.write("启动失败：%s\n" % exc)
        return 2
    return app.run()
