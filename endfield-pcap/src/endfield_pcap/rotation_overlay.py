"""Independent transparent guide strip; only packets advance its steps."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import os
import sys
import tkinter as tk
import webbrowser
from tkinter import messagebox

import psutil
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from .rotation_client import OverlaySettingsStore, RotationGuideController
from . import rotation_client as _rotation_client

BG, FG, MUTED, ACCENT, CARD = "#17232a", "#e4edf2", "#92aab6", "#3cd2f0", "#22353e"


def format_guide_time(ms: int) -> str:
    total = max(0, int(ms))
    return f"{total // 60000}:{(total % 60000) / 1000:06.3f}"


def guide_steps(report: dict) -> list[dict]:
    return [s for s in report.get("steps", []) if isinstance(s, dict) and not s.get("completed")][:4]


def place_guide(game_bounds: tuple, size: tuple, settings: dict) -> tuple[int, int]:
    left, top, width, height = game_bounds
    if settings.get("guidePositionMode") == "absolute" and "guide" in settings.get("positions", {}):
        x, y = settings["positions"]["guide"]
    else:
        anchor = settings.get("guideAnchor", [0.5, 0.13])
        x, y = left + width*anchor[0] - size[0]/2, top + height*anchor[1]
    return (round(max(left, min(x, left+max(0, width-size[0])))),
            round(max(top, min(y, top+max(0, height-size[1])))))


def _windows_api():
    if os.name != "nt":
        return None
    api = ctypes.WinDLL("user32", use_last_error=True)
    signatures = {
        "GetForegroundWindow": ([], wintypes.HWND),
        "GetWindowThreadProcessId": ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        "GetAncestor": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
        "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL), "IsIconic": ([wintypes.HWND], wintypes.BOOL),
        "GetClientRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        "ClientToScreen": ([wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
        "GetWindowRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        "GetDpiForWindow": ([wintypes.HWND], wintypes.UINT),
        "SetThreadDpiAwarenessContext": ([wintypes.HANDLE], wintypes.HANDLE),
        "SetWindowPos": ([wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
    }
    for name, (args, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    return api


def game_window_context(game_exe: str, previous: dict | None = None, *, editing: bool = False) -> dict | None:
    """Read client geometry of the exact configured foreground process, never its memory."""
    api = _windows_api()
    if api is None:
        return None
    prior = api.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        hwnd, pid = api.GetAncestor(api.GetForegroundWindow(), 2), wintypes.DWORD()
        if not hwnd or not api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            return None
        if editing and pid.value == os.getpid() and previous:
            hwnd = previous["hwnd"]
            if not api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
                return None
        if psutil.Process(pid.value).name().casefold() != os.path.basename(game_exe).casefold():
            return None
        if not api.IsWindowVisible(hwnd) or api.IsIconic(hwnd):
            return None
        rect, origin = wintypes.RECT(), wintypes.POINT()
        if not api.GetClientRect(hwnd, ctypes.byref(rect)) or not api.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None
        width, height, dpi = rect.right-rect.left, rect.bottom-rect.top, api.GetDpiForWindow(hwnd)
        if width <= 0 or height <= 0 or dpi <= 0:
            return None
        return {"hwnd": hwnd, "rect": (origin.x, origin.y, width, height), "dpi": dpi}
    except (OSError, psutil.Error):
        return None
    finally:
        if prior:
            api.SetThreadDpiAwarenessContext(prior)


class GuideStrip(QWidget):
    def __init__(self, on_drag_finished=None) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("排轴指导")
        self.report, self.visible_steps, self.unit = {}, [], 1.0
        self.offline = self.editing = self.dragging = False
        self._origin, self._on_drag_finished = None, on_drag_finished
        self._images, self._note_height = {}, 0
        self._width, self._height = 580, 188

    def _font(self, size: int, bold: bool = False) -> QFont:
        font = QFont("Microsoft YaHei")
        font.setPixelSize(max(7, round(size*self.unit)))
        font.setBold(bold)
        return font

    def set_snapshot(self, report: dict, *, unit: float = 1.0, offline: bool = False) -> None:
        self.report, self.unit, self.offline = report, max(.1, float(unit)), offline
        self.visible_steps = guide_steps(report)
        self._width = 90 + (160 + 92*(len(self.visible_steps)-1) if self.visible_steps else 300)
        note = str(self.visible_steps[0].get("note") or "") if self.visible_steps else ""
        flags = Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere
        rect = QFontMetrics(self._font(13)).boundingRect(0, 0, round((self._width-88)*self.unit), 10000, int(flags), note)
        self._note_height = math.ceil(rect.height()/self.unit) if note else 0
        self._height = 200 + self._note_height + (22 if report.get("paused") or offline else 0)
        self.resize(self.sizeHint())
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(math.ceil(self._width*self.unit), math.ceil(self._height*self.unit))

    def set_editing(self, editing: bool) -> None:
        if self.editing != editing:
            shown, self.editing = self.isVisible(), editing
            self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, not editing)
            self.setCursor(Qt.CursorShape.SizeAllCursor if editing else Qt.CursorShape.ArrowCursor)
            if shown:
                self.show()
            self.update()

    def _text(self, p, rect, text, size, color=FG, *, bold=False, flags=Qt.AlignmentFlag.AlignCenter, elide=True):
        p.setFont(self._font(size, bold))
        if elide:
            text = p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, round(rect.width()))
        p.setPen(QColor(0, 0, 0, 210))
        p.drawText(rect.translated(self.unit, self.unit), int(flags), text)
        p.setPen(QColor(color))
        p.drawText(rect, int(flags), text)

    def _image(self, p, rect, path, *, avatar=False):
        if not path:
            return False
        try:
            stat = os.stat(path)
        except OSError:
            return False
        signature = (stat.st_mtime_ns, stat.st_size)
        cached = self._images.get(path)
        if cached is None or cached[0] != signature:
            self._images[path] = (signature, QPixmap(path))
        image = self._images[path][1]
        if image.isNull():
            return False
        p.save()
        if avatar:
            clip = QPainterPath()
            clip.addEllipse(rect)
            p.setClipPath(clip)
        fitted = image.scaled(round(rect.width()), round(rect.height()), Qt.AspectRatioMode.KeepAspectRatioByExpanding
                              if avatar else Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap(QPointF(rect.center().x()-fitted.width()/2, rect.center().y()-fitted.height()/2), fitted)
        p.restore()
        return True

    def paintEvent(self, _event) -> None:
        p, u = QPainter(self), self.unit
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        box = QRectF(0, 0, self.width(), self.height())
        band = QLinearGradient(box.topLeft(), box.topRight())
        for position, alpha in ((0, 0), (.08, 156), (.92, 156), (1, 0)):
            band.setColorAt(position, QColor(80, 84, 90, alpha))
        p.fillRect(box, band)
        if self.editing:
            p.setPen(QPen(QColor(ACCENT), u))
            p.drawRoundedRect(box.adjusted(u, u, -u, -u), 5*u, 5*u)
        title = str(self.report.get("name") or "排轴指导")
        if self.visible_steps and self.visible_steps[0].get("phase"):
            title = f"{self.visible_steps[0]['phase']} · {title}"
        self._text(p, QRectF(44*u, 5*u, (self._width-210)*u, 24*u), title, 11, MUTED,
                   flags=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._text(p, QRectF((self._width-165)*u, 5*u, 120*u, 24*u), format_guide_time(self.report.get("elapsedMs", 0)), 13, ACCENT,
                   flags=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if not self.visible_steps:
            self._text(p, QRectF(44*u, 45*u, (self._width-88)*u, 65*u),
                       "本轴已完成" if self.report.get("allCompleted") else "等待步骤资料", 20, bold=True)
        elapsed = int(self.report.get("elapsedMs") or 0)
        for index, step in enumerate(self.visible_steps):
            current = index == 0
            x, width = (44, 160) if current else (204+(index-1)*92, 92)
            icon_size = 64 if current else 48
            cx, cy = (x+width/2)*u, 100*u
            icon = QRectF(cx-icon_size*u/2, cy-icon_size*u/2, icon_size*u, icon_size*u)
            if current:
                p.setPen(QPen(QColor(ACCENT), 2.5*u))
                for y in (29, 35):
                    p.drawPolyline([QPointF(cx-6*u,y*u), QPointF(cx,(y+4)*u), QPointF(cx+6*u,y*u)])
            self._text(p, QRectF(x*u, 45*u, width*u, 20*u), str(step.get("actionName") or "技能"), 11,
                       FG if current else MUTED, bold=current)
            p.setBrush(QColor(24,26,30,224) if current else QColor(38,40,44,140))
            p.setPen(QPen(QColor(ACCENT if current else "#a2afb7"), (2.5 if current else 1.2)*u))
            p.drawEllipse(icon)
            inset = icon_size*.14*u
            if not self._image(p, icon.adjusted(inset,inset,-inset,-inset), str(step.get("iconPath") or "")):
                self._text(p, icon, "技", 24 if current else 18)
            avatar_size = (25 if current else 21)*u
            avatar = QRectF(icon.right()-avatar_size*.65,icon.bottom()-avatar_size*.65,avatar_size,avatar_size)
            if self._image(p, avatar, str(step.get("avatarPath") or ""), avatar=True):
                p.setPen(QPen(QColor(FG), u))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(avatar)
            self._text(p, QRectF(x*u,141*u,width*u,19*u), str(step.get("characterName") or "角色资料待更新"), 11, bold=current)
            remaining = int(step["atMs"])-elapsed
            timing = (f"{remaining/1000:.1f} 秒后" if remaining > 0 else "待释放") if current else format_guide_time(step["atMs"])
            self._text(p, QRectF(x*u,162*u,width*u,20*u), timing, 12 if current else 10, ACCENT if current else MUTED, bold=current)
        if self.visible_steps and self.visible_steps[0].get("note"):
            self._text(p, QRectF(44*u,188*u,(self._width-88)*u,self._note_height*u), str(self.visible_steps[0]["note"]), 13,
                       flags=Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere, elide=False)
        if self.report.get("paused"):
            self._text(p, QRectF(44*u,(self._height-23)*u,(self._width-88)*u,20*u), "已暂停 · 保留最后确认进度", 10, MUTED)
        elif self.offline:
            self._text(p, QRectF(44*u,(self._height-18)*u,(self._width-88)*u,16*u), "离线缓存", 9, MUTED)
        p.end()

    def mousePressEvent(self, event) -> None:
        if self.editing and event.button() == Qt.MouseButton.LeftButton:
            self.dragging, self._origin = True, event.globalPosition().toPoint()-self.pos()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self.dragging and self._origin is not None:
            self.move(event.globalPosition().toPoint()-self._origin)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self.dragging:
            self.dragging, self._origin = False, None
            if self._on_drag_finished:
                self._on_drag_finished()
            event.accept()


class RotationOverlayModule:
    def __init__(self, parent: tk.Tk, controller: RotationGuideController, *, on_meter_change) -> None:
        self.parent, self.controller, self.on_meter_change = parent, controller, on_meter_change
        self.store = OverlaySettingsStore()
        self.settings = self.store.load()
        self.settings_window = self._sync_label = self._game_context = None
        self._closed = self._shown = False
        self._app = QApplication.instance() or QApplication([])
        self._app.setQuitOnLastWindowClosed(False)
        self.window = GuideStrip(self._finish_drag)
        self.window.setWindowOpacity(self.settings.get("guideOpacity", self.settings["opacity"]))
        self._pump_id = self.parent.after(16, self._pump)

    def _pump(self) -> None:
        if self._closed:
            return
        try:
            self._app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self._qt_pump_error = None
        except Exception as exc:
            error = (type(exc).__name__, str(exc))
            if getattr(self, "_qt_pump_error", None) != error:
                print(f"浮窗事件处理失败：{error[0]}: {error[1]}", file=sys.stderr)
            self._qt_pump_error = error
            try:
                self.window.hide()
            except Exception:
                pass
            self._shown = False
        finally:
            if not self._closed:
                self._pump_id = self.parent.after(16, self._pump)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.parent.after_cancel(self._pump_id)
        except tk.TclError:
            pass
        self.window.close()
        self.window.deleteLater()
        self._app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _finish_drag(self) -> None:
        if not _rotation_client.ROTATION_GUIDANCE_ENABLED:
            return
        api = _windows_api()
        if not self._game_context or api is None:
            return
        prior = api.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        try:
            rect = wintypes.RECT()
            if not api.GetWindowRect(int(self.window.winId()), ctypes.byref(rect)):
                return
            x,y,w,h = self._game_context["rect"]
            self.settings.update(guidePositionMode="game", guideAnchor=[max(0,min(1,((rect.left+rect.right)/2-x)/w)), max(0,min(1,(rect.top-y)/h))])
            self._save()
        finally:
            if prior:
                api.SetThreadDpiAwarenessContext(prior)

    def _save(self) -> None:
        try:
            self.store.save(self.settings)
        except OSError:
            messagebox.showerror("浮窗设置", "无法保存本地设置，请检查磁盘空间。", parent=self.settings_window or self.parent)

    def save_meter_position(self, x: int, y: int) -> None:
        self.settings["positions"]["meter"] = [max(0,x),max(0,y)]
        self._save()

    def _apply(self, meter: bool, guide: bool, opacity: float, scale: float | None = None) -> None:
        self.settings.update(meterEnabled=meter, guideEnabled=guide, opacity=opacity,
                             meterOpacity=opacity, guideOpacity=opacity)
        if scale is not None:
            self.settings["guideScale"] = scale
        self.parent.attributes("-alpha", opacity)
        self.window.setWindowOpacity(opacity)
        self.on_meter_change(meter)
        self._save()
        if not guide:
            self.window.hide()
            self._shown = False

    def apply_module_settings(self, module: str, *, enabled: bool, opacity: float, scale: float = 1.0) -> None:
        if module == "guide" and not _rotation_client.ROTATION_GUIDANCE_ENABLED:
            raise ValueError("排轴与指导条已停用")
        if module not in {"meter", "guide"} or type(enabled) is not bool or not .35 <= opacity <= 1:
            raise ValueError("浮窗设置无效")
        if module == "guide" and not .5 <= scale <= 2:
            raise ValueError("指导条大小无效")
        self.settings[module + "Enabled"] = enabled
        self.settings[module + "Opacity"] = opacity
        if module == "meter":
            self.parent.attributes("-alpha", opacity)
            self.on_meter_change(enabled)
        else:
            self.settings["guideScale"] = scale
            self.window.setWindowOpacity(opacity)
            if not enabled:
                self.window.hide()
                self._shown = False
        self._save()

    def open_editor(self) -> None:
        if not _rotation_client.ROTATION_GUIDANCE_ENABLED:
            return
        settings = self.controller.settings_store.load_settings()
        webbrowser.open(settings.web_base_url.rstrip("/")+"/rotations")

    def open_settings(self) -> None:
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.deiconify()
            self.settings_window.lift()
            return
        dialog = tk.Toplevel(self.parent)
        self.settings_window = dialog
        dialog.title("浮窗设置")
        dialog.attributes("-topmost", True)
        dialog.configure(bg=BG)
        dialog.resizable(False,False)
        body = tk.Frame(dialog,bg=BG)
        body.pack(fill="both",expand=True,padx=18,pady=14)
        tk.Label(body,text="浮窗模块",font=("Microsoft YaHei",12,"bold"),bg=BG,fg=FG).pack(anchor="w")
        if not _rotation_client.ROTATION_GUIDANCE_ENABLED:
            meter = tk.BooleanVar(value=self.settings["meterEnabled"])
            opacity = tk.DoubleVar(value=self.settings.get("meterOpacity", self.settings["opacity"]))
            tk.Checkbutton(body,text="伤害统计（DPS / rDPS）",variable=meter,bg=BG,fg=FG,
                           selectcolor=CARD,activebackground=BG,activeforeground=FG).pack(anchor="w",pady=2)
            tk.Label(body,text="透明度",bg=BG,fg=MUTED).pack(anchor="w",pady=(6,0))
            tk.Scale(body,from_=.35,to=1.,resolution=.05,variable=opacity,orient="horizontal",
                     bg=BG,fg=FG,highlightthickness=0,length=360).pack(fill="x")
            tk.Button(body,text="保存",command=lambda:self.apply_module_settings(
                "meter",enabled=meter.get(),opacity=opacity.get()),bg=CARD,fg=FG,
                bd=0,padx=12,pady=5).pack(anchor="w",pady=(8,0))
            def close_settings():
                self.settings_window = self._sync_label = None
                dialog.destroy()
            dialog.protocol("WM_DELETE_WINDOW",close_settings)
            dialog.update_idletasks()
            dialog.geometry(f"{max(400,dialog.winfo_reqwidth())}x{max(220,dialog.winfo_reqheight())}")
            return
        meter,guide = tk.BooleanVar(value=self.settings["meterEnabled"]),tk.BooleanVar(value=self.settings["guideEnabled"])
        opacity,scale = tk.DoubleVar(value=self.settings["opacity"]),tk.DoubleVar(value=self.settings["guideScale"])
        for text,value in (("伤害统计（DPS / rDPS）",meter),("排轴指导条",guide)):
            tk.Checkbutton(body,text=text,variable=value,bg=BG,fg=FG,selectcolor=CARD,activebackground=BG,activeforeground=FG).pack(anchor="w",pady=2)
        for title,value,low,high,increment in (("透明度",opacity,.35,1.,.05),("指导条大小",scale,.5,2.,.1)):
            tk.Label(body,text=title,bg=BG,fg=MUTED).pack(anchor="w",pady=(6,0))
            tk.Scale(body,variable=value,from_=low,to=high,resolution=increment,orient="horizontal",length=365,
                     bg=BG,fg=FG,highlightthickness=0,troughcolor=CARD).pack(fill="x")
        def reset_position():
            self.settings.update(guidePositionMode="game",guideAnchor=[0.5,0.13])
            self._save()
        tk.Button(body,text="恢复指导条默认位置",command=reset_position,bg=CARD,fg=FG,bd=0,padx=10,pady=5).pack(anchor="w",pady=6)
        tk.Label(body,text="打开此设置时可拖动战斗中的指导条，松手保存位置。",bg=BG,fg=MUTED,font=("Microsoft YaHei",8)).pack(anchor="w")
        supported = self.controller.sync_state()["clockSupported"]
        tk.Label(body,text="地图和四人阵容匹配后，按官方算法计时。" if supported else "当前游戏计时版本未验证，指导条保持隐藏。",
                 bg=BG,fg=MUTED,font=("Microsoft YaHei",8)).pack(anchor="w",pady=5)
        self._sync_label = tk.Label(body,text=self.controller.sync_state()["message"],bg=BG,fg=MUTED,font=("Microsoft YaHei",8))
        self._sync_label.pack(anchor="w",pady=3)
        buttons = tk.Frame(body,bg=BG)
        buttons.pack(fill="x",pady=(8,0))
        for text,command in (("网站编辑",self.open_editor),("立即同步",self.controller.request_sync),
                             ("保存",lambda:self._apply(meter.get(),guide.get(),opacity.get(),scale.get()))):
            tk.Button(buttons,text=text,command=command,bg=CARD,fg=FG,bd=0,padx=12,pady=5).pack(side="left",padx=(0,8))
        self.window.set_editing(True)
        def close_settings():
            self.window.set_editing(False)
            self.settings_window = self._sync_label = None
            dialog.destroy()
        dialog.protocol("WM_DELETE_WINDOW",close_settings)
        dialog.update_idletasks()
        dialog.geometry(f"{max(430,dialog.winfo_reqwidth())}x{max(380,dialog.winfo_reqheight())}")

    def update(self, *, available: bool, game_exe: str = "Endfield.exe", game_bounds: tuple | None = None) -> None:
        if not _rotation_client.ROTATION_GUIDANCE_ENABLED:
            self.window.hide()
            self.window.set_editing(False)
            self._shown = False
            return
        # A focus change hides only the UI. It must not reset capture or clock.
        self.controller.set_available(available)
        editing = (self.settings_window is not None and self.settings_window.winfo_exists()) or getattr(self, "external_settings_open", False)
        self.window.set_editing(bool(editing))
        if self._sync_label is not None and editing:
            self._sync_label.configure(text=self.controller.sync_state()["message"])
        report = self.controller.snapshot() if self.settings["guideEnabled"] else None
        if report is None:
            self.window.hide()
            self._shown = False
            return
        context = ({"rect":game_bounds,"dpi":96} if game_bounds is not None else
                   game_window_context(game_exe,self._game_context,editing=bool(editing)))
        if report is None or context is None:
            self.window.hide()
            self._shown = False
            return
        self._game_context = context
        bounds,dpi = context["rect"],context["dpi"]/96
        factor = min(2.5,max(.6,bounds[3]/1080))*self.settings["guideScale"]
        self.window.set_snapshot(report,unit=factor/dpi,offline=self.controller.sync_state()["offline"])
        if self.window.width()*dpi > bounds[2]-12:
            factor *= max(.1, (bounds[2]-12)/(self.window.width()*dpi))
            self.window.set_snapshot(report,unit=factor/dpi,offline=self.controller.sync_state()["offline"])
        size = (math.ceil(self.window.width()*dpi),math.ceil(self.window.height()*dpi))
        x,y = place_guide(bounds,size,self.settings)
        if not self.window.dragging:
            api = _windows_api()
            if api is not None:
                prior = api.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
                try:
                    if not api.SetWindowPos(int(self.window.winId()),None,x,y,size[0],size[1],0x0010|0x0004):
                        self.window.hide()
                        self._shown = False
                        return
                finally:
                    if prior:
                        api.SetThreadDpiAwarenessContext(prior)
            else:
                self.window.move(x,y)
        if not self._shown:
            self.window.show()
            self._shown = True
