from __future__ import annotations

import os
import subprocess
from itertools import cycle
from pathlib import Path

from pyJianYingDraft import (
    AudioMaterial,
    AudioSegment,
    DraftFolder,
    ScriptFile,
    TextSegment,
    Timerange,
    TrackSpec,
    TrackType,
    VideoMaterial,
    VideoSegment,
)


SECOND = 1_000_000


def candidate_draft_dirs() -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    candidates = [
        local / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft",
        local / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft",
        Path.home() / "AppData" / "Local" / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft",
    ]
    seen: set[str] = set()
    result: list[Path] = []
    for path in candidates:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def find_default_draft_dir() -> Path:
    for path in candidate_draft_dirs():
        if path.exists():
            return path
    return candidate_draft_dirs()[0]


def candidate_jianying_executables() -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    candidates: list[Path] = []
    apps_dir = local / "JianyingPro" / "Apps"
    if apps_dir.exists():
        version_dirs = sorted(
            [path for path in apps_dir.iterdir() if path.is_dir() and path.name[:1].isdigit()],
            key=lambda path: [int(part) for part in path.name.split(".") if part.isdigit()],
            reverse=True,
        )
        candidates.extend(path / "JianyingPro.exe" for path in version_dirs)

    candidates.extend(
        [
        Path("C:/Program Files/JianyingPro/JianyingPro.exe"),
        Path("C:/Program Files (x86)/JianyingPro/JianyingPro.exe"),
        local / "JianyingPro" / "JianyingPro.exe",
        local / "JianyingPro" / "Apps" / "JianyingPro.exe",
        ]
    )
    result: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def find_jianying_executable() -> Path | None:
    for path in candidate_jianying_executables():
        if path.exists():
            return path
    return None


def detect_jianying_versions() -> list[tuple[Path, str]]:
    versions: list[tuple[Path, str]] = []
    for path in candidate_jianying_executables():
        if not path.exists():
            continue
        parent_name = path.parent.name
        version = parent_name if parent_name and parent_name[0:1].isdigit() else "已安装"
        versions.append((path, version))
    return versions


def launch_jianying(executable_path: str | Path | None = None) -> Path | None:
    executable = Path(executable_path) if executable_path else find_jianying_executable()
    if executable and not executable.exists():
        executable = None
    if not executable:
        return None
    subprocess.Popen([str(executable)], close_fds=True)
    return executable


def _normalize_lines(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        value = line.strip()
        if value:
            lines.append(value)
    return lines


def _material_duration(material: VideoMaterial, requested_duration: int) -> int:
    if material.material_type == "photo":
        return requested_duration
    return min(material.duration, requested_duration)


def create_jianying_draft(
    *,
    draft_root: str | Path,
    draft_name: str,
    media_paths: list[str | Path],
    prompt_text: str = "",
    image_duration: float = 3.0,
    video_max_duration: float = 15.0,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    background_audio: str | Path | None = None,
) -> Path:
    paths = [Path(path) for path in media_paths]
    paths = [path for path in paths if path.exists()]
    if not paths:
        raise ValueError("至少需要一张图片或一段视频才能生成剪映草稿。")

    root = Path(draft_root)
    root.mkdir(parents=True, exist_ok=True)
    draft_folder = DraftFolder(str(root))
    script: ScriptFile = draft_folder.create_draft(
        draft_name,
        width,
        height,
        fps,
        allow_replace=True,
    )

    video_track = script.append_track(TrackSpec(TrackType.video, name="主素材"))
    text_track = script.append_track(TrackSpec(TrackType.text, name="字幕文案"))
    captions = _normalize_lines(prompt_text)
    caption_cycle = cycle(captions) if captions else None

    timeline_start = 0
    image_duration_us = max(1, int(image_duration * SECOND))
    video_max_duration_us = max(1, int(video_max_duration * SECOND))

    for path in paths:
        material = VideoMaterial(str(path))
        requested_duration = (
            image_duration_us if material.material_type == "photo" else video_max_duration_us
        )
        duration = _material_duration(material, requested_duration)
        if duration <= 0:
            continue

        segment = VideoSegment(
            material,
            Timerange(timeline_start, duration),
        )
        script.add_segment(segment, video_track)

        if caption_cycle is not None:
            caption = next(caption_cycle)
            text_duration = min(duration, 4 * SECOND)
            text_segment = TextSegment(
                caption,
                Timerange(timeline_start, text_duration),
            )
            script.add_segment(text_segment, text_track)

        timeline_start += duration

    if background_audio:
        audio_path = Path(background_audio)
        if audio_path.exists():
            audio_track = script.append_track(TrackSpec(TrackType.audio, name="背景音频"))
            audio_material = AudioMaterial(str(audio_path))
            audio_duration = min(audio_material.duration, timeline_start)
            if audio_duration > 0:
                script.add_segment(
                    AudioSegment(audio_material, Timerange(0, audio_duration)),
                    audio_track,
                )

    script.save()
    return root / draft_name
