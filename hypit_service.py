from __future__ import annotations

import os
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


APP_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "ZhangXiaoxingGenerator"
HYPIT_HOME = APP_DATA / "hypit"
HYPIT_NODE_MODULES = HYPIT_HOME / "node_modules"
HYPIT_PROJECTS = APP_DATA / "hypit_projects"
SHIM_DIR = APP_DATA / "bin"


@dataclass(slots=True)
class HypitEnvironment:
    node: str
    npm: str
    pnpm: str
    ffmpeg: str
    ffprobe: str
    uv: str
    hypit: str

    @property
    def ready(self) -> bool:
        return all((self.node, self.npm, self.pnpm, self.ffmpeg, self.ffprobe, self.hypit))


def _first_existing(candidates: list[Path]) -> str:
    for path in candidates:
        if path.exists():
            return str(path)
    return ""


def find_node() -> str:
    found = shutil.which("node")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return _first_existing(
        [
            Path("C:/Program Files/nodejs/node.exe"),
            local / "Programs" / "nodejs" / "node.exe",
            Path.home() / ".trae-cn" / "sdks" / "versions" / "node" / "current" / "node.exe",
        ]
    )


def find_npm() -> str:
    found = shutil.which("npm.cmd") or shutil.which("npm")
    if found:
        return found
    node = Path(find_node()) if find_node() else None
    if node:
        candidate = node.with_name("npm.cmd" if os.name == "nt" else "npm")
        if candidate.exists():
            return str(candidate)
    return ""


def find_pnpm() -> str:
    found = shutil.which("pnpm.cmd") or shutil.which("pnpm")
    if found:
        return found
    node = Path(find_node()) if find_node() else None
    if node:
        candidate = node.with_name("pnpm.cmd" if os.name == "nt" else "pnpm")
        if candidate.exists():
            return str(candidate)
    return ""


def find_uv() -> str:
    found = shutil.which("uv.exe") or shutil.which("uv")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return _first_existing(
        [
            Path.home() / ".local" / "bin" / "uv.exe",
            local / "Programs" / "uv" / "uv.exe",
            Path("D:/AiPyPro/resources/app.asar.unpacked/resources/bin/uv.exe"),
        ]
    )


def find_edge() -> str:
    return _first_existing(
        [
            Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
            Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
        ]
    )


def find_static_ffmpeg_tools() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe
    try:
        from static_ffmpeg import run

        ffmpeg_path, ffprobe_path = run.get_or_fetch_platform_executables_else_raise()
        return str(ffmpeg_path), str(ffprobe_path)
    except Exception:
        pass
    return ffmpeg or "", ffprobe or ""


def hypit_executable() -> str:
    suffix = ".cmd" if os.name == "nt" else ""
    candidate = HYPIT_NODE_MODULES / ".bin" / f"hypit{suffix}"
    if candidate.exists():
        return str(candidate)
    return shutil.which("hypit") or ""


def environment() -> HypitEnvironment:
    ffmpeg, ffprobe = find_static_ffmpeg_tools()
    return HypitEnvironment(
        node=find_node(),
        npm=find_npm(),
        pnpm=find_pnpm(),
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        uv=find_uv(),
        hypit=hypit_executable(),
    )


def command_environment() -> dict[str, str]:
    env = os.environ.copy()
    info = environment()
    path_parts: list[str] = []
    for executable in (info.node, info.pnpm, info.ffmpeg, info.ffprobe, info.uv):
        if executable:
            path_parts.append(str(Path(executable).parent))
    path_parts.append(str(HYPIT_NODE_MODULES / ".bin"))
    path_parts.extend(env.get("PATH", "").split(os.pathsep))
    env["PATH"] = os.pathsep.join(dict.fromkeys(part for part in path_parts if part))
    return env


def ensure_project_dir(name: str) -> Path:
    safe = "".join(character if character.isalnum() or character in "-_" else "_" for character in name)
    project = HYPIT_PROJECTS / (safe or "hypit_project")
    project.mkdir(parents=True, exist_ok=True)
    return project


def run_hypit(
    arguments: list[str],
    *,
    cwd: str | Path | None = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    info = environment()
    if not info.hypit:
        raise RuntimeError("Hypit CLI 未安装。")
    command = [info.hypit, *arguments]
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=command_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
    )


def start_hypit_process(
    arguments: list[str],
    *,
    cwd: str | Path | None = None,
) -> subprocess.Popen[str]:
    info = environment()
    if not info.hypit:
        raise RuntimeError("Hypit CLI 未安装。")
    return subprocess.Popen(
        [info.hypit, *arguments],
        cwd=str(cwd) if cwd else None,
        env=command_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        shell=False,
    )


def launch_hypit_studio(
    arguments: list[str],
    *,
    cwd: str | Path | None = None,
) -> subprocess.Popen:
    info = environment()
    if not info.hypit:
        raise RuntimeError("Hypit CLI 未安装。")
    return subprocess.Popen(
        [info.hypit, "studio", *arguments],
        cwd=str(cwd) if cwd else None,
        env=command_environment(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
    )


def hypit_version() -> str:
    result = run_hypit(["--version"], timeout=30)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Hypit 版本检查失败。")
    return result.stdout.strip()


def environment_report() -> str:
    info = environment()
    lines = [
        f"Node: {info.node or '未安装'}",
        f"npm: {info.npm or '未安装'}",
        f"pnpm: {info.pnpm or '未安装'}",
        f"ffmpeg: {info.ffmpeg or '未安装'}",
        f"ffprobe: {info.ffprobe or '未安装'}",
        f"uv: {info.uv or '未安装'}",
        f"Hypit CLI: {info.hypit or '未安装'}",
    ]
    return "\n".join(lines)


def install_hypit_cli(version: str = "0.2.12") -> str:
    npm = find_npm()
    if not npm:
        raise RuntimeError("未找到 npm，无法安装 Hypit CLI。")
    HYPIT_HOME.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [npm, "install", "--prefix", str(HYPIT_HOME), f"@hypit/hypit@{version}", "--no-audit", "--no-fund"],
        env=command_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Hypit CLI 安装失败。")
    return hypit_executable()


def install_ffmpeg_tools() -> tuple[str, str]:
    ffmpeg, ffprobe = find_static_ffmpeg_tools()
    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "static-ffmpeg==3.0"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "FFmpeg 安装失败。")
    return find_static_ffmpeg_tools()


def ensure_pnpm() -> str:
    found = find_pnpm()
    if found:
        return found
    node = find_node()
    if not node:
        raise RuntimeError("未找到 Node.js，无法准备 pnpm。")
    corepack = Path(node).with_name("corepack.cmd" if os.name == "nt" else "corepack")
    if not corepack.exists():
        raise RuntimeError("未找到 corepack，无法准备 pnpm。")
    subprocess.run([str(corepack), "enable"], check=True, timeout=120)
    subprocess.run([str(corepack), "prepare", "pnpm@10.33.0", "--activate"], check=True, timeout=300)
    return find_pnpm()


def initialize_project(project_dir: str | Path) -> subprocess.CompletedProcess[str]:
    return run_hypit(["runtime", "init"], cwd=project_dir, timeout=120)


def configure_local_profile(project_dir: str | Path) -> Path:
    project = Path(project_dir)
    profile = project / "hypit.runtime.json"
    if not profile.exists():
        raise RuntimeError("未找到 hypit.runtime.json，请先初始化 Runtime。")
    info = environment()
    edge = find_edge()
    if not edge:
        raise RuntimeError("未找到 Microsoft Edge，无法配置本地渲染浏览器。")
    if not info.ffmpeg or not info.ffprobe:
        raise RuntimeError("未找到 FFmpeg 或 FFprobe。")

    document = json.loads(profile.read_text(encoding="utf-8"))
    endpoints = document.setdefault("endpoints", {})
    endpoints.setdefault(
        "image-opencv.local",
        {"use": "@hypit/provider-image-opencv-local"},
    )
    hyperframes = endpoints.setdefault(
        "hyperframes.local",
        {"use": "@hypit/provider-hyperframes-local"},
    )
    config = hyperframes.setdefault("config", {})
    config.update(
        {
            "nodePath": info.node,
            "chromePath": edge,
            "ffmpegPath": info.ffmpeg,
            "ffprobePath": info.ffprobe,
            "browserGpu": "software",
        }
    )
    profile.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    bindings = document.setdefault("bindings", {})
    bindings.setdefault(
        "@hypit/raster@1#execute-raster",
        "image-opencv.local",
    )
    profile.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return profile


def install_apib_provider(project_dir: str | Path) -> Path:
    project = Path(project_dir)
    source = Path(__file__).resolve().parent / "hypit_provider_apib"
    if not source.exists():
        raise RuntimeError("APIB Provider 模板不存在。")
    destination = project / "packages" / "provider-apib"
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)

    profile = project / "hypit.runtime.json"
    if not profile.exists():
        raise RuntimeError("未找到 hypit.runtime.json，请先初始化 Runtime。")
    document = json.loads(profile.read_text(encoding="utf-8"))
    endpoints = document.setdefault("endpoints", {})
    endpoints["apib.default"] = {
        "use": "@zhangxiaoxing/provider-apib",
        "pool": "apib.default",
        "config": {
            "baseUrl": "https://api.apib.ai/v1",
            "apiKey": {"store": "platform", "key": "apib.api-key"},
            "concurrency": 2,
            "pollIntervalMs": 5000,
        },
    }
    bindings = document.setdefault("bindings", {})
    for capability in (
        "@hypit/gpt-image@1#gpt-image-2",
        "@hypit/seedream@1#seedream-5-lite",
        "@hypit/nano-banana@1#nano-banana-2",
        "@hypit/nano-banana@1#nano-banana-pro",
        "@hypit/seedance@1#seedance-2",
        "@hypit/seedance@1#seedance-2-fast",
        "@hypit/seedance@1#seedance-2-mini",
        "@hypit/seedance@1#seedance-2.5",
    ):
        bindings[capability] = "apib.default"
    profile.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def login_apib_credential(
    project_dir: str | Path,
    api_key: str,
) -> subprocess.CompletedProcess[str]:
    if not api_key.strip():
        raise RuntimeError("APIB API Key 为空。")
    secret_path = APP_DATA / "apib-key.tmp"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        secret_path.write_text(api_key.strip(), encoding="utf-8")
        return run_hypit(
            [
                "auth",
                "login",
                "apib.default",
                "--from",
                str(secret_path),
            ],
            cwd=project_dir,
            timeout=120,
        )
    finally:
        secret_path.unlink(missing_ok=True)


def doctor(project_dir: str | Path) -> subprocess.CompletedProcess[str]:
    return run_hypit(["doctor"], cwd=project_dir, timeout=600)
