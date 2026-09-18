"""本地文件 / m3u / JSON 曲目来源解析。"""

from __future__ import annotations

import json
import os
from typing import List

from .api import Track, normalize_platform
from .tags import read_id3

AUDIO_EXT = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma", ".ape", ".alac", ".mka",
}


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://", "rtsp://", "rtmp://", "mms://"))


def track_from_file(path: str) -> Track:
    path = os.path.abspath(os.path.expanduser(path))
    tags = read_id3(path)
    name = tags.get("title") or os.path.splitext(os.path.basename(path))[0]
    track = Track(
        id="local:" + path,
        type="",
        name=name,
        artist=tags.get("artist") or "",
        album=tags.get("album") or "",
        url=path,
        source="本地文件",
    )
    picture = tags.get("picture")
    if picture:
        track.extra["embedded_cover"] = picture
    return track


def track_from_url(url: str, name: str = "") -> Track:
    label = name or os.path.basename(url.split("?")[0]) or url
    return Track(id="url:" + url, type="", name=label, url=url, source="网络直链")


def track_from_mapping(raw: dict) -> Track:
    track = Track.from_dict(raw)
    if track.type:
        track.type = normalize_platform(track.type) or track.type
    if not track.source:
        if track.url and not is_url(track.url):
            track.source = "本地文件"
        elif track.url:
            track.source = "网络直链"
        elif track.type:
            track.source = track.platform
    return track


def looks_like_json(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith(("[", "{"))


def load_json_tracks(raw) -> List[Track]:
    if isinstance(raw, dict):
        for key in ("tracks", "songs", "data", "items"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        else:
            raw = [raw]
    if not isinstance(raw, list):
        return []
    return [track_from_mapping(item) for item in raw if isinstance(item, dict)]


def read_json_source(source: str) -> List[Track]:
    if os.path.exists(source):
        with open(source, "r", encoding="utf-8") as fh:
            return load_json_tracks(json.load(fh))
    stripped = source.lstrip()
    if not stripped.startswith(("[", "{")):
        # 既不是 JSON 字面量，又不是已存在的文件 —— 多半是路径写错了，
        # 报「文件不存在」比让 json 抛一句看不懂的解析错误有用得多。
        raise FileNotFoundError("文件不存在：%s" % source)
    return load_json_tracks(json.loads(source))


def track_from_directive(value: str, platform: str = "wy") -> Track:
    """解析 ``type:id``（如 ``qq:001I6gzS3LufWy``）这类直接指定歌曲的写法。"""
    head, sep, tail = value.partition(":")
    code = normalize_platform(head)
    if sep and code and tail:
        return Track(id=tail.strip(), type=code)
    return Track()


def read_m3u(path: str) -> List[Track]:
    tracks: List[Track] = []
    base = os.path.dirname(os.path.abspath(path))
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            target = line if is_url(line) else os.path.join(base, line)
            if os.path.exists(target) and os.path.splitext(target)[1].lower() in AUDIO_EXT:
                tracks.append(track_from_file(target))
            elif is_url(line):
                tracks.append(track_from_url(line))
    return tracks


def scan_directory(directory: str) -> List[Track]:
    found: List[Track] = []
    for root, _dirs, files in os.walk(directory):
        for name in sorted(files):
            if os.path.splitext(name)[1].lower() in AUDIO_EXT:
                found.append(track_from_file(os.path.join(root, name)))
    return found
