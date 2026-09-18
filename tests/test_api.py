import unittest

from musiccil.api import ApiError, MusicApi, Track, normalize_platform


class NormalizeTest(unittest.TestCase):
    def test_codes_and_names(self):
        cases = {
            "wy": "wy", "网易": "wy", "网易云": "wy", "netease": "wy",
            "qq": "qq", "QQ音乐": "qq", "tencent": "qq",
            "kw": "kw", "酷我": "kw", "kg": "kg", "酷狗": "kg",
            "mg": "mg", "咪咕": "mg", "qi": "qi", "千千": "qi",
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_platform(raw), expected, raw)

    def test_unknown(self):
        self.assertIsNone(normalize_platform("spotify"))
        self.assertIsNone(normalize_platform(""))


class UnwrapTest(unittest.TestCase):
    def test_success_payload(self):
        body = MusicApi._unwrap({"code": 1, "msg": "获取成功", "data": [{"id": 1}]}, "search")
        self.assertEqual(body, [{"id": 1}])

    def test_userlist_uses_playlist_root(self):
        body = MusicApi._unwrap({"code": 200, "playlist": [{"id": 9}]}, "userlist", root="playlist")
        self.assertEqual(body, [{"id": 9}])

    def test_failure_codes_raise(self):
        for payload in (
            {"code": 0, "msg": "获取失败", "data": ""},
            {"code": 0, "msg": "密钥KEY可用次数不足", "data": ""},
        ):
            with self.assertRaises(ApiError) as ctx:
                MusicApi._unwrap(payload, "info")
            self.assertEqual(str(ctx.exception), payload["msg"])

    def test_quota_error_flag(self):
        with self.assertRaises(ApiError) as ctx:
            MusicApi._unwrap({"code": 0, "msg": "密钥KEY可用次数不足", "data": ""}, "info")
        self.assertTrue(ctx.exception.is_quota)

    def test_empty_data_variants_raise(self):
        for empty in ("", "[]", "{}", "null", None):
            with self.assertRaises(ApiError):
                MusicApi._unwrap({"code": 1, "msg": "获取成功", "data": empty}, "info")

    def test_json_string_data_is_parsed(self):
        body = MusicApi._unwrap({"code": 1, "msg": "ok", "data": '{"a": 1}'}, "info")
        self.assertEqual(body, {"a": 1})

    def test_non_dict_payload(self):
        with self.assertRaises(ApiError):
            MusicApi._unwrap([], "info")


class TrackTest(unittest.TestCase):
    def test_round_trip(self):
        track = Track(id="1", type="qq", name="歌", artist="A/B", url="http://x", lrc="[00:00.00]x")
        restored = Track.from_dict(track.to_dict())
        self.assertEqual(restored.id, "1")
        self.assertEqual(restored.type, "qq")
        self.assertEqual(restored.url, "http://x")
        self.assertEqual(restored.artist, "A/B")

    def test_from_dict_tolerates_noise(self):
        track = Track.from_dict({"name": "n", "unknown": 5, "type": "wy"})
        self.assertEqual(track.name, "n")
        self.assertEqual(track.extra.get("unknown"), 5)

    def test_from_dict_rejects_non_dict(self):
        self.assertEqual(Track.from_dict("nope").name, "")

    def test_display_artist(self):
        self.assertEqual(Track(artist="A/B").display_artist, "A / B")

    def test_platform_label(self):
        self.assertEqual(Track(type="wy").platform, "网易云")
        self.assertEqual(Track(type="").platform, "本地")

    def test_tracks_from_list_handles_ragged_arrays(self):
        payload = {
            "songId": [1, 2, 3],
            "songName": ["a", "b"],
            "artistName": ["x"],
            "albumName": ["m", "n", "o"],
            "type": ["wy", "qq"],
        }
        tracks = MusicApi.tracks_from_list(payload, "wy")
        self.assertEqual(len(tracks), 3)
        self.assertEqual(tracks[2].name, "")
        self.assertEqual(tracks[1].type, "qq")
        self.assertEqual(tracks[2].type, "wy")
        self.assertEqual(tracks[2].album, "o")

    def test_tracks_from_list_rejects_garbage(self):
        self.assertEqual(MusicApi.tracks_from_list(None), [])
        self.assertEqual(MusicApi.tracks_from_list({}), [])


class KeyTest(unittest.TestCase):
    def test_env_override(self):
        import os

        os.environ["MUSICCIL_API_KEY"] = "test-key-123"
        try:
            self.assertEqual(MusicApi().key, "test-key-123")
            self.assertEqual(MusicApi(key="explicit").key, "explicit")
        finally:
            os.environ.pop("MUSICCIL_API_KEY", None)

    def test_no_hardcoded_key_in_repo(self):
        """密钥不能写进代码：公开仓库里硬编码等于把配额送人。"""
        import musiccil.api as m

        self.assertFalse(hasattr(m, "DEFAULT_KEY"), "又出现硬编码默认密钥了")

    def test_missing_key_reports_help_not_network_error(self):
        import os
        from unittest import mock

        from musiccil.api import ApiError, MusicApi

        saved = os.environ.pop("MUSICCIL_API_KEY", None)
        try:
            with mock.patch.object(MusicApi, "__init__", lambda self, key=None, **kw: None):
                pass
            api = MusicApi(key="")
            api.key = ""
            with mock.patch("builtins.open", side_effect=OSError):
                with self.assertRaises(ApiError) as ctx:
                    api._request("search")
            self.assertIn("MUSICCIL_API_KEY", str(ctx.exception))
        finally:
            if saved:
                os.environ["MUSICCIL_API_KEY"] = saved

    def test_key_file_is_used_when_env_absent(self):
        import os
        import tempfile
        from unittest import mock

        from musiccil import api as m

        saved = os.environ.pop(m.ENV_KEY, None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "key")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("  file-key-456\n")
                with mock.patch.object(m, "KEY_FILE", path):
                    self.assertEqual(m.resolve_key(), "file-key-456")
        finally:
            if saved:
                os.environ[m.ENV_KEY] = saved


if __name__ == "__main__":
    unittest.main()
