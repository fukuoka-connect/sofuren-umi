#!/usr/bin/env python3
"""Publish one scheduled Instagram post without exposing credentials."""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
QUEUE_PATH = ROOT / "sns-posts" / "queue.json"
CONFIG_PATH = ROOT / "sns-posts" / "config.json"
REQUEST_TIMEOUT = 30


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def api_request(method: str, url: str, access_token: str, **kwargs) -> dict:
    import requests

    data = dict(kwargs.pop("data", {}) or {})
    params = dict(kwargs.pop("params", {}) or {})
    if method.upper() == "GET":
        params["access_token"] = access_token
    else:
        data["access_token"] = access_token
    response = requests.request(
        method,
        url,
        data=data,
        params=params,
        timeout=REQUEST_TIMEOUT,
        **kwargs,
    )
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Instagram API returned non-JSON: HTTP {response.status_code}") from exc
    if not response.ok or body.get("error"):
        error = body.get("error", {})
        safe_error = {
            "status": response.status_code,
            "code": error.get("code"),
            "type": error.get("type"),
            "message": error.get("message", "Instagram API request failed"),
        }
        raise RuntimeError(json.dumps(safe_error, ensure_ascii=False))
    return body


def recent_duplicate(base_url: str, user_id: str, caption: str, token: str) -> str | None:
    result = api_request(
        "GET",
        f"{base_url}/{user_id}/media",
        token,
        params={"fields": "id,caption,timestamp,permalink", "limit": "15"},
    )
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=36)
    for item in result.get("data", []):
        if item.get("caption", "").strip() != caption.strip():
            continue
        timestamp = item.get("timestamp")
        if not timestamp:
            return item.get("permalink") or item.get("id")
        published_at = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if published_at >= cutoff:
            return item.get("permalink") or item.get("id")
    return None


def main() -> int:
    queue = load_json(QUEUE_PATH)
    config = load_json(CONFIG_PATH)
    timezone = ZoneInfo(config.get("timezone", "Asia/Tokyo"))
    target_date = os.getenv("TARGET_DATE") or dt.datetime.now(timezone).date().isoformat()
    post = next((item for item in queue.get("posts", []) if item.get("date") == target_date), None)
    if not post:
        print(f"{target_date}: 投稿予定なし。終了します。")
        return 0
    if post.get("status") != "ready":
        print(f"{target_date}: status={post.get('status')} のため投稿しません。")
        return 0

    caption = f"{post.get('caption', '').strip()}\n\n{post.get('hashtags', '').strip()}".strip()
    image_url = post.get("imageUrl", "")
    if not image_url.startswith("https://") or not caption:
        raise RuntimeError("公開画像URLまたは本文が不正です")

    dry_run = bool_env("DRY_RUN", True)
    enabled = bool_env("AUTO_PUBLISH_ENABLED", False)
    if dry_run:
        print(json.dumps({
            "dryRun": True,
            "date": target_date,
            "kind": post.get("kind"),
            "subject": post.get("subject"),
            "imageUrl": image_url,
            "captionLength": len(caption),
        }, ensure_ascii=False, indent=2))
        return 0
    if not enabled:
        print(f"{target_date}: 自動投稿スイッチがOFFのため投稿しません。")
        return 0

    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
    if len(access_token) < 50:
        raise RuntimeError("INSTAGRAM_ACCESS_TOKEN が設定されていません")

    api_version = os.getenv("INSTAGRAM_API_VERSION", config.get("apiVersion", "v24.0"))
    user_id = config["instagramUserId"]
    base_url = f"https://graph.instagram.com/{api_version}"

    duplicate = recent_duplicate(base_url, user_id, caption, access_token)
    if duplicate:
        print(f"{target_date}: 同じ本文の投稿を確認したため重複投稿を防止しました。{duplicate}")
        return 0

    container = api_request(
        "POST",
        f"{base_url}/{user_id}/media",
        access_token,
        data={"image_url": image_url, "caption": caption},
    )
    creation_id = container["id"]

    for attempt in range(5):
        status = api_request(
            "GET",
            f"{base_url}/{creation_id}",
            access_token,
            params={"fields": "status_code,status"},
        )
        status_code = status.get("status_code")
        if status_code == "FINISHED":
            break
        if status_code in {"ERROR", "EXPIRED"}:
            raise RuntimeError(f"メディア準備失敗: {status_code} {status.get('status', '')}")
        if attempt < 4:
            time.sleep(60)
    else:
        raise RuntimeError("メディア準備が5分以内に完了しませんでした")

    published = api_request(
        "POST",
        f"{base_url}/{user_id}/media_publish",
        access_token,
        data={"creation_id": creation_id},
    )
    media_id = published["id"]
    details = api_request(
        "GET",
        f"{base_url}/{media_id}",
        access_token,
        params={"fields": "permalink,timestamp"},
    )
    print(f"{target_date}: Instagram投稿完了 {details.get('permalink', media_id)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Instagram自動投稿エラー: {error}", file=sys.stderr)
        raise SystemExit(1)
