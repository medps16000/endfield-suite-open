"""Trusted native settings for independent, read-only web overlays."""
from __future__ import annotations

from pathlib import Path
import os
import copy
from . import rotation_client as _rotation_client

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QApplication, QPlainTextEdit, QPushButton, QScrollArea, QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)


class OverlaySettingsPage(QWidget):
    """Only native settings hold the manager; custom pages never receive it."""

    def __init__(self, manager, *, rotation_module=None, example_root: Path | None = None, parent=None):
        super().__init__(parent)
        font = QFont("Microsoft YaHei")
        if os.name == "nt" and not QFontMetrics(font).inFont("效"):
            font_root = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
            for filename in ("msyh.ttc", "msyhbd.ttc"):
                if (font_root / filename).is_file():
                    QFontDatabase.addApplicationFont(str(font_root / filename))
        self.setFont(font)
        self.manager = manager
        self.rotation = rotation_module
        self.example_root = example_root or Path(__file__).with_name("overlay_examples")
        self._loading = False
        self._dirty = False
        self._remote = callable(getattr(manager, "submit", None))
        self._pending = None
        self._diagnostic_dialog = None
        self._configuration_buttons = []
        self._live_buttons = []
        self._selected_id = None
        self.setObjectName("overlaySettingsPage")
        self.setStyleSheet("""
            QWidget#overlaySettingsPage { background:#eff5fc; color:#17314b; font-family:"Microsoft YaHei UI","Microsoft YaHei","Segoe UI"; font-size:13px; }
            QFrame#settingsHero { background:#123b63; border:1px solid #123b63; border-radius:20px; }
            QFrame#settingsCard { background:white; border:1px solid #dbe7f2; border-radius:16px; }
            QLabel { color:#17314b; background:transparent; }
            QLabel#settingsEyebrow { color:#91bff1; font-size:11px; font-weight:700; }
            QLabel#settingsTitle { color:white; font-size:26px; font-weight:700; }
            QLabel#settingsIntro { color:#d6e6f7; font-size:13px; }
            QLabel#connectionBadge { background:#e4f0fe; color:#174f83; border-radius:12px; padding:8px 12px; font-weight:600; }
            QLabel#feedback { background:#eaf4ff; border:1px solid #cee1f5; padding:9px 12px; border-radius:10px; color:#245783; }
            QListWidget { background:transparent; border:0; outline:0; color:#17314b; }
            QListWidget::item { padding:12px 10px; margin:3px 0; border-radius:9px; }
            QListWidget::item:selected { background:#e5f0fe; color:#154d83; font-weight:700; }
            QListWidget::item:hover { background:#f1f6fc; }
            QLineEdit,QComboBox,QSpinBox { background:white; color:#17314b; border:1px solid #ccddeb; padding:7px 9px; min-height:20px; border-radius:8px; selection-background-color:#c7defe; }
            QLineEdit:focus,QComboBox:focus,QSpinBox:focus { border-color:#3b82c4; }
            QPushButton { background:#f6f9fd; color:#285276; border:1px solid #cddfed; border-radius:9px; padding:8px 10px; min-height:20px; }
            QPushButton:hover { background:#eaf3ff; border-color:#86b4df; }
            QPushButton:focus { border:2px solid #387db8; }
            QPushButton[primary="true"] { background:#185588; color:white; border-color:#185588; font-weight:600; }
            QPushButton[danger="true"] { color:#a74949; background:#fff9f9; border-color:#e8d0d0; }
            QPushButton:disabled { color:#94a6b7; background:#f1f5f9; border-color:#dce6ef; }
            QCheckBox { color:#284963; spacing:8px; padding:5px 0; }
            QCheckBox::indicator { width:17px; height:17px; }
            QLabel[muted="true"] { color:#607a94; }
            QScrollArea { border:0; background:transparent; }
            QWidget#settingsContent { background:white; }
            QSlider::groove:horizontal { background:#dce7f2; height:5px; border-radius:2px; }
            QSlider::sub-page:horizontal { background:#4287bd; border-radius:2px; }
            QSlider::handle:horizontal { background:white; border:2px solid #3b81b8; width:13px; margin:-6px 0; border-radius:8px; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(14)
        hero = QFrame()
        hero.setObjectName("settingsHero")
        heading = QHBoxLayout(hero)
        heading.setContentsMargins(24, 19, 24, 19)
        labels = QVBoxLayout()
        labels.setSpacing(5)
        for name, text in (("settingsEyebrow", "OVERLAY SETTINGS"), ("settingsTitle", "悬浮窗"), ("settingsIntro", "管理窗口、调整显示，让战斗信息按你的习惯呈现。")):
            label = QLabel(text)
            label.setObjectName(name)
            label.setWordWrap(True)
            labels.addWidget(label)
        heading.addLayout(labels, 1)
        self.connection_badge = QLabel()
        self.connection_badge.setObjectName("connectionBadge")
        heading.addWidget(self.connection_badge, 0, Qt.AlignmentFlag.AlignTop)
        outer.addWidget(hero)
        self.feedback = QLabel()
        self.feedback.setObjectName("feedback")
        self.feedback.setWordWrap(True)
        self.feedback.hide()
        outer.addWidget(self.feedback)
        shell = QHBoxLayout()
        shell.setSpacing(16)
        outer.addLayout(shell, 1)
        card = QFrame()
        card.setObjectName("settingsCard")
        card.setFixedWidth(218)
        sidebar = QVBoxLayout(card)
        sidebar.setContentsMargins(14, 16, 14, 16)
        title = QLabel("我的悬浮窗")
        title.setStyleSheet("font-size:17px;font-weight:700")
        sidebar.addWidget(title)
        self.window_count = QLabel("各窗口独立配置")
        self.window_count.setProperty("muted", True)
        sidebar.addWidget(self.window_count)
        self.items = QListWidget()
        self.items.setMinimumHeight(120)
        sidebar.addWidget(self.items, 1)
        for label, callback in (
            ("添加效果监控", lambda: self._add_example("effects-monitor.html", "效果监控")),
            ("打开数据检查器", lambda: self._add_example("topic-inspector.html", "数据检查器")),
            ("添加本地 HTML", self._add_file), ("添加网页地址", self._add_url),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            self._configuration_buttons.append(button)
            sidebar.addWidget(button)
        hint = QLabel("页面由你自行编写，客户端提供只读数据接口。")
        hint.setWordWrap(True)
        hint.setProperty("muted", True)
        sidebar.addWidget(hint)
        shell.addWidget(card)
        self.pages = QStackedWidget()
        shell.addWidget(self.pages, 1)
        self._make_legacy_page()
        self._make_entry_page()
        self.items.currentItemChanged.connect(self._selection_changed)
        for field in (self.name, self.source):
            field.textChanged.connect(self._mark_dirty)
        self.source_type.currentIndexChanged.connect(self._mark_dirty)
        for spin in self.geometry_inputs.values():
            spin.valueChanged.connect(self._mark_dirty)
        for check in [*self.flags.values(), self.legacy_enabled]:
            check.toggled.connect(self._mark_dirty)
        for slider in (self.opacity, self.legacy_opacity):
            slider.valueChanged.connect(self._mark_dirty)
        if self._remote:
            manager.snapshotChanged.connect(self._snapshot_changed)
            manager.connectionChanged.connect(lambda _status: self._update_connection())
            manager.operationFinished.connect(self._operation_finished)
            manager.operationFailed.connect(self._operation_failed)
        self.refresh_list()

    def _make_legacy_page(self):
        page = QFrame()
        page.setObjectName("settingsCard")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)
        self.legacy_title = QLabel()
        self.legacy_title.setStyleSheet("font-size:21px;font-weight:bold")
        layout.addWidget(self.legacy_title)
        self.legacy_enabled = QCheckBox("显示基础 DPS 悬浮窗")
        layout.addWidget(self.legacy_enabled)
        layout.addWidget(QLabel("透明度"))
        self.legacy_opacity = QSlider(Qt.Orientation.Horizontal)
        self.legacy_opacity.setRange(35, 100)
        slider = QHBoxLayout()
        slider.addWidget(self.legacy_opacity, 1)
        self.legacy_opacity_value = QLabel()
        self.legacy_opacity_value.setFixedWidth(42)
        self.legacy_opacity.valueChanged.connect(lambda value: self.legacy_opacity_value.setText(f"{value}%"))
        slider.addWidget(self.legacy_opacity_value)
        layout.addLayout(slider)
        self.legacy_scale_label = QLabel("指导条大小")
        self.legacy_scale = QSlider(Qt.Orientation.Horizontal)
        self.legacy_scale.setRange(50, 200)
        layout.addWidget(self.legacy_scale_label)
        layout.addWidget(self.legacy_scale)
        self.legacy_description = QLabel()
        self.legacy_description.setWordWrap(True)
        self.legacy_description.setProperty("muted", True)
        layout.addWidget(self.legacy_description)
        self.legacy_buttons = QWidget()
        row = QHBoxLayout(self.legacy_buttons)
        guide_actions = (("网站编辑", self._open_editor), ("立即同步", self._sync),
                         ("恢复指导条位置", self._recover_guide)) if _rotation_client.ROTATION_GUIDANCE_ENABLED else ()
        for text, callback in guide_actions:
            button = QPushButton(text)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addWidget(self.legacy_buttons)
        layout.addStretch()
        save = QPushButton("保存设置")
        save.setProperty("primary", True)
        save.clicked.connect(self._save_legacy)
        self.legacy_save = save
        self._configuration_buttons.append(save)
        actions = QHBoxLayout()
        actions.addWidget(save)
        actions.addStretch()
        layout.addLayout(actions)
        self.pages.addWidget(page)

    def _make_entry_page(self):
        card = QFrame()
        card.setObjectName("settingsCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 20, 20, 18)
        card_layout.setSpacing(15)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        page.setObjectName("settingsContent")
        layout = QVBoxLayout(page)
        title = QLabel("独立悬浮窗")
        title.setStyleSheet("font-size:21px;font-weight:bold")
        layout.addWidget(title)
        self.entry_description = QLabel("选择本地 HTML 文件或 HTTPS 网页，独立调整这个窗口的显示方式。")
        self.entry_description.setWordWrap(True)
        self.entry_description.setProperty("muted", True)
        layout.addWidget(self.entry_description)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setMaxLength(80)
        self.source_type = QComboBox()
        self.source_type.addItem("本地 HTML", "file")
        self.source_type.addItem("网页地址", "url")
        self.source = QLineEdit()
        self.source.setMaxLength(4096)
        source_row = QHBoxLayout()
        source_row.addWidget(self.source, 1)
        choose = QPushButton("选择文件")
        choose.clicked.connect(self._choose_source)
        source_row.addWidget(choose)
        form.addRow("名称", self.name)
        form.addRow("来源", self.source_type)
        form.addRow("入口", source_row)
        self.geometry_inputs = {}
        dimensions = QGridLayout()
        for index, (key, label, low, high) in enumerate((("x", "横坐标", -100000, 100000), ("y", "纵坐标", -100000, 100000), ("width", "宽度", 160, 4096), ("height", "高度", 100, 4096))):
            spin = QSpinBox()
            spin.setRange(low, high)
            spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            spin.setToolTip("可直接输入数值，或使用上下方向键调整。")
            self.geometry_inputs[key] = spin
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addWidget(spin, 1)
            dimensions.addLayout(row, index // 2, index % 2)
        form.addRow("位置与尺寸", dimensions)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(20, 100)
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self.opacity, 1)
        self.opacity_value = QLabel()
        self.opacity_value.setFixedWidth(42)
        self.opacity.valueChanged.connect(lambda value: self.opacity_value.setText(f"{value}%"))
        opacity_row.addWidget(self.opacity_value)
        form.addRow("透明度", opacity_row)
        layout.addLayout(form)
        self.flags = {}
        flags = QGridLayout()
        for index, (key, label) in enumerate((("enabled", "随客户端启用"), ("visible", "显示窗口"), ("locked", "锁定位置和尺寸"), ("clickThrough", "鼠标穿透"))):
            check = QCheckBox(label)
            self.flags[key] = check
            flags.addWidget(check, index // 2, index % 2)
        layout.addLayout(flags)
        notice = QLabel("未锁定时可拖动窗口顶栏、调整尺寸；无法操作时可用“找回窗口”。")
        notice.setWordWrap(True)
        notice.setProperty("muted", True)
        layout.addWidget(notice)
        layout.addStretch()
        actions = QHBoxLayout()
        for label, callback in (("保存设置", self._save_entry), ("重载", self._reload), ("错误日志", self._show_diagnostics), ("找回窗口", self._recover), ("删除", self._delete)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            if label == "保存设置":
                button.setProperty("primary", True)
            if label == "删除":
                button.setProperty("danger", True)
                actions.addStretch()
            (self._configuration_buttons if label in {"保存设置", "删除"} else self._live_buttons).append(button)
            actions.addWidget(button)
        scroll.setWidget(page)
        card_layout.addWidget(scroll, 1)
        card_layout.addLayout(actions)
        self.pages.addWidget(card)

    def _mark_dirty(self, *_args):
        if not self._loading:
            self._dirty = True

    def _capabilities(self):
        return self.manager.snapshot.get("capabilities", {}) if self._remote else {"configuration": True, "liveActions": True}

    def _update_connection(self):
        state = self.manager.snapshot if self._remote else {"online": True}
        writable = bool(self._capabilities().get("configuration"))
        text = "客户端在线 · 实时生效" if state.get("online") else ("离线设置 · 下次启动生效" if writable else "连接中 · 等待客户端")
        self.connection_badge.setText(text)
        self.connection_badge.setToolTip(str(state.get("status", "")))
        ready = self._pending is None
        for button in self._configuration_buttons:
            button.setEnabled(ready and writable)
        for button in self._live_buttons:
            button.setEnabled(ready and bool(self._capabilities().get("liveActions")))
        self.items.setEnabled(ready)
        self.pages.setEnabled(ready)
        self.legacy_save.setEnabled(ready and writable and (self._remote or self.rotation is not None))

    def _snapshot_changed(self, _snapshot):
        self.refresh_list(preserve_draft=self._dirty or self._pending is not None)

    def on_activated(self):
        self.refresh_list(preserve_draft=self._dirty or self._pending is not None)

    def _feedback(self, message, *, error=False):
        self.feedback.setText(message)
        self.feedback.setStyleSheet("color:#a14141;background:#fff2f2;border-color:#edcccc" if error else "")
        self.feedback.setVisible(bool(message))

    def _request(self, operation, arguments, *, after=None, saved=False):
        if self._pending is not None:
            return
        live = operation in {"reload", "recover", "diagnostics"}
        if not self._capabilities().get("liveActions" if live else "configuration"):
            self._feedback("悬浮窗服务尚未连接，请稍后重试。", error=True)
            return
        try:
            request_id = self.manager.submit(operation, arguments)
        except (ValueError, OSError, RuntimeError) as exc:
            self._feedback(str(exc), error=True)
            return
        self._pending = (request_id, after, saved)
        self._feedback("正在处理…")
        self._update_connection()

    def _operation_finished(self, request_id, result):
        if self._pending is None or self._pending[0] != request_id:
            return
        _, after, saved = self._pending
        self._pending = None
        if saved:
            self._dirty = False
        self._update_connection()
        self._feedback("设置已保存，下次启动采集客户端后生效。" if result.get("offlineSaved") else "设置已保存。" if saved else "操作已完成。")
        if after:
            after(result.get("value"))
        elif saved:
            self.refresh_list()

    def _operation_failed(self, request_id, message):
        if self._pending is not None and self._pending[0] == request_id:
            self._pending = None
            self._update_connection()
            self._feedback(str(message), error=True)

    def _confirm_discard(self):
        return not self._dirty or QMessageBox.question(self, "尚未保存", "放弃当前窗口尚未保存的修改？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def refresh_list(self, preferred_id=None, *, preserve_draft=False):
        selected = preferred_id or self._selected_id or "meter"
        self._loading = True
        self.items.clear()
        modules = [("meter", "基础 DPS")]
        if _rotation_client.ROTATION_GUIDANCE_ENABLED:
            modules.append(("guide", "排轴指导"))
        for key, text in modules:
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.items.addItem(item)
        for entry in self.manager.list_entries():
            item = QListWidgetItem(entry["name"])
            item.setData(Qt.ItemDataRole.UserRole, entry["id"])
            self.items.addItem(item)
        self.window_count.setText(f"{self.items.count()} 个窗口 · 独立配置")
        target = 0
        for index in range(self.items.count()):
            if self.items.item(index).data(Qt.ItemDataRole.UserRole) == selected:
                target = index
                break
        self.items.setCurrentRow(target)
        self._loading = False
        if not preserve_draft:
            self._dirty = False
            self._selection_changed(self.items.item(target), None)
        self._update_connection()

    def _selection_changed(self, current, _previous):
        if self._loading or current is None:
            return
        key = current.data(Qt.ItemDataRole.UserRole)
        if self._dirty and key != self._selected_id and not self._confirm_discard():
            self._loading = True
            for index in range(self.items.count()):
                if self.items.item(index).data(Qt.ItemDataRole.UserRole) == self._selected_id:
                    self.items.setCurrentRow(index)
                    break
            self._loading = False
            return
        self._loading = True
        try:
            self._populate_selection(current)
            self._dirty = False
        finally:
            self._loading = False

    def _populate_selection(self, current):
        key = current.data(Qt.ItemDataRole.UserRole)
        if key == "guide" and not _rotation_client.ROTATION_GUIDANCE_ENABLED:
            return
        self._selected_id = key
        if key in {"meter", "guide"}:
            self.pages.setCurrentIndex(0)
            guide = key == "guide"
            self.legacy_title.setText("排轴指导" if guide else "基础 DPS")
            self.pages.currentWidget().setEnabled(self._remote or self.rotation is not None)
            self.legacy_scale.setVisible(guide)
            self.legacy_scale_label.setVisible(guide)
            self.legacy_buttons.setVisible(guide)
            if self.rotation or self._remote:
                settings = self.manager.snapshot.get("meter", {}) if self._remote else self.rotation.settings
                self.legacy_enabled.setChecked(settings["guideEnabled" if guide else "meterEnabled"])
                self.legacy_opacity.setValue(round(settings.get("guideOpacity" if guide else "meterOpacity", settings["opacity"]) * 100))
                self.legacy_scale.setValue(round(settings["guideScale"] * 100))
            self.legacy_description.setText("地图和阵容匹配后，按已验证的官方时钟提供指导。" if guide else "显示队伍 DPS / rDPS 统计，可单独调整窗口透明度。")
            return
        entry = next((row for row in self.manager.list_entries() if row["id"] == key), None)
        if entry is None:
            return
        self._loaded_entry = copy.deepcopy(entry)
        self.pages.setCurrentIndex(1)
        self.name.setText(entry["name"])
        self.source_type.setCurrentIndex(self.source_type.findData(entry["sourceType"]))
        self.source.setText(entry["sourceValue"])
        self.source.setCursorPosition(0)
        self.source.setToolTip(entry["sourceValue"])
        for field, spin in self.geometry_inputs.items():
            spin.setValue(entry["geometry"][field])
        self.opacity.setValue(round(entry["opacity"] * 100))
        for field, check in self.flags.items():
            check.setChecked(entry[field])

    def _action(self, callback):
        try:
            return callback()
        except (ValueError, OSError, RuntimeError) as exc:
            self._feedback(str(exc), error=True)
            return None

    def _add(self, name, source_type, source_value):
        if not self._confirm_discard():
            return
        if self._remote:
            self._request("add", {"name": name, "sourceType": source_type, "sourceValue": source_value},
                          after=lambda entry: self.refresh_list(entry["id"]), saved=True)
            return
        entry = self._action(lambda: self.manager.add_entry(name, source_type, source_value))
        if entry:
            self.refresh_list(entry["id"])

    def _add_example(self, filename, name):
        path = self.example_root / filename
        if not path.is_file():
            self._feedback("示例未找到，请确认客户端包含示例页面。", error=True)
            return
        if self._remote:
            if not self._confirm_discard():
                return
            dimensions = {"width": 460 if filename == "effects-monitor.html" else 660,
                          "height": 620 if filename == "effects-monitor.html" else 540}
            def added(entry):
                self._request("update", {"id": entry["id"], "patch": {"geometry": dimensions}},
                              after=lambda _value: self.refresh_list(entry["id"]), saved=True)
            self._request("add", {"name": name, "sourceType": "file", "sourceValue": str(path)}, after=added, saved=True)
            return
        entry = self._action(lambda: self.manager.add_entry(name, "file", str(path)))
        if entry:
            geometry = {**entry["geometry"], "width": 460 if filename == "effects-monitor.html" else 660,
                        "height": 620 if filename == "effects-monitor.html" else 540}
            self._action(lambda: self.manager.update_entry(entry["id"], geometry=geometry))
            self.refresh_list(entry["id"])

    def _add_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, "选择悬浮窗页面", "", "HTML 页面 (*.html *.htm)")
        if filename:
            self._add(Path(filename).stem, "file", filename)

    def _add_url(self):
        url, accepted = QInputDialog.getText(self, "添加网页悬浮窗", "网页地址（HTTPS，自定义订阅页面）")
        if accepted and url.strip():
            self._add("网页悬浮窗", "url", url.strip())

    def _choose_source(self):
        filename, _ = QFileDialog.getOpenFileName(self, "选择悬浮窗页面", "", "HTML 页面 (*.html *.htm)")
        if filename:
            self.source_type.setCurrentIndex(self.source_type.findData("file"))
            self.source.setText(filename)

    def _save_entry(self):
        loaded = self._loaded_entry
        values = {"name": self.name.text().strip(), "sourceType": self.source_type.currentData(),
                  "sourceValue": self.source.text().strip(), "opacity": self.opacity.value() / 100,
                  **{key: check.isChecked() for key, check in self.flags.items()}}
        changed = {key: value for key, value in values.items()
                   if value != (round(loaded[key] * 100) / 100 if key == "opacity" else loaded[key])}
        dimensions = {key: spin.value() for key, spin in self.geometry_inputs.items()
                      if spin.value() != loaded["geometry"][key]}
        if dimensions:
            changed["geometry"] = dimensions if self._remote else {**self.manager.get_entry(self._selected_id)["geometry"], **dimensions}
        if self._remote:
            self._request("update", {"id": self._selected_id, "patch": changed}, saved=True)
            return
        entry = self._action(lambda: self.manager.update_entry(
            self._selected_id, **changed,
        ))
        if entry:
            self.refresh_list(entry["id"])

    def _reload(self):
        if self._remote:
            self._request("reload", {"id": self._selected_id})
            return
        self._action(lambda: self.manager.reload_entry(self._selected_id))

    def _show_diagnostics(self):
        self._request_diagnostics(self._selected_id)

    def _request_diagnostics(self, identifier):
        if self._remote:
            self._request("diagnostics", {"id": identifier}, after=lambda text: self._display_diagnostics(text, identifier))
            return
        self._display_diagnostics(self.manager.diagnostics.text(identifier), identifier)

    def _display_diagnostics(self, content, identifier):
        if self._diagnostic_dialog is not None:
            self._diagnostic_dialog.close()
        dialog = QDialog(self)
        dialog.setWindowTitle("悬浮窗错误日志")
        dialog.resize(720, 430)
        layout = QVBoxLayout(dialog)
        notice = QLabel("仅显示此窗口本次运行最近 100 条脱敏诊断。完整地址、路径及常见凭据会被隐藏；高频日志会限流。")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(str(content))
        layout.addWidget(text, 1)
        actions = QHBoxLayout()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(lambda: self._request_diagnostics(identifier))
        copy = QPushButton("复制诊断")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(text.toPlainText()))
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        for button in (refresh, copy, close):
            actions.addWidget(button)
        layout.addLayout(actions)
        self._diagnostic_dialog = dialog
        dialog.show()

    def _recover(self):
        if not self._confirm_discard():
            return
        if self._remote:
            self._request("recover", {"id": self._selected_id}, saved=True)
            return
        self._action(lambda: self.manager.recover_entry(self._selected_id))
        self.refresh_list()

    def _delete(self):
        if QMessageBox.question(self, "删除悬浮窗", "删除此窗口及其窗口配置？原始 HTML 文件会保留。") == QMessageBox.StandardButton.Yes:
            if self._remote:
                self._request("remove", {"id": self._selected_id}, after=lambda _value: self.refresh_list("meter"), saved=True)
                return
            self._action(lambda: self.manager.remove_entry(self._selected_id))
            self.refresh_list("meter")

    def _save_legacy(self):
        if self._selected_id == "guide" and not _rotation_client.ROTATION_GUIDANCE_ENABLED:
            return
        if self._remote:
            self._request("meter", {"enabled": self.legacy_enabled.isChecked(), "opacity": self.legacy_opacity.value() / 100}, saved=True)
            return
        if self.rotation:
            try:
                self.rotation.apply_module_settings(self._selected_id, enabled=self.legacy_enabled.isChecked(), opacity=self.legacy_opacity.value() / 100, scale=self.legacy_scale.value() / 100)
            except (ValueError, OSError, RuntimeError) as exc:
                self._feedback(str(exc), error=True)
                return
            self._dirty = False
            self.refresh_list("meter")
            self._feedback("设置已保存。")

    def _open_editor(self):
        if self.rotation and _rotation_client.ROTATION_GUIDANCE_ENABLED:
            self.rotation.open_editor()

    def _sync(self):
        if self.rotation and _rotation_client.ROTATION_GUIDANCE_ENABLED:
            self.rotation.controller.request_sync()

    def _recover_guide(self):
        if self.rotation and _rotation_client.ROTATION_GUIDANCE_ENABLED:
            self.rotation.settings.update(guidePositionMode="game", guideAnchor=[0.5, 0.13])
            self.rotation._save()

class OverlaySettingsDialog(QDialog):
    """Local-preview compatibility wrapper; the main uploader embeds the page."""
    def __init__(self, manager, *, rotation_module=None, example_root=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("悬浮窗设置")
        self.resize(980, 700)
        self.setMinimumSize(820, 560)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self._page = OverlaySettingsPage(manager, rotation_module=rotation_module, example_root=example_root, parent=self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._page)

    def __getattr__(self, name):
        page = self.__dict__.get("_page")
        if page is not None and hasattr(page, name):
            return getattr(page, name)
        raise AttributeError(name)

    def showEvent(self, event):
        if self.rotation:
            self.rotation.external_settings_open = True
        super().showEvent(event)

    def hideEvent(self, event):
        if self.rotation:
            self.rotation.external_settings_open = False
            self.rotation.window.set_editing(False)
        super().hideEvent(event)
