from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

from publish_platforms import PLATFORMS


XIAOHONGSHU_LOGIN_URL = "https://creator.xiaohongshu.com/login"
XIAOHONGSHU_PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish"


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


def _persistent_context(playwright, platform_key: str, *, headless: bool):
    executable = edge_executable()
    if not executable:
        raise RuntimeError("未找到 Microsoft Edge，无法打开平台页面。")
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(browser_profile_dir(platform_key)),
        executable_path=str(executable),
        headless=headless,
        accept_downloads=True,
        viewport={"width": 1440, "height": 900},
    )


def _xiaohongshu_logged_in(page: Page, context: BrowserContext) -> bool:
    try:
        if "/login" in page.url:
            return False
        login_markers = (
            'text=扫码登录',
            'text=手机号登录',
            'text=登录小红书',
        )
        if _first_visible(page, list(login_markers)) is not None:
            return False
        cookie_names = {cookie.get("name") for cookie in context.cookies()}
        if "web_session" in cookie_names:
            return True
        return _first_visible(
            page,
            [
                'text=发布笔记',
                'text=上传图文',
                'text=上传视频',
                'text=创作服务',
            ],
        ) is not None
    except Exception:
        return False


def xiaohongshu_login_status() -> tuple[bool, str]:
    with sync_playwright() as playwright:
        context = _persistent_context(playwright, "xiaohongshu", headless=True)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                XIAOHONGSHU_PUBLISH_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(2500)
            if _xiaohongshu_logged_in(page, context):
                return True, "小红书已登录。"
            return False, "小红书未登录，请点击“扫码登录小红书”。"
        finally:
            context.close()


def xiaohongshu_login(*, timeout_seconds: int = 300) -> tuple[bool, str]:
    with sync_playwright() as playwright:
        context = _persistent_context(playwright, "xiaohongshu", headless=False)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                XIAOHONGSHU_LOGIN_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            started = time.monotonic()
            while time.monotonic() - started < timeout_seconds:
                if not context.pages:
                    return False, "登录窗口已关闭。"
                page.wait_for_timeout(2000)
                if _xiaohongshu_logged_in(page, context):
                    return True, "小红书扫码登录成功，登录状态已保存在独立浏览器配置中。"
            return False, "等待扫码登录超时，请重新点击“扫码登录小红书”。"
        finally:
            context.close()


def clear_xiaohongshu_login() -> str:
    profile = browser_profile_dir("xiaohongshu")
    try:
        shutil.rmtree(profile)
    except FileNotFoundError:
        return "小红书独立浏览器配置已清除。"
    except OSError as exc:
        raise RuntimeError(f"清除小红书登录状态失败：{exc}") from exc
    profile.mkdir(parents=True, exist_ok=True)
    return "小红书独立浏览器配置已清除。"


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


def assist_upload_xiaohongshu(
    *,
    media_paths: list[str | Path],
    media_type: str,
    title: str,
    description: str,
    tags: list[str],
    keep_open: bool = True,
) -> dict:
    existing = [Path(path) for path in media_paths if Path(path).exists()]
    if not existing:
        raise RuntimeError("没有可上传的图片或视频。")

    with sync_playwright() as playwright:
        context = _persistent_context(playwright, "xiaohongshu", headless=False)
        result = {
            "platform": "小红书",
            "uploaded_files": 0,
            "title_filled": False,
            "description_filled": False,
            "tags_filled": False,
            "message": "",
        }
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                XIAOHONGSHU_PUBLISH_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(3000)
            if not _xiaohongshu_logged_in(page, context):
                raise RuntimeError("小红书未登录，请先点击“扫码登录小红书”。")

            mode_text = "上传视频" if media_type == "video" else "上传图文"
            mode_button = _first_visible(
                page,
                [
                    f'text={mode_text}',
                    f'button:has-text("{mode_text}")',
                    f'div[role="tab"]:has-text("{mode_text}")',
                ],
            )
            if mode_button is not None:
                try:
                    mode_button.click(timeout=3000)
                    page.wait_for_timeout(1200)
                except Exception:
                    pass

            result["uploaded_files"] = _upload_files(page, existing)
            if result["uploaded_files"] == 0:
                raise RuntimeError("没有找到小红书的图片或视频上传控件。")

            page.wait_for_timeout(5000)
            result["title_filled"] = _fill_field(
                page,
                [
                    'input[placeholder*="填写标题"]',
                    'input[placeholder*="标题"]',
                    'textarea[placeholder*="标题"]',
                ],
                title,
            )
            full_description = description
            tag_text = " ".join(f"#{tag.lstrip('#')}" for tag in tags if tag.strip())
            if tag_text:
                full_description = f"{full_description}\n\n{tag_text}".strip()
            result["description_filled"] = _fill_field(
                page,
                [
                    'textarea[placeholder*="正文"]',
                    'textarea[placeholder*="描述"]',
                    'textarea[placeholder*="填写"]',
                    'div[contenteditable="true"]',
                    '[contenteditable="true"]',
                ],
                full_description,
            )
            result["tags_filled"] = bool(tag_text and result["description_filled"])
            result["message"] = (
                "已打开小红书官方发布页。"
                f"素材加载 {result['uploaded_files']} 个，"
                f"标题自动填写{'完成' if result['title_filled'] else '未完成'}，"
                f"正文和标签自动填写{'完成' if result['description_filled'] else '未完成'}。"
                "请检查预览、图片顺序和标签后，手动点击最终发布。"
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
