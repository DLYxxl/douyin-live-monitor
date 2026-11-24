import json
import time
import unittest

from douyin import (
    DouyinLookupError,
    avatar_host_ok,
    filter_catalog,
    navigation_host_ok,
    parse_count,
    parse_live_html,
    parse_query,
    prepare_catalog_room,
    query_action,
    session_metrics,
)
from server import room_detail
from server import sort_rooms


def _page(room: dict | None) -> str:
    if room is None:
        payload = json.dumps({"roomStore": {"roomInfo": {}}}, ensure_ascii=False)
    else:
        payload = json.dumps({"roomStore": {"roomInfo": {"room": room}}}, ensure_ascii=False)
    escaped = payload.replace("\\", "\\\\").replace('"', '\\"')
    return '<script>self.__pace_f.push([1,"' + escaped + '"])</script>'


class ParseTests(unittest.TestCase):
    def test_room_id_and_web_path(self):
        self.assertEqual(parse_query("7711811793449945890"), {"mode": "room_id", "value": "7711811793449945890"})
        self.assertEqual(parse_query("59288147052"), {"mode": "web_path", "value": "59288147052"})
        self.assertEqual(
            parse_query("https://live.douyin.com/EK17777777?from=share"),
            {"mode": "web_path", "value": "EK17777777"},
        )
        self.assertEqual(parse_query("@menghuanyuanzi"), {"mode": "web_path", "value": "menghuanyuanzi"})
        self.assertEqual(
            parse_query("https://webcast.amemv.com/douyin/webcast/reflow/7711811793449945890?x=1"),
            {"mode": "room_id", "value": "7711811793449945890"},
        )
        self.assertEqual(parse_query("https://v.douyin.com/iAbCdEf/")["mode"], "short_url")

    def test_rejects_other_sites_and_profile_links(self):
        with self.assertRaises(DouyinLookupError):
            parse_query("https://example.com/123")
        with self.assertRaises(DouyinLookupError):
            parse_query("http://127.0.0.1/secret")
        with self.assertRaises(DouyinLookupError):
            parse_query("https://www.douyin.com/user/MS4wLjABAAAA")
        with self.assertRaises(DouyinLookupError):
            parse_query("   ")

    def test_count_text(self):
        self.assertEqual(parse_count("1.2万"), 12000)
        self.assertEqual(parse_count("10万+"), 100000)
        self.assertEqual(parse_count(93), 93)
        self.assertIsNone(parse_count(""))

    def test_html_room_and_empty_page(self):
        html = _page(
            {
                "id_str": "1234567890123456789",
                "status": 2,
                "title": '你好"直播',
                "user_count": 1280,
                "owner": {
                    "nickname": "测试主播",
                    "display_id": "testdy",
                    "avatar_thumb": {"url_list": ["https://p3.douyinpic.com/a.jpeg"]},
                },
                "stats": {"total_user": 50, "user_count_str": "1280"},
            }
        )
        snap = parse_live_html(html)
        self.assertIsNotNone(snap)
        self.assertEqual(snap["nickname"], "测试主播")
        self.assertEqual(snap["viewer_count"], 1280)
        self.assertTrue(snap["live"])
        self.assertEqual(snap["title"], '你好"直播')
        self.assertFalse(snap["viewer_approximate"])
        self.assertIsNone(parse_live_html(_page(None)))

    def test_prefers_exact_count_over_rounded_text(self):
        snap = parse_live_html(
            _page(
                {
                    "id_str": "1234567890123456789",
                    "status": 2,
                    "title": "标题",
                    "user_count_str": "100+",
                    "owner": {"nickname": "甲"},
                    "stats": {"user_count_str": "105"},
                    "room_view_stats": {
                        "display_type": 1,
                        "display_value": 105,
                        "display_long": "105在线观众",
                        "is_hidden": False,
                    },
                }
            )
        )
        self.assertEqual(snap["viewer_count"], 105)
        self.assertFalse(snap["viewer_approximate"])
        rounded = parse_live_html(
            _page(
                {
                    "id_str": "1234567890123456789",
                    "status": 4,
                    "title": "结束",
                    "user_count_str": "100+",
                }
            )
        )
        self.assertEqual(rounded["viewer_count"], 100)
        self.assertTrue(rounded["viewer_approximate"])

    def test_hosts(self):
        self.assertTrue(navigation_host_ok("live.douyin.com"))
        self.assertFalse(navigation_host_ok("evil.douyin.com.example"))
        self.assertTrue(avatar_host_ok("p3.douyinpic.com"))
        self.assertFalse(avatar_host_ok("127.0.0.1"))

    def test_name_query_stays_off_the_direct_add_path(self):
        self.assertEqual(query_action("罗永浩"), "name")
        self.assertEqual(query_action("  娟娟姐。"), "name")
        self.assertEqual(query_action("@秋秋🎙️"), "name")
        self.assertEqual(query_action("你好 世界"), "name")
        self.assertEqual(query_action(""), "empty")
        self.assertEqual(query_action("lisa"), "direct")
        self.assertEqual(query_action("@menghuanyuanzi"), "direct")
        self.assertEqual(query_action("7711811793449945890"), "direct")
        self.assertEqual(query_action("https://live.douyin.com/EK17777777"), "direct")
        self.assertEqual(query_action("https://example.com/123"), "direct")
        self.assertEqual(parse_query("罗永浩"), {"mode": "web_path", "value": "罗永浩"})

    def test_catalog_match_ranks_exact_name_before_a_busier_partial(self):
        snaps = [
            {"nickname": "小娟娟", "viewer_count": 5000, "room_id": "1"},
            {"nickname": "娟娟", "viewer_count": 3, "room_id": "2"},
            {"nickname": "别人", "viewer_count": 9, "room_id": "3"},
            {"nickname": "娟娟姐", "viewer_count": 80, "room_id": "4"},
            {"nickname": "", "viewer_count": 100, "room_id": "5"},
        ]
        matched, total = filter_catalog(snaps, "娟娟", limit=10)
        self.assertEqual(total, 3)
        self.assertEqual([item["nickname"] for item in matched], ["娟娟", "娟娟姐", "小娟娟"])
        self.assertEqual(filter_catalog(snaps, "   ", limit=10), ([], 0))

    def test_catalog_room_uses_audience_text_and_does_not_invent_a_count(self):
        live = prepare_catalog_room(
            {
                "id_str": "7711843011574975232",
                "status": 0,
                "title": "伤感情歌",
                "user_count": 589,
                "owner": {
                    "nickname": "秋秋",
                    "display_id": "32930508987",
                    "web_rid": "32089583401",
                },
                "room_view_stats": {
                    "display_type": 1,
                    "display_value": 589,
                    "display_long": "589在线观众",
                    "is_hidden": False,
                },
            }
        )
        self.assertTrue(live["live"])
        self.assertEqual(live["viewer_count"], 589)
        self.assertEqual(live["web_path"], "32930508987")
        self.assertFalse(live["viewer_approximate"])
        missing = prepare_catalog_room(
            {
                "id_str": "7711843011574975233",
                "status": 0,
                "title": "没给人数",
                "owner": {"nickname": "没有人数"},
            }
        )
        self.assertIsNone(missing["viewer_count"])
        self.assertFalse(missing["live"])
        self.assertIsNone(
            prepare_catalog_room(
                {"id_str": "1", "status": 4, "owner": {"nickname": "已结束"}, "user_count": 10}
            )
        )

    def test_session_metrics_keep_follows_and_likes_but_not_zeroed_gifts(self):
        metrics = session_metrics(
            {
                "start_time": 1795556003,
                "like_count": 3251,
                "stats": {"follow_count": 106, "like_count": 0, "gift_uv_count": 0, "fan_ticket": 0},
            }
        )
        self.assertEqual(metrics["started_at"], 1795556003)
        self.assertEqual(metrics["session_follows"], 106)
        self.assertEqual(metrics["like_count"], 3251)
        self.assertIsNone(metrics["gift_count"])

    def test_detail_does_not_invent_today_followers_or_gift_segments(self):
        detail = room_detail(
            {
                "live": True,
                "started_at": int(time.time()) - 125,
                "session_follows": 4,
                "like_count": 9,
                "viewer_count": 20,
            },
            [{"t": 1, "count": 10}, {"t": 2, "count": 20}],
        )
        self.assertIsNone(detail["today_followers"])
        self.assertIsNone(detail["gift_segments"])
        self.assertIn("自然日", detail["today_followers_reason"])
        self.assertIn("礼物", detail["gift_reason"])
        self.assertEqual(detail["session_follows"], 4)
        self.assertGreaterEqual(detail["live_seconds"], 120)
        self.assertEqual(detail["observed_peak"], 20)
        offline = room_detail({"live": False, "started_at": int(time.time()) - 500}, [])
        self.assertIsNone(offline["live_seconds"])

    def test_sort_live_rooms_by_viewers(self):
        ordered = sort_rooms(
            [
                {"nickname": "乙", "live": True, "viewer_count": 10},
                {"nickname": "甲", "live": False, "viewer_count": 9999, "title": "上次"},
                {"nickname": "丙", "live": True, "viewer_count": 80},
                {"nickname": "", "live": False, "query": "坏的"},
            ]
        )
        self.assertEqual([item["nickname"] for item in ordered[:2]], ["丙", "乙"])
        self.assertEqual(ordered[-1]["query"], "坏的")


if __name__ == "__main__":
    unittest.main()
