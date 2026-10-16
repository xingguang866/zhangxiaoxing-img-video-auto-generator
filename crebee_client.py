from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import requests

from publish_platforms import classify_media


DEFAULT_CREBEE_BASE_URL = "http://127.0.0.1:3456"


class CreBeeError(RuntimeError):
    pass


def account_identifier(account: Mapping[str, Any]) -> str:
    """CreBee 接口用账号表的 account_id 定位账号（account_key 不被接受）。"""
    for key in ("account_id", "account_key", "id"):
        value = account.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def account_label(account: Mapping[str, Any]) -> str:
    name = (
        account.get("account_custom_alias")
        or account.get("account_alias")
        or account.get("account_key")
        or "未命名账号"
    )
    parts = [str(name)]
    platform = str(account.get("account_platform") or "").strip()
    status = str(account.get("account_status") or "").strip()
    if platform:
        parts.append(platform)
    if status:
        parts.append(status)
    return " · ".join(parts)


def parse_accounts(payload: Mapping[str, Any] | None) -> list[dict]:
    raw = (payload or {}).get("raw")
    items = raw.get("items") if isinstance(raw, Mapping) else None
    return [item for item in items or [] if isinstance(item, Mapping)]


def build_publish_payload(
    *,
    account: Mapping[str, Any],
    title: str,
    description: str,
    tags: Sequence[str],
    media_paths: Sequence[str | Path],
    scheduled_at: dt.datetime | None = None,
    task_id: str = "",
) -> dict:
    """把发布草稿转换成 CreBee 批量发布接口的请求体。"""
    images, videos, _unsupported = classify_media(list(media_paths))
    if not task_id:
        task_id = uuid.uuid4().hex
    timing = int(scheduled_at.timestamp()) if scheduled_at else 0
    account_id = account_identifier(account)
    platform = str(account.get("account_platform") or "").strip()
    if not account_id:
        raise CreBeeError("所选账号缺少账号标识，请刷新账号后重试。")
    if not platform:
        raise CreBeeError("所选账号缺少平台信息，请刷新账号后重试。")

    params: dict[str, Any] = {"timing": timing}
    if videos:
        video_path = str(videos[0])
        cover = str(images[0]) if images else ""
        if not cover:
            raise CreBeeError("视频发布需要封面图，请在素材列表中同时放入一张图片。")
        params.update(
            {
                "title": title,
                "desc": description,
                "videoPath": video_path,
                "coverPath": cover,
            }
        )
        common_form: dict[str, Any] = {
            "title": title,
            "desc": description,
            "videoPath": video_path,
            "coverPath": cover,
            "timing": timing,
            "taskId": task_id,
        }
        content_type = "video"
    else:
        if not images:
            raise CreBeeError("发布需要至少一张图片或一个视频。")
        image_values = [str(path) for path in images]
        params.update(
            {
                "title": title,
                "desc": description,
                "images": image_values,
                "topics": [tag for tag in tags if tag],
            }
        )
        common_form = {
            "desc": description,
            "images": image_values,
            "taskId": task_id,
        }
        content_type = "image"

    return {
        "contentType": content_type,
        "commonForm": common_form,
        "tasks": [
            {
                "taskId": task_id,
                "accountId": account_id,
                "platform": platform,
                "contentType": content_type,
                "params": params,
            }
        ],
    }


def summarize_publish_result(payload: Mapping[str, Any] | None) -> str:
    """把批量发布响应整理成一行行可读结果。"""
    payload = payload or {}
    raw = payload.get("raw") or {}
    lines = [
        f"接口返回：{payload.get('message') or '无'}（code={payload.get('code')}）",
        f"任务合计 {raw.get('total', 0)}，成功 {raw.get('success', 0)}，失败 {raw.get('failed', 0)}",
    ]
    for item in raw.get("results") or []:
        if not isinstance(item, Mapping):
            continue
        status = "成功" if item.get("status") == "success" else "失败"
        detail = item.get("error") or ""
        line = f"- [{status}] {item.get('platform')} / {item.get('accountId')}"
        if detail:
            line = f"{line}：{detail}"
        lines.append(line)
    return "\n".join(lines)


class CreBeeClient:
    """CreBee 本地服务的轻量客户端，覆盖鉴权、账号列表和批量发布。"""

    def __init__(self, base_url: str = DEFAULT_CREBEE_BASE_URL, timeout: int = 60):
        self.base_url = (base_url or DEFAULT_CREBEE_BASE_URL).strip().rstrip("/")
        self.timeout = timeout
        self._token = ""
        self._token_expires_at = dt.datetime.min

    def token(self) -> str:
        if self._token and dt.datetime.now() < self._token_expires_at:
            return self._token
        payload = self._post("/galic/v1/auth/token", authorized=False)
        token = str(payload.get("token") or "")
        if not token:
            raise CreBeeError("三方发布接口没有返回 token。")
        self._token = token
        self._token_expires_at = self._parse_expiry(payload.get("expiresAt"))
        return token

    def accounts(self) -> list[dict]:
        return parse_accounts(self._post("/galic/v1/account/getAll"))

    def publish_batch(self, payload: Mapping[str, Any]) -> dict:
        return self._post("/galic/v1/platform/publish/batch", body=payload)

    def cancel_task(self, task_id: str) -> dict:
        return self._post(
            "/galic/v1/platform/publish/cancelTask",
            body={"taskId": task_id},
        )

    @staticmethod
    def _parse_expiry(value: object) -> dt.datetime:
        text = str(value or "").strip()
        if text:
            try:
                parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                parsed = None
            if parsed is not None:
                if parsed.tzinfo is not None:
                    parsed = parsed.astimezone().replace(tzinfo=None)
                return parsed - dt.timedelta(seconds=60)
        return dt.datetime.now() + dt.timedelta(minutes=10)

    def _post(
        self,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        authorized: bool = True,
    ) -> dict:
        headers = {"Content-Type": "application/json"}
        if authorized:
            headers["Authorization"] = f"Bearer {self.token()}"
        try:
            response = requests.post(
                f"{self.base_url}{path}",
                json=dict(body) if body is not None else {},
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise CreBeeError(
                f"无法连接三方发布接口 {self.base_url}：{exc}"
            ) from exc
        if response.status_code >= 400:
            raise CreBeeError(
                f"{path} 返回 {response.status_code}：{response.text[:300]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise CreBeeError(f"{path} 返回的内容不是 JSON。") from exc
        if not isinstance(payload, dict):
            raise CreBeeError(f"{path} 返回的内容不是对象。")
        return payload
