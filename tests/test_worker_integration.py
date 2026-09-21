from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from app import GenerationWorker


class WorkerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _run_worker(self, worker: GenerationWorker) -> tuple[dict, list[dict], list[dict]]:
        loop = QtCore.QEventLoop()
        summary: dict = {}
        images: list[dict] = []
        videos: list[dict] = []
        errors: list[str] = []
        worker.signals.image_ready.connect(images.append)
        worker.signals.video_ready.connect(videos.append)
        worker.signals.error.connect(errors.append)

        def finish(result: dict):
            summary.update(result)
            loop.quit()

        worker.signals.finished.connect(finish)
        worker.start()
        QtCore.QTimer.singleShot(60_000, loop.quit)
        loop.exec()
        worker.wait(10_000)
        if errors:
            self.fail(errors[0])
        return summary, images, videos

    def test_worker_image_then_video(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            settings = {
                "api_key": "",
                "base_url": "https://api.apib.ai/v1",
                "output_dir": str(output),
                "mock_mode": True,
                "image_model": "gpt-image-2",
                "image_size": "3:4",
                "image_resolution": "1k",
                "video_model": "seedance-2.0-fast",
                "video_size": "9:16",
                "video_resolution": "720p",
                "video_duration": 2,
                "generate_audio": False,
            }
            image_worker = GenerationWorker(
                "manual_images",
                settings,
                {
                    "output_dir": str(output / "images"),
                    "items": [
                        {
                            "title": "测试图片",
                            "style": "手绘卡通",
                            "prompt": "生成一张3:4竖版中文知识科普图。主题：测试。画面内容：1、减少摩擦。",
                        }
                    ],
                },
            )
            image_summary, images, _ = self._run_worker(image_worker)
            self.assertEqual(image_summary.get("generated"), 1)
            self.assertEqual(len(images), 1)
            self.assertTrue(Path(images[0]["path"]).exists())

            video_worker = GenerationWorker(
                "manual_videos",
                settings,
                {
                    "output_dir": str(output / "videos"),
                    "items": [
                        {
                            "title": "测试视频",
                            "prompt": "镜头缓慢推进，保持画面风格。",
                            "image_paths": [images[0]["path"]],
                            "image_urls": [],
                        }
                    ],
                },
            )
            video_summary, _, videos = self._run_worker(video_worker)
            self.assertEqual(video_summary.get("generated"), 1)
            self.assertEqual(len(videos), 1)
            self.assertTrue(Path(videos[0]["path"]).exists())
            self.assertGreater(Path(videos[0]["path"]).stat().st_size, 10_000)


if __name__ == "__main__":
    unittest.main()
