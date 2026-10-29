"""Bilibili monitoring backend used by the Tkinter application."""

import json
import getpass
import os
import re
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from crash_logging import install_crash_logging

install_crash_logging()

import qrcode
import requests
import requests.utils as request_utils
from config_store import DEFAULT_CONFIG, load_config as _load_config, save_config as _save_config
from dynamic_monitor import dynamic_identity, format_dynamic, format_publish_time, legacy_dynamic_identity


ROOT_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "bili"
WWW_DIR = ROOT_DIR / "www" / "wwwroot"
COOKIE_FILE = DATA_DIR / "cookie.txt"
JSON_FILE = DATA_DIR / "jsonAll.json"
OLD_BVID_FILE = DATA_DIR / "old_bvid.json"
QR_FILE = WWW_DIR / "qr.png"
CONFIG_FILE = ROOT_DIR / "config.json"

MAX_HISTORY_IDS = 10000


class TaskCancelledError(RuntimeError):
    """Raised when a background operation is asked to stop."""


class CookieExpiredError(RuntimeError):
    """Raised when Bilibili reports that the authenticated session is invalid."""


def load_config() -> dict:
    return _load_config(CONFIG_FILE)


def save_config(config: dict) -> None:
    _save_config(CONFIG_FILE, config)


def protect_cookie_file(path: Path = COOKIE_FILE) -> None:
    """Restrict cookie access where the host OS provides a practical mechanism."""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    if os.name == "nt" and path.exists():
        username = os.environ.get("USERNAME") or getpass.getuser()
        try:
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{username}:F"],
                check=False, capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            pass


class BilibiliClient:
    """Owns an authenticated session and Bilibili API operations."""

    def __init__(
        self,
        status_callback: Optional[Callable[[str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Referer": "https://www.bilibili.com/",
            }
        )
        self.status_callback = status_callback or (lambda _message: None)
        self.cancel_event = cancel_event or threading.Event()
        self.qrcode_key = None
        self.account = None
        self._load_cookies()

    def _status(self, message: str) -> None:
        self.status_callback(message)

    def _check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise TaskCancelledError("任务已取消")

    def _load_cookies(self) -> None:
        if not COOKIE_FILE.exists():
            return
        try:
            cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
            if not isinstance(cookies, dict):
                raise ValueError("Cookie 文件必须是 JSON 对象")
            self.session.cookies.update(cookies)
            protect_cookie_file(COOKIE_FILE)
        except (OSError, json.JSONDecodeError, TypeError) as error:
            self._status(f"本地 Cookie 无法读取，将重新登录：{error}")

    def _save_cookies(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        cookies = request_utils.dict_from_cookiejar(self.session.cookies)
        COOKIE_FILE.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
        protect_cookie_file(COOKIE_FILE)

    @staticmethod
    def _api_data(response: requests.Response, label: str) -> dict:
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            message = payload.get("message") or payload.get("msg") or "未知错误"
            if payload.get("code") in (-101, -111):
                raise CookieExpiredError(f"{label}失败：登录状态已失效，请重新扫码登录")
            raise RuntimeError(f"{label}失败（{payload.get('code')}）：{message}")
        return payload.get("data") or {}

    def get_account(self) -> dict:
        self._check_cancelled()
        response = self.session.get("https://api.bilibili.com/x/space/myinfo", timeout=15)
        data = self._api_data(response, "读取登录账号")
        if not data.get("mid"):
            raise RuntimeError("B 站未返回登录账号信息，请重新登录")
        self.account = {"mid": str(data["mid"]), "name": data.get("name", "Bilibili 用户")}
        return self.account

    def is_logged_in(self) -> bool:
        try:
            self.get_account()
            return True
        except (requests.RequestException, ValueError, RuntimeError) as error:
            self._status(f"尚未登录或登录已失效：{error}")
            return False

    def create_qrcode(self) -> Path:
        self._check_cancelled()
        url = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
        response = self.session.get(url, timeout=15)
        data = self._api_data(response, "生成登录二维码")
        self.qrcode_key = data.get("qrcode_key")
        login_url = data.get("url")
        if not self.qrcode_key or not login_url:
            raise RuntimeError("B 站未返回有效的登录二维码")
        QR_FILE.parent.mkdir(parents=True, exist_ok=True)
        qrcode.make(login_url).save(QR_FILE)
        return QR_FILE

    def poll_qrcode(self) -> str:
        self._check_cancelled()
        if not self.qrcode_key:
            raise RuntimeError("尚未生成登录二维码")
        response = self.session.get(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
            params={"qrcode_key": self.qrcode_key},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        code = (payload.get("data") or {}).get("code")
        if code == 0:
            self._save_cookies()
            self.qrcode_key = None
            return "success"
        if code == 86101:
            return "waiting"
        if code == 86090:
            return "scanned"
        if code in (86038, 86039):
            return "expired"
        raise RuntimeError(payload.get("message") or payload.get("msg") or f"二维码状态异常：{payload}")

    def login(
        self,
        state_callback: Optional[Callable[[str], None]] = None,
        qr_callback: Optional[Callable[[Path], None]] = None,
        force: bool = False,
    ) -> dict:
        state_callback = state_callback or self._status
        if not force and self.is_logged_in():
            state_callback("已使用本地 Cookie 登录")
            return self.account

        if force:
            self.session.cookies.clear()
            COOKIE_FILE.unlink(missing_ok=True)

        qr_file = self.create_qrcode()
        if qr_callback:
            qr_callback(qr_file)
        state_callback(f"请使用哔哩哔哩 App 扫描窗口中的二维码（{qr_file}）")
        while True:
            if self.cancel_event.wait(2):
                raise TaskCancelledError("登录任务已取消")
            state = self.poll_qrcode()
            if state == "success":
                account = self.get_account()
                state_callback(f"登录成功：{account['name']}（MID: {account['mid']}）")
                return account
            if state == "scanned":
                state_callback("已扫码，请在手机上确认登录")
            elif state == "waiting":
                state_callback("等待扫码")
            elif state == "expired":
                self.qrcode_key = None
                qr_file = self.create_qrcode()
                if qr_callback:
                    qr_callback(qr_file)
                state_callback(f"二维码过期，已刷新（{qr_file}）")

    def get_followings(self) -> list[dict]:
        account = self.account or self.get_account()
        page = 1
        page_size = 50
        total = None
        users = []
        seen_mids = set()

        while total is None or len(users) < total:
            self._check_cancelled()
            response = self.session.get(
                "https://api.bilibili.com/x/relation/followings",
                params={"vmid": account["mid"], "pn": page, "ps": page_size, "order": "desc"},
                timeout=20,
            )
            data = self._api_data(response, "读取关注列表")
            page_users = data.get("list") or []
            if total is None:
                try:
                    total = int(data.get("total", len(page_users)))
                except (TypeError, ValueError):
                    total = len(page_users)

            fresh_users = []
            for user in page_users:
                mid = str(user.get("mid", ""))
                if mid and mid not in seen_mids:
                    seen_mids.add(mid)
                    fresh_users.append({"mid": mid, "name": user.get("uname") or "未知用户"})
            users.extend(fresh_users)
            if not page_users or not fresh_users or len(users) >= total:
                break
            page += 1
        return users

    def _fetch_dynamic_feed(self) -> dict:
        """Fetch the dynamic feed through its cursor pages."""
        base_params = {"type": "all", "features": "itemOpusStyle"}
        cursor = None
        page = 1
        all_items = []
        seen_ids = set()
        last_payload = {}

        for _ in range(20):
            self._check_cancelled()
            params = dict(base_params)
            if cursor:
                params["offset"] = cursor
            else:
                params["page"] = page
            response = self.session.get(
                "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/all",
                params=params,
                timeout=30,
            )
            data = self._api_data(response, "读取关注动态")
            last_payload = data
            items = data.get("items") or []
            fresh_items = []
            for item in items:
                item_id = str(item.get("id_str") or item.get("id") or "")
                if item_id and item_id in seen_ids:
                    continue
                if item_id:
                    seen_ids.add(item_id)
                fresh_items.append(item)
            all_items.extend(fresh_items)

            has_more = bool(data.get("has_more"))
            next_cursor = data.get("offset") or data.get("next_offset")
            if not has_more or not items:
                break
            if next_cursor and str(next_cursor) != str(cursor):
                cursor = str(next_cursor)
                continue
            if cursor is None and page < 20:
                page += 1
                continue
            break

        result = dict(last_payload)
        result["items"] = all_items
        return result

    def get_followed_dynamics(
        self,
        selected_mids: list[str],
        dynamic_types: list[str],
        on_status: Optional[Callable[[str], None]] = None,
    ) -> list[dict]:
        self._check_cancelled()
        if not selected_mids:
            return []
        status = on_status or self._status
        data = self._fetch_dynamic_feed()
        self._check_cancelled()
        payload = {"code": 0, "data": data}
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        JSON_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        selected = {str(mid) for mid in selected_mids}
        allowed_types = set(dynamic_types)
        dynamics = []
        for item in (data.get("items") or []):
            dynamic_type = item.get("type")
            if dynamic_type not in allowed_types:
                continue
            modules = item.get("modules") or {}
            author = modules.get("module_author") or {}
            author_mid = str(author.get("mid", ""))
            if author_mid not in selected:
                continue
            author_name = author.get("name") or "未知用户"
            pub_ts = format_publish_time(author.get("pub_ts"))
            module_dynamic = modules.get("module_dynamic") or {}
            item_id = str(item.get("id_str") or item.get("id") or "")

            if dynamic_type == "DYNAMIC_TYPE_AV":
                archive = ((module_dynamic.get("major") or {}).get("archive") or {})
                if archive.get("bvid"):
                    dynamics.append({
                        "type": "video", "name": author_name, "mid": author_mid,
                        "pub_ts": pub_ts, "title": archive.get("title", ""),
                        "bvid": archive["bvid"], "dynamic_id": item_id,
                    })
            elif dynamic_type == "DYNAMIC_TYPE_DRAW":
                opus = ((module_dynamic.get("major") or {}).get("opus") or {})
                summary = opus.get("summary") or {}
                content = summary.get("text", "")
                if content and item_id:
                    dynamics.append({
                        "type": "text", "name": author_name, "mid": author_mid,
                        "pub_ts": pub_ts, "title": content[:100] + ("..." if len(content) > 100 else ""),
                        "content": content, "dynamic_id": item_id,
                    })
            elif dynamic_type == "DYNAMIC_TYPE_FORWARD":
                original = item.get("orig") or {}
                original_modules = original.get("modules") or {}
                original_author = (original_modules.get("module_author") or {}).get("name", "未知作者")
                description = module_dynamic.get("desc") or {}
                comment = description.get("text", "")
                original_dynamic = original_modules.get("module_dynamic") or {}
                if original.get("type") == "DYNAMIC_TYPE_AV":
                    archive = ((original_dynamic.get("major") or {}).get("archive") or {})
                    if archive.get("bvid") and item_id:
                        dynamics.append({
                            "type": "forward_video", "name": author_name, "mid": author_mid,
                            "pub_ts": pub_ts, "title": archive.get("title", ""),
                            "forward_comment": comment[:100], "orig_author": original_author,
                            "bvid": archive["bvid"], "dynamic_id": item_id,
                        })
                elif original.get("type") == "DYNAMIC_TYPE_DRAW":
                    opus = ((original_dynamic.get("major") or {}).get("opus") or {})
                    content = (opus.get("summary") or {}).get("text", "")
                    if content and item_id:
                        dynamics.append({
                            "type": "forward_text", "name": author_name, "mid": author_mid,
                            "pub_ts": pub_ts, "title": content[:100] + ("..." if len(content) > 100 else ""),
                            "forward_comment": comment[:100], "orig_author": original_author,
                            "dynamic_id": item_id,
                        })

        try:
            raw_history = json.loads(OLD_BVID_FILE.read_text(encoding="utf-8"))
            if isinstance(raw_history, list):
                old_ids = list(dict.fromkeys(str(item) for item in raw_history if item))
            elif isinstance(raw_history, dict):
                # Be tolerant of an early experimental format that stored IDs
                # as object keys.
                old_ids = list(dict.fromkeys(str(item) for item in raw_history if item))
            else:
                old_ids = []
        except (OSError, json.JSONDecodeError, TypeError):
            old_ids = []

        old_id_set = set(old_ids)
        new_dynamics = [
            dynamic for dynamic in dynamics
            if dynamic_identity(dynamic) not in old_id_set
            and legacy_dynamic_identity(dynamic) not in old_id_set
        ]
        all_ids = [dynamic_identity(dynamic) for dynamic in dynamics]
        # Keep insertion order so the file is stable and retain IDs after they
        # leave the current feed.  A cap prevents an unattended installation
        # from growing the history file forever.
        merged_ids = list(dict.fromkeys([*old_ids, *all_ids]))[-MAX_HISTORY_IDS:]
        OLD_BVID_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_file = OLD_BVID_FILE.with_suffix(".json.tmp")
        temp_file.write_text(json.dumps(merged_ids, ensure_ascii=False), encoding="utf-8")
        temp_file.replace(OLD_BVID_FILE)
        if new_dynamics:
            status(f"发现 {len(new_dynamics)} 个新动态")
        else:
            status("本次无新增动态")
        return list(reversed(new_dynamics))


def print_dynamics(dynamics: list[dict]) -> None:
    for dynamic in dynamics:
        print(format_dynamic(dynamic))


def run_console() -> None:
    config = load_config()
    client = BilibiliClient(status_callback=print)
    try:
        account = client.login(state_callback=print, qr_callback=lambda path: print(f"二维码已保存到：{path}"))
        print(f"已登录：{account['name']}（MID: {account['mid']}）")
        followings = client.get_followings()
        print(f"已加载 {len(followings)} 个关注对象")
        selected_mids = config.get("followed_mids", [])
        if not selected_mids:
            print("未选择监控对象，请使用图形界面选择关注的 UP 主。")
            return
        while True:
            dynamics = client.get_followed_dynamics(
                selected_mids,
                config.get("followed_dynamic_types", DEFAULT_CONFIG["followed_dynamic_types"]),
                print,
            )
            if dynamics:
                print_dynamics(dynamics)
            time.sleep(max(1, int(config.get("check_interval_minutes", 1))) * 60)
    except KeyboardInterrupt:
        print("已停止监控")


def main() -> None:
    if "--console" in sys.argv:
        run_console()
        return
    from gui import run_app
    run_app(startup="--startup" in sys.argv)


if __name__ == "__main__":
    main()
