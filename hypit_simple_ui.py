from __future__ import annotations

import re
import threading
import webbrowser
from html import escape
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from hypit_reference import (
    DEFAULT_WORKSPACE_NAME,
    ReferenceWorkflowError,
    ReferenceProject,
    build_reference_project,
    normalize_reference_url,
    reference_workspace,
    run_reference_workflow,
)
from hypit_rewrite import (
    TTS_MODELS,
    TTS_VOICES,
    RewriteOptions,
    run_originality_workflow,
)
from hypit_service import start_hypit_process


LANGUAGE_OPTIONS = {
    "中文": "zh",
    "英文": "en",
    "日文": "ja",
    "韩文": "ko",
}
PREFERRED_ANALYSIS_MODELS = (
    "gpt-5.2-pro",
    "gpt-5.1",
    "gpt-5",
    "gpt-4o",
    "claude-sonnet-4-6",
    "claude-sonnet-4.5",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
)


def _section(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    label.setObjectName("sectionTitle")
    return label


def _muted(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    label.setObjectName("mutedLabel")
    label.setWordWrap(True)
    return label


class ReferenceWorkflowThread(QtCore.QThread):
    progress = QtCore.Signal(str)
    completed = QtCore.Signal(dict)
    failed = QtCore.Signal(str)

    def __init__(
        self,
        *,
        title: str,
        hook: str,
        source_file: str,
        source_url: str,
        language: str,
        aspect_ratio: str,
        analysis_model: str,
        api_key: str,
        base_url: str,
        mock_mode: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.parameters = {
            "title": title,
            "hook": hook,
            "source_file": source_file,
            "source_url": source_url,
            "language": language,
            "aspect_ratio": aspect_ratio,
            "analysis_model": analysis_model,
            "api_key": api_key,
            "base_url": base_url,
            "mock_mode": mock_mode,
            "workspace_name": DEFAULT_WORKSPACE_NAME,
        }

    def run(self) -> None:
        try:
            project = run_reference_workflow(
                **self.parameters,
                progress=self.progress.emit,
            )
            self.completed.emit(project.as_dict())
        except Exception as exc:
            self.failed.emit(str(exc))


class ReferenceBuildThread(QtCore.QThread):
    progress = QtCore.Signal(str)
    completed = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, project: ReferenceProject, parent=None):
        super().__init__(parent)
        self.project = project

    def run(self) -> None:
        try:
            output = build_reference_project(
                self.project,
                progress=self.progress.emit,
            )
            self.completed.emit(str(output))
        except Exception as exc:
            self.failed.emit(str(exc))


class ReferenceRewriteThread(QtCore.QThread):
    progress = QtCore.Signal(str)
    completed = QtCore.Signal(dict)
    failed = QtCore.Signal(str)

    def __init__(
        self,
        project: ReferenceProject,
        options: RewriteOptions,
        *,
        api_key: str,
        base_url: str,
        mock_mode: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.project = project
        self.options = options
        self.api_key = api_key
        self.base_url = base_url
        self.mock_mode = mock_mode

    def run(self) -> None:
        try:
            project = run_originality_workflow(
                self.project,
                self.options,
                api_key=self.api_key,
                base_url=self.base_url,
                mock_mode=self.mock_mode,
                progress=self.progress.emit,
            )
            self.completed.emit(project.as_dict())
        except Exception as exc:
            self.failed.emit(str(exc))


class HypitSimplePage(QtWidgets.QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.project: ReferenceProject | None = None
        self.source_project: ReferenceProject | None = None
        self.workflow_thread: ReferenceWorkflowThread | None = None
        self.rewrite_thread: ReferenceRewriteThread | None = None
        self.build_thread: ReferenceBuildThread | None = None
        self.studio_process = None
        self.setAcceptDrops(True)

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 12, 0, 0)
        root.setSpacing(14)

        controls = QtWidgets.QFrame()
        controls.setObjectName("sideCard")
        controls.setMinimumWidth(520)
        form = QtWidgets.QVBoxLayout(controls)
        form.setContentsMargins(20, 20, 20, 20)
        form.setSpacing(12)
        form.addWidget(_section("一键参考视频工程"))
        form.addWidget(
            _muted(
                "上传参考视频后，软件自动完成视频下载、口播识别、画面取样、结构分析，"
                "并生成可继续编辑和重复运行的 Hypit 工程。"
            )
        )

        step_one = _section("1. 选择参考视频")
        form.addWidget(step_one)
        self.source_url_edit = QtWidgets.QLineEdit()
        self.source_url_edit.setPlaceholderText("粘贴小红书、抖音、快手、视频号或 B站分享链接")
        form.addWidget(self.source_url_edit)
        source_row = QtWidgets.QHBoxLayout()
        self.source_file_edit = QtWidgets.QLineEdit()
        self.source_file_edit.setPlaceholderText("也可以选择本地 MP4、MOV、MKV 或 WEBM")
        choose_file = QtWidgets.QPushButton("选择视频")
        choose_file.setObjectName("secondaryButton")
        choose_file.clicked.connect(self.choose_source_file)
        source_row.addWidget(self.source_file_edit, 1)
        source_row.addWidget(choose_file)
        form.addLayout(source_row)

        form.addWidget(_section("2. 填写创作目标"))
        self.title_edit = QtWidgets.QLineEdit()
        self.title_edit.setPlaceholderText("视频主题，例如：久坐党如何减少肛周摩擦")
        self.hook_edit = QtWidgets.QLineEdit()
        self.hook_edit.setPlaceholderText("开头钩子，例如：这 3 个习惯正在让肛门反复不舒服")
        form.addWidget(self.title_edit)
        form.addWidget(self.hook_edit)

        options = QtWidgets.QGridLayout()
        self.language_combo = QtWidgets.QComboBox()
        self.language_combo.addItems(list(LANGUAGE_OPTIONS))
        self.aspect_combo = QtWidgets.QComboBox()
        self.aspect_combo.addItems(["9:16", "16:9", "1:1"])
        self.analysis_model_combo = QtWidgets.QComboBox()
        self.analysis_model_combo.setEditable(True)
        options.addWidget(QtWidgets.QLabel("口播语言"), 0, 0)
        options.addWidget(QtWidgets.QLabel("输出比例"), 0, 1)
        options.addWidget(QtWidgets.QLabel("画面分析模型"), 0, 2)
        options.addWidget(self.language_combo, 1, 0)
        options.addWidget(self.aspect_combo, 1, 1)
        options.addWidget(self.analysis_model_combo, 1, 2)
        options.setColumnStretch(2, 1)
        form.addLayout(options)

        mode = self.main_window.settings_store.as_dict()
        if mode["mock_mode"]:
            mode_hint = "当前是演示模式：会生成真实可编辑工程，但画面分析使用基础规则。"
        elif mode["api_key"]:
            mode_hint = "APIB 已配置：将调用多模态模型分析关键画面，并调用 Whisper-1 识别口播。"
        else:
            mode_hint = "未配置 APIB API Key：仍可生成工程，画面分析使用基础规则。"
        form.addWidget(_muted(mode_hint))

        self.start_button = QtWidgets.QPushButton("一键分析并生成工程")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setMinimumHeight(48)
        self.start_button.clicked.connect(self.start_workflow)
        form.addWidget(self.start_button)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        form.addWidget(self.progress)
        self.status_label = _muted("等待选择参考视频。")
        form.addWidget(self.status_label)
        form.addStretch(1)

        result_card = QtWidgets.QFrame()
        result_card.setObjectName("previewCard")
        result_layout = QtWidgets.QVBoxLayout(result_card)
        result_layout.setContentsMargins(20, 20, 20, 20)
        result_layout.setSpacing(10)
        result_layout.addWidget(_section("分析结果与工程"))
        self.summary = QtWidgets.QTextBrowser()
        self.summary.setOpenExternalLinks(False)
        self.summary.setHtml(
            "<p style='color:#93858D'>完成分析后，这里会显示参考视频的结构摘要、"
            "口播情况、节奏、B-roll 和转场建议。</p>"
        )
        result_layout.addWidget(self.summary, 1)

        actions = QtWidgets.QGridLayout()
        self.open_workspace_button = QtWidgets.QPushButton("打开工程目录")
        self.open_studio_button = QtWidgets.QPushButton("打开 Studio")
        self.build_button = QtWidgets.QPushButton("生成成片")
        self.open_output_button = QtWidgets.QPushButton("播放成片")
        for button in (
            self.open_workspace_button,
            self.open_studio_button,
            self.build_button,
            self.open_output_button,
        ):
            button.setObjectName("secondaryButton")
            button.setEnabled(False)
        self.open_workspace_button.clicked.connect(self.open_workspace)
        self.open_studio_button.clicked.connect(self.open_studio)
        self.build_button.clicked.connect(self.build_video)
        self.open_output_button.clicked.connect(self.open_output)
        actions.addWidget(self.open_workspace_button, 0, 0)
        actions.addWidget(self.open_studio_button, 0, 1)
        actions.addWidget(self.build_button, 1, 0)
        actions.addWidget(self.open_output_button, 1, 1)
        result_layout.addLayout(actions)

        result_layout.addWidget(_section("2. 原创口播与配音"))
        rewrite_options = QtWidgets.QGridLayout()
        self.originality_combo = QtWidgets.QComboBox()
        self.originality_combo.addItems(["轻度改写", "中度改写", "深度改写"])
        self.originality_combo.setCurrentText("中度改写")
        self.remove_ai_check = QtWidgets.QCheckBox("去 AI 味")
        self.remove_ai_check.setChecked(True)
        self.remove_promo_check = QtWidgets.QCheckBox("去掉商业促销")
        self.remove_promo_check.setChecked(True)
        rewrite_options.addWidget(QtWidgets.QLabel("改写强度"), 0, 0)
        rewrite_options.addWidget(self.originality_combo, 1, 0)
        rewrite_options.addWidget(self.remove_ai_check, 1, 1)
        rewrite_options.addWidget(self.remove_promo_check, 1, 2)
        result_layout.addLayout(rewrite_options)

        voice_options = QtWidgets.QGridLayout()
        self.tts_model_combo = QtWidgets.QComboBox()
        self.tts_model_combo.setEditable(True)
        self.tts_model_combo.addItems(TTS_MODELS)
        self.voice_combo = QtWidgets.QComboBox()
        self.voice_combo.setEditable(True)
        self.voice_combo.addItems(TTS_VOICES)
        voice_options.addWidget(QtWidgets.QLabel("配音模型"), 0, 0)
        voice_options.addWidget(QtWidgets.QLabel("配音音色"), 0, 1)
        voice_options.addWidget(self.tts_model_combo, 1, 0)
        voice_options.addWidget(self.voice_combo, 1, 1)
        voice_options.setColumnStretch(0, 1)
        voice_options.setColumnStretch(1, 1)
        result_layout.addLayout(voice_options)
        self.set_models(self.main_window.model_catalog)

        self.rewrite_button = QtWidgets.QPushButton("生成原创口播版本")
        self.rewrite_button.setObjectName("primaryButton")
        self.rewrite_button.setMinimumHeight(44)
        self.rewrite_button.setEnabled(False)
        self.rewrite_button.clicked.connect(self.start_rewrite)
        result_layout.addWidget(self.rewrite_button)
        self.rewrite_status = _muted("先生成参考视频工程，然后可以重写口播。")
        result_layout.addWidget(self.rewrite_status)

        result_layout.addWidget(_section("运行记录"))
        self.log_edit = QtWidgets.QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumHeight(150)
        result_layout.addWidget(self.log_edit)

        root.addWidget(controls)
        root.addWidget(result_card, 1)

    def set_models(self, catalog: dict[str, list[dict]]) -> None:
        current = self.analysis_model_combo.currentText().strip()
        candidates = [
            str(item.get("id", "")).strip()
            for item in catalog.get("chat", [])
            if str(item.get("id", "")).strip()
        ]
        preferred = [model for model in PREFERRED_ANALYSIS_MODELS if model in candidates]
        remaining = [model for model in candidates if model not in preferred]
        models = preferred + remaining
        if not models:
            models = list(PREFERRED_ANALYSIS_MODELS[:3])
        self.analysis_model_combo.blockSignals(True)
        self.analysis_model_combo.clear()
        self.analysis_model_combo.addItems(models)
        saved = str(
            self.main_window.settings_store.settings.value(
                "hypit_analysis_model",
                "",
            )
        ).strip()
        selected = current if current in models else saved if saved in models else models[0]
        self.analysis_model_combo.setCurrentText(selected)
        self.analysis_model_combo.blockSignals(False)

        current_tts = self.tts_model_combo.currentText().strip()
        audio_models = [
            str(item.get("id", "")).strip()
            for item in catalog.get("audio", [])
            if str(item.get("id", "")).strip()
        ]
        preferred_tts = [model for model in TTS_MODELS if model in audio_models]
        dynamic_tts = [
            model
            for model in audio_models
            if model not in preferred_tts
            and any(token in model.lower() for token in ("tts", "speech", "voice", "fishaudio"))
        ]
        tts_models = preferred_tts + dynamic_tts + [
            model for model in TTS_MODELS if model not in preferred_tts + dynamic_tts
        ]
        self.tts_model_combo.blockSignals(True)
        self.tts_model_combo.clear()
        self.tts_model_combo.addItems(tts_models)
        self.tts_model_combo.setCurrentText(
            current_tts if current_tts in tts_models else tts_models[0]
        )
        self.tts_model_combo.blockSignals(False)

    def choose_source_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择参考视频",
            self.source_file_edit.text(),
            "视频文件 (*.mp4 *.mov *.mkv *.webm *.m4v);;所有文件 (*)",
        )
        if path:
            self.source_file_edit.setText(path)
            self.source_url_edit.clear()

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            event.acceptProposedAction()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        urls = event.mimeData().urls()
        if not urls:
            return
        path = Path(urls[0].toLocalFile())
        if path.exists():
            self.source_file_edit.setText(str(path))
            self.source_url_edit.clear()
            event.acceptProposedAction()

    def _set_busy(self, busy: bool, message: str) -> None:
        self.start_button.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.status_label.setText(message)

    def start_workflow(self) -> None:
        if self.workflow_thread and self.workflow_thread.isRunning():
            return
        source_file = self.source_file_edit.text().strip()
        source_url = self.source_url_edit.text().strip()
        if not source_file and not source_url:
            QtWidgets.QMessageBox.warning(self, "缺少参考视频", "请填写视频链接或选择本地视频。")
            return
        if source_url and not source_file:
            try:
                source_url = normalize_reference_url(source_url)
            except ReferenceWorkflowError as exc:
                QtWidgets.QMessageBox.warning(self, "视频链接格式不正确", str(exc))
                return
            self.source_url_edit.setText(source_url)
        values = self.main_window.settings_store.as_dict()
        if values["api_key"] and not values["mock_mode"]:
            answer = QtWidgets.QMessageBox.question(
                self,
                "确认 API 费用",
                "本次操作会调用 APIB 口播转写和多模态画面分析，"
                "可能产生少量 API 费用。视频下载和最终渲染在本地完成。\n\n"
                "是否继续？",
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.Yes,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self.main_window.settings_store.save_value(
            "hypit_analysis_model",
            self.analysis_model_combo.currentText().strip(),
        )
        self.log_edit.clear()
        self._append_log("开始准备参考视频工程。")
        self._set_busy(True, "准备中...")
        self.workflow_thread = ReferenceWorkflowThread(
            title=self.title_edit.text().strip(),
            hook=self.hook_edit.text().strip(),
            source_file=source_file,
            source_url=source_url,
            language=LANGUAGE_OPTIONS.get(self.language_combo.currentText(), "zh"),
            aspect_ratio=self.aspect_combo.currentText(),
            analysis_model=self.analysis_model_combo.currentText().strip(),
            api_key=values["api_key"],
            base_url=values["base_url"],
            mock_mode=bool(values["mock_mode"]),
            parent=self,
        )
        self.workflow_thread.progress.connect(self._on_progress)
        self.workflow_thread.completed.connect(self._on_completed)
        self.workflow_thread.failed.connect(self._on_failed)
        self.workflow_thread.finished.connect(
            lambda thread=self.workflow_thread: self._clear_workflow_thread(thread)
        )
        self.workflow_thread.start()

    def _clear_workflow_thread(self, thread) -> None:
        if self.workflow_thread is thread:
            self.workflow_thread = None

    def _on_progress(self, message: str) -> None:
        self.status_label.setText(message)
        self._append_log(message)

    def _on_completed(self, payload: dict) -> None:
        self.source_project = ReferenceProject(
            workspace=Path(payload["workspace"]),
            run_dir=Path(payload["run_dir"]),
            media_path=Path(payload["media_path"]),
            analysis_path=Path(payload["analysis_path"]),
            svml_path=Path(payload["svml_path"]),
            svrun_path=Path(payload["svrun_path"]),
            output_path=Path(payload["output_path"]),
            analysis=payload.get("analysis") or {},
        )
        self.project = self.source_project
        self._set_busy(False, f"工程已生成：{self.project.run_dir.name}")
        self.summary.setHtml(self._analysis_html(self.project.analysis))
        self.open_workspace_button.setEnabled(True)
        self.open_studio_button.setEnabled(True)
        self.build_button.setEnabled(True)
        self.rewrite_button.setEnabled(True)
        self.open_output_button.setEnabled(self.project.output_path.exists())
        self._append_log("可编辑工程已生成并通过 Hypit 校验。")

    def _on_failed(self, message: str) -> None:
        self._set_busy(False, "生成失败。")
        self._append_log(f"错误：{message}")
        QtWidgets.QMessageBox.critical(self, "参考视频工程生成失败", message)

    def _append_log(self, message: str) -> None:
        self.log_edit.appendPlainText(message)

    def _analysis_html(self, analysis: dict) -> str:
        def lines(value) -> str:
            if isinstance(value, list):
                return "".join(f"<li>{escape(str(item))}</li>" for item in value)
            return f"<li>{escape(str(value))}</li>"

        hook = analysis.get("hook") or {}
        speech = analysis.get("speech") or {}
        rhythm = analysis.get("rhythm") or {}
        subtitle = analysis.get("subtitle_style") or {}
        return f"""
        <style>
          body {{ color:#3D343B; font-family:"Microsoft YaHei UI"; line-height:1.7; }}
          h2 {{ color:#E14C7C; font-size:19px; }}
          h3 {{ color:#4A414A; font-size:15px; margin-top:18px; }}
          .box {{ background:#FFF4F8; border-left:4px solid #F45D8D; padding:10px 12px; }}
        </style>
        <h2>参考视频分析</h2>
        <div class="box">{escape(str(analysis.get("summary") or "暂无摘要"))}</div>
        <h3>开头钩子</h3>
        <p>{escape(str(hook.get("proposed_text") or "未填写"))}<br>
        建议时长：{escape(str(hook.get("duration_seconds") or "待确认"))} 秒<br>
        {escape(str(hook.get("analysis") or ""))}</p>
        <h3>口播与字幕</h3>
        <p>语言：{escape(str(speech.get("language") or "待识别"))}<br>
        转写状态：{"已完成" if speech.get("available") else "未完成或未检测到语音"}</p>
        <p>{escape(str(speech.get("text") or speech.get("note") or ""))}</p>
        <p>{escape(str(subtitle.get("recommendation") or ""))}</p>
        <h3>节奏与转场</h3>
        <p>{escape(str(rhythm.get("assessment") or ""))}</p>
        <h3>B-roll 建议</h3>
        <ul>{lines(analysis.get("broll") or [])}</ul>
        <h3>转场建议</h3>
        <ul>{lines(analysis.get("transitions") or [])}</ul>
        <h3>视觉元素</h3>
        <ul>{lines(analysis.get("visual_effects") or [])}</ul>
        """

    def _rewrite_html(self, rewrite: dict) -> str:
        def lines(value) -> str:
            if isinstance(value, list):
                return "".join(f"<li>{escape(str(item))}</li>" for item in value)
            return f"<li>{escape(str(value))}</li>"

        segments = rewrite.get("segments") or []
        rendered_segments = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            rendered_segments.append(
                "<h3>"
                + escape(str(segment.get("intent") or f"段落 {segment.get('id', '')}"))
                + "</h3><p>"
                + escape(str(segment.get("rewritten") or ""))
                + "</p><p style='color:#93858D'>画面建议："
                + escape(str(segment.get("visual_note") or "沿用参考画面"))
                + "</p>"
            )
        return f"""
        <style>
          body {{ color:#3D343B; font-family:"Microsoft YaHei UI"; line-height:1.7; }}
          h2 {{ color:#E14C7C; font-size:19px; }}
          h3 {{ color:#4A414A; font-size:15px; margin-top:18px; }}
          .box {{ background:#FFF4F8; border-left:4px solid #F45D8D; padding:10px 12px; }}
        </style>
        <h2>原创口播版本</h2>
        <div class="box">{escape(str(rewrite.get("hook") or "暂无开头钩子"))}</div>
        <p><b>完整配音</b></p>
        <p>{escape(str(rewrite.get("full_script") or ""))}</p>
        <h3>改写说明</h3>
        <p>{escape(str(rewrite.get("note") or ""))}</p>
        <h3>段落结构</h3>
        {''.join(rendered_segments)}
        """

    def open_workspace(self) -> None:
        if self.project:
            QtGui.QDesktopServices.openUrl(
                QtCore.QUrl.fromLocalFile(str(self.project.run_dir))
            )

    def open_studio(self) -> None:
        if not self.project:
            return
        try:
            relative = self.project.svrun_path.relative_to(self.project.workspace).as_posix()
            self.studio_process = start_hypit_process(
                ["studio", "--run", relative],
                cwd=self.project.workspace,
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Studio 启动失败", str(exc))
            return
        self._append_log("Studio 正在启动，浏览器将自动打开。")
        threading.Thread(
            target=self._wait_for_studio_url,
            args=(self.studio_process,),
            daemon=True,
        ).start()

    def _wait_for_studio_url(self, process) -> None:
        if not process.stdout:
            return
        for raw_line in process.stdout:
            clean = re.sub(r"\x1b\[[0-9;]*m", "", raw_line)
            match = re.search(r"https?://[^\s]+", clean)
            if match:
                webbrowser.open(match.group(0).rstrip(".,;)"))
                return

    def build_video(self) -> None:
        if not self.project or (self.build_thread and self.build_thread.isRunning()):
            return
        self.build_button.setEnabled(False)
        self.progress.setVisible(True)
        self.status_label.setText("正在编译和渲染...")
        self.build_thread = ReferenceBuildThread(self.project, self)
        self.build_thread.progress.connect(self._on_progress)
        self.build_thread.completed.connect(self._on_build_completed)
        self.build_thread.failed.connect(self._on_build_failed)
        self.build_thread.finished.connect(
            lambda thread=self.build_thread: self._clear_build_thread(thread)
        )
        self.build_thread.start()

    def start_rewrite(self) -> None:
        if not self.source_project:
            QtWidgets.QMessageBox.warning(self, "缺少参考工程", "请先生成参考视频工程。")
            return
        if self.rewrite_thread and self.rewrite_thread.isRunning():
            return
        values = self.main_window.settings_store.as_dict()
        if values["api_key"] and not values["mock_mode"]:
            answer = QtWidgets.QMessageBox.question(
                self,
                "确认 API 费用",
                "本次操作会调用文本模型重写口播、TTS 生成配音，并再次调用 Whisper "
                "识别字幕时间点，可能产生 API 费用。\n\n是否继续？",
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.Yes,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        options = RewriteOptions(
            model=self.analysis_model_combo.currentText().strip(),
            originality_level=self.originality_combo.currentText(),
            remove_ai_flavor=self.remove_ai_check.isChecked(),
            remove_promotional=self.remove_promo_check.isChecked(),
            tts_model=self.tts_model_combo.currentText().strip() or "gpt-4o-mini-tts",
            voice=self.voice_combo.currentText().strip() or "alloy",
            language=LANGUAGE_OPTIONS.get(self.language_combo.currentText(), "zh"),
        )
        self.rewrite_button.setEnabled(False)
        self.progress.setVisible(True)
        self.rewrite_status.setText("正在生成原创口播...")
        self.rewrite_thread = ReferenceRewriteThread(
            self.source_project,
            options,
            api_key=values["api_key"],
            base_url=values["base_url"],
            mock_mode=bool(values["mock_mode"]),
            parent=self,
        )
        self.rewrite_thread.progress.connect(self._on_rewrite_progress)
        self.rewrite_thread.completed.connect(self._on_rewrite_completed)
        self.rewrite_thread.failed.connect(self._on_rewrite_failed)
        self.rewrite_thread.finished.connect(
            lambda thread=self.rewrite_thread: self._clear_rewrite_thread(thread)
        )
        self.rewrite_thread.start()

    def _clear_rewrite_thread(self, thread) -> None:
        if self.rewrite_thread is thread:
            self.rewrite_thread = None

    def _on_rewrite_progress(self, message: str) -> None:
        self.rewrite_status.setText(message)
        self._append_log(message)

    def _on_rewrite_completed(self, payload: dict) -> None:
        self.project = ReferenceProject(
            workspace=Path(payload["workspace"]),
            run_dir=Path(payload["run_dir"]),
            media_path=Path(payload["media_path"]),
            analysis_path=Path(payload["analysis_path"]),
            svml_path=Path(payload["svml_path"]),
            svrun_path=Path(payload["svrun_path"]),
            output_path=Path(payload["output_path"]),
            analysis=payload.get("analysis") or {},
        )
        self.progress.setVisible(False)
        self.rewrite_button.setEnabled(True)
        self.rewrite_status.setText(f"原创口播工程已生成：{self.project.run_dir.name}")
        self.summary.setHtml(self._rewrite_html(self.project.analysis))
        self.open_workspace_button.setEnabled(True)
        self.open_studio_button.setEnabled(True)
        self.build_button.setEnabled(True)
        self.open_output_button.setEnabled(False)
        self._append_log("原创口播工程已生成并通过 Hypit 校验。点击“生成成片”导出新版本。")

    def _on_rewrite_failed(self, message: str) -> None:
        self.progress.setVisible(False)
        self.rewrite_button.setEnabled(True)
        self.rewrite_status.setText("原创口播生成失败。")
        self._append_log(f"原创口播错误：{message}")
        QtWidgets.QMessageBox.critical(self, "原创口播生成失败", message)

    def _clear_build_thread(self, thread) -> None:
        if self.build_thread is thread:
            self.build_thread = None

    def _on_build_completed(self, output: str) -> None:
        if self.project:
            self.project.output_path = Path(output)
        self.progress.setVisible(False)
        self.build_button.setEnabled(True)
        self.open_output_button.setEnabled(True)
        self.status_label.setText("成片已生成。")
        self._append_log(f"成片已导出：{output}")

    def _on_build_failed(self, message: str) -> None:
        self.progress.setVisible(False)
        self.build_button.setEnabled(True)
        self.status_label.setText("渲染失败。")
        self._append_log(f"渲染错误：{message}")
        QtWidgets.QMessageBox.critical(self, "成片生成失败", message)

    def open_output(self) -> None:
        if self.project and self.project.output_path.exists():
            QtGui.QDesktopServices.openUrl(
                QtCore.QUrl.fromLocalFile(str(self.project.output_path))
            )
