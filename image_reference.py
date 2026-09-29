from __future__ import annotations

from pathlib import Path


REFERENCE_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
REFERENCE_IMAGE_MAX_BYTES = 10 * 1024 * 1024


def max_reference_images_for_model(model: str) -> int:
    lowered = (model or "").strip().lower()
    if not lowered:
        return 3
    if lowered.startswith("z-image"):
        return 0
    if lowered.startswith(("gpt-image", "gpt-4o-image")):
        return 5
    if lowered.startswith("gemini-3-pro-image"):
        return 14
    if lowered.startswith("gemini"):
        return 5
    if lowered.startswith("seedream"):
        return 10
    if lowered.startswith("qwen-image"):
        return 3
    return 3


def reference_limit_text(model: str) -> str:
    limit = max_reference_images_for_model(model)
    if limit <= 0:
        return "当前图片模型不支持参考图，请更换支持图生图的模型。"
    return f"当前模型最多上传 {limit} 张参考图，单张不超过 10MB。"


def validate_reference_image(path: str | Path) -> str:
    image_path = Path(path)
    if not image_path.exists():
        return f"参考图不存在：{image_path}"
    if image_path.suffix.lower() not in REFERENCE_IMAGE_EXTENSIONS:
        return "参考图仅支持 JPG、JPEG、PNG、WEBP 格式。"
    if image_path.stat().st_size > REFERENCE_IMAGE_MAX_BYTES:
        return f"参考图超过 10MB：{image_path.name}"
    return ""


def build_reference_instruction(
    mode: str,
    custom_text: str = "",
    *,
    image_count: int = 0,
) -> str:
    if mode == "none" or image_count <= 0:
        return ""
    instructions = {
        "product": "参考图中产品的外观、颜色、结构、材质和包装细节，不要照抄原图背景。",
        "person": "参考图中人物的外貌特征、服装、姿态和气质，不要照搬原图场景。",
        "background": "参考图中背景的空间结构、光线、配色和氛围，人物和产品按当前主题重新生成。",
    }
    if mode in instructions:
        return instructions[mode]
    if mode == "custom":
        return custom_text.strip()
    return ""
