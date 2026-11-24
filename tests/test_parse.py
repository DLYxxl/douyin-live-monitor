import json
import unittest

from douyin import (
    DouyinLookupError,
    avatar_host_ok,
    navigation_host_ok,
    parse_count,
    parse_live_html,
    parse_query,
)
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
