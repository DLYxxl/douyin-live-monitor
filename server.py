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
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from douyin import (
    AVERAGE_ONLINE_UNAVAILABLE,
    AVERAGE_STAY_UNAVAILABLE,
    GIFT_SEGMENTS_UNAVAILABLE,
    SESSION_VIEWERS_NOTE,
    TODAY_FOLLOWERS_UNAVAILABLE,
    VIEWER_SEGMENTS_NOTE,
    DouyinClient,
    DouyinLookupError,
    avatar_host_ok,
    clean_query,
    filter_catalog,
    query_action,
    search_note,
)

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA_PATH = ROOT / "data" / "watchlist.json"
HISTORY_PATH = ROOT / "data" / "history.json"
MAX_ROOMS = 20
DEFAULT_INTERVAL = 10
HISTORY_LIMIT = 180
MAX_DAY_SAMPLES = 1500
RETAIN_DAYS = 120

rooms: list[dict] = []
histories: dict[str, list[dict]] = {}
daily_history: dict[str, dict[str, dict]] = {}
data_lock = threading.Lock()
history_lock = threading.Lock()
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
        "detail": room_detail(item, history),
    }


def room_detail(item: dict, history: list[dict]) -> dict:
    now = time.time()
    started = item.get("started_at")
    live_seconds = None
    if item.get("live") and isinstance(started, int) and 1_000_000_000 <= started <= now + 120:
        live_seconds = max(0, int(now - started))
    counts = [
        point["count"]
        for point in history
        if isinstance(point.get("count"), int) and not point.get("approximate")
    ]
    if isinstance(item.get("viewer_count"), int) and not item.get("viewer_approximate"):
        counts.append(item["viewer_count"])
    stored_peak = item.get("observed_peak")
    peak = stored_peak if isinstance(stored_peak, int) else None
    if counts:
        peak = max(counts) if peak is None else max(peak, max(counts))
    follows = item.get("session_follows")
    likes = item.get("like_count")
    viewers = item.get("session_viewers")
    return {
        "started_at": started if isinstance(started, int) else None,
        "live_seconds": live_seconds,
        "session_follows": follows if isinstance(follows, int) else None,
        "today_followers": None,
        "today_followers_reason": TODAY_FOLLOWERS_UNAVAILABLE,
        "like_count": likes if isinstance(likes, int) else None,
        "session_viewers": viewers if isinstance(viewers, int) else None,
        "session_viewers_note": SESSION_VIEWERS_NOTE,
        "average_online": None,
        "average_online_reason": AVERAGE_ONLINE_UNAVAILABLE,
        "average_stay_seconds": None,
        "average_stay_reason": AVERAGE_STAY_UNAVAILABLE,
        "gift_segments": None,
        "gift_reason": GIFT_SEGMENTS_UNAVAILABLE,
        "viewer_segments": history,
        "viewer_segments_note": VIEWER_SEGMENTS_NOTE,
        "observed_peak": peak,
    }


def local_day(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(timestamp))


def _kept_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _thin_samples(samples: list[dict]) -> list[dict]:
    """相邻两点合成一点时留下较高的在线人数，避免抽稀把峰值丢掉。"""
    if len(samples) <= MAX_DAY_SAMPLES:
        return samples
    thinned: list[dict] = []
    index = 0
    while index < len(samples):
        if index + 1 < len(samples):
            first, second = samples[index], samples[index + 1]
            counts = [value for value in (_kept_int(first.get("viewers")), _kept_int(second.get("viewers"))) if value is not None]
            merged = dict(second)
            if counts:
                merged["viewers"] = max(counts)
            thinned.append(merged)
            index += 2
        else:
            thinned.append(samples[index])
            index += 1
    return thinned


def prune_history(store: dict[str, dict], today: date | None = None) -> None:
    anchor = today or datetime.now().astimezone().date()
    cutoff = (anchor - timedelta(days=RETAIN_DAYS)).isoformat()
    for day in list(store):
        if day < cutoff:
            del store[day]


def append_observation(store: dict[str, dict], item: dict, when: float) -> bool:
    """把一次真实读数归到本地日期。没有整数读数时不写。"""
    viewers = _kept_int(item.get("viewer_count"))
    likes = _kept_int(item.get("like_count"))
    follows = _kept_int(item.get("session_follows"))
    watched = _kept_int(item.get("session_viewers"))
    if viewers is None and likes is None and follows is None and watched is None:
        return False
    room_key = str(item.get("room_id") or item.get("display_id") or item.get("nickname") or "")
    if not room_key:
        return False
    sample = {
        "t": when,
        "viewers": viewers,
        "likes": likes,
        "follows": follows,
        "watched": watched,
        "live": bool(item.get("live")),
        "started_at": _kept_int(item.get("started_at")),
        "viewers_approximate": bool(viewers is not None and item.get("viewer_approximate")),
    }
    day = local_day(when)
    rooms_on_day = store.setdefault(day, {})
    room = rooms_on_day.get(room_key)
    if room is None:
        room = {
            "room_id": str(item.get("room_id") or ""),
            "display_id": str(item.get("display_id") or ""),
            "nickname": str(item.get("nickname") or ""),
            "web_path": str(item.get("web_path") or ""),
            "samples": [],
        }
        rooms_on_day[room_key] = room
    for field in ("room_id", "display_id", "nickname", "web_path"):
        value = item.get(field)
        if value:
            room[field] = str(value)
    samples: list[dict] = room["samples"]
    if samples:
        previous = samples[-1]
        unchanged = all(
            previous.get(key) == sample[key]
            for key in ("viewers", "likes", "follows", "watched", "live", "viewers_approximate")
        )
        if unchanged and when - float(previous.get("t") or 0) < 15:
            return False
    samples.append(sample)
    room["samples"] = _thin_samples(samples)
    prune_history(store, datetime.fromtimestamp(when).astimezone().date())
    return True


def _last_int(samples: list[dict], key: str) -> int | None:
    for sample in reversed(samples):
        value = _kept_int(sample.get(key))
        if value is not None:
            return value
    return None


def summarize_day_room(room: dict) -> dict:
    samples = [sample for sample in room.get("samples") or [] if isinstance(sample, dict)]
    viewer_points = []
    for sample in samples:
        if not isinstance(sample.get("t"), (int, float)) or not isinstance(sample.get("viewers"), int):
            continue
        point = {"t": sample["t"], "count": sample["viewers"]}
        if sample.get("viewers_approximate"):
            point["approximate"] = True
        viewer_points.append(point)
    exact_counts = [point["count"] for point in viewer_points if not point.get("approximate")]
    peak = max(exact_counts, default=None)
    recorded = None
    if len(samples) >= 2 and isinstance(samples[0].get("t"), (int, float)) and isinstance(samples[-1].get("t"), (int, float)):
        recorded = max(0, int(samples[-1]["t"] - samples[0]["t"]))
    started = _last_int(samples, "started_at")
    return {
        "room_id": room.get("room_id") or "",
        "display_id": room.get("display_id") or "",
        "nickname": room.get("nickname") or "",
        "web_path": room.get("web_path") or "",
        "samples": [
            {
                "t": sample.get("t"),
                "viewers": _kept_int(sample.get("viewers")),
                "likes": _kept_int(sample.get("likes")),
                "follows": _kept_int(sample.get("follows")),
                "watched": _kept_int(sample.get("watched")),
                "live": bool(sample.get("live")),
                "viewers_approximate": bool(sample.get("viewers_approximate")),
            }
            for sample in samples
            if isinstance(sample.get("t"), (int, float))
        ],
        "viewer_segments": viewer_points,
        "peak_viewers": peak,
        "likes": _last_int(samples, "likes"),
        "follows": _last_int(samples, "follows"),
        "watched": _last_int(samples, "watched"),
        "recorded_seconds": recorded,
        "started_at": started,
        "gifts": None,
        "gift_reason": GIFT_SEGMENTS_UNAVAILABLE,
        "average_online": None,
        "average_online_reason": AVERAGE_ONLINE_UNAVAILABLE,
        "average_stay_seconds": None,
        "average_stay_reason": AVERAGE_STAY_UNAVAILABLE,
        "today_followers": None,
        "today_followers_reason": TODAY_FOLLOWERS_UNAVAILABLE,
    }


def month_index(store: dict[str, dict], month: str) -> dict[str, dict]:
    found = {}
    prefix = month + "-"
    for day, rooms_on_day in store.items():
        if not day.startswith(prefix) or not isinstance(rooms_on_day, dict):
            continue
        samples = 0
        for room in rooms_on_day.values():
            if isinstance(room, dict):
                samples += len(room.get("samples") or [])
        found[day] = {"rooms": len(rooms_on_day), "samples": samples}
    return found


def day_view(store: dict[str, dict], day: str) -> dict:
    rooms_on_day = store.get(day) if isinstance(store.get(day), dict) else {}
    rooms_out = [summarize_day_room(room) for room in rooms_on_day.values() if isinstance(room, dict)]
    rooms_out.sort(key=lambda room: (-(room["peak_viewers"] or -1), room.get("nickname") or ""))
    return {
        "day": day,
        "rooms": rooms_out,
        "empty": not rooms_out,
        "note": "只包含这台电脑上本程序刷新到的读数。关掉程序的时段不会补。礼物、整场平均在线和人均停留没有可靠来源，所以留空。",
    }


def remember_observation(item: dict, when: float | None = None) -> None:
    moment = time.time() if when is None else when
    with history_lock:
        if not append_observation(daily_history, item, moment):
            return
        save_history_locked()


def save_history_locked() -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "days": daily_history}
    temporary = HISTORY_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(HISTORY_PATH)


def load_history() -> None:
    global daily_history
    if not HISTORY_PATH.exists():
        daily_history = {}
        return
    try:
        payload = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        loaded = payload.get("days") if isinstance(payload, dict) else None
        daily_history = loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError) as error:
        backup = HISTORY_PATH.with_suffix(".bad")
        try:
            HISTORY_PATH.replace(backup)
        except OSError:
            pass
        daily_history = {}
        log(f"日历记录无法读取，已忽略并备份：{error}")
    with history_lock:
        prune_history(daily_history)
    hydrate_today()


def hydrate_today() -> None:
    day = local_day(time.time())
    rooms_on_day = daily_history.get(day) if isinstance(daily_history.get(day), dict) else {}
    by_room = {}
    for room in rooms_on_day.values():
        if isinstance(room, dict) and room.get("room_id"):
            by_room[str(room["room_id"])] = room
    for item in rooms:
        saved = by_room.get(str(item.get("room_id") or ""))
        if not saved:
            continue
        points = []
        for sample in saved.get("samples") or []:
            if not isinstance(sample, dict) or not isinstance(sample.get("t"), (int, float)):
                continue
            if not isinstance(sample.get("viewers"), int):
                continue
            point = {"t": sample["t"], "count": sample["viewers"]}
            if sample.get("viewers_approximate"):
                point["approximate"] = True
            points.append(point)
        if points:
            histories[item["id"]] = points[-HISTORY_LIMIT:]


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
        for field in ("started_at", "session_follows", "like_count", "session_viewers", "observed_peak"):
            item.pop(field, None)
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
    for field in ("started_at", "session_follows", "like_count", "session_viewers"):
        if snap.get(field) is not None:
            item[field] = snap[field]
    if isinstance(item.get("viewer_count"), int) and not item.get("viewer_approximate"):
        previous_peak = item.get("observed_peak")
        if not isinstance(previous_peak, int) or item["viewer_count"] > previous_peak:
            item["observed_peak"] = item["viewer_count"]
    item["_offline_ticks"] = snap.get("_offline_ticks", item.get("_offline_ticks", 0))
    item["error"] = None
    item["updated_at"] = time.time()
    if item["live"] and isinstance(item["viewer_count"], int):
        series = histories.setdefault(item["id"], [])
        point = {"t": item["updated_at"], "count": item["viewer_count"]}
        if item.get("viewer_approximate"):
            point["approximate"] = True
        series.append(point)
        del series[:-HISTORY_LIMIT]
    remember_observation(item, item["updated_at"])


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
        "started_at": snap.get("started_at"),
        "session_follows": snap.get("session_follows"),
        "like_count": snap.get("like_count"),
        "session_viewers": snap.get("session_viewers"),
        "observed_peak": snap.get("viewer_count")
        if isinstance(snap.get("viewer_count"), int) and not snap.get("viewer_approximate")
        else None,
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
            point = {"t": now, "count": item["viewer_count"]}
            if item.get("viewer_approximate"):
                point["approximate"] = True
            histories[item["id"]] = [point]
        save_rooms()
        created = public_room(item)
    remember_observation(item, now)
    return created


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


_catalog_cache: dict = {"at": 0.0, "rooms": []}
_catalog_lock = threading.Lock()


def load_name_catalog(fresh: bool = False) -> list[dict]:
    now = time.time()
    with _catalog_lock:
        if not fresh and _catalog_cache["rooms"] and now - _catalog_cache["at"] < 40:
            return list(_catalog_cache["rooms"])
    with net_lock:
        snaps = client.fetch_name_catalog()
    with _catalog_lock:
        _catalog_cache["at"] = time.time()
        _catalog_cache["rooms"] = snaps
    return list(snaps)


def public_pick(snap: dict) -> dict:
    return {
        "room_id": snap.get("room_id") or "",
        "display_id": snap.get("display_id") or "",
        "web_path": snap.get("web_path") or snap.get("display_id") or "",
        "nickname": snap.get("nickname") or "",
        "avatar": snap.get("avatar") or "",
        "avatar_urls": [url for url in (snap.get("avatar_urls") or []) if isinstance(url, str)][:3],
        "title": snap.get("title") or "",
        "live": bool(snap.get("live")),
        "viewer_count": snap.get("viewer_count") if isinstance(snap.get("viewer_count"), int) else None,
        "viewer_approximate": bool(snap.get("viewer_approximate")),
        "link": room_link(snap),
    }


def search_by_name(keyword: str) -> dict:
    text = clean_query(keyword)
    action = query_action(text)
    if action == "empty":
        raise DouyinLookupError("请输入主播昵称")
    if action == "direct":
        raise DouyinLookupError("这是链接、房间号或抖音号，请直接点添加")
    if len(text) > 30:
        raise DouyinLookupError("名字太长了")
    catalog = load_name_catalog()
    if not catalog:
        raise DouyinLookupError("这次没有从公开直播信息流拿到房间")
    matched, total = filter_catalog(catalog, text)
    return {
        "query": text,
        "scanned": len(catalog),
        "total": total,
        "rooms": [public_pick(snap) for snap in matched],
        "note": search_note(len(catalog), total, len(matched)),
        "error": None,
    }


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
        if path == "/api/history":
            self._history(parsed)
            return
        if path == "/api/search":
            keyword = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
            try:
                payload = search_by_name(keyword)
            except DouyinLookupError as error:
                self.send_json(200, {"query": keyword, "rooms": [], "scanned": 0, "total": 0, "note": "", "error": error.message})
                return
            self.send_json(200, payload)
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

    def _history(self, parsed) -> None:
        query = urllib.parse.parse_qs(parsed.query)
        month = query.get("month", [""])[0]
        day = query.get("day", [""])[0]
        with history_lock:
            if len(month) == 7 and month[4] == "-" and month[:4].isdigit() and month[5:7].isdigit():
                self.send_json(
                    200,
                    {
                        "month": month,
                        "today": local_day(time.time()),
                        "days": month_index(daily_history, month),
                        "retained_days": RETAIN_DAYS,
                        "note": "按这台电脑的本地日期保存。一天里只列出本程序实际刷新到的房间。",
                    },
                )
                return
            if len(day) == 10 and day[4] == "-" and day[7] == "-" and day.replace("-", "").isdigit():
                self.send_json(200, day_view(daily_history, day))
                return
        self.send_json(400, {"error": "需要 month=YYYY-MM 或 day=YYYY-MM-DD"})

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
    load_history()
    worker = threading.Thread(target=refresh_loop, name="refresh", daemon=True)
    worker.start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    shown_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    print(f"抖音直播在线人数已启动：http://{shown_host}:{args.port}")
    print(f"名单保存在 {DATA_PATH}")
    print(f"日历记录保存在 {HISTORY_PATH}")
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
