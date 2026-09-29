from __future__ import annotations

import json
import os
import random
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from browser_assistant import assist_upload_xiaohongshu
from publish_drafts import PublishDraft


APP_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
QUEUE_STATE_PATH = APP_DATA / "ZhangXiaoxingGenerator" / "xiaohongshu_publish_queue.json"


@dataclass(slots=True)
class PublishQueueEntry:
    draft: PublishDraft
    scheduled_at: datetime
    status: str = "等待中"
    error: str = ""

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["scheduled_at"] = self.scheduled_at.isoformat(timespec="seconds")
        return payload


def evenly_arranged_entries(
    drafts: list[PublishDraft],
    *,
    start_at: datetime,
    min_delay_minutes: int,
    max_delay_minutes: int,
) -> list[PublishQueueEntry]:
    if max_delay_minutes < min_delay_minutes:
        raise ValueError("最大随机间隔不能小于最小随机间隔。")
    entries: list[PublishQueueEntry] = []
    cursor = start_at.replace(second=0, microsecond=0)
    for index, draft in enumerate(drafts):
        if index:
            delay_minutes = random.randint(
                min_delay_minutes,
                max_delay_minutes,
            )
            jitter_seconds = random.randint(0, 59)
            cursor += timedelta(
                minutes=delay_minutes,
                seconds=jitter_seconds,
            )
        entries.append(
            PublishQueueEntry(
                draft=draft,
                scheduled_at=cursor,
            )
        )
    return entries


def save_queue_state(entries: list[PublishQueueEntry]) -> Path:
    QUEUE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "entries": [entry.to_dict() for entry in entries],
    }
    QUEUE_STATE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return QUEUE_STATE_PATH


ProgressCallback = Callable[[int, int, str], None]


def run_publish_queue(
    entries: list[PublishQueueEntry],
    *,
    browser_name: str,
    auto_publish: bool,
    prep_lead_minutes: int,
    stop_event: threading.Event,
    progress: ProgressCallback | None = None,
) -> None:
    notify = progress or (lambda _index, _total, _message: None)
    total = len(entries)
    consecutive_failures = 0
    for index, entry in enumerate(entries, start=1):
        if stop_event.is_set():
            entry.status = "已停止"
            save_queue_state(entries)
            return
        prep_at = entry.scheduled_at - timedelta(
            minutes=max(1, prep_lead_minutes),
            seconds=random.randint(0, 90),
        )
        while datetime.now() < prep_at:
            if stop_event.wait(5):
                entry.status = "已停止"
                save_queue_state(entries)
                return
            remaining = max(0, int((prep_at - datetime.now()).total_seconds()))
            notify(
                index,
                total,
                f"{entry.draft.title or '未命名'}：等待 {remaining // 60} 分 {remaining % 60} 秒",
            )

        entry.status = "辅助发布中"
        save_queue_state(entries)
        notify(
            index,
            total,
            f"正在辅助发布：{entry.draft.title or '未命名'}",
        )
        try:
            result = assist_upload_xiaohongshu(
                media_paths=entry.draft.media_paths,
                media_type=entry.draft.media_type,
                title=entry.draft.title,
                description=entry.draft.description,
                tags=entry.draft.tags,
                keep_open=False,
                browser_name=browser_name,
                auto_publish=auto_publish,
                schedule_at=entry.scheduled_at.strftime("%Y-%m-%d %H:%M"),
                progress=lambda message, idx=index: notify(idx, total, message),
            )
            entry.status = (
                "定时发布完成"
                if result.get("published") and result.get("scheduled")
                else "已填写待人工发布"
            )
            consecutive_failures = 0
        except Exception as exc:
            entry.status = "失败"
            entry.error = str(exc)
            consecutive_failures += 1
            notify(index, total, f"失败：{entry.error}")
        save_queue_state(entries)

        if consecutive_failures >= 2:
            for pending in entries[index:]:
                if pending.status == "等待中":
                    pending.status = "已暂停"
            save_queue_state(entries)
            notify(
                index,
                total,
                "连续 2 条任务失败，队列已自动暂停，请人工检查登录状态和页面提示。",
            )
            return

        if index < total and not stop_event.is_set():
            cooldown = random.randint(15, 45)
            notify(index, total, f"任务间隔冷却 {cooldown} 秒...")
            if stop_event.wait(cooldown):
                return
    notify(total, total, "批量发布队列结束。")

