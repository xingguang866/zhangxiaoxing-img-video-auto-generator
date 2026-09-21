from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

from publish_platforms import PLATFORMS


def edge_executable() -> Path | None:
    candidates = [
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def browser_profile_dir(platform_key: str) -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    path = root / "ZhangXiaoxingGenerator" / "browser_profiles" / platform_key
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_platform_page(platform_key: str, url: str | None = None) -> Path:
    profile = PLATFORMS[platform_key]
    executable = edge_executable()
    if not executable:
        raise RuntimeError("未找到 Microsoft Edge，无法打开发布页面。")
    command = [
        str(executable),
        f"--user-data-dir={browser_profile_dir(platform_key)}",
        "--new-window",
        url or profile.publish_url,
    ]
    subprocess.Popen(command, close_fds=True)
    return executable


def _first_visible(page: Page, selectors: list[str]):
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible(timeout=500):
                return locator
        except Exception:
            continue
    return None


def _fill_field(page: Page, selectors: list[str], value: str) -> bool:
    locator = _first_visible(page, selectors)
    if locator is None or not value:
        return False
    try:
        locator.fill(value, timeout=3000)
        return True
    except Exception:
        try:
            locator.click(timeout=2000)
            page.keyboard.press("Control+A")
            page.keyboard.type(value, delay=5)
            return True
        except Exception:
            return False


def _upload_files(page: Page, media_paths: list[str | Path]) -> int:
    paths = [str(Path(path)) for path in media_paths if Path(path).exists()]
    if not paths:
        return 0
    try:
        inputs = page.locator("input[type=file]")
        count = inputs.count()
    except Exception:
        return 0

    for index in range(count):
        locator = inputs.nth(index)
        try:
            multiple = locator.get_attribute("multiple")
            if multiple is not None:
                locator.set_input_files(paths, timeout=5000)
                return len(paths)
            if len(paths) == 1:
                locator.set_input_files(paths[0], timeout=5000)
                return 1
        except Exception:
            continue

    if count:
        try:
            inputs.first.set_input_files(paths[0], timeout=5000)
            return 1
        except Exception:
            return 0
    return 0


def assist_upload(
    *,
    platform_key: str,
    media_paths: list[str | Path],
    title: str,
    description: str,
    keep_open: bool = True,
) -> dict:
    profile = PLATFORMS[platform_key]
    executable = edge_executable()
    if not executable:
        raise RuntimeError("未找到 Microsoft Edge，无法执行辅助上传。")

    result = {
        "platform": profile.name,
        "uploaded_files": 0,
        "title_filled": False,
        "description_filled": False,
        "message": "",
    }
    with sync_playwright() as playwright:
        context: BrowserContext = playwright.chromium.launch_persistent_context(
            user_data_dir=str(browser_profile_dir(platform_key)),
            executable_path=str(executable),
            headless=False,
            accept_downloads=True,
            viewport={"width": 1440, "height": 900},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(profile.publish_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3500)

            result["uploaded_files"] = _upload_files(page, media_paths)
            page.wait_for_timeout(1500)
            result["title_filled"] = _fill_field(
                page,
                [
                    'input[placeholder*="标题"]',
                    'textarea[placeholder*="标题"]',
                    'input[placeholder*="填写"]',
                ],
                title,
            )
            result["description_filled"] = _fill_field(
                page,
                [
                    'textarea[placeholder*="简介"]',
                    'textarea[placeholder*="描述"]',
                    'textarea[placeholder*="正文"]',
                    'textarea[placeholder*="说点什么"]',
                    '[contenteditable="true"]',
                ],
                description,
            )
            result["message"] = (
                f"已打开{profile.name}官方发布页。"
                f"文件加载 {result['uploaded_files']} 个，标题自动填写"
                f"{'完成' if result['title_filled'] else '未完成'}，简介自动填写"
                f"{'完成' if result['description_filled'] else '未完成'}。"
                "请在浏览器中检查并手动点击最终发布。"
            )

            if keep_open:
                while context.pages:
                    page.wait_for_timeout(1000)
        finally:
            try:
                context.close()
            except Exception:
                pass
    return result
