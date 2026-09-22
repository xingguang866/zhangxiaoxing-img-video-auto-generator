from __future__ import annotations


STYLE_PROMPTS: dict[str, str] = {
    "不限风格": "高质量社交媒体图文，主题清晰，构图专业，视觉层级明确，画面干净，适合健康知识科普。",
    "清新治愈": "清新治愈风格，柔和自然光，低饱和粉蓝黄绿色彩，留白充足，温暖干净，治愈系视觉。",
    "极简静奢": "极简静奢风格，克制的米白与浅灰配色，精致材质，大面积留白，高级杂志排版，安静而有质感。",
    "多巴胺": "多巴胺风格，明亮高饱和撞色，活泼几何形状，年轻社交媒体海报，视觉冲击力强但层次清晰。",
    "韩系温柔": "韩系温柔风格，奶油色调，柔和光线，简约生活感，细腻纸张质感，温柔而克制的排版。",
    "Y2K千禧拍": "Y2K千禧风格，复古数码相机闪光，银色和糖果色点缀，千禧年杂志排版，年轻潮流感。",
    "随手拍": "自然随手拍风格，像真实手机记录的生活画面，光线自然，不刻意摆拍，真实亲切，构图简洁。",
    "胶片复古": "胶片复古风格，细腻颗粒，暖橙与青绿色调，复古杂志排版，柔和阴影，具有时间质感。",
    "手绘卡通": "手绘卡通风格，水彩与彩铅质感，粗圆手写标题，可爱贴纸和胶带拼贴，轻柔人物线条，治愈系。",
    "轻新中式": "轻新中式风格，现代东方审美，米白宣纸质感，墨色与朱红点缀，简约留白，雅致但不老气。",
    "立体3D质感": "立体3D质感，柔和的C4D渲染，圆润黏土材质，清透光照，柔和阴影，精致而有亲和力。",
    "棕巴恩风": "棕巴恩风，复古工装与自然材质，棕绿卡其配色，粗纹理纸感，质朴松弛，具有户外生活气息。",
}


def build_image_prompt(
    theme: str,
    page_copy: str,
    style: str,
    *,
    extra_prompt: str = "",
) -> str:
    theme = theme.strip()
    page_copy = page_copy.strip()
    extra_prompt = extra_prompt.strip()
    style_prompt = STYLE_PROMPTS.get(style, STYLE_PROMPTS["不限风格"])

    parts = [
        "生成一张3:4竖版中文知识科普图。",
        f"主题：{theme or page_copy[:30]}。",
        f"画面内容：{page_copy}。",
        f"视觉风格：{style_prompt}",
        "排版要求：中文标题清晰可读，信息层级明确，重点词换色，配合简洁图标、便签、箭头或编号，不出现乱码。",
        "内容边界：不出现隐私部位、血腥、医疗器械、疗效对比和夸大承诺。",
    ]
    if extra_prompt:
        parts.append(f"补充要求：{extra_prompt}")
    return "".join(parts)


def build_cover_prompt(
    theme: str,
    cover_title: str,
    cover_subtitle: str,
    style: str,
) -> str:
    theme = theme.strip()
    cover_title = cover_title.strip() or theme or "健康生活小知识"
    cover_subtitle = cover_subtitle.strip()
    style_prompt = STYLE_PROMPTS.get(style, STYLE_PROMPTS["不限风格"])

    parts = [
        "生成一张3:4竖版中文封面图，适合抖音、小红书、视频号图文和短视频使用。",
        f"封面主标题：{cover_title}。",
        "主标题必须最大、最醒目、中文文字清晰，不出现错别字、乱码、缺字或重复字。",
    ]
    if cover_subtitle:
        parts.append(f"封面副标题：{cover_subtitle}。副标题字号小于主标题，信息简洁。")
    if theme:
        parts.append(f"内容主题：{theme}。")
    parts.extend(
        [
            f"视觉风格：{style_prompt}",
            "封面布局：中心或上半区突出主标题，搭配一到三个简洁图标、箭头、便签或手绘元素。",
            "信息密度适中，重点突出，留白充足，手机小屏缩略图下仍然清晰可读。",
            "不要生成大段正文，不要堆满元素，不出现二维码、水印和联系方式。",
            "内容边界：不出现隐私部位、血腥、医疗器械、疗效对比和夸大承诺。",
        ]
    )
    return "".join(parts)


def build_video_prompt(
    theme: str,
    image_copy: str,
    user_prompt: str = "",
    *,
    duration: int = 5,
) -> str:
    prompt = user_prompt.strip()
    if prompt:
        return (
            f"{prompt}。保持参考图的原有风格、人物形象和画面构图。"
            f"镜头运动自然，节奏稳定，时长约{duration}秒，适合竖版短视频。"
            "画面中不出现隐私部位、血腥、医疗器械和疗效对比。"
        )

    return (
        f"以参考图为第一帧，围绕“{theme}”制作一段{duration}秒知识科普短视频。"
        f"画面重点表现：{image_copy}。"
        "镜头从轻微推进到稳定定格，加入自然的纸张飘动、图标出现和信息卡片切换。"
        "保持参考图的手绘或设计风格，中文文字清晰，动作舒缓，适合移动端观看。"
        "不出现隐私部位、血腥、医疗器械和疗效对比。"
    )


def normalize_style_name(value: str) -> str:
    aliases = {
        "灯新中式": "轻新中式",
        "熊巴恩风": "棕巴恩风",
        "立3D质感": "立体3D质感",
        "3D质感": "立体3D质感",
    }
    return aliases.get(value.strip(), value.strip() or "不限风格")
