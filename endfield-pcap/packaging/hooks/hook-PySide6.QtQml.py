"""Keep native QML dependencies without shipping the unused QML application tree.

The client uses QWidget/QWebEngineView, and never instantiates a QQmlEngine.
The standard hook also collects every QML plugin, which pulls Quick3D and other
unrelated application modules into this widget-only client. Native dependencies
are still resolved by PyInstaller's Qt helper and PE dependency analysis.
"""
from pathlib import PurePosixPath

from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
# QQml debugger/profiler factories are only used by QML development tooling.
binaries = [(source, target) for source, target in binaries
            if "qmltooling" not in PurePosixPath(str(target).replace("\\", "/")).parts]
