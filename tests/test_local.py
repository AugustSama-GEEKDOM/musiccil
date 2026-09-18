import json
import os
import tempfile
import unittest

from musiccil.local import (
    is_url,
    load_json_tracks,
    looks_like_json,
    read_json_source,
    read_m3u,
    scan_directory,
    track_from_directive,
    track_from_file,
)


class DirectiveTest(unittest.TestCase):
    def test_platform_id(self):
        track = track_from_directive("qq:001I6gzS3LufWy")
        self.assertEqual(track.type, "qq")
        self.assertEqual(track.id, "001I6gzS3LufWy")

    def test_plain_text_is_not_a_directive(self):
        self.assertEqual(track_from_directive("加州梦游").id, "")
        self.assertEqual(track_from_directive("unknown:123").id, "")


class JsonTest(unittest.TestCase):
    def test_detects_json(self):
        self.assertTrue(looks_like_json('  [{"a":1}]'))
        self.assertTrue(looks_like_json('{"tracks":[]}'))
        self.assertFalse(looks_like_json("加州梦游"))

    def test_loads_various_shapes(self):
        self.assertEqual(len(load_json_tracks([{"name": "a"}])), 1)
        self.assertEqual(len(load_json_tracks({"tracks": [{"name": "a"}]})), 1)
        self.assertEqual(len(load_json_tracks({"data": [{"name": "a"}, {"name": "b"}]})), 2)
        self.assertEqual(len(load_json_tracks({"name": "solo"})), 1)
        self.assertEqual(load_json_tracks("nope"), [])

    def test_read_from_file_and_string(self):
        payload = [{"id": "1", "type": "wy", "name": "歌"}]
        self.assertEqual(read_json_source(json.dumps(payload))[0].name, "歌")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            self.assertEqual(read_json_source(path)[0].type, "wy")

    def test_missing_path_reports_missing_file(self):
        # 路径写错时应该报「文件不存在」，而不是 json 那句看不懂的解析错误
        with self.assertRaises(FileNotFoundError):
            read_json_source(os.path.join(tempfile.gettempdir(), "musiccil_no_such.json"))
        # 但 JSON 字面量本身要照常解析
        self.assertEqual(read_json_source('{"id": "1", "type": "wy"}')[0].type, "wy")


class FileTest(unittest.TestCase):
    def test_id3v1_is_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "song.mp3")
            def field(text: str) -> bytes:
                return text.encode("latin-1").ljust(30, b"\x00")

            # ID3v1：3 + 30*3 + 4(年) + 30(备注) + 1(流派) = 128 字节
            tag = (
                b"TAG" + field("Title") + field("Artist") + field("Album")
                + b"2024" + b"\x00" * 30 + b"\x0c"
            )
            self.assertEqual(len(tag), 128)
            with open(path, "wb") as fh:
                fh.write(b"\xff\xfb\x90\x00" + b"\x00" * 400 + tag)
            track = track_from_file(path)
            self.assertEqual(track.name, "Title")
            self.assertEqual(track.artist, "Artist")
            self.assertEqual(track.album, "Album")
            self.assertEqual(track.source, "本地文件")
            self.assertTrue(track.url.endswith("song.mp3"))

    def test_filename_fallback_without_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "我的歌.mp3")
            with open(path, "wb") as fh:
                fh.write(b"\x00" * 64)
            self.assertEqual(track_from_file(path).name, "我的歌")

    def test_scan_directory_filters_by_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("a.mp3", "b.flac", "c.txt", "d.ogg"):
                with open(os.path.join(tmp, name), "wb") as fh:
                    fh.write(b"\x00" * 8)
            found = {os.path.basename(t.url) for t in scan_directory(tmp)}
            self.assertEqual(found, {"a.mp3", "b.flac", "d.ogg"})

    def test_m3u_skips_comments_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.mp3"), "wb") as fh:
                fh.write(b"\x00" * 8)
            m3u = os.path.join(tmp, "list.m3u")
            with open(m3u, "w", encoding="utf-8") as fh:
                fh.write("#EXTM3U\na.mp3\nmissing.mp3\nhttps://example.com/x.mp3\n")
            tracks = read_m3u(m3u)
            self.assertEqual(len(tracks), 2)
            self.assertTrue(tracks[0].url.endswith("a.mp3"))
            self.assertEqual(tracks[1].source, "网络直链")


class UrlTest(unittest.TestCase):
    def test_is_url(self):
        self.assertTrue(is_url("https://a/b.mp3"))
        self.assertTrue(is_url("rtsp://a/b"))
        self.assertFalse(is_url("C:\\music\\a.mp3"))
        self.assertFalse(is_url("加州梦游"))


if __name__ == "__main__":
    unittest.main()
