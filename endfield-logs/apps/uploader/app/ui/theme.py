from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QLabel, QWidget


WORKFLOW_STYLE = """
QWidget#traceImportRoot,
QWidget#battleUploadRoot {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 #f3f8ff,
        stop: 0.52 #e8f2ff,
        stop: 1 #eef7f2
    );
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
}
QFrame#workflowCard {
    background: rgba(255, 255, 255, 0.95);
    border: 1px solid rgba(18, 63, 109, 0.10);
    border-radius: 26px;
}
QFrame#heroStrip {
    background: #123b63;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 22px;
}
QFrame#dropZone,
QFrame#summaryPanel,
QFrame#progressPanel,
QFrame#filterPanel,
QFrame#actionPanel {
    background: #f8fbfe;
    border: 1px solid #dbe7f2;
    border-radius: 18px;
}
QLabel#workflowEyebrow {
    color: #8fbdf2;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#workflowTitle {
    color: #f8fbff;
    font-size: 27px;
    font-weight: 700;
}
QLabel#workflowBody {
    color: rgba(240, 246, 255, 0.78);
    font-size: 13px;
}
QLabel#sectionTitle {
    color: #17314b;
    font-size: 16px;
    font-weight: 700;
}
QLabel#subtleText,
QLabel#dropHint,
QLabel#progressLabel {
    color: #5f7690;
    font-size: 13px;
}
QLabel#fileLabel {
    color: #17314b;
    font-size: 15px;
    font-weight: 700;
}
QLabel#integrityLabel {
    color: #406387;
    font-size: 13px;
    font-weight: 600;
}
QLabel#logBadge {
    background: #e2f0ff;
    border: 1px solid #bdd9f7;
    border-radius: 16px;
    color: #1f5f9f;
    font-size: 18px;
    font-weight: 800;
    padding: 12px 16px;
}
QLabel#summaryLabel {
    color: #28435f;
    font-size: 13px;
    font-weight: 600;
}
QLineEdit,
QComboBox {
    background: #ffffff;
    border: 1px solid #d4e0ec;
    border-radius: 14px;
    color: #17314b;
    font-size: 13px;
    padding: 10px 12px;
    selection-background-color: #c7defe;
}
QComboBox {
    padding-right: 28px;
}
QLineEdit:focus,
QComboBox:focus {
    border: 1px solid #3b82c4;
}
QCheckBox#showUnclearedFilter {
    color: #35516f;
    font-size: 13px;
    font-weight: 600;
    spacing: 8px;
}
QCheckBox#showUnclearedFilter::indicator {
    height: 18px;
    width: 18px;
}
QListWidget {
    background: #ffffff;
    border: 1px solid #dbe7f2;
    border-radius: 18px;
    color: #233c57;
    font-size: 13px;
    outline: 0;
    padding: 8px;
}
QListWidget::item {
    border: 1px solid #e3edf7;
    border-radius: 14px;
    margin: 5px;
    padding: 12px 14px;
}
QListWidget::item:selected {
    background: #eef7ff;
    border: 1px solid #9ac2ea;
    color: #17314b;
}
QListWidget::item:hover {
    background: #f6faff;
}
QListWidget::indicator {
    height: 18px;
    width: 18px;
}
QProgressBar {
    background: #e7eff8;
    border: 0;
    border-radius: 8px;
    color: #17314b;
    font-size: 12px;
    font-weight: 600;
    height: 12px;
    text-align: center;
}
QProgressBar::chunk {
    background: #2f7fc0;
    border-radius: 8px;
}
QPushButton#primaryAction {
    background: #153d66;
    border: 0;
    border-radius: 15px;
    color: #ffffff;
    font-size: 14px;
    font-weight: 700;
    padding: 12px 18px;
}
QPushButton#primaryAction:hover {
    background: #1e5b92;
}
QPushButton#primaryAction:disabled {
    background: #b4c2d2;
    color: #eef3f9;
}
QPushButton#secondaryAction {
    background: #ffffff;
    border: 1px solid #d3dde8;
    border-radius: 15px;
    color: #35516f;
    font-size: 13px;
    font-weight: 600;
    padding: 11px 16px;
}
QPushButton#secondaryAction:hover {
    background: #f4f8fc;
    border-color: #b9cfe6;
}
QPushButton#secondaryAction:disabled {
    color: #9aa9b8;
    background: #f4f6f9;
}
QPushButton#ghostAction {
    background: transparent;
    border: 0;
    color: #47627f;
    font-size: 13px;
    font-weight: 600;
    padding: 10px 12px;
}
QPushButton#ghostAction:hover {
    color: #153d66;
    background: #edf5fd;
    border-radius: 12px;
}
QLabel#messageLabel {
    border-radius: 14px;
    font-size: 13px;
    font-weight: 600;
    padding: 10px 12px;
}
QLabel#messageLabel[tone="info"] {
    background: #eef7ff;
    border: 1px solid #c8def3;
    color: #24507d;
}
QLabel#messageLabel[tone="error"] {
    background: #fff1f1;
    border: 1px solid #f2c6c6;
    color: #b53131;
}
QScrollBar:vertical {
    background: transparent;
    margin: 8px 4px 8px 0;
    width: 10px;
}
QScrollBar::handle:vertical {
    background: #c7d6e6;
    border-radius: 5px;
    min-height: 28px;
}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0;
}
"""


MAIN_WINDOW_STYLE = """
QMainWindow#mainWindow {
    background: #eef6ff;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
}
QWidget#desktopWorkspace,
QStackedWidget#workspacePages,
QWidget#overlaySettingsHost {
    background: #eff6fd;
}
QFrame#appSidebar {
    background: #f8fbfe;
    border: 0;
    border-right: 1px solid #dbe7f2;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
}
QLabel#sidebarMark {
    background: #153e66;
    border-radius: 11px;
    color: #ffffff;
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 1px;
}
QLabel#sidebarBrand {
    color: #7490aa;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.5px;
}
QLabel#sidebarTitle {
    color: #173c60;
    font-size: 20px;
    font-weight: 700;
}
QLabel#sidebarCaption {
    color: #7d91a6;
    font-size: 11px;
    line-height: 1.4;
}
QLabel#sidebarSection {
    color: #8b9caf;
    font-size: 11px;
    font-weight: 600;
    padding-left: 9px;
}
QPushButton#sidebarNav {
    text-align: left;
    padding: 0 12px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 12px;
    color: #57728e;
    font-size: 14px;
    font-weight: 600;
}
QPushButton#sidebarNav:hover {
    background: #edf4fc;
    color: #173e65;
}
QPushButton#sidebarNav:checked {
    background: #173f67;
    color: #ffffff;
    border: 1px solid #173f67;
}
QPushButton#sidebarNav:focus {
    border: 1px solid #7eafdc;
}
QFrame#sidebarStatusCard {
    background: #edf5fc;
    border: 1px solid #e0ebf6;
    border-radius: 13px;
}
QLabel#sidebarStatus {
    color: #356188;
    font-size: 12px;
    font-weight: 600;
}
QLabel#sidebarStatusHint,
QLabel#sidebarVersion {
    color: #8b9daf;
    font-size: 10px;
}
QLabel#sidebarVersion {
    padding-left: 9px;
}
QStatusBar {
    background: #f8fbfe;
    border-top: 1px solid #dbe7f2;
    color: #49637e;
    min-height: 34px;
}
QStatusBar QLabel {
    color: #49637e;
    font-size: 12px;
    font-weight: 600;
    padding: 4px 8px;
}
QLabel#statusServer {
    border-radius: 10px;
    padding: 4px 10px;
}
"""


def install_shadow(widget: QWidget, *, blur_radius: int = 40, offset_y: int = 16) -> None:
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur_radius)
    shadow.setOffset(0, offset_y)
    shadow.setColor(QColor(18, 53, 91, 42))
    widget.setGraphicsEffect(shadow)


def set_message_label(label: QLabel, message: str, *, error: bool = False) -> None:
    label.setText(message)
    label.setVisible(bool(message))
    label.setProperty("tone", "error" if error else "info")
    label.style().unpolish(label)
    label.style().polish(label)


def set_pointing_hand(widget: QWidget) -> None:
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
