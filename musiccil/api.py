"""聚合音乐 API 客户端。

文档见 references/api.md。这个接口有几个必须容忍的不一致：

请求统一发到 musiccil API 调用站（<https://muscil.geekdom.top/open/music/>），
由服务端用站点自己的密钥去取数。因此这里只关心"路径 + 参数 + 响应形态"。

* 失败时 HTTP 依然是 200，只能看 body 里的 ``code``；
* ``code`` 成功值不统一：多数接口是 ``1``，``userlist`` 是 ``200``；
* 失败时 ``data`` 可能是 ``""``、``"[]"`` 或对象/数组；
* 搜索接口带 ``format=1`` 才会返回精简结构。
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, NamedTuple, Optional

#: 接口基址。客户端不内置任何密钥，密钥由用户在本地配置。
#: 需要指向别的部署时用 MUSICCIL_API_BASE 覆盖。
DEFAULT_BASE = "https://muscil.geekdom.top/open/music/"

ENV_BASE = "MUSICCIL_API_BASE"

#: 密钥申请页。提示文案与文档里都从这里取，避免网址散落在好几处对不上。
API_SITE = "https://muscil.geekdom.top"


def api_base() -> str:
    """当前使用的接口基址（末尾保证带斜杠）。

    每次请求时现取，而不是在 import 时定死：否则 import 顺序会决定用哪个地址，
    测试和"先设环境变量再调用"的脚本都会莫名其妙地不生效。
    """
    base = (os.environ.get(ENV_BASE) or "").strip() or DEFAULT_BASE
    return base if base.endswith("/") else base + "/"


#: 兼容旧代码里 api.BASE 的读法。
BASE = DEFAULT_BASE

PLATFORMS = {
    "wy": "网易云",
    "qq": "QQ音乐",
    "kw": "酷我",
    "kg": "酷狗",
    "mg": "咪咕",
    "qi": "千千音乐",
    "my": "明月浩空",
}

#: 按"原唱命中率"排序的平台顺序：网易云曲库最大，酷狗/酷我常有网易云缺失的版权曲。
#: 接口文档也提示过：找不到音频直链时优先换这几个平台。
PLATFORM_FALLBACK = ("wy", "kg", "kw", "qq")

SUCCESS_CODES = frozenset((1, "1", 200, "200"))

ENV_KEY = "MUSICCIL_API_KEY"

#: 密钥不写进代码：仓库是公开的，硬编码等于把接口配额送人。
#: 查找顺序是 显式参数 → 环境变量 → 本地密钥文件。密钥文件放在用户目录下，
#: 不进仓库（见 .gitignore）。
KEY_DIR = os.path.join(os.path.expanduser("~"), ".musiccil")
KEY_FILE = os.path.join(KEY_DIR, "key")

KEY_HELP = (
    "没有找到接口密钥。\n"
    "  免费领取：%s（注册后自动生成，页面里可复制）\n"
    "  领到后运行一次即可，播放器会引导你粘贴并保存：musiccil --setup\n"
    "  也可以手工指定：环境变量 MUSICCIL_API_KEY、命令行 --key <密钥>、\n"
    "  或把密钥写进 %s（一行，只有密钥本身）。\n"
    "  让 Agent 代你保存这把密钥：musiccil --setup --key <密钥>\n"
    "接口按次计配额，密钥请勿公开或提交到仓库。"
) % (API_SITE, KEY_FILE)


class ApiError(RuntimeError):
    """接口返回 code 非成功。"""

    def __init__(self, msg: str, code: Any = None, endpoint: str = ""):
        super().__init__(msg)
        self.code = code
        self.endpoint = endpoint

    @property
    def is_quota(self) -> bool:
        return "次数" in str(self) or "密钥" in str(self)


def resolve_key(explicit: Optional[str] = None) -> str:
    """按 显式参数 → 环境变量 → 本地密钥文件 的顺序取密钥。

    仓库里**不放默认密钥**：那样任何克隆下来的人都能消耗你的配额。
    """
    for candidate in (explicit, os.environ.get(ENV_KEY)):
        if candidate and candidate.strip():
            return candidate.strip()
    try:
        with open(KEY_FILE, "r", encoding="utf-8") as fh:
            text = fh.read().strip()
        if text:
            return text
    except OSError:
        pass
    return ""


def api_site_url() -> str:
    """从接口基址推出站点地址（密钥就在那里领）。"""
    base = api_base()
    marker = "/open/music/"
    if marker in base:
        return base.split(marker)[0]
    return API_SITE


def save_key(value: str) -> str:
    """把密钥写进本地密钥文件（仅本人可读），返回文件路径。

    权限设成 0600：同机器上的其他账号不该顺手读到你的配额。
    Windows 上 chmod 基本是空操作，但文件本来就在用户目录下，够用。
    """
    value = (value or "").strip()
    if not value:
        raise ValueError("密钥不能为空")
    os.makedirs(KEY_DIR, exist_ok=True)
    with open(KEY_FILE, "w", encoding="utf-8") as fh:
        fh.write(value + "\n")
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass
    return KEY_FILE


class KeyInfo(NamedTuple):
    ok: bool
    message: str
    quota: Optional[int] = None
    email: str = ""
    known: bool = True


def check_key(value: str, timeout: float = 8.0) -> KeyInfo:
    """拿密钥去站点问一句"这钥匙还有多少配额"。

    走的是站点自己的 /api/key-info，校验密钥不该扣配额，
    否则用户第一次配置就要白白花掉一次。
    基址指向别处、或探测失败时(404/解析失败)返回 known=False，
    调用方按"未知"处理：照样保存，别因为探测不了就把用户拦在门外。
    """
    value = (value or "").strip()
    if not value:
        return KeyInfo(False, "密钥不能为空")
    url = "%s/api/key-info?key=%s" % (api_site_url(), urllib.parse.quote(value))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "musiccil/2.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return KeyInfo(True, "无法连接站点校验（已跳过校验）", known=False)
    if not isinstance(payload, dict):
        return KeyInfo(True, "站点返回结构异常（已跳过校验）", known=False)
    if payload.get("ok"):
        return KeyInfo(
            True,
            "密钥有效",
            quota=int(payload.get("quota", 0)),
            email=str(payload.get("email") or ""),
        )
    return KeyInfo(False, str(payload.get("msg") or "密钥无效"))


def normalize_platform(name: str) -> Optional[str]:
    if not name:
        return None
    n = name.strip().lower()
    if n in PLATFORMS:
        return n
    for code, label in PLATFORMS.items():
        if n == label.lower() or n in label.lower() or label.lower() in n:
            return code
    aliases = {
        "netease": "wy", "网易": "wy", "网易云音乐": "wy",
        "qq音乐": "qq", "tencent": "qq",
        "酷我音乐": "kw", "kuwo": "kw",
        "酷狗音乐": "kg", "kugou": "kg",
        "咪咕音乐": "mg", "migu": "mg",
        "千千": "qi", "qiqian": "qi",
    }
    return aliases.get(n)


def _norm(text: str) -> str:
    """归一化用于匹配：小写、去掉括号补充说明与空白。"""
    s = (text or "").lower().strip()
    s = re.sub(r"[（(][^）)]*[）)]", " ", s)
    s = re.sub(r"[\s\-_·、,，/&]+", "", s)
    return s


def safe_search(api: "MusicApi", name: str, platform: str, limit: int) -> List["Track"]:
    """搜索失败就返回空，不让一次网络抖动打断整条挑选流程。"""
    try:
        return api.search(name, platform, limit=limit)
    except ApiError:
        return []


def _artist_set(artist: str) -> set:
    """把 "A/B、C & D" 这类合作歌手拆成归一化后的集合。

    只做整名比对，不做子串匹配——否则 "周杰伦./街道办GDC" 会把 "周杰伦"
    这种联名翻唱算成原唱。
    """
    parts = re.split(r"[/、,，&;；]+|\s+feat\.?\s+|\s+ft\.?\s+", artist or "", flags=re.I)
    return {_norm(p) for p in parts if _norm(p)}


#: 非原唱的常见后缀。用户没点名要 live/remix 时，这些版本不该占推荐位。
_VARIANT = re.compile(
    r"(live|演唱会|现场|remix|混音|dj|伴奏|instrumental|纯音乐|cover|翻唱|"
    r"\(吉他版\)|钢琴版|片段|demo|清唱|抖音版|女生版|男声版)",
    re.I,
)


def looks_like_variant(name: str) -> bool:
    return bool(_VARIANT.search(name or ""))


def song_key(track: "Track"):
    """去重用的曲目标识：歌名 + **歌手集合**。

    不能拼歌手字符串：同一首歌的歌手顺序在不同条目里可能是反的
    （"李玟/周杰伦" 与 "周杰伦/李玟"），按字符串比会被当成两首。
    """
    return (_norm(track.name), frozenset(_artist_set(track.artist)) or {_norm(track.artist)})


def pick_mix(
    hits: List["Track"],
    count: int = 8,
    per_artist: int = 2,
    want_variants: bool = False,
    exclude: Optional[set] = None,
) -> List["Track"]:
    """从一次搜索结果里挑出一份"能直接听"的歌单。

    这一步是做给 agent 减负的：过去要让模型读完候选再自己排重、去 live/remix 版本，
    每多一轮工具调用就多几十秒。规则很朴素——同名同歌手只留一首、同一歌手最多
    ``per_artist`` 首、没有明确要求时跳过 live/remix/伴奏版，其余按平台给的排序保留。
    凑不满时放宽同歌手限制；只在原版一首都挑不到时才回退使用变体版本，
    候选仍然不够由调用方（``--mix``）翻页补足。
    """
    out: List["Track"] = []
    seen_songs = set()
    artist_count: Dict[str, int] = {}
    if exclude:
        seen_songs.update(exclude)

    def take(cap: int, allow_variants: bool) -> None:
        for track in hits:
            if len(out) >= count:
                return
            if not track.name:
                continue
            # 平台会把同一首歌重复列出多条（只有 ID 不同），这些在用户眼里就是同一行，
            # 必须合并；不同专辑里的同名曲目同样会产生一模一样的列表项，所以按
            # 歌名+歌手去重。候选因此变少时，靠自动翻页补足，而不是塞重复项。
            # 去重要看"歌名 + 歌手集合"，不能拼歌手字符串：
            # 同一首歌的歌手顺序在不同条目里可能是反的（"李玟/周杰伦" 与
            # "周杰伦/李玟"），按字符串比会被当成两首，歌单里就出现重复。
            song = song_key(track)
            if song in seen_songs:
                continue
            if not allow_variants and looks_like_variant(track.name):
                continue
            main_artist = _norm(track.artist)
            if cap and main_artist and artist_count.get(main_artist, 0) >= cap:
                continue
            seen_songs.add(song)
            if main_artist:
                artist_count[main_artist] = artist_count.get(main_artist, 0) + 1
            out.append(track)

    take(per_artist, want_variants)
    # 搜索结果常集中在同一位歌手（按风格搜索时整页可能都是同一个人的作品）。
    # 这时宁可多给几首同歌手的歌，也不要只给用户两三首，所以放开同歌手限制。
    if per_artist and len(out) < count:
        take(0, want_variants)
    # 变体版本只在"原版一首都没挑到"时才回退使用，避免歌单里混进 live/remix
    if not out and not want_variants:
        take(0, True)
    return out


@dataclass
class Track:
    """一首歌。id/type 用于回查详情，url/pic/lrc 是补齐后的资源。"""

    id: str = ""
    type: str = "wy"
    name: str = ""
    artist: str = ""
    album: str = ""
    duration: float = 0.0
    url: str = ""
    pic: str = ""
    lrc: str = ""
    source: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def platform(self) -> str:
        return PLATFORMS.get(self.type, self.type or "本地")

    @property
    def display_artist(self) -> str:
        return self.artist.replace("/", " / ")

    def to_dict(self) -> Dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "extra"}
        if self.extra:
            d["extra"] = self.extra
        return d

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Track":
        if not isinstance(raw, dict):
            return cls()
        known = set(cls.__dataclass_fields__)
        kwargs = {k: v for k, v in raw.items() if k in known and k != "extra"}
        track = cls(**kwargs)
        extra = raw.get("extra")
        track.extra = dict(extra) if isinstance(extra, dict) else {}
        track.extra.update({k: v for k, v in raw.items() if k not in known})
        return track


class MusicApi:
    def __init__(self, key: Optional[str] = None, timeout: float = 10.0, retries: int = 1):
        """默认超时/重试都调小：挑选流程里会有多个平台试探，
        单个平台卡住（如 QQ 常返回 500）不该把整条流程拖到几十秒。"""
        self.key = resolve_key(key)
        self.timeout = timeout
        self.retries = retries

    def _request(self, endpoint: str, **params) -> Any:
        if not self.key:
            # 早失败：没密钥时发出去只会拿回一个看不懂的接口错误
            raise ApiError(KEY_HELP, endpoint=endpoint)
        query = {k: v for k, v in params.items() if v is not None}
        query["key"] = self.key
        url = api_base() + endpoint + "?" + urllib.parse.urlencode(query, doseq=True)
        last = None
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "musiccil/2.0", "Accept": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw)
            except urllib.error.HTTPError as exc:
                last = ApiError(f"HTTP {exc.code}", code=exc.code, endpoint=endpoint)
                if exc.code < 500:
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = ApiError(f"网络错误：{exc}", endpoint=endpoint)
            except json.JSONDecodeError as exc:
                last = ApiError(f"返回不是合法 JSON：{exc}", endpoint=endpoint)
            if attempt < self.retries:
                time.sleep(0.3 * (attempt + 1))
        raise last or ApiError("请求失败", endpoint=endpoint)

    @staticmethod
    def _unwrap(payload: Any, endpoint: str, root: str = "data") -> Any:
        """拆包并归一出错；返回成功时的数据部分。"""
        if not isinstance(payload, dict):
            raise ApiError("返回结构异常", endpoint=endpoint)
        code = payload.get("code")
        msg = str(payload.get("msg") or "")
        if code not in SUCCESS_CODES:
            raise ApiError(msg or "接口返回失败", code=code, endpoint=endpoint)
        body = payload.get(root, payload.get("playlist"))
        if isinstance(body, str):
            body = body.strip()
            if body in ("", "[]", "{}", "null"):
                raise ApiError(msg or "没有结果", code=code, endpoint=endpoint)
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                return body
        if body is None:
            raise ApiError(msg or "没有结果", code=code, endpoint=endpoint)
        return body

    def best(self, name: str, artist: str = "", platform: str = "wy", candidates: int = 8) -> Optional[Track]:
        """一次搜索就挑出"最像"的那首，省掉让模型逐条比对的一轮往返。

        匹配优先级：歌手完全命中 > 歌手包含 > 歌名完全相等 > 歌名包含。
        官方原曲通常排在前面，所以同分时保留靠前的候选。

        """
        seen: Dict[str, Track] = {}
        queries = [name]
        if artist and artist.lower() not in name.lower():
            # 带上歌手再搜一次：很多平台对"歌名+歌手"的召回更准，
            # 只搜歌名时原唱可能根本不在前几条里
            queries.append("%s %s" % (name, artist))
        for query in queries:
            try:
                hits = self.search(query, platform, limit=max(1, candidates))
            except ApiError:
                continue
            for track in hits:
                seen.setdefault("%s:%s" % (track.type, track.id), track)
        hits = list(seen.values())
        if not hits:
            return None
        want_name = _norm(name)
        want_artist = _norm(artist)
        best, best_score = hits[0], -1.0
        for rank, track in enumerate(hits):
            score, _ = self._score(track, want_name, want_artist, rank)
            if score > best_score:
                best_score, best = score, track
        return best

    @staticmethod
    def _score(track: Track, want_name: str, want_artist: str, rank: int = 99):
        """给候选打分；返回 (总分, 歌手是否命中, 歌名是否也算命中)。"""
        got_name = _norm(track.name)
        got_artist = _norm(track.artist)
        artist_hit = False
        score = 0.0
        if want_artist:
            # 必须精确相等，或作为独立的歌手名出现在合作列表里。
            # 不能用简单的子串包含："周杰伦./街道办GDC" 这种翻唱联名也会命中，
            # 结果把翻唱当成原唱。
            if got_artist == want_artist:
                score += 8
                artist_hit = True
            elif want_artist and want_artist in _artist_set(track.artist):
                score += 7
                artist_hit = True
        if got_name == want_name:
            score += 6
            name_hit = True
        elif want_name and want_name in got_name:
            score += 4
            name_hit = True
        elif got_name and got_name in want_name:
            score += 2
            name_hit = True
        else:
            name_hit = False
        score += max(0, 3 - rank) * 0.1          # 轻微偏好靠前的官方结果
        return score, artist_hit, name_hit

    def best_across_platforms(
        self,
        name: str,
        artist: str = "",
        platform: str = "wy",
        candidates: int = 12,
        allow_fallback: bool = True,
    ) -> Optional[Track]:
        """在所有候选平台上并发搜索，再挑出最像原唱的那首。

        这一步替掉了过去"模型自己发现某平台没有原版、再手动换平台重搜"的往返：
        实测周杰伦等歌手的原版在网易云缺席，而酷狗/酷我有收录。

        平台搜索是**并发**发出的：串行按 wy→kg→kw→qq 试探最坏要 4 秒，
        而每条请求本身只要 0.2–0.5 秒，并发后最坏耗时约等于最慢的那一个平台。
        指定的平台在分数相同时优先（见 ``prefer`` 加分），保证不会因为并发而
        反而冷落了用户点名的平台。
        """
        order = [platform]
        if allow_fallback and artist:
            order += [p for p in PLATFORM_FALLBACK if p != platform]

        want_name, want_artist = _norm(name), _norm(artist)
        queries = [name]
        if artist and artist.lower() not in name.lower():
            # 带上歌手再搜一次：很多平台对"歌名+歌手"的召回更准，
            # 只搜歌名时原唱可能根本不在前几条里
            queries.append("%s %s" % (name, artist))

        jobs = [(code, query) for code in order for query in queries]
        results: Dict[tuple, List[Track]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(jobs) or 1)) as pool:
            futures = {
                pool.submit(self.search, query, code, 1, max(1, candidates)): (code, query)
                for code, query in jobs
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    results[futures[future]] = list(future.result())
                except ApiError:
                    continue

        # 按平台顺序、查询顺序合并去重，让靠前的平台在分数相同时胜出
        seen: Dict[str, Track] = {}
        for code, query in jobs:
            for track in results.get((code, query), []):
                seen.setdefault("%s:%s" % (track.type, track.id), track)
        if not seen:
            return None

        prefer = {code: i for i, code in enumerate(order)}
        scored = []
        for i, track in enumerate(seen.values()):
            score, artist_hit, name_hit = self._score(track, want_name, want_artist, i)
            score += (len(order) - prefer.get(track.type, len(order))) * 0.05
            scored.append(((score, artist_hit, name_hit), track))
        if not artist:
            return max(scored, key=lambda p: p[0][0])[1]
        # 歌手对了还不算原唱：必须是同一首歌（歌名得对得上），
        # 否则会出现"歌手命中但拿错歌"的情况（例如查《夜曲》返回《布拉格广场》）
        hit = [p for p in scored if p[0][1] and (p[0][2] or want_name in _norm(p[1].name))]
        if hit:
            return max(hit, key=lambda p: p[0][0])[1]
        return max(scored, key=lambda p: p[0][0])[1]

    def _search_body(self, name: str, ptype: str, page: int, limit: int) -> Any:
        """搜索并拆包，多词关键词失败时逐步丢词重试。

        接口对多个词的关键词（如 "DOUDOU 独立民谣"）常直接回 ``获取失败``，
        而单关键词是好的。这不是网络问题、重试也没用，但用户的描述天然是
        "歌手 + 风格"这种多词形式，所以这里逐级丢掉修饰词退化成能查的关键词，
        总比直接报错、让用户自己改词强。
        """
        words = (name or "").split()
        attempts = [" ".join(words)]
        if len(words) > 1:
            # 先只留第一个词（通常是歌手/歌名），再退到单个关键词
            attempts.append(words[0])
            attempts.extend(words)
        seen = []
        last: Optional[ApiError] = None
        for candidate in attempts:
            if not candidate or candidate in seen:
                continue
            seen.append(candidate)
            try:
                return self._unwrap(
                    self._request(
                        "search", name=candidate, type=ptype, page=page, limit=limit, format=1
                    ),
                    "search",
                )
            except ApiError as exc:
                last = exc
        raise last or ApiError("搜索失败", endpoint="search")

    def search(self, name: str, platform: str = "wy", page: int = 1, limit: int = 10) -> List[Track]:
        """按歌名搜索，返回带 type/id 的 Track 列表，可直接查详情。"""
        ptype = normalize_platform(platform) or platform
        body = self._search_body(name, ptype, page, limit)
        if isinstance(body, dict):
            body = body.get("songs") or body.get("list") or []
        if not isinstance(body, list):
            return []
        out: List[Track] = []
        for item in body:
            if not isinstance(item, dict):
                continue
            artist = item.get("artist") or item.get("artists") or ""
            if isinstance(artist, list):
                artist = "/".join(str(a) for a in artist)
            out.append(
                Track(
                    id=str(item.get("id", "")),
                    type=str(item.get("type") or ptype),
                    name=str(item.get("name") or ""),
                    artist=str(artist),
                    album=str(item.get("album") or ""),
                    extra={"pic_id": item.get("pic_id"), "size": item.get("size")},
                )
            )
        return out

    def info(
        self,
        song_id: str,
        platform: str = "wy",
        with_url: bool = True,
        with_pic: bool = True,
        with_lrc: bool = True,
        base: Optional[Track] = None,
    ) -> Track:
        """取歌曲详情，按需带上 MP3 / 封面 / 歌词。"""
        track = Track(
            id=str(song_id),
            type=normalize_platform(platform) or platform,
            name=base.name if base else "",
            artist=base.artist if base else "",
            album=base.album if base else "",
        )
        if base is not None:
            track.extra = dict(base.extra)
        body = self._unwrap(
            self._request(
                "info",
                id=song_id,
                type=track.type,
                url=1 if with_url else None,
                pic=1 if with_pic else None,
                lrc=1 if with_lrc else None,
            ),
            "info",
        )
        if not isinstance(body, dict):
            raise ApiError("详情结构异常", endpoint="info")
        track.name = str(body.get("name") or track.name)
        track.album = str(body.get("album") or track.album)
        artist = body.get("artist")
        if isinstance(artist, list):
            artist = "/".join(str(a) for a in artist)
        track.artist = str(artist or track.artist)
        track.url = str(body.get("url") or "")
        track.pic = str(body.get("pic") or "")
        track.lrc = str(body.get("lrc") or "")
        track.extra["picid"] = body.get("picid")
        return track

    def pic(self, song_id: str, platform: str = "wy") -> str:
        return str(self._unwrap(self._request("pic", id=song_id, type=platform), "pic") or "")

    def lrc(self, song_id: str, platform: str = "wy", translate: bool = False, ksc: bool = False) -> str:
        body = self._unwrap(
            self._request(
                "lrc", id=song_id, type=platform, t=1 if translate else None, ksc=1 if ksc else None
            ),
            "lrc",
        )
        return body if isinstance(body, str) else ""

    def playlist(self, list_id: str, platform: str = "wy") -> Dict[str, Any]:
        body = self._unwrap(self._request("list", id=list_id, type=platform, format=1), "list")
        return body if isinstance(body, dict) else {}

    def toplist(self, list_id: str, platform: str = "wy") -> Dict[str, Any]:
        body = self._unwrap(self._request("toplist", id=list_id, type=platform, format=1), "toplist")
        return body if isinstance(body, dict) else {}

    def user_playlists(self, uid: str, platform: str = "wy") -> List[Dict[str, Any]]:
        """注意：该接口成功码是 200，且数据在 playlist 字段而非 data。"""
        body = self._unwrap(
            self._request("userlist", uid=uid, type=platform, format=1), "userlist", root="playlist"
        )
        if isinstance(body, dict):
            body = body.get("playlist", body)
        if isinstance(body, dict):
            return [body]
        return [b for b in body if isinstance(b, dict)] if isinstance(body, list) else []

    @staticmethod
    def tracks_from_list(payload: Dict[str, Any], platform: str = "wy") -> List[Track]:
        """把 playlist / toplist 的并行数组转成 Track 列表。"""
        if not isinstance(payload, dict):
            return []
        ids = payload.get("songId") or []
        names = payload.get("songName") or []
        albums = payload.get("albumName") or []
        artists = payload.get("artistName") or []
        types = payload.get("type") or []
        out: List[Track] = []
        for i, sid in enumerate(ids):
            ptype = str(types[i]) if i < len(types) and types[i] else platform
            out.append(
                Track(
                    id=str(sid),
                    type=normalize_platform(ptype) or ptype,
                    name=str(names[i]) if i < len(names) else "",
                    album=str(albums[i]) if i < len(albums) else "",
                    artist=str(artists[i]) if i < len(artists) else "",
                )
            )
        return out

    def resolve(
        self,
        tracks: List[Track],
        with_pic: bool = True,
        with_lrc: bool = True,
        with_url: bool = True,
        progress=None,
    ) -> List[Track]:
        """把一批 Track 补全为可直接播放的 Track（已补全的会跳过）。

        详情请求**并发**发出：串行解析 10 首要 4 秒以上，并发后接近单首耗时。
        返回顺序与入参一致，调用方按下标取歌不受影响。
        """
        done: List[Optional[Track]] = [None] * len(tracks)
        pending = []
        for i, track in enumerate(tracks):
            if track.url and (track.pic or not with_pic) and (track.lrc or not with_lrc):
                done[i] = track
            else:
                pending.append(i)
        if pending:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(pending))) as pool:
                futures = {
                    pool.submit(
                        self.info, tracks[i].id, tracks[i].type, with_url, with_pic, with_lrc, tracks[i]
                    ): i
                    for i in pending
                }
                finished = 0
                for future in concurrent.futures.as_completed(futures):
                    i = futures[future]
                    finished += 1
                    if progress:
                        progress(finished, len(pending), tracks[i])
                    try:
                        done[i] = future.result()
                    except ApiError as exc:
                        tracks[i].extra["error"] = str(exc)
                        done[i] = tracks[i]
        return [tracks[i] if t is None else t for i, t in enumerate(done)]
