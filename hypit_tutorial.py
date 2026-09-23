from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ASSET_DIR = Path(__file__).resolve().parent / "assets" / "hypit_tutorial"


def _font(size: int, bold: bool = False):
    candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc") if bold else Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, value: str, font, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for character in value:
        candidate = current + character
        if draw.textbbox((0, 0), candidate, font=font)[2] <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = character
    if current:
        lines.append(current)
    return lines


def _card(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    lines: list[str],
    *,
    accent: str = "#F45D8D",
    fill: str = "#FFFFFF",
) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, radius=24, fill=fill, outline="#E9D7DF", width=3)
    draw.rounded_rectangle((x1, y1, x1 + 12, y2), radius=6, fill=accent)
    title_font = _font(30, bold=True)
    body_font = _font(22)
    draw.text((x1 + 32, y1 + 20), title, font=title_font, fill="#332A33")
    y = y1 + 68
    for line in lines:
        for wrapped in _wrap(draw, line, body_font, x2 - x1 - 64):
            draw.text((x1 + 32, y), wrapped, font=body_font, fill="#655D65")
            y += 31
        y += 8


def _arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int]) -> None:
    draw.line((*start, *end), fill="#E291AF", width=5)
    x, y = end
    draw.polygon([(x, y), (x - 16, y - 9), (x - 16, y + 9)], fill="#E291AF")


def _base(title: str, subtitle: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (1200, 640), "#FFF8FB")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((24, 20, 1176, 620), radius=28, outline="#F0DCE6", width=3)
    draw.text((54, 38), title, font=_font(42, bold=True), fill="#2F2730")
    draw.text((56, 96), subtitle, font=_font(22), fill="#8C7C85")
    return image, draw


def _save(image: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, quality=95)
    return path


def generate_tutorial_assets() -> dict[str, Path]:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    assets: dict[str, Path] = {}

    image, draw = _base("1. 创建或选择 Hypit 项目", "项目保存 Source、Runtime Profile、Result 和自定义 Provider")
    _card(draw, (70, 170, 480, 510), "项目名称", ["张小星Hypit项目", "建议使用清晰、稳定的名称"], accent="#F6B6CC")
    _card(draw, (520, 170, 1130, 510), "项目目录", ["创建项目后会自动生成目录", "也可以点击“项目目录”选择已有项目"], accent="#A7D8C5")
    _arrow(draw, (480, 340), (520, 340))
    assets["project"] = _save(image, ASSET_DIR / "01_project.png")

    image, draw = _base("2. 配置 APIB Provider", "复用软件“配置”页面中保存的 APIB API Key")
    _card(draw, (60, 170, 380, 510), "准备 API Key", ["在“配置”页面填写 APIB Key", "Key 仅保存在本机凭据中"], accent="#F6B6CC")
    _card(draw, (430, 170, 780, 510), "写入 Hypit", ["点击“配置 APIB Provider”", "软件写入 Hypit 平台凭据存储"], accent="#9FC7F0")
    _card(draw, (830, 170, 1140, 510), "完成绑定", ["GPT Image 2", "Seedream 5.0 Lite", "Nano Banana 2 / Pro", "Seedance 2.0 / 2.5"], accent="#E8CF86")
    _arrow(draw, (380, 340), (430, 340))
    _arrow(draw, (780, 340), (830, 340))
    assets["provider"] = _save(image, ASSET_DIR / "02_provider.png")

    image, draw = _base("3. 初始化并准备 Runtime", "本地 Node、FFmpeg、Edge 和 OpenCV 组成执行环境")
    _card(draw, (70, 170, 520, 510), "点击顺序", ["1. 初始化 Runtime", "2. 准备 Runtime", "3. Doctor"], accent="#F6B6CC")
    _card(draw, (570, 170, 1130, 510), "通过标志", ["Local Runtime ready", "Worker running", "Programs 2/2 ready"], accent="#A7D8C5")
    assets["runtime"] = _save(image, ASSET_DIR / "03_runtime.png")

    image, draw = _base("4. 选择 .svrun 并执行 Plan", "Plan 只检查请求、Provider、凭据和费用入口，不提交付费任务")
    _card(draw, (60, 170, 540, 510), "选择运行文件", ["点击“选择SVRun”", "选择 .svrun 文件", "确认项目目录正确"], accent="#9FC7F0")
    _card(draw, (590, 170, 1140, 510), "检查计划", ["点击 Plan", "确认 Provider 为 apib.default", "确认模型、时长、分辨率和张数"], accent="#E8CF86")
    _arrow(draw, (540, 340), (590, 340))
    assets["plan"] = _save(image, ASSET_DIR / "04_plan.png")

    image, draw = _base("5. 执行 Build 并等待任务", "Hypit 会提交 APIB 异步任务，自动轮询并收集结果")
    _card(draw, (60, 170, 370, 510), "提交", ["点击 Build", "保留 Build ID"], accent="#F6B6CC")
    _card(draw, (430, 170, 780, 510), "轮询", ["submitted", "processing", "completed"], accent="#9FC7F0")
    _card(draw, (840, 170, 1140, 510), "结果", ["图片或视频下载", "写入 Hypit Result", "保留费用和任务证据"], accent="#A7D8C5")
    _arrow(draw, (370, 340), (430, 340))
    _arrow(draw, (780, 340), (840, 340))
    assets["build"] = _save(image, ASSET_DIR / "05_build.png")

    image, draw = _base("6. 查看状态、导出和 Studio", "Build 完成后可导出文件或进入 Studio 继续编辑")
    _card(draw, (60, 170, 540, 510), "查看与导出", ["输入 Build ID", "点击“查看状态”", "填写 Output 名称和导出路径", "点击“导出结果”"], accent="#9FC7F0")
    _card(draw, (590, 170, 1140, 510), "打开 Studio", ["选择同一个 .svrun", "点击“打开 Studio”", "在浏览器中查看时间线和参数"], accent="#A7D8C5")
    assets["output"] = _save(image, ASSET_DIR / "06_output.png")

    image, draw = _base("推荐完整流程", "首次使用建议严格按照顺序执行")
    steps = [
        "配置 APIB Key",
        "创建项目",
        "初始化 Runtime",
        "配置 APIB Provider",
        "准备 Runtime",
        "Doctor",
        "选择 SVRun",
        "Plan",
        "Build",
        "查看状态",
        "导出结果",
        "打开 Studio",
    ]
    start_x, start_y = 70, 185
    for index, step in enumerate(steps):
        column = index % 3
        row = index // 3
        x = start_x + column * 365
        y = start_y + row * 95
        draw.rounded_rectangle((x, y, x + 320, y + 64), radius=18, fill="#FFFFFF", outline="#E9D7DF", width=3)
        draw.ellipse((x + 18, y + 15, x + 52, y + 49), fill="#F45D8D")
        draw.text((x + 27, y + 20), str(index + 1), font=_font(18, bold=True), fill="#FFFFFF")
        draw.text((x + 70, y + 17), step, font=_font(24, bold=True), fill="#4A414A")
    assets["flow"] = _save(image, ASSET_DIR / "07_flow.png")
    return assets


def build_tutorial_html() -> str:
    assets = generate_tutorial_assets()

    def image(name: str, alt: str) -> str:
        return f'<img src="{assets[name].as_uri()}" alt="{alt}">'

    return f"""
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body {{
          font-family: "Microsoft YaHei UI", "Microsoft YaHei";
          color: #3D343B;
          background: #FFF9FC;
          line-height: 1.7;
          font-size: 14px;
        }}
        h1 {{ color: #E64F80; font-size: 28px; }}
        h2 {{ color: #3D343B; font-size: 20px; margin-top: 28px; }}
        h3 {{ color: #E14C7C; font-size: 17px; }}
        img {{
          width: 100%;
          max-width: 980px;
          border-radius: 14px;
          border: 1px solid #F0DCE6;
          margin: 12px 0 22px 0;
        }}
        .tip {{
          background: #EAF7F1;
          border-left: 5px solid #4C9A74;
          padding: 12px 16px;
          border-radius: 8px;
          margin: 12px 0;
        }}
        .warn {{
          background: #FFF1F5;
          border-left: 5px solid #E64F80;
          padding: 12px 16px;
          border-radius: 8px;
          margin: 12px 0;
        }}
        table {{
          border-collapse: collapse;
          width: 100%;
          margin: 12px 0;
        }}
        th, td {{
          border: 1px solid #ECD8E2;
          padding: 9px 11px;
          vertical-align: top;
        }}
        th {{ background: #FFF0F5; }}
        code {{
          background: #F7EDF2;
          color: #B93D68;
          padding: 2px 5px;
          border-radius: 5px;
        }}
      </style>
    </head>
    <body>
      <h1>Hypit 视频栏目使用教程</h1>
      <p>本教程适用于当前软件版本。Hypit 本地负责工作流执行和渲染，APIB 负责图片、视频模型请求。</p>

      <h2>普通用户：优先使用简易模式</h2>
      <ol>
        <li>进入“Hypit视频”，保持“简易模式”。</li>
        <li>粘贴参考视频链接，或者选择本地视频。</li>
        <li>填写视频主题、开头钩子和语言。</li>
        <li>选择画面分析模型，点击“一键分析并生成工程”。</li>
        <li>工程完成后可打开目录、打开 Studio、生成成片和播放结果。</li>
      </ol>
      <div class="tip">
        简易模式会自动使用 Whisper-1 识别口播、调用多模态模型分析画面并生成本地可编辑工程。
        真实模式执行前会提示可能产生 API 费用。
      </div>

      <h2>高级用户：使用高级模式</h2>
      <p>下面的完整流程用于排查环境、管理 Provider、运行自定义工程和精细控制 Build。</p>

      <div class="warn">
        <b>付费提醒：</b>Plan 不提交付费任务。只有点击 Build 才会调用 APIB 模型并产生费用。
        正式 Build 前务必检查模型、图片张数、视频时长和分辨率。
      </div>

      {image("flow", "完整流程")}

      <h2>1. 创建或选择 Hypit 项目</h2>
      {image("project", "创建项目")}
      <ol>
        <li>进入顶部“Hypit视频”栏目。</li>
        <li>输入项目名称，点击“创建项目”。</li>
        <li>也可以点击“项目目录”选择已有 Hypit 项目。</li>
      </ol>
      <div class="tip">建议不同系列视频使用不同项目，避免 Source、Result 和临时文件互相覆盖。</div>

      <h2>2. 配置现有 APIB API</h2>
      {image("provider", "配置 APIB Provider")}
      <ol>
        <li>先在“配置”页面填写并保存 APIB API Key。</li>
        <li>进入“Hypit视频”，点击“配置 APIB Provider”。</li>
        <li>软件会安装项目 Provider，并把 Key 写入 Hypit 平台凭据存储。</li>
      </ol>
      <p>已绑定模型：GPT Image 2、Seedream 5.0 Lite、Nano Banana 2 / Pro、Seedance 2.0 / Fast / Mini、Seedance 2.5。</p>

      <h2>3. 初始化并准备 Runtime</h2>
      {image("runtime", "初始化 Runtime")}
      <ol>
        <li>点击“初始化 Runtime”。</li>
        <li>软件自动配置 Edge、FFmpeg、FFprobe 和本地 OpenCV。</li>
        <li>点击“准备 Runtime”，启动本地 Worker。</li>
        <li>点击 Doctor，确认退出码为 0。</li>
      </ol>
      <div class="tip">本机渲染使用 Microsoft Edge，不需要下载 Chrome Headless Shell。</div>

      <h2>4. 选择 .svrun 并执行 Plan</h2>
      {image("plan", "执行 Plan")}
      <ol>
        <li>点击“选择SVRun”，选择 Hypit 项目的 <code>.svrun</code> 文件。</li>
        <li>点击 Plan。</li>
        <li>检查 Provider 是否为 <code>apib.default</code>。</li>
        <li>核对模型名称、分辨率、时长、参考素材和请求数量。</li>
      </ol>
      <div class="tip">Plan 不提交付费任务，项目 Source 有问题时应先修复，不要直接 Build。</div>

      <h2>5. 执行 Build</h2>
      {image("build", "执行 Build")}
      <ol>
        <li>确认 Plan 全部通过后点击 Build。</li>
        <li>记录右侧日志中的 Build ID。</li>
        <li>等待 submitted、processing、completed 状态变化。</li>
        <li>图片和视频会自动下载并写入 Hypit Result。</li>
      </ol>
      <div class="warn">关闭日志窗口不会取消远程任务。需要停止时使用“停止当前命令”或 Hypit 的取消能力。</div>

      <h2>6. 查看状态、导出结果和打开 Studio</h2>
      {image("output", "导出结果")}
      <ol>
        <li>在 Build ID 中输入右侧日志里的 Build ID。</li>
        <li>点击“查看状态”读取 Result 情况。</li>
        <li>填写 Output 名称，例如 <code>final.video</code> 或 <code>cover.image</code>。</li>
        <li>填写导出文件路径，点击“导出结果”。</li>
        <li>选择同一个 <code>.svrun</code> 后点击“打开 Studio”继续可视化编辑。</li>
      </ol>

      <h2>常见问题</h2>
      <table>
        <tr><th>问题</th><th>处理方法</th></tr>
        <tr><td>缺少 APIB API Key</td><td>在“配置”页面保存 Key，再点击“配置 APIB Provider”。</td></tr>
        <tr><td>Doctor 报凭据缺失</td><td>确认点击过“配置 APIB Provider”，并检查 Key 是否有效。</td></tr>
        <tr><td>Plan 提示 Unsupported</td><td>检查模型、比例、分辨率、时长或参考素材是否超出 APIB Provider 支持范围。</td></tr>
        <tr><td>Build 返回 HTTP 400</td><td>展开右侧日志查看 APIB 的错误字段，先修正 Source 或参数。</td></tr>
        <tr><td>任务长时间 processing</td><td>查看 Build 状态，不要重复提交同一付费任务。</td></tr>
        <tr><td>无法导出 Output</td><td>核对 Build ID 和 Output 名称，Output 名称必须与 Target 完全一致。</td></tr>
        <tr><td>Studio 无法打开</td><td>确认 Runtime 已准备、.svrun 路径有效并且 Worker 正在运行。</td></tr>
      </table>

      <h2>推荐操作顺序</h2>
      <p>配置 Key → 创建项目 → 初始化 Runtime → 配置 APIB Provider → 准备 Runtime → Doctor → 选择 SVRun → Plan → Build → 查看状态 → 导出结果 → Studio。</p>
    </body>
    </html>
    """
