from __future__ import annotations

import random
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg


STYLE_COLORS: dict[str, tuple[str, str, str]] = {
    "不限风格": ("#FFFDFB", "#3D3A44", "#F7C9D8"),
    "清新治愈": ("#FFFDF8", "#446A67", "#F4C7D7"),
    "极简静奢": ("#FAF8F4", "#34312E", "#D9CDBD"),
    "多巴胺": ("#FFF9E8", "#2A2350", "#FF7C9C"),
    "韩系温柔": ("#FFF9F7", "#5D4A50", "#F2C8CF"),
    "Y2K千禧拍": ("#F7F4FF", "#34265D", "#B8A6FF"),
    "随手拍": ("#FFFCF6", "#3C3B38", "#D8CDBF"),
    "胶片复古": ("#F6EFE5", "#493A2E", "#C67D58"),
    "手绘卡通": ("#FFFDF5", "#4B5779", "#F5B7C8"),
    "轻新中式": ("#FBF8F1", "#3F3B35", "#B95D54"),
    "立体3D质感": ("#F8FAFF", "#34415F", "#9FB6FF"),
    "棕巴恩风": ("#F5EFE4", "#414033", "#A98057"),
}


def _font_path(bold: bool = False) -> str:
    candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc") if bold else Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ""


def _font(size: int, bold: bool = False):
    path = _font_path(bold)
    if path:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        width = draw.textbbox((0, 0), candidate, font=font)[2]
        if width <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = char
    if current:
        lines.append(current)
    return lines


def generate_mock_image(
    prompt: str,
    style: str,
    output_path: str | Path,
    *,
    width: int = 768,
    height: int = 1024,
    page_number: int = 1,
) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    background, ink, accent = STYLE_COLORS.get(style, STYLE_COLORS["不限风格"])
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)

    random.seed(f"{prompt}-{style}")
    for _ in range(90):
        x = random.randint(0, width)
        y = random.randint(0, height)
        radius = random.randint(1, 3)
        color = random.choice(["#F3E8DC", "#EFE4DC", "#FFF5EA"])
        draw.ellipse((x, y, x + radius, y + radius), fill=color)

    draw.rounded_rectangle((26, 26, width - 26, height - 26), radius=30, outline="#E9D8D2", width=3)
    draw.rounded_rectangle((62, 62, width - 62, 148), radius=20, fill="#FFFFFF", outline=accent, width=3)

    small_font = _font(23)
    title_font = _font(54, bold=True)
    label_font = _font(27, bold=True)
    body_font = _font(24)

    draw.text((92, 82), f"知识科普 · {style}", font=small_font, fill=ink)
    draw.text((width - 142, 82), f"{page_number:02d}", font=label_font, fill=accent)

    title = prompt.replace("生成一张3:4竖版中文知识科普图。", "").strip()
    title = title[:24] if title else "健康生活小知识"
    title_lines = _wrap_text(draw, title, title_font, width - 150)[:2]
    y = 190
    for line in title_lines:
        draw.text((74, y), line, font=title_font, fill=ink)
        y += 68

    draw.rounded_rectangle((72, y + 10, width - 72, y + 70), radius=18, fill=accent)
    draw.text((94, y + 24), "今天就把这个小习惯做好", font=label_font, fill="#FFFFFF")
    y += 110

    blocks = [
        "先观察自己的日常习惯",
        "减少反复摩擦和过度清洁",
        "饮食、饮水与活动一起调整",
        "持续不适时及时寻求专业评估",
    ]
    colors = ["#FCE7EF", "#E7F4EE", "#FFF2CC", "#E8EEFF"]
    for index, block in enumerate(blocks):
        top = y + index * 128
        draw.rounded_rectangle((66, top, width - 66, top + 104), radius=22, fill="#FFFFFF", outline="#E8DDE0", width=2)
        draw.ellipse((88, top + 25, 142, top + 79), fill=colors[index])
        draw.text((103, top + 37), str(index + 1), font=label_font, fill=ink)
        draw.text((162, top + 34), block, font=label_font, fill=ink)

    body_text = prompt[-260:] if len(prompt) > 260 else prompt
    body_lines = _wrap_text(draw, body_text, body_font, width - 150)
    draw.rounded_rectangle((66, height - 230, width - 66, height - 92), radius=24, fill="#FFFFFF", outline="#E8DDE0", width=2)
    for index, line in enumerate(body_lines[:5]):
        draw.text((92, height - 208 + index * 29), line, font=body_font, fill="#655D65")

    draw.text((72, height - 64), "本地演示图 · 用于测试完整生成流程", font=small_font, fill="#9A8E92")
    image.save(target, quality=95)
    return target


def _ffmpeg_executable() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def generate_mock_video(
    image_paths: list[str | Path],
    output_path: str | Path,
    *,
    duration: int = 5,
    width: int = 720,
    height: int = 1280,
) -> Path:
    paths = [Path(path) for path in image_paths if Path(path).exists()]
    if not paths:
        raise ValueError("至少需要一张图片才能生成演示视频。")

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _ffmpeg_executable()

    if len(paths) == 1:
        command = [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-i",
            str(paths[0]),
            "-vf",
            (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                f"zoompan=z='min(zoom+0.0015,1.12)':d={max(2, duration) * 30}:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps=30,"
                "format=yuv420p"
            ),
            "-t",
            str(max(2, duration)),
            "-c:v",
            "libx264",
            "-movflags",
            "+faststart",
            str(target),
        ]
    else:
        segment_duration = max(1.0, duration / len(paths))
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
            for path in paths:
                escaped = path.resolve().as_posix().replace("'", "'\\''")
                handle.write(f"file '{escaped}'\n")
                handle.write(f"duration {segment_duration:.3f}\n")
            escaped = paths[-1].resolve().as_posix().replace("'", "'\\''")
            handle.write(f"file '{escaped}'\n")
            concat_file = Path(handle.name)

        try:
            command = [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-vf",
                (
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                    "fps=30,format=yuv420p"
                ),
                "-t",
                str(max(2, duration)),
                "-c:v",
                "libx264",
                "-movflags",
                "+faststart",
                str(target),
            ]
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        finally:
            concat_file.unlink(missing_ok=True)
        return target

    subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return target


def create_video_thumbnail(video_path: str | Path, thumbnail_path: str | Path) -> Path:
    target = Path(thumbnail_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _ffmpeg_executable()
    command = [
        ffmpeg,
        "-y",
        "-ss",
        "0.5",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        "scale=540:-1",
        str(target),
    ]
    subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return target
