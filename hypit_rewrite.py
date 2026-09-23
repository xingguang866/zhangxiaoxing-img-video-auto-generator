from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Callable

from api_client import APIConfig, APIMartClient

from hypit_reference import (
    ReferenceProject,
    ReferenceWorkflowError,
    _workspace_relative,
    extract_audio,
    frames_for_duration,
    prepare_reference_workspace,
    reference_workspace,
    _unique_run_dir,
)
from hypit_service import environment, run_hypit


ProgressCallback = Callable[[str], None]
TTS_MODELS = ["gpt-4o-mini-tts", "tts-1", "tts-1-hd", "fishaudio-tts"]
TTS_VOICES = ["alloy", "coral", "nova", "shimmer", "echo", "fable", "onyx", "verse", "ballad", "ash", "sage"]


@dataclass(slots=True)
class RewriteOptions:
    model: str
    target_title: str = ""
    target_hook: str = ""
    originality_level: str = "中度改写"
    remove_ai_flavor: bool = True
    remove_promotional: bool = True
    tts_model: str = "gpt-4o-mini-tts"
    voice: str = "alloy"
    language: str = "zh"


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ReferenceWorkflowError("原创脚本模型没有返回 JSON 对象。")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ReferenceWorkflowError("原创脚本模型返回的 JSON 不是对象。")
    return value


def _source_text(project: ReferenceProject) -> str:
    transcript_path = project.run_dir / "analysis" / "transcript.json"
    transcript: dict[str, Any] = {}
    if transcript_path.exists():
        try:
            value = json.loads(transcript_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                transcript = value
        except (OSError, json.JSONDecodeError):
            transcript = {}
    text = str(transcript.get("text") or "").strip()
    if text:
        return text
    speech = project.analysis.get("speech") or {}
    if isinstance(speech, dict):
        return str(speech.get("text") or "").strip()
    return ""


def _fallback_rewrite(project: ReferenceProject, options: RewriteOptions) -> dict[str, Any]:
    source = _source_text(project)
    if not source:
        source = "参考视频没有可用口播，请先在 Studio 中补充原创脚本。"
    disclaimer = (
        "当前为演示模式或未配置文本模型，以下内容保留原意，未执行云端深度改写。"
    )
    return {
        "analysis_mode": "rewrite_fallback",
        "title": options.target_title.strip()
        or project.analysis.get("summary")
        or "原创口播视频",
        "hook": options.target_hook.strip()
        or (project.analysis.get("hook") or {}).get("proposed_text")
        or "先用一句话说清观众能得到什么",
        "full_script": source,
        "segments": [
            {
                "id": 1,
                "intent": "保留原始信息结构",
                "original": source,
                "rewritten": source,
                "visual_note": "沿用参考视频主要画面",
            }
        ],
        "note": disclaimer,
        "options": {
            "originality_level": options.originality_level,
            "remove_ai_flavor": options.remove_ai_flavor,
            "remove_promotional": options.remove_promotional,
        },
    }


def rewrite_script(
    project: ReferenceProject,
    options: RewriteOptions,
    *,
    api_key: str,
    base_url: str,
    mock_mode: bool,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    notify = progress or (lambda _message: None)
    source_text = _source_text(project)
    if mock_mode or not api_key.strip() or not options.model.strip():
        return _fallback_rewrite(project, options)

    notify(f"正在使用 {options.model} 做原创口播重写...")
    transcript_path = project.run_dir / "analysis" / "transcript.json"
    transcript_text = transcript_path.read_text(encoding="utf-8") if transcript_path.exists() else "{}"
    prompt = f"""
你是一名肛周健康科普短视频编导。请基于参考视频的结构和事实，重新创作一版中文口播。

硬性要求：
1. 保留有用的健康知识，不编造医学结论，不把普通护理建议写成治疗承诺。
2. 不能逐句照抄原稿，必须改变表达方式、句序和比喻。
3. 输出严格 JSON，不要 Markdown 代码块，不要 JSON 之外的文字。
4. 口播要像真人说话，适合直接交给 TTS，不使用括号动作说明。
5. 按原视频的段落结构重写，但允许合并或调整顺序。

真正创作目标：{options.target_title.strip() or "未填写"}
指定开头钩子：{options.target_hook.strip() or "未填写"}
改写强度：{options.originality_level}
去 AI 味：{"开启" if options.remove_ai_flavor else "关闭"}
去掉商业促销：{"开启" if options.remove_promotional else "关闭"}

去 AI 味要求：
- 避免“首先、其次、最后、总而言之、值得注意的是”等机械结构。
- 避免每句话长度相同，允许短句、停顿和自然口语。
- 不要堆砌排比，不要连续使用空洞形容词。

去商业促销要求：
- 不出现“赶紧买、限时、最低价、闭眼入、家人们冲”等带货话术。
- 不夸大产品功效，不替代就医建议。
- 如果原文提到产品，只保留真实使用场景和功能描述。

参考视频分析：
{json.dumps(project.analysis, ensure_ascii=False)[:30000]}

参考视频口播转写：
{transcript_text[:60000]}

返回 JSON 字段：
- title: 新视频标题
- hook: 新开头钩子，尽量控制在 3 秒内
- full_script: 完整配音文本
- segments: 数组，每项包含 id、intent、original、rewritten、visual_note
- note: 改写说明
""".strip()
    try:
        client = APIMartClient(APIConfig(base_url=base_url, api_key=api_key))
        raw = client.chat_completion(
            model=options.model,
            prompt=prompt,
            max_tokens=6000,
            temperature=0.7,
        )
        result = _extract_json_object(raw)
        result.setdefault("title", project.analysis.get("summary") or "原创口播视频")
        result.setdefault("hook", "")
        if options.target_title.strip():
            result["title"] = options.target_title.strip()
        if options.target_hook.strip():
            result["hook"] = options.target_hook.strip()
        result.setdefault("segments", [])
        full_script = str(result.get("full_script") or "").strip()
        if not full_script:
            full_script = "\n".join(
                str(item.get("rewritten") or "").strip()
                for item in result.get("segments", [])
                if isinstance(item, dict)
            ).strip()
        if not full_script:
            raise ReferenceWorkflowError("原创脚本为空。")
        result["full_script"] = full_script
        result["analysis_mode"] = "rewrite_multimodal"
        return result
    except Exception as exc:
        fallback = _fallback_rewrite(project, options)
        fallback["rewrite_error"] = str(exc)
        return fallback


def _split_text(text: str, limit: int = 3500) -> list[str]:
    sentences = re.split(r"(?<=[。！？!?；;])", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if current and len(current) + len(sentence) > limit:
            chunks.append(current)
            current = sentence
        else:
            current += sentence
    if current:
        chunks.append(current)
    return chunks or [text.strip()]


def _concat_wav_files(parts: list[Path], destination: Path) -> Path:
    if len(parts) == 1:
        shutil.copy2(parts[0], destination)
        return destination
    info = environment()
    if not info.ffmpeg:
        raise ReferenceWorkflowError("未找到 FFmpeg，无法拼接配音。")
    arguments = [info.ffmpeg, "-y"]
    for path in parts:
        arguments.extend(["-i", str(path)])
    streams = "".join(f"[{index}:a]" for index in range(len(parts)))
    arguments.extend(
        [
            "-filter_complex",
            f"{streams}concat=n={len(parts)}:v=0:a=1[out]",
            "-map",
            "[out]",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ]
    )
    result = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1200,
        shell=False,
    )
    if result.returncode != 0 or not destination.exists():
        raise ReferenceWorkflowError(
            result.stderr.strip() or "配音音频拼接失败。"
        )
    return destination


def synthesize_voiceover(
    project: ReferenceProject,
    script: str,
    options: RewriteOptions,
    *,
    destination: Path,
    api_key: str,
    base_url: str,
    mock_mode: bool,
    progress: ProgressCallback | None = None,
) -> Path:
    notify = progress or (lambda _message: None)
    if mock_mode or not api_key.strip():
        notify("演示模式：使用参考视频原音作为占位配音。")
        original_audio = extract_audio(
            project.media_path,
            destination=destination,
            progress=notify,
        )
        if original_audio:
            return original_audio
        info = environment()
        if not info.ffmpeg:
            raise ReferenceWorkflowError("未找到 FFmpeg，无法创建演示配音。")
        result = subprocess.run(
            [
                info.ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=mono",
                "-t",
                "1",
                "-c:a",
                "pcm_s16le",
                str(destination),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            shell=False,
        )
        if result.returncode != 0:
            raise ReferenceWorkflowError(result.stderr.strip() or "演示配音生成失败。")
        return destination

    chunks = _split_text(script)
    parts: list[Path] = []
    client = APIMartClient(APIConfig(base_url=base_url, api_key=api_key))
    for index, chunk in enumerate(chunks, start=1):
        notify(f"正在生成配音 {index}/{len(chunks)}...")
        content = client.synthesize_speech(
            text=chunk,
            model=options.tts_model,
            voice=options.voice,
            response_format="wav",
        )
        part = destination.parent / f"voice_part_{index:02d}.wav"
        part.write_bytes(content)
        parts.append(part)
    _concat_wav_files(parts, destination)
    for part in parts:
        part.unlink(missing_ok=True)
    return destination


def _probe_media(path: Path) -> dict[str, Any]:
    result = run_hypit(["media", "probe", str(path), "--json"], timeout=120)
    if result.returncode != 0:
        raise ReferenceWorkflowError(
            result.stderr.strip() or result.stdout.strip() or f"媒体探测失败：{path}"
        )
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ReferenceWorkflowError(f"媒体探测返回格式异常：{value}")
    return value


def transcribe_voiceover(
    audio_path: Path,
    *,
    language: str,
    api_key: str,
    base_url: str,
    mock_mode: bool,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    notify = progress or (lambda _message: None)
    if mock_mode or not api_key.strip():
        return {"available": False, "reason": "演示模式或未配置 API Key。"}
    notify("正在重新识别新配音的字幕时间点...")
    try:
        client = APIMartClient(APIConfig(base_url=base_url, api_key=api_key))
        result = client.transcribe_audio(audio_path, language=language)
        result["available"] = True
        return result
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


def _fallback_timed_segments(text: str, duration: float) -> list[dict[str, Any]]:
    pieces = [part.strip() for part in re.split(r"(?<=[。！？!?；;])", text) if part.strip()]
    if not pieces:
        pieces = [text.strip() or "原创口播"]
    total = sum(max(1, len(piece)) for piece in pieces)
    cursor = 0.0
    result: list[dict[str, Any]] = []
    for index, piece in enumerate(pieces):
        share = max(1, len(piece)) / total
        item_duration = duration * share if index < len(pieces) - 1 else duration - cursor
        result.append(
            {
                "id": index + 1,
                "start": cursor,
                "end": min(duration, cursor + item_duration),
                "text": piece,
            }
        )
        cursor += item_duration
    return result


def timed_segments(
    transcript: dict[str, Any],
    *,
    fallback_text: str,
    duration: float,
) -> list[dict[str, Any]]:
    segments = transcript.get("segments") if transcript.get("available") else None
    if isinstance(segments, list):
        result = []
        for index, segment in enumerate(segments, start=1):
            if not isinstance(segment, dict):
                continue
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            start = max(0.0, float(segment.get("start") or 0))
            end = max(start, float(segment.get("end") or start))
            result.append(
                {
                    "id": index,
                    "start": min(start, duration),
                    "end": min(max(end, start), duration),
                    "text": text,
                }
            )
        if result:
            return result
    return _fallback_timed_segments(fallback_text, duration)


def _write_srt(segments: list[dict[str, Any]], destination: Path) -> None:
    def timestamp(seconds: float) -> str:
        milliseconds = max(0, int(round(seconds * 1000)))
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, millis = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    lines: list[str] = []
    for index, segment in enumerate(segments, start=1):
        lines.extend(
            [
                str(index),
                f"{timestamp(float(segment['start']))} --> {timestamp(float(segment['end']))}",
                str(segment["text"]),
                "",
            ]
        )
    destination.write_text("\n".join(lines), encoding="utf-8")


def _frame_window(start: float, end: float, frame_rate: int = 30) -> tuple[int, int]:
    start_frame = max(0, int(start * frame_rate))
    end_frame = max(start_frame + 1, math.ceil(end * frame_rate))
    return start_frame, end_frame - start_frame


def generate_rewritten_project(
    project: ReferenceProject,
    rewrite: dict[str, Any],
    *,
    voiceover_path: Path,
    transcript: dict[str, Any],
    options: RewriteOptions,
    progress: ProgressCallback | None = None,
) -> ReferenceProject:
    notify = progress or (lambda _message: None)
    notify("正在生成原创口播 Hypit 工程...")
    workspace = project.workspace
    prepare_reference_workspace(workspace, progress=notify)
    run_dir = _unique_run_dir(workspace, f"{project.run_dir.name}_原创口播")
    assets = run_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    media_path = assets / project.media_path.name
    shutil.copy2(project.media_path, media_path)
    audio_path = assets / "voiceover.wav"
    shutil.copy2(voiceover_path, audio_path)

    video_probe = _probe_media(media_path)
    audio_probe = _probe_media(audio_path)
    video_duration = max(1.0, float(video_probe.get("duration") or 1))
    audio_duration = max(1.0, float(audio_probe.get("duration") or 1))
    duration_frames = frames_for_duration(max(video_duration, audio_duration))
    duration = duration_frames / 30
    width, height = 720, 1280
    title = str(rewrite.get("title") or "原创口播视频").strip()
    hook = str(rewrite.get("hook") or title).strip()
    full_script = str(rewrite.get("full_script") or "").strip()
    segments = timed_segments(
        transcript,
        fallback_text=full_script,
        duration=duration,
    )
    title_frames = min(90, duration_frames)

    rewrite_path = run_dir / "rewrite.json"
    rewrite_path.write_text(
        json.dumps(rewrite, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "script.txt").write_text(full_script + "\n", encoding="utf-8")
    _write_srt(segments, run_dir / "captions.srt")

    subtitle_areas: list[str] = []
    for index, segment in enumerate(segments, start=1):
        at_frame, item_frames = _frame_window(
            float(segment["start"]),
            float(segment["end"]),
        )
        subtitle_areas.append(
            f"""    <typo:Area id="subtitle-{index}"
      placement={{subtitle-frame}} style={{subtitle-style}}
      at="{at_frame}f" for="{item_frames}f">
      <typo:P>{escape(str(segment["text"]))}</typo:P>
    </typo:Area>"""
        )

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
  <import as="audio" from="@hypit/audio-track@1"/>
  <import as="text" from="@hypit/text@1"/>
  <import as="fonts" from="@hypit/fonts-open@1"/>
  <import as="typo" from="@hypit/typography-track@1"/>
  <import as="film" from="@hypit/film@1"/>
  <import as="render" from="@hypit/render-hyperframes@1"/>
  <import as="look" source="./look.svs"/>

  <asset:Video id="reference" src="./assets/{media_path.name}"/>
  <asset:Audio id="voiceover" src="./assets/voiceover.wav"/>
  <program:Clock id="clock" frame-rate="30"/>
  <space:Canvas id="canvas" width="{width}" height="{height}"/>
  <space:Frame id="full" within={{canvas}}
    left="0%" top="0%" right="100%" bottom="100%"/>
  <space:Frame id="hook-frame" within={{canvas}}
    left="8%" top="10%" right="92%" bottom="34%"/>
  <space:Frame id="subtitle-frame" within={{canvas}}
    left="7%" top="68%" right="93%" bottom="90%"/>
  <pipeline:Normalize id="reference-media" source={{reference}} clock={{clock}}
    video="primary-moving" audio="none" span-authority="video"/>
  <pipeline:Normalize id="voice-media" source={{voiceover}} clock={{clock}}
    video="none" audio="default" span-authority="audio"/>
  <time:Timeline id="program" clock={{clock}} end="{duration_frames}f"/>

  <media-track:Track id="reference-track" timeline={{program.timeline}} canvas={{canvas}}>
    <media-track:Item id="reference-item" media={{reference-media.media}}
      frame={{full}} during="program"
      appearance={{look.media.reference-loop}}/>
  </media-track:Track>

  <audio:Track id="voice-track" timeline={{program.timeline}}>
    <audio:Item id="voiceover" source={{voice-media.media}}
      during="program" playback="once"/>
  </audio:Track>

  <text:Value id="hook-copy">{escape(hook)}</text:Value>
  <fonts:Stack id="title-font" family="noto-sans-sc" weight="700" style="normal"/>
  <typo:Style id="title-style" recipe={{look.text.title}} font={{title-font}}>
    <typo:Fill color="#FFFFFF"/>
    <typo:Stroke color="#1F1720" width="5" placement="outside"/>
  </typo:Style>
  <typo:Style id="subtitle-style" recipe={{look.text.subtitle}} font={{title-font}}>
    <typo:Fill color="#FFFFFF"/>
    <typo:Stroke color="#1F1720" width="4" placement="outside"/>
  </typo:Style>
  <typo:Track id="titles" timeline={{program.timeline}}>
    <typo:Area id="hook-title" content={{hook-copy}} placement={{hook-frame}}
      style={{title-style}} at="0f" for="{title_frames}f"/>
  </typo:Track>
  <typo:Track id="subtitles" timeline={{program.timeline}}>
{chr(10).join(subtitle_areas)}
  </typo:Track>

  <film:Film id="main" canvas={{canvas}} timeline={{program.timeline}}
    appearance={{look.film.main}}>
    <film:Track source={{reference-track.visual}}/>
    <film:Track source={{voice-track.audio}}/>
    <film:Track source={{titles.track}}/>
    <film:Track source={{subtitles.track}}/>
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
  media.reference-loop { stack-order: 0; fit: cover; playback: loop-start; }
  text.title { size: 64; weight: 700; stack-order: 20; }
  text.subtitle { size: 42; weight: 650; stack-order: 30; }
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
                f"- 原创开头钩子：{hook}",
                f"- 改写强度：{options.originality_level}",
                f"- 去 AI 味：{'是' if options.remove_ai_flavor else '否'}",
                f"- 去商业促销：{'是' if options.remove_promotional else '否'}",
                f"- 配音模型：{options.tts_model}",
                f"- 配音音色：{options.voice}",
                "",
                "## 新口播",
                full_script,
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = run_hypit(
        ["check", _workspace_relative(svrun_path, workspace)],
        cwd=workspace,
        timeout=300,
    )
    if result.returncode != 0:
        raise ReferenceWorkflowError(
            result.stderr.strip() or result.stdout.strip() or "原创口播工程校验失败。"
        )

    return ReferenceProject(
        workspace=workspace,
        run_dir=run_dir,
        media_path=media_path,
        analysis_path=rewrite_path,
        svml_path=svml_path,
        svrun_path=svrun_path,
        output_path=run_dir / "output" / "final.mp4",
        analysis=rewrite,
    )


def run_originality_workflow(
    project: ReferenceProject,
    options: RewriteOptions,
    *,
    api_key: str,
    base_url: str,
    mock_mode: bool,
    progress: ProgressCallback | None = None,
) -> ReferenceProject:
    notify = progress or (lambda _message: None)
    rewrite = rewrite_script(
        project,
        options,
        api_key=api_key,
        base_url=base_url,
        mock_mode=mock_mode,
        progress=notify,
    )
    run_dir = _unique_run_dir(project.workspace, f"{project.run_dir.name}_配音准备")
    destination = run_dir / "assets" / "voiceover.wav"
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        voiceover = synthesize_voiceover(
            project,
            str(rewrite.get("full_script") or ""),
            options,
            destination=destination,
            api_key=api_key,
            base_url=base_url,
            mock_mode=mock_mode,
            progress=notify,
        )
        transcript = transcribe_voiceover(
            voiceover,
            language=options.language,
            api_key=api_key,
            base_url=base_url,
            mock_mode=mock_mode,
            progress=notify,
        )
        generated = generate_rewritten_project(
            project,
            rewrite,
            voiceover_path=voiceover,
            transcript=transcript,
            options=options,
            progress=notify,
        )
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
    return generated
