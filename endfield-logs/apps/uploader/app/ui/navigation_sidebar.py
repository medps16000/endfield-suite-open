"""Small native navigation surface for the unified desktop workspace."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QButtonGroup, QFrame, QLabel, QPushButton, QVBoxLayout


def _navigation_icon(kind: str) -> QIcon:
    icon = QIcon()
    for mode, state, color in ((QIcon.Mode.Normal, QIcon.State.Off, "#607c97"),
                                (QIcon.Mode.Normal, QIcon.State.On, "#ffffff"),
                                (QIcon.Mode.Active, QIcon.State.Off, "#173e65"),
                                (QIcon.Mode.Active, QIcon.State.On, "#ffffff"),
                                (QIcon.Mode.Selected, QIcon.State.On, "#ffffff")):
        pixmap = QPixmap(48, 48)
        pixmap.setDevicePixelRatio(2.0)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        if kind == "battle":
            painter.drawRoundedRect(4, 3, 16, 18, 3, 3)
            for row in (8, 12, 16):
                painter.drawLine(8, row, 16, row)
        else:
            painter.drawRoundedRect(3, 3, 14, 12, 3, 3)
            painter.drawRoundedRect(9, 10, 12, 11, 3, 3)
            painter.drawPoint(13, 14)
            painter.drawLine(13, 17, 17, 17)
        painter.end()
        icon.addPixmap(pixmap, mode, state)
    return icon


def _desktop_version() -> str:
    if getattr(sys, "frozen", False):
        path = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "version.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            version = value.get("version")
            if isinstance(version, str) and len(version) <= 40:
                return version
        except (OSError, ValueError, TypeError):
            pass
    return "本地预览"


class NavigationSidebar(QFrame):
    page_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("appSidebar")
        self.setFixedWidth(184)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 30, 18, 22)
        layout.setSpacing(0)

        mark = QLabel("LOGS")
        mark.setObjectName("sidebarMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(49, 41)
        brand = QLabel("ENDFIELD")
        brand.setObjectName("sidebarBrand")
        title = QLabel("日志客户端")
        title.setObjectName("sidebarTitle")
        caption = QLabel("记录战斗，定制你的桌面")
        caption.setObjectName("sidebarCaption")
        caption.setWordWrap(True)
        layout.addWidget(mark)
        layout.addSpacing(18)
        layout.addWidget(brand)
        layout.addSpacing(5)
        layout.addWidget(title)
        layout.addSpacing(10)
        layout.addWidget(caption)
        layout.addSpacing(35)

        group_label = QLabel("工作空间")
        group_label.setObjectName("sidebarSection")
        layout.addWidget(group_label)
        layout.addSpacing(10)
        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)
        self.buttons = {}
        for key, label, tooltip in (("battle", "战斗记录", "登录、导入日志与上传战斗记录"),
                                    ("overlays", "悬浮窗", "管理伤害统计、HTML 窗口与显示设置")):
            button = QPushButton(label)
            button.setObjectName("sidebarNav")
            button.setProperty("page", key)
            button.setCheckable(True)
            button.setIcon(_navigation_icon(key))
            button.setIconSize(QSize(24, 24))
            button.setMinimumHeight(48)
            button.setToolTip(tooltip)
            button.setAccessibleName(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.button_group.addButton(button)
            self.buttons[key] = button
            button.clicked.connect(lambda _checked=False, page=key: self.page_requested.emit(page))
            layout.addWidget(button)
            layout.addSpacing(9)
        layout.addStretch(1)

        status = QFrame()
        status.setObjectName("sidebarStatusCard")
        status_layout = QVBoxLayout(status)
        status_layout.setContentsMargins(12, 13, 12, 13)
        status_layout.setSpacing(5)
        self.status_label = QLabel("等待日志")
        self.status_label.setObjectName("sidebarStatus")
        self.status_label.setWordWrap(True)
        self.overlay_status_label = QLabel("悬浮窗 · 等待采集")
        self.overlay_status_label.setObjectName("sidebarStatusHint")
        self.overlay_status_label.setWordWrap(True)
        status.setToolTip("切换页面不会中断战斗记录监听或悬浮窗。")
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.overlay_status_label)
        layout.addWidget(status)
        layout.addSpacing(13)
        version = QLabel(_desktop_version())
        version.setObjectName("sidebarVersion")
        version.setToolTip("客户端版本")
        version.setWordWrap(True)
        layout.addWidget(version)
        self.set_current_page("battle")

    def set_current_page(self, page: str) -> None:
        self.buttons[page].setChecked(True)

    def set_monitoring(self, active: bool) -> None:
        self.status_label.setText("正在监听战斗" if active else "等待日志")

    def set_overlay_status(self, label: str) -> None:
        self.overlay_status_label.setText(str(label)[:48])
