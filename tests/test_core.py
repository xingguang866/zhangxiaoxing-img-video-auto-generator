from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from api_client import APIClientError, APIMartClient
from app import FALLBACK_MODEL_CATALOG, MainWindow
from batch_parser import create_batch_template, load_batch_items
from jianying_service import create_jianying_draft
from mock_engine import create_video_thumbnail, generate_mock_image, generate_mock_video
from pricing_utils import format_pricing, format_usage
from publish_platforms import build_platform_posts
from prompts import STYLE_PROMPTS, build_cover_prompt, build_image_prompt


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
        self.assertIn("30%至45%", cover)

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
        source = self.root.parent / "肛周健康科普素材库_60条_含生图提示词.csv"
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

    def test_ui_smoke(self):
        window = MainWindow()
        window.show()
        self.app.processEvents()
        self.assertEqual(window.stack.count(), 7)
        self.assertEqual(window.stack.currentIndex(), 0)
        self.assertEqual(window.models_page.tabs.count(), 4)
        self.assertEqual(window.publish_page.tabs.count(), 5)
        self.assertGreater(len(FALLBACK_MODEL_CATALOG["image"]), 0)
        self.assertGreater(len(FALLBACK_MODEL_CATALOG["video"]), 0)
        self.assertGreater(len(FALLBACK_MODEL_CATALOG["audio"]), 0)
        self.assertGreater(len(FALLBACK_MODEL_CATALOG["chat"]), 0)
        self.assertTrue(window.image_page.output_edit.text())
        self.assertTrue(window.image_page.cover_title_edit.placeholderText())
        self.assertEqual(window.image_page.cover_title_edit.maxLength(), 24)
        self.assertEqual(window.image_page.cover_button.text(), "生成封面图")
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
