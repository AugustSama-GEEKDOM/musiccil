"""LRC 歌词解析与定位。"""

from __future__ import annotations

import re
from typing import List, Tuple

_TS = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_TAG = re.compile(r"^\[(ti|ar|al|by|re|ve|length|offset):(.*)\]$", re.I)

#: 一行歌词：(时间秒, 该时间点的若干行文本)
LyricLine = Tuple[float, List[str]]


def parse(text: str) -> List[LyricLine]:
    if not text:
        return []
    offset = 0.0
    raw: List[Tuple[float, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        tag = _TAG.match(line)
        if tag:
            if tag.group(1).lower() == "offset":
                try:
                    offset = float(tag.group(2).strip()) / 1000.0
                except ValueError:
                    pass
            continue
        stamps = list(_TS.finditer(line))
        if not stamps:
            continue
        content = _TS.sub("", line).strip()
        if not content:
            continue
        for m in stamps:
            mm, ss, frac = m.group(1), m.group(2), m.group(3) or "0"
            frac = (frac + "00")[:3] if len(frac) <= 3 else frac[:3]
            t = int(mm) * 60 + int(ss) + int(frac) / 1000.0
            raw.append((t, content))
    raw.sort(key=lambda x: x[0])
    merged: List[LyricLine] = []
    for t, content in raw:
        t = max(0.0, t - offset)
        if merged and abs(merged[-1][0] - t) < 0.02:
            # 同一时间点的第二行一般是对照/翻译歌词
            if content not in merged[-1][1]:
                merged[-1][1].append(content)
        else:
            merged.append((t, [content]))
    return merged


def index_at(lines: List[LyricLine], t: float) -> int:
    """返回当前应高亮的行号；t 早于第一句时返回 -1。"""
    lo, hi, found = 0, len(lines) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if lines[mid][0] <= t:
            found = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return found
