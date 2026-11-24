"""本地抖音直播在线人数监测服务。"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from douyin import DouyinClient, DouyinLookupError, avatar_host_ok

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA_PATH = ROOT / "data" / "watchlist.json"
MAX_ROOMS = 20
DEFAULT_INTERVAL = 10
HISTORY_LIMIT = 36

rooms: list[dict] = []
histories: dict[str, list[dict]] = {}
data_lock = threading.Lock()
net_lock = threading.Lock()
refresh_lock = threading.Lock()
client = DouyinClient()

interval_seconds = DEFAULT_INTERVAL
last_refresh_at: float | None = None
refreshing = False
stop_event = threading.Event()


def log(message: str) -> None:
    try:
        print(time.strftime("%H:%M:%S"), message, file=sys.stderr)
    except UnicodeEncodeError:
        print(time.strftime("%H:%M:%S"), message.encode("utf-8", "replace"), file=sys.stderr)


def sort_rooms(items: list[dict]) -> list[dict]:
    def key(item: dict) -> tuple:
        if item.get("live"):
            tier = 0
            count = -(item.get("viewer_count") or 0)
        elif item.get("nickname") or item.get("title"):
            tier = 1
            count = 0
        else:
            tier = 2
            count = 0
        return (tier, count, item.get("nickname") or item.get("query") or "")

    return sorted(items, key=key)


def _persistable(item: dict) -> dict:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def save_rooms() -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "rooms": [_persistable(item) for item in rooms]}
    temporary = DATA_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(DATA_PATH)


def load_rooms() -> None:
    global rooms
    if not DATA_PATH.exists():
        rooms = []
        return
    try:
        payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        loaded = payload.get("rooms") if isinstance(payload, dict) else None
        rooms = [item for item in loaded if isinstance(item, dict) and item.get("id")] if isinstance(loaded, list) else []
    except (OSError, json.JSONDecodeError) as error:
        backup = DATA_PATH.with_suffix(".bad")
        try:
            DATA_PATH.replace(backup)
        except OSError:
            pass
        rooms = []
        log(f"名单文件无法读取，已忽略并备份：{error}")


def room_link(item: dict) -> str:
    web_path = item.get("web_path") or item.get("display_id") or ""
    if web_path:
        return "https://live.douyin.com/" + urllib.parse.quote(str(web_path), safe="")
    if item.get("room_id"):
        return f"https://webcast.amemv.com/douyin/webcast/reflow/{item['room_id']}"
    return ""


def public_room(item: dict) -> dict:
    history = list(histories.get(item["id"], []))
    return {
        "id": item.get("id"),
        "query": item.get("query") or "",
        "web_path": item.get("web_path") or "",
        "room_id": item.get("room_id") or "",
        "display_id": item.get("display_id") or "",
        "nickname": item.get("nickname") or "",
        "avatar": item.get("avatar") or "",
        "avatar_urls": [url for url in (item.get("avatar_urls") or []) if isinstance(url, str)][:3],
        "title": item.get("title") or "",
        "live": bool(item.get("live")),
        "status": item.get("status") or 0,
        "viewer_count": item.get("viewer_count"),
        "viewer_approximate": bool(item.get("viewer_approximate")),
        "total_user": item.get("total_user"),
        "error": item.get("error"),
        "updated_at": item.get("updated_at"),
        "added_at": item.get("added_at"),
        "link": room_link(item),
        "history": history,
    }


def state_payload() -> dict:
    with data_lock:
        return {
            "interval_seconds": interval_seconds,
            "now": time.time(),
            "last_refresh_at": last_refresh_at,
            "refreshing": refreshing,
            "max_rooms": MAX_ROOMS,
            "rooms": [public_room(item) for item in sort_rooms(rooms)],
        }


def friendly_error(error: Exception) -> str:
    if isinstance(error, DouyinLookupError):
        return error.message
    return "获取失败，请稍后再试"


def apply_snapshot(item: dict, snap: dict) -> None:
    previous_room = str(item.get("room_id") or "")
    new_room = str(snap.get("room_id") or "")
    if previous_room and new_room and previous_room != new_room:
        histories[item["id"]] = []
    for field in ("room_id", "display_id", "nickname", "avatar", "title", "web_path"):
        value = snap.get(field)
        if field == "web_path":
            if not item.get("web_path") and value:
                item["web_path"] = value
            continue
        if value not in (None, ""):
            item[field] = value
    if snap.get("avatar_urls"):
        item["avatar_urls"] = [url for url in snap["avatar_urls"] if isinstance(url, str)][:3]
    item["live"] = bool(snap.get("live"))
    item["status"] = snap.get("status") or 0
    item["viewer_count"] = snap.get("viewer_count")
    item["viewer_approximate"] = bool(snap.get("viewer_approximate"))
    item["total_user"] = snap.get("total_user")
    item["_offline_ticks"] = snap.get("_offline_ticks", item.get("_offline_ticks", 0))
    item["error"] = None
    item["updated_at"] = time.time()
    if item["live"] and isinstance(item["viewer_count"], int):
        series = histories.setdefault(item["id"], [])
        series.append({"t": item["updated_at"], "count": item["viewer_count"]})
        del series[:-HISTORY_LIMIT]


def _duplicate(snap: dict) -> bool:
    room_id = snap.get("room_id") or ""
    web_path = (snap.get("web_path") or "").lower()
    display_id = (snap.get("display_id") or "").lower()
    for item in rooms:
        if room_id and item.get("room_id") == room_id:
            return True
        existing_path = str(item.get("web_path") or "").lower()
        existing_display = str(item.get("display_id") or "").lower()
        if web_path and web_path in {existing_path, existing_display}:
            return True
        if display_id and display_id in {existing_path, existing_display}:
            return True
    return False


def add_room(query: str) -> dict:
    with data_lock:
        if len(rooms) >= MAX_ROOMS:
            raise DouyinLookupError(f"最多同时监测 {MAX_ROOMS} 个直播间，请先移出一些")
    with net_lock:
        snap = client.resolve_input(query)
    now = time.time()
    item = {
        "id": uuid.uuid4().hex,
        "query": query.strip(),
        "web_path": snap.get("web_path") or "",
        "room_id": snap.get("room_id") or "",
        "display_id": snap.get("display_id") or "",
        "nickname": snap.get("nickname") or "",
        "avatar": snap.get("avatar") or "",
        "avatar_urls": [url for url in (snap.get("avatar_urls") or []) if isinstance(url, str)][:3],
        "title": snap.get("title") or "",
        "live": bool(snap.get("live")),
        "status": snap.get("status") or 0,
        "viewer_count": snap.get("viewer_count"),
        "viewer_approximate": bool(snap.get("viewer_approximate")),
        "total_user": snap.get("total_user"),
        "error": None,
        "updated_at": now,
        "added_at": now,
        "_offline_ticks": 0 if snap.get("live") else 1,
    }
    with data_lock:
        if _duplicate(snap):
            raise DouyinLookupError("这个直播间已经在列表里")
        rooms.append(item)
        if item["live"] and isinstance(item["viewer_count"], int):
            histories[item["id"]] = [{"t": now, "count": item["viewer_count"]}]
        save_rooms()
        return public_room(item)


def remove_room(room_key: str) -> bool:
    with data_lock:
        before = len(rooms)
        rooms[:] = [item for item in rooms if item.get("id") != room_key]
        histories.pop(room_key, None)
        if len(rooms) == before:
            return False
        save_rooms()
        return True


def refresh_all() -> None:
    global last_refresh_at, refreshing
    with data_lock:
        identifiers = [item["id"] for item in rooms]
    if not identifiers:
        last_refresh_at = time.time()
        return
    refreshing = True
    try:
        for index, identifier in enumerate(identifiers):
            if stop_event.is_set():
                return
            with data_lock:
                current = next((item for item in rooms if item["id"] == identifier), None)
                if current is None:
                    continue
                previous = dict(current)
            try:
                with net_lock:
                    snap = client.fetch_snapshot(previous)
            except Exception as error:
                log(f"刷新失败 {previous.get('nickname') or previous.get('query')}: {error}")
                with data_lock:
                    current = next((item for item in rooms if item["id"] == identifier), None)
                    if current is not None:
                        current["error"] = friendly_error(error)
                        current["updated_at"] = time.time()
            else:
                with data_lock:
                    current = next((item for item in rooms if item["id"] == identifier), None)
                    if current is not None:
                        apply_snapshot(current, snap)
            if index + 1 < len(identifiers):
                time.sleep(0.2)
        with data_lock:
            save_rooms()
        last_refresh_at = time.time()
    finally:
        refreshing = False


def refresh_loop() -> None:
    while not stop_event.is_set():
        started = time.time()
        if refresh_lock.acquire(blocking=False):
            try:
                refresh_all()
            except Exception:
                traceback.print_exc()
            finally:
                refresh_lock.release()
        remaining = interval_seconds - (time.time() - started)
        deadline = time.time() + max(0.5, remaining)
        while time.time() < deadline and not stop_event.is_set():
            time.sleep(0.2)


def request_refresh() -> dict:
    acquired = refresh_lock.acquire(timeout=25)
    if not acquired:
        return state_payload()
    try:
        refresh_all()
    finally:
        refresh_lock.release()
    return state_payload()


_suggestion_cache: dict = {"at": 0.0, "rooms": []}
_suggestion_lock = threading.Lock()


def load_suggestions(fresh: bool = False) -> list[dict]:
    now = time.time()
    with _suggestion_lock:
        if not fresh and _suggestion_cache["rooms"] and now - _suggestion_cache["at"] < 45:
            return list(_suggestion_cache["rooms"])
    with net_lock:
        snaps = client.suggestions()
    public = []
    for snap in snaps:
        public.append(
            {
                "room_id": snap.get("room_id") or "",
                "display_id": snap.get("display_id") or "",
                "web_path": snap.get("web_path") or snap.get("display_id") or "",
                "nickname": snap.get("nickname") or "",
                "avatar": snap.get("avatar") or "",
                "avatar_urls": [url for url in (snap.get("avatar_urls") or []) if isinstance(url, str)][:3],
                "title": snap.get("title") or "",
                "viewer_count": snap.get("viewer_count"),
                "link": room_link(snap),
            }
        )
    with _suggestion_lock:
        _suggestion_cache["at"] = time.time()
        _suggestion_cache["rooms"] = public
    return public


class _AvatarRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        absolute = urllib.parse.urljoin(req.full_url, newurl)
        host = urllib.parse.urlparse(absolute).hostname or ""
        if urllib.parse.urlparse(absolute).scheme != "https" or not avatar_host_ok(host):
            return None
        return super().redirect_request(req, fp, code, msg, headers, absolute)


_avatar_opener = urllib.request.build_opener(_AvatarRedirect())
_avatar_cache: dict[str, tuple[float, str, bytes]] = {}
_avatar_lock = threading.Lock()


def fetch_first_avatar(urls: list[str]) -> tuple[str, bytes]:
    cleaned: list[str] = []
    for url in urls:
        if isinstance(url, str) and url.startswith("https://") and url not in cleaned:
            cleaned.append(url)
        if len(cleaned) == 3:
            break
    if not cleaned:
        raise DouyinLookupError("头像地址不在允许的范围内")
    error: DouyinLookupError | None = None
    for url in cleaned:
        try:
            return fetch_avatar(url)
        except DouyinLookupError as exc:
            error = exc
    raise error or DouyinLookupError("头像加载失败")


def fetch_avatar(url: str) -> tuple[str, bytes]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not avatar_host_ok(parsed.hostname or ""):
        raise DouyinLookupError("头像地址不在允许的范围内")
    now = time.time()
    with _avatar_lock:
        cached = _avatar_cache.get(url)
        if cached and cached[0] > now:
            return cached[1], cached[2]
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "image/avif,image/webp,image/*,*/*",
            "Referer": "https://live.douyin.com/",
        },
    )
    try:
        with _avatar_opener.open(request, timeout=5) as response:
            content_type = response.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            body = response.read(2_000_001)
    except (urllib.error.URLError, TimeoutError) as error:
        raise DouyinLookupError("头像加载失败") from error
    if not content_type.startswith("image/") or len(body) > 2_000_000 or len(body) < 32:
        raise DouyinLookupError("头像内容无效")
    with _avatar_lock:
        if len(_avatar_cache) > 80:
            _avatar_cache.clear()
        _avatar_cache[url] = (now + 300, content_type, body)
    return content_type, body


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30

    def log_message(self, fmt: str, *args) -> None:
        log("http " + (fmt % args))

    def _send(self, code: int, body: bytes, content_type: str, cache: str = "no-store") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, code: int, payload: dict | list) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, raw, "application/json; charset=utf-8")

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        if length < 0 or length > 8192:
            raise DouyinLookupError("请求内容过大")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise DouyinLookupError("请求不是有效的 JSON") from error
        if not isinstance(data, dict):
            raise DouyinLookupError("请求格式不正确")
        return data

    def do_GET(self) -> None:
        try:
            self._get()
        except DouyinLookupError as error:
            self.send_json(400, {"error": error.message})
        except Exception:
            traceback.print_exc()
            self.send_json(500, {"error": "服务内部出错"})

    def do_POST(self) -> None:
        try:
            self._post()
        except DouyinLookupError as error:
            self.send_json(400, {"error": error.message})
        except Exception:
            traceback.print_exc()
            self.send_json(500, {"error": "服务内部出错"})

    def do_DELETE(self) -> None:
        try:
            self._delete()
        except DouyinLookupError as error:
            self.send_json(400, {"error": error.message})
        except Exception:
            traceback.print_exc()
            self.send_json(500, {"error": "服务内部出错"})

    def _get(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/state":
            self.send_json(200, state_payload())
            return
        if path == "/api/suggestions":
            fresh = urllib.parse.parse_qs(parsed.query).get("fresh", ["0"])[0] == "1"
            try:
                rooms_out = load_suggestions(fresh=fresh)
            except DouyinLookupError as error:
                self.send_json(200, {"rooms": [], "error": error.message})
                return
            self.send_json(200, {"rooms": rooms_out, "error": None})
            return
        if path == "/api/avatar":
            targets = urllib.parse.parse_qs(parsed.query).get("url", [])
            content_type, body = fetch_first_avatar(targets)
            self._send(200, body, content_type, cache="public, max-age=300")
            return
        if path in ("/", "/index.html"):
            self._file(STATIC / "index.html", "text/html; charset=utf-8")
            return
        if path.startswith("/static/"):
            relative = path[len("/static/") :]
            self._static(relative)
            return
        self.send_json(404, {"error": "没有这个地址"})

    def _post(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/rooms":
            body = self.read_json()
            created = add_room(str(body.get("query") or ""))
            self.send_json(200, created)
            return
        if parsed.path == "/api/refresh":
            self.send_json(200, request_refresh())
            return
        self.send_json(404, {"error": "没有这个地址"})

    def _delete(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        prefix = "/api/rooms/"
        if not parsed.path.startswith(prefix):
            self.send_json(404, {"error": "没有这个地址"})
            return
        room_key = urllib.parse.unquote(parsed.path[len(prefix) :])
        if not remove_room(room_key):
            self.send_json(404, {"error": "列表里没有这个直播间"})
            return
        self.send_json(200, {"ok": True})

    def _static(self, relative: str) -> None:
        types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
        }
        target = (STATIC / relative).resolve()
        if not str(target).startswith(str(STATIC.resolve())) or not target.is_file():
            self.send_json(404, {"error": "没有这个文件"})
            return
        self._file(target, types.get(target.suffix, "application/octet-stream"))

    def _file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_json(404, {"error": "页面不存在"})
            return
        self._send(200, path.read_bytes(), content_type)


def run_check() -> int:
    print("正在读取公开热播列表，确认能否拿到真实在线人数……")
    try:
        with net_lock:
            snaps = client.suggestions(pages=1)
    except DouyinLookupError as error:
        print(error.message)
        print("这次没有核对成功。界面仍可手动添加直播间；若抖音无法访问，卡片会显示错误，不会编造人数。")
        return 1
    if not snaps:
        print("接口有响应，但这次没有返回正在直播的房间。")
        return 1
    print(f"拿到 {len(snaps)} 个正在直播的公开房间。前几个：")
    for snap in snaps[:5]:
        count = snap["viewer_count"]
        count_text = "未知" if count is None else str(count)
        print(f"  {snap.get('nickname') or '未命名'}  在线 {count_text}  抖音号 {snap.get('display_id') or '-'}")
    return 0


def main() -> None:
    global interval_seconds
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if sys.version_info < (3, 10):
        sys.exit("需要 Python 3.10 或更高版本")

    parser = argparse.ArgumentParser(description="抖音直播在线人数监测")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认只对本机开放")
    parser.add_argument("--port", type=int, default=8741, help="端口，默认 8741")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="刷新间隔（秒），8 到 120")
    parser.add_argument("--check", action="store_true", help="请求一次公开热播列表后退出，用来确认网络")
    args = parser.parse_args()
    if not 8 <= args.interval <= 120:
        parser.error("刷新间隔需要在 8 到 120 秒之间")
    interval_seconds = args.interval
    if args.check:
        sys.exit(run_check())

    load_rooms()
    worker = threading.Thread(target=refresh_loop, name="refresh", daemon=True)
    worker.start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    shown_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    print(f"抖音直播在线人数已启动：http://{shown_host}:{args.port}")
    print(f"名单保存在 {DATA_PATH}")
    print("按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        stop_event.set()
        server.server_close()


if __name__ == "__main__":
    main()
