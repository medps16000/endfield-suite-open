"""Deploy the release WebEngine runtime used by the HTML overlay windows."""
from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies, pyside6_library_info

if pyside6_library_info.version is not None:
    if pyside6_library_info.version < [6, 2, 2]:
        raise SystemExit("QtWebEngine packaging requires Qt 6.2.2 or newer")

    hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
    engine_binaries, engine_datas = pyside6_library_info.collect_qtwebengine_files()
    binaries += engine_binaries

    # Debug packs belong to debug Qt builds. Remote devtools are disabled by the
    # overlay host. Locale packs translate Chromium UI, not user HTML text/fonts.
    locales = {"en-US.pak", "zh-CN.pak", "zh-TW.pak"}
    resources = {"icudtl.dat", "qtwebengine_resources.pak", "qtwebengine_resources_100p.pak",
                 "qtwebengine_resources_200p.pak", "v8_context_snapshot.bin"}
    development_resources = {"qtwebengine_resources.debug.pak", "qtwebengine_resources_100p.debug.pak",
                             "qtwebengine_resources_200p.debug.pak", "v8_context_snapshot.debug.bin",
                             "qtwebengine_devtools_resources.debug.pak", "qtwebengine_devtools_resources.pak"}
    for source, destination in engine_datas:
        path = Path(source)
        # The upstream helper returns directories. Expand them before filtering
        # so no wildcard can reintroduce debug files or drop every locale.
        if path.is_dir() and path.name == "resources":
            files = {item.name: item for item in path.iterdir() if item.is_file()}
            unknown = set(files) - resources - development_resources
            if unknown or not resources.issubset(files):
                raise RuntimeError(f"Unreviewed or missing QtWebEngine resources: {unknown}")
            datas.extend((str(files[name]), destination) for name in sorted(resources))
        elif path.is_dir() and path.name == "qtwebengine_locales":
            for name in sorted(locales):
                if not (path / name).is_file():
                    raise RuntimeError(f"Missing supported WebEngine locale: {name}")
                datas.append((str(path / name), destination))
        else:
            datas.append((source, destination))
    hiddenimports += ["PySide6.QtPrintSupport"]
