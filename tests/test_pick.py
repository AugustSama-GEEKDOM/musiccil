"""挑选与换平台：直接决定"搜一次能不能拿对歌"，也是流程提速的关键。"""

import unittest
from unittest import mock

from musiccil.api import ApiError, MusicApi, Track, _artist_set, _norm, pick_mix


class NormTest(unittest.TestCase):
    def test_strips_brackets_and_punctuation(self):
        self.assertEqual(_norm("A Rusty Dream (from Cyberpunk)"), "arustydream")
        self.assertEqual(_norm("加州梦游（Live）"), "加州梦游")
        self.assertEqual(_norm("  Hello - World  "), "helloworld")

    def test_artist_set_splits_collaborators(self):
        self.assertEqual(_artist_set("周杰伦/温岚"), {"周杰伦", "温岚"})
        self.assertEqual(_artist_set("A & B"), {"a", "b"})
        self.assertIn("周杰伦", _artist_set("周杰伦 feat. 某人"))

    def test_artist_set_does_not_merge_joined_names(self):
        # "周杰伦./街道办GDC" 是翻唱联名，不能算成"周杰伦"本人
        self.assertNotIn("周杰伦", _artist_set("周杰伦./街道办GDC"))


class ScoreTest(unittest.TestCase):
    def test_exact_artist_beats_substring_collab(self):
        want_name, want_artist = _norm("稻香"), _norm("周杰伦")
        real = MusicApi._score(Track(name="稻香", artist="周杰伦"), want_name, want_artist, 0)
        cover = MusicApi._score(
            Track(name="稻香(治愈版)", artist="周杰伦./街道办GDC"), want_name, want_artist, 1
        )
        self.assertTrue(real[1], "原唱应记为歌手命中")
        self.assertFalse(cover[1], "联名翻唱不该记为歌手命中")
        self.assertGreater(real[0], cover[0])

    def test_name_hit_flag(self):
        want_name, want_artist = _norm("夜曲"), _norm("周杰伦")
        wrong_song = MusicApi._score(Track(name="布拉格广场", artist="周杰伦"), want_name, want_artist, 0)
        self.assertTrue(wrong_song[1], "歌手命中")
        self.assertFalse(wrong_song[2], "但歌名不对，不能当成原唱")


class FallbackTest(unittest.TestCase):
    """指定平台没有原唱时，应自动换到有收录的平台。"""

    def setUp(self):
        self.api = MusicApi()

    def _search_map(self, mapping):
        def fake(name, platform, page=1, limit=10):
            if platform not in mapping:
                raise ApiError("获取失败", endpoint="search")
            return mapping[platform]
        return fake

    def test_falls_back_to_platform_that_has_original(self):
        mapping = {
            "wy": [Track(id="1", type="wy", name="晴天(深情版)", artist="Lucky小爱"),
                   Track(id="2", type="wy", name="晴天", artist="梦里啥都有")],
            "kg": [Track(id="9", type="kg", name="晴天", artist="周杰伦")],
        }
        with mock.patch.object(self.api, "search", side_effect=self._search_map(mapping)):
            picked = self.api.best_across_platforms("晴天", "周杰伦", "wy", candidates=5)
        self.assertIsNotNone(picked)
        self.assertEqual(picked.type, "kg")
        self.assertEqual(picked.artist, "周杰伦")

    def test_prefers_requested_platform_when_original_present(self):
        mapping = {"wy": [Track(id="1", type="wy", name="海阔天空", artist="Beyond")]}
        with mock.patch.object(self.api, "search", side_effect=self._search_map(mapping)):
            picked = self.api.best_across_platforms("海阔天空", "Beyond", "wy")
        self.assertEqual(picked.type, "wy")

    def test_no_artist_uses_plain_best_match(self):
        mapping = {"wy": [Track(id="1", type="wy", name="晴天", artist="某人")]}
        with mock.patch.object(self.api, "search", side_effect=self._search_map(mapping)):
            picked = self.api.best_across_platforms("晴天", "", "wy")
        self.assertEqual(picked.id, "1")

    def test_returns_none_when_everything_fails(self):
        with mock.patch.object(self.api, "search", side_effect=ApiError("失败", endpoint="search")):
            self.assertIsNone(self.api.best_across_platforms("晴天", "周杰伦", "wy"))

    def test_fallback_disabled_stays_on_platform(self):
        mapping = {
            "wy": [Track(id="1", type="wy", name="晴天(深情版)", artist="Lucky小爱")],
            "kg": [Track(id="9", type="kg", name="晴天", artist="周杰伦")],
        }
        with mock.patch.object(self.api, "search", side_effect=self._search_map(mapping)):
            picked = self.api.best_across_platforms(
                "晴天", "周杰伦", "wy", allow_fallback=False
            )
        self.assertEqual(picked.type, "wy")

    def test_duplicate_query_skipped_when_artist_already_in_name(self):
        calls = []

        def fake(name, platform, page=1, limit=10):
            calls.append((platform, name))
            return [Track(id="1", type="wy", name="晴天 周杰伦", artist="周杰伦")]

        with mock.patch.object(self.api, "search", side_effect=fake):
            self.api.best_across_platforms("晴天 周杰伦", "周杰伦", "wy")
        # 跨平台是并发试探，每个平台搜一次；歌手已在歌名里就不要再拼一次 "歌名+歌手"
        self.assertEqual(len(calls), len(set(calls)), "同一平台同一关键词不该重复搜")
        self.assertTrue(all(name == "晴天 周杰伦" for _, name in calls), calls)


class PickMixTest(unittest.TestCase):
    """一次搜索就能凑出一份可直接播的歌单，省掉让模型反复读候选的往返。"""

    def test_dedupes_same_song_and_caps_per_artist(self):
        hits = [
            Track(id="1", name="夜曲", artist="周杰伦"),
            Track(id="2", name="夜曲", artist="周杰伦"),
            Track(id="3", name="稻香", artist="周杰伦"),
            Track(id="4", name="晴天", artist="周杰伦"),
            Track(id="5", name="海阔天空", artist="Beyond"),
        ]
        # 候选够多时严格守住"同歌手最多 2 首"，重复的《夜曲》只留一条
        self.assertEqual([t.id for t in pick_mix(hits, count=3, per_artist=2)], ["1", "3", "5"])
        # 要的歌比严格规则能给的多，就放开同歌手限制把人凑齐，而不是交回 3 首
        self.assertEqual(
            [t.id for t in pick_mix(hits, count=4, per_artist=2)], ["1", "3", "5", "4"]
        )

    def test_same_song_with_reversed_artist_order_counts_once(self):
        # 同一首歌在不同条目里歌手顺序可能相反（"李玟/周杰伦" 与 "周杰伦/李玟"），
        # 按字符串去重会漏掉，歌单里就会出现重复条目。
        hits = [
            Track(id="1", name="刀马旦", artist="李玟/周杰伦"),
            Track(id="2", name="刀马旦", artist="周杰伦/李玟"),
        ]
        self.assertEqual([t.id for t in pick_mix(hits, count=5, per_artist=0)], ["1"])
        # 顺序反过来同样只留先出现的那条
        flipped = list(reversed(hits))
        self.assertEqual([t.id for t in pick_mix(flipped, count=5, per_artist=0)], ["2"])

    def test_skips_live_and_remix_unless_requested(self):
        hits = [
            Track(id="1", name="晴天", artist="周杰伦"),
            Track(id="2", name="稻香 Remix", artist="周杰伦"),
            Track(id="3", name="稻香 Remix", artist="周杰伦"),
        ]
        # 默认丢掉 remix 版，只要原版
        self.assertEqual([t.id for t in pick_mix(hits)], ["1"])
        # 明确要变体时，remix 与同名原版一起保留（同 id 之外的重复仍会去掉）
        self.assertEqual([t.id for t in pick_mix(hits, want_variants=True)], ["1", "2"])

    def test_live_suffix_dedupes_with_original(self):
        # "(Live)" 既会被变体规则过滤，归一化后也和原版同名，最终只留原版
        hits = [
            Track(id="1", name="晴天 (Live)", artist="周杰伦"),
            Track(id="2", name="晴天", artist="周杰伦"),
        ]
        self.assertEqual([t.id for t in pick_mix(hits)], ["2"])
        # 明确要变体时也只保留一条，不会把同一首歌的 live 版重复塞进歌单
        self.assertEqual([t.id for t in pick_mix(hits, want_variants=True)], ["1"])

    def test_respects_count_and_empty(self):
        hits = [Track(id=str(i), name="歌%d" % i, artist="A") for i in range(5)]
        self.assertEqual(len(pick_mix(hits, count=3, per_artist=0)), 3)
        self.assertEqual(pick_mix([], count=3), [])

    def test_relaxes_artist_cap_when_search_is_dominated_by_one_artist(self):
        # 按风格搜索时常整页都是同一位歌手，此时不该只给用户 2 首
        hits = [Track(id=str(i), name="歌%d" % i, artist="同一个人") for i in range(6)]
        picked = pick_mix(hits, count=5, per_artist=2)
        self.assertEqual(len(picked), 5)

    def test_variants_only_used_when_nothing_else_available(self):
        mixed = [
            Track(id="1", name="A (Live)", artist="X"),
            Track(id="2", name="B", artist="Y"),
        ]
        # 有原版可选时，不给变体留位置
        self.assertEqual([t.id for t in pick_mix(mixed, count=5)], ["2"])
        # 全是变体时回退使用，不至于给空歌单
        only_variants = [Track(id="1", name="A (Live)", artist="X")]
        self.assertEqual([t.id for t in pick_mix(only_variants, count=5)], ["1"])


if __name__ == "__main__":
    unittest.main()
