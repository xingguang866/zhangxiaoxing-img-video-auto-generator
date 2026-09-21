from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PlatformProfile:
    key: str
    name: str
    publish_url: str
    title_limit: int
    description_limit: int
    max_tags: int
    accepts_images: bool
    accepts_videos: bool


PLATFORMS: dict[str, PlatformProfile] = {
    "douyin": PlatformProfile(
        key="douyin",
        name="抖音",
        publish_url="https://creator.douyin.com/creator-micro/content/upload",
        title_limit=55,
        description_limit=1000,
        max_tags=10,
        accepts_images=True,
        accepts_videos=True,
    ),
    "kuaishou": PlatformProfile(
        key="kuaishou",
        name="快手",
        publish_url="https://cp.kuaishou.com/article/publish/video",
        title_limit=60,
        description_limit=500,
        max_tags=10,
        accepts_images=True,
        accepts_videos=True,
    ),
    "xiaohongshu": PlatformProfile(
        key="xiaohongshu",
        name="小红书",
        publish_url="https://creator.xiaohongshu.com/publish/publish",
        title_limit=20,
        description_limit=1000,
        max_tags=10,
        accepts_images=True,
        accepts_videos=True,
    ),
    "channels": PlatformProfile(
        key="channels",
        name="视频号",
        publish_url="https://channels.weixin.qq.com/platform/post/create",
        title_limit=30,
        description_limit=1000,
        max_tags=10,
        accepts_images=True,
        accepts_videos=True,
    ),
    "bilibili": PlatformProfile(
        key="bilibili",
        name="B站",
        publish_url="https://member.bilibili.com/platform/upload/video/frame",
        title_limit=80,
        description_limit=2000,
        max_tags=10,
        accepts_images=False,
        accepts_videos=True,
    ),
}


@dataclass(slots=True)
class PlatformPost:
    platform: PlatformProfile
    title: str
    description: str
    tags: list[str]
    full_text: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


def normalize_tags(raw_tags: str, max_tags: int) -> list[str]:
    values = re.split(r"[\s,，、;；#]+", raw_tags.strip())
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = value.strip().lstrip("#")
        if not tag or tag in seen:
            continue
        seen.add(tag)
        result.append(tag)
        if len(result) >= max_tags:
            break
    return result


def _clip_title(value: str, limit: int) -> str:
    title = re.sub(r"\s+", " ", value).strip()
    return title[:limit].rstrip("，。！？,.!? ")


def _clip_description(value: str, limit: int) -> tuple[str, bool]:
    description = value.strip()
    if len(description) <= limit:
        return description, False
    return description[: max(0, limit - 1)].rstrip() + "…", True


def _format_tags(platform_key: str, tags: list[str]) -> str:
    if not tags:
        return ""
    if platform_key == "bilibili":
        return "标签：" + "、".join(tags)
    return " ".join(f"#{tag}" for tag in tags)


def classify_media(paths: list[str | Path]) -> tuple[list[Path], list[Path], list[Path]]:
    images: list[Path] = []
    videos: list[Path] = []
    unsupported: list[Path] = []
    image_extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
    video_extensions = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            unsupported.append(path)
        elif path.suffix.lower() in image_extensions:
            images.append(path)
        elif path.suffix.lower() in video_extensions:
            videos.append(path)
        else:
            unsupported.append(path)
    return images, videos, unsupported


def build_platform_posts(
    *,
    base_title: str,
    description: str,
    raw_tags: str,
    media_paths: list[str | Path],
    platform_keys: list[str],
) -> list[PlatformPost]:
    images, videos, unsupported = classify_media(media_paths)
    posts: list[PlatformPost] = []

    for key in platform_keys:
        profile = PLATFORMS[key]
        tags = normalize_tags(raw_tags, profile.max_tags)
        title = _clip_title(base_title, profile.title_limit)
        clipped_description, description_clipped = _clip_description(
            description,
            profile.description_limit,
        )
        tag_text = _format_tags(key, tags)
        full_text = clipped_description
        if tag_text:
            full_text = f"{full_text}\n\n{tag_text}".strip()

        errors: list[str] = []
        warnings: list[str] = []
        if not title:
            errors.append("标题为空。")
        if not media_paths:
            errors.append("未选择图片或视频素材。")
        if unsupported:
            errors.append(
                "存在不支持的文件：" + "、".join(path.name for path in unsupported[:5])
            )
        if images and not videos and not profile.accepts_images:
            errors.append(f"{profile.name}当前发布适配器不支持纯图片发布。")
        if videos and not profile.accepts_videos:
            errors.append(f"{profile.name}当前发布适配器不支持视频发布。")
        if not videos and not images:
            errors.append("没有可发布的媒体文件。")
        if description_clipped:
            warnings.append(f"简介超过建议长度，已截断到 {profile.description_limit} 字。")
        if len(normalize_tags(raw_tags, 999)) > profile.max_tags:
            warnings.append(f"标签超过建议数量，已保留前 {profile.max_tags} 个。")
        if not tags:
            warnings.append("未填写标签，发布页需要手动补充。")

        posts.append(
            PlatformPost(
                platform=profile,
                title=title,
                description=clipped_description,
                tags=tags,
                full_text=full_text,
                errors=errors,
                warnings=warnings,
            )
        )

    return posts
