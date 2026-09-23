from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable
from datetime import datetime
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


def chrome_executable() -> Path | None:
    candidates = [
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def browser_executable(browser_name: str = "edge") -> Path:
    normalized = browser_name.strip().lower()
    if normalized == "chrome":
        executable = chrome_executable()
        if executable:
            return executable
        raise RuntimeError("未找到 Google Chrome，请安装 Chrome 或改用 Edge。")
    executable = edge_executable()
    if executable:
        return executable
    raise RuntimeError("未找到 Microsoft Edge，请安装 Edge 或改用 Chrome。")


def browser_profile_dir(platform_key: str) -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    path = root / "ZhangXiaoxingGenerator" / "browser_profiles" / platform_key
    path.mkdir(parents=True, exist_ok=True)
    return path


def terminate_platform_browser_processes(platform_key: str) -> list[int]:
    profile = str(browser_profile_dir(platform_key)).lower()
    terminated: list[int] = []
    try:
        import psutil
    except ImportError:
        return terminated
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = str(process.info.get("name") or "").lower()
            if name not in {"msedge.exe", "chrome.exe"}:
                continue
            command = " ".join(process.info.get("cmdline") or []).lower()
            if profile not in command:
                continue
            process.terminate()
            terminated.append(int(process.info["pid"]))
        except (psutil.Error, OSError, ValueError):
            continue
    if terminated:
        time.sleep(1.0)
        for pid in terminated:
            try:
                process = psutil.Process(pid)
                if process.is_running():
                    process.kill()
            except (psutil.Error, OSError):
                continue
    return terminated


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


def _persistent_context(
    playwright,
    platform_key: str,
    *,
    headless: bool,
    browser_name: str = "edge",
):
    executable = browser_executable(browser_name)
    arguments = [
        "--disable-features=msEdgeSidebarV2",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    try:
        return playwright.chromium.launch_persistent_context(
            user_data_dir=str(browser_profile_dir(platform_key)),
            executable_path=str(executable),
            headless=headless,
            accept_downloads=True,
            viewport={"width": 1440, "height": 900},
            args=arguments,
        )
    except Exception as first_error:
        terminated = terminate_platform_browser_processes(platform_key)
        if not terminated:
            raise RuntimeError(
                "浏览器启动失败。请关闭该平台专用 Edge 窗口后重试。"
            ) from first_error
        try:
            return playwright.chromium.launch_persistent_context(
                user_data_dir=str(browser_profile_dir(platform_key)),
                executable_path=str(executable),
                headless=headless,
                accept_downloads=True,
                viewport={"width": 1440, "height": 900},
                args=arguments,
            )
        except Exception as second_error:
            raise RuntimeError(
                "浏览器配置仍被其他进程占用，且自动清理失败。"
            ) from second_error


def _xiaohongshu_logged_in(page: Page, context: BrowserContext) -> bool:
    try:
        if not str(page.locator("body").inner_text() or "").strip():
            return False
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


def xiaohongshu_login_status(
    *,
    browser_name: str = "edge",
) -> tuple[bool, str]:
    with sync_playwright() as playwright:
        context = _persistent_context(
            playwright,
            "xiaohongshu",
            headless=True,
            browser_name=browser_name,
        )
        try:
            page = context.new_page()
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


def xiaohongshu_login(
    *,
    timeout_seconds: int = 300,
    browser_name: str = "edge",
) -> tuple[bool, str]:
    with sync_playwright() as playwright:
        context = _persistent_context(
            playwright,
            "xiaohongshu",
            headless=False,
            browser_name=browser_name,
        )
        try:
            page = context.new_page()
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
    terminate_platform_browser_processes("xiaohongshu")
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


def _commit_input_value(locator, value: str) -> bool:
    try:
        locator.click(timeout=3000)
        locator.fill(value, timeout=3000)
        locator.evaluate(
            """(element, nextValue) => {
                const setter = Object.getOwnPropertyDescriptor(
                    HTMLInputElement.prototype,
                    'value'
                )?.set;
                if (setter) setter.call(element, nextValue);
                element.dispatchEvent(new Event('input', { bubbles: true }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
                element.blur();
            }""",
            value,
        )
        return str(locator.input_value() or "").strip() == value.strip()
    except Exception:
        return False


def _upload_files(
    page: Page,
    media_paths: list[str | Path],
    *,
    accept_contains: str = "",
) -> int:
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
            accept = str(locator.get_attribute("accept") or "").lower()
            if accept_contains and accept_contains.lower() not in accept:
                continue
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
            for index in range(count):
                locator = inputs.nth(index)
                if accept_contains:
                    accept = str(locator.get_attribute("accept") or "").lower()
                    if accept_contains.lower() not in accept:
                        continue
                locator.set_input_files(paths[0], timeout=5000)
                return 1
        except Exception:
            return 0
    return 0


def _wait_for_any(
    page: Page,
    selectors: list[str],
    *,
    timeout_ms: int,
    poll_ms: int = 500,
    require_visible: bool = True,
):
    started = time.monotonic()
    while (time.monotonic() - started) * 1000 < timeout_ms:
        if require_visible:
            locator = _first_visible(page, selectors)
            if locator is not None:
                return locator
        else:
            for selector in selectors:
                locator = page.locator(selector).first
                if locator.count():
                    return locator
        page.wait_for_timeout(poll_ms)
    return None


def _wait_for_xiaohongshu_upload_complete(
    page: Page,
    *,
    media_type: str,
    expected_count: int,
    timeout_ms: int = 300000,
) -> None:
    started = time.monotonic()
    while (time.monotonic() - started) * 1000 < timeout_ms:
        if media_type == "video":
            ready = page.locator("video").count() >= 1
        else:
            previews = page.locator(
                'img.preivew-image, .swiper-slide img, img[src^="blob:"]'
            ).count()
            ready = previews >= expected_count
        uploading = _first_visible(
            page,
            [
                "text=上传中",
                "text=正在上传",
            ],
        )
        if ready and uploading is None:
            page.wait_for_timeout(1500)
            return
        page.wait_for_timeout(1000)
    raise RuntimeError("素材上传超时，页面未完成素材处理。")


def _wait_for_xiaohongshu_publish_result(
    page: Page,
    *,
    timeout_ms: int = 20000,
) -> bool:
    started = time.monotonic()
    while (time.monotonic() - started) * 1000 < timeout_ms:
        error = _first_visible(
            page,
            [
                'text=请填写标题',
                'text=请上传图片',
                'text=请上传视频',
                'text=请选择发布时间',
                'text=发布失败',
                'text=操作失败',
                'text=内容违规',
            ],
        )
        if error is not None:
            raise RuntimeError(
                f"小红书发布校验失败：{error.inner_text(timeout=1000).strip()}"
            )
        success = _first_visible(
            page,
            [
                'text=发布成功',
                'text=已发布',
                'text=定时发布成功',
            ],
        )
        if success is not None or "/publish/publish" not in page.url:
            return True
        page.wait_for_timeout(1000)
    raise RuntimeError(
        f"已点击最终发布，但没有检测到成功结果。当前页面：{page.url}"
    )


def _append_xiaohongshu_tags(page: Page, tags: list[str]) -> int:
    clean_tags = [tag.strip().lstrip("#") for tag in tags if tag.strip().lstrip("#")]
    if not clean_tags:
        return 0
    editor = _wait_for_any(
        page,
        [
            'div.tiptap[contenteditable="true"]',
            '[contenteditable="true"]',
        ],
        timeout_ms=10000,
    )
    if editor is None:
        return 0
    try:
        editor.click(timeout=3000)
        page.keyboard.press("Control+End")
        page.keyboard.press("Enter")
        inserted = 0
        for tag in clean_tags:
            before_count = editor.locator("a.tiptap-topic").count()
            page.keyboard.type(f"#{tag}", delay=30)
            page.wait_for_timeout(1200)
            options = page.locator("span.name")
            selected = False
            for index in range(min(options.count(), 80)):
                option = options.nth(index)
                try:
                    if (option.inner_text(timeout=300) or "").strip() == f"#{tag}":
                        option.locator("xpath=..").click(timeout=3000)
                        selected = True
                        break
                except Exception:
                    continue
            if not selected:
                page.keyboard.press("Enter")
            page.wait_for_timeout(300)
            page.keyboard.press("Space")
            page.wait_for_timeout(300)
            if editor.locator("a.tiptap-topic").count() > before_count:
                inserted += 1
        return inserted
    except Exception:
        return 0


def _configure_xiaohongshu_schedule(
    page: Page,
    schedule_at: str,
) -> tuple[bool, str]:
    schedule_at = schedule_at.strip()
    if not schedule_at:
        return False, ""
    try:
        datetime.strptime(schedule_at, "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise RuntimeError("定时发布时间格式不正确，应为 YYYY-MM-DD HH:MM。") from exc

    switch = _wait_for_any(
        page,
        [
            ".post-time-switch-container .d-switch-simulator",
            ".post-time-switch-container input[type=checkbox]",
        ],
        timeout_ms=10000,
    )
    if switch is None:
        raise RuntimeError("小红书页面没有找到“定时发布”开关。")
    try:
        checkbox = page.locator(
            '.post-time-switch-container input[type="checkbox"]'
        ).first
        if not checkbox.is_checked():
            switch.scroll_into_view_if_needed(timeout=3000)
            switch.click(force=True)
            page.wait_for_timeout(1000)
    except Exception as exc:
        raise RuntimeError(f"开启小红书定时发布失败：{exc}") from exc

    date_input = _wait_for_any(
        page,
        [
            ".date-picker-container input.d-text",
            ".date-picker-container input",
        ],
        timeout_ms=10000,
    )
    if date_input is None:
        raise RuntimeError("没有找到小红书定时发布日期输入框。")
    date_input.click(timeout=3000)
    if not _commit_input_value(date_input, schedule_at):
        raise RuntimeError("定时发布时间没有成功写入页面。")
    page.locator(
        ".post-time-switch-container"
    ).click(timeout=3000, position={"x": 5, "y": 5})
    page.wait_for_timeout(1000)
    value = str(date_input.input_value() or "").strip()
    if value != schedule_at:
        raise RuntimeError(
            f"定时发布时间没有正确写入，页面当前值为：{value or '空'}"
        )
    return True, schedule_at


def _verify_xiaohongshu_draft_fields(
    page: Page,
    *,
    title: str,
    description: str,
    tags: list[str],
    schedule_at: str,
) -> None:
    title_input = _first_visible(
        page,
        [
            'input[placeholder*="填写标题"]',
            'input[placeholder*="标题"]',
        ],
    )
    if title_input is None or title_input.input_value().strip() != title.strip():
        raise RuntimeError("发布前校验失败：标题没有写入小红书页面。")

    editor = _first_visible(
        page,
        [
            'div.tiptap[contenteditable="true"]',
            '[contenteditable="true"]',
        ],
    )
    if editor is None:
        raise RuntimeError("发布前校验失败：没有找到正文编辑器。")
    editor_text = str(editor.inner_text() or "")
    if description.strip() and description.strip() not in editor_text:
        raise RuntimeError("发布前校验失败：正文没有写入小红书页面。")
    expected_tags = len([tag for tag in tags if tag.strip()])
    if expected_tags and editor.locator("a.tiptap-topic").count() < expected_tags:
        raise RuntimeError("发布前校验失败：话题标签没有全部写入。")
    if schedule_at:
        checkbox = page.locator(
            '.post-time-switch-container input[type="checkbox"]'
        ).first
        date_input = page.locator(".date-picker-container input").first
        if not checkbox.is_checked():
            raise RuntimeError("发布前校验失败：定时发布开关没有开启。")
        if str(date_input.input_value() or "").strip() != schedule_at:
            raise RuntimeError("发布前校验失败：定时发布时间没有写入。")


def _find_xiaohongshu_publish_button(page: Page):
    return _wait_for_any(
        page,
        [
            '.publish-video .btn-wrapper:has-text("发布笔记")',
            'div.btn-wrapper:has-text("发布笔记")',
            'div.btn-inner:has-text("发布笔记")',
            'button:has-text("发布笔记")',
            'button:has-text("发布")',
        ],
        timeout_ms=5000,
    )


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
            page = context.new_page()
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
    browser_name: str = "edge",
    auto_publish: bool = False,
    schedule_at: str = "",
    progress: Callable[[str], None] | None = None,
) -> dict:
    notify = progress or (lambda _message: None)
    existing = [Path(path) for path in media_paths if Path(path).exists()]
    if not existing:
        raise RuntimeError("没有可上传的图片或视频。")

    with sync_playwright() as playwright:
        context = _persistent_context(
            playwright,
            "xiaohongshu",
            headless=False,
            browser_name=browser_name,
        )
        result = {
            "platform": "小红书",
            "uploaded_files": 0,
            "title_filled": False,
            "description_filled": False,
            "tags_filled": False,
            "published": False,
            "scheduled": False,
            "scheduled_at": "",
            "message": "",
        }
        try:
            page = context.pages[0] if context.pages else context.new_page()
            notify("正在打开小红书官方发布页...")
            page.goto(
                XIAOHONGSHU_PUBLISH_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(3000)
            if not _xiaohongshu_logged_in(page, context):
                raise RuntimeError("小红书未登录，请先点击“扫码登录小红书”。")

            mode_text = "上传视频" if media_type == "video" else "上传图文"
            notify(f"正在切换{mode_text}模式...")
            mode_button = page.locator('.creator-tab').filter(
                has_text=mode_text
            ).last
            if mode_button.count() == 0:
                mode_button = _first_visible(
                    page,
                    [
                        f'button:has-text("{mode_text}")',
                        f'div[role="tab"]:has-text("{mode_text}")',
                    ],
                )
            if mode_button is not None:
                try:
                    mode_button.scroll_into_view_if_needed(timeout=3000)
                    mode_button.click(timeout=3000)
                    page.wait_for_timeout(1500)
                except Exception:
                    pass

            accept_filter = ".mp4" if media_type == "video" else ".jpg"
            input_ready = _wait_for_any(
                page,
                [
                    f'input[type="file"][accept*="{accept_filter}"]',
                    'input[type="file"]',
                ],
                timeout_ms=15000,
                require_visible=False,
            )
            if input_ready is None:
                raise RuntimeError(
                    f"没有找到{mode_text}的文件上传控件，请检查页面是否仍停留在发布页。"
                )
            notify(f"正在上传{len(existing)}个素材...")
            result["uploaded_files"] = _upload_files(
                page,
                existing,
                accept_contains=accept_filter,
            )
            if result["uploaded_files"] == 0:
                raise RuntimeError("没有找到小红书的图片或视频上传控件。")

            notify("素材已提交，正在等待上传完成...")
            _wait_for_xiaohongshu_upload_complete(
                page,
                media_type=media_type,
                expected_count=result["uploaded_files"],
            )
            notify("素材上传完成，正在等待编辑区域...")
            title_input = _wait_for_any(
                page,
                [
                    'input[placeholder*="填写标题"]',
                    'input[placeholder*="标题"]',
                    'input[maxlength="20"]',
                ],
                timeout_ms=180000,
                poll_ms=1000,
            )
            if title_input is None:
                raise RuntimeError(
                    "素材上传后没有出现标题输入框，请检查平台错误提示或素材格式。"
                )
            notify("正在填写标题、正文和标签...")
            title_input = _first_visible(
                page,
                [
                    'input[placeholder*="填写标题"]',
                    'input[placeholder*="标题"]',
                ],
            )
            result["title_filled"] = bool(
                title_input is not None
                and _commit_input_value(title_input, title)
            )
            result["description_filled"] = _fill_field(
                page,
                [
                    'textarea[placeholder*="正文"]',
                    'textarea[placeholder*="描述"]',
                    'textarea[placeholder*="填写"]',
                    'div[contenteditable="true"]',
                    '[contenteditable="true"]',
                ],
                description,
            )
            inserted_tags = _append_xiaohongshu_tags(page, tags)
            result["tags_filled"] = inserted_tags == len(
                [tag for tag in tags if tag.strip()]
            )
            if schedule_at.strip():
                notify(f"正在设置定时发布：{schedule_at}...")
                scheduled, actual_schedule = _configure_xiaohongshu_schedule(
                    page,
                    schedule_at,
                )
                result["scheduled"] = scheduled
                result["scheduled_at"] = actual_schedule
            _verify_xiaohongshu_draft_fields(
                page,
                title=title,
                description=description,
                tags=tags,
                schedule_at=schedule_at,
            )
            if auto_publish:
                publish_button = _find_xiaohongshu_publish_button(page)
                if publish_button is None:
                    raise RuntimeError(
                        "未找到小红书最终发布按钮，页面已保留。"
                    )
                publish_button.click(timeout=5000)
                result["published"] = _wait_for_xiaohongshu_publish_result(page)
            result["message"] = (
                "已打开小红书官方发布页。"
                f"素材加载 {result['uploaded_files']} 个，"
                f"标题自动填写{'完成' if result['title_filled'] else '未完成'}，"
                f"正文自动填写{'完成' if result['description_filled'] else '未完成'}，"
                f"话题标签写入 {inserted_tags}/{len(tags)}。"
                + (
                    (
                        f"已设置定时发布：{result['scheduled_at']}。"
                        "已尝试点击最终发布，请在小红书确认定时任务。"
                        if result["scheduled"]
                        else "已尝试自动点击最终发布，请在浏览器确认发布结果。"
                    )
                    if result["published"]
                    else "请检查预览、图片顺序和标签后，手动点击最终发布。"
                )
            )
            notify("标题、正文和标签处理完成，请人工确认最终发布。")
            if keep_open:
                pages_empty_since: float | None = None
                while True:
                    if context.pages:
                        pages_empty_since = None
                        page.wait_for_timeout(1000)
                        continue
                    if pages_empty_since is None:
                        pages_empty_since = time.monotonic()
                    if time.monotonic() - pages_empty_since > 10:
                        break
                    time.sleep(1)
        finally:
            try:
                context.close()
            except Exception:
                pass
    return result
