from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests


class APIClientError(RuntimeError):
    pass


@dataclass(slots=True)
class APIConfig:
    base_url: str = "https://api.apib.ai/v1"
    api_key: str = ""
    timeout: int = 120

    @property
    def normalized_base_url(self) -> str:
        return self.base_url.rstrip("/")


class APIMartClient:
    """Client for the APIB / APIMart OpenAI-compatible generation endpoints."""

    def __init__(self, config: APIConfig):
        self.config = config
        self.session = requests.Session()

    def _url(self, path: str) -> str:
        return f"{self.config.normalized_base_url}/{path.lstrip('/')}"

    def _headers(self, json_body: bool = True) -> dict[str, str]:
        if not self.config.api_key.strip():
            raise APIClientError("未配置 API Key，请在“配置”页面填写。")
        headers = {"Authorization": f"Bearer {self.config.api_key.strip()}"}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _error_message(payload: object, fallback: str) -> str:
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or error.get("type") or fallback)
            if isinstance(error, str):
                return error
            message = payload.get("message")
            if message:
                return str(message)
        return fallback

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        data: dict | None = None,
        files: dict | None = None,
        params: dict | None = None,
        timeout: int | None = None,
        max_retries: int = 3,
    ) -> dict:
        last_error = ""
        for attempt in range(max_retries):
            try:
                response = self.session.request(
                    method,
                    self._url(path),
                    headers=self._headers(json_body=json_body is not None),
                    json=json_body,
                    data=data,
                    files=files,
                    params=params,
                    timeout=timeout or self.config.timeout,
                )
            except requests.RequestException as exc:
                last_error = f"网络请求失败：{exc}"
                if attempt + 1 < max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise APIClientError(last_error) from exc

            try:
                payload = response.json()
            except ValueError:
                payload = {"raw": response.text[:1000]}

            if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < max_retries:
                time.sleep(2.0 * (attempt + 1))
                continue

            if not response.ok:
                message = self._error_message(payload, f"HTTP {response.status_code}")
                raise APIClientError(message)

            if not isinstance(payload, dict):
                raise APIClientError("接口返回内容不是有效 JSON。")
            return payload

        raise APIClientError(last_error or "请求失败。")

    def test_connection(self) -> dict:
        return self._request("GET", "/models", params={"limit": 1}, max_retries=1)

    def list_models(self, *, expand: str = "category", category: str | None = None) -> list[dict]:
        params = {"expand": expand}
        if category:
            params["category"] = category
        response = self._request("GET", "/models", params=params, max_retries=2)
        data = response.get("data")
        if not isinstance(data, list):
            raise APIClientError(f"模型列表返回格式异常：{response}")
        return [item for item in data if isinstance(item, dict) and item.get("id")]

    def get_model_pricing(self, model: str) -> dict:
        parsed = urlparse(self.config.normalized_base_url)
        if not parsed.scheme or not parsed.netloc:
            raise APIClientError("Base URL 格式无效，无法查询模型价格。")
        url = f"{parsed.scheme}://{parsed.netloc}/api/pricing/model"
        try:
            response = self.session.get(
                url,
                params={"model": model},
                headers={"Accept": "application/json"},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise APIClientError(f"价格查询失败：{exc}") from exc

        try:
            payload = response.json()
        except ValueError:
            raise APIClientError("价格接口返回内容不是有效 JSON。") from None
        if not response.ok:
            raise APIClientError(self._error_message(payload, f"价格查询失败：HTTP {response.status_code}"))
        data = payload.get("data")
        if not isinstance(data, dict):
            raise APIClientError(f"价格接口未返回有效数据：{payload}")
        return data

    def get_usage(
        self,
        *,
        start: str | int | None = None,
        end: str | int | None = None,
        group_by: str = "model",
        scope: str = "key",
        model: str | None = None,
    ) -> dict:
        params: dict[str, str | int] = {
            "group_by": group_by,
            "scope": scope,
        }
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        if model:
            params["model"] = model
        response = self._request("GET", "/usage", params=params, max_retries=2)
        data = response.get("data")
        if not isinstance(data, dict):
            raise APIClientError(f"用量接口返回格式异常：{response}")
        return data

    def get_balance(self) -> dict:
        return self._request("GET", "/balance", max_retries=2)

    def upload_image(self, image_path: str | Path) -> str:
        path = Path(image_path)
        if not path.exists():
            raise APIClientError(f"图片不存在：{path}")

        try:
            with path.open("rb") as handle:
                response = self.session.post(
                    self._url("/uploads/images"),
                    headers=self._headers(json_body=False),
                    files={"file": (path.name, handle)},
                    timeout=max(self.config.timeout, 180),
                )
        except requests.RequestException as exc:
            raise APIClientError(f"图片上传失败：{exc}") from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text[:1000]}

        if not response.ok:
            raise APIClientError(self._error_message(payload, f"图片上传失败：HTTP {response.status_code}"))

        url = payload.get("url")
        if not url:
            raise APIClientError(f"上传接口未返回图片 URL：{payload}")
        return str(url)

    def transcribe_audio(
        self,
        audio_path: str | Path,
        *,
        language: str = "zh",
        model: str = "whisper-1",
    ) -> dict:
        path = Path(audio_path)
        if not path.exists():
            raise APIClientError(f"音频文件不存在：{path}")
        try:
            with path.open("rb") as handle:
                response = self._request(
                    "POST",
                    "/audio/transcriptions",
                    files={"file": (path.name, handle, "audio/wav")},
                    data={
                        "model": model,
                        "language": language,
                        "response_format": "verbose_json",
                    },
                    timeout=max(self.config.timeout, 600),
                    max_retries=1,
                )
        except OSError as exc:
            raise APIClientError(f"音频读取失败：{exc}") from exc

        payload = response.get("data", response)
        if not isinstance(payload, dict):
            raise APIClientError(f"音频转写返回格式异常：{response}")
        return payload

    def multimodal_response(
        self,
        *,
        model: str,
        prompt: str,
        image_data_uris: list[str],
        max_tokens: int = 4000,
        temperature: float = 0.2,
    ) -> str:
        content: list[dict] = [{"type": "input_text", "text": prompt}]
        content.extend(
            {"type": "input_image", "image_url": image_uri}
            for image_uri in image_data_uris
        )
        response = self._request(
            "POST",
            "/responses",
            json_body={
                "model": model,
                "stream": False,
                "input": [{"role": "user", "content": content}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=max(self.config.timeout, 600),
            max_retries=1,
        )
        payload = response.get("data", response)
        if not isinstance(payload, dict):
            raise APIClientError(f"多模态分析返回格式异常：{response}")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise APIClientError(f"多模态分析未返回结果：{payload}")
        first = choices[0]
        if not isinstance(first, dict):
            raise APIClientError(f"多模态分析结果格式异常：{payload}")
        message = first.get("message")
        if not isinstance(message, dict):
            raise APIClientError(f"多模态分析结果缺少 message：{payload}")
        content_value = message.get("content")
        if isinstance(content_value, str) and content_value.strip():
            return content_value.strip()
        if isinstance(content_value, list):
            texts = [
                str(item.get("text", ""))
                for item in content_value
                if isinstance(item, dict) and item.get("text")
            ]
            joined = "\n".join(texts).strip()
            if joined:
                return joined
        raise APIClientError(f"多模态分析返回空文本：{payload}")

    @staticmethod
    def _normalize_image_resolution(model: str, resolution: str) -> str:
        if model.lower().startswith(("gemini", "nano-banana")):
            return resolution.upper()
        return resolution.lower()

    def submit_image(
        self,
        *,
        prompt: str,
        model: str,
        size: str,
        resolution: str,
        image_urls: list[str] | None = None,
        nsfw_check: bool = True,
    ) -> str:
        payload: dict = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "resolution": self._normalize_image_resolution(model, resolution),
            "nsfw_check": nsfw_check,
        }
        if image_urls:
            payload["image_urls"] = image_urls

        response = self._request("POST", "/images/generations", json_body=payload)
        data = response.get("data")
        if not isinstance(data, list) or not data:
            raise APIClientError(f"图片任务提交失败：{response}")
        task_id = data[0].get("task_id")
        if not task_id:
            raise APIClientError(f"图片任务未返回 task_id：{response}")
        return str(task_id)

    def submit_video(
        self,
        *,
        prompt: str,
        model: str,
        image_urls: list[str] | None,
        size: str,
        resolution: str,
        duration: int,
        generate_audio: bool,
        nsfw_check: bool = True,
    ) -> str:
        duration, resolution = self._normalize_video_parameters(
            model=model,
            duration=duration,
            resolution=resolution,
        )
        payload: dict = {
            "model": model,
            "prompt": prompt,
            "duration": int(duration),
            "resolution": resolution,
            "nsfw_check": nsfw_check,
        }
        if image_urls:
            lowered = model.lower()
            if lowered.startswith("sora") and len(image_urls) > 1:
                raise APIClientError("Sora2 图生视频最多只能使用 1 张参考图片。")
            if lowered.startswith("veo3.1-lite") and image_urls:
                raise APIClientError("veo3.1-lite 不支持参考图片，请更换模型。")
            if lowered.startswith("veo") and len(image_urls) > 3:
                raise APIClientError("VEO3 最多使用 3 张参考图片。")
            if lowered.startswith("seedance-2.0") and len(image_urls) > 9:
                raise APIClientError("seedance-2.0 最多使用 9 张参考图片。")
            if lowered.startswith("seedance-2.5") and len(image_urls) > 30:
                raise APIClientError("seedance-2.5 最多使用 30 张参考图片。")
            payload["image_urls"] = image_urls

        lowered = model.lower()
        if lowered.startswith("seedance"):
            payload["size"] = size
            payload["generate_audio"] = bool(generate_audio)
            if lowered.startswith("seedance-2.5"):
                payload["output_format"] = "mp4"
                payload["watermark"] = False
                if size == "adaptive":
                    payload["omni_reference_task_type"] = "reference"
        elif lowered.startswith("sora"):
            payload["aspect_ratio"] = size
            if not image_urls:
                payload["aspect_ratio"] = "16:9" if size == "adaptive" else size
            payload.pop("generate_audio", None)
        elif lowered.startswith("veo"):
            payload["aspect_ratio"] = size
            payload["duration"] = 8
            if lowered.endswith("-lite"):
                payload.pop("resolution", None)
            payload.pop("generate_audio", None)
        else:
            payload["size"] = size
            payload["generate_audio"] = bool(generate_audio)

        response = self._request("POST", "/videos/generations", json_body=payload)
        data = response.get("data")
        if not isinstance(data, list) or not data:
            raise APIClientError(f"视频任务提交失败：{response}")
        task_id = data[0].get("task_id")
        if not task_id:
            raise APIClientError(f"视频任务未返回 task_id：{response}")
        return str(task_id)

    @staticmethod
    def _normalize_video_parameters(
        *,
        model: str,
        duration: int,
        resolution: str,
    ) -> tuple[int, str]:
        lowered = model.lower()

        if lowered.startswith("sora-2"):
            allowed_durations = {4, 8, 12, 16, 20}
            if duration not in allowed_durations:
                raise APIClientError("Sora2 的视频时长仅支持 4、8、12、16、20 秒。")
            allowed_resolutions = {"720p"} if lowered == "sora-2" else {"720p", "1024p", "1080p"}
            if resolution not in allowed_resolutions:
                if lowered == "sora-2":
                    raise APIClientError("sora-2 仅支持 720p。sora-2-pro 可选 1080p。")
                raise APIClientError("sora-2-pro 仅支持 720p、1024p 或 1080p。")
            return duration, resolution

        if lowered.startswith("veo"):
            if resolution not in {"720p", "1080p", "4k"}:
                raise APIClientError("VEO3 仅支持 720p、1080p 或 4k。")
            return 8, resolution

        if lowered.startswith("seedance-2.5"):
            if resolution not in {"480p", "720p", "1080p"}:
                raise APIClientError("seedance-2.5 仅支持 480p、720p 或 1080p。")
            if duration != -1 and not (4 <= duration <= 30):
                raise APIClientError("seedance-2.5 的视频时长范围为 4 到 30 秒。")
            return duration, resolution

        if lowered.startswith("seedance-2.0-fast"):
            if resolution not in {"480p", "720p"}:
                raise APIClientError("seedance-2.0-fast 仅支持 480p 或 720p。")
            if not (4 <= duration <= 15):
                raise APIClientError("seedance-2.0 的视频时长范围为 4 到 15 秒。")
            return duration, resolution

        if lowered.startswith("seedance-2.0"):
            if resolution not in {"480p", "720p", "1080p", "4k"}:
                raise APIClientError("seedance-2.0 仅支持 480p、720p、1080p 或 4k。")
            if not (4 <= duration <= 15):
                raise APIClientError("seedance-2.0 的视频时长范围为 4 到 15 秒。")
            return duration, resolution

        return duration, resolution

    def get_task(self, task_id: str) -> dict:
        response = self._request(
            "GET",
            f"/tasks/{task_id}",
            params={"language": "zh"},
            max_retries=2,
        )
        data = response.get("data")
        if not isinstance(data, dict):
            raise APIClientError(f"任务状态返回格式异常：{response}")
        return data

    def wait_task(
        self,
        task_id: str,
        *,
        interval: float = 4.0,
        timeout: int = 3600,
        on_update: Callable[[int, str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict:
        started = time.monotonic()
        last_message = ""
        while True:
            if is_cancelled and is_cancelled():
                raise APIClientError("任务已取消。云端任务可能仍在继续执行。")

            task = self.get_task(task_id)
            status = str(task.get("status", "unknown"))
            progress = int(task.get("progress") or 0)
            message = f"{status} · {progress}%"
            if message != last_message and on_update:
                on_update(progress, message)
                last_message = message

            if status == "completed":
                return task
            if status in {"failed", "cancelled"}:
                error = task.get("error")
                if isinstance(error, dict):
                    detail = error.get("message") or error.get("type") or "未知错误"
                else:
                    detail = str(error or "未知错误")
                raise APIClientError(f"任务失败：{detail}")

            if time.monotonic() - started > timeout:
                raise APIClientError(f"任务超时，task_id={task_id}")
            time.sleep(max(1.0, interval))

    @staticmethod
    def extract_result_urls(task: dict) -> list[str]:
        result = task.get("result")
        if not isinstance(result, dict):
            return []

        urls: list[str] = []
        for kind in ("images", "videos"):
            items = result.get(kind)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                value = item.get("url")
                if isinstance(value, str):
                    urls.append(value)
                elif isinstance(value, list):
                    urls.extend(str(url) for url in value if url)
        return urls

    def download_file(self, url: str, destination: str | Path) -> Path:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".part")

        try:
            with self.session.get(url, stream=True, timeout=max(self.config.timeout, 300)) as response:
                response.raise_for_status()
                with temporary.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            handle.write(chunk)
        except (requests.RequestException, OSError) as exc:
            temporary.unlink(missing_ok=True)
            raise APIClientError(f"下载文件失败：{exc}") from exc

        temporary.replace(path)
        return path
