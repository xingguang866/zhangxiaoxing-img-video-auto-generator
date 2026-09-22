from __future__ import annotations

import copy
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from api_client import APIClientError, APIConfig, APIMartClient
from batch_parser import BatchItem, create_batch_template, load_batch_items
from browser_assistant import assist_upload, open_platform_page
from hypit_service import (
    HYPIT_PROJECTS,
    configure_local_profile,
    doctor as hypit_doctor,
    ensure_pnpm,
    ensure_project_dir,
    environment_report as hypit_environment_report,
    environment_summary as hypit_environment_summary,
    hypit_version,
    install_apib_provider,
    install_ffmpeg_tools,
    install_hypit_cli,
    initialize_project as hypit_initialize_project,
    launch_hypit_studio,
    login_apib_credential,
    run_hypit,
    start_hypit_process,
)
from hypit_tutorial import build_tutorial_html
from jianying_service import (
    create_jianying_draft,
    detect_jianying_versions,
    find_default_draft_dir,
    find_jianying_executable,
    launch_jianying,
)
from mock_engine import create_video_thumbnail, generate_mock_image, generate_mock_video
from pricing_utils import format_balance, format_billing, format_pricing, format_usage
from prompts import (
    STYLE_PROMPTS,
    build_cover_prompt,
    build_image_prompt,
    build_video_prompt,
    normalize_style_name,
)
from publish_platforms import PLATFORMS, PlatformPost, build_platform_posts


APP_TITLE = "张小星图文视频生成器"
IMAGE_MODELS = [
    "gpt-image-2",
    "gemini-3.1-flash-image-preview",
    "seedream-5.0-pro",
    "qwen-image-3.0",
    "z-image-turbo",
]
VIDEO_MODELS = [
    "seedance-2.0-fast",
    "seedance-2.0",
    "seedance-2.5",
    "sora-2",
    "sora-2-pro",
    "veo3.1-fast",
    "veo3.1-quality",
]
IMAGE_SIZES = ["3:4", "4:5", "9:16", "1:1", "16:9"]
IMAGE_RESOLUTIONS = ["1k", "2k", "4k"]
VIDEO_SIZES = ["9:16", "16:9", "1:1", "3:4", "adaptive"]
VIDEO_RESOLUTIONS = ["720p", "1080p", "480p", "4k"]
MODEL_CATEGORY_LABELS = {
    "image": "图片模型",
    "video": "视频模型",
    "audio": "音频模型",
    "chat": "文本模型",
}
FALLBACK_MODEL_CATALOG: dict[str, list[dict]] = {
    "image": [
        {"id": "gpt-image-2", "capability_tags": ["Text to Image", "Image to Image"]},
        {"id": "gpt-image-2.5-flare", "capability_tags": ["Text to Image", "Image to Image"]},
        {"id": "gemini-3.1-flash-image-preview", "capability_tags": ["Text to Image", "Image to Image"]},
        {"id": "gemini-3-pro-image-preview", "capability_tags": ["Text to Image", "Image to Image"]},
        {"id": "seedream-5.0-pro", "capability_tags": ["Text to Image", "Image to Image"]},
        {"id": "seedream-5.0-lite", "capability_tags": ["Text to Image", "Image to Image"]},
        {"id": "qwen-image-3.0", "capability_tags": ["Text to Image", "Image to Image"]},
        {"id": "z-image-turbo", "capability_tags": ["Text to Image"]},
        {"id": "wan2.7-image-pro", "capability_tags": ["Text to Image", "Image to Image"]},
    ],
    "video": [
        {"id": "seedance-2.0-fast", "capability_tags": ["Text to Video", "Image to Video"]},
        {"id": "seedance-2.0", "capability_tags": ["Text to Video", "Image to Video", "Video to Video"]},
        {"id": "seedance-2.5", "capability_tags": ["Text to Video", "Image to Video", "Video to Video"]},
        {"id": "sora-2", "capability_tags": ["Text to Video", "Image to Video"]},
        {"id": "sora-2-pro", "capability_tags": ["Text to Video", "Image to Video"]},
        {"id": "veo3.1-fast", "capability_tags": ["Text to Video", "Image to Video"]},
        {"id": "veo3.1-quality", "capability_tags": ["Text to Video", "Image to Video"]},
        {"id": "wan2.6", "capability_tags": ["Text to Video", "Image to Video"]},
        {"id": "kling-v3", "capability_tags": ["Text to Video", "Image to Video"]},
    ],
    "audio": [
        {"id": "whisper-1", "capability_tags": ["Audio", "Speech to Text"]},
        {"id": "tts-1", "capability_tags": ["Audio", "Text to Speech"]},
        {"id": "flow-music", "capability_tags": ["Audio", "Music"]},
        {"id": "lyria-3.5", "capability_tags": ["Audio", "Music"]},
        {"id": "suno-v6", "capability_tags": ["Audio", "Music", "Vocals"]},
    ],
    "chat": [
        {"id": "gpt-5", "capability_tags": ["Text", "Vision"]},
        {"id": "gpt-4o", "capability_tags": ["Text", "Vision"]},
        {"id": "gpt-4o-mini", "capability_tags": ["Text", "Vision"]},
        {"id": "claude-sonnet-4.5", "capability_tags": ["Text", "Vision"]},
        {"id": "claude-haiku-4.5", "capability_tags": ["Text", "Vision"]},
        {"id": "gemini-2.0-flash", "capability_tags": ["Text", "Vision"]},
        {"id": "qwen3.8-max", "capability_tags": ["Text", "Reasoning"]},
    ],
}


def model_ids(catalog: dict[str, list[dict]], category: str) -> list[str]:
    ids = [str(item.get("id", "")).strip() for item in catalog.get(category, [])]
    return [model_id for model_id in ids if model_id]


def thread_is_running(thread: QtCore.QThread | None) -> bool:
    if thread is None:
        return False
    try:
        return thread.isRunning()
    except RuntimeError:
        return False


def count_chinese_characters(value: str) -> int:
    return len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", value))


def build_manual_image_items(
    *,
    theme: str,
    page_lines: list[str],
    style: str,
    cover_title: str,
    cover_subtitle: str,
) -> list[dict]:
    resolved_cover_title = (
        cover_title.strip()
        or theme.strip()
        or (page_lines[0].strip() if page_lines else "健康生活小知识")
    )
    items: list[dict] = [
        {
            "title": f"封面_{resolved_cover_title}",
            "style": style,
            "prompt": build_cover_prompt(
                theme,
                resolved_cover_title,
                cover_subtitle,
                style,
            ),
            "size": "3:4",
            "is_cover": True,
        }
    ]
    items.extend(
        {
            "title": theme or f"图片{index}",
            "style": style,
            "prompt": build_image_prompt(theme, line, style),
            "is_cover": False,
        }
        for index, line in enumerate(page_lines, start=1)
    )
    return items


def truncate_to_chinese_limit(value: str, limit: int) -> str:
    result: list[str] = []
    chinese_count = 0
    for character in value:
        if re.match(r"[\u3400-\u4dbf\u4e00-\u9fff]", character):
            chinese_count += 1
        if chinese_count > limit:
            break
        result.append(character)
    return "".join(result).strip(" ，。！？,.!?；;")


def derive_batch_cover_text(theme: str, copy: str) -> tuple[str, str]:
    raw = (theme or copy).strip()
    if "｜" in raw:
        raw = raw.split("｜", 1)[1].strip()
    raw = re.sub(r"^[0-9０-９、.．\s]+", "", raw)
    pieces = re.split(r"[，,。！？!?；;]", raw, maxsplit=1)
    title = truncate_to_chinese_limit(pieces[0].strip(), 14)
    subtitle = pieces[1].strip() if len(pieces) > 1 else ""
    if not title:
        title = truncate_to_chinese_limit(copy.strip(), 14) or "健康生活小知识"
    return title, subtitle


def build_batch_image_jobs(item: BatchItem) -> list[dict]:
    cover_title, cover_subtitle = derive_batch_cover_text(item.theme, item.copy)
    style = normalize_style_name(item.style)
    jobs: list[dict] = [
        {
            "order": 0,
            "is_cover": True,
            "prompt": build_cover_prompt(
                item.theme,
                cover_title,
                cover_subtitle,
                style,
            ),
            "title": f"{item.theme}_封面",
            "size": "3:4",
        }
    ]
    jobs.extend(
        {
            "order": index,
            "is_cover": False,
            "prompt": prompt,
            "title": f"{item.theme}_图{index}",
        }
        for index, prompt in enumerate(item.image_prompts, start=1)
    )
    return jobs


def safe_filename(value: str, max_length: int = 40) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    value = re.sub(r"\s+", "_", value)
    return (value or "未命名")[:max_length]


def default_output_dir() -> Path:
    return Path(__file__).resolve().parent / "output"


def make_icon(color: str = "#F45D8D") -> QtGui.QIcon:
    pixmap = QtGui.QPixmap(64, 64)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setBrush(QtGui.QColor(color))
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
    painter.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF"), 5, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap))
    painter.drawLine(20, 32, 44, 32)
    painter.drawLine(32, 20, 32, 44)
    painter.end()
    return QtGui.QIcon(pixmap)


class SettingsStore:
    def __init__(self):
        self.settings = QtCore.QSettings("ZhangXiaoxing", "MediaStudio")

    def _bool(self, key: str, default: bool) -> bool:
        value = self.settings.value(key, default)
        if isinstance(value, bool):
            return value
        return str(value).lower() in {"1", "true", "yes", "on"}

    def as_dict(self) -> dict:
        base_output = Path(self.settings.value("output_dir", default_output_dir()))
        return {
            "api_key": str(self.settings.value("api_key", "")),
            "base_url": str(self.settings.value("base_url", "https://api.apib.ai/v1")),
            "output_dir": str(base_output),
            "image_output_dir": str(self.settings.value("image_output_dir", base_output / "images")),
            "video_output_dir": str(self.settings.value("video_output_dir", base_output / "videos")),
            "batch_output_dir": str(self.settings.value("batch_output_dir", base_output / "batch")),
            "mock_mode": self._bool("mock_mode", True),
        }

    def save(self, values: dict) -> None:
        for key, value in values.items():
            self.settings.setValue(key, value)
        self.settings.sync()

    def save_value(self, key: str, value: str) -> None:
        self.settings.setValue(key, value)
        self.settings.sync()


class WorkerSignals(QtCore.QObject):
    progress = QtCore.Signal(int, str)
    image_ready = QtCore.Signal(dict)
    video_ready = QtCore.Signal(dict)
    batch_status = QtCore.Signal(int, str)
    log = QtCore.Signal(str)
    error = QtCore.Signal(str)
    finished = QtCore.Signal(dict)


class GenerationWorker(QtCore.QThread):
    def __init__(self, kind: str, settings: dict, payload: dict, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.settings = copy.deepcopy(settings)
        self.payload = payload
        self.signals = WorkerSignals()
        self._cancelled = False
        self.output_root = Path(self.settings["output_dir"])
        self.output_root.mkdir(parents=True, exist_ok=True)

    def cancel(self) -> None:
        self._cancelled = True

    def _client(self) -> APIMartClient:
        return APIMartClient(
            APIConfig(
                base_url=self.settings["base_url"],
                api_key=self.settings.get("api_key", ""),
            )
        )

    def _check_cancel(self) -> None:
        if self._cancelled:
            raise APIClientError("任务已取消。")

    def _set_progress(self, current: int, total: int, message: str) -> None:
        progress = int(current / max(1, total) * 100)
        self.signals.progress.emit(progress, message)

    def _generate_real_image(
        self,
        client: APIMartClient,
        prompt: str,
        destination: Path,
        *,
        size: str | None = None,
        resolution: str | None = None,
    ) -> tuple[Path, str]:
        task_id = client.submit_image(
            prompt=prompt,
            model=self.settings["image_model"],
            size=size or self.settings["image_size"],
            resolution=resolution or self.settings["image_resolution"],
        )
        self.signals.log.emit(f"图片任务已提交：{task_id}")
        task = client.wait_task(
            task_id,
            interval=4,
            timeout=3600,
            on_update=lambda progress, message: self.signals.progress.emit(progress, message),
            is_cancelled=lambda: self._cancelled,
        )
        urls = client.extract_result_urls(task)
        if not urls:
            raise APIClientError(f"图片任务完成但未返回图片 URL：{task}")
        download_path = destination.with_suffix(Path(urls[0]).suffix or ".png")
        client.download_file(urls[0], download_path)
        return download_path, urls[0]

    def _generate_mock_image(
        self,
        prompt: str,
        style: str,
        destination: Path,
        page_number: int,
    ) -> tuple[Path, str]:
        path = generate_mock_image(
            prompt,
            style,
            destination.with_suffix(".png"),
            page_number=page_number,
        )
        return path, ""

    def _generate_real_video(
        self,
        client: APIMartClient,
        *,
        prompt: str,
        image_paths: list[Path],
        image_urls: list[str],
        destination: Path,
    ) -> tuple[Path, str, Path | None]:
        remote_urls = list(image_urls)
        if image_paths:
            remote_urls = []
            for index, path in enumerate(image_paths, start=1):
                self._check_cancel()
                self.signals.log.emit(f"上传参考图片 {index}/{len(image_paths)}：{path.name}")
                remote_urls.append(client.upload_image(path))

        task_id = client.submit_video(
            prompt=prompt,
            model=self.settings["video_model"],
            image_urls=remote_urls or None,
            size=self.settings["video_size"],
            resolution=self.settings["video_resolution"],
            duration=self.settings["video_duration"],
            generate_audio=self.settings["generate_audio"],
        )
        self.signals.log.emit(f"视频任务已提交：{task_id}")
        task = client.wait_task(
            task_id,
            interval=5,
            timeout=5400,
            on_update=lambda progress, message: self.signals.progress.emit(progress, message),
            is_cancelled=lambda: self._cancelled,
        )
        urls = client.extract_result_urls(task)
        if not urls:
            raise APIClientError(f"视频任务完成但未返回视频 URL：{task}")
        download_path = destination.with_suffix(".mp4")
        client.download_file(urls[0], download_path)
        thumbnail = None
        try:
            thumbnail = create_video_thumbnail(download_path, download_path.with_name(download_path.stem + "_thumb.jpg"))
        except Exception:
            thumbnail = None
        return download_path, urls[0], thumbnail

    def _generate_mock_video(
        self,
        image_paths: list[Path],
        destination: Path,
    ) -> tuple[Path, str, Path | None]:
        output = generate_mock_video(
            image_paths,
            destination.with_suffix(".mp4"),
            duration=self.settings["video_duration"],
        )
        thumbnail = None
        try:
            thumbnail = create_video_thumbnail(output, output.with_name(output.stem + "_thumb.jpg"))
        except Exception:
            thumbnail = None
        return output, "", thumbnail

    def _run_manual_images(self) -> dict:
        items = self.payload["items"]
        client = None if self.settings["mock_mode"] else self._client()
        output_dir = Path(self.payload["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        generated = 0

        for index, item in enumerate(items, start=1):
            self._check_cancel()
            self._set_progress(index - 1, len(items), f"生成图片 {index}/{len(items)}")
            destination = output_dir / f"{index:02d}_{safe_filename(item['title'])}"
            if self.settings["mock_mode"]:
                path, remote_url = self._generate_mock_image(
                    item["prompt"],
                    item["style"],
                    destination,
                    index,
                )
            else:
                path, remote_url = self._generate_real_image(
                    client,
                    item["prompt"],
                    destination,
                    size=item.get("size"),
                    resolution=item.get("resolution"),
                )
            generated += 1
            self.signals.image_ready.emit(
                {
                    "path": str(path),
                    "remote_url": remote_url,
                    "prompt": item["prompt"],
                    "title": item["title"],
                    "page": index,
                }
            )
        self._set_progress(len(items), len(items), "图片生成完成")
        return {"generated": generated, "output_dir": str(output_dir)}

    def _run_manual_videos(self) -> dict:
        items = self.payload["items"]
        client = None if self.settings["mock_mode"] else self._client()
        output_dir = Path(self.payload["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        generated = 0

        for index, item in enumerate(items, start=1):
            self._check_cancel()
            image_paths = [Path(path) for path in item["image_paths"]]
            image_urls = list(item.get("image_urls", []))
            if not self.settings["mock_mode"] and not image_paths and not image_urls:
                raise APIClientError("缺少可用的参考图片。")
            self._set_progress(index - 1, len(items), f"生成视频 {index}/{len(items)}")
            destination = output_dir / f"{index:02d}_{safe_filename(item['title'])}"
            if self.settings["mock_mode"]:
                path, remote_url, thumbnail = self._generate_mock_video(image_paths, destination)
            else:
                path, remote_url, thumbnail = self._generate_real_video(
                    client,
                    prompt=item["prompt"],
                    image_paths=image_paths,
                    image_urls=image_urls,
                    destination=destination,
                )
            generated += 1
            self.signals.video_ready.emit(
                {
                    "path": str(path),
                    "remote_url": remote_url,
                    "thumbnail": str(thumbnail) if thumbnail else "",
                    "prompt": item["prompt"],
                    "title": item["title"],
                    "page": index,
                }
            )
        self._set_progress(len(items), len(items), "视频生成完成")
        return {"generated": generated, "output_dir": str(output_dir)}

    def _run_batch_images(self) -> dict:
        items: list[BatchItem] = self.payload["items"]
        client = None if self.settings["mock_mode"] else self._client()
        batch_dir = Path(self.payload["output_dir"])
        item_jobs = [(item, build_batch_image_jobs(item)) for item in items]
        total = sum(len(jobs) for _, jobs in item_jobs)
        current = 0
        generated = 0

        for item, jobs in item_jobs:
            self._check_cancel()
            item_dir = batch_dir / f"{item.index:02d}_{safe_filename(item.theme)}"
            item_dir.mkdir(parents=True, exist_ok=True)
            self.signals.batch_status.emit(item.index, f"生成图片中（含封面，共{len(jobs)}张）")

            for job in jobs:
                self._check_cancel()
                order = int(job["order"])
                prompt = str(job["prompt"])
                is_cover = bool(job.get("is_cover"))
                label = "封面" if is_cover else "image"
                self._set_progress(
                    current,
                    total,
                    f"{item.theme} · {'封面' if is_cover else f'图{order}'}",
                )
                destination = item_dir / f"{order:02d}_{label}"
                style = normalize_style_name(item.style or self.settings["style"])
                if self.settings["mock_mode"]:
                    path, remote_url = self._generate_mock_image(
                        prompt,
                        style,
                        destination,
                        1 if is_cover else order,
                    )
                else:
                    path, remote_url = self._generate_real_image(
                        client,
                        prompt,
                        destination,
                        size=job.get("size"),
                        resolution=job.get("resolution"),
                    )
                item.image_paths.append(path)
                if remote_url:
                    item.image_urls.append(remote_url)
                generated += 1
                current += 1
                self.signals.image_ready.emit(
                    {
                        "batch_index": item.index,
                        "path": str(path),
                        "remote_url": remote_url,
                        "prompt": prompt,
                        "title": job["title"],
                        "page": order,
                        "is_cover": is_cover,
                    }
                )

            self.signals.batch_status.emit(item.index, f"图片完成 {len(item.image_paths)} 张")

        self._set_progress(total, total, "批量图片完成")
        return {
            "generated": generated,
            "output_dir": str(batch_dir),
            "items": items,
        }

    def _run_batch_videos(self) -> dict:
        items: list[BatchItem] = self.payload["items"]
        client = None if self.settings["mock_mode"] else self._client()
        batch_dir = Path(self.payload["output_dir"])
        merge_mode = self.payload.get("merge_mode", "each")
        jobs: list[tuple[BatchItem, list[Path], str]] = []

        for item in items:
            if not item.image_paths:
                continue
            if merge_mode == "merge":
                prompt = build_video_prompt(
                    item.theme,
                    item.copy,
                    item.video_prompt,
                    duration=self.settings["video_duration"],
                )
                jobs.append((item, item.image_paths, prompt))
            else:
                for image_path in item.image_paths:
                    prompt = build_video_prompt(
                        item.theme,
                        item.copy,
                        item.video_prompt,
                        duration=self.settings["video_duration"],
                    )
                    jobs.append((item, [image_path], prompt))

        generated = 0
        for index, (item, image_paths, prompt) in enumerate(jobs, start=1):
            self._check_cancel()
            item_dir = batch_dir / f"{item.index:02d}_{safe_filename(item.theme)}"
            item_dir.mkdir(parents=True, exist_ok=True)
            self.signals.batch_status.emit(item.index, f"生成视频 {index}/{len(jobs)}")
            self._set_progress(index - 1, len(jobs), f"{item.theme} · 视频")
            destination = item_dir / f"video_{index:02d}"
            if self.settings["mock_mode"]:
                path, _, thumbnail = self._generate_mock_video(image_paths, destination)
            else:
                path, _, thumbnail = self._generate_real_video(
                    client,
                    prompt=prompt,
                    image_paths=image_paths,
                    image_urls=[],
                    destination=destination,
                )
            item.video_paths.append(path)
            generated += 1
            self.signals.video_ready.emit(
                {
                    "batch_index": item.index,
                    "path": str(path),
                    "remote_url": "",
                    "thumbnail": str(thumbnail) if thumbnail else "",
                    "prompt": prompt,
                    "title": item.theme,
                    "page": index,
                }
            )

        self._set_progress(len(jobs), len(jobs), "批量视频完成")
        return {
            "generated": generated,
            "output_dir": str(batch_dir),
            "items": items,
        }

    def run(self) -> None:
        try:
            if self.kind == "manual_images":
                summary = self._run_manual_images()
            elif self.kind == "manual_videos":
                summary = self._run_manual_videos()
            elif self.kind == "batch_images":
                summary = self._run_batch_images()
            elif self.kind == "batch_videos":
                summary = self._run_batch_videos()
            else:
                raise APIClientError(f"未知任务类型：{self.kind}")
            self.signals.finished.emit(summary)
        except APIClientError as exc:
            self.signals.error.emit(str(exc))
            self.signals.finished.emit({"error": str(exc)})
        except Exception as exc:
            self.signals.error.emit(f"发生未处理错误：{exc}")
            self.signals.finished.emit({"error": str(exc)})


class MediaGallery(QtWidgets.QFrame):
    def __init__(self, title: str = "预览", parent=None):
        super().__init__(parent)
        self.setObjectName("previewCard")
        self.items: list[dict] = []
        self.current_index = -1

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setObjectName("sectionTitle")
        self.count_label = QtWidgets.QLabel("0 个结果")
        self.count_label.setObjectName("mutedLabel")
        header.addWidget(self.title_label)
        header.addStretch(1)
        header.addWidget(self.count_label)
        layout.addLayout(header)

        self.preview = QtWidgets.QLabel("生成结果会在这里显示")
        self.preview.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(540, 460)
        self.preview.setObjectName("previewArea")
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview, 1)

        self.path_label = QtWidgets.QLabel("等待生成")
        self.path_label.setObjectName("mutedLabel")
        self.path_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.path_label)

        thumbs_scroll = QtWidgets.QScrollArea()
        thumbs_scroll.setWidgetResizable(True)
        thumbs_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        thumbs_scroll.setFixedHeight(118)
        self.thumb_container = QtWidgets.QWidget()
        self.thumb_layout = QtWidgets.QHBoxLayout(self.thumb_container)
        self.thumb_layout.setContentsMargins(0, 0, 0, 0)
        self.thumb_layout.setSpacing(8)
        self.thumb_layout.addStretch(1)
        thumbs_scroll.setWidget(self.thumb_container)
        layout.addWidget(thumbs_scroll)

        footer = QtWidgets.QHBoxLayout()
        self.prev_button = QtWidgets.QPushButton("上一张")
        self.next_button = QtWidgets.QPushButton("下一张")
        self.open_button = QtWidgets.QPushButton("打开文件")
        self.open_dir_button = QtWidgets.QPushButton("打开目录")
        for button in (self.prev_button, self.next_button, self.open_button, self.open_dir_button):
            button.setObjectName("secondaryButton")
        self.prev_button.clicked.connect(lambda: self.step(-1))
        self.next_button.clicked.connect(lambda: self.step(1))
        self.open_button.clicked.connect(self.open_current)
        self.open_dir_button.clicked.connect(self.open_directory)
        footer.addWidget(self.prev_button)
        footer.addWidget(self.next_button)
        footer.addStretch(1)
        footer.addWidget(self.open_button)
        footer.addWidget(self.open_dir_button)
        layout.addLayout(footer)
        self._update_buttons()

    def _clear_thumbnails(self) -> None:
        while self.thumb_layout.count():
            item = self.thumb_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def set_items(self, items: list[dict]) -> None:
        self.items = items
        self.current_index = 0 if items else -1
        self._rebuild_thumbnails()
        self._show_current()

    def add_item(self, item: dict) -> None:
        self.items.append(item)
        self.current_index = len(self.items) - 1
        self._rebuild_thumbnails()
        self._show_current()

    def _rebuild_thumbnails(self) -> None:
        self._clear_thumbnails()
        for index, item in enumerate(self.items):
            button = QtWidgets.QToolButton()
            button.setObjectName("thumbButton")
            button.setFixedSize(88, 88)
            button.setIconSize(QtCore.QSize(76, 76))
            button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
            preview_path = item.get("thumbnail") or item.get("path")
            icon = QtGui.QIcon(str(preview_path))
            if not icon.isNull():
                button.setIcon(icon)
            else:
                button.setText(str(index + 1))
            button.setToolTip(item.get("title") or item.get("prompt", "")[:80])
            button.clicked.connect(lambda checked=False, idx=index: self.select_index(idx))
            self.thumb_layout.addWidget(button)
        self.thumb_layout.addStretch(1)
        self.count_label.setText(f"{len(self.items)} 个结果")

    def select_index(self, index: int) -> None:
        if 0 <= index < len(self.items):
            self.current_index = index
            self._show_current()

    def step(self, delta: int) -> None:
        if not self.items:
            return
        self.current_index = (self.current_index + delta) % len(self.items)
        self._show_current()

    def _show_current(self) -> None:
        self._update_buttons()
        if not self.items or self.current_index < 0:
            self.preview.setPixmap(QtGui.QPixmap())
            self.preview.setText("生成结果会在这里显示")
            self.path_label.setText("等待生成")
            return

        item = self.items[self.current_index]
        preview_path = item.get("thumbnail") or item.get("path")
        pixmap = QtGui.QPixmap(str(preview_path))
        if not pixmap.isNull():
            available = self.preview.size() - QtCore.QSize(24, 24)
            self.preview.setPixmap(
                pixmap.scaled(
                    available,
                    QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.SmoothTransformation,
                )
            )
            self.preview.setText("")
        else:
            is_video = str(item.get("path", "")).lower().endswith((".mp4", ".mov"))
            self.preview.setPixmap(QtGui.QPixmap())
            self.preview.setText("视频已生成\n点击“打开文件”播放" if is_video else "无法预览此文件")
        title = item.get("title") or f"第 {self.current_index + 1} 张"
        self.path_label.setText(f"{title}  ·  {item.get('path', '')}")

    def _update_buttons(self) -> None:
        enabled = bool(self.items)
        self.prev_button.setEnabled(enabled)
        self.next_button.setEnabled(enabled)
        self.open_button.setEnabled(enabled)
        self.open_dir_button.setEnabled(enabled)

    def open_current(self) -> None:
        if not self.items or self.current_index < 0:
            return
        path = Path(self.items[self.current_index].get("path", ""))
        if path.exists():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def open_directory(self) -> None:
        if not self.items or self.current_index < 0:
            return
        path = Path(self.items[self.current_index].get("path", ""))
        if path.exists():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path.parent)))


def card_frame() -> QtWidgets.QFrame:
    frame = QtWidgets.QFrame()
    frame.setObjectName("sideCard")
    return frame


def scrollable_side_card(card: QtWidgets.QWidget, width: int = 414) -> QtWidgets.QScrollArea:
    scroll = QtWidgets.QScrollArea()
    scroll.setObjectName("sideScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setFixedWidth(width)
    scroll.setWidget(card)
    return scroll


def section_label(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    label.setObjectName("sectionTitle")
    return label


def hint_label(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    label.setObjectName("mutedLabel")
    label.setWordWrap(True)
    return label


class BaseGenerationPage(QtWidgets.QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.worker: GenerationWorker | None = None

    def current_settings(self) -> dict:
        return self.main_window.settings_store.as_dict()

    def start_worker(self, kind: str, settings: dict, payload: dict) -> None:
        if self.worker and self.worker.isRunning():
            QtWidgets.QMessageBox.warning(self, "任务进行中", "请先等待当前任务完成或点击停止。")
            return
        self.worker = GenerationWorker(kind, settings, payload, self)
        self.connect_worker(self.worker)
        self.worker.start()

    def connect_worker(self, worker: GenerationWorker) -> None:
        worker.signals.error.connect(lambda message: QtWidgets.QMessageBox.critical(self, "任务失败", message))
        worker.signals.finished.connect(lambda summary: self.on_finished(summary))

    def on_finished(self, summary: dict) -> None:
        pass

    def stop_worker(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()


class ImagePage(BaseGenerationPage):
    def __init__(self, main_window, parent=None):
        super().__init__(main_window, parent)
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        side = card_frame()
        side.setMinimumWidth(360)
        side_layout = QtWidgets.QVBoxLayout(side)
        side_layout.setContentsMargins(18, 18, 18, 18)
        side_layout.setSpacing(12)

        side_layout.addWidget(section_label("图文描述"))
        side_layout.addWidget(hint_label("输入主题和每张图文案。每行代表一张图片。"))

        self.theme_edit = QtWidgets.QLineEdit()
        self.theme_edit.setPlaceholderText("主题，例如：久坐党如厕后的温和清洁")
        side_layout.addWidget(self.theme_edit)

        self.copy_edit = QtWidgets.QPlainTextEdit()
        self.copy_edit.setPlaceholderText(
            "每行一张图文案，例如：\n"
            "1、如厕时不要把手机带进去\n"
            "2、便后清洁要减少反复摩擦\n"
            "3、久坐一小时起身活动\n"
            "4、出现持续不适及时就医"
        )
        self.copy_edit.setFixedHeight(180)
        side_layout.addWidget(self.copy_edit)

        side_layout.addWidget(section_label("封面图生成"))
        cover_form = QtWidgets.QFormLayout()
        cover_form.setSpacing(10)
        self.cover_title_edit = QtWidgets.QLineEdit()
        self.cover_title_edit.setPlaceholderText("封面主标题，例如：久坐党别忽略这件事")
        self.cover_title_edit.setMaxLength(40)
        self.cover_subtitle_edit = QtWidgets.QLineEdit()
        self.cover_subtitle_edit.setPlaceholderText("封面副标题，可选")
        self.cover_counter_label = QtWidgets.QLabel("0/14")
        self.cover_counter_label.setObjectName("counterLabel")
        self.cover_counter_label.setMinimumWidth(54)
        self.cover_counter_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        cover_title_row = QtWidgets.QWidget()
        cover_title_layout = QtWidgets.QHBoxLayout(cover_title_row)
        cover_title_layout.setContentsMargins(0, 0, 0, 0)
        cover_title_layout.setSpacing(8)
        cover_title_layout.addWidget(self.cover_title_edit, 1)
        cover_title_layout.addWidget(self.cover_counter_label)
        cover_form.addRow("主标题", cover_title_row)
        cover_form.addRow("副标题", self.cover_subtitle_edit)
        side_layout.addLayout(cover_form)
        self.cover_title_edit.textChanged.connect(self.update_cover_counter)
        self.update_cover_counter("")
        side_layout.addWidget(
            hint_label("建议主标题控制在 14 个字以内，封面会优先保证大字和远距离可读性。")
        )

        form = QtWidgets.QFormLayout()
        form.setSpacing(10)
        self.style_combo = QtWidgets.QComboBox()
        self.style_combo.addItems(list(STYLE_PROMPTS.keys()))
        self.style_combo.setCurrentText("手绘卡通")
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems(IMAGE_MODELS)
        self.model_combo.setCurrentText(IMAGE_MODELS[0])
        self.size_combo = QtWidgets.QComboBox()
        self.size_combo.addItems(IMAGE_SIZES)
        self.resolution_combo = QtWidgets.QComboBox()
        self.resolution_combo.addItems(IMAGE_RESOLUTIONS)
        form.addRow("图片风格", self.style_combo)
        form.addRow("图片模型", self.model_combo)
        form.addRow("画面比例", self.size_combo)
        form.addRow("清晰度", self.resolution_combo)
        side_layout.addLayout(form)

        output_row = QtWidgets.QHBoxLayout()
        self.output_edit = QtWidgets.QLineEdit(
            self.current_settings()["image_output_dir"]
        )
        browse_output = QtWidgets.QPushButton("输出路径")
        browse_output.setObjectName("secondaryButton")
        browse_output.clicked.connect(self.browse_output_dir)
        self.output_edit.editingFinished.connect(self.save_output_dir)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(browse_output)
        side_layout.addLayout(output_row)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        side_layout.addWidget(self.progress)

        self.status_label = hint_label("准备就绪")
        side_layout.addWidget(self.status_label)

        self.generate_button = QtWidgets.QPushButton("一键生成全部图文")
        self.generate_button.setObjectName("primaryButton")
        self.generate_button.setMinimumHeight(48)
        self.generate_button.clicked.connect(self.generate)
        side_layout.addWidget(self.generate_button)

        self.cover_button = QtWidgets.QPushButton("生成封面图")
        self.cover_button.setObjectName("secondaryButton")
        self.cover_button.setMinimumHeight(42)
        self.cover_button.clicked.connect(self.generate_cover)
        side_layout.addWidget(self.cover_button)

        self.stop_button = QtWidgets.QPushButton("停止生成")
        self.stop_button.setObjectName("secondaryButton")
        self.stop_button.clicked.connect(self.stop_worker)
        side_layout.addWidget(self.stop_button)
        side_layout.addStretch(1)

        self.gallery = MediaGallery("图片预览")
        root.addWidget(scrollable_side_card(side))
        root.addWidget(self.gallery, 1)

    def set_models(self, models: list[str]) -> None:
        current = self.model_combo.currentText().strip()
        target = current if current in models else (models[0] if models else current or IMAGE_MODELS[0])
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        self.model_combo.setCurrentText(target)
        self.model_combo.blockSignals(False)

    def connect_worker(self, worker: GenerationWorker) -> None:
        super().connect_worker(worker)
        worker.signals.progress.connect(self._on_progress)
        worker.signals.image_ready.connect(self.gallery.add_item)
        worker.signals.log.connect(self.status_label.setText)

    def _on_progress(self, value: int, message: str) -> None:
        self.progress.setValue(value)
        self.status_label.setText(message)

    def browse_output_dir(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "选择图文输出目录",
            self.output_edit.text(),
        )
        if path:
            self.output_edit.setText(path)
            self.save_output_dir()

    def save_output_dir(self) -> None:
        self.main_window.settings_store.save_value(
            "image_output_dir",
            self.output_edit.text().strip(),
        )

    def update_cover_counter(self, text: str) -> None:
        count = count_chinese_characters(text)
        self.cover_counter_label.setText(f"{count}/14")
        color = "#D64B73" if count > 14 else "#3E8767"
        self.cover_counter_label.setStyleSheet(f"color: {color}; font-weight: 700;")

    def generate_cover(self) -> None:
        theme = self.theme_edit.text().strip()
        title = self.cover_title_edit.text().strip() or theme
        subtitle = self.cover_subtitle_edit.text().strip()
        if not title:
            QtWidgets.QMessageBox.warning(
                self,
                "缺少封面标题",
                "请填写封面主标题，或先在主题中填写主题。",
            )
            return
        chinese_count = count_chinese_characters(title)
        if chinese_count > 14:
            answer = QtWidgets.QMessageBox.question(
                self,
                "标题可能过长",
                f"当前主标题包含 {chinese_count} 个汉字，超过建议的 14 个。\n"
                "汉字过多时，模型可能自动缩小字号。是否仍要继续生成？",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return

        settings = self.current_settings()
        settings.update(
            {
                "image_model": self.model_combo.currentText().strip(),
                "image_size": "3:4",
                "image_resolution": self.resolution_combo.currentText(),
            }
        )
        style = self.style_combo.currentText()
        prompt = build_cover_prompt(theme, title, subtitle, style)
        item = {
            "title": f"封面_{title}",
            "style": style,
            "prompt": prompt,
        }
        self.save_output_dir()
        output_base = Path(self.output_edit.text().strip() or settings["image_output_dir"])
        output_dir = output_base / f"封面_{datetime.now():%Y%m%d_%H%M%S}"
        self.progress.setValue(0)
        self.start_worker(
            "manual_images",
            settings,
            {"items": [item], "output_dir": str(output_dir)},
        )

    def generate(self) -> None:
        theme = self.theme_edit.text().strip()
        lines = [line.strip() for line in self.copy_edit.toPlainText().splitlines() if line.strip()]
        if not lines:
            QtWidgets.QMessageBox.warning(self, "缺少文案", "请至少填写一行图片文案。")
            return
        settings = self.current_settings()
        settings.update(
            {
                "image_model": self.model_combo.currentText().strip(),
                "image_size": self.size_combo.currentText(),
                "image_resolution": self.resolution_combo.currentText(),
            }
        )
        style = self.style_combo.currentText()
        items = build_manual_image_items(
            theme=theme,
            page_lines=lines,
            style=style,
            cover_title=self.cover_title_edit.text().strip(),
            cover_subtitle=self.cover_subtitle_edit.text().strip(),
        )
        self.save_output_dir()
        output_base = Path(self.output_edit.text().strip() or settings["image_output_dir"])
        output_dir = output_base / f"图文_{datetime.now():%Y%m%d_%H%M%S}"
        self.gallery.set_items([])
        self.progress.setValue(0)
        self.start_worker("manual_images", settings, {"items": items, "output_dir": str(output_dir)})

    def on_finished(self, summary: dict) -> None:
        if "error" in summary:
            self.status_label.setText(f"任务失败：{summary['error']}")
            return
        self.status_label.setText(
            f"完成，共生成 {summary.get('generated', 0)} 张图片（包含封面图）"
        )


class VideoPage(BaseGenerationPage):
    def __init__(self, main_window, parent=None):
        super().__init__(main_window, parent)
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        side = card_frame()
        side.setMinimumWidth(360)
        side_layout = QtWidgets.QVBoxLayout(side)
        side_layout.setContentsMargins(18, 18, 18, 18)
        side_layout.setSpacing(12)
        side_layout.addWidget(section_label("视频生成"))
        side_layout.addWidget(hint_label("添加一张或多张图片，填写视频提示词，生成对应短视频。"))

        self.video_theme = QtWidgets.QLineEdit()
        self.video_theme.setPlaceholderText("视频主题，例如：便后清洁动作演示")
        side_layout.addWidget(self.video_theme)

        self.prompt_edit = QtWidgets.QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("视频提示词，例如：镜头缓慢推进，纸张和图标依次出现")
        self.prompt_edit.setFixedHeight(130)
        side_layout.addWidget(self.prompt_edit)

        image_header = QtWidgets.QHBoxLayout()
        image_header.addWidget(section_label("参考图片"))
        image_header.addStretch(1)
        self.image_count_label = hint_label("0 张")
        image_header.addWidget(self.image_count_label)
        side_layout.addLayout(image_header)

        self.image_list = QtWidgets.QListWidget()
        self.image_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.image_list.setFixedHeight(140)
        side_layout.addWidget(self.image_list)

        image_buttons = QtWidgets.QHBoxLayout()
        add_images = QtWidgets.QPushButton("添加图片")
        add_folder = QtWidgets.QPushButton("添加文件夹")
        clear_images = QtWidgets.QPushButton("清空")
        for button in (add_images, add_folder, clear_images):
            button.setObjectName("secondaryButton")
        add_images.clicked.connect(self.add_images)
        add_folder.clicked.connect(self.add_folder)
        clear_images.clicked.connect(self.clear_images)
        image_buttons.addWidget(add_images)
        image_buttons.addWidget(add_folder)
        image_buttons.addWidget(clear_images)
        side_layout.addLayout(image_buttons)

        form = QtWidgets.QFormLayout()
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems(VIDEO_MODELS)
        self.model_combo.setCurrentText(VIDEO_MODELS[0])
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["每张图片各生成一条", "多张图片合成一条"])
        self.size_combo = QtWidgets.QComboBox()
        self.size_combo.addItems(VIDEO_SIZES)
        self.resolution_combo = QtWidgets.QComboBox()
        self.resolution_combo.addItems(VIDEO_RESOLUTIONS)
        self.duration_combo = QtWidgets.QComboBox()
        self.duration_combo.addItems([str(value) for value in (4, 5, 6, 8, 10, 12, 15)])
        self.duration_combo.setCurrentText("5")
        self.audio_check = QtWidgets.QCheckBox("生成有声视频")
        self.audio_check.setChecked(False)
        form.addRow("视频模型", self.model_combo)
        form.addRow("生成方式", self.mode_combo)
        form.addRow("画面比例", self.size_combo)
        form.addRow("清晰度", self.resolution_combo)
        form.addRow("视频时长", self.duration_combo)
        form.addRow("音频", self.audio_check)
        side_layout.addLayout(form)

        output_row = QtWidgets.QHBoxLayout()
        self.output_edit = QtWidgets.QLineEdit(
            self.current_settings()["video_output_dir"]
        )
        browse_output = QtWidgets.QPushButton("输出路径")
        browse_output.setObjectName("secondaryButton")
        browse_output.clicked.connect(self.browse_output_dir)
        self.output_edit.editingFinished.connect(self.save_output_dir)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(browse_output)
        side_layout.addLayout(output_row)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        side_layout.addWidget(self.progress)
        self.status_label = hint_label("准备就绪")
        side_layout.addWidget(self.status_label)

        self.generate_button = QtWidgets.QPushButton("一键生成视频")
        self.generate_button.setObjectName("primaryButton")
        self.generate_button.setMinimumHeight(48)
        self.generate_button.clicked.connect(self.generate)
        side_layout.addWidget(self.generate_button)
        self.stop_button = QtWidgets.QPushButton("停止生成")
        self.stop_button.setObjectName("secondaryButton")
        self.stop_button.clicked.connect(self.stop_worker)
        side_layout.addWidget(self.stop_button)
        side_layout.addStretch(1)

        self.gallery = MediaGallery("视频预览")
        root.addWidget(scrollable_side_card(side))
        root.addWidget(self.gallery, 1)

    def set_models(self, models: list[str]) -> None:
        current = self.model_combo.currentText().strip()
        target = current if current in models else (models[0] if models else current or VIDEO_MODELS[0])
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        self.model_combo.setCurrentText(target)
        self.model_combo.blockSignals(False)

    def add_images(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "选择参考图片",
            str(Path.home()),
            "图片 (*.png *.jpg *.jpeg *.webp *.gif *.bmp)",
        )
        for path in paths:
            if not self.image_list.findItems(path, QtCore.Qt.MatchFlag.MatchExactly):
                self.image_list.addItem(path)
        self._update_image_count()

    def add_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "选择图片文件夹", str(Path.home()))
        if not folder:
            return
        extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
        for path in sorted(Path(folder).iterdir()):
            if path.suffix.lower() in extensions and not self.image_list.findItems(str(path), QtCore.Qt.MatchFlag.MatchExactly):
                self.image_list.addItem(str(path))
        self._update_image_count()

    def clear_images(self) -> None:
        self.image_list.clear()
        self._update_image_count()

    def _update_image_count(self) -> None:
        self.image_count_label.setText(f"{self.image_list.count()} 张")

    def connect_worker(self, worker: GenerationWorker) -> None:
        super().connect_worker(worker)
        worker.signals.progress.connect(self._on_progress)
        worker.signals.video_ready.connect(self.gallery.add_item)
        worker.signals.log.connect(self.status_label.setText)

    def _on_progress(self, value: int, message: str) -> None:
        self.progress.setValue(value)
        self.status_label.setText(message)

    def browse_output_dir(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "选择视频输出目录",
            self.output_edit.text(),
        )
        if path:
            self.output_edit.setText(path)
            self.save_output_dir()

    def save_output_dir(self) -> None:
        self.main_window.settings_store.save_value(
            "video_output_dir",
            self.output_edit.text().strip(),
        )

    def generate(self) -> None:
        paths = [Path(self.image_list.item(index).text()) for index in range(self.image_list.count())]
        if not paths:
            QtWidgets.QMessageBox.warning(self, "缺少图片", "请至少添加一张参考图片。")
            return
        settings = self.current_settings()
        settings.update(
            {
                "video_model": self.model_combo.currentText().strip(),
                "video_size": self.size_combo.currentText(),
                "video_resolution": self.resolution_combo.currentText(),
                "video_duration": int(self.duration_combo.currentText()),
                "generate_audio": self.audio_check.isChecked(),
            }
        )
        theme = self.video_theme.text().strip() or paths[0].stem
        prompt = build_video_prompt(
            theme,
            "参考图片内容",
            self.prompt_edit.toPlainText(),
            duration=settings["video_duration"],
        )
        if self.mode_combo.currentIndex() == 0:
            items = [
                {
                    "title": f"{theme}_{index + 1}",
                    "prompt": prompt,
                    "image_paths": [str(path)],
                    "image_urls": [],
                }
                for index, path in enumerate(paths)
            ]
        else:
            items = [
                {
                    "title": theme,
                    "prompt": prompt,
                    "image_paths": [str(path) for path in paths],
                    "image_urls": [],
                }
            ]
        self.save_output_dir()
        output_base = Path(self.output_edit.text().strip() or settings["video_output_dir"])
        output_dir = output_base / f"视频_{datetime.now():%Y%m%d_%H%M%S}"
        self.gallery.set_items([])
        self.progress.setValue(0)
        self.start_worker("manual_videos", settings, {"items": items, "output_dir": str(output_dir)})

    def on_finished(self, summary: dict) -> None:
        if "error" in summary:
            self.status_label.setText(f"任务失败：{summary['error']}")
            return
        self.status_label.setText(f"完成，共生成 {summary.get('generated', 0)} 条视频")


class BatchPage(BaseGenerationPage):
    def __init__(self, main_window, parent=None):
        super().__init__(main_window, parent)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        toolbar_card = card_frame()
        toolbar = QtWidgets.QHBoxLayout(toolbar_card)
        toolbar.setContentsMargins(16, 12, 16, 12)
        self.import_button = QtWidgets.QPushButton("导入 Excel / CSV")
        self.template_button = QtWidgets.QPushButton("下载导入模板")
        self.open_output_button = QtWidgets.QPushButton("打开输出目录")
        self.clear_button = QtWidgets.QPushButton("清空列表")
        for button in (self.import_button, self.template_button, self.open_output_button, self.clear_button):
            button.setObjectName("secondaryButton")
        self.import_button.clicked.connect(self.import_file)
        self.template_button.clicked.connect(self.download_template)
        self.open_output_button.clicked.connect(self.open_output_dir)
        self.clear_button.clicked.connect(self.clear_items)
        toolbar.addWidget(self.import_button)
        toolbar.addWidget(self.template_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.open_output_button)
        toolbar.addWidget(self.clear_button)
        root.addWidget(toolbar_card)

        controls_card = card_frame()
        controls_layout = QtWidgets.QVBoxLayout(controls_card)
        controls_layout.setContentsMargins(16, 12, 16, 12)
        controls_layout.setSpacing(10)
        controls = QtWidgets.QHBoxLayout()
        self.style_combo = QtWidgets.QComboBox()
        self.style_combo.addItems(list(STYLE_PROMPTS.keys()))
        self.style_combo.setCurrentText("手绘卡通")
        self.image_model_combo = QtWidgets.QComboBox()
        self.image_model_combo.setEditable(True)
        self.image_model_combo.addItems(IMAGE_MODELS)
        self.video_model_combo = QtWidgets.QComboBox()
        self.video_model_combo.setEditable(True)
        self.video_model_combo.addItems(VIDEO_MODELS)
        self.video_mode_combo = QtWidgets.QComboBox()
        self.video_mode_combo.addItems(["每张图片各生成一条", "每组素材合成一条"])
        self.generate_images_button = QtWidgets.QPushButton("批量生成图片")
        self.generate_images_button.setObjectName("primaryButton")
        self.generate_videos_button = QtWidgets.QPushButton("批量生成视频")
        self.generate_videos_button.setObjectName("primaryButton")
        self.stop_button = QtWidgets.QPushButton("停止")
        self.stop_button.setObjectName("secondaryButton")
        self.generate_images_button.clicked.connect(self.generate_images)
        self.generate_videos_button.clicked.connect(self.generate_videos)
        self.stop_button.clicked.connect(self.stop_worker)

        controls.addWidget(QtWidgets.QLabel("风格"))
        controls.addWidget(self.style_combo)
        controls.addWidget(QtWidgets.QLabel("图片模型"))
        controls.addWidget(self.image_model_combo, 1)
        controls.addWidget(QtWidgets.QLabel("视频模型"))
        controls.addWidget(self.video_model_combo, 1)
        controls.addWidget(self.video_mode_combo)
        controls.addWidget(self.generate_images_button)
        controls.addWidget(self.generate_videos_button)
        controls.addWidget(self.stop_button)
        controls_layout.addLayout(controls)

        output_row = QtWidgets.QHBoxLayout()
        self.output_edit = QtWidgets.QLineEdit(
            self.current_settings()["batch_output_dir"]
        )
        browse_output = QtWidgets.QPushButton("批量输出路径")
        browse_output.setObjectName("secondaryButton")
        browse_output.clicked.connect(self.browse_output_dir)
        self.output_edit.editingFinished.connect(self.save_output_dir)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(browse_output)
        controls_layout.addLayout(output_row)
        root.addWidget(controls_card)

        content = QtWidgets.QHBoxLayout()
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["序号", "主题", "图片数量", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        content.addWidget(self.table, 2)

        log_card = card_frame()
        log_layout = QtWidgets.QVBoxLayout(log_card)
        log_layout.setContentsMargins(14, 14, 14, 14)
        log_layout.addWidget(section_label("运行日志"))
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        log_layout.addWidget(self.progress)
        self.log_edit = QtWidgets.QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        log_layout.addWidget(self.log_edit, 1)
        content.addWidget(log_card, 1)
        root.addLayout(content, 1)

        self.items: list[BatchItem] = []

    def set_models(self, image_models: list[str], video_models: list[str]) -> None:
        current_image = self.image_model_combo.currentText().strip()
        current_video = self.video_model_combo.currentText().strip()
        target_image = (
            current_image
            if current_image in image_models
            else (image_models[0] if image_models else current_image or IMAGE_MODELS[0])
        )
        target_video = (
            current_video
            if current_video in video_models
            else (video_models[0] if video_models else current_video or VIDEO_MODELS[0])
        )
        self.image_model_combo.blockSignals(True)
        self.video_model_combo.blockSignals(True)
        self.image_model_combo.clear()
        self.image_model_combo.addItems(image_models)
        self.video_model_combo.clear()
        self.video_model_combo.addItems(video_models)
        self.image_model_combo.setCurrentText(target_image)
        self.video_model_combo.setCurrentText(target_video)
        self.image_model_combo.blockSignals(False)
        self.video_model_combo.blockSignals(False)

    def connect_worker(self, worker: GenerationWorker) -> None:
        super().connect_worker(worker)
        worker.signals.progress.connect(self._on_progress)
        worker.signals.image_ready.connect(self._on_image_ready)
        worker.signals.video_ready.connect(self._on_video_ready)
        worker.signals.batch_status.connect(self._on_batch_status)
        worker.signals.log.connect(self.append_log)

    def append_log(self, message: str) -> None:
        self.log_edit.appendPlainText(f"[{datetime.now():%H:%M:%S}] {message}")

    def _on_progress(self, value: int, message: str) -> None:
        self.progress.setValue(value)
        self.append_log(message)

    def _on_batch_status(self, index: int, status: str) -> None:
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).text() == str(index):
                self.table.item(row, 3).setText(status)
                break
        for item in self.items:
            if item.index == index:
                item.status = status
                break

    def _on_image_ready(self, item: dict) -> None:
        index = item.get("batch_index")
        if not index:
            return
        for batch_item in self.items:
            if batch_item.index == index:
                path = Path(item["path"])
                if path not in batch_item.image_paths:
                    batch_item.image_paths.append(path)
                remote_url = item.get("remote_url", "")
                if remote_url:
                    batch_item.image_urls.append(remote_url)
                self._update_image_count(index, len(batch_item.image_paths))
                break

    def _on_video_ready(self, item: dict) -> None:
        index = item.get("batch_index")
        if not index:
            return
        for batch_item in self.items:
            if batch_item.index == index:
                path = Path(item["path"])
                if path not in batch_item.video_paths:
                    batch_item.video_paths.append(path)
                break

    def _update_image_count(self, index: int, count: int) -> None:
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).text() == str(index):
                self.table.item(row, 2).setText(str(count))
                break

    def refresh_table(self) -> None:
        self.table.setRowCount(len(self.items))
        for row, item in enumerate(self.items):
            values = [str(item.index), item.theme, str(len(item.image_paths)), item.status]
            for column, value in enumerate(values):
                cell = QtWidgets.QTableWidgetItem(value)
                if column in {0, 2, 3}:
                    cell.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, cell)

    def import_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "导入批量文案",
            str(Path(__file__).resolve().parent.parent),
            "表格文件 (*.xlsx *.xlsm *.csv)",
        )
        if not path:
            return
        try:
            self.items = load_batch_items(path, default_style=self.style_combo.currentText())
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "导入失败", str(exc))
            return
        self.refresh_table()
        self.append_log(f"已导入 {len(self.items)} 行：{path}")

    def download_template(self) -> None:
        default_name = str(Path.home() / "张小星批量生成模板.xlsx")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "保存模板", default_name, "Excel 文件 (*.xlsx)")
        if not path:
            return
        try:
            create_batch_template(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "创建模板失败", str(exc))
            return
        self.append_log(f"模板已保存：{path}")

    def browse_output_dir(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "选择批量输出目录",
            self.output_edit.text(),
        )
        if path:
            self.output_edit.setText(path)
            self.save_output_dir()

    def save_output_dir(self) -> None:
        self.main_window.settings_store.save_value(
            "batch_output_dir",
            self.output_edit.text().strip(),
        )

    def open_output_dir(self) -> None:
        path = Path(self.output_edit.text().strip() or self.current_settings()["batch_output_dir"])
        path.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def clear_items(self) -> None:
        self.items = []
        self.table.setRowCount(0)
        self.log_edit.clear()

    def generate_images(self) -> None:
        if not self.items:
            QtWidgets.QMessageBox.warning(self, "没有数据", "请先导入 Excel 或 CSV。")
            return
        settings = self.current_settings()
        settings.update(
            {
                "style": self.style_combo.currentText(),
                "image_model": self.image_model_combo.currentText().strip(),
                "image_size": "3:4",
                "image_resolution": "1k",
            }
        )
        items = [copy.deepcopy(item) for item in self.items]
        self.save_output_dir()
        output_base = Path(self.output_edit.text().strip() or settings["batch_output_dir"])
        output_dir = output_base / f"批量图片_{datetime.now():%Y%m%d_%H%M%S}"
        self.progress.setValue(0)
        self.start_worker("batch_images", settings, {"items": items, "output_dir": str(output_dir)})

    def generate_videos(self) -> None:
        if not self.items:
            QtWidgets.QMessageBox.warning(self, "没有数据", "请先导入 Excel 或 CSV。")
            return
        if not any(item.image_paths for item in self.items):
            QtWidgets.QMessageBox.warning(self, "没有图片", "请先批量生成图片，或确保当前列表已关联图片。")
            return
        settings = self.current_settings()
        settings.update(
            {
                "video_model": self.video_model_combo.currentText().strip(),
                "video_size": "9:16",
                "video_resolution": "720p",
                "video_duration": 5,
                "generate_audio": False,
            }
        )
        items = [copy.deepcopy(item) for item in self.items]
        self.save_output_dir()
        output_base = Path(self.output_edit.text().strip() or settings["batch_output_dir"])
        output_dir = output_base / f"批量视频_{datetime.now():%Y%m%d_%H%M%S}"
        self.progress.setValue(0)
        self.start_worker(
            "batch_videos",
            settings,
            {
                "items": items,
                "output_dir": str(output_dir),
                "merge_mode": "merge" if self.video_mode_combo.currentIndex() == 1 else "each",
            },
        )

    def on_finished(self, summary: dict) -> None:
        if "error" in summary:
            self.append_log(f"任务结束：{summary['error']}")
            return
        if isinstance(summary.get("items"), list):
            self.items = summary["items"]
            self.refresh_table()
        self.append_log(
            f"任务完成，共输出 {summary.get('generated', 0)} 个文件。输出目录：{summary.get('output_dir', '')}"
        )


class JianyingDraftThread(QtCore.QThread):
    result = QtCore.Signal(str)
    error = QtCore.Signal(str)

    def __init__(self, params: dict, parent=None):
        super().__init__(parent)
        self.params = params

    def run(self) -> None:
        try:
            path = create_jianying_draft(**self.params)
            self.result.emit(str(path))
        except Exception as exc:
            self.error.emit(str(exc))


class JianyingPage(QtWidgets.QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.draft_thread: JianyingDraftThread | None = None
        self.media_items: list[dict] = []

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        side = card_frame()
        side.setMinimumWidth(360)
        side_layout = QtWidgets.QVBoxLayout(side)
        side_layout.setContentsMargins(18, 18, 18, 18)
        side_layout.setSpacing(12)

        side_layout.addWidget(section_label("剪映草稿"))
        side_layout.addWidget(
            hint_label(
                "上传或载入软件生成的图片、视频，按分镜文案生成剪映草稿。"
                "当前已按剪映 6.0.1 兼容模式生成。"
            )
        )

        self.draft_name_edit = QtWidgets.QLineEdit(f"张小星草稿_{datetime.now():%Y%m%d_%H%M%S}")
        side_layout.addWidget(self.draft_name_edit)

        draft_dir_row = QtWidgets.QHBoxLayout()
        self.draft_dir_edit = QtWidgets.QLineEdit(str(find_default_draft_dir()))
        browse_draft_button = QtWidgets.QPushButton("选择草稿目录")
        browse_draft_button.setObjectName("secondaryButton")
        browse_draft_button.clicked.connect(self.browse_draft_dir)
        draft_dir_row.addWidget(self.draft_dir_edit, 1)
        draft_dir_row.addWidget(browse_draft_button)
        side_layout.addLayout(draft_dir_row)

        detected_versions = detect_jianying_versions()
        default_executable = find_jianying_executable()
        executable_row = QtWidgets.QHBoxLayout()
        self.jianying_exe_edit = QtWidgets.QLineEdit(
            str(default_executable) if default_executable else ""
        )
        browse_exe_button = QtWidgets.QPushButton("选择剪映")
        browse_exe_button.setObjectName("secondaryButton")
        browse_exe_button.clicked.connect(self.browse_jianying_executable)
        executable_row.addWidget(self.jianying_exe_edit, 1)
        executable_row.addWidget(browse_exe_button)
        side_layout.addLayout(executable_row)
        version_text = "、".join(
            f"{version} ({path})" for path, version in detected_versions
        ) or "未自动检测到剪映程序"
        side_layout.addWidget(hint_label(f"检测到的剪映：{version_text}"))

        self.prompt_edit = QtWidgets.QPlainTextEdit()
        self.prompt_edit.setPlaceholderText(
            "字幕/分镜提示词，每行对应一个素材，例如：\n"
            "第一页：如厕时不要把手机带进去\n"
            "第二页：便后清洁减少反复摩擦\n"
            "第三页：久坐后起身活动"
        )
        self.prompt_edit.setFixedHeight(140)
        side_layout.addWidget(self.prompt_edit)

        media_header = QtWidgets.QHBoxLayout()
        media_header.addWidget(section_label("图片与视频素材"))
        media_header.addStretch(1)
        self.media_count_label = hint_label("0 个素材")
        media_header.addWidget(self.media_count_label)
        side_layout.addLayout(media_header)

        self.media_list = QtWidgets.QListWidget()
        self.media_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.media_list.setFixedHeight(150)
        side_layout.addWidget(self.media_list)

        media_buttons = QtWidgets.QGridLayout()
        add_media_button = QtWidgets.QPushButton("上传图片/视频")
        add_folder_button = QtWidgets.QPushButton("添加文件夹")
        load_latest_button = QtWidgets.QPushButton("载入最近生成")
        remove_button = QtWidgets.QPushButton("移除选中")
        clear_button = QtWidgets.QPushButton("清空")
        for button in (
            add_media_button,
            add_folder_button,
            load_latest_button,
            remove_button,
            clear_button,
        ):
            button.setObjectName("secondaryButton")
        add_media_button.clicked.connect(self.add_media)
        add_folder_button.clicked.connect(self.add_folder)
        load_latest_button.clicked.connect(self.load_latest_outputs)
        remove_button.clicked.connect(self.remove_selected)
        clear_button.clicked.connect(self.clear_media)
        media_buttons.addWidget(add_media_button, 0, 0)
        media_buttons.addWidget(add_folder_button, 0, 1)
        media_buttons.addWidget(load_latest_button, 1, 0)
        media_buttons.addWidget(remove_button, 1, 1)
        media_buttons.addWidget(clear_button, 2, 0, 1, 2)
        side_layout.addLayout(media_buttons)

        form = QtWidgets.QFormLayout()
        self.ratio_combo = QtWidgets.QComboBox()
        self.ratio_combo.addItems(["9:16 竖版", "16:9 横版", "1:1 方形"])
        self.image_duration_spin = QtWidgets.QDoubleSpinBox()
        self.image_duration_spin.setRange(0.5, 30.0)
        self.image_duration_spin.setSingleStep(0.5)
        self.image_duration_spin.setValue(3.0)
        self.image_duration_spin.setSuffix(" 秒")
        self.video_duration_spin = QtWidgets.QDoubleSpinBox()
        self.video_duration_spin.setRange(0.5, 120.0)
        self.video_duration_spin.setSingleStep(0.5)
        self.video_duration_spin.setValue(15.0)
        self.video_duration_spin.setSuffix(" 秒")
        self.fps_combo = QtWidgets.QComboBox()
        self.fps_combo.addItems(["30", "25", "24", "60"])
        self.auto_launch_check = QtWidgets.QCheckBox("生成后启动剪映")
        self.auto_launch_check.setChecked(False)
        form.addRow("画布比例", self.ratio_combo)
        form.addRow("图片时长", self.image_duration_spin)
        form.addRow("视频最长使用", self.video_duration_spin)
        form.addRow("帧率", self.fps_combo)
        form.addRow("", self.auto_launch_check)
        side_layout.addLayout(form)

        self.status_label = hint_label("准备就绪")
        side_layout.addWidget(self.status_label)

        generate_button = QtWidgets.QPushButton("生成剪映草稿")
        generate_button.setObjectName("primaryButton")
        generate_button.setMinimumHeight(48)
        generate_button.clicked.connect(self.generate_draft)
        side_layout.addWidget(generate_button)

        footer_buttons = QtWidgets.QHBoxLayout()
        open_folder_button = QtWidgets.QPushButton("打开草稿目录")
        open_app_button = QtWidgets.QPushButton("启动剪映")
        for button in (open_folder_button, open_app_button):
            button.setObjectName("secondaryButton")
        open_folder_button.clicked.connect(self.open_draft_folder)
        open_app_button.clicked.connect(self.open_jianying)
        footer_buttons.addWidget(open_folder_button)
        footer_buttons.addWidget(open_app_button)
        side_layout.addLayout(footer_buttons)
        side_layout.addStretch(1)

        self.gallery = MediaGallery("草稿素材预览")
        root.addWidget(scrollable_side_card(side))
        root.addWidget(self.gallery, 1)

    def browse_draft_dir(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "选择剪映草稿目录",
            self.draft_dir_edit.text(),
        )
        if path:
            self.draft_dir_edit.setText(path)

    def browse_jianying_executable(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择 JianyingPro.exe",
            self.jianying_exe_edit.text() or str(Path.home()),
            "剪映程序 (JianyingPro.exe);;可执行文件 (*.exe)",
        )
        if path:
            self.jianying_exe_edit.setText(path)

    def _thumbnail_for(self, path: Path) -> str:
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
            return str(path)
        thumb = path.with_name(path.stem + "_thumb.jpg")
        if thumb.exists():
            return str(thumb)
        generated = path.with_name(path.stem + "_draft_thumb.jpg")
        try:
            create_video_thumbnail(path, generated)
            return str(generated)
        except Exception:
            return ""

    def add_paths(self, paths: list[str | Path]) -> None:
        existing = {Path(self.media_list.item(index).text()).resolve() for index in range(self.media_list.count())}
        for raw_path in paths:
            path = Path(raw_path)
            if not path.exists() or path.resolve() in existing:
                continue
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".mp4", ".mov", ".avi"}:
                continue
            self.media_list.addItem(str(path))
            existing.add(path.resolve())
            self.media_items.append(
                {
                    "title": path.stem,
                    "path": str(path),
                    "thumbnail": self._thumbnail_for(path),
                }
            )
        self.media_count_label.setText(f"{self.media_list.count()} 个素材")
        self.gallery.set_items(self.media_items)

    def add_media(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "选择图片或视频",
            str(Path.home()),
            "媒体文件 (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.mp4 *.mov *.avi)",
        )
        self.add_paths(paths)

    def add_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "选择素材文件夹", str(Path.home()))
        if folder:
            extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".mp4", ".mov", ".avi"}
            paths = [path for path in sorted(Path(folder).iterdir()) if path.suffix.lower() in extensions]
            self.add_paths(paths)

    def load_latest_outputs(self) -> None:
        output_dir = Path(self.main_window.settings_store.as_dict()["output_dir"])
        extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".mp4", ".mov"}
        files = [
            path
            for path in output_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in extensions and "drafts" not in path.parts
        ]
        files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        self.add_paths(files[:30])
        self.status_label.setText(f"已载入最近生成的 {min(30, len(files))} 个媒体文件")

    def remove_selected(self) -> None:
        rows = sorted({index.row() for index in self.media_list.selectedIndexes()}, reverse=True)
        for row in rows:
            self.media_list.takeItem(row)
            if 0 <= row < len(self.media_items):
                self.media_items.pop(row)
        self.media_count_label.setText(f"{self.media_list.count()} 个素材")
        self.gallery.set_items(self.media_items)

    def clear_media(self) -> None:
        self.media_list.clear()
        self.media_items = []
        self.media_count_label.setText("0 个素材")
        self.gallery.set_items([])

    def _canvas_size(self) -> tuple[int, int]:
        ratio = self.ratio_combo.currentText()
        if ratio.startswith("16:9"):
            return 1920, 1080
        if ratio.startswith("1:1"):
            return 1080, 1080
        return 1080, 1920

    def generate_draft(self) -> None:
        paths = [Path(self.media_list.item(index).text()) for index in range(self.media_list.count())]
        if not paths:
            QtWidgets.QMessageBox.warning(self, "缺少素材", "请先上传或载入图片、视频素材。")
            return
        if thread_is_running(self.draft_thread):
            QtWidgets.QMessageBox.information(self, "正在生成", "剪映草稿正在生成，请稍候。")
            return
        width, height = self._canvas_size()
        params = {
            "draft_root": self.draft_dir_edit.text().strip(),
            "draft_name": self.draft_name_edit.text().strip() or f"张小星草稿_{datetime.now():%Y%m%d_%H%M%S}",
            "media_paths": [str(path) for path in paths],
            "prompt_text": self.prompt_edit.toPlainText(),
            "image_duration": self.image_duration_spin.value(),
            "video_max_duration": self.video_duration_spin.value(),
            "width": width,
            "height": height,
            "fps": int(self.fps_combo.currentText()),
        }
        self.status_label.setText("正在生成剪映草稿...")
        self.draft_thread = JianyingDraftThread(params, self)
        self.draft_thread.result.connect(self._on_draft_ready)
        self.draft_thread.error.connect(self._on_draft_error)
        self.draft_thread.finished.connect(
            lambda thread=self.draft_thread: self._clear_draft_thread(thread)
        )
        self.draft_thread.start()

    def _clear_draft_thread(self, thread: JianyingDraftThread) -> None:
        if self.draft_thread is thread:
            self.draft_thread = None

    def _on_draft_ready(self, path: str) -> None:
        self.status_label.setText(f"草稿已生成：{path}")
        if self.auto_launch_check.isChecked():
            self.open_jianying()
        else:
            QtWidgets.QMessageBox.information(
                self,
                "剪映草稿已生成",
                f"草稿目录：{path}\n\n"
                "如果剪映当前已经打开，请重启剪映后再进入草稿列表。",
            )

    def _on_draft_error(self, message: str) -> None:
        self.status_label.setText(f"生成失败：{message}")
        QtWidgets.QMessageBox.critical(self, "生成剪映草稿失败", message)

    def open_draft_folder(self) -> None:
        path = Path(self.draft_dir_edit.text().strip())
        path.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def open_jianying(self) -> None:
        executable = Path(self.jianying_exe_edit.text().strip()) if self.jianying_exe_edit.text().strip() else None
        if executable and not executable.exists():
            executable = None
        if not executable:
            executable = find_jianying_executable()
        if executable:
            launch_jianying(executable)
            self.status_label.setText(f"已启动剪映：{executable}")
        else:
            QtWidgets.QMessageBox.warning(
                self,
                "未找到剪映",
                "未在常见安装目录找到 JianyingPro.exe，请手动启动剪映。",
            )


class HypitCommandThread(QtCore.QThread):
    line = QtCore.Signal(str)
    error = QtCore.Signal(str)
    finished = QtCore.Signal(int)

    def __init__(self, args: list[str], cwd: str, parent=None):
        super().__init__(parent)
        self.args = args
        self.cwd = cwd
        self.process = None

    def cancel(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def run(self) -> None:
        try:
            self.process = start_hypit_process(self.args, cwd=self.cwd)
            if self.process.stdout:
                for line in self.process.stdout:
                    self.line.emit(line.rstrip())
            code = self.process.wait()
            self.finished.emit(code)
        except Exception as exc:
            self.error.emit(str(exc))
            self.finished.emit(-1)


class HypitSetupThread(QtCore.QThread):
    line = QtCore.Signal(str)
    error = QtCore.Signal(str)
    finished = QtCore.Signal(bool)

    def run(self) -> None:
        try:
            self.line.emit("检查 pnpm...")
            pnpm = ensure_pnpm()
            self.line.emit(f"pnpm: {pnpm}")
            self.line.emit("检查 FFmpeg / FFprobe...")
            ffmpeg, ffprobe = install_ffmpeg_tools()
            self.line.emit(f"ffmpeg: {ffmpeg}")
            self.line.emit(f"ffprobe: {ffprobe}")
            self.line.emit("安装 Hypit CLI 0.2.12...")
            hypit = install_hypit_cli()
            self.line.emit(f"Hypit: {hypit}")
            self.finished.emit(True)
        except Exception as exc:
            self.error.emit(str(exc))
            self.finished.emit(False)


class HypitInitializeThread(QtCore.QThread):
    line = QtCore.Signal(str)
    error = QtCore.Signal(str)
    finished = QtCore.Signal(bool)

    def __init__(self, project_dir: str, parent=None):
        super().__init__(parent)
        self.project_dir = project_dir

    def run(self) -> None:
        try:
            result = hypit_initialize_project(self.project_dir)
            if result.stdout:
                for line in result.stdout.splitlines():
                    self.line.emit(line)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "Runtime 初始化失败。")
            profile = configure_local_profile(self.project_dir)
            self.line.emit(f"已配置本地 Edge、FFmpeg 和 FFprobe：{profile}")
            self.finished.emit(True)
        except Exception as exc:
            self.error.emit(str(exc))
            self.finished.emit(False)


class HypitApibSetupThread(QtCore.QThread):
    line = QtCore.Signal(str)
    error = QtCore.Signal(str)
    finished = QtCore.Signal(bool)

    def __init__(self, project_dir: str, api_key: str, parent=None):
        super().__init__(parent)
        self.project_dir = project_dir
        self.api_key = api_key

    def run(self) -> None:
        try:
            self.line.emit("安装 APIB Provider 项目包...")
            destination = install_apib_provider(self.project_dir)
            self.line.emit(f"Provider 已安装：{destination}")
            self.line.emit("写入 Hypit 平台凭据存储...")
            result = login_apib_credential(self.project_dir, self.api_key)
            if result.stdout:
                self.line.emit(result.stdout.strip())
            if result.returncode != 0:
                raise RuntimeError(
                    result.stderr.strip() or "APIB 凭据写入失败。"
                )
            self.finished.emit(True)
        except Exception as exc:
            self.error.emit(str(exc))
            self.finished.emit(False)


class HypitPage(QtWidgets.QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.command_thread: HypitCommandThread | None = None
        self.setup_thread: HypitSetupThread | None = None
        self.initialize_thread: HypitInitializeThread | None = None
        self.apib_thread: HypitApibSetupThread | None = None

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        side = card_frame()
        side.setMinimumWidth(380)
        side_layout = QtWidgets.QVBoxLayout(side)
        side_layout.setContentsMargins(18, 18, 18, 18)
        side_layout.setSpacing(10)
        side_layout.addWidget(section_label("Hypit 视频引擎"))
        side_layout.addWidget(
            hint_label(
                "Hypit 作为独立 Node.js 视频工作流引擎运行。"
                "默认项目已接入 APIB Provider；正式 Build 前先执行 Plan 并确认模型费用。"
            )
        )

        self.environment_label = QtWidgets.QTextBrowser()
        self.environment_label.setReadOnly(True)
        self.environment_label.setWordWrapMode(
            QtGui.QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
        )
        self.environment_label.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.environment_label.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.environment_label.setFixedHeight(165)
        self.refresh_environment_text()
        side_layout.addWidget(self.environment_label)

        project_row = QtWidgets.QHBoxLayout()
        self.project_name_edit = QtWidgets.QLineEdit("张小星Hypit项目")
        create_project_button = QtWidgets.QPushButton("创建项目")
        create_project_button.setObjectName("secondaryButton")
        create_project_button.clicked.connect(self.create_project)
        project_row.addWidget(self.project_name_edit, 1)
        project_row.addWidget(create_project_button)
        side_layout.addLayout(project_row)

        project_path_row = QtWidgets.QHBoxLayout()
        default_project = ensure_project_dir(self.project_name_edit.text())
        self.project_path_edit = QtWidgets.QLineEdit(str(default_project))
        self.project_path_edit.setToolTip(str(default_project))
        self.project_path_edit.textChanged.connect(self.project_path_edit.setToolTip)
        browse_project = QtWidgets.QPushButton("项目目录")
        browse_project.setObjectName("secondaryButton")
        browse_project.clicked.connect(self.browse_project)
        project_path_row.addWidget(self.project_path_edit, 1)
        project_path_row.addWidget(browse_project)
        side_layout.addLayout(project_path_row)

        svrun_row = QtWidgets.QHBoxLayout()
        self.svrun_edit = QtWidgets.QLineEdit()
        self.svrun_edit.setPlaceholderText("选择 .svrun 运行文件")
        self.svrun_edit.textChanged.connect(self.svrun_edit.setToolTip)
        browse_svrun = QtWidgets.QPushButton("选择SVRun")
        browse_svrun.setObjectName("secondaryButton")
        browse_svrun.clicked.connect(self.browse_svrun)
        svrun_row.addWidget(self.svrun_edit, 1)
        svrun_row.addWidget(browse_svrun)
        side_layout.addLayout(svrun_row)

        side_layout.addWidget(section_label("操作"))
        buttons = QtWidgets.QGridLayout()
        buttons.setHorizontalSpacing(10)
        buttons.setVerticalSpacing(10)
        buttons.setColumnStretch(0, 1)
        buttons.setColumnStretch(1, 1)
        self.check_button = QtWidgets.QPushButton("检查环境")
        self.tutorial_button = QtWidgets.QPushButton("使用教程")
        self.install_button = QtWidgets.QPushButton("安装/修复环境")
        self.runtime_button = QtWidgets.QPushButton("初始化 Runtime")
        self.prepare_runtime_button = QtWidgets.QPushButton("准备 Runtime")
        self.apib_button = QtWidgets.QPushButton("配置 APIB Provider")
        self.doctor_button = QtWidgets.QPushButton("Doctor")
        self.plan_button = QtWidgets.QPushButton("Plan")
        self.build_button = QtWidgets.QPushButton("Build")
        self.status_button = QtWidgets.QPushButton("查看状态")
        self.get_button = QtWidgets.QPushButton("导出结果")
        self.studio_button = QtWidgets.QPushButton("打开 Studio")
        for button in (
            self.check_button,
            self.tutorial_button,
            self.install_button,
            self.runtime_button,
            self.prepare_runtime_button,
            self.apib_button,
            self.doctor_button,
            self.plan_button,
            self.build_button,
            self.status_button,
            self.get_button,
            self.studio_button,
        ):
            button.setObjectName("secondaryButton")
        self.check_button.clicked.connect(self.check_environment)
        self.tutorial_button.clicked.connect(self.open_tutorial)
        self.install_button.clicked.connect(self.install_environment)
        self.runtime_button.clicked.connect(self.initialize_runtime)
        self.prepare_runtime_button.clicked.connect(self.prepare_runtime)
        self.apib_button.clicked.connect(self.configure_apib_provider)
        self.doctor_button.clicked.connect(self.run_doctor)
        self.plan_button.clicked.connect(self.run_plan)
        self.build_button.clicked.connect(self.run_build)
        self.status_button.clicked.connect(self.run_status)
        self.get_button.clicked.connect(self.get_output)
        self.studio_button.clicked.connect(self.open_studio)
        button_specs = [
            self.check_button,
            self.tutorial_button,
            self.install_button,
            self.runtime_button,
            self.prepare_runtime_button,
            self.apib_button,
            self.doctor_button,
            self.plan_button,
            self.build_button,
            self.status_button,
            self.get_button,
            self.studio_button,
        ]
        for index, button in enumerate(button_specs):
            button.setMinimumHeight(38)
            button.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )
            buttons.addWidget(button, index // 2, index % 2)
        side_layout.addLayout(buttons)

        self.build_id_edit = QtWidgets.QLineEdit()
        self.build_id_edit.setPlaceholderText("Build ID")
        self.output_name_edit = QtWidgets.QLineEdit()
        self.output_name_edit.setPlaceholderText("Output 名称，例如 final.video")
        export_row = QtWidgets.QHBoxLayout()
        self.export_path_edit = QtWidgets.QLineEdit()
        self.export_path_edit.setPlaceholderText("导出文件路径")
        self.export_path_edit.textChanged.connect(
            self.export_path_edit.setToolTip
        )
        browse_export = QtWidgets.QPushButton("导出路径")
        browse_export.setObjectName("secondaryButton")
        browse_export.clicked.connect(self.browse_export)
        export_row.addWidget(self.export_path_edit, 1)
        export_row.addWidget(browse_export)
        side_layout.addWidget(self.build_id_edit)
        side_layout.addWidget(self.output_name_edit)
        side_layout.addLayout(export_row)
        self.stop_button = QtWidgets.QPushButton("停止当前命令")
        self.stop_button.setObjectName("secondaryButton")
        self.stop_button.clicked.connect(self.stop_command)
        side_layout.addWidget(self.stop_button)
        side_layout.addStretch(1)

        log_card = card_frame()
        log_layout = QtWidgets.QVBoxLayout(log_card)
        log_layout.setContentsMargins(16, 16, 16, 16)
        log_layout.addWidget(section_label("Hypit 运行日志"))
        self.log_edit = QtWidgets.QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        log_layout.addWidget(self.log_edit, 1)

        root.addWidget(scrollable_side_card(side, width=500))
        root.addWidget(log_card, 1)
        self.append_log("Hypit 独立栏目已就绪。")

    def refresh_environment_text(self) -> None:
        self.environment_label.setPlainText(hypit_environment_summary())

    def append_log(self, text: str) -> None:
        self.log_edit.appendPlainText(text)

    def create_project(self) -> None:
        path = ensure_project_dir(self.project_name_edit.text())
        self.project_path_edit.setText(str(path))
        self.append_log(f"项目目录已创建：{path}")

    def browse_project(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "选择 Hypit 项目目录",
            self.project_path_edit.text(),
        )
        if path:
            self.project_path_edit.setText(path)

    def browse_svrun(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择 SVRun 文件",
            self.project_path_edit.text(),
            "Hypit Run (*.svrun);;所有文件 (*)",
        )
        if path:
            self.svrun_edit.setText(path)

    def browse_export(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出 Hypit 结果",
            self.export_path_edit.text() or str(Path.home() / "hypit-output.mp4"),
            "视频文件 (*.mp4 *.mov *.webm);;所有文件 (*)",
        )
        if path:
            self.export_path_edit.setText(path)

    def set_busy(self, busy: bool) -> None:
        for button in (
            self.check_button,
            self.tutorial_button,
            self.install_button,
            self.runtime_button,
            self.prepare_runtime_button,
            self.apib_button,
            self.doctor_button,
            self.plan_button,
            self.build_button,
            self.status_button,
            self.get_button,
            self.studio_button,
        ):
            button.setEnabled(not busy)
        self.stop_button.setEnabled(busy)

    def check_environment(self) -> None:
        self.refresh_environment_text()
        try:
            version = hypit_version()
            self.append_log(f"Hypit CLI 版本：{version}")
            self.append_log(hypit_environment_report())
        except Exception as exc:
            self.append_log(f"环境检查失败：{exc}")

    def open_tutorial(self) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Hypit视频使用教程")
        dialog.resize(1000, 760)
        layout = QtWidgets.QVBoxLayout(dialog)
        browser = QtWidgets.QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setHtml(build_tutorial_html())
        layout.addWidget(browser, 1)
        close_button = QtWidgets.QPushButton("关闭")
        close_button.setObjectName("primaryButton")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button, 0, QtCore.Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def install_environment(self) -> None:
        if thread_is_running(self.setup_thread):
            return
        self.set_busy(True)
        self.setup_thread = HypitSetupThread(self)
        self.setup_thread.line.connect(self.append_log)
        self.setup_thread.error.connect(lambda message: self.append_log(f"安装失败：{message}"))
        self.setup_thread.finished.connect(self._on_setup_finished)
        self.setup_thread.finished.connect(
            lambda thread=self.setup_thread: self._clear_setup_thread(thread)
        )
        self.setup_thread.start()

    def _clear_setup_thread(self, thread: HypitSetupThread) -> None:
        if self.setup_thread is thread:
            self.setup_thread = None

    def _on_setup_finished(self, success: bool) -> None:
        self.set_busy(False)
        self.refresh_environment_text()
        self.append_log("Hypit 环境安装完成。" if success else "Hypit 环境安装未完成。")

    def run_command(self, args: list[str], label: str) -> None:
        if thread_is_running(self.command_thread):
            QtWidgets.QMessageBox.information(self, "Hypit 正在运行", "请先停止当前命令。")
            return
        project = self.project_path_edit.text().strip()
        if not project or not Path(project).exists():
            QtWidgets.QMessageBox.warning(self, "缺少项目目录", "请先创建或选择 Hypit 项目目录。")
            return
        self.set_busy(True)
        self.append_log(f"$ hypit {' '.join(args)}")
        self.command_thread = HypitCommandThread(args, project, self)
        self.command_thread.line.connect(self.append_log)
        self.command_thread.error.connect(lambda message: self.append_log(f"错误：{message}"))
        self.command_thread.finished.connect(self._on_command_finished)
        self.command_thread.finished.connect(
            lambda code=0, thread=self.command_thread: self._clear_command_thread(thread)
        )
        self.command_thread.start()

    def _clear_command_thread(self, thread: HypitCommandThread) -> None:
        if self.command_thread is thread:
            self.command_thread = None

    def _on_command_finished(self, code: int) -> None:
        self.set_busy(False)
        self.append_log(f"命令结束，退出码：{code}")

    def stop_command(self) -> None:
        if self.command_thread:
            self.command_thread.cancel()
            self.append_log("已请求停止 Hypit 命令。")

    def initialize_runtime(self) -> None:
        if thread_is_running(self.initialize_thread):
            return
        project = self.project_path_edit.text().strip()
        if not project or not Path(project).exists():
            QtWidgets.QMessageBox.warning(self, "缺少项目目录", "请先创建或选择 Hypit 项目目录。")
            return
        self.set_busy(True)
        self.append_log("$ hypit runtime init")
        self.initialize_thread = HypitInitializeThread(project, self)
        self.initialize_thread.line.connect(self.append_log)
        self.initialize_thread.error.connect(lambda message: self.append_log(f"错误：{message}"))
        self.initialize_thread.finished.connect(self._on_initialize_finished)
        self.initialize_thread.finished.connect(
            lambda thread=self.initialize_thread: self._clear_initialize_thread(thread)
        )
        self.initialize_thread.start()

    def _clear_initialize_thread(self, thread: HypitInitializeThread) -> None:
        if self.initialize_thread is thread:
            self.initialize_thread = None

    def _on_initialize_finished(self, success: bool) -> None:
        self.set_busy(False)
        self.append_log("Runtime 初始化完成。" if success else "Runtime 初始化未完成。")

    def prepare_runtime(self) -> None:
        self.run_command(["runtime", "up"], "runtime up")

    def configure_apib_provider(self) -> None:
        if thread_is_running(self.apib_thread):
            return
        project = self.project_path_edit.text().strip()
        if not project or not Path(project).exists():
            QtWidgets.QMessageBox.warning(self, "缺少项目目录", "请先创建或选择 Hypit 项目目录。")
            return
        api_key = self.main_window.settings_store.as_dict()["api_key"].strip()
        if not api_key:
            QtWidgets.QMessageBox.warning(
                self,
                "缺少 APIB API Key",
                "请先在“配置”页面填写 APIB API Key。",
            )
            return
        self.set_busy(True)
        self.append_log("开始安装 APIB Provider。")
        self.apib_thread = HypitApibSetupThread(project, api_key, self)
        self.apib_thread.line.connect(self.append_log)
        self.apib_thread.error.connect(lambda message: self.append_log(f"APIB 配置失败：{message}"))
        self.apib_thread.finished.connect(self._on_apib_finished)
        self.apib_thread.finished.connect(
            lambda thread=self.apib_thread: self._clear_apib_thread(thread)
        )
        self.apib_thread.start()

    def _clear_apib_thread(self, thread: HypitApibSetupThread) -> None:
        if self.apib_thread is thread:
            self.apib_thread = None

    def _on_apib_finished(self, success: bool) -> None:
        self.set_busy(False)
        self.append_log("APIB Provider 配置完成。" if success else "APIB Provider 配置未完成。")

    def run_doctor(self) -> None:
        self.run_command(["doctor"], "doctor")

    def _require_svrun(self) -> str | None:
        svrun = self.svrun_edit.text().strip()
        if not svrun or not Path(svrun).exists():
            QtWidgets.QMessageBox.warning(self, "缺少 SVRun", "请选择有效的 .svrun 运行文件。")
            return None
        return svrun

    def run_plan(self) -> None:
        svrun = self._require_svrun()
        if svrun:
            self.run_command(["plan", svrun], "plan")

    def run_build(self) -> None:
        svrun = self._require_svrun()
        if svrun:
            self.run_command(["build", svrun, "--follow"], "build")

    def run_status(self) -> None:
        build_id = self.build_id_edit.text().strip()
        if not build_id:
            QtWidgets.QMessageBox.warning(self, "缺少 Build ID", "请输入 Build ID。")
            return
        self.run_command(["status", build_id], "status")

    def get_output(self) -> None:
        build_id = self.build_id_edit.text().strip()
        output = self.output_name_edit.text().strip()
        target = self.export_path_edit.text().strip()
        if not build_id or not output or not target:
            QtWidgets.QMessageBox.warning(self, "信息不完整", "请填写 Build ID、Output 名称和导出路径。")
            return
        self.run_command(["get", build_id, "--output", output, "--to", target], "get")

    def open_studio(self) -> None:
        svrun = self._require_svrun()
        if not svrun:
            return
        try:
            launch_hypit_studio(["--run", svrun], cwd=self.project_path_edit.text().strip())
            self.append_log("Hypit Studio 已启动。")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Studio 启动失败", str(exc))


class PublishAssistThread(QtCore.QThread):
    result = QtCore.Signal(dict)
    error = QtCore.Signal(str)

    def __init__(self, params: dict, parent=None):
        super().__init__(parent)
        self.params = params

    def run(self) -> None:
        try:
            self.result.emit(assist_upload(**self.params))
        except Exception as exc:
            self.error.emit(str(exc))


class PublishPage(QtWidgets.QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.posts_by_key: dict[str, PlatformPost] = {}
        self.platform_widgets: dict[str, dict] = {}
        self.assist_thread: PublishAssistThread | None = None

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        side = card_frame()
        side.setMinimumWidth(360)
        side_layout = QtWidgets.QVBoxLayout(side)
        side_layout.setContentsMargins(18, 18, 18, 18)
        side_layout.setSpacing(12)
        side_layout.addWidget(section_label("一键准备与分发"))
        side_layout.addWidget(
            hint_label(
                "软件负责生成平台文案、校验格式、打开官方发布页并尝试上传。"
                "最终发布按钮必须由你在平台页面手动点击。"
            )
        )

        self.title_edit = QtWidgets.QLineEdit()
        self.title_edit.setPlaceholderText("统一标题")
        self.description_edit = QtWidgets.QPlainTextEdit()
        self.description_edit.setPlaceholderText("统一简介或正文")
        self.description_edit.setFixedHeight(120)
        self.tags_edit = QtWidgets.QLineEdit()
        self.tags_edit.setPlaceholderText("标签，用空格或逗号分隔，例如：肛周护理 久坐党 健康科普")
        side_layout.addWidget(self.title_edit)
        side_layout.addWidget(self.description_edit)
        side_layout.addWidget(self.tags_edit)

        side_layout.addWidget(section_label("图片与视频"))
        self.media_list = QtWidgets.QListWidget()
        self.media_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.media_list.setFixedHeight(130)
        side_layout.addWidget(self.media_list)
        media_buttons = QtWidgets.QGridLayout()
        add_media = QtWidgets.QPushButton("上传文件")
        load_latest = QtWidgets.QPushButton("载入最近生成")
        remove_selected = QtWidgets.QPushButton("移除选中")
        clear_media = QtWidgets.QPushButton("清空")
        for button in (add_media, load_latest, remove_selected, clear_media):
            button.setObjectName("secondaryButton")
        add_media.clicked.connect(self.add_media)
        load_latest.clicked.connect(self.load_latest)
        remove_selected.clicked.connect(self.remove_selected)
        clear_media.clicked.connect(self.clear_media)
        media_buttons.addWidget(add_media, 0, 0)
        media_buttons.addWidget(load_latest, 0, 1)
        media_buttons.addWidget(remove_selected, 1, 0)
        media_buttons.addWidget(clear_media, 1, 1)
        side_layout.addLayout(media_buttons)

        side_layout.addWidget(section_label("发布平台"))
        platform_grid = QtWidgets.QGridLayout()
        self.platform_checks: dict[str, QtWidgets.QCheckBox] = {}
        for index, (key, profile) in enumerate(PLATFORMS.items()):
            checkbox = QtWidgets.QCheckBox(profile.name)
            checkbox.setChecked(True)
            self.platform_checks[key] = checkbox
            platform_grid.addWidget(checkbox, index // 3, index % 3)
        side_layout.addLayout(platform_grid)

        self.status_label = hint_label("准备就绪")
        side_layout.addWidget(self.status_label)
        generate_button = QtWidgets.QPushButton("生成平台适配文案")
        generate_button.setObjectName("primaryButton")
        generate_button.setMinimumHeight(46)
        generate_button.clicked.connect(self.generate_posts)
        side_layout.addWidget(generate_button)
        side_layout.addStretch(1)

        self.tabs = QtWidgets.QTabWidget()
        for key, profile in PLATFORMS.items():
            page = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(page)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(10)
            title = QtWidgets.QLineEdit()
            title.setPlaceholderText("平台标题")
            description = QtWidgets.QPlainTextEdit()
            description.setPlaceholderText("平台简介或正文")
            tags = QtWidgets.QLineEdit()
            tags.setPlaceholderText("平台标签")
            validation = hint_label("尚未生成适配文案。")
            buttons = QtWidgets.QHBoxLayout()
            copy_button = QtWidgets.QPushButton("复制全部文案")
            open_button = QtWidgets.QPushButton("打开官方发布页")
            upload_button = QtWidgets.QPushButton("辅助上传")
            for button in (copy_button, open_button, upload_button):
                button.setObjectName("secondaryButton")
            copy_button.clicked.connect(lambda checked=False, k=key: self.copy_post(k))
            open_button.clicked.connect(lambda checked=False, k=key: self.open_page(k))
            upload_button.clicked.connect(lambda checked=False, k=key: self.assist_upload(k))
            buttons.addWidget(copy_button)
            buttons.addWidget(open_button)
            buttons.addWidget(upload_button)
            layout.addWidget(QtWidgets.QLabel("标题"))
            layout.addWidget(title)
            layout.addWidget(QtWidgets.QLabel("简介 / 正文"))
            layout.addWidget(description, 1)
            layout.addWidget(QtWidgets.QLabel("标签"))
            layout.addWidget(tags)
            layout.addWidget(validation)
            layout.addLayout(buttons)
            self.platform_widgets[key] = {
                "page": page,
                "title": title,
                "description": description,
                "tags": tags,
                "validation": validation,
                "upload_button": upload_button,
            }
            self.tabs.addTab(page, profile.name)
        root.addWidget(scrollable_side_card(side))
        root.addWidget(self.tabs, 1)

    def add_media(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "选择发布素材",
            str(Path.home()),
            "媒体文件 (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.mp4 *.mov *.avi *.mkv *.webm)",
        )
        existing = {self.media_list.item(index).text() for index in range(self.media_list.count())}
        for path in paths:
            if path not in existing:
                self.media_list.addItem(path)

    def load_latest(self) -> None:
        output_dir = Path(self.main_window.settings_store.as_dict()["output_dir"])
        extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".mp4", ".mov", ".avi", ".mkv", ".webm"}
        files = [
            path
            for path in output_dir.rglob("*")
            if path.is_file()
            and path.suffix.lower() in extensions
            and "drafts" not in path.parts
            and "browser_profiles" not in path.parts
        ]
        files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        existing = {self.media_list.item(index).text() for index in range(self.media_list.count())}
        for path in files[:30]:
            if str(path) not in existing:
                self.media_list.addItem(str(path))
        self.status_label.setText(f"已载入最近生成的 {min(30, len(files))} 个媒体文件")

    def remove_selected(self) -> None:
        rows = sorted({index.row() for index in self.media_list.selectedIndexes()}, reverse=True)
        for row in rows:
            self.media_list.takeItem(row)

    def clear_media(self) -> None:
        self.media_list.clear()

    def selected_media(self) -> list[Path]:
        return [Path(self.media_list.item(index).text()) for index in range(self.media_list.count())]

    def selected_platforms(self) -> list[str]:
        return [key for key, checkbox in self.platform_checks.items() if checkbox.isChecked()]

    def generate_posts(self) -> None:
        keys = self.selected_platforms()
        if not keys:
            QtWidgets.QMessageBox.warning(self, "未选择平台", "请至少选择一个发布平台。")
            return
        media = self.selected_media()
        if not media:
            QtWidgets.QMessageBox.warning(self, "缺少素材", "请上传或载入图片、视频。")
            return
        posts = build_platform_posts(
            base_title=self.title_edit.text(),
            description=self.description_edit.toPlainText(),
            raw_tags=self.tags_edit.text(),
            media_paths=media,
            platform_keys=keys,
        )
        self.posts_by_key = {post.platform.key: post for post in posts}
        for index, (key, profile) in enumerate(PLATFORMS.items()):
            enabled = key in self.posts_by_key
            self.tabs.setTabEnabled(index, enabled)
            widgets = self.platform_widgets[key]
            if not enabled:
                widgets["validation"].setText("未勾选此平台。")
                continue
            post = self.posts_by_key[key]
            widgets["title"].setText(post.title)
            widgets["description"].setPlainText(post.description)
            widgets["tags"].setText(" ".join(post.tags))
            messages = post.errors + post.warnings
            widgets["validation"].setText("；".join(messages) if messages else "校验通过，可以打开发布页。")
        self.status_label.setText(f"已生成 {len(posts)} 个平台适配方案")

    def _current_post_text(self, key: str) -> str:
        widgets = self.platform_widgets[key]
        title = widgets["title"].text().strip()
        description = widgets["description"].toPlainText().strip()
        tags = widgets["tags"].text().strip()
        tag_text = " ".join(f"#{tag.lstrip('#')}" for tag in tags.split() if tag)
        return "\n\n".join(part for part in (title, description, tag_text) if part)

    def copy_post(self, key: str) -> None:
        text = self._current_post_text(key)
        if not text:
            QtWidgets.QMessageBox.warning(self, "没有文案", "请先生成平台适配文案。")
            return
        QtWidgets.QApplication.clipboard().setText(text)
        self.status_label.setText(f"已复制{PLATFORMS[key].name}文案到剪贴板")

    def open_page(self, key: str) -> None:
        try:
            open_platform_page(key)
            self.status_label.setText(f"已打开{PLATFORMS[key].name}官方发布页")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "打开发布页失败", str(exc))

    def assist_upload(self, key: str) -> None:
        post = self.posts_by_key.get(key)
        if not post:
            QtWidgets.QMessageBox.warning(self, "没有文案", "请先生成平台适配文案。")
            return
        if post.errors:
            QtWidgets.QMessageBox.warning(self, "校验未通过", "\n".join(post.errors))
            return
        if thread_is_running(self.assist_thread):
            QtWidgets.QMessageBox.information(self, "正在辅助上传", "请先完成当前浏览器页面。")
            return

        widgets = self.platform_widgets[key]
        params = {
            "platform_key": key,
            "media_paths": [str(path) for path in self.selected_media()],
            "title": widgets["title"].text().strip(),
            "description": widgets["description"].toPlainText().strip(),
            "keep_open": True,
        }
        self.status_label.setText(f"正在打开{PLATFORMS[key].name}并尝试上传，最终发布请手动点击")
        self.assist_thread = PublishAssistThread(params, self)
        self.assist_thread.result.connect(self._on_assist_result)
        self.assist_thread.error.connect(self._on_assist_error)
        self.assist_thread.finished.connect(
            lambda thread=self.assist_thread: self._clear_assist_thread(thread)
        )
        self.assist_thread.start()

    def _clear_assist_thread(self, thread: PublishAssistThread) -> None:
        if self.assist_thread is thread:
            self.assist_thread = None

    def _on_assist_result(self, result: dict) -> None:
        self.status_label.setText(result.get("message", "辅助上传完成"))
        QtWidgets.QMessageBox.information(self, "辅助上传完成", result.get("message", ""))

    def _on_assist_error(self, message: str) -> None:
        self.status_label.setText(f"辅助上传失败：{message}")
        QtWidgets.QMessageBox.critical(self, "辅助上传失败", message)


class ModelsPage(QtWidgets.QWidget):
    refresh_requested = QtCore.Signal()
    pricing_refresh_requested = QtCore.Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.trees: dict[str, QtWidgets.QTreeWidget] = {}
        self.row_by_model: dict[str, QtWidgets.QTreeWidgetItem] = {}
        self._pricing_requested = False

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        toolbar = card_frame()
        toolbar_layout = QtWidgets.QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(18, 14, 18, 14)
        title = section_label("模型中心")
        self.source_label = hint_label("正在加载模型...")
        self.balance_label = hint_label("余额：未查询")
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("搜索模型 ID 或能力标签")
        self.refresh_button = QtWidgets.QPushButton("刷新模型列表")
        self.refresh_button.setObjectName("primaryButton")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.pricing_button = QtWidgets.QPushButton("刷新价格与用量")
        self.pricing_button.setObjectName("secondaryButton")
        self.pricing_button.clicked.connect(self.pricing_refresh_requested.emit)
        self.search_edit.textChanged.connect(self.filter_trees)
        toolbar_layout.addWidget(title)
        toolbar_layout.addWidget(self.source_label)
        toolbar_layout.addWidget(self.balance_label)
        toolbar_layout.addStretch(1)
        toolbar_layout.addWidget(self.search_edit, 1)
        toolbar_layout.addWidget(self.refresh_button)
        toolbar_layout.addWidget(self.pricing_button)
        root.addWidget(toolbar)

        self.tabs = QtWidgets.QTabWidget()
        for category in ("image", "video", "audio", "chat"):
            tree = QtWidgets.QTreeWidget()
            tree.setColumnCount(6)
            tree.setHeaderLabels(["模型 ID", "能力标签", "价格 / Token计费", "已消耗Token", "已用金额", "来源"])
            tree.setRootIsDecorated(False)
            tree.setAlternatingRowColors(True)
            tree.setUniformRowHeights(True)
            tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
            tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
            tree.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
            tree.header().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Stretch)
            tree.header().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            tree.header().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            tree.itemDoubleClicked.connect(
                lambda item, column, cat=category: self.apply_item(cat, item)
            )
            self.trees[category] = tree
            self.tabs.addTab(tree, MODEL_CATEGORY_LABELS[category])
        root.addWidget(self.tabs, 1)

        note = hint_label(
            "模型列表优先从 /v1/models?expand=category 动态获取，范围受当前 API Key 的权限和分组限制。"
            "未配置 Key 或接口暂不可用时显示内置文档清单。双击图片或视频模型可直接应用。"
        )
        root.addWidget(note)
        self.refresh(self.main_window.model_catalog, self.main_window.model_source)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._pricing_requested:
            self._pricing_requested = True
            QtCore.QTimer.singleShot(0, self.pricing_refresh_requested.emit)

    def refresh(self, catalog: dict[str, list[dict]], source: str) -> None:
        self.source_label.setText(source)
        self.row_by_model.clear()
        self._pricing_requested = False
        for category, tree in self.trees.items():
            tree.clear()
            for model in sorted(catalog.get(category, []), key=lambda item: str(item.get("id", "")).lower()):
                model_id = str(model.get("id", "")).strip()
                if not model_id:
                    continue
                tags = model.get("capability_tags") or []
                tag_text = "、".join(str(tag) for tag in tags) if isinstance(tags, list) else str(tags)
                item = QtWidgets.QTreeWidgetItem(
                    [model_id, tag_text, "查询中...", "未配置API Key", "未配置API Key", source]
                )
                item.setData(0, QtCore.Qt.ItemDataRole.UserRole, model_id)
                tree.addTopLevelItem(item)
                self.row_by_model[model_id.lower()] = item
        self.filter_trees(self.search_edit.text())

    def set_balance(self, balance: dict | None) -> None:
        self.balance_label.setText(format_balance(balance))

    def update_pricing(
        self,
        model_id: str,
        pricing: dict | None,
        usage: dict | None,
        error: str = "",
    ) -> None:
        item = self.row_by_model.get(model_id.lower())
        if item is None:
            return
        if error:
            item.setText(2, f"价格查询失败：{error}")
        else:
            item.setText(2, format_pricing(pricing))
        item.setText(3, format_usage(usage))
        item.setText(4, format_billing(usage))

    def filter_trees(self, text: str) -> None:
        needle = text.strip().lower()
        for tree in self.trees.values():
            for index in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(index)
                haystack = " ".join(item.text(column) for column in range(item.columnCount())).lower()
                item.setHidden(bool(needle and needle not in haystack))

    def apply_item(self, category: str, item: QtWidgets.QTreeWidgetItem) -> None:
        model_id = item.data(0, QtCore.Qt.ItemDataRole.UserRole) or item.text(0)
        if category == "image":
            self.main_window.image_page.model_combo.setCurrentText(str(model_id))
            self.main_window.batch_page.image_model_combo.setCurrentText(str(model_id))
            self.main_window.stack.setCurrentIndex(0)
            self.main_window.nav_buttons[0].setChecked(True)
        elif category == "video":
            self.main_window.video_page.model_combo.setCurrentText(str(model_id))
            self.main_window.batch_page.video_model_combo.setCurrentText(str(model_id))
            self.main_window.stack.setCurrentIndex(1)
            self.main_window.nav_buttons[1].setChecked(True)
        else:
            QtWidgets.QMessageBox.information(
                self,
                "模型已选择",
                f"已选择 {MODEL_CATEGORY_LABELS[category]}：{model_id}\n"
                "当前版本尚未提供该分类的独立生成页面，模型信息已保留在模型中心。",
            )


class SettingsPage(QtWidgets.QWidget):
    saved = QtCore.Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        card = card_frame()
        card.setMaximumWidth(820)
        form = QtWidgets.QFormLayout(card)
        form.setContentsMargins(28, 28, 28, 28)
        form.setSpacing(16)

        self.api_key_edit = QtWidgets.QLineEdit()
        self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("填写 APIB / APIMart API Key")
        self.base_url_edit = QtWidgets.QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.apib.ai/v1")
        self.mock_check = QtWidgets.QCheckBox("演示模式：不调用云端接口，本地生成占位图片和视频")
        self.mock_check.setChecked(True)
        self.output_edit = QtWidgets.QLineEdit()
        browse_button = QtWidgets.QPushButton("选择目录")
        browse_button.setObjectName("secondaryButton")
        browse_button.clicked.connect(self.browse_output)
        output_row = QtWidgets.QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(browse_button)
        output_widget = QtWidgets.QWidget()
        output_widget.setLayout(output_row)

        form.addRow("API Key", self.api_key_edit)
        form.addRow("Base URL", self.base_url_edit)
        form.addRow("运行模式", self.mock_check)
        form.addRow("输出目录", output_widget)

        self.note = hint_label(
            "真实模式使用 APIB/APIMart 上传图片、图片生成、视频生成和任务状态接口。"
            "API Key 只保存在本机 Qt 设置中，不会写入项目文件。"
        )
        form.addRow("", self.note)

        buttons = QtWidgets.QHBoxLayout()
        self.test_button = QtWidgets.QPushButton("测试连接")
        self.test_button.setObjectName("secondaryButton")
        self.test_button.clicked.connect(self.test_connection)
        self.save_button = QtWidgets.QPushButton("保存配置")
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self.save)
        buttons.addStretch(1)
        buttons.addWidget(self.test_button)
        buttons.addWidget(self.save_button)
        form.addRow("", buttons)

        layout.addWidget(card, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)

        self.status = hint_label("")
        layout.addWidget(self.status)
        self.load()

    def load(self) -> None:
        values = self.main_window.settings_store.as_dict()
        self.api_key_edit.setText(values["api_key"])
        self.base_url_edit.setText(values["base_url"])
        self.mock_check.setChecked(bool(values["mock_mode"]))
        self.output_edit.setText(values["output_dir"])

    def browse_output(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择输出目录", self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    def save(self) -> None:
        values = {
            "api_key": self.api_key_edit.text().strip(),
            "base_url": self.base_url_edit.text().strip() or "https://api.apib.ai/v1",
            "mock_mode": self.mock_check.isChecked(),
            "output_dir": self.output_edit.text().strip() or str(default_output_dir()),
        }
        self.main_window.settings_store.save(values)
        self.main_window.update_status()
        self.status.setText("配置已保存。")
        self.saved.emit()

    def test_connection(self) -> None:
        if thread_is_running(getattr(self, "_test_thread", None)):
            self.status.setText("连接测试正在进行，请稍候。")
            return
        if self.mock_check.isChecked():
            self.status.setText("演示模式可用，无需联网。")
            return
        if not self.api_key_edit.text().strip():
            self.status.setText("请先填写 API Key。")
            return

        self.test_button.setEnabled(False)
        self.status.setText("正在测试连接...")

        class TestThread(QtCore.QThread):
            result = QtCore.Signal(bool, str)

            def __init__(self, base_url: str, api_key: str, parent=None):
                super().__init__(parent)
                self.base_url = base_url
                self.api_key = api_key

            def run(self):
                try:
                    client = APIMartClient(APIConfig(base_url=self.base_url, api_key=self.api_key))
                    client.test_connection()
                    self.result.emit(True, "连接成功。")
                except Exception as exc:
                    self.result.emit(False, str(exc))

        self._test_thread = TestThread(self.base_url_edit.text().strip(), self.api_key_edit.text().strip(), self)
        self._test_thread.result.connect(self._on_test_result)
        self._test_thread.finished.connect(
            lambda thread=self._test_thread: self._clear_test_thread(thread)
        )
        self._test_thread.start()

    def _clear_test_thread(self, thread) -> None:
        if getattr(self, "_test_thread", None) is thread:
            self._test_thread = None

    def _on_test_result(self, success: bool, message: str) -> None:
        self.test_button.setEnabled(True)
        self.status.setText(message)
        if success:
            QtWidgets.QMessageBox.information(self, "连接测试", message)
        else:
            QtWidgets.QMessageBox.warning(self, "连接测试", message)


class ModelFetchThread(QtCore.QThread):
    result = QtCore.Signal(dict, str)
    error = QtCore.Signal(str)

    def __init__(self, base_url: str, api_key: str, parent=None):
        super().__init__(parent)
        self.base_url = base_url
        self.api_key = api_key

    def run(self) -> None:
        try:
            client = APIMartClient(APIConfig(base_url=self.base_url, api_key=self.api_key))
            models = client.list_models(expand="category")
            catalog = {category: [] for category in MODEL_CATEGORY_LABELS}
            for model in models:
                category = str(model.get("category") or "")
                if category in catalog:
                    catalog[category].append(model)
            total = sum(len(items) for items in catalog.values())
            if total == 0:
                raise APIClientError("当前 API Key 未返回可识别的图片、视频、音频或文本模型。")
            self.result.emit(catalog, f"官方接口动态清单 · {total} 个模型")
        except Exception as exc:
            self.error.emit(str(exc))


class PricingFetchThread(QtCore.QThread):
    balance_ready = QtCore.Signal(dict)
    pricing_ready = QtCore.Signal(str, dict, dict, str)
    error = QtCore.Signal(str)

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_ids_to_fetch: list[str],
        parent=None,
    ):
        super().__init__(parent)
        self.base_url = base_url
        self.api_key = api_key
        self.model_ids_to_fetch = model_ids_to_fetch[:80]

    def _fetch_one(self, model_id: str) -> tuple[str, dict, str]:
        try:
            client = APIMartClient(APIConfig(base_url=self.base_url, api_key=self.api_key))
            return model_id, client.get_model_pricing(model_id), ""
        except Exception as exc:
            return model_id, {}, str(exc)

    def run(self) -> None:
        usage_map: dict[str, dict] = {}
        if self.api_key.strip():
            try:
                client = APIMartClient(APIConfig(base_url=self.base_url, api_key=self.api_key))
                try:
                    balance = client.get_balance()
                    self.balance_ready.emit(balance)
                except Exception:
                    pass
                end = datetime.now(timezone.utc)
                start = end - timedelta(days=30)
                usage = client.get_usage(
                    start=start.isoformat(),
                    end=end.isoformat(),
                    group_by="model",
                    scope="key",
                )
                items = usage.get("items")
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and item.get("model"):
                            usage_map[str(item["model"]).lower()] = item
            except Exception as exc:
                self.error.emit(f"用量查询失败：{exc}")

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(self._fetch_one, model_id) for model_id in self.model_ids_to_fetch]
            for future in as_completed(futures):
                model_id, pricing, error = future.result()
                self.pricing_ready.emit(
                    model_id,
                    pricing,
                    usage_map.get(model_id.lower(), {}),
                    error,
                )


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings_store = SettingsStore()
        self.model_catalog = copy.deepcopy(FALLBACK_MODEL_CATALOG)
        self.model_source = "内置文档清单"
        self._model_fetch_thread: ModelFetchThread | None = None
        self._pricing_fetch_thread: PricingFetchThread | None = None
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(make_icon())
        self.resize(1280, 840)
        self.setMinimumSize(1120, 720)

        central = QtWidgets.QWidget()
        central.setObjectName("appRoot")
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 18)
        root.setSpacing(14)

        header = QtWidgets.QFrame()
        header.setObjectName("headerCard")
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(18, 12, 18, 12)
        logo = QtWidgets.QLabel("+")
        logo.setObjectName("logoBadge")
        logo.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(40, 40)
        title_box = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel(APP_TITLE)
        title.setObjectName("appTitle")
        subtitle = QtWidgets.QLabel("图片生成 · 视频生成 · Excel 批量处理")
        subtitle.setObjectName("mutedLabel")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addWidget(logo)
        header_layout.addLayout(title_box)
        header_layout.addStretch(1)

        self.nav_group = QtWidgets.QButtonGroup(self)
        self.nav_group.setExclusive(True)
        nav_labels = [
            "图文生成",
            "视频生成",
            "批量处理",
            "剪映草稿",
            "Hypit视频",
            "发布中心",
            "模型中心",
            "配置",
        ]
        self.nav_buttons: list[QtWidgets.QPushButton] = []
        for index, label in enumerate(nav_labels):
            button = QtWidgets.QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("navButton")
            button.clicked.connect(lambda checked=False, idx=index: self.stack.setCurrentIndex(idx))
            self.nav_group.addButton(button, index)
            self.nav_buttons.append(button)
            header_layout.addWidget(button)
        self.nav_buttons[0].setChecked(True)

        self.status_badge = QtWidgets.QLabel()
        self.status_badge.setObjectName("statusBadge")
        header_layout.addWidget(self.status_badge)
        root.addWidget(header)

        self.stack = QtWidgets.QStackedWidget()
        self.image_page = ImagePage(self)
        self.video_page = VideoPage(self)
        self.batch_page = BatchPage(self)
        self.jianying_page = JianyingPage(self)
        self.hypit_page = HypitPage(self)
        self.publish_page = PublishPage(self)
        self.models_page = ModelsPage(self)
        self.settings_page = SettingsPage(self)
        self.models_page.refresh_requested.connect(self.refresh_models)
        self.models_page.pricing_refresh_requested.connect(self.refresh_pricing_usage)
        self.settings_page.saved.connect(self.on_settings_saved)
        for page in (
            self.image_page,
            self.video_page,
            self.batch_page,
            self.jianying_page,
            self.hypit_page,
            self.publish_page,
            self.models_page,
            self.settings_page,
        ):
            self.stack.addWidget(page)
        root.addWidget(self.stack, 1)

        self.setStyleSheet(APP_STYLE)
        self.set_model_catalog(self.model_catalog, self.model_source)
        self.update_status()

    def on_settings_saved(self) -> None:
        self.update_status()
        self.refresh_models(silent=True)

    def set_model_catalog(self, catalog: dict[str, list[dict]], source: str) -> None:
        self.model_catalog = catalog
        self.model_source = source
        self.models_page.refresh(catalog, source)
        self.image_page.set_models(model_ids(catalog, "image"))
        self.video_page.set_models(model_ids(catalog, "video"))
        self.batch_page.set_models(
            model_ids(catalog, "image"),
            model_ids(catalog, "video"),
        )

    def refresh_models(self, silent: bool = False) -> None:
        if thread_is_running(self._model_fetch_thread):
            if not silent:
                QtWidgets.QMessageBox.information(self, "模型中心", "模型列表正在刷新，请稍候。")
            return
        self._model_fetch_thread = None
        values = self.settings_store.as_dict()
        if values["mock_mode"]:
            self.set_model_catalog(
                copy.deepcopy(FALLBACK_MODEL_CATALOG),
                "内置文档清单 · 演示模式",
            )
            if not silent:
                QtWidgets.QMessageBox.information(
                    self,
                    "模型中心",
                    "当前是演示模式，显示内置文档模型清单。关闭演示模式并配置 API Key 后，可拉取账号可用模型。",
                )
            return
        if not values["api_key"]:
            self.set_model_catalog(
                copy.deepcopy(FALLBACK_MODEL_CATALOG),
                "内置文档清单 · 未配置 API Key",
            )
            if not silent:
                QtWidgets.QMessageBox.warning(self, "模型中心", "请先在“配置”页面填写 API Key。")
            return

        self.models_page.source_label.setText("正在从官方接口获取模型列表...")
        self.models_page.refresh_button.setEnabled(False)
        self._model_fetch_silent = silent
        self._model_fetch_thread = ModelFetchThread(values["base_url"], values["api_key"], self)
        self._model_fetch_thread.result.connect(self._on_models_loaded)
        self._model_fetch_thread.error.connect(self._on_models_error)
        self._model_fetch_thread.finished.connect(
            lambda thread=self._model_fetch_thread: self._clear_model_fetch_thread(thread)
        )
        self._model_fetch_thread.start()

    def _clear_model_fetch_thread(self, thread: ModelFetchThread) -> None:
        if self._model_fetch_thread is thread:
            self._model_fetch_thread = None

    def _on_models_loaded(self, catalog: dict[str, list[dict]], source: str) -> None:
        self.models_page.refresh_button.setEnabled(True)
        self.set_model_catalog(catalog, source)
        if not getattr(self, "_model_fetch_silent", False):
            QtWidgets.QMessageBox.information(self, "模型中心", f"模型列表已更新：{source}")

    def _on_models_error(self, message: str) -> None:
        self.models_page.refresh_button.setEnabled(True)
        self.set_model_catalog(
            copy.deepcopy(FALLBACK_MODEL_CATALOG),
            "内置文档清单 · 官方接口暂不可用",
        )
        if not getattr(self, "_model_fetch_silent", False):
            QtWidgets.QMessageBox.warning(self, "模型列表获取失败", message)

    def refresh_pricing_usage(self, silent: bool = False) -> None:
        if thread_is_running(self._pricing_fetch_thread):
            if not silent:
                QtWidgets.QMessageBox.information(self, "模型中心", "价格和用量正在刷新，请稍候。")
            return
        self._pricing_fetch_thread = None
        values = self.settings_store.as_dict()
        model_list = [
            str(item.get("id", "")).strip()
            for category in ("image", "video", "audio", "chat")
            for item in self.model_catalog.get(category, [])
        ]
        model_list = [model_id for model_id in model_list if model_id]
        if not model_list:
            if not silent:
                QtWidgets.QMessageBox.warning(self, "模型中心", "当前没有可查询价格的模型。")
            return

        self.models_page.pricing_button.setEnabled(False)
        self.models_page.balance_label.setText("余额：查询中...")
        self._pricing_fetch_thread = PricingFetchThread(
            base_url=values["base_url"],
            api_key=values["api_key"],
            model_ids_to_fetch=model_list,
            parent=self,
        )
        self._pricing_fetch_thread.balance_ready.connect(self.models_page.set_balance)
        self._pricing_fetch_thread.pricing_ready.connect(self.models_page.update_pricing)
        self._pricing_fetch_thread.error.connect(
            lambda message: self.models_page.balance_label.setText(message)
        )
        self._pricing_fetch_thread.finished.connect(
            lambda thread=self._pricing_fetch_thread: self._on_pricing_finished(thread)
        )
        self._pricing_fetch_thread.start()

    def _on_pricing_finished(self, thread: PricingFetchThread) -> None:
        if self._pricing_fetch_thread is thread:
            self._pricing_fetch_thread = None
        self.models_page.pricing_button.setEnabled(True)
        if self.models_page.balance_label.text() == "余额：查询中...":
            self.models_page.balance_label.setText("余额：未配置 API Key 或接口不可用")

    def update_status(self) -> None:
        values = self.settings_store.as_dict()
        if values["mock_mode"]:
            text = "演示模式"
        elif values["api_key"]:
            text = "API 已配置"
        else:
            text = "待配置 API"
        self.status_badge.setText(text)


APP_STYLE = """
QWidget {
    color: #322A31;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei";
    font-size: 13px;
}
QWidget#appRoot {
    background: #FFF8FB;
}
QFrame#headerCard, QFrame#sideCard, QFrame#previewCard {
    background: #FFFFFF;
    border: 1px solid #F0DCE6;
    border-radius: 16px;
}
QLabel#appTitle {
    color: #2D2630;
    font-size: 20px;
    font-weight: 700;
}
QLabel#sectionTitle {
    color: #332A33;
    font-size: 14px;
    font-weight: 700;
}
QLabel#mutedLabel {
    color: #93858D;
    font-size: 12px;
}
QLabel#logoBadge {
    background: #F45D8D;
    color: #FFFFFF;
    border-radius: 12px;
    font-size: 24px;
    font-weight: 700;
}
QLabel#statusBadge {
    background: #E5F6EE;
    color: #247A58;
    border-radius: 12px;
    padding: 7px 12px;
    font-size: 12px;
    font-weight: 600;
}
QLabel#previewArea {
    background: #FCF9FB;
    border: 1px dashed #E8D4DE;
    border-radius: 14px;
    color: #A99AA3;
    font-size: 18px;
    padding: 20px;
}
QPushButton {
    border: none;
    border-radius: 10px;
    padding: 9px 14px;
    font-weight: 600;
}
QPushButton#primaryButton {
    background: #F45D8D;
    color: #FFFFFF;
}
QPushButton#primaryButton:hover {
    background: #E94C7E;
}
QPushButton#secondaryButton {
    background: #FFF0F5;
    color: #E14C7C;
    border: 1px solid #F4D2DF;
}
QPushButton#secondaryButton:hover {
    background: #FFE7EF;
}
QPushButton#navButton {
    background: transparent;
    color: #7B6D75;
    padding: 9px 14px;
}
QPushButton#navButton:checked {
    background: #FFF0F5;
    color: #E14C7C;
}
QLineEdit, QPlainTextEdit, QComboBox, QListWidget, QTableWidget {
    background: #FFFCFD;
    border: 1px solid #E9D7DF;
    border-radius: 10px;
    padding: 8px;
    selection-background-color: #F7B7CD;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
    border: 1px solid #F28BB0;
}
QProgressBar {
    background: #F7EDF2;
    border: none;
    border-radius: 7px;
    height: 14px;
    text-align: center;
    color: #6A5962;
}
QProgressBar::chunk {
    background: #F45D8D;
    border-radius: 7px;
}
QToolButton#thumbButton {
    background: #FFF8FB;
    border: 1px solid #EED7E1;
    border-radius: 12px;
}
QToolButton#thumbButton:hover {
    border: 2px solid #F28BB0;
}
QScrollArea {
    background: transparent;
    border: none;
}
QScrollArea#sideScroll {
    background: transparent;
    border: none;
}
QScrollArea#sideScroll > QWidget > QWidget {
    background: transparent;
}
QScrollBar:vertical {
    background: #FFF4F8;
    width: 9px;
    margin: 2px 0 2px 0;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #EAB8CA;
    min-height: 32px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #E291AF;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QHeaderView::section {
    background: #FFF0F5;
    color: #6D5C66;
    border: none;
    padding: 8px;
}
"""


def main() -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(make_icon())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
