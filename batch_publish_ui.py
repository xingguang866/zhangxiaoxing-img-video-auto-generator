from __future__ import annotations

import threading
from datetime import datetime, timedelta

from PySide6 import QtCore, QtWidgets

from publish_drafts import PublishDraft
from publish_queue import (
    PublishQueueEntry,
    evenly_arranged_entries,
    run_publish_queue,
    save_queue_state,
)


class BatchPublishQueueThread(QtCore.QThread):
    progress = QtCore.Signal(int, int, str)
    completed = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(
        self,
        entries: list[PublishQueueEntry],
        *,
        browser_name: str,
        auto_publish: bool,
        prep_lead_minutes: int,
        parent=None,
    ):
        super().__init__(parent)
        self.entries = entries
        self.browser_name = browser_name
        self.auto_publish = auto_publish
        self.prep_lead_minutes = prep_lead_minutes
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        try:
            run_publish_queue(
                self.entries,
                browser_name=self.browser_name,
                auto_publish=self.auto_publish,
                prep_lead_minutes=self.prep_lead_minutes,
                stop_event=self.stop_event,
                progress=lambda index, total, message: self.progress.emit(
                    index,
                    total,
                    message,
                ),
            )
            self.completed.emit("批量发布队列结束。")
        except Exception as exc:
            self.failed.emit(str(exc))


class BatchPublishQueueDialog(QtWidgets.QDialog):
    def __init__(
        self,
        drafts: list[PublishDraft],
        *,
        parent=None,
    ):
        super().__init__(parent)
        self.drafts = drafts
        self.entries: list[PublishQueueEntry] = []
        self.setWindowTitle("批量排队发布到小红书")
        self.resize(980, 680)

        root = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QLabel(
            "软件会按随机延迟逐条打开小红书，上传素材、填写标题正文标签、"
            "设置自定义定时时间并提交发布。"
        )
        header.setWordWrap(True)
        root.addWidget(header)

        warning = QtWidgets.QLabel(
            "注意：批量定时发布会自动点击最终发布，存在平台风控和误发风险。"
            "请先确认标题、正文、素材和每个时间点。"
        )
        warning.setStyleSheet(
            "background:#FFF1F5;color:#B93D68;border-left:4px solid #E64F80;"
            "padding:10px;"
        )
        warning.setWordWrap(True)
        root.addWidget(warning)

        settings = QtWidgets.QGridLayout()
        self.browser_combo = QtWidgets.QComboBox()
        self.browser_combo.addItem("Microsoft Edge（推荐）", "edge")
        self.browser_combo.addItem("Google Chrome", "chrome")
        self.start_edit = QtWidgets.QDateTimeEdit(
            QtCore.QDateTime.currentDateTime().addSecs(3600)
        )
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.min_delay_spin = QtWidgets.QSpinBox()
        self.min_delay_spin.setRange(1, 240)
        self.min_delay_spin.setValue(5)
        self.max_delay_spin = QtWidgets.QSpinBox()
        self.max_delay_spin.setRange(1, 360)
        self.max_delay_spin.setValue(15)
        self.prep_lead_spin = QtWidgets.QSpinBox()
        self.prep_lead_spin.setRange(1, 60)
        self.prep_lead_spin.setValue(5)
        self.auto_publish_check = QtWidgets.QCheckBox(
            "任务填写完成后自动点击最终发布"
        )
        self.auto_publish_check.setChecked(True)
        randomize_button = QtWidgets.QPushButton("重新随机生成时间")
        randomize_button.setObjectName("secondaryButton")
        randomize_button.clicked.connect(self.refresh_schedule)

        settings.addWidget(QtWidgets.QLabel("自动化浏览器"), 0, 0)
        settings.addWidget(QtWidgets.QLabel("首条定时发布时间"), 0, 1)
        settings.addWidget(QtWidgets.QLabel("随机间隔（分钟）"), 0, 2)
        settings.addWidget(QtWidgets.QLabel("提前准备（分钟）"), 0, 3)
        settings.addWidget(self.browser_combo, 1, 0)
        settings.addWidget(self.start_edit, 1, 1)
        delay_row = QtWidgets.QHBoxLayout()
        delay_row.addWidget(self.min_delay_spin)
        delay_row.addWidget(QtWidgets.QLabel("至"))
        delay_row.addWidget(self.max_delay_spin)
        settings.addLayout(delay_row, 1, 2)
        settings.addWidget(self.prep_lead_spin, 1, 3)
        settings.addWidget(self.auto_publish_check, 2, 0, 1, 3)
        settings.addWidget(randomize_button, 2, 3)
        root.addLayout(settings)

        self.table = QtWidgets.QTableWidget(len(drafts), 6)
        self.table.setHorizontalHeaderLabels(
            ["序号", "标题", "类型", "素材", "定时发布时间", "状态"]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1,
            QtWidgets.QHeaderView.ResizeMode.Stretch,
        )
        self.table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.time_edits: list[QtWidgets.QDateTimeEdit] = []
        for row, draft in enumerate(drafts):
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(row + 1)))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(draft.title))
            type_label = "视频" if draft.media_type == "video" else "图文"
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(type_label))
            self.table.setItem(
                row,
                3,
                QtWidgets.QTableWidgetItem(str(len(draft.existing_media))),
            )
            time_edit = QtWidgets.QDateTimeEdit()
            time_edit.setCalendarPopup(True)
            time_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
            time_edit.setMinimumDateTime(
                QtCore.QDateTime.currentDateTime().addSecs(3600)
            )
            time_edit.setMaximumDateTime(
                QtCore.QDateTime.currentDateTime().addDays(30)
            )
            self.table.setCellWidget(row, 4, time_edit)
            self.time_edits.append(time_edit)
            self.table.setItem(row, 5, QtWidgets.QTableWidgetItem("等待中"))
        root.addWidget(self.table, 1)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        cancel_button = QtWidgets.QPushButton("取消")
        cancel_button.setObjectName("secondaryButton")
        start_button = QtWidgets.QPushButton("启动批量发布队列")
        start_button.setObjectName("primaryButton")
        cancel_button.clicked.connect(self.reject)
        start_button.clicked.connect(self.accept_queue)
        buttons.addWidget(cancel_button)
        buttons.addWidget(start_button)
        root.addLayout(buttons)
        self.refresh_schedule()

    def refresh_schedule(self) -> None:
        start_at = self.start_edit.dateTime().toPython()
        try:
            generated = evenly_arranged_entries(
                self.drafts,
                start_at=start_at,
                min_delay_minutes=self.min_delay_spin.value(),
                max_delay_minutes=self.max_delay_spin.value(),
            )
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "时间设置错误", str(exc))
            return
        for index, entry in enumerate(generated):
            self.time_edits[index].setDateTime(
                QtCore.QDateTime(entry.scheduled_at)
            )

    def accept_queue(self) -> None:
        if self.max_delay_spin.value() < self.min_delay_spin.value():
            QtWidgets.QMessageBox.warning(
                self,
                "时间设置错误",
                "最大随机间隔不能小于最小随机间隔。",
            )
            return
        minimum = datetime.now() + timedelta(hours=1)
        entries: list[PublishQueueEntry] = []
        for draft, time_edit in zip(self.drafts, self.time_edits):
            scheduled_at = time_edit.dateTime().toPython()
            if scheduled_at < minimum:
                QtWidgets.QMessageBox.warning(
                    self,
                    "发布时间无效",
                    f"“{draft.title}”的发布时间必须晚于当前时间至少 1 小时。",
                )
                return
            entries.append(
                PublishQueueEntry(
                    draft=draft,
                    scheduled_at=scheduled_at,
                )
            )
        self.entries = entries
        save_queue_state(entries)
        self.accept()

