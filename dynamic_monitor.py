"""Pure helpers for dynamic parsing output and persistent de-duplication."""

from datetime import datetime
from typing import Optional

TYPE_NAMES = {
    "video": "视频",
    "text": "图文/文字",
    "forward_video": "转发视频",
    "forward_text": "转发动态",
}


def format_publish_time(value) -> str:
    try:
        timestamp = float(value)
        if timestamp > 100_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError, OverflowError):
        return str(value or "未知时间")


def dynamic_identity(dynamic: dict) -> str:
    dynamic_id = dynamic.get("dynamic_id")
    if dynamic_id:
        return f"dynamic_{dynamic_id}"
    if dynamic["type"] == "video":
        return f"video_{dynamic['bvid']}"
    if dynamic["type"] == "forward_video":
        return f"forward_video_{dynamic['bvid']}"
    return f"{dynamic['type']}_{dynamic.get('dynamic_id', '')}"


def legacy_dynamic_identity(dynamic: dict) -> Optional[str]:
    if dynamic.get("type") == "video" and dynamic.get("bvid"):
        return f"video_{dynamic['bvid']}"
    if dynamic.get("type") == "forward_video" and dynamic.get("bvid"):
        return f"forward_video_{dynamic['bvid']}"
    return None


def format_dynamic_result(dynamic: dict) -> tuple[str, str, str]:
    kind = dynamic["type"]
    lines = [f"UP主：{dynamic['name']}", f"更新时间：{dynamic['pub_ts']}", f"内容：{dynamic['title']}"]
    if kind in ("forward_video", "forward_text"):
        lines.extend([f"原作者：{dynamic.get('orig_author', '未知作者')}", f"转发评论：{dynamic.get('forward_comment') or '（无）'}"])
    if kind in ("video", "forward_video"):
        link_text = dynamic["bvid"]
        url = f"https://www.bilibili.com/video/{link_text}"
    else:
        link_text = "查看动态"
        url = f"https://t.bilibili.com/{dynamic['dynamic_id']}"
    return "\n".join(lines), link_text, url


def format_dynamic(dynamic: dict) -> str:
    kind = dynamic["type"]
    lines = ["=" * 60, f"类型：{TYPE_NAMES.get(kind, kind)}", f"UP主：{dynamic['name']} (MID: {dynamic['mid']})", f"时间：{dynamic['pub_ts']}", f"内容：{dynamic['title']}"]
    if kind in ("forward_video", "forward_text"):
        lines.extend([f"转发评论：{dynamic.get('forward_comment') or '（无）'}", f"原作者：{dynamic.get('orig_author', '未知作者')}"])
    url = f"https://www.bilibili.com/video/{dynamic['bvid']}" if kind == "video" else f"https://t.bilibili.com/{dynamic['dynamic_id']}"
    lines.extend([f"链接：{url}", "=" * 60])
    return "\n".join(lines)
