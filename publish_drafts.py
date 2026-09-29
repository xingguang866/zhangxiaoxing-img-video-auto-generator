from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from uuid import uuid4


def new_publish_draft_id() -> str:
    return f"draft_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"


@dataclass(slots=True)
class PublishDraft:
    source_page: str
    media_paths: list[str]
    media_type: str
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    cover_path: str = ""
    scheduled_at: str = ""
    fingerprint: str = ""
    validation_errors: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    status: str = "待校验"
    draft_id: str = field(default_factory=new_publish_draft_id)

    @property
    def existing_media(self) -> list[Path]:
        return [Path(path) for path in self.media_paths if Path(path).exists()]

    @property
    def display_name(self) -> str:
        title = self.title.strip() or "未命名发布草稿"
        return f"{title[:24]} · {len(self.existing_media)} 个素材"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "PublishDraft":
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: item for key, item in value.items() if key in allowed})

