from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from openpyxl import Workbook

from api_client import APIConfig, APIMartClient
from image_reference import to_data_uri
from prompts import build_image_prompt, normalize_style_name


CONTENT_STYLE_PROFILES: dict[str, dict[str, str]] = {
    "小红书风格": {
        "description": "真实亲身经历种草风，痛点故事开篇，实测清单卖点，情绪自然，适合小红书图文和口播。",
        "example": (
            "上个月去海边度假，偏偏撞上特殊时期，闷热黏腻很难受。"
            "实测后发现，选对清洁用品能明显提升舒适感，重点突出真实体验和卖点清单。"
        ),
    },
    "干货科普风": {
        "description": "少情绪化感叹，讲底层逻辑和避坑知识，像闺蜜科普，建立专业感，不夸大功效。",
        "example": "很多女生经期容易闷痒，其实清洁方式选错也会影响私处菌群，先看成分和干爽速度。",
    },
    "简短吐槽爽文风": {
        "description": "开头强情绪，大量短句，吐槽痛点为主，卖点一句话带过，阅读压力低，适合短视频口播和图文。",
        "example": "谁懂经期闷黏的痛苦。以前踩雷很多湿厕纸，擦完湿哒哒，越擦越闷。",
    },
    "极简高级质感风": {
        "description": "弱化叫卖，侧重精致生活感受，文字克制温柔，少感叹号，适合氛围感图片和松弛感表达。",
        "example": "海边度假遇上特殊时期，日常私护清洁，我更偏爱简单温和的选择。",
    },
    "避坑对比测评风": {
        "description": "先讲踩雷点，再横向对比产品，突出关键差异，适合测评号，表达客观真实。",
        "example": "自费实测多款湿厕纸。踩雷款水分大、容易破；更好的选择更厚实、更快干、配方更温和。",
    },
    "闺蜜碎碎念日记风": {
        "description": "第一人称碎碎念，生活化，小情绪自然穿插，卖点融入日常叙述，广告感最低。",
        "example": "上次去海边度假又赶上经期，一直很在意清洁，不敢乱用东西，最近用的这款让我安心不少。",
    },
}
CONTENT_STYLES = list(CONTENT_STYLE_PROFILES)


def _new_item_id() -> str:
    return f"hot_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"


@dataclass(slots=True)
class HotContentItem:
    source_mode: str
    source_url: str = ""
    title: str = ""
    body: str = ""
    author: str = ""
    content_type: str = "图文"
    tags: list[str] = field(default_factory=list)
    metrics: str = ""
    cover_url: str = ""
    image_urls: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    status: str = "已采集"
    item_id: str = field(default_factory=_new_item_id)

    def to_dict(self) -> dict:
        return asdict(self)

    def analysis_text(self) -> str:
        parts = [
            f"标题：{self.title or '未读取'}",
            f"作者：{self.author or '未读取'}",
            f"类型：{self.content_type}",
            f"来源：{self.source_mode}",
            f"链接：{self.source_url or '无'}",
        ]
        if self.metrics:
            parts.append(f"可见数据：{self.metrics}")
        if self.body:
            parts.append(f"\n正文：\n{self.body}")
        if self.tags:
            parts.append("\n标签：" + " ".join(f"#{tag}" for tag in self.tags))
        return "\n".join(parts)


@dataclass(slots=True)
class OriginalContent:
    title: str
    body: str
    tags: list[str]
    cover_title: str
    pages: list[dict]
    video_prompt: str = ""
    image_copy_items: list[dict] = field(default_factory=list)

    def page_lines(self) -> list[str]:
        lines: list[str] = []
        for index, page in enumerate(self.pages, start=1):
            heading = str(page.get("heading", "")).strip()
            copy = str(page.get("copy", "")).strip()
            lines.append(
                f"{index}、{heading}：{copy}" if heading else f"{index}、{copy}"
            )
        return [line for line in lines if line.strip()]

    def image_prompts(self, *, theme: str, style: str) -> list[str]:
        prompts: list[str] = []
        for page in self.pages:
            page_prompt = str(page.get("image_prompt", "")).strip()
            page_copy = str(page.get("copy", "")).strip()
            if "生成一张" in page_prompt and "视觉风格" in page_prompt:
                prompts.append(page_prompt)
            else:
                prompts.append(
                    build_image_prompt(
                        theme,
                        page_prompt or page_copy,
                        style,
                    )
                )
        return prompts


def _extract_json(text: str) -> dict:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型没有返回有效 JSON。") from None
        payload = json.loads(value[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("模型返回内容不是对象。")
    return payload


def _normalize_tags(value: object) -> list[str]:
    if isinstance(value, list):
        values = [str(item) for item in value]
    else:
        values = re.split(r"[\s,，、;；#]+", str(value or ""))
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        tag = item.strip().lstrip("#")
        if not tag or tag in seen:
            continue
        seen.add(tag)
        result.append(tag)
    return result[:10]


RECOMMENDED_TAG_COUNT = 5
IMAGE_ANALYSIS_MAX_IMAGES = 9


def build_image_analysis_prompt(index: int, total: int) -> str:
    return f"""请客观描述这张图片（第 {index} 张，共 {total} 张）。

1. 画面主体：有没有人物、产品或场景，人物在做什么。
2. 构图与配色：主体位置、镜头视角、光线、主色调和背景元素。
3. 图中文字：把画面里出现的中文标题、正文和标签逐条写出来。

只输出简体中文纯文本，不超过 5 行，格式固定：
画面主体：……
构图与配色：……
图中文字：……""".strip()


IMAGE_REPLICATION_NOTE = (
    "二次原创方案：按上面的识别结果重绘这张图，保持原图的画面主体、"
    "构图布局和整体风格，只做轻微细节变化，不要过度改款。"
)


def _is_image_refusal(text: str) -> bool:
    if "画面主体" in text:
        return False
    return any(
        marker in text
        for marker in (
            "无法识别",
            "无法描述",
            "无法分析",
            "无法帮助",
            "无法处理",
            "无法看到",
            "无法查看",
            "抱歉",
        )
    )


def mock_image_analysis(
    index: int,
    total: int,
    *,
    theme: str = "",
) -> str:
    subject = theme or "小红书图文"
    return (
        f"画面主体：第 {index}/{total} 张图围绕“{subject}”，主体居中，"
        "配有中文标题和说明文字。\n"
        "构图与配色：竖版 3:4，浅色背景，配色清爽，文字集中在画面上方。\n"
        "图中文字：标题大字和两行说明文字，均为简体中文。"
    )


def analyze_reference_images(
    images: list[str],
    *,
    model: str,
    settings: dict,
    theme: str = "",
    client: APIMartClient | None = None,
) -> list[str]:
    paths = [str(path).strip() for path in images if str(path).strip()]
    paths = paths[:IMAGE_ANALYSIS_MAX_IMAGES]
    if not paths:
        return []
    total = len(paths)
    if settings.get("mock_mode"):
        return [
            f"{mock_image_analysis(index, total, theme=theme)}\n{IMAGE_REPLICATION_NOTE}"
            for index in range(1, total + 1)
        ]
    actual_client = client or APIMartClient(
        APIConfig(
            base_url=str(settings.get("base_url", "https://api.apib.ai/v1")),
            api_key=str(settings.get("api_key", "")),
        )
    )
    results: list[str] = []
    for index, path in enumerate(paths, start=1):
        try:
            image_uri = to_data_uri(path)
        except OSError as exc:
            results.append(f"第 {index} 张原图读取失败：{exc}")
            continue
        description = actual_client.multimodal_response(
            model=model or "gpt-4o-mini",
            prompt=build_image_analysis_prompt(index, total),
            image_data_uris=[image_uri],
            max_tokens=1500,
            temperature=0.3,
        )
        if _is_image_refusal(description):
            results.append(
                f"第 {index} 张原图未能识别：模型没有返回画面描述。"
                "请换用支持读取图片的模型后重新生成。"
            )
            continue
        results.append(f"{description}\n{IMAGE_REPLICATION_NOTE}")
    return results


def extract_image_texts(image_analysis: list[str]) -> list[str]:
    """从逐张识别结果里取出“图中文字”部分。"""
    texts: list[str] = []
    for value in image_analysis:
        match = re.search(r"图中文字[：:]\s*(.+)", str(value), re.S)
        if not match:
            texts.append("")
            continue
        block = match.group(1).split("二次原创方案")[0]
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        texts.append("\n".join(lines))
    return texts


def build_image_copy_prompt(
    item: HotContentItem,
    *,
    image_analysis: list[str],
    style: str,
    product_info: str,
    audience: str,
) -> str:
    image_lines = [
        f"第 {index} 张原图：{str(value).strip()}"
        for index, value in enumerate(image_analysis, start=1)
    ]
    return f"""
下面是从小红书爆款图文里逐张识别出来的画面内容和图中文字。请逐张改写图片文案，并给出这一张图的生图提示词。

必须遵守：
1. 逐张处理，输出数量必须和识别结果数量一致。
2. 图片文案要改写图中文字，保留原意和卖点，换一种说法重新表达，不得照抄原句。
3. 图片文案要口语化、去 AI 味：像真人第一人称说话，短句为主，不用 emoji，不堆砌排比。
4. 不得编造原图文字里没有的功效承诺，不写治愈、根治、保证有效。
5. 内容风格：{style}。
6. 生图提示词必须以改写后的图片文案为主体，并保持原图的画面主体、构图布局和整体风格接近，只做轻微细节变化，不要过度改款。
7. 生图提示词要写明画面中出现的简体中文大字和排版位置，不出现英文、拼音和水印。

爆款标题：{item.title}

产品或服务：{product_info or "未填写"}

目标人群：{audience or "未填写"}

逐张识别结果：
{chr(10).join(image_lines)}

只返回 JSON，不要解释：
{{
  "images": [
    {{
      "index": 1,
      "image_copy": "这一张图改写后的图片文案",
      "image_prompt": "这一张图的生图提示词"
    }}
  ]
}}
""".strip()


def normalize_image_copy_payload(payload: dict) -> list[dict]:
    raw_items = payload.get("images")
    if not isinstance(raw_items, list):
        return []
    results: list[dict] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        image_copy = str(
            entry.get("image_copy") or entry.get("copy") or ""
        ).strip()
        image_prompt = str(entry.get("image_prompt") or "").strip()
        if not image_copy and not image_prompt:
            continue
        results.append(
            {
                "index": len(results) + 1,
                "image_copy": image_copy,
                "image_prompt": image_prompt,
            }
        )
    return results


def mock_image_copy_plan(
    item: HotContentItem,
    *,
    image_analysis: list[str],
    style: str,
) -> list[dict]:
    texts = extract_image_texts(image_analysis)
    plan: list[dict] = []
    for index, value in enumerate(image_analysis, start=1):
        source = (texts[index - 1] if index - 1 < len(texts) else "").replace(
            "\n", " "
        ).strip()
        source = source or item.title or "图片文案"
        plan.append(
            {
                "index": index,
                "image_copy": f"{source}（{style}二次原创版）",
                "image_prompt": (
                    f"生成一张3:4竖版中文图文，第{index}张，"
                    f"画面围绕“{source}”，保持原图的画面主体和构图布局，"
                    "中文大字清晰可读，整体风格与原图接近。"
                ),
            }
        )
    return plan


def generate_image_copy_plan(
    item: HotContentItem,
    *,
    image_analysis: list[str],
    style: str,
    product_info: str,
    audience: str,
    text_model: str,
    settings: dict,
    client: APIMartClient | None = None,
) -> list[dict]:
    if not image_analysis:
        return []
    if settings.get("mock_mode"):
        return mock_image_copy_plan(
            item,
            image_analysis=image_analysis,
            style=style,
        )
    actual_client = client or APIMartClient(
        APIConfig(
            base_url=str(settings.get("base_url", "https://api.apib.ai/v1")),
            api_key=str(settings.get("api_key", "")),
        )
    )
    response = actual_client.chat_completion(
        model=text_model or "gpt-4o-mini",
        prompt=build_image_copy_prompt(
            item,
            image_analysis=image_analysis,
            style=style,
            product_info=product_info,
            audience=audience,
        ),
        system_prompt=(
            "你是资深中文新媒体图片文案编辑，擅长把画面文字改写成真人口语表达，"
            "并据此给出图片生成提示词。"
        ),
        max_tokens=4000,
        temperature=0.75,
    )
    return normalize_image_copy_payload(_extract_json(response))


def shuffle_tags(
    tags: list[str],
    *,
    limit: int = RECOMMENDED_TAG_COUNT,
) -> list[str]:
    values = _normalize_tags(tags)
    if not values:
        return []
    return random.sample(values, min(limit, len(values)))


def normalize_original_content(payload: dict) -> OriginalContent:
    pages: list[dict] = []
    raw_pages = payload.get("pages")
    if isinstance(raw_pages, list):
        for page in raw_pages:
            if isinstance(page, dict):
                pages.append(
                    {
                        "heading": str(page.get("heading", "")).strip(),
                        "copy": str(page.get("copy", "")).strip(),
                        "on_image_text": str(
                            page.get("on_image_text", "")
                        ).strip(),
                        "image_prompt": str(page.get("image_prompt", "")).strip(),
                    }
                )
    pages = [page for page in pages if page["copy"] or page["image_prompt"]]
    if not pages:
        body = str(payload.get("body", "")).strip()
        pages = [
            {
                "heading": "",
                "copy": line.strip(),
                "on_image_text": "",
                "image_prompt": line.strip(),
            }
            for line in body.splitlines()
            if line.strip()
        ]
    return OriginalContent(
        title=str(payload.get("title", "")).strip(),
        body=str(payload.get("body", "")).strip(),
        tags=_normalize_tags(payload.get("tags", [])),
        cover_title=str(payload.get("cover_title", "")).strip(),
        pages=pages,
        video_prompt=str(payload.get("video_prompt", "")).strip(),
    )


def build_rewrite_prompt(
    item: HotContentItem,
    *,
    reference_content: str,
    product_info: str,
    audience: str,
    style: str,
    target_word_count: int,
    image_analysis: list[str] | None = None,
) -> str:
    style_profile = CONTENT_STYLE_PROFILES.get(style, {})
    style_description = style_profile.get("description", style)
    style_example = style_profile.get("example", "")
    analysis_values = [
        str(value).strip() for value in (image_analysis or []) if str(value).strip()
    ]
    if analysis_values:
        analysis_lines = [
            f"第 {index} 张原图：{value}"
            for index, value in enumerate(analysis_values, start=1)
        ]
        analysis_lines.append(
            "每页 image_prompt 必须以对应原图的识别结果为基础做二次原创："
            "保持原图的主体、构图布局、配色关系和整体风格尽量接近，"
            "只做轻微细节变化，不要过度改款。"
        )
        image_section = (
            "原图识别与二次原创方案：\n" + "\n".join(analysis_lines) + "\n\n"
        )
    else:
        image_section = ""
    reference_text = reference_content.strip()
    if reference_text:
        source_rule = "参考内容是最高优先级事实来源。"
        reference_block = f"用户参考内容：\n{reference_text}"
    else:
        source_rule = (
            "用户参考内容未填写，请直接以采集到的爆款内容为素材，"
            "换角度、换说法做二次原创仿写。"
        )
        reference_block = (
            "用户参考内容：未填写（本次直接对采集到的爆款内容做二次原创仿写）"
        )
    return f"""
请根据“参考爆款结构”和“用户提供的参考内容”，生成全新的原创图文内容。

必须遵守：
1. {source_rule}
2. 只参考爆款的选题、结构和表达节奏，不得复制原句。
3. 不编造产品功效，不写治愈、根治、保证有效等承诺。
4. 内容风格：{style}。
5. 风格特点：{style_description}
6. 风格参考样例（只模仿语气和结构，不得照抄）：
{style_example or "无"}
7. 正文目标字数：约 {target_word_count} 个中文字符。
8. 输出 4 到 6 个图文页面。
9. 所有图片中的可见文字必须使用简体中文。

口语化与去 AI 味要求（必须严格执行）：
- 用真人第一人称口语写，像对着朋友讲自己的经历，不要写成说明书或百科条目。
- 禁止机械连接词：首先、其次、再次、最后、总而言之、综上所述、值得注意的是。
- 禁止排比堆砌和空洞形容词，句子长短要错落，允许自然断句和口语停顿。
- 不使用 emoji 堆砌，不使用“姐妹们冲”“绝绝子”这类网感模板腔。
- 读起来必须像真人在说话，可以直接当口播稿念出来。

爆款参考：
标题：{item.title}
正文：{item.body}
标签：{" ".join(item.tags)}

{image_section}{reference_block}

产品或服务：
{product_info or "未填写"}

目标人群：
{audience or "未填写"}

只返回 JSON，不要解释：
{{
  "title": "原创标题",
  "cover_title": "封面大字标题，尽量不超过14个汉字",
  "body": "完整原创正文",
  "tags": ["标签1", "标签2"],
  "pages": [
    {{
      "heading": "页面标题",
      "copy": "该页展示的原创文案",
      "on_image_text": "图片中需要出现的中文大字",
      "image_prompt": "该页图片画面和中文文字排版描述"
    }}
  ],
  "video_prompt": "根据新文案生成的图生视频分镜提示词，逐条描述画面如何从图片动起来：运镜方式、元素动效、节奏和时长"
}}
""".strip()


def generate_original_content(
    item: HotContentItem,
    *,
    reference_content: str,
    product_info: str,
    audience: str,
    style: str,
    target_word_count: int,
    text_model: str,
    settings: dict,
    image_analysis: list[str] | None = None,
    client: APIMartClient | None = None,
) -> OriginalContent:
    if settings.get("mock_mode"):
        original = mock_original_content(
            item,
            reference_content=reference_content,
            product_info=product_info,
            audience=audience,
            style=style,
            target_word_count=target_word_count,
        )
        if item.tags:
            original.tags = shuffle_tags(item.tags)
        return original
    actual_client = client or APIMartClient(
        APIConfig(
            base_url=str(settings.get("base_url", "https://api.apib.ai/v1")),
            api_key=str(settings.get("api_key", "")),
        )
    )
    prompt = build_rewrite_prompt(
        item,
        reference_content=reference_content,
        product_info=product_info,
        audience=audience,
        style=style,
        target_word_count=target_word_count,
        image_analysis=image_analysis,
    )
    response = actual_client.chat_completion(
        model=text_model or "gpt-4o-mini",
        prompt=prompt,
        system_prompt=(
            "你是资深中文新媒体内容策划，擅长在遵守合规边界的前提下进行"
            "结构化原创改写。"
        ),
        max_tokens=5000,
        temperature=0.75,
    )
    original = normalize_original_content(_extract_json(response))
    if item.tags:
        original.tags = shuffle_tags(item.tags)
    return original


def mock_original_content(
    item: HotContentItem,
    *,
    reference_content: str,
    product_info: str,
    audience: str,
    style: str,
    target_word_count: int,
) -> OriginalContent:
    facts = [
        line.strip(" -0123456789、.．")
        for line in reference_content.splitlines()
        if line.strip(" -0123456789、.．")
    ]
    if not facts:
        facts = [
            line.strip(" -0123456789、.．")
            for line in (item.body or "").splitlines()
            if line.strip(" -0123456789、.．")
        ]
    if not facts and item.title.strip():
        facts = [item.title.strip()]
    if not facts:
        facts = ["围绕采集到的爆款内容做日常护理分享", "减少反复摩擦", "保持生活规律"]
    title_base = facts[0][:18]
    title = f"{title_base}，这几点值得注意"
    cover_title = title_base[:14]
    page_count = 4
    while len(facts) < page_count:
        facts.append(facts[len(facts) % len(facts)])
    pages: list[dict] = []
    for index, fact in enumerate(facts[:page_count], start=1):
        heading = f"{style}要点{index}"
        copy = f"{fact}。"
        if index == 1 and product_info:
            copy += f" 可结合{product_info}做好日常护理。"
        pages.append(
            {
                "heading": heading,
                "copy": copy,
                "on_image_text": heading,
                "image_prompt": (
                    f"第{index}页，围绕“{fact}”设计中文知识图文，"
                    f"标题为“{heading}”，画面干净、层次清晰。"
                ),
            }
        )
    body = "\n".join(f"{index}、{page['copy']}" for index, page in enumerate(pages, 1))
    body = body[: max(80, target_word_count)]
    tags = _normalize_tags(
        item.tags
        or ["肛周护理", "健康生活", "日常养护", audience or "健康科普"]
    )
    return OriginalContent(
        title=title,
        body=body,
        tags=tags,
        cover_title=cover_title,
        pages=pages,
        video_prompt=(
            f"以{style}形式制作短视频，先提出{title_base}，"
            "再依次说明护理要点，画面使用用户上传素材，字幕保持简体中文。"
        ),
    )


def export_original_content_excel(
    path: str | Path,
    *,
    item: HotContentItem,
    original: OriginalContent,
    image_style: str = "手绘卡通",
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "原创图文"
    sheet.append(
        [
            "主题",
            "每张图文案",
            "风格",
            "生图提示词",
            "视频提示词",
            "标签关键词",
        ]
    )
    style = normalize_style_name(image_style)
    prompts = original.image_prompts(theme=original.title, style=style)
    for index, page in enumerate(original.pages):
        prompt = prompts[index] if index < len(prompts) else ""
        sheet.append(
            [
                original.title,
                page.get("copy", ""),
                style,
                prompt,
                original.video_prompt,
                " ".join(f"#{tag}" for tag in original.tags),
            ]
        )
    widths = {"A": 28, "B": 48, "C": 14, "D": 72, "E": 52, "F": 30}
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    workbook.save(target)
    return target
