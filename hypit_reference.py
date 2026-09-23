from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from api_client import APIClientError, APIConfig, APIMartClient
from hypit_service import (
    HYPIT_PROJECTS,
    configure_local_profile,
    environment,
    initialize_project,
    run_hypit,
)


ProgressCallback = Callable[[str], None]
DEFAULT_WORKSPACE_NAME = "张小星Hypit工作台"
SUPPORTED_REFERENCE_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


class ReferenceWorkflowError(RuntimeError):
    pass


@dataclass(slots=True)
class ReferenceProject:
    workspace: Path
    run_dir: Path
    media_path: Path
    analysis_path: Path
    svml_path: Path
    svrun_path: Path
    output_path: Path
    analysis: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for key, value in list(result.items()):
            if isinstance(value, Path):
                result[key] = str(value)
        return result


def _safe_name(value: str, fallback: str = "参考视频") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    cleaned = re.sub(r"\s+", "_", cleaned)
    return (cleaned or fallback)[:60]


def reference_workspace(name: str = DEFAULT_WORKSPACE_NAME) -> Path:
    workspace = HYPIT_PROJECTS / _safe_name(name, "张小星Hypit工作台")
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def prepare_reference_workspace(
    workspace: Path,
    *,
    progress: ProgressCallback | None = None,
) -> Path:
    notify = progress or (lambda _message: None)
    workspace.mkdir(parents=True, exist_ok=True)
    profile = workspace / "hypit.runtime.json"
    if not profile.exists():
        notify("正在初始化 Hypit 工作台...")
        result = initialize_project(workspace)
        if result.returncode != 0:
            raise ReferenceWorkflowError(
                result.stderr.strip()
                or result.stdout.strip()
                or "Hypit Runtime 初始化失败。"
            )
    notify("正在检查中文字体组件...")
    font_result = run_hypit(
        [
            "packages",
            "install",
            "@fontsource-variable/noto-sans-sc@5.3.0",
        ],
        cwd=workspace,
        timeout=1800,
    )
    if font_result.returncode != 0:
        raise ReferenceWorkflowError(
            font_result.stderr.strip()
            or font_result.stdout.strip()
            or "中文字体组件安装失败。"
        )
    notify("正在准备本地渲染组件...")
    configure_local_profile(workspace)
    return workspace


def _unique_run_dir(workspace: Path, title: str) -> Path:
    runs_root = workspace / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    base = f"{datetime.now():%Y%m%d_%H%M%S}_{_safe_name(title)}"
    candidate = runs_root / base
    suffix = 2
    while candidate.exists():
        candidate = runs_root / f"{base}_{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def _workspace_relative(path: Path, workspace: Path) -> str:
    return path.resolve().relative_to(workspace.resolve()).as_posix()


def _run_json(
    arguments: list[str],
    *,
    workspace: Path,
    timeout: int,
) -> dict[str, Any]:
    result = run_hypit(arguments, cwd=workspace, timeout=timeout)
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode != 0:
        raise ReferenceWorkflowError(output or f"Hypit 命令执行失败：{' '.join(arguments)}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReferenceWorkflowError(f"Hypit 未返回有效 JSON：{output}") from exc
    if not isinstance(payload, dict):
        raise ReferenceWorkflowError(f"Hypit JSON 结构异常：{payload}")
    return payload


def resolve_reference_video(
    *,
    workspace: Path,
    run_dir: Path,
    source_file: str = "",
    source_url: str = "",
    progress: ProgressCallback | None = None,
) -> Path:
    notify = progress or (lambda _message: None)
    assets = run_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    source_url = source_url.strip()
    source_file = source_file.strip()

    if source_file:
        source = Path(source_file)
        if not source.exists():
            raise ReferenceWorkflowError(f"本地参考视频不存在：{source}")
        if source.suffix.lower() not in SUPPORTED_REFERENCE_SUFFIXES:
            raise ReferenceWorkflowError("参考视频仅支持 MP4、MOV、MKV、WEBM 或 M4V。")
        notify("正在复制本地参考视频...")
        destination = assets / f"reference{source.suffix.lower()}"
        shutil.copy2(source, destination)
        return destination

    if not source_url:
        raise ReferenceWorkflowError("请填写视频链接或选择本地 MP4 文件。")

    notify("正在准备视频下载组件...")
    prepare = run_hypit(["media", "prepare-fetch"], cwd=workspace, timeout=1800)
    if prepare.returncode != 0:
        raise ReferenceWorkflowError(
            prepare.stderr.strip()
            or prepare.stdout.strip()
            or "yt-dlp 下载组件准备失败。"
        )

    destination = assets / "reference.mp4"
    notify("正在下载参考视频...")
    result = run_hypit(
        ["media", "fetch", source_url, "--to", _workspace_relative(destination, workspace), "--json"],
        cwd=workspace,
        timeout=1800,
    )
    if result.returncode != 0:
        raise ReferenceWorkflowError(
            result.stderr.strip()
            or result.stdout.strip()
            or "参考视频下载失败。"
        )
    if not destination.exists():
        raise ReferenceWorkflowError("下载完成，但没有找到参考视频文件。")
    return destination


def extract_reference_evidence(
    media_path: Path,
    *,
    workspace: Path,
    run_dir: Path,
    progress: ProgressCallback | None = None,
) -> tuple[dict[str, Any], list[Path]]:
    notify = progress or (lambda _message: None)
    relative_media = _workspace_relative(media_path, workspace)
    notify("正在读取视频时长、分辨率和音轨信息...")
    probe = _run_json(
        ["media", "probe", relative_media, "--json"],
        workspace=workspace,
        timeout=120,
    )
    notify("正在识别镜头切换和节奏变化...")
    boundaries = _run_json(
        ["media", "boundaries", relative_media, "--json"],
        workspace=workspace,
        timeout=180,
    )

    duration = float(probe.get("duration") or 0)
    interval = max(1.0, duration / 6) if duration else 2.0
    frames_dir = run_dir / "analysis" / "frames"
    notify("正在提取关键画面...")
    tiles = _run_json(
        [
            "media",
            "tiles",
            relative_media,
            "--every",
            f"{interval:.3f}",
            "--cell",
            "480",
            "--columns",
            "3",
            "--to",
            _workspace_relative(frames_dir, workspace),
            "--json",
        ],
        workspace=workspace,
        timeout=600,
    )
    frame_paths = [
        Path(str(item.get("path")))
        for item in tiles.get("grids", [])
        if isinstance(item, dict) and item.get("path")
    ]
    evidence = {
        "probe": probe,
        "boundaries": boundaries,
        "frame_grids": frame_paths[:6],
    }
    return evidence, frame_paths[:6]


def extract_audio(
    media_path: Path,
    *,
    destination: Path,
    progress: ProgressCallback | None = None,
) -> Path | None:
    info = environment()
    if not info.ffmpeg:
        return None
    notify = progress or (lambda _message: None)
    notify("正在提取参考视频口播音轨...")
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            info.ffmpeg,
            "-y",
            "-i",
            str(media_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        shell=False,
    )
    if result.returncode != 0 or not destination.exists():
        return None
    if destination.stat().st_size > 25 * 1024 * 1024:
        destination.unlink(missing_ok=True)
        return None
    return destination


def transcribe_reference(
    audio_path: Path | None,
    *,
    language: str,
    api_key: str,
    base_url: str,
    mock_mode: bool,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    if not audio_path:
        return {"available": False, "reason": "未检测到可用音轨。"}
    if mock_mode or not api_key.strip():
        return {"available": False, "reason": "未配置 APIB API Key，跳过口播转写。"}
    notify = progress or (lambda _message: None)
    notify("正在识别口播和字幕时间点...")
    try:
        client = APIMartClient(APIConfig(base_url=base_url, api_key=api_key))
        payload = client.transcribe_audio(audio_path, language=language)
    except Exception as exc:
        return {"available": False, "reason": str(exc)}
    payload["available"] = True
    return payload


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ReferenceWorkflowError("分析模型没有返回 JSON 对象。")
    payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ReferenceWorkflowError("分析模型返回的 JSON 不是对象。")
    return payload


def _image_data_uri(path: Path) -> str:
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((1800, 1800))
        from io import BytesIO

        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=82, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def heuristic_analysis(
    *,
    title: str,
    hook: str,
    language: str,
    aspect_ratio: str,
    evidence: dict[str, Any],
    transcript: dict[str, Any],
) -> dict[str, Any]:
    probe = evidence.get("probe") or {}
    candidates = (evidence.get("boundaries") or {}).get("candidates") or []
    segments = transcript.get("segments") if transcript.get("available") else []
    if not isinstance(segments, list):
        segments = []
    return {
        "analysis_mode": "heuristic",
        "summary": title or "参考视频二次创作工程",
        "hook": {
            "proposed_text": hook or title or "前三秒改用问题式开场",
            "duration_seconds": min(3, float(probe.get("duration") or 3)),
            "analysis": "已建立可替换的前三秒钩子区域，建议结合原视频开场人工复核。",
        },
        "speech": {
            "language": language,
            "available": bool(transcript.get("available")),
            "text": transcript.get("text", ""),
            "segments": segments,
            "note": transcript.get("reason", ""),
        },
        "subtitle_style": {
            "recommendation": "按语义分段显示，每行不超过 14 个汉字，关键动作跟随口播时间。",
            "source": "待多模态模型或 Studio 精调",
        },
        "rhythm": {
            "duration_seconds": probe.get("duration"),
            "shot_change_candidates": candidates,
            "assessment": (
                f"检测到 {len(candidates)} 个机械镜头变化候选点。"
                if candidates
                else "没有检测到明显硬切，需要人工或多模态模型判断节奏。"
            ),
        },
        "broll": [
            "根据每个镜头变化点准备对应的 B-roll 素材。",
            "商品细节优先使用真实产品实拍，不使用生成模型改写包装文字。",
        ],
        "transitions": (
            ["参考视频存在机械镜头变化点，可在 Studio 中逐点确认切镜或转场。"]
            if candidates
            else ["暂未识别到明确硬切，保留连续镜头并在 Studio 中调整。"]
        ),
        "visual_effects": [
            "待接入多模态模型分析字幕、字卡、动画、滤镜和商品演示。",
        ],
        "editable_project": {
            "aspect_ratio": aspect_ratio,
            "base_media": probe.get("path", ""),
            "next_step": "打开 Studio，基于参考视频时间轴拆解镜头并替换为原创素材。",
        },
    }


def analyze_reference(
    *,
    title: str,
    hook: str,
    language: str,
    aspect_ratio: str,
    analysis_model: str,
    api_key: str,
    base_url: str,
    mock_mode: bool,
    evidence: dict[str, Any],
    frame_paths: list[Path],
    transcript: dict[str, Any],
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    notify = progress or (lambda _message: None)
    if mock_mode or not api_key.strip() or not analysis_model.strip():
        return heuristic_analysis(
            title=title,
            hook=hook,
            language=language,
            aspect_ratio=aspect_ratio,
            evidence=evidence,
            transcript=transcript,
        )

    notify(f"正在使用 {analysis_model} 分析画面、节奏和表达结构...")
    probe = evidence.get("probe") or {}
    boundaries = evidence.get("boundaries") or {}
    transcript_text = json.dumps(transcript, ensure_ascii=False)[:60000]
    prompt = f"""
你是一名短视频结构分析师。请分析参考视频的关键画面、口播时间、镜头变化和节奏，
输出一个严格 JSON 对象，不要使用 Markdown 代码块，也不要补充 JSON 之外的文字。

用户主题：{title or "未填写"}
计划开头钩子：{hook or "未填写"}
语言：{language}
目标比例：{aspect_ratio}
视频信息：{json.dumps(probe, ensure_ascii=False)}
机械镜头变化：{json.dumps(boundaries, ensure_ascii=False)}
口播转写：{transcript_text}

JSON 必须包含以下字段：
- analysis_mode: "multimodal"
- summary: 视频内容与表达结构摘要
- hook: 对象，包含 proposed_text、duration_seconds、analysis
- speech: 对象，包含 language、available、text、segments、delivery_summary
- subtitle_style: 对象，包含 recommendation、timing_notes、visual_notes
- rhythm: 对象，包含 duration_seconds、shot_change_candidates、pacing、assessment
- broll: 字符串数组，说明每个主要段落适合的 B-roll
- transitions: 字符串数组，逐段判断切镜、转场与镜头运动
- visual_effects: 字符串数组，判断字幕、字卡、MG、贴纸、滤镜和商品演示
- editable_project: 对象，包含 aspect_ratio、base_media、recommended_scenes、next_step

分析时请区分“画面直接可见的证据”和“推断”。不要声称读取了音频里没有的词语。
""".strip()
    try:
        client = APIMartClient(APIConfig(base_url=base_url, api_key=api_key))
        raw = client.multimodal_response(
            model=analysis_model,
            prompt=prompt,
            image_data_uris=[_image_data_uri(path) for path in frame_paths if path.exists()],
        )
        analysis = _extract_json_object(raw)
        analysis["analysis_mode"] = "multimodal"
        return analysis
    except (APIClientError, ReferenceWorkflowError, OSError, ValueError) as exc:
        fallback = heuristic_analysis(
            title=title,
            hook=hook,
            language=language,
            aspect_ratio=aspect_ratio,
            evidence=evidence,
            transcript=transcript,
        )
        fallback["analysis_error"] = str(exc)
        return fallback


def _canvas_for(aspect_ratio: str) -> tuple[int, int]:
    if aspect_ratio == "16:9":
        return 1280, 720
    if aspect_ratio == "1:1":
        return 1080, 1080
    return 720, 1280


def align_duration_to_frame(seconds: float, frame_rate: int = 30) -> float:
    if frame_rate <= 0:
        raise ValueError("frame_rate must be positive")
    return max(1, int(float(seconds) * frame_rate)) / frame_rate


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _write_srt(transcript: dict[str, Any], destination: Path) -> None:
    segments = transcript.get("segments")
    if not isinstance(segments, list):
        return
    lines: list[str] = []
    for index, segment in enumerate(segments, start=1):
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment.get("start") or 0)
        end = float(segment.get("end") or start)
        lines.extend(
            [
                str(index),
                f"{_srt_time(start)} --> {_srt_time(end)}",
                text,
                "",
            ]
        )
    if lines:
        destination.write_text("\n".join(lines), encoding="utf-8")


def generate_reference_project(
    *,
    workspace: Path,
    media_path: Path,
    title: str,
    hook: str,
    language: str,
    aspect_ratio: str,
    analysis: dict[str, Any],
    evidence: dict[str, Any],
    transcript: dict[str, Any],
    progress: ProgressCallback | None = None,
) -> ReferenceProject:
    notify = progress or (lambda _message: None)
    run_dir = media_path.parent.parent
    analysis_dir = run_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    title = title.strip() or "参考视频二次创作"
    hook = hook.strip() or title
    raw_duration = max(1.0, float((evidence.get("probe") or {}).get("duration") or 1.0))
    duration = align_duration_to_frame(raw_duration)
    has_audio = bool((evidence.get("probe") or {}).get("hasAudio"))
    width, height = _canvas_for(aspect_ratio)
    title_duration = min(3.0, duration)
    title_text = escape(hook)

    analysis_path = analysis_dir / "analysis.json"
    analysis_path.write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    transcript_path = analysis_dir / "transcript.json"
    transcript_path.write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_srt(transcript, analysis_dir / "subtitles.srt")

    svml_path = run_dir / "main.svml"
    svml_path.write_text(
        f"""<?svml using="@hypit/markup@1"?>
<svml>
  <import as="asset" from="@hypit/media@1"/>
  <import as="pipeline" from="@hypit/media-pipeline@1"/>
  <import as="program" from="@hypit/program-space@1"/>
  <import as="time" from="@hypit/timeline-author@1"/>
  <import as="space" from="@hypit/spatial@1"/>
  <import as="media-track" from="@hypit/media-track@1"/>
  <import as="text" from="@hypit/text@1"/>
  <import as="fonts" from="@hypit/fonts-open@1"/>
  <import as="typo" from="@hypit/typography-track@1"/>
  <import as="film" from="@hypit/film@1"/>
  <import as="render" from="@hypit/render-hyperframes@1"/>
  <import as="look" source="./look.svs"/>

  <asset:Video id="reference" src="./assets/{media_path.name}"/>
  <program:Clock id="clock" frame-rate="30"/>
  <space:Canvas id="canvas" width="{width}" height="{height}"/>
  <space:Frame id="full" within={{canvas}}
    left="0%" top="0%" right="100%" bottom="100%"/>
  <space:Frame id="hook-frame" within={{canvas}}
    left="8%" top="10%" right="92%" bottom="34%"/>
  <pipeline:Normalize id="reference-media" source={{reference}} clock={{clock}}
    video="primary-moving" audio="{'default' if has_audio else 'none'}" span-authority="video"/>
  <time:Timeline id="program" clock={{clock}} end="{duration:.3f}s"/>

  <media-track:Track id="reference-track" timeline={{program.timeline}} canvas={{canvas}}>
    <media-track:Item id="reference-item" media={{reference-media.media}}
      frame={{full}} during="program"{' source-audio="content"' if has_audio else ''}
      appearance={{look.media.reference}}/>
  </media-track:Track>

  <text:Value id="hook-copy">{title_text}</text:Value>
  <fonts:Stack id="title-font" family="noto-sans-sc" weight="700" style="normal"/>
  <typo:Style id="title-style" recipe={{look.text.title}} font={{title-font}}>
    <typo:Fill color="#FFFFFF"/>
    <typo:Stroke color="#1F1720" width="5" placement="outside"/>
  </typo:Style>
  <typo:Track id="titles" timeline={{program.timeline}}>
    <typo:Area id="hook-title" content={{hook-copy}} placement={{hook-frame}}
      style={{title-style}} at="0s" for="{title_duration:.3f}s"/>
  </typo:Track>

  <film:Film id="main" canvas={{canvas}} timeline={{program.timeline}}
    appearance={{look.film.main}}>
    <film:Track source={{reference-track.visual}}/>
    {('<film:Track source={reference-track.audio}/>' if has_audio else '')}
    <film:Track source={{titles.track}}/>
  </film:Film>
  <render:Video id="final" composition={{main.composition}} timeline={{program.timeline}}/>
</svml>
""",
        encoding="utf-8",
    )
    (run_dir / "look.svs").write_text(
        """<?svml using="@hypit/svs@1"?>
<sheet version="1">
  film.main { background: #121014; }
  media.reference { stack-order: 0; fit: cover; }
  text.title { size: 64; weight: 700; stack-order: 20; }
</sheet>
""",
        encoding="utf-8",
    )
    svrun_path = run_dir / "project.svrun"
    svrun_path.write_text(
        """<?svml using="@hypit/run-markup@1"?>
<svrun version="1">
  <author source="./main.svml"/>
  <target output="final.video"/>
</svrun>
""",
        encoding="utf-8",
    )
    (run_dir / "BRIEF.md").write_text(
        "\n".join(
            [
                f"# {title}",
                "",
                f"- 开头钩子：{hook}",
                f"- 语言：{language}",
                f"- 画面比例：{aspect_ratio}",
                f"- 参考视频：`{media_path.name}`",
                f"- 分析文件：`{analysis_path.relative_to(run_dir).as_posix()}`",
                f"- 字幕文件：`{(analysis_dir / 'subtitles.srt').relative_to(run_dir).as_posix()}`",
                "",
                "## 下一步",
                "1. 打开 Studio 检查时间线。",
                "2. 按镜头段落替换人物、商品和背景。",
                "3. 在 Studio 中调整字幕、字卡、MG 和转场。",
                "4. 完成后重新 Build 导出最终视频。",
                "",
            ]
        ),
        encoding="utf-8",
    )

    notify("正在校验可编辑工程...")
    result = run_hypit(
        ["check", _workspace_relative(svrun_path, workspace)],
        cwd=workspace,
        timeout=300,
    )
    if result.returncode != 0:
        raise ReferenceWorkflowError(
            result.stderr.strip()
            or result.stdout.strip()
            or "生成的 Hypit 工程校验失败。"
        )

    return ReferenceProject(
        workspace=workspace,
        run_dir=run_dir,
        media_path=media_path,
        analysis_path=analysis_path,
        svml_path=svml_path,
        svrun_path=svrun_path,
        output_path=run_dir / "output" / "final.mp4",
        analysis=analysis,
    )


def run_reference_workflow(
    *,
    title: str,
    hook: str,
    source_file: str,
    source_url: str,
    language: str,
    aspect_ratio: str,
    analysis_model: str,
    api_key: str,
    base_url: str,
    mock_mode: bool,
    workspace_name: str = DEFAULT_WORKSPACE_NAME,
    progress: ProgressCallback | None = None,
) -> ReferenceProject:
    notify = progress or (lambda _message: None)
    workspace = reference_workspace(workspace_name)
    prepare_reference_workspace(workspace, progress=notify)
    run_dir = _unique_run_dir(workspace, title or Path(source_file).stem or "参考视频")
    media_path = resolve_reference_video(
        workspace=workspace,
        run_dir=run_dir,
        source_file=source_file,
        source_url=source_url,
        progress=notify,
    )
    evidence, frame_paths = extract_reference_evidence(
        media_path,
        workspace=workspace,
        run_dir=run_dir,
        progress=notify,
    )
    audio_path = None
    if bool((evidence.get("probe") or {}).get("hasAudio")):
        audio_path = extract_audio(
            media_path,
            destination=run_dir / "analysis" / "speech.wav",
            progress=notify,
        )
    transcript = transcribe_reference(
        audio_path,
        language=language,
        api_key=api_key,
        base_url=base_url,
        mock_mode=mock_mode,
        progress=notify,
    )
    analysis = analyze_reference(
        title=title,
        hook=hook,
        language=language,
        aspect_ratio=aspect_ratio,
        analysis_model=analysis_model,
        api_key=api_key,
        base_url=base_url,
        mock_mode=mock_mode,
        evidence=evidence,
        frame_paths=frame_paths,
        transcript=transcript,
        progress=notify,
    )
    return generate_reference_project(
        workspace=workspace,
        media_path=media_path,
        title=title,
        hook=hook,
        language=language,
        aspect_ratio=aspect_ratio,
        analysis=analysis,
        evidence=evidence,
        transcript=transcript,
        progress=notify,
    )


def build_reference_project(
    project: ReferenceProject,
    *,
    progress: ProgressCallback | None = None,
) -> Path:
    notify = progress or (lambda _message: None)
    notify("正在编译并渲染参考视频工程...")
    result = run_hypit(
        [
            "build",
            _workspace_relative(project.svrun_path, project.workspace),
            "--follow",
        ],
        cwd=project.workspace,
        timeout=7200,
    )
    output = "\n".join(
        part for part in (result.stdout, result.stderr) if part
    ).strip()
    if result.returncode != 0:
        raise ReferenceWorkflowError(output or "Hypit Build 执行失败。")
    match = re.search(r"\bbld_[A-Za-z0-9_-]+\b", output)
    if not match:
        raise ReferenceWorkflowError(f"Build 完成，但没有找到 Build ID。\n{output}")
    build_id = match.group(0)
    notify(f"正在导出成片：{build_id}")
    project.output_path.parent.mkdir(parents=True, exist_ok=True)
    export = run_hypit(
        [
            "get",
            build_id,
            "--output",
            "final.video",
            "--to",
            str(project.output_path),
        ],
        cwd=project.workspace,
        timeout=1800,
    )
    if export.returncode != 0 or not project.output_path.exists():
        raise ReferenceWorkflowError(
            export.stderr.strip()
            or export.stdout.strip()
            or "Build 成功，但成片导出失败。"
        )
    return project.output_path
