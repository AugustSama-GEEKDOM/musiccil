"""本地音频的 ID3 标签读取（标题/歌手/专辑/内嵌封面）。"""

from __future__ import annotations

import os
from typing import Optional

_ENC = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}


def _synchsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _decode(data: bytes) -> str:
    if not data:
        return ""
    enc = _ENC.get(data[0], "latin-1")
    body = data[1:]
    try:
        text = body.decode(enc, errors="replace")
    except Exception:
        text = body.decode("latin-1", errors="replace")
    return text.replace("\x00", "").strip()


def _find_terminator(data: bytes, start: int, wide: bool) -> int:
    if wide:
        i = start
        while i + 1 < len(data):
            if data[i] == 0 and data[i + 1] == 0:
                return i + 2
            i += 2
        return len(data)
    idx = data.find(b"\x00", start)
    return len(data) if idx < 0 else idx + 1


def read_id3(path: str) -> dict:
    info = {"title": "", "artist": "", "album": "", "picture": None}
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(10)
            if head[:3] != b"ID3":
                return _read_id3v1(fh, size, info)
            major = head[3]
            tag_size = _synchsafe(head[6:10])
            body = fh.read(tag_size)
    except OSError:
        return info

    pos = 0
    id_len = 3 if major == 2 else 4
    while pos + id_len + (3 if major == 2 else 6) <= len(body):
        frame_id = body[pos : pos + id_len]
        if not frame_id.strip(b"\x00"):
            break
        pos += id_len
        if major == 2:
            fsize = int.from_bytes(body[pos : pos + 3], "big")
            pos += 3
        elif major == 4:
            fsize = _synchsafe(body[pos : pos + 4])
            pos += 6  # size + 2 字节 flags
        else:
            fsize = int.from_bytes(body[pos : pos + 4], "big")
            pos += 6
        if fsize <= 0 or pos + fsize > len(body):
            break
        data = body[pos : pos + fsize]
        pos += fsize
        try:
            fid = frame_id.decode("latin-1")
        except Exception:
            continue
        if fid in ("TIT2", "TT2"):
            info["title"] = _decode(data)
        elif fid in ("TPE1", "TP1"):
            info["artist"] = _decode(data)
        elif fid in ("TALB", "TAL"):
            info["album"] = _decode(data)
        elif fid in ("APIC", "PIC") and info["picture"] is None:
            info["picture"] = _extract_apic(data, major)
    return info


def _extract_apic(data: bytes, major: int) -> Optional[bytes]:
    if not data:
        return None
    enc = _ENC.get(data[0], "latin-1")
    wide = enc.startswith("utf-16")
    pos = 1
    if major == 2:  # PIC: 3 字节图片格式
        pos += 3
    else:           # APIC: MIME 以 \x00 结尾
        pos = _find_terminator(data, pos, False)
    if pos >= len(data):
        return None
    pos += 1  # 图片类型
    pos = _find_terminator(data, pos, wide)
    return data[pos:] or None


def _read_id3v1(fh, size: int, info: dict) -> dict:
    if size < 128:
        return info
    fh.seek(-128, os.SEEK_END)
    tail = fh.read(128)
    if tail[:3] != b"TAG":
        return info

    def grab(a: int, b: int) -> str:
        return tail[a:b].split(b"\x00")[0].decode("latin-1", errors="replace").strip()

    info["title"] = grab(3, 33)
    info["artist"] = grab(33, 63)
    info["album"] = grab(63, 93)
    return info
