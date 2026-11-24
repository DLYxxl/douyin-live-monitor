"""读取抖音公开直播间数据。

只用浏览器也能打开的公开页面和公开 webcast 接口，不登录、不携带密钥。
直播间网址（live.douyin.com/号码或抖音号）里的页面状态，以及：

- https://webcast.amemv.com/webcast/room/reflow/info/  按房间号读在线人数
- https://live.douyin.com/webcast/feed/               公开信息流里正在直播的房间

`aid=6383` 和 `app_id=1128` 是网页端公开客户端标识，不是密钥。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

FEED_URL = (
    "https://live.douyin.com/webcast/feed/"
    "?aid=6383&app_name=douyin_web&live_id=1&device_platform=web&channel=test&count=6"
)
REFLOW_URL = (
    "https://webcast.amemv.com/webcast/room/reflow/info/"
    "?type_id=0&live_id=1&app_id=1128&room_id={room_id}"
)

_NAV_SUFFIXES = ("douyin.com", "iesdouyin.com", "amemv.com", "douyin.cn")
_AVATAR_SUFFIXES = (
    "douyinpic.com",
    "douyinstatic.com",
    "byteimg.com",
    "bytecdn.cn",
    "ibyteimg.com",
    "bytetos.com",
    "bytescm.com",
    "bytegoofy.com",
)
_ID_RE = re.compile(r"^[\w.\-]{1,80}$", re.UNICODE)
_PACE_MARK = 'self.__pace_f.push([1,"'


class DouyinLookupError(Exception):
    """用户能看懂的查询失败。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)


def navigation_host_ok(host: str) -> bool:
    return host_matches(host, _NAV_SUFFIXES)


def avatar_host_ok(host: str) -> bool:
    return host_matches(host, _AVATAR_SUFFIXES)


def parse_count(value: object) -> int | None:
    """把接口里的人数字段转成整数。支持 1.2万、10万+ 这种展示文案。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "").replace("，", "").replace("+", "")
    if not text:
        return None
    multiplier = 1
    if text.endswith("万") or text.lower().endswith("w"):
        multiplier = 10_000
        text = text[:-1]
    elif text.endswith("亿"):
        multiplier = 100_000_000
        text = text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return None


def parse_query(raw: str) -> dict[str, str]:
    """把用户输入分成房间号、直播间路径或短链。

    16 位及以上的纯数字视为一场直播的房间号（接口里的 room_id）。
    更短的纯数字、抖音号，视为 live.douyin.com 后面的那段路径。
    """
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", raw or "")
    text = text.strip().strip("，。,.、；;\"'“”‘’")
    if not text:
        raise DouyinLookupError("请输入直播间链接、房间号或抖音号")
    if len(text) > 500:
        raise DouyinLookupError("输入太长了")

    pieces = text.split()
    if len(pieces) > 1 and (
        pieces[0].startswith("http://")
        or pieces[0].startswith("https://")
        or "douyin.com" in pieces[0]
        or "amemv.com" in pieces[0]
    ):
        text = pieces[0]

    if text.startswith("@"):
        text = text[1:].strip()

    if "://" not in text and ("douyin.com" in text or "amemv.com" in text):
        text = "https://" + text.lstrip("/")

    if "://" in text:
        parsed = urllib.parse.urlparse(text)
        host = (parsed.hostname or "").lower()
        if not navigation_host_ok(host):
            raise DouyinLookupError("只支持抖音直播间链接，不接受其他网站的地址")
        if host == "v.douyin.com" or host.endswith(".v.douyin.com"):
            return {"mode": "short_url", "value": text}
        path = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
        if host.endswith("amemv.com"):
            for index, part in enumerate(path):
                if part == "reflow" and index + 1 < len(path) and path[index + 1].isdigit():
                    return {"mode": "room_id", "value": path[index + 1]}
            raise DouyinLookupError("这条链接里没有直播房间号")
        if "www.douyin.com" == host and path and path[0] == "user":
            raise DouyinLookupError("这是个人主页链接。请改用直播间链接（live.douyin.com）或抖音号")
        if path[:1] == ["follow"] and len(path) >= 3 and path[1] == "live":
            return _classify_token(path[2])
        if path[:1] == ["live"] and len(path) >= 2:
            return _classify_token(path[1])
        if not path:
            raise DouyinLookupError("链接里没有直播间号码或抖音号")
        return _classify_token(path[0])

    return _classify_token(text)


def _classify_token(token: str) -> dict[str, str]:
    token = token.strip().strip("/")
    if not token or not _ID_RE.match(token):
        raise DouyinLookupError("直播间标识里有无法识别的字符。请粘贴直播间链接、房间号或抖音号")
    if token.isdigit() and len(token) >= 16:
        return {"mode": "room_id", "value": token}
    return {"mode": "web_path", "value": token}


def _decode_js_string(html: str, start: int) -> tuple[str, int]:
    """从 start 指向的 JS 双引号字符串内容开始解码，返回文本和下一个位置。"""
    out: list[str] = []
    index = start
    limit = min(len(html), start + 2_000_000)
    while index < limit:
        char = html[index]
        if char == "\\":
            if index + 1 >= len(html):
                break
            nxt = html[index + 1]
            mapping = {'"': '"', "\\": "\\", "/": "/", "n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}
            if nxt in mapping:
                out.append(mapping[nxt])
                index += 2
                continue
            if nxt == "u" and index + 6 <= len(html):
                hex_digits = html[index + 2 : index + 6]
                try:
                    out.append(chr(int(hex_digits, 16)))
                    index += 6
                    continue
                except ValueError:
                    pass
            out.append(nxt)
            index += 2
            continue
        if char == '"':
            return "".join(out), index + 1
        out.append(char)
        index += 1
    return "".join(out), index


def iter_pace_payloads(html: str) -> Iterator[str]:
    start = 0
    while True:
        found = html.find(_PACE_MARK, start)
        if found < 0:
            return
        payload, start = _decode_js_string(html, found + len(_PACE_MARK))
        if payload:
            yield payload


def _extract_object(text: str, brace_at: int) -> str | None:
    if brace_at < 0 or brace_at >= len(text) or text[brace_at] != "{":
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(brace_at, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[brace_at : index + 1]
    return None


def normalize_room(room: dict) -> dict:
    owner = room.get("owner") if isinstance(room.get("owner"), dict) else {}
    urls = []
    for source in (owner.get("avatar_thumb"), owner.get("avatar_medium"), owner.get("avatar_large")):
        if not isinstance(source, dict):
            continue
        listed = source.get("url_list") if isinstance(source.get("url_list"), list) else []
        for url in listed:
            if isinstance(url, str) and url.startswith("https://") and url not in urls:
                urls.append(url)
            if len(urls) >= 3:
                break
        if len(urls) >= 3:
            break
    avatar = urls[0] if urls else ""
    status_raw = room.get("status")
    try:
        status = int(status_raw)
    except (TypeError, ValueError):
        status = 0
    stats = room.get("stats") if isinstance(room.get("stats"), dict) else {}
    view_stats = room.get("room_view_stats") if isinstance(room.get("room_view_stats"), dict) else {}
    # 整数在线人数优先。页面上的「100+」是模糊展示，不能把它当成精确值。
    def exact_count(value: object) -> int | None:
        if isinstance(value, str) and "+" in value:
            return None
        return parse_count(value)

    viewer_count = exact_count(room.get("user_count"))
    if viewer_count is None:
        viewer_count = exact_count(stats.get("user_count_str"))
    approximate = False
    if viewer_count is None and view_stats.get("display_type") == 1 and not view_stats.get("is_hidden"):
        viewer_count = parse_count(view_stats.get("display_value"))
        if viewer_count is None:
            viewer_count = parse_count(view_stats.get("display_short"))
    if viewer_count is None:
        loose = str(room.get("user_count_str") or stats.get("user_count_str") or "")
        viewer_count = parse_count(loose)
        approximate = bool(viewer_count is not None and "+" in loose)
    total_user = parse_count(stats.get("total_user"))
    if total_user is None:
        total_user = parse_count(stats.get("total_user_str"))
    room_id = str(room.get("id_str") or room.get("id") or "")
    return {
        "room_id": room_id,
        "display_id": str(owner.get("display_id") or ""),
        "nickname": str(owner.get("nickname") or ""),
        "avatar": avatar,
        "avatar_urls": urls,
        "title": str(room.get("title") or ""),
        "status": status,
        "live": status == 2,
        "viewer_count": viewer_count,
        "viewer_approximate": approximate,
        "total_user": total_user,
        "web_path": "",
    }


def _room_score(snap: dict) -> tuple:
    return (
        1 if snap.get("nickname") else 0,
        1 if snap.get("viewer_count") is not None and not snap.get("viewer_approximate") else 0,
        1 if snap.get("title") else 0,
    )


def parse_live_html(html: str) -> dict | None:
    """从直播间网页内嵌状态取出房间信息。找不到房间时返回 None。"""
    best: dict | None = None
    for payload in iter_pace_payloads(html):
        start = 0
        while True:
            marker = payload.find('"roomStore":', start)
            if marker < 0:
                break
            start = marker + len('"roomStore":')
            brace = payload.find("{", marker, marker + 80)
            raw = _extract_object(payload, brace)
            if not raw:
                continue
            try:
                room_store = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(room_store, dict):
                continue
            room_info = room_store.get("roomInfo")
            room = room_info.get("room") if isinstance(room_info, dict) else None
            if not isinstance(room, dict) or not (room.get("id_str") or room.get("id")):
                continue
            snap = normalize_room(room)
            if best is None or _room_score(snap) > _room_score(best):
                best = snap
    return best


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        absolute = urllib.parse.urljoin(req.full_url, newurl)
        host = urllib.parse.urlparse(absolute).hostname or ""
        if not navigation_host_ok(host):
            return None
        return super().redirect_request(req, fp, code, msg, headers, absolute)


class DouyinClient:
    def __init__(self) -> None:
        self.jar = urllib.request.HTTPCookieProcessor()
        self.opener = urllib.request.build_opener(_SafeRedirect(), self.jar)
        self._warmed_at = 0.0

    def _has_ttwid(self) -> bool:
        return any(cookie.name == "ttwid" for cookie in self.jar.cookiejar)

    def _request(self, url: str, accept: str, timeout: float = 15) -> tuple[int, str, bytes]:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": accept,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://live.douyin.com/",
            },
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return response.status, response.geturl(), response.read()
        except urllib.error.HTTPError as error:
            if error.code in (403, 429, 444):
                raise DouyinLookupError("抖音暂时拒绝了请求，可能是访问太频繁。请稍后再试") from error
            if error.code == 404:
                raise DouyinLookupError("抖音没有这个地址") from error
            raise DouyinLookupError(f"抖音返回了错误（HTTP {error.code}）") from error
        except (TimeoutError, urllib.error.URLError) as error:
            raise DouyinLookupError("网络请求失败。请检查能否打开 live.douyin.com，然后重试") from error

    def warm(self) -> None:
        if self._warmed_at and time.time() - self._warmed_at < 1800 and self._has_ttwid():
            return
        try:
            self._request("https://live.douyin.com/", "text/html,application/xhtml+xml", timeout=15)
            self._warmed_at = time.time()
        except DouyinLookupError:
            return

    def _json(self, url: str) -> dict:
        _status, _final, body = self._request(url, "application/json, text/plain, */*")
        if not body.strip():
            raise DouyinLookupError("抖音返回了空内容，可能触发了访问限制。请稍后再试")
        try:
            data = json.loads(body.decode("utf-8", "ignore"))
        except json.JSONDecodeError as error:
            raise DouyinLookupError("抖音返回的内容无法解析") from error
        if not isinstance(data, dict):
            raise DouyinLookupError("抖音返回的内容无法解析")
        return data

    def fetch_by_room_id(self, room_id: str) -> dict:
        if not room_id.isdigit():
            raise DouyinLookupError("房间号必须是数字")
        data = self._json(REFLOW_URL.format(room_id=urllib.parse.quote(room_id)))
        status_code = data.get("status_code")
        room = data.get("data", {}).get("room") if isinstance(data.get("data"), dict) else None
        if status_code not in (0, None) or not isinstance(room, dict) or not (room.get("id_str") or room.get("id")):
            raise DouyinLookupError("没有找到这个房间号")
        snap = normalize_room(room)
        if snap.get("display_id"):
            snap["web_path"] = snap["display_id"]
        return snap

    def fetch_by_web_path(self, web_path: str) -> dict:
        self.warm()
        quoted = urllib.parse.quote(web_path, safe="")
        _status, final, body = self._request(
            f"https://live.douyin.com/{quoted}",
            "text/html,application/xhtml+xml",
            timeout=20,
        )
        html = body.decode("utf-8", "ignore")
        snap = parse_live_html(html)
        if snap is None:
            raise DouyinLookupError("没有找到这个直播间。请核对链接、房间号或抖音号是否正确")
        final_host = (urllib.parse.urlparse(final).hostname or "").lower()
        final_parts = [urllib.parse.unquote(part) for part in urllib.parse.urlparse(final).path.split("/") if part]
        if final_host.endswith("live.douyin.com") and final_parts:
            snap["web_path"] = final_parts[0]
        else:
            snap["web_path"] = web_path
        # 网页里经常没有抖音号，在线人数也可能被写成「100+」。房间接口能补上这两项。
        if snap.get("room_id"):
            try:
                rich = self.fetch_by_room_id(snap["room_id"])
            except DouyinLookupError:
                rich = None
            if rich:
                for field in ("nickname", "display_id", "avatar", "title"):
                    if rich.get(field):
                        snap[field] = rich[field]
                snap["live"] = rich["live"]
                snap["status"] = rich["status"]
                if rich.get("viewer_count") is not None:
                    snap["viewer_count"] = rich["viewer_count"]
                    snap["viewer_approximate"] = rich.get("viewer_approximate", False)
                if rich.get("total_user") is not None:
                    snap["total_user"] = rich["total_user"]
        return snap

    def resolve_input(self, raw: str) -> dict:
        parsed = parse_query(raw)
        if parsed["mode"] == "short_url":
            _status, final, _body = self._request(parsed["value"], "text/html,application/xhtml+xml", timeout=20)
            parsed = parse_query(final)
            if parsed["mode"] == "short_url":
                raise DouyinLookupError("这条短链没有打开直播间")
        if parsed["mode"] == "room_id":
            try:
                snap = self.fetch_by_room_id(parsed["value"])
            except DouyinLookupError:
                snap = self.fetch_by_web_path(parsed["value"])
        else:
            snap = self.fetch_by_web_path(parsed["value"])
        if parsed["mode"] == "web_path":
            snap["web_path"] = snap.get("web_path") or parsed["value"]
        elif not snap.get("web_path") and snap.get("display_id"):
            snap["web_path"] = snap["display_id"]
        return snap

    def fetch_snapshot(self, previous: dict) -> dict:
        """刷新一个已保存的直播间。开播时走轻量房间接口，未开播时偶尔回查网页以捕捉新一场。"""
        web_path = str(previous.get("web_path") or "")
        room_id = str(previous.get("room_id") or "")
        ticks = int(previous.get("_offline_ticks") or 0)
        was_live = bool(previous.get("live"))

        def finish(snap: dict, offline_ticks: int) -> dict:
            if not snap.get("web_path"):
                snap["web_path"] = web_path or snap.get("display_id") or ""
            snap["_offline_ticks"] = 0 if snap.get("live") else offline_ticks
            return snap

        if was_live and room_id:
            snap = self.fetch_by_room_id(room_id)
            if not snap["live"] and web_path:
                try:
                    snap = self.fetch_by_web_path(web_path)
                except DouyinLookupError:
                    pass
            return finish(snap, 1)

        if web_path and (not room_id or ticks % 3 == 0):
            try:
                snap = self.fetch_by_web_path(web_path)
            except DouyinLookupError:
                if not room_id:
                    raise
                snap = self.fetch_by_room_id(room_id)
            return finish(snap, ticks + 1)

        if room_id:
            return finish(self.fetch_by_room_id(room_id), ticks + 1)
        raise DouyinLookupError("缺少直播间标识，请删掉后重新添加")

    def suggestions(self, pages: int = 3) -> list[dict]:
        """从公开直播信息流取一批正在开播的房间，按在线人数从高到低。"""
        self.warm()
        found: dict[str, dict] = {}
        max_time: int | None = None
        page_count = max(1, min(pages, 4))
        for index in range(page_count):
            url = FEED_URL
            if max_time:
                url += f"&max_time={int(max_time)}&action=loadmore"
            data = self._json(url)
            if data.get("status_code") not in (0, None):
                raise DouyinLookupError("热播列表暂时不可用")
            items = data.get("data") if isinstance(data.get("data"), list) else []
            for item in items:
                room = item.get("data") if isinstance(item, dict) else None
                if not isinstance(room, dict) or room.get("status") != 2:
                    continue
                snap = normalize_room(room)
                if not snap["room_id"]:
                    continue
                if snap.get("display_id"):
                    snap["web_path"] = snap["display_id"]
                found[snap["room_id"]] = snap
            extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
            max_time = extra.get("max_time")
            if not extra.get("has_more") or not max_time:
                break
            if index + 1 < page_count:
                time.sleep(0.25)
        return sorted(
            found.values(),
            key=lambda snap: snap["viewer_count"] if snap["viewer_count"] is not None else -1,
            reverse=True,
        )
