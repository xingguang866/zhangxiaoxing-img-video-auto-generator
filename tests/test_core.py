from __future__ import annotations

import os
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFilter
from playwright.sync_api import sync_playwright

from api_client import APIClientError, APIConfig, APIMartClient
from app import (
    FALLBACK_MODEL_CATALOG,
    HotRewriteThread,
    MainWindow,
    build_batch_image_jobs,
    build_manual_image_items,
    count_chinese_characters,
)
from batch_parser import BatchItem, create_batch_template, load_batch_items
from batch_publish_ui import BatchPublishQueueDialog
from browser_assistant import (
    _append_douyin_tags,
    _commit_input_value,
    _configure_douyin_schedule,
    _configure_xiaohongshu_schedule,
    _find_xiaohongshu_publish_button,
    _fill_field,
    _fill_tiptap_body,
    _open_douyin_music_panel,
    _read_douyin_music_items,
    _upload_files,
    _verify_douyin_fields,
    _wait_for_xiaohongshu_publish_result,
    read_douyin_hot_music,
    select_douyin_music,
)
from jianying_service import create_jianying_draft
from crebee_client import (
    CreBeeError,
    account_identifier,
    account_label,
    build_publish_payload,
    parse_accounts,
    summarize_publish_result,
)
from hypit_service import (
    environment as hypit_environment,
    hypit_version,
    install_apib_provider,
    run_hypit,
)
from hypit_reference import (
    ReferenceWorkflowError,
    ReferenceProject,
    align_duration_to_frame,
    build_reference_project,
    frames_for_duration,
    heuristic_analysis,
    is_douyin_url,
    normalize_reference_url,
    reference_workspace,
    run_reference_workflow,
    select_best_browser_media,
)
from hypit_rewrite import (
    RewriteOptions,
    emotion_instruction,
    run_originality_workflow,
)
from hypit_tutorial import ASSET_DIR, build_tutorial_html
from hot_content import (
    HotContentItem,
    OriginalContent,
    analyze_reference_images,
    build_image_analysis_prompt,
    build_image_copy_prompt,
    build_rewrite_prompt,
    export_original_content_excel,
    extract_image_texts,
    generate_image_copy_plan,
    generate_original_content,
    mock_original_content,
    normalize_image_copy_payload,
    shuffle_tags,
    strip_signature_lines,
)
from image_reference import (
    build_reference_instruction,
    max_reference_images_for_model,
    to_data_uri,
    validate_reference_image,
)
from mock_engine import create_video_thumbnail, generate_mock_image, generate_mock_video
from pricing_utils import format_pricing, format_usage
from publish_platforms import PLATFORMS, build_platform_posts
from publish_drafts import PublishDraft, new_publish_draft_id
from publish_batch import (
    PUBLISH_SCHEDULE_DISPLAY_FORMAT,
    apply_publish_metadata,
    create_publish_batch_template,
    dedupe_publish_drafts,
    discover_publish_drafts,
    load_publish_drafts_from_excel,
    load_publish_metadata_from_excel,
    publish_draft_validation_tooltip,
    validate_publish_draft,
)
from publish_queue import PublishQueueEntry, evenly_arranged_entries, run_publish_queue
from prompts import STYLE_PROMPTS, build_cover_prompt, build_image_prompt
from xhs_collector import (
    _extract_detail,
    _extract_visible_cards,
    _login_required,
    clean_note_title,
    download_note_images,
    is_flat_placeholder,
    is_low_information_image,
    is_note_image_url,
    is_useful_note_image,
    normalize_xhs_url,
)


def _note_like_png(width: int = 900, height: int = 1200) -> bytes:
    """构造一张多色图片，避免被“纯色占位图”规则过滤。"""
    buffer = BytesIO()
    with Image.new("RGB", (width, height), "#F6D9E0") as image:
        draw = ImageDraw.Draw(image)
        draw.rectangle((60, 80, width - 60, 420), fill="#FFFFFF")
        draw.rectangle((60, 520, width - 60, height - 80), fill="#F45D8D")
        draw.ellipse((120, 160, 320, 360), fill="#2B6CB0")
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def _blob_png(width: int = 1080, height: int = 1440) -> bytes:
    """构造一张无文字、无内容的模糊色块图（爆款里的装饰性废图）。"""
    buffer = BytesIO()
    with Image.new("RGB", (width, height), "#FCFBFD") as canvas:
        layer = Image.new("RGB", (width, height), "#FCFBFD")
        ImageDraw.Draw(layer).ellipse(
            (width * 0.33, height * 0.36, width * 0.67, height * 0.65),
            fill="#9FE3DC",
        )
        blob = Image.blend(
            canvas,
            layer.filter(ImageFilter.GaussianBlur(24)),
            0.85,
        )
        blob.save(buffer, format="PNG")
    return buffer.getvalue()


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_style_count_and_prompt(self):
        self.assertEqual(len(STYLE_PROMPTS), 12)
        prompt = build_image_prompt("如厕习惯", "1、减少反复摩擦；2、保持干爽", "清新治愈")
        self.assertIn("如厕习惯", prompt)
        self.assertIn("清新治愈", prompt)
        self.assertIn("3:4竖版", prompt)
        cover = build_cover_prompt("久坐护理", "久坐党别忽略这件事", "三个习惯现在就改", "手绘卡通")
        self.assertIn("封面主标题", cover)
        self.assertIn("久坐党别忽略这件事", cover)
        self.assertIn("三个习惯现在就改", cover)
        self.assertIn("超大字号", cover)
        self.assertIn("45%至60%", cover)
        self.assertIn("禁止出现英文单词", cover)
        self.assertIn("禁止出现英文单词", prompt)
        self.assertEqual(count_chinese_characters("以下这5类人"), 5)

    def test_manual_image_bundle_includes_cover(self):
        items = build_manual_image_items(
            theme="久坐护理",
            page_lines=["第一页内容", "第二页内容"],
            style="手绘卡通",
            cover_title="久坐党必看",
            cover_subtitle="三个习惯",
        )
        self.assertEqual(len(items), 3)
        self.assertTrue(items[0]["is_cover"])
        self.assertEqual(items[0]["size"], "3:4")
        self.assertIn("封面主标题", items[0]["prompt"])
        self.assertFalse(items[1]["is_cover"])

    def test_image_reference_limits_and_prompt(self):
        self.assertEqual(max_reference_images_for_model("gpt-image-2"), 5)
        self.assertEqual(max_reference_images_for_model("seedream-5.0-pro"), 10)
        self.assertEqual(max_reference_images_for_model("qwen-image-3.0"), 3)
        self.assertEqual(max_reference_images_for_model("z-image-turbo"), 0)
        instruction = build_reference_instruction(
            "product",
            image_count=2,
        )
        self.assertIn("产品", instruction)
        self.assertEqual(
            build_reference_instruction("none", image_count=2),
            "",
        )
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "reference.png"
            image.write_bytes(b"image")
            self.assertEqual(validate_reference_image(image), "")
            invalid = Path(temp) / "reference.txt"
            invalid.write_text("bad", encoding="utf-8")
            self.assertIn("仅支持", validate_reference_image(invalid))

    def test_image_page_reference_controls(self):
        window = MainWindow()
        page = window.image_page
        self.assertEqual(
            [
                page.reference_mode_combo.itemData(index)
                for index in range(page.reference_mode_combo.count())
            ],
            ["none", "product", "person", "background", "replicate", "custom"],
        )
        page.model_combo.setCurrentText("z-image-turbo")
        self.assertFalse(page.add_reference_button.isEnabled())
        page.model_combo.setCurrentText("gpt-image-2")
        self.assertTrue(page.add_reference_button.isEnabled())
        page.reference_mode_combo.setCurrentIndex(
            page.reference_mode_combo.findData("custom")
        )
        self.assertFalse(page.reference_custom_edit.isHidden())
        self.assertFalse(page.reference_template_button.isHidden())
        window.close()

    def test_image_page_imported_prompt_mode(self):
        window = MainWindow()
        page = window.image_page
        original = OriginalContent(
            title="原创标题",
            body="原创正文",
            tags=["肛周护理"],
            cover_title="封面临",
            pages=[
                {
                    "heading": "第一页",
                    "copy": "第一页文案",
                    "on_image_text": "保持清爽",
                    "image_prompt": "生成一张3:4竖版中文知识科普图。主题：原创标题。",
                }
            ],
        )
        page.load_generation_package(
            original,
            image_prompts=["生成一张3:4竖版中文知识科普图。主题：原创标题。"],
            image_style="手绘卡通",
        )
        self.assertEqual(
            page.generation_mode_combo.currentData(),
            "imported",
        )
        self.assertEqual(page.page_task_table.rowCount(), 1)
        self.assertEqual(page.preview_tabs.currentIndex(), 1)
        captured: dict = {}

        def capture_worker(kind, settings, payload):
            captured.update(
                {"kind": kind, "settings": settings, "payload": payload}
            )

        page.start_worker = capture_worker
        page.generate()
        self.assertEqual(captured["kind"], "manual_images")
        items = captured["payload"]["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(
            items[1]["prompt"],
            "生成一张3:4竖版中文知识科普图。主题：原创标题。",
        )
        window.close()

    def test_image_reference_limit_is_enforced_by_client(self):
        client = APIMartClient(APIConfig(api_key="test"))
        with self.assertRaisesRegex(APIClientError, "最多使用 5 张参考图"):
            client.submit_image(
                prompt="测试",
                model="gpt-image-2",
                size="3:4",
                resolution="1k",
                image_urls=[f"https://example.com/{index}.png" for index in range(6)],
            )

    def test_gpt_image_25_quality_payload_and_controls(self):
        client = APIMartClient(APIConfig(api_key="test"))
        with patch.object(
            client,
            "_request",
            return_value={"data": [{"task_id": "quality-task"}]},
        ) as request:
            task_id = client.submit_image(
                prompt="测试质量参数",
                model="gpt-image-2.5-flare",
                size="3:4",
                resolution="1k",
                quality="xhigh",
            )
        self.assertEqual(task_id, "quality-task")
        payload = request.call_args.kwargs["json_body"]
        self.assertEqual(payload["quality"], "xhigh")

        window = MainWindow()
        page = window.image_page
        page.model_combo.setCurrentText("gpt-image-2.5-flare")
        self.assertTrue(page.quality_combo.isEnabled())
        self.assertEqual(
            [
                page.quality_combo.itemData(index)
                for index in range(page.quality_combo.count())
            ],
            ["low", "medium", "high", "xhigh", "max", "auto"],
        )
        page.quality_combo.setCurrentIndex(
            page.quality_combo.findData("xhigh")
        )
        self.assertEqual(page.selected_image_quality(), "xhigh")
        page.model_combo.setCurrentText("z-image-turbo")
        self.assertFalse(page.quality_combo.isEnabled())
        self.assertIsNone(page.selected_image_quality())
        window.close()

    def test_batch_jobs_include_large_title_cover(self):
        item = BatchItem(
            index=1,
            theme="干纸猛擦VS湿厕纸轻擦，差别有多大？",
            copy="1、减少摩擦；2、保持干爽",
            style="手绘卡通",
            image_prompts=["图1：对比场景", "图2：步骤说明"],
        )
        jobs = build_batch_image_jobs(item)
        self.assertEqual(len(jobs), 3)
        self.assertTrue(jobs[0]["is_cover"])
        self.assertEqual(jobs[0]["order"], 0)
        self.assertEqual(jobs[0]["size"], "3:4")
        self.assertIn("干纸猛擦VS湿厕纸轻擦", jobs[0]["prompt"])
        self.assertIn("45%至60%", jobs[0]["prompt"])
        self.assertIn("禁止出现英文单词", jobs[0]["prompt"])
        self.assertIn("禁止出现英文单词", jobs[1]["prompt"])
        self.assertIn("图1：对比场景", jobs[1]["prompt"])
        self.assertFalse(jobs[1]["is_cover"])

    def test_model_parameter_validation(self):
        duration, resolution = APIMartClient._normalize_video_parameters(
            model="seedance-2.0",
            duration=8,
            resolution="1080p",
        )
        self.assertEqual((duration, resolution), (8, "1080p"))

        duration, resolution = APIMartClient._normalize_video_parameters(
            model="veo3.1-fast",
            duration=5,
            resolution="720p",
        )
        self.assertEqual((duration, resolution), (8, "720p"))

        with self.assertRaises(APIClientError):
            APIMartClient._normalize_video_parameters(
                model="seedance-2.0-fast",
                duration=8,
                resolution="4k",
            )

    def test_chat_completion_response_formats(self):
        client = APIMartClient(
            APIConfig(base_url="https://api.apib.ai/v1", api_key="test-key")
        )
        captured = {}

        def wrapped_request(method, path, **kwargs):
            captured.update({"method": method, "path": path, "kwargs": kwargs})
            return {
                "code": 200,
                "data": {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "原创口播测试",
                            }
                        }
                    ]
                },
            }

        client._request = wrapped_request
        result = client.chat_completion(model="gpt-5", prompt="重写口播")
        self.assertEqual(result, "原创口播测试")
        self.assertEqual(captured["path"], "/chat/completions")
        self.assertEqual(
            captured["kwargs"]["json_body"]["messages"][-1]["content"],
            "重写口播",
        )

        client._request = lambda method, path, **kwargs: {
            "choices": [{"message": {"content": "direct-ok"}}]
        }
        self.assertEqual(
            client.chat_completion(model="gpt-5", prompt="test"),
            "direct-ok",
        )

    def test_multimodal_response_sends_vision_message(self):
        client = APIMartClient(
            APIConfig(base_url="https://api.apib.ai/v1", api_key="test-key")
        )
        captured = {}

        def wrapped_request(method, path, **kwargs):
            captured.update({"method": method, "path": path, "kwargs": kwargs})
            return {"choices": [{"message": {"content": "画面主体：桌面上的湿厕纸。"}}]}

        client._request = wrapped_request
        text = client.multimodal_response(
            model="gpt-4o-mini",
            prompt="识别图片",
            image_data_uris=["data:image/jpeg;base64,AAAA"],
        )
        self.assertEqual(text, "画面主体：桌面上的湿厕纸。")
        self.assertEqual(captured["path"], "/chat/completions")
        content = captured["kwargs"]["json_body"]["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "识别图片"})
        self.assertEqual(
            content[1],
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64,AAAA"},
            },
        )

        client._request = lambda method, path, **kwargs: {
            "id": "resp_test",
            "object": "response",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "responses-ok"}
                    ],
                }
            ],
        }
        self.assertEqual(
            client.multimodal_response(
                model="gpt-4o-mini",
                prompt="识别图片",
                image_data_uris=["data:image/jpeg;base64,AAAA"],
            ),
            "responses-ok",
        )

        attempts: list[str] = []

        def failing_chat(method, path, **kwargs):
            attempts.append(path)
            if path == "/chat/completions":
                raise APIClientError("HTTP 404")
            return {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "fallback-ok"}],
                    }
                ]
            }

        client._request = failing_chat
        self.assertEqual(
            client.multimodal_response(
                model="gpt-4o-mini",
                prompt="识别图片",
                image_data_uris=["data:image/jpeg;base64,AAAA"],
            ),
            "fallback-ok",
        )
        self.assertEqual(attempts, ["/chat/completions", "/responses"])

    def test_speech_payload_supports_apib_tts_routes(self):
        client = APIMartClient(
            APIConfig(base_url="https://api.apib.ai/v1", api_key="test-key")
        )
        captured = {}

        class FakeResponse:
            ok = True
            status_code = 200
            content = b"RIFFtest"
            text = ""

        class FakeSession:
            def post(self, url, **kwargs):
                captured.update({"url": url, "kwargs": kwargs})
                return FakeResponse()

        client.session = FakeSession()
        content = client.synthesize_speech(
            text="测试配音",
            model="gpt-4o-mini-tts",
            voice="alloy",
            response_format="wav",
            speed=1.05,
            instructions="温柔、有感情地朗读。",
        )
        self.assertEqual(content, b"RIFFtest")
        self.assertTrue(captured["url"].endswith("/audio/speech"))
        self.assertEqual(captured["kwargs"]["json"]["input"], "测试配音")
        self.assertEqual(captured["kwargs"]["json"]["prompt"], "测试配音")
        self.assertEqual(captured["kwargs"]["json"]["speed"], 1.05)
        self.assertEqual(
            captured["kwargs"]["json"]["instructions"],
            "温柔、有感情地朗读。",
        )

    def test_emotion_instruction_uses_position_and_intent(self):
        opening = emotion_instruction(
            "强钩子",
            "明显",
            position=0,
            total=3,
            intent="问题引入",
        )
        conclusion = emotion_instruction(
            "温暖治愈",
            "强烈",
            position=2,
            total=3,
            intent="总结和提醒",
        )
        self.assertIn("抓住注意力", opening)
        self.assertIn("问题和风险", opening)
        self.assertIn("结尾", conclusion)
        self.assertIn("结论句", conclusion)

    def test_pricing_formatting(self):
        token_pricing = {
            "pricing": {
                "effective_rates": {"input": 1.0, "output": 8.0},
                "limits": {"max_input_tokens": 128000, "max_output_tokens": 16000},
            }
        }
        self.assertIn("输入 $1/百万Token", format_pricing(token_pricing))
        self.assertIn("输出 $8/百万Token", format_pricing(token_pricing))
        image_pricing = {
            "resolution_prices": {"1K": 0.21, "2K": 0.42},
        }
        self.assertIn("1K $0.21/张", format_pricing(image_pricing))
        usage = {"prompt_tokens": 100, "completion_tokens": 50}
        self.assertIn("合计 150 Token", format_usage(usage))

    def test_publish_platform_adaptation(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "video.mp4"
            video.write_bytes(b"not-a-real-video")
            posts = build_platform_posts(
                base_title="这是一个用于测试小红书标题截断和中文长度限制的超级长标题",
                description="这是一段发布简介。" * 100,
                raw_tags="肛周护理 久坐党 健康科普 湿厕纸",
                media_paths=[video],
                platform_keys=["douyin", "xiaohongshu", "bilibili"],
            )
            by_key = {post.platform.key: post for post in posts}
            self.assertLessEqual(len(by_key["xiaohongshu"].title), 20)
            self.assertTrue(by_key["douyin"].valid)
            self.assertTrue(by_key["bilibili"].valid)

            image = Path(temp) / "image.png"
            image.write_bytes(b"not-a-real-image")
            image_posts = build_platform_posts(
                base_title="测试",
                description="测试",
                raw_tags="测试",
                media_paths=[image],
                platform_keys=["bilibili"],
            )
            self.assertFalse(image_posts[0].valid)

    def test_publish_draft_navigation_and_queue(self):
        window = MainWindow()
        window.show()
        self.app.processEvents()
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "image.png"
            image.write_bytes(b"image")
            video = Path(temp) / "video.mp4"
            video.write_bytes(b"video")
            scheduled_at = (
                datetime.now() + timedelta(days=2)
            ).replace(second=0, microsecond=0)
            image_draft = PublishDraft(
                draft_id=new_publish_draft_id(),
                source_page="图文生成",
                media_paths=[str(image)],
                media_type="image",
                title="图文发布草稿",
                description="图文简介",
                tags=["肛周护理"],
            )
            video_draft = PublishDraft(
                draft_id=new_publish_draft_id(),
                source_page="视频生成",
                media_paths=[str(video)],
                media_type="video",
                title="视频发布草稿",
                description="视频简介",
                tags=["健康科普"],
                scheduled_at=scheduled_at.strftime("%Y-%m-%d %H:%M"),
            )
            window.open_publish_center([image_draft, video_draft])
            self.app.processEvents()
            self.assertEqual(
                window.stack.currentIndex(),
                window.stack.indexOf(window.publish_page),
            )
            self.assertEqual(window.publish_page.draft_combo.count(), 2)
            self.assertEqual(window.publish_page.media_list.count(), 1)
            self.assertEqual(window.publish_page.title_edit.text(), "视频发布草稿")
            self.assertFalse(hasattr(window.publish_page, "platform_checks"))
            self.assertTrue(
                all(
                    window.publish_page.tabs.isTabEnabled(index)
                    for index in range(window.publish_page.tabs.count())
                )
            )
            xhs_widgets = window.publish_page.platform_widgets["xiaohongshu"]
            self.assertTrue(xhs_widgets["schedule_check"].isChecked())
            self.assertEqual(
                xhs_widgets["schedule_edit"].dateTime().toPython(),
                scheduled_at,
            )
            self.assertEqual(
                window.publish_page.tabs.currentIndex(),
                list(PLATFORMS).index("xiaohongshu"),
            )
        window.close()

    def test_batch_publish_queue_schedule(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "image.png"
            image.write_bytes(b"image")
            drafts = [
                PublishDraft(
                    draft_id=new_publish_draft_id(),
                    source_page="批量处理",
                    media_paths=[str(image)],
                    media_type="image",
                    title=f"批量标题{index}",
                    description="批量正文",
                    tags=["测试"],
                )
                for index in range(3)
            ]
            start = datetime.now() + timedelta(hours=2)
            entries = evenly_arranged_entries(
                drafts,
                start_at=start,
                min_delay_minutes=5,
                max_delay_minutes=10,
            )
            self.assertEqual(len(entries), 3)
            self.assertEqual(entries[0].scheduled_at, start.replace(second=0, microsecond=0))
            for previous, current in zip(entries, entries[1:]):
                self.assertGreaterEqual(
                    (current.scheduled_at - previous.scheduled_at).total_seconds(),
                    5 * 60,
                )

            dialog = BatchPublishQueueDialog(drafts)
            self.assertEqual(dialog.table.rowCount(), 3)
            self.assertEqual(len(dialog.time_edits), 3)
            self.assertFalse(dialog.auto_publish_check.isChecked())
            self.assertEqual(dialog.max_jobs_spin.value(), 2)
            self.assertEqual(
                dialog.start_edit.displayFormat(),
                PUBLISH_SCHEDULE_DISPLAY_FORMAT,
            )
            self.assertEqual(
                dialog.time_edits[0].displayFormat(),
                PUBLISH_SCHEDULE_DISPLAY_FORMAT,
            )
            dialog.close()

    def test_publish_batch_folder_and_excel_import(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_note = root / "笔记01"
            image_note.mkdir()
            (image_note / "02_内容.png").write_bytes(b"image-2")
            (image_note / "01_封面.png").write_bytes(b"image-1")

            video_note = root / "笔记02"
            video_note.mkdir()
            (video_note / "成片.mp4").write_bytes(b"video")
            (video_note / "封面.jpg").write_bytes(b"cover")

            drafts = discover_publish_drafts(root)
            self.assertEqual(len(drafts), 2)
            image_draft = next(draft for draft in drafts if draft.media_type == "image")
            video_draft = next(draft for draft in drafts if draft.media_type == "video")
            self.assertTrue(image_draft.media_paths[0].endswith("01_封面.png"))
            self.assertTrue(video_draft.cover_path.endswith("封面.jpg"))
            self.assertEqual(video_draft.media_type, "video")

            template = root / "发布模板.xlsx"
            create_publish_batch_template(template)
            self.assertTrue(template.exists())

            workbook = Workbook()
            sheet = workbook.active
            sheet.append(
                [
                    "类型",
                    "素材路径",
                    "封面路径",
                    "标题",
                    "正文",
                    "标签",
                    "计划发布时间",
                ]
            )
            sheet.append(
                [
                    "图文",
                    "笔记01",
                    "笔记01/01_封面.png",
                    "批量发布测试",
                    "第一段\n第二段",
                    "肛周护理 健康科普",
                    "2026-10-01 10:00",
                ]
            )
            excel_path = root / "批量发布.xlsx"
            workbook.save(excel_path)

            imported = load_publish_drafts_from_excel(excel_path)
            self.assertEqual(len(imported), 1)
            self.assertEqual(imported[0].title, "批量发布测试")
            self.assertEqual(imported[0].tags, ["肛周护理", "健康科普"])
            self.assertEqual(imported[0].status, "已校验")

            unique, duplicates = dedupe_publish_drafts(imported + imported)
            self.assertEqual(len(unique), 1)
            self.assertEqual(duplicates, 1)

    def test_publish_batch_validation_rejects_multiple_videos(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "first.mp4"
            second = root / "second.mp4"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            draft = PublishDraft(
                source_page="测试",
                media_paths=[str(first), str(second)],
                media_type="video",
                title="错误视频包",
            )
            validate_publish_draft(draft)
            self.assertEqual(draft.status, "需修正")
            self.assertTrue(
                any("只能包含 1 个视频" in error for error in draft.validation_errors)
            )

    def test_publish_metadata_excel_maps_by_row_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("笔记01", "笔记02"):
                folder = root / name
                folder.mkdir()
                (folder / "00_封面.png").write_bytes(b"image")

            drafts = discover_publish_drafts(root)
            self.assertEqual(len(drafts), 2)

            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["标题", "内容", "标签", "定时发布时间"])
            sheet.append(
                [
                    "第一行标题",
                    "第一行内容",
                    "肛周护理 健康科普",
                    "2026/10/1 9:30",
                ]
            )
            sheet.append(
                [
                    "第二行标题",
                    "第二行内容",
                    "久坐党 健康生活",
                    datetime(2026, 10, 1, 20, 0),
                ]
            )
            excel_path = root / "发布文案.xlsx"
            workbook.save(excel_path)

            metadata_rows = load_publish_metadata_from_excel(excel_path)
            self.assertEqual(len(metadata_rows), 2)
            apply_publish_metadata(drafts, metadata_rows)
            self.assertEqual(
                [draft.title for draft in drafts],
                ["第一行标题", "第二行标题"],
            )
            self.assertEqual(
                [draft.description for draft in drafts],
                ["第一行内容", "第二行内容"],
            )
            self.assertEqual(drafts[0].tags, ["肛周护理", "健康科普"])
            self.assertEqual(drafts[0].scheduled_at, "2026-10-01 09:30")
            self.assertEqual(drafts[1].scheduled_at, "2026-10-01 20:00")

    def test_publish_queue_pauses_after_two_consecutive_failures(self):
        scheduled_at = datetime.now() - timedelta(minutes=2)
        drafts = [
            PublishDraft(
                source_page="测试",
                media_paths=[],
                media_type="image",
                title=f"失败任务{index}",
            )
            for index in range(3)
        ]
        entries = [
            PublishQueueEntry(draft=draft, scheduled_at=scheduled_at)
            for draft in drafts
        ]
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch(
                    "publish_queue.assist_upload_xiaohongshu",
                    side_effect=RuntimeError("模拟失败"),
                ),
                patch(
                    "publish_queue.QUEUE_STATE_PATH",
                    Path(temp) / "queue.json",
                ),
                patch("publish_queue.random.randint", return_value=0),
            ):
                run_publish_queue(
                    entries,
                    browser_name="edge",
                    auto_publish=False,
                    prep_lead_minutes=1,
                    stop_event=threading.Event(),
                )
        self.assertEqual([entry.status for entry in entries], ["失败", "失败", "已暂停"])

    def test_publish_validation_tooltip_shows_reason_and_fix(self):
        draft = PublishDraft(
            source_page="测试",
            media_paths=[],
            media_type="image",
            title="",
            scheduled_at="错误时间",
        )
        validate_publish_draft(draft)
        tooltip = publish_draft_validation_tooltip(draft)
        self.assertIn("修正原因", tooltip)
        self.assertIn("修正方法", tooltip)
        self.assertIn("没有选择图片或视频", tooltip)
        self.assertIn("标题不能为空", tooltip)
        self.assertIn("YYYY/M/D H:mm", tooltip)
        self.assertIn("Excel 日期单元格", tooltip)

        window = MainWindow()
        window.publish_page.add_drafts([draft])
        self.app.processEvents()
        status_item = window.publish_page.draft_table.item(0, 3)
        self.assertIn("修正原因", status_item.toolTip())
        self.assertIn("修正方法", status_item.toolTip())
        window.close()

    def test_crebee_image_payload_uses_account_and_tags(self):
        account = {
            "account_key": "xhs-account-key",
            "account_id": "xhs-account-id",
            "account_alias": "张小星",
            "account_platform": "xiaohongshu",
            "account_status": "connected",
        }
        self.assertEqual(account_identifier(account), "xhs-account-id")
        self.assertIn("张小星", account_label(account))
        self.assertIn("xiaohongshu", account_label(account))

        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "原图01.png"
            image.write_bytes(b"fake-image")
            payload = build_publish_payload(
                account=account,
                title="久坐党护理",
                description="正文",
                tags=["肛周护理", "久坐党"],
                media_paths=[image],
                scheduled_at=datetime(2026, 10, 20, 19, 30),
                task_id="task-1",
            )

        self.assertEqual(payload["contentType"], "image")
        self.assertEqual(payload["commonForm"]["taskId"], "task-1")
        self.assertEqual(payload["commonForm"]["images"], [str(image)])
        task = payload["tasks"][0]
        self.assertEqual(task["accountId"], "xhs-account-id")
        self.assertEqual(task["platform"], "xiaohongshu")
        self.assertEqual(task["contentType"], "image")
        self.assertEqual(task["params"]["topics"], ["肛周护理", "久坐党"])
        self.assertEqual(
            task["params"]["timing"],
            int(datetime(2026, 10, 20, 19, 30).timestamp()),
        )

    def test_crebee_video_payload_requires_cover_and_summarizes_result(self):
        account = {
            "account_key": "key-2",
            "account_id": "id-2",
            "account_platform": "xiaohongshu",
        }
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "video.mp4"
            video.write_bytes(b"fake-video")
            with self.assertRaises(CreBeeError):
                build_publish_payload(
                    account=account,
                    title="标题",
                    description="正文",
                    tags=[],
                    media_paths=[video],
                )
            cover = Path(temp) / "cover.png"
            cover.write_bytes(b"fake-cover")
            payload = build_publish_payload(
                account=account,
                title="标题",
                description="正文",
                tags=[],
                media_paths=[video, cover],
                task_id="task-2",
            )

        self.assertEqual(payload["contentType"], "video")
        self.assertEqual(payload["commonForm"]["videoPath"], str(video))
        self.assertEqual(payload["commonForm"]["coverPath"], str(cover))
        self.assertEqual(payload["tasks"][0]["params"]["timing"], 0)

        summary = summarize_publish_result(
            {
                "code": 0,
                "message": "已提交",
                "raw": {
                    "total": 1,
                    "success": 0,
                    "failed": 1,
                    "results": [
                        {
                            "accountId": "key-2",
                            "platform": "xiaohongshu",
                            "status": "failed",
                            "error": "账号未绑定",
                        }
                    ],
                },
            }
        )
        self.assertIn("已提交", summary)
        self.assertIn("[失败]", summary)
        self.assertIn("账号未绑定", summary)

    def test_crebee_account_list_parsing(self):
        payload = {
            "code": 0,
            "raw": {"items": [{"account_key": "a"}, {"account_key": "b"}]},
        }
        self.assertEqual(len(parse_accounts(payload)), 2)
        self.assertEqual(parse_accounts({}), [])

    def test_generation_pages_have_publish_entry_buttons(self):
        window = MainWindow()
        self.app.processEvents()
        self.assertEqual(
            window.image_page.send_publish_button.text(),
            "发送到发布中心",
        )
        self.assertEqual(
            window.video_page.send_publish_button.text(),
            "发送到发布中心",
        )
        self.assertEqual(
            window.batch_page.send_publish_button.text(),
            "发送到发布中心",
        )
        self.assertFalse(hasattr(window.batch_page, "batch_publish_button"))
        self.assertFalse(hasattr(window.batch_page, "stop_publish_queue_button"))
        publish_buttons = {
            button.text()
            for button in window.publish_page.findChildren(QtWidgets.QPushButton)
        }
        self.assertIn("导入素材文件夹", publish_buttons)
        self.assertIn("导入发布 Excel", publish_buttons)
        self.assertIn("下载发布模板", publish_buttons)
        self.assertIn("批量准备小红书", publish_buttons)
        self.assertIn("停止批量准备", publish_buttons)
        window.close()

    def test_xiaohongshu_form_helpers_fill_local_page(self):
        fixture = self.root / "tests" / "fixtures" / "xhs_publish.html"
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "note.png"
            image.write_bytes(b"not-a-real-image")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    channel="msedge",
                    headless=True,
                )
                page = browser.new_page()
                page.goto(fixture.as_uri(), wait_until="domcontentloaded")
                uploaded = _upload_files(page, [image])
                title_input = page.locator('input[placeholder*="标题"]')
                title_filled = _commit_input_value(title_input, "测试标题")
                body_filled = _fill_field(
                    page,
                    ['textarea[placeholder*="正文"]'],
                    "测试正文\n\n#肛周护理",
                )
                self.assertEqual(uploaded, 1)
                self.assertTrue(title_filled)
                self.assertTrue(body_filled)
                self.assertEqual(page.locator("#title").input_value(), "测试标题")
                self.assertEqual(
                    page.locator("#body").input_value(),
                    "测试正文\n\n#肛周护理",
                )
                scheduled, schedule_at = _configure_xiaohongshu_schedule(
                    page,
                    "2026-09-30 10:30",
                )
                self.assertTrue(scheduled)
                self.assertEqual(schedule_at, "2026-09-30 10:30")
                self.assertEqual(
                    page.locator(".date-picker-container input").input_value(),
                    "2026-09-30 10:30",
                )
                self.assertIsNotNone(_find_xiaohongshu_publish_button(page))
                page.evaluate(
                    """() => {
                        const node = document.createElement("div");
                        node.textContent = "发布成功";
                        document.body.appendChild(node);
                    }"""
                )
                self.assertTrue(_wait_for_xiaohongshu_publish_result(page))
                browser.close()

    def test_douyin_form_helpers_fill_local_page(self):
        fixture = self.root / "tests" / "fixtures" / "douyin_publish.html"
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "note.mp4"
            video.write_bytes(b"not-a-real-video")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    channel="msedge",
                    headless=True,
                )
                page = browser.new_page()
                page.goto(fixture.as_uri(), wait_until="domcontentloaded")
                uploaded = _upload_files(page, [video], accept_contains="video")
                title_input = page.locator('input[placeholder*="作品标题"]')
                title_filled = _commit_input_value(title_input, "抖音测试标题")
                body = page.locator("#body")
                body_filled = _fill_tiptap_body(page, body, "抖音测试正文")
                tags_filled = _append_douyin_tags(page, ["肛周护理", "健康科普"])
                scheduled, schedule_at = _configure_douyin_schedule(
                    page,
                    "2026-09-30 11:30",
                )
                _open_douyin_music_panel(page)
                music_items = _read_douyin_music_items(page)
                selected_music = select_douyin_music(
                    page,
                    tab="热门榜",
                    query="阳光",
                    index=1,
                )
                self.assertEqual(uploaded, 1)
                self.assertTrue(title_filled)
                self.assertTrue(body_filled)
                self.assertEqual(tags_filled, 2)
                self.assertTrue(scheduled)
                self.assertEqual(schedule_at, "2026-09-30 11:30")
                self.assertEqual(len(music_items), 2)
                self.assertEqual(selected_music["name"], "阳光总在风雨后")
                self.assertEqual(
                    page.locator("#selected-music").inner_text(),
                    "阳光总在风雨后",
                )
                hot_music = read_douyin_hot_music(page, tab="热门榜")
                self.assertEqual(len(hot_music), 2)
                _verify_douyin_fields(
                    page,
                    title="抖音测试标题",
                    description="抖音测试正文",
                    tags=["肛周护理", "健康科普"],
                    schedule_at="2026-09-30 11:30",
                )
                browser.close()

    def test_mock_image_and_video_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            image = generate_mock_image(
                "生成一张3:4竖版中文知识科普图。主题：测试。画面内容：1、减少反复摩擦。",
                "手绘卡通",
                output / "image.png",
                width=540,
                height=720,
            )
            self.assertTrue(image.exists())
            self.assertGreater(image.stat().st_size, 10_000)

            video = generate_mock_video(
                [image],
                output / "video.mp4",
                duration=2,
                width=360,
                height=640,
            )
            self.assertTrue(video.exists())
            self.assertGreater(video.stat().st_size, 10_000)

            thumbnail = create_video_thumbnail(video, output / "thumb.jpg")
            self.assertTrue(thumbnail.exists())
            self.assertGreater(thumbnail.stat().st_size, 1_000)

    def test_material_library_import(self):
        source = self.root / "肛周健康科普素材库_60条_含生图提示词.csv"
        self.assertTrue(source.exists(), source)
        items = load_batch_items(source, default_style="手绘卡通")
        self.assertEqual(len(items), 60)
        self.assertEqual(len(items[0].image_prompts), 4)
        self.assertIn("图1", items[0].image_prompts[0])

    def test_batch_template_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            template = Path(temp) / "template.xlsx"
            create_batch_template(template)
            items = load_batch_items(template, default_style="清新治愈")
            self.assertEqual(len(items), 1)
            self.assertEqual(len(items[0].image_prompts), 4)

    def test_jianying_draft_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = generate_mock_image(
                "生成一张3:4竖版中文知识科普图。主题：剪映测试。",
                "清新治愈",
                root / "source.png",
                width=360,
                height=640,
            )
            draft = create_jianying_draft(
                draft_root=root / "drafts",
                draft_name="张小星测试草稿",
                media_paths=[image],
                prompt_text="第一页：日常护理",
                image_duration=2,
                width=720,
                height=1280,
                fps=30,
            )
            self.assertTrue((draft / "draft_content.json").exists())
            self.assertTrue((draft / "draft_meta_info.json").exists())
            self.assertGreater((draft / "draft_content.json").stat().st_size, 1_000)
            content = json.loads((draft / "draft_content.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(content.get("tracks", [])), 2)
            self.assertGreaterEqual(len(content.get("materials", {}).get("videos", [])), 1)
            self.assertGreaterEqual(len(content.get("materials", {}).get("texts", [])), 1)

    def test_hypit_environment(self):
        info = hypit_environment()
        self.assertTrue(info.node)
        self.assertTrue(info.npm)
        self.assertTrue(info.pnpm)
        self.assertTrue(info.ffmpeg)
        self.assertTrue(info.ffprobe)
        self.assertTrue(info.hypit)
        self.assertEqual(hypit_version(), "0.2.12")

    def test_apib_provider_installation(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            profile = project / "hypit.runtime.json"
            profile.write_text("{}", encoding="utf-8")
            destination = install_apib_provider(project)
            self.assertTrue((destination / "src" / "provider.ts").exists())
            document = json.loads(profile.read_text(encoding="utf-8"))
            self.assertEqual(
                document["endpoints"]["apib.default"]["use"],
                "@zhangxiaoxing/provider-apib",
            )
            self.assertEqual(
                document["bindings"]["@hypit/gpt-image@1#gpt-image-2"],
                "apib.default",
            )

    def test_hypit_tutorial_assets_and_content(self):
        html = build_tutorial_html()
        expected_assets = [
            "01_project.png",
            "02_provider.png",
            "03_runtime.png",
            "04_plan.png",
            "05_build.png",
            "06_output.png",
            "07_flow.png",
        ]
        for name in expected_assets:
            asset = ASSET_DIR / name
            self.assertTrue(asset.exists(), asset)
            self.assertGreater(asset.stat().st_size, 10_000)
            self.assertIn(asset.as_uri(), html)

        self.assertIn("Hypit 视频栏目使用教程", html)
        self.assertIn("配置 APIB Provider", html)
        self.assertIn("Plan 不提交付费任务", html)
        self.assertIn("常见问题", html)

    def test_hypit_reference_heuristic_analysis(self):
        analysis = heuristic_analysis(
            title="久坐护理",
            hook="久坐党别忽略这件事",
            language="zh",
            aspect_ratio="9:16",
            evidence={
                "probe": {"duration": 12, "hasAudio": False},
                "boundaries": {"candidates": [{"at": 3.5}, {"at": 7.2}]},
            },
            transcript={"available": False, "reason": "未检测到音轨"},
        )
        self.assertEqual(analysis["analysis_mode"], "heuristic")
        self.assertEqual(analysis["hook"]["proposed_text"], "久坐党别忽略这件事")
        self.assertEqual(len(analysis["rhythm"]["shot_change_candidates"]), 2)
        self.assertIn("editable_project", analysis)

    def test_hypit_reference_duration_aligns_to_frame(self):
        self.assertEqual(align_duration_to_frame(44.033), 44.0)
        self.assertEqual(align_duration_to_frame(44.034), 44.03333333333333)
        self.assertEqual(align_duration_to_frame(0.01), 1 / 30)
        self.assertEqual(frames_for_duration(61.43333333333333), 1843)

    def test_hypit_reference_url_normalization(self):
        self.assertEqual(
            normalize_reference_url("复制这段链接 https://v.douyin.com/abc123/ 打开抖音"),
            "https://v.douyin.com/abc123/",
        )
        self.assertEqual(
            normalize_reference_url("v.douyin.com/abc123/"),
            "https://v.douyin.com/abc123/",
        )
        with self.assertRaises(ReferenceWorkflowError):
            normalize_reference_url("这是一段没有链接的分享文案")

    def test_hypit_douyin_browser_media_selection(self):
        self.assertTrue(is_douyin_url("https://v.douyin.com/abc123/"))
        self.assertTrue(is_douyin_url("https://www.douyin.com/video/123"))
        self.assertFalse(is_douyin_url("https://www.bilibili.com/video/BV123"))
        best = select_best_browser_media(
            [
                {
                    "url": "https://lf-douyin-pc-web.douyinstatic.com/obj/sample.mp4",
                    "status": 206,
                },
                {
                    "url": "https://v26-web.douyinvod.com/video/sample.mp4?br=736",
                    "status": 206,
                },
                {
                    "url": "https://v26-web.douyinvod.com/video/sample.mp4?br=1674",
                    "status": 206,
                },
            ]
        )
        self.assertIn("br=1674", best["url"])

    def test_hypit_reference_project_generation(self):
        workspace_name = f"unittest_reference_{os.getpid()}"
        workspace = reference_workspace(workspace_name)
        try:
            with tempfile.TemporaryDirectory() as temp:
                source_root = Path(temp)
                image = generate_mock_image(
                    "生成一张3:4竖版参考图。",
                    "清新治愈",
                    source_root / "source.png",
                    width=360,
                    height=640,
                )
                video = generate_mock_video(
                    [image],
                    source_root / "source.mp4",
                    duration=1,
                    width=360,
                    height=640,
                )
                project = run_reference_workflow(
                    title="参考工程测试",
                    hook="三秒看懂参考结构",
                    source_file=str(video),
                    source_url="",
                    language="zh",
                    aspect_ratio="9:16",
                    analysis_model="",
                    api_key="",
                    base_url="https://api.apib.ai/v1",
                    mock_mode=True,
                    workspace_name=workspace_name,
                )
                self.assertTrue(project.svml_path.exists())
                self.assertTrue(project.svrun_path.exists())
                self.assertTrue(project.analysis_path.exists())
                document = project.svml_path.read_text(encoding="utf-8")
                self.assertIn('family="noto-sans-sc"', document)
                self.assertRegex(document, r'end="\d+f"')

                relative_run = project.svrun_path.relative_to(project.workspace).as_posix()
                plan = run_hypit(["plan", relative_run, "--json"], cwd=project.workspace, timeout=300)
                self.assertEqual(plan.returncode, 0, plan.stderr or plan.stdout)
                payload = json.loads(plan.stdout)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["unresolvedRequestCount"], 0)
                self.assertEqual(payload["providerRequestCount"], 0)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_hypit_reference_export_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            run_dir = workspace / "run"
            output = run_dir / "output" / "final.mp4"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"old-video")
            project = ReferenceProject(
                workspace=workspace,
                run_dir=run_dir,
                media_path=run_dir / "assets" / "reference.mp4",
                analysis_path=run_dir / "analysis.json",
                svml_path=run_dir / "main.svml",
                svrun_path=run_dir / "project.svrun",
                output_path=output,
                analysis={},
            )

            def fake_run(arguments, **kwargs):
                if arguments[0] == "build":
                    return subprocess.CompletedProcess(
                        arguments,
                        0,
                        stdout="Build bld_test_123 completed",
                        stderr="",
                    )
                destination = Path(arguments[arguments.index("--to") + 1])
                destination.write_bytes(b"new-video")
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout="downloaded",
                    stderr="",
                )

            with patch("hypit_reference.run_hypit", side_effect=fake_run):
                result = build_reference_project(project)
            self.assertEqual(result, output)
            self.assertEqual(output.read_bytes(), b"new-video")
            self.assertFalse((output.parent / "final.new.mp4").exists())

    def test_hypit_originality_project_generation(self):
        workspace_name = f"unittest_rewrite_{os.getpid()}"
        workspace = reference_workspace(workspace_name)
        try:
            with tempfile.TemporaryDirectory() as temp:
                source_root = Path(temp)
                image = generate_mock_image(
                    "生成一张3:4竖版参考图。",
                    "清新治愈",
                    source_root / "source.png",
                    width=360,
                    height=640,
                )
                video = generate_mock_video(
                    [image],
                    source_root / "source.mp4",
                    duration=1,
                    width=360,
                    height=640,
                )
                source = run_reference_workflow(
                    title="原创口播测试",
                    hook="先别急着划走",
                    source_file=str(video),
                    source_url="",
                    language="zh",
                    aspect_ratio="9:16",
                    analysis_model="",
                    api_key="",
                    base_url="https://api.apib.ai/v1",
                    mock_mode=True,
                    workspace_name=workspace_name,
                )
                rewritten = run_originality_workflow(
                    source,
                    RewriteOptions(
                        model="",
                        target_title="久坐党减少肛周摩擦的正确方法",
                        target_hook="别再反复干擦，这个习惯可能让问题更明显",
                        originality_level="中度改写",
                        remove_ai_flavor=True,
                        remove_promotional=True,
                        tts_model="tts-1",
                        voice="alloy",
                        language="zh",
                    ),
                    api_key="",
                    base_url="https://api.apib.ai/v1",
                    mock_mode=True,
                )
                self.assertTrue(rewritten.svml_path.exists())
                self.assertTrue((rewritten.run_dir / "assets" / "voiceover.wav").exists())
                self.assertTrue((rewritten.run_dir / "captions.srt").exists())
                self.assertEqual(
                    rewritten.analysis["title"],
                    "久坐党减少肛周摩擦的正确方法",
                )
                self.assertEqual(
                    rewritten.analysis["hook"],
                    "别再反复干擦，这个习惯可能让问题更明显",
                )
                document = rewritten.svml_path.read_text(encoding="utf-8")
                self.assertIn("@hypit/audio-track@1", document)
                self.assertIn("<typo:Track id=\"subtitles\"", document)
                self.assertIn("别再反复干擦", document)
                self.assertRegex(document, r'end="\d+f"')

                relative_run = rewritten.svrun_path.relative_to(rewritten.workspace).as_posix()
                plan = run_hypit(["plan", relative_run, "--json"], cwd=rewritten.workspace, timeout=300)
                self.assertEqual(plan.returncode, 0, plan.stderr or plan.stdout)
                payload = json.loads(plan.stdout)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["unresolvedRequestCount"], 0)
                self.assertEqual(payload["providerRequestCount"], 0)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_hot_content_rewrite_and_excel_export(self):
        item = HotContentItem(
            source_mode="链接采集",
            source_url="https://www.xiaohongshu.com/explore/test",
            title="久坐党护理",
            body="减少反复摩擦，保持清爽",
            author="测试作者",
            tags=["肛周护理", "久坐党"],
        )
        original = mock_original_content(
            item,
            reference_content=(
                "1、如厕后减少反复摩擦\n"
                "2、使用湿厕纸保持清爽\n"
                "3、久坐一小时起身活动"
            ),
            product_info="马应龙彩虹湿厕纸",
            audience="久坐上班族",
            style="科普体",
            target_word_count=260,
        )
        self.assertTrue(original.title)
        self.assertGreaterEqual(len(original.pages), 4)
        self.assertIn("马应龙彩虹湿厕纸", original.body)
        prompts = original.image_prompts(
            theme=original.title,
            style="手绘卡通",
        )
        self.assertEqual(len(prompts), len(original.pages))

        with tempfile.TemporaryDirectory() as temp:
            output = export_original_content_excel(
                Path(temp) / "原创内容.xlsx",
                item=item,
                original=original,
            )
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 5_000)

    def test_xhs_visible_collector_helpers(self):
        self.assertEqual(
            normalize_xhs_url(
                "复制链接 https://www.xiaohongshu.com/explore/abc 打开小红书"
            ),
            "https://www.xiaohongshu.com/explore/abc",
        )
        fixture = self.root / "tests" / "fixtures" / "xhs_collect.html"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel="msedge",
                headless=True,
            )
            page = browser.new_page()
            page.goto(fixture.as_uri(), wait_until="domcontentloaded")
            self.assertFalse(_login_required(page))
            page.set_content(
                "<body><div>登录后查看搜索结果</div>"
                "<button>手机号登录</button></body>"
            )
            self.assertTrue(_login_required(page))
            page.goto(fixture.as_uri(), wait_until="domcontentloaded")
            detail = _extract_detail(page)
            cards = _extract_visible_cards(page, limit=5)
            self.assertEqual(detail.title, "久坐党日常护理的三个习惯")
            self.assertIn("反复摩擦", detail.body)
            self.assertIn("肛周护理", detail.tags)
            self.assertGreaterEqual(len(cards), 1)
            self.assertEqual(cards[0].title, "久坐党护理清单")
            browser.close()

    def test_collected_tags_are_reordered_and_limited(self):
        tags = ["肛周护理", "久坐党", "湿厕纸", "健康生活", "日常养护", "清洁"]
        original = generate_original_content(
            HotContentItem(source_mode="链接采集", tags=tags),
            reference_content="1、如厕后减少反复摩擦",
            product_info="",
            audience="",
            style="小红书风格",
            target_word_count=200,
            text_model="gpt-4o-mini",
            settings={"mock_mode": True},
        )
        self.assertEqual(len(original.tags), 5)
        self.assertTrue(set(original.tags).issubset(set(tags)))
        self.assertEqual(len(shuffle_tags(tags)), 5)
        self.assertEqual(shuffle_tags([]), [])
        orderings = {tuple(shuffle_tags(tags)) for _ in range(30)}
        self.assertGreater(len(orderings), 1)

    def test_rewrite_prompt_requires_colloquial_copy(self):
        prompt = build_rewrite_prompt(
            HotContentItem(
                source_mode="链接采集",
                title="标题",
                body="正文",
                tags=["肛周护理"],
            ),
            reference_content="参考内容",
            product_info="",
            audience="",
            style="小红书风格",
            target_word_count=300,
        )
        self.assertIn("去 AI 味", prompt)
        self.assertIn("口语", prompt)
        self.assertIn("像真人在说话", prompt)
        self.assertNotIn("原图识别与二次原创方案", prompt)

    def test_image_analysis_prompt_keeps_original_layout(self):
        prompt = build_image_analysis_prompt(2, 5)
        self.assertIn("第 2 张，共 5 张", prompt)
        self.assertIn("画面主体", prompt)
        self.assertIn("构图", prompt)
        self.assertIn("图中文字", prompt)
        # 识别提示词必须保持中性，出现"二次原创/复刻"会被模型当成仿图请求而拒答
        self.assertNotIn("二次原创", prompt)
        self.assertNotIn("保持原图", prompt)

    def test_analyze_reference_images_uses_mock_and_data_uri(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "原图01.png"
            with Image.new("RGB", (40, 40), "white") as image:
                image.save(path)
            self.assertTrue(to_data_uri(path).startswith("data:image/jpeg;base64,"))
            analysis = analyze_reference_images(
                [str(path)],
                model="gpt-4o-mini",
                settings={"mock_mode": True},
                theme="久坐党护理",
            )
        self.assertEqual(len(analysis), 1)
        self.assertIn("久坐党护理", analysis[0])
        self.assertIn("二次原创方案", analysis[0])
        self.assertIn("不要过度改款", analysis[0])

    def test_analyze_reference_images_calls_vision_client(self):
        class StubClient:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def multimodal_response(self, **kwargs) -> str:
                self.calls.append(kwargs)
                return "画面主体：桌面上的湿厕纸。\n二次原创方案：保持构图，只改文字。"

        stub = StubClient()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "原图01.jpg"
            with Image.new("RGB", (16, 16), "white") as image:
                image.save(path, format="JPEG")
            analysis = analyze_reference_images(
                [str(path)],
                model="gpt-4o-mini",
                settings={"mock_mode": False},
                theme="久坐党护理",
                client=stub,
            )
        self.assertEqual(
            analysis,
            [
                "画面主体：桌面上的湿厕纸。\n二次原创方案：保持构图，只改文字。"
                "\n二次原创方案：按上面的识别结果重绘这张图，保持原图的画面主体、"
                "构图布局和整体风格，只做轻微细节变化，不要过度改款。"
            ],
        )
        self.assertEqual(len(stub.calls), 1)
        self.assertTrue(
            stub.calls[0]["image_data_uris"][0].startswith("data:image/jpeg;base64,")
        )

    def test_analyze_reference_images_flags_model_refusal(self):
        class RefusingClient:
            def multimodal_response(self, **kwargs) -> str:
                return "抱歉，我无法识别或描述这张图片的内容。"

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "原图01.jpg"
            with Image.new("RGB", (16, 16), "white") as image:
                image.save(path, format="JPEG")
            analysis = analyze_reference_images(
                [str(path)],
                model="gpt-4o-mini",
                settings={"mock_mode": False},
                client=RefusingClient(),
            )
        self.assertEqual(len(analysis), 1)
        self.assertIn("未能识别", analysis[0])
        self.assertIn("支持读取图片的模型", analysis[0])
        self.assertNotIn("抱歉", analysis[0])

    def test_extract_image_texts_pulls_in_image_copy(self):
        analysis = [
            "画面主体：桌面上的湿厕纸。\n构图与配色：浅色背景。\n"
            "图中文字：1. 标题：别反复摩擦\n2. 正文：保持清爽\n"
            "二次原创方案：按上面的识别结果重绘这张图，保持原图的画面主体。",
            "画面主体：人物在洗手。\n构图与配色：暖光。\n图中文字：无",
        ]
        texts = extract_image_texts(analysis)
        self.assertEqual(len(texts), 2)
        self.assertIn("别反复摩擦", texts[0])
        self.assertIn("保持清爽", texts[0])
        self.assertNotIn("二次原创方案", texts[0])
        self.assertEqual(texts[1], "无")
        self.assertEqual(extract_image_texts(["没有识别结果"]), [""])

    def test_image_copy_prompt_requires_rewrite(self):
        prompt = build_image_copy_prompt(
            HotContentItem(source_mode="链接采集", title="久坐党护理"),
            image_analysis=["图中文字：1. 标题：别反复摩擦"],
            style="小红书风格",
            product_info="马应龙彩虹湿厕纸",
            audience="久坐上班族",
        )
        self.assertIn("不得照抄原句", prompt)
        self.assertIn("去 AI 味", prompt)
        self.assertIn("生图提示词", prompt)
        self.assertIn("图中文字", prompt)
        self.assertIn("马应龙彩虹湿厕纸", prompt)

    def test_image_copy_plan_mock_and_normalization(self):
        item = HotContentItem(source_mode="链接采集", title="久坐党护理")
        analysis = [
            "画面主体：桌面上的湿厕纸。\n图中文字：1. 标题：别反复摩擦\n"
            "二次原创方案：按上面的识别结果重绘这张图。"
        ]
        plan = generate_image_copy_plan(
            item,
            image_analysis=analysis,
            style="小红书风格",
            product_info="",
            audience="",
            text_model="gpt-4o-mini",
            settings={"mock_mode": True},
        )
        self.assertEqual(len(plan), 1)
        self.assertIn("别反复摩擦", plan[0]["image_copy"])
        self.assertIn("生成一张", plan[0]["image_prompt"])
        self.assertEqual(
            generate_image_copy_plan(
                item,
                image_analysis=[],
                style="小红书风格",
                product_info="",
                audience="",
                text_model="gpt-4o-mini",
                settings={"mock_mode": True},
            ),
            [],
        )
        self.assertEqual(
            normalize_image_copy_payload(
                {
                    "images": [
                        {"index": 1, "image_copy": "别老反复擦", "image_prompt": "提示词一"},
                        {"index": 2, "image_copy": "", "image_prompt": ""},
                        {"index": 3, "copy": "擦完要清爽", "image_prompt": "提示词三"},
                    ]
                }
            ),
            [
                {"index": 1, "image_copy": "别老反复擦", "image_prompt": "提示词一"},
                {"index": 2, "image_copy": "擦完要清爽", "image_prompt": "提示词三"},
            ],
        )
        self.assertEqual(normalize_image_copy_payload({"images": "bad"}), [])

    def test_rewrite_prompt_includes_image_analysis(self):
        item = HotContentItem(
            source_mode="链接采集",
            title="标题",
            body="正文",
            tags=["肛周护理"],
        )
        prompt = build_rewrite_prompt(
            item,
            reference_content="参考内容",
            product_info="",
            audience="",
            style="小红书风格",
            target_word_count=300,
            image_analysis=["画面主体：人物坐在书桌前。二次原创方案：保持构图，微调桌面小物件。"],
        )
        self.assertIn("原图识别与二次原创方案：", prompt)
        self.assertIn("第 1 张原图：画面主体：人物坐在书桌前", prompt)
        self.assertIn("不要过度改款", prompt)
        original = generate_original_content(
            item,
            reference_content="参考内容",
            product_info="",
            audience="",
            style="小红书风格",
            target_word_count=200,
            text_model="gpt-4o-mini",
            settings={"mock_mode": True},
            image_analysis=["画面主体：人物坐在书桌前。"],
        )
        self.assertTrue(original.title)

    def test_rewrite_prompt_without_reference_content(self):
        item = HotContentItem(
            source_mode="链接采集",
            title="久坐党护理清单",
            body="减少反复摩擦，保持清爽",
            tags=["肛周护理"],
        )
        prompt = build_rewrite_prompt(
            item,
            reference_content="",
            product_info="",
            audience="",
            style="小红书风格",
            target_word_count=300,
        )
        self.assertIn("二次原创仿写", prompt)
        self.assertNotIn("参考内容是最高优先级事实来源", prompt)
        self.assertIn("不得复制原句", prompt)
        original = generate_original_content(
            item,
            reference_content="",
            product_info="",
            audience="",
            style="小红书风格",
            target_word_count=300,
            text_model="gpt-4o-mini",
            settings={"mock_mode": True},
        )
        self.assertTrue(original.title)
        self.assertGreaterEqual(len(original.pages), 4)

    def test_rewrite_prompt_strips_authorship_content(self):
        body = (
            "肛周脓肿早期要注意，别硬扛。\n"
            "作者：南方医院消化内科 张三\n"
            "本文由南方医院消化内科审核\n"
            "@南方医院消化内科\n"
            "平时少吃辣，多喝水。\n"
            "来源：小红书"
        )
        self.assertEqual(
            strip_signature_lines(body),
            "肛周脓肿早期要注意，别硬扛。\n平时少吃辣，多喝水。",
        )
        cleaned = strip_signature_lines(body)
        self.assertNotIn("张三", cleaned)
        self.assertNotIn("南方医院", cleaned)
        prompt = build_rewrite_prompt(
            HotContentItem(
                source_mode="链接采集",
                title="久坐党护理",
                body=body,
                tags=["肛周护理"],
            ),
            reference_content="参考内容",
            product_info="",
            audience="",
            style="小红书风格",
            target_word_count=300,
        )
        self.assertIn("去掉署名类内容", prompt)
        self.assertIn("医生提醒", prompt)
        self.assertNotIn("张三", prompt)
        self.assertNotIn("南方医院消化内科审核", prompt)
        image_copy_prompt = build_image_copy_prompt(
            HotContentItem(source_mode="链接采集", title="久坐党护理"),
            image_analysis=["图中文字：1. 标题：别反复摩擦"],
            style="小红书风格",
            product_info="",
            audience="",
        )
        self.assertIn("去掉署名类内容", image_copy_prompt)

    def test_generated_body_drops_signature_lines(self):
        class StubClient:
            def chat_completion(self, **kwargs) -> str:
                return json.dumps(
                    {
                        "title": "久坐党护理",
                        "cover_title": "久坐党护理",
                        "body": "别硬扛。\n作者：南方医院消化内科 张三\n多喝水。",
                        "tags": ["肛周护理"],
                        "pages": [
                            {
                                "heading": "别硬扛",
                                "copy": "别硬扛。",
                                "on_image_text": "别硬扛",
                                "image_prompt": "生成一张3:4竖版图。",
                            }
                        ],
                        "video_prompt": "镜头推进。",
                    },
                    ensure_ascii=False,
                )

        original = generate_original_content(
            HotContentItem(
                source_mode="链接采集",
                title="久坐党护理",
                tags=["肛周护理"],
            ),
            reference_content="参考内容",
            product_info="",
            audience="",
            style="小红书风格",
            target_word_count=200,
            text_model="gpt-4o-mini",
            settings={
                "mock_mode": False,
                "base_url": "https://api.apib.ai/v1",
                "api_key": "test-key",
            },
            client=StubClient(),
        )
        self.assertEqual(original.body, "别硬扛。\n多喝水。")
        self.assertNotIn("南方医院", original.body)

    def test_replicate_reference_instruction(self):
        text = build_reference_instruction("replicate", image_count=2)
        self.assertIn("复刻", text)
        self.assertIn("尽量接近", text)
        self.assertEqual(
            build_reference_instruction("replicate", image_count=0),
            "",
        )

    def test_xhs_detail_extracts_note_images(self):
        fixture = self.root / "tests" / "fixtures" / "xhs_collect_images.html"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel="msedge",
                headless=True,
            )
            page = browser.new_page()
            page.goto(fixture.as_uri(), wait_until="domcontentloaded")
            detail = _extract_detail(page)
            self.assertEqual(
                detail.image_urls,
                [
                    "https://sns-webpic-qc.xhscdn.com/note-1.jpg",
                    "https://sns-webpic-qc.xhscdn.com/note-2.webp",
                ],
            )
            self.assertIn("肛周护理", detail.tags)
            browser.close()

    def test_download_note_images_saves_files(self):
        payload = _note_like_png()
        blob_payload = _blob_png()
        icon_buffer = BytesIO()
        with Image.new("RGB", (64, 64), "white") as icon:
            icon.save(icon_buffer, format="PNG")
        icon_payload = icon_buffer.getvalue()

        class FakeResponse:
            ok = True
            headers = {"content-type": "image/png"}

            def body(self) -> bytes:
                return payload

        class FakeIconResponse(FakeResponse):
            def body(self) -> bytes:
                return icon_payload

        class FakeBlobResponse(FakeResponse):
            def body(self) -> bytes:
                return blob_payload

        class FakeRequest:
            def get(self, url: str, **kwargs):
                if url.endswith("/bad"):
                    raise RuntimeError("下载失败")
                if "/icon" in url:
                    return FakeIconResponse()
                if "/blob" in url:
                    return FakeBlobResponse()
                return FakeResponse()

        class FakeContext:
            request = FakeRequest()

        with tempfile.TemporaryDirectory() as temp:
            saved = download_note_images(
                FakeContext(),
                [
                    "https://sns-webpic-qc.xhscdn.com/note-1",
                    "https://sns-webpic-qc.xhscdn.com/icon-1",
                    "https://sns-webpic-qc.xhscdn.com/blob-1",
                    "https://sns-webpic-qc.xhscdn.com/bad",
                    "https://sns-webpic-qc.xhscdn.com/note-2",
                ],
                Path(temp),
            )
            self.assertEqual(len(saved), 2)
            self.assertEqual(
                [Path(path).name for path in saved],
                ["原图01.jpg", "原图02.jpg"],
            )
            with Image.open(saved[0]) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.size, (900, 1200))

    def test_note_image_filters_skip_icons_and_stickers(self):
        self.assertTrue(is_useful_note_image(900, 1200))
        self.assertFalse(is_useful_note_image(64, 64))
        self.assertFalse(is_useful_note_image(1080, 120))
        with Image.open(BytesIO(_note_like_png())) as photo:
            self.assertFalse(is_flat_placeholder(photo))
        with Image.new("RGB", (600, 600), "white") as solid:
            self.assertTrue(is_flat_placeholder(solid))
        with Image.new("RGB", (600, 600), "#123A6B") as placeholder:
            ImageDraw.Draw(placeholder).polygon(
                [(300, 150), (450, 450), (150, 450)],
                fill="#FFFFFF",
            )
            self.assertTrue(is_flat_placeholder(placeholder))
        self.assertFalse(
            is_note_image_url("https://sns-avatar-qc.xhscdn.com/avatar/1")
        )
        self.assertFalse(
            is_note_image_url("https://sns-webpic-qc.xhscdn.com/emoji/1.png")
        )
        self.assertFalse(
            is_note_image_url("https://sns-webpic-qc.xhscdn.com/sticker/1.png")
        )
        self.assertTrue(
            is_note_image_url("https://sns-webpic-qc.xhscdn.com/note-1")
        )

    def test_low_information_images_are_skipped(self):
        with Image.open(BytesIO(_blob_png())) as blob:
            self.assertTrue(is_low_information_image(blob))
        with Image.open(BytesIO(_note_like_png())) as card:
            self.assertFalse(is_low_information_image(card))
        with Image.new("RGB", (1080, 1440), "#FCFBFD") as blank:
            self.assertTrue(is_low_information_image(blank))

    def test_clean_note_title_strips_platform_signature(self):
        self.assertEqual(
            clean_note_title("拉粑粑痛？便秘/肛裂/痔疮 - 小红书"),
            "拉粑粑痛？便秘/肛裂/痔疮",
        )
        self.assertEqual(
            clean_note_title("久坐党日常护理的三个习惯"),
            "久坐党日常护理的三个习惯",
        )

    def test_image_page_loads_collected_reference_images(self):
        window = MainWindow()
        page = window.image_page
        with tempfile.TemporaryDirectory() as temp:
            image_path = Path(temp) / "原图01.jpg"
            image_path.write_bytes(b"fake-image")
            original = OriginalContent(
                title="标题",
                body="正文",
                tags=["肛周护理"],
                cover_title="封面",
                pages=[
                    {
                        "heading": "标题",
                        "copy": "文案",
                        "image_prompt": "提示词",
                    }
                ],
            )
            page.load_generation_package(
                original,
                image_prompts=["提示词"],
                image_style="手绘卡通",
                reference_images=[str(image_path)],
            )
            self.assertEqual(page.reference_list.count(), 1)
            self.assertEqual(
                page.reference_mode_combo.currentData(),
                "replicate",
            )
            self.assertIn("复刻", page.reference_prompt_instruction())
        window.close()

    def test_hot_content_clear_button(self):
        window = MainWindow()
        page = window.hot_content_page
        page.items = [
            HotContentItem(
                source_mode="链接采集",
                title="待清空内容",
            )
        ]
        page._refresh_table()
        page.reference_edit.setPlainText("测试参考内容")
        page.original = OriginalContent(
            title="测试标题",
            body="测试正文",
            tags=["测试"],
            cover_title="测试封面",
            pages=[{"heading": "", "copy": "测试", "image_prompt": "测试"}],
        )
        page.original_title_edit.setText("测试标题")
        page.original_body_edit.setPlainText("测试正文")
        with patch(
            "app.QtWidgets.QMessageBox.question",
            return_value=QtWidgets.QMessageBox.StandardButton.Yes,
        ):
            page.clear_all_content()
        self.assertEqual(page.items, [])
        self.assertIsNone(page.original)
        self.assertEqual(page.table.rowCount(), 0)
        self.assertFalse(page.reference_edit.toPlainText())
        self.assertFalse(page.original_title_edit.text())
        window.close()

    def test_hot_rewrite_thread_returns_image_analysis(self):
        window = MainWindow()
        page = window.hot_content_page
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "原图01.jpg"
            with Image.new("RGB", (32, 32), "white") as image:
                image.save(path)
            item = HotContentItem(
                source_mode="链接采集",
                title="久坐党护理",
                tags=["肛周护理", "久坐党"],
                images=[str(path)],
            )
            page.items = [item]
            page._refresh_table()
            page.table.selectRow(0)
            thread = HotRewriteThread(
                {
                    "item": item,
                    "reference_content": "1、如厕后减少反复摩擦",
                    "product_info": "",
                    "audience": "",
                    "style": "小红书风格",
                    "target_word_count": 200,
                    "text_model": "gpt-4o-mini",
                    "settings": {"mock_mode": True},
                }
            )
            received: list = []
            thread.result.connect(received.append)
            thread.run()
        self.assertEqual(len(received), 1)
        original, image_analysis, copy_error = received[0]
        self.assertTrue(original.tags)
        self.assertEqual(len(image_analysis), 1)
        self.assertEqual(copy_error, "")
        self.assertEqual(len(original.image_copy_items), 1)
        self.assertTrue(original.image_copy_items[0]["image_prompt"])
        page._on_rewrite_result(received[0])
        self.assertEqual(len(page.image_analysis), 1)
        self.assertIn("久坐党护理", page.image_analysis_edit.toPlainText())
        self.assertIn("二次原创", page.image_copy_edit.toPlainText())
        self.assertIn("生成一张", page.image_prompt_edit.toPlainText())
        self.assertEqual(page.collected_image_list.count(), 1)
        self.assertIs(page.result_tabs.currentWidget(), page.original_page)
        window.close()

    def test_hot_rewrite_allows_empty_reference_pack(self):
        window = MainWindow()
        page = window.hot_content_page
        page.items = [
            HotContentItem(
                source_mode="链接采集",
                title="久坐党护理清单",
                body="减少反复摩擦，保持清爽",
                tags=["肛周护理"],
            )
        ]
        page._refresh_table()
        page.table.selectRow(0)
        self.assertFalse(page.reference_edit.toPlainText())
        self.assertIn("可不填", page.reference_edit.placeholderText())
        warnings: list = []
        with patch.object(
            page.main_window.settings_store,
            "as_dict",
            return_value={"mock_mode": True},
        ), patch(
            "app.QtWidgets.QMessageBox.warning",
            side_effect=lambda *args, **kwargs: warnings.append(args),
        ):
            page.start_rewrite()
            deadline = time.time() + 30
            while page.rewrite_thread is not None and time.time() < deadline:
                self.app.processEvents()
                time.sleep(0.02)
        self.assertEqual(warnings, [])
        self.assertIsNotNone(page.original)
        self.assertTrue(page.original.title)
        self.assertEqual(len(page.original.tags), 1)
        window.close()

    def test_hot_content_tab_flow_and_image_rows(self):
        window = MainWindow()
        page = window.hot_content_page
        self.assertEqual(
            [
                page.result_tabs.tabText(index)
                for index in range(page.result_tabs.count())
            ],
            [
                "采集内容",
                "图片采集",
                "原创文案",
                "图片文案二创",
                "生图提示词",
                "分页脚本",
                "视频提示词",
            ],
        )
        with tempfile.TemporaryDirectory() as temp:
            paths: list[str] = []
            for index in range(2):
                path = Path(temp) / f"原图{index + 1:02d}.jpg"
                with Image.new("RGB", (16, 16), "white") as image:
                    image.save(path, format="JPEG")
                paths.append(str(path))
            page.items = [
                HotContentItem(
                    source_mode="链接采集",
                    title="久坐党护理",
                    body="减少反复摩擦",
                    tags=["肛周护理"],
                    images=paths,
                )
            ]
            page._refresh_table()
            page.table.selectRow(0)
            self.app.processEvents()
            self.assertEqual(page.collected_image_list.count(), 2)
            self.assertFalse(page.collected_image_list.item(0).icon().isNull())
            self.assertIn("减少反复摩擦", page.original_source_edit.toPlainText())

            page.original = OriginalContent(
                title="久坐党护理",
                body="正文",
                tags=["肛周护理"],
                cover_title="久坐党护理",
                pages=[
                    {
                        "heading": "别硬擦",
                        "copy": "内容",
                        "image_prompt": "生成一张3:4竖版中文知识科普图。视觉风格：手绘卡通。",
                    }
                ],
                video_prompt="镜头推进。",
                image_copy_items=[
                    {
                        "index": 1,
                        "image_copy": "别老反复擦",
                        "image_prompt": "生成一张3:4竖版中文图文，第一张。",
                    },
                    {
                        "index": 2,
                        "image_copy": "擦完要清爽",
                        "image_prompt": "生成一张3:4竖版中文图文，第二张。",
                    },
                ],
            )
            page.image_analysis = [
                "画面主体：桌面。\n图中文字：1. 标题：别反复摩擦",
                "画面主体：洗手。\n图中文字：1. 标题：保持清爽",
            ]
            page._on_rewrite_result((page.original, page.image_analysis, ""))
            self.assertIn("别老反复擦", page.image_copy_edit.toPlainText())
            self.assertIn("别反复摩擦", page.image_copy_edit.toPlainText())
            self.assertIn("第一张", page.image_prompt_edit.toPlainText())

            page.send_to_image_generation()
            self.app.processEvents()
            self.assertIs(window.stack.currentWidget(), window.image_page)
            table = window.image_page.page_task_table
            self.assertEqual(table.rowCount(), 2)
            self.assertEqual(table.item(0, 2).text(), "别老反复擦")
            self.assertEqual(table.item(1, 4).text(), "生成一张3:4竖版中文图文，第二张。")
            self.assertEqual(len(window.image_page.selected_reference_paths()), 2)
        window.close()

    def test_hot_content_deletes_selected_collected_image(self):
        window = MainWindow()
        page = window.hot_content_page
        with tempfile.TemporaryDirectory() as temp:
            paths: list[str] = []
            for index in range(3):
                path = Path(temp) / f"原图{index + 1:02d}.jpg"
                with Image.new("RGB", (16, 16), "white") as image:
                    image.save(path, format="JPEG")
                paths.append(str(path))
            item = HotContentItem(
                source_mode="链接采集",
                title="久坐党护理",
                images=list(paths),
            )
            page.items = [item]
            page._refresh_table()
            page.table.selectRow(0)
            self.app.processEvents()
            page.image_analysis = ["图一识别", "图二识别", "图三识别"]
            page.original = OriginalContent(
                title="久坐党护理",
                body="正文",
                tags=["肛周护理"],
                cover_title="久坐党护理",
                pages=[{"heading": "要点", "copy": "正文", "image_prompt": "提示词"}],
                image_copy_items=[
                    {"index": 1, "image_copy": "第一张", "image_prompt": "提示词一"},
                    {"index": 2, "image_copy": "第二张", "image_prompt": "提示词二"},
                    {"index": 3, "image_copy": "第三张", "image_prompt": "提示词三"},
                ],
            )
            page.collected_image_list.setCurrentRow(1)
            page.delete_selected_collected_image()
            self.app.processEvents()
            self.assertEqual(page.collected_image_list.count(), 2)
            self.assertEqual(item.images, [paths[0], paths[2]])
            self.assertEqual(page.image_analysis, ["图一识别", "图三识别"])
            self.assertEqual(
                [entry["image_copy"] for entry in page.original.image_copy_items],
                ["第一张", "第三张"],
            )
            self.assertEqual(page.collected_image_list.currentRow(), 0)
            self.assertIn("图一识别", page.image_analysis_edit.toPlainText())
        window.close()

    def test_hot_content_sends_to_video_generation(self):
        window = MainWindow()
        page = window.hot_content_page
        page.original = OriginalContent(
            title="久坐党护理",
            body="测试正文",
            tags=["肛周护理"],
            cover_title="久坐党护理",
            pages=[{"heading": "要点", "copy": "测试正文", "image_prompt": "测试"}],
            video_prompt="镜头缓慢推进，图标依次出现。",
        )
        page.original_title_edit.setText("久坐党护理")
        page.video_prompt_edit.setPlainText("镜头缓慢推进，图标依次出现。")
        page.send_to_video_generation()
        self.assertIs(window.stack.currentWidget(), window.video_page)
        self.assertEqual(window.video_page.video_theme.text(), "久坐党护理")
        self.assertEqual(
            window.video_page.prompt_edit.toPlainText(),
            "镜头缓慢推进，图标依次出现。",
        )
        window.close()

    def test_video_page_loads_generated_images(self):
        window = MainWindow()
        page = window.video_page
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "新图01.png"
            with Image.new("RGB", (32, 32), "white") as image:
                image.save(path)
            missing = Path(temp) / "不存在.png"
            page.load_video_package(
                theme="图文转视频",
                video_prompt="镜头推进。",
                image_paths=[str(path), str(path), str(missing)],
            )
            self.assertEqual(page.image_list.count(), 1)
            self.assertEqual(page.image_list.item(0).text(), str(path))
        self.assertEqual(page.image_count_label.text(), "1 张")
        window.close()

    def test_ui_smoke(self):
        window = MainWindow()
        window.show()
        self.app.processEvents()
        self.assertEqual(window.stack.count(), 9)
        self.assertEqual(window.stack.currentIndex(), 0)
        self.assertEqual(
            [button.text() for button in window.nav_buttons],
            [
                "爆款重构",
                "图文生成",
                "视频生成",
                "批量处理",
                "发布中心",
                "剪映草稿",
                "Hypit视频",
                "模型中心",
                "配置",
            ],
        )
        self.assertIs(window.stack.currentWidget(), window.hot_content_page)
        self.assertEqual(window.models_page.tabs.count(), 4)
        self.assertEqual(window.publish_page.tabs.count(), 6)
        self.assertEqual(
            window.publish_page.tabs.tabText(5),
            "三方发布",
        )
        self.assertEqual(
            window.hot_content_page.collection_mode_combo.count(),
            2,
        )
        self.assertEqual(
            window.hot_content_page.content_style_combo.currentText(),
            "小红书风格",
        )
        self.assertEqual(
            window.hot_content_page.content_style_combo.count(),
            6,
        )
        self.assertTrue(
            window.hot_content_page.content_style_combo.toolTip()
        )
        hot_buttons = {
            button.text()
            for button in window.hot_content_page.findChildren(
                QtWidgets.QPushButton
            )
        }
        self.assertIn("清空内容", hot_buttons)
        self.assertTrue(window.hot_content_page.reference_edit.placeholderText())
        self.assertIn("Hypit CLI", window.hypit_page.environment_label.toPlainText())
        self.assertEqual(window.hypit_page.tutorial_button.text(), "使用教程")
        self.assertEqual(window.hypit_page.mode_tabs.count(), 2)
        self.assertEqual(window.hypit_page.mode_tabs.tabText(0), "简易模式")
        self.assertEqual(window.hypit_page.mode_tabs.tabText(1), "高级模式")
        self.assertEqual(window.hypit_page.simple_page.start_button.text(), "一键分析并生成工程")
        self.assertEqual(
            window.hypit_page.simple_page.rewrite_button.text(),
            "按创作目标生成原创口播",
        )
        self.assertEqual(
            window.hypit_page.simple_page.build_button.text(),
            "生成原创成片",
        )
        self.assertTrue(window.hypit_page.simple_page.target_title_edit.placeholderText())
        self.assertTrue(window.hypit_page.simple_page.target_hook_edit.placeholderText())
        self.assertEqual(
            window.hypit_page.simple_page.voice_combo.currentText(),
            "nova",
        )
        self.assertEqual(
            window.hypit_page.simple_page.preview_button.text(),
            "试听当前音色和情感",
        )
        self.assertTrue(window.hypit_page.simple_page.segment_emotion_check.isChecked())
        self.assertEqual(
            window.publish_page.platform_widgets["xiaohongshu"][
                "browser_combo"
            ].count(),
            2,
        )
        schedule_check = window.publish_page.platform_widgets[
            "xiaohongshu"
        ]["schedule_check"]
        schedule_edit = window.publish_page.platform_widgets[
            "xiaohongshu"
        ]["schedule_edit"]
        self.assertFalse(schedule_edit.isEnabled())
        schedule_check.setChecked(True)
        self.assertTrue(schedule_edit.isEnabled())
        douyin_widgets = window.publish_page.platform_widgets["douyin"]
        self.assertEqual(douyin_widgets["browser_combo"].count(), 2)
        self.assertTrue(douyin_widgets["supports_schedule"])
        self.assertTrue(douyin_widgets["schedule_check"].isEnabled())
        self.assertIsNotNone(douyin_widgets["music_check"])
        self.assertEqual(douyin_widgets["music_tab_combo"].count(), 5)
        self.assertTrue(douyin_widgets["music_query_edit"].placeholderText())
        window.publish_page._on_login_result(
            "xiaohongshu",
            "clear",
            True,
            "小红书独立浏览器配置已清除。",
        )
        self.assertIn(
            "未登录",
            window.publish_page.platform_widgets["xiaohongshu"][
                "login_status"
            ].text(),
        )
        window.publish_page._on_login_result(
            "douyin",
            "clear",
            True,
            "抖音独立浏览器配置已清除。",
        )
        self.assertIn(
            "未登录",
            window.publish_page.platform_widgets["douyin"][
                "login_status"
            ].text(),
        )
        self.assertGreater(len(FALLBACK_MODEL_CATALOG["image"]), 0)
        self.assertGreater(len(FALLBACK_MODEL_CATALOG["video"]), 0)
        self.assertGreater(len(FALLBACK_MODEL_CATALOG["audio"]), 0)
        self.assertGreater(len(FALLBACK_MODEL_CATALOG["chat"]), 0)
        self.assertTrue(window.image_page.output_edit.text())
        self.assertTrue(window.image_page.cover_title_edit.placeholderText())
        self.assertEqual(window.image_page.cover_title_edit.maxLength(), 40)
        window.image_page.cover_title_edit.setText("测试封面标题超过十四个汉字需要提醒用户")
        self.assertEqual(window.image_page.cover_counter_label.text(), "19/14")
        self.assertEqual(window.image_page.cover_button.text(), "生成封面图")
        self.assertEqual(window.image_page.generation_mode_combo.count(), 2)
        self.assertEqual(window.image_page.preview_tabs.count(), 2)
        self.assertTrue(window.video_page.output_edit.text())
        self.assertTrue(window.batch_page.output_edit.text())
        test_catalog = {
            "image": [{"id": "test-image-model", "capability_tags": ["Text to Image"]}],
            "video": [{"id": "test-video-model", "capability_tags": ["Image to Video"]}],
            "audio": [{"id": "test-audio-model", "capability_tags": ["Audio"]}],
            "chat": [{"id": "test-text-model", "capability_tags": ["Text"]}],
        }
        window.set_model_catalog(test_catalog, "测试清单")
        self.assertEqual(window.image_page.model_combo.currentText(), "test-image-model")
        self.assertEqual(window.video_page.model_combo.currentText(), "test-video-model")
        window.close()


if __name__ == "__main__":
    unittest.main()
