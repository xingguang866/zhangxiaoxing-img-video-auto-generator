from __future__ import annotations

import re
import time
from urllib.parse import quote, urljoin

from playwright.sync_api import Page, sync_playwright

from browser_assistant import browser_executable, browser_profile_dir
from hot_content import HotContentItem


XHS_HOME_URL = "https://www.xiaohongshu.com/"
XHS_SEARCH_URL = "https://www.xiaohongshu.com/search_result?keyword={keyword}"


def normalize_xhs_url(value: str) -> str:
    text = value.strip()
    match = re.search(r"https?://[^\s]+", text)
    if not match:
        raise ValueError("没有找到有效的小红书链接。")
    return match.group(0).rstrip("，。；;、)")


def _persistent_context(playwright, browser_name: str):
    executable = browser_executable(browser_name)
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(browser_profile_dir("xiaohongshu")),
        executable_path=str(executable),
        headless=False,
        accept_downloads=True,
        viewport={"width": 1440, "height": 900},
        args=[
            "--disable-features=msEdgeSidebarV2",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    )


def _login_required(page: Page) -> bool:
    text = page.locator("body").inner_text(timeout=5000)
    markers = (
        "登录后查看搜索结果",
        "扫码登录",
        "手机号登录",
        "登录后查看",
        "请先登录",
    )
    return any(marker in text for marker in markers) and "发布" not in text[:500]


def _wait_for_login(
    page: Page,
    *,
    timeout_seconds: int = 300,
) -> None:
    if not _login_required(page):
        return
    started = time.monotonic()
    while time.monotonic() - started < timeout_seconds:
        page.wait_for_timeout(2000)
        if not _login_required(page):
            page.wait_for_timeout(1500)
            return
    raise RuntimeError("等待小红书扫码登录超时，请重试采集。")


def _extract_detail(page: Page) -> HotContentItem:
    title = ""
    for selector in (
        'meta[property="og:title"]',
        'meta[name="twitter:title"]',
        "h1",
    ):
        locator = page.locator(selector).first
        if locator.count():
            if selector.startswith("meta"):
                title = str(locator.get_attribute("content") or "").strip()
            else:
                title = locator.inner_text(timeout=2000).strip()
            if title:
                break

    author = ""
    for selector in (
        'meta[name="author"]',
        '[class*="author"] [class*="name"]',
        '[class*="user-name"]',
    ):
        locator = page.locator(selector).first
        if not locator.count():
            continue
        author = (
            str(locator.get_attribute("content") or "").strip()
            if selector.startswith("meta")
            else locator.inner_text(timeout=2000).strip()
        )
        if author:
            break

    body = ""
    body_candidates = (
        "#detail-desc",
        '[class*="note-text"]',
        '[class*="desc"]',
        "article",
    )
    for selector in body_candidates:
        locator = page.locator(selector).first
        if not locator.count():
            continue
        value = locator.inner_text(timeout=3000).strip()
        if len(value) > len(body):
            body = value
    if not body:
        body = page.locator("body").inner_text(timeout=5000)[:5000]

    tags = list(
        dict.fromkeys(
            re.findall(r"#([^#\s，。；;]{1,30})", f"{title}\n{body}")
        )
    )[:10]
    metrics_match = re.search(
        r"(点赞|赞)[^\d]{0,4}(\d+(?:\.\d+)?[万千]?)",
        page.locator("body").inner_text(timeout=5000),
    )
    metrics = (
        f"点赞 {metrics_match.group(2)}"
        if metrics_match
        else ""
    )
    return HotContentItem(
        source_mode="链接采集",
        source_url=page.url,
        title=title or "未读取标题",
        body=body.strip(),
        author=author,
        content_type="视频" if page.locator("video").count() else "图文",
        tags=tags,
        metrics=metrics,
    )


def collect_xhs_link(
    link: str,
    *,
    browser_name: str = "edge",
) -> HotContentItem:
    target_url = normalize_xhs_url(link)
    with sync_playwright() as playwright:
        context = _persistent_context(playwright, browser_name)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4500)
            _wait_for_login(page)
            return _extract_detail(page)
        finally:
            context.close()


def _extract_visible_cards(page: Page, *, limit: int = 10) -> list[HotContentItem]:
    anchors = page.locator(
        'a[href*="/explore/"], a[href*="/discovery/item/"]'
    )
    result: list[HotContentItem] = []
    seen: set[str] = set()
    for index in range(anchors.count()):
        if len(result) >= limit:
            break
        anchor = anchors.nth(index)
        try:
            href = str(anchor.get_attribute("href") or "").strip()
            if not href:
                continue
            url = urljoin(XHS_HOME_URL, href)
            if url in seen:
                continue
            seen.add(url)
            card_text = anchor.evaluate(
                """(element) => {
                    const card = element.closest('section')
                        || element.parentElement?.parentElement
                        || element;
                    return card.innerText || element.innerText || '';
                }"""
            )
            lines = [line.strip() for line in card_text.splitlines() if line.strip()]
            if not lines:
                continue
            title = lines[0][:120]
            author = lines[1][:60] if len(lines) > 1 else ""
            metrics = next(
                (
                    line
                    for line in lines
                    if re.search(r"\d", line)
                    and any(unit in line for unit in ("赞", "收藏", "评论"))
                ),
                "",
            )
            result.append(
                HotContentItem(
                    source_mode="关键词采集",
                    source_url=url,
                    title=title,
                    author=author,
                    metrics=metrics,
                    content_type=(
                        "视频"
                        if anchor.locator("xpath=.//video").count()
                        else "图文"
                    ),
                )
            )
        except Exception:
            continue
    return result


def search_xhs_visible(
    keyword: str,
    *,
    limit: int = 10,
    browser_name: str = "edge",
) -> list[HotContentItem]:
    keyword = keyword.strip()
    if not keyword:
        raise ValueError("请输入搜索关键词。")
    limit = max(1, min(int(limit), 30))
    url = XHS_SEARCH_URL.format(keyword=quote(keyword))
    with sync_playwright() as playwright:
        context = _persistent_context(playwright, browser_name)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6000)
            _wait_for_login(page)
            if "/search_result" not in page.url:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)
            return _extract_visible_cards(page, limit=limit)
        finally:
            context.close()
