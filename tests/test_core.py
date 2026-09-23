from __future__ import annotations

import os
import json
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from api_client import APIClientError, APIConfig, APIMartClient
from app import (
    FALLBACK_MODEL_CATALOG,
    MainWindow,
    build_batch_image_jobs,
    build_manual_image_items,
    count_chinese_characters,
)
from batch_parser import BatchItem, create_batch_template, load_batch_items
from jianying_service import create_jianying_draft
from hypit_service import (
    environment as hypit_environment,
    hypit_version,
    install_apib_provider,
    run_hypit,
)
from hypit_reference import (
    ReferenceWorkflowError,
    align_duration_to_frame,
    heuristic_analysis,
    is_douyin_url,
    normalize_reference_url,
    reference_workspace,
    run_reference_workflow,
    select_best_browser_media,
)
from hypit_rewrite import RewriteOptions, run_originality_workflow
from hypit_tutorial import ASSET_DIR, build_tutorial_html
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
        self.assertIn("45%至60%", cover)
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
        )
        self.assertEqual(content, b"RIFFtest")
        self.assertTrue(captured["url"].endswith("/audio/speech"))
        self.assertEqual(captured["kwargs"]["json"]["input"], "测试配音")
        self.assertEqual(captured["kwargs"]["json"]["prompt"], "测试配音")

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
                self.assertIn('family="noto-sans-sc"', project.svml_path.read_text(encoding="utf-8"))

                relative_run = project.svrun_path.relative_to(project.workspace).as_posix()
                plan = run_hypit(["plan", relative_run, "--json"], cwd=project.workspace, timeout=300)
                self.assertEqual(plan.returncode, 0, plan.stderr or plan.stdout)
                payload = json.loads(plan.stdout)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["unresolvedRequestCount"], 0)
                self.assertEqual(payload["providerRequestCount"], 0)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

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

                relative_run = rewritten.svrun_path.relative_to(rewritten.workspace).as_posix()
                plan = run_hypit(["plan", relative_run, "--json"], cwd=rewritten.workspace, timeout=300)
                self.assertEqual(plan.returncode, 0, plan.stderr or plan.stdout)
                payload = json.loads(plan.stdout)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["unresolvedRequestCount"], 0)
                self.assertEqual(payload["providerRequestCount"], 0)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_ui_smoke(self):
        window = MainWindow()
        window.show()
        self.app.processEvents()
        self.assertEqual(window.stack.count(), 8)
        self.assertEqual(window.stack.currentIndex(), 0)
        self.assertEqual(window.models_page.tabs.count(), 4)
        self.assertEqual(window.publish_page.tabs.count(), 5)
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
