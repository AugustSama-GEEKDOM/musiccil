"""ID3 解析测试：构造真实的 ID3v2.3 / ID3v2.4 帧，验证标题、歌手与内嵌封面。"""

import os
import tempfile
import unittest

from musiccil.tags import read_id3

JPEG = b"\xff\xd8\xff\xe0" + b"JFIF-fake-image-data" + b"\xff\xd9"


def synchsafe(n: int) -> bytes:
    return bytes(((n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F))


def frame_v23(fid: str, payload: bytes) -> bytes:
    return fid.encode("latin-1") + len(payload).to_bytes(4, "big") + b"\x00\x00" + payload


def frame_v24(fid: str, payload: bytes) -> bytes:
    return fid.encode("latin-1") + synchsafe(len(payload)) + b"\x00\x00" + payload


def text_frame(text: str, encoding: int = 3) -> bytes:
    body = text.encode("utf-8" if encoding == 3 else "latin-1")
    return bytes([encoding]) + body


def apic_frame(mime: bytes = b"image/jpeg", desc: bytes = b"cover", image: bytes = JPEG) -> bytes:
    return b"\x00" + mime + b"\x00" + b"\x03" + desc + b"\x00" + image


def write_tag(path: str, frames: bytes, major: int = 3) -> None:
    header = b"ID3" + bytes([major, 0, 0]) + synchsafe(len(frames))
    padding = b"\x00" * 64
    with open(path, "wb") as fh:
        fh.write(header + frames + padding + b"\xff\xfb\x90\x00" + b"\x00" * 128)


class Id3Test(unittest.TestCase):
    def test_reads_v23_text_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.mp3")
            frames = (
                frame_v23("TIT2", text_frame("加州梦游"))
                + frame_v23("TPE1", text_frame("闫泽欢"))
                + frame_v23("TALB", text_frame("专辑名"))
            )
            write_tag(path, frames)
            info = read_id3(path)
            self.assertEqual(info["title"], "加州梦游")
            self.assertEqual(info["artist"], "闫泽欢")
            self.assertEqual(info["album"], "专辑名")

    def test_reads_v24_text_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "b.mp3")
            frames = frame_v24("TIT2", text_frame("Title")) + frame_v24("TPE1", text_frame("Artist"))
            write_tag(path, frames, major=4)
            info = read_id3(path)
            self.assertEqual(info["title"], "Title")
            self.assertEqual(info["artist"], "Artist")

    def test_extracts_embedded_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.mp3")
            write_tag(path, frame_v23("APIC", apic_frame()))
            self.assertEqual(read_id3(path)["picture"], JPEG)

    def test_handles_utf16_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.mp3")
            payload = b"\x01" + "歌名".encode("utf-16")
            write_tag(path, frame_v23("TIT2", payload))
            self.assertEqual(read_id3(path)["title"], "歌名")

    def test_parses_frame_before_write_tag_padding(self):
        """ID3 的 padding 在末尾；遇到全零帧头应当停下而不是报错。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "e.mp3")
            write_tag(path, frame_v23("TIT2", text_frame("标题")))
            self.assertEqual(read_id3(path)["title"], "标题")

    def test_padding_only_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "e2.mp3")
            write_tag(path, b"")
            info = read_id3(path)
            self.assertEqual(info["title"], "")
            self.assertIsNone(info["picture"])

    def test_plain_file_without_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.mp3")
            with open(path, "wb") as fh:
                fh.write(b"\x00" * 256)
            info = read_id3(path)
            self.assertEqual(info["title"], "")
            self.assertIsNone(info["picture"])

    def test_truncated_frame_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "g.mp3")
            bogus = b"TIT2" + (9999).to_bytes(4, "big") + b"\x00\x00" + b"short"
            write_tag(path, bogus)
            self.assertEqual(read_id3(path)["title"], "")

    def test_missing_file_is_safe(self):
        info = read_id3(os.path.join(tempfile.gettempdir(), "definitely-not-here-42.mp3"))
        self.assertEqual(info["title"], "")


if __name__ == "__main__":
    unittest.main()
